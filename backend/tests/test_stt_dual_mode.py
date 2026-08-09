"""
STT 雙模式（stt_diff / stt_llm）單元測試。

Unit tests for the STT dual-mode evaluators (stt_diff / stt_llm).

涵蓋計畫 §5 的自動化測試項 1-9：語言映射、正規化、diff 標記、
防護路徑、HTML escape、payload 純文字、樣板分支、工廠與 Schema
相容性。

Covers automated test items 1-9 of plan §5: language mapping,
normalization, diff markup, guard paths, HTML escaping, text-only
payloads, template branching, factory wiring, and schema compatibility.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.core.config import settings
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.stt.whisper_client import WhisperClient
from app.infrastructure.audio_evaluator.stt_diff_evaluator import (
    STTDiffEvaluator,
    _normalize_with_map,
    _render_diff,
)
from app.schemas.llm.speaking import AudioEvaluationResult


class TestWhisperLanguageMap(unittest.TestCase):
    """語言映射測試（計畫 §5 測試 1）。Language-mapping tests (plan §5 #1)."""

    def test_locale_mapping(self) -> None:
        """locale 前綴應正確映射為 ISO 639-1。Locales map to ISO 639-1."""
        self.assertEqual(WhisperClient.to_whisper_language("ja-JP"), "ja")
        self.assertEqual(WhisperClient.to_whisper_language("en-US"), "en")
        self.assertEqual(WhisperClient.to_whisper_language("zh-TW"), "zh")
        self.assertEqual(WhisperClient.to_whisper_language("JA-jp"), "ja")

    def test_unknown_falls_back_to_auto(self) -> None:
        """'other'/空值應回退自動偵測。'other'/empty fall back to auto."""
        self.assertIsNone(WhisperClient.to_whisper_language("other"))
        self.assertIsNone(WhisperClient.to_whisper_language(None))
        self.assertIsNone(WhisperClient.to_whisper_language(""))
        self.assertIsNone(WhisperClient.to_whisper_language("  "))


class TestNormalization(unittest.TestCase):
    """正規化測試（計畫 §5 測試 2）。Normalization tests (plan §5 #2)."""

    def test_nfkc_and_punctuation(self) -> None:
        """全形轉半形、剝標點、英文小寫。NFKC, punctuation strip, lowercase."""
        norm, _ = _normalize_with_map("Ａｂｃ、テスト。 Hello!")
        self.assertEqual(norm, "abcテストhello")

    def test_display_map_restores_original(self) -> None:
        """顯示映射應還原原始字元（含全形）。Map restores original chars."""
        norm, mapping = _normalize_with_map("Ａb")
        self.assertEqual(norm, "ab")
        self.assertEqual(mapping, ["Ａ", "b"])


class TestDiffEvaluator(unittest.TestCase):
    """stt_diff 邏輯測試（計畫 §5 測試 3-5）。stt_diff tests (plan §5 #3-5)."""

    def _make_evaluator(self, transcript: str) -> STTDiffEvaluator:
        """建立注入 mock WhisperClient 的評分器。Build evaluator with mock STT."""
        with patch.object(STTDiffEvaluator, "__init__", lambda self: None):
            ev = STTDiffEvaluator()
        ev._whisper = AsyncMock()
        ev._whisper.transcribe = AsyncMock(return_value=transcript)
        return ev

    def _evaluate(self, transcript: str, refs: list[str]) -> AudioEvaluationResult:
        """執行一次評分。Run one evaluation."""
        ev = self._make_evaluator(transcript)
        return asyncio.run(
            ev.evaluate_audio(
                audio_data=b"x",
                audio_filename="a.ogg",
                prompt_text="",
                context_text="",
                reference_answers=refs,
                target_language="ja-JP",
            )
        )

    def test_perfect_match_scores_100(self) -> None:
        """完全一致（含標點差異）應得 100 分。Perfect match scores 100."""
        result = self._evaluate("今日は暑いですね。", ["今日は暑いですね"])
        self.assertEqual(result.score, 100)
        self.assertNotIn("<s>", result.feedback)

    def test_replace_produces_dual_markup(self) -> None:
        """替換應產生 <s>/<b> 與紅綠 span。Replace yields dual markup."""
        result = self._evaluate("今日は寒いです", ["今日は暑いです"])
        self.assertLess(result.score, 100)
        self.assertIn("<s>寒</s><b>暑</b>", result.feedback)
        assert result.feedback_anki_html is not None
        self.assertIn('<span style="color:red">寒</span>', result.feedback_anki_html)
        self.assertIn('<span style="color:green">暑</span>', result.feedback_anki_html)

    def test_multiple_references_picks_best(self) -> None:
        """多條參考答案取最高相似度。Best of multiple references wins."""
        result = self._evaluate("おはよう", ["こんばんは", "おはよう"])
        self.assertEqual(result.score, 100)
        # 完全命中第 2 條 → 差異標記中不應出現任何修正記號
        self.assertNotIn("<s>", result.feedback)
        self.assertNotIn("<b>", result.feedback)

    def test_no_references_guard(self) -> None:
        """無參考答案應回 0 分並提示。No references → score 0 + hint."""
        result = self._evaluate("テスト", [])
        self.assertEqual(result.score, 0)
        self.assertIn("stt_diff", result.feedback)
        self.assertEqual(result.transcript, "テスト")

    def test_empty_transcript_guard(self) -> None:
        """空逐字稿應回 0 分。Empty transcript → score 0."""
        result = self._evaluate("   ", ["何か"])
        self.assertEqual(result.score, 0)
        self.assertEqual(result.transcript, "（無語音）")

    def test_html_escape_in_tg_output(self) -> None:
        """原文含 < 與 & 時 TG 輸出必須轉義。TG output escapes < and &."""
        _, tg = "", ""
        tg, anki = _render_diff(
            ["<", "a"], ["&", "a"],
            [("replace", 0, 1, 0, 1), ("equal", 1, 2, 1, 2)],
        )
        self.assertIn("&lt;", tg)
        self.assertIn("&amp;", tg)
        self.assertNotIn("<a", tg)
        self.assertIn("&lt;", anki)


class TestTemplateBranch(unittest.TestCase):
    """樣板分支測試（計畫 §5 測試 7）。Template-branch tests (plan §5 #7)."""

    _TEMPLATES = [
        "prompts/audio_evaluator.j2",
        "prompts/anki/Speaking_Trilingual_JA.j2",
        "prompts/anki/Speaking_Trilingual_ZH.j2",
        "prompts/anki/Speaking_Trilingual_EN.j2",
    ]

    def _render(self, template: str, **extra: object) -> str:
        """渲染樣板。Render a template."""
        from app.core.dependencies import get_template_engine
        return get_template_engine().render(
            template,
            prompt_text="p", context_text="c",
            reference_answers=["r"], target_language=None,
            disable_markdown=False, **extra,
        )

    def test_without_transcript_has_no_stt_marker(self) -> None:
        """未傳 user_transcript 時不得出現 STT 模式指示。No marker without it."""
        for tpl in self._TEMPLATES:
            self.assertNotIn("STT Mode", self._render(tpl), msg=tpl)

    def test_with_transcript_has_stt_marker(self) -> None:
        """傳入 user_transcript 時必須出現 STT 模式指示。Marker appears with it."""
        for tpl in self._TEMPLATES:
            rendered = self._render(tpl, user_transcript="こんにちは")
            self.assertIn("STT Mode", rendered, msg=tpl)


class TestFactoryAndSchema(unittest.TestCase):
    """工廠與 Schema 相容性（計畫 §5 測試 8-9）。Factory & schema (plan §5 #8-9)."""

    def test_factory_creates_stt_diff(self) -> None:
        """工廠應能建立 STTDiffEvaluator。Factory builds STTDiffEvaluator."""
        from app.infrastructure.audio_evaluator.factory import create_audio_evaluator
        with patch.object(settings, "AUDIO_EVALUATOR_PROVIDER", "stt_diff"), \
             patch.object(settings, "STT_SERVER_URL", "http://localhost:8000/v1"):
            evaluator = create_audio_evaluator()
        self.assertIsInstance(evaluator, STTDiffEvaluator)

    def test_factory_rejects_unknown_with_full_list(self) -> None:
        """非法值的錯誤訊息應列出全部五個合法值。Error lists all five values."""
        from app.infrastructure.audio_evaluator.factory import create_audio_evaluator
        with patch.object(settings, "AUDIO_EVALUATOR_PROVIDER", "bogus"):
            with self.assertRaises(ValueError) as ctx:
                create_audio_evaluator()
        msg = str(ctx.exception)
        for name in ("gemini_native", "openai", "proxy", "stt_diff", "stt_llm"):
            self.assertIn(name, msg)

    def test_schema_backward_compatible(self) -> None:
        """三欄位輸入（無新欄位）仍可建構。Three-field input still validates."""
        result = AudioEvaluationResult(score=90, feedback="x", transcript="y")
        self.assertIsNone(result.feedback_anki_html)


class TestJsonModifierGuard(unittest.TestCase):
    """S001 前置修復測試。Tests for the S001 prerequisite fix."""

    def test_corrupted_json_raises(self) -> None:
        """損毀 JSON 應拋例外而非回空陣列。Corrupted JSON raises, not []."""
        with self.assertRaises(AnkiFieldCorruptedError):
            AnkiJsonFieldManager.parse_field_string("{broken json!")

    def test_non_list_raises(self) -> None:
        """非陣列 JSON 應拋例外。Non-list JSON raises."""
        with self.assertRaises(AnkiFieldCorruptedError):
            AnkiJsonFieldManager.parse_field_string('{"a": 1}')

    def test_empty_field_returns_empty_list(self) -> None:
        """空欄位仍回空陣列（合法初始狀態）。Empty field still returns []."""
        self.assertEqual(AnkiJsonFieldManager.parse_field_string(""), [])
        self.assertEqual(AnkiJsonFieldManager.parse_field_string("[]"), [])


if __name__ == "__main__":
    unittest.main()
