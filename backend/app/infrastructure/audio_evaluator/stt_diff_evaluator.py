"""
STT + difflib 零成本語音評分器 (stt_diff)。

STT + difflib zero-cost audio evaluator (stt_diff).

流程：本地 Whisper 轉錄 → 與參考答案做字元級 difflib 比對 →
相似度轉分數，並產出 TG（<s>/<b>）與 Anki（<span style>）雙格式
差異標記。全程除 STT 外零網路 IO、零 API 費用。

Pipeline: local Whisper transcription → character-level difflib
comparison against reference answers → similarity-to-score mapping,
producing dual-format diff markup for Telegram (<s>/<b>) and Anki
(<span style>). Zero network IO besides STT, zero API cost.

設計決策：
- 逐字元比對（非分詞）：對無空格的日文即可良好運作；比對前做
  NFKC 正規化、剝除標點空白、英文小寫化。
- 顯示用差異以「正規化索引 → 原始字元」映射還原原文字元
  （標點在顯示中省略，屬預期行為）。
- 無 reference_answers 的卡片回傳 score=0 並提示不支援本模式。

Design decisions:
- Character-level diff (no tokenization): works well for spaceless
  Japanese; inputs are NFKC-normalized, stripped of punctuation and
  whitespace, and lowercased before comparison.
- Display diffs restore original characters through a normalized-index
  → original-character map (punctuation is omitted from display by
  design).
- Cards without reference_answers return score=0 with a hint that this
  mode is unsupported for them.
"""

import difflib
import html
import logging
import unicodedata

from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.infrastructure.stt.whisper_client import WhisperClient
from app.schemas.llm.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)

# 比對前剝除的標點與空白字元集
# Punctuation and whitespace stripped before comparison.
_STRIP_CHARS = set(
    "。、．，,.!?！？「」『』（）()［］[]｛｝{}…・：:；;〜~ー－-—\"'‘’“”　 \t\r\n"
)


def _normalize_with_map(text: str) -> tuple[str, list[str]]:
    """正規化字串並保留「正規化位置 → 原始字元」的顯示映射。

    Normalize a string while keeping a normalized-position → original
    character map for display.

    逐字元做 NFKC 正規化與小寫化，剝除標點/空白；每個正規化後字元
    對應一個原始字元（NFKC 一對多展開時，展開出的每個字元都映射回
    同一原始字元）。

    Applies per-character NFKC normalization and lowercasing, stripping
    punctuation/whitespace; each normalized character maps to one
    original character (for one-to-many NFKC expansions, every expanded
    character maps back to the same original).

    Args:
        text: 原始字串。The original string.

    Returns:
        (正規化字串, 顯示映射)：映射第 i 項為正規化字串第 i 個字元
        對應的原始字元。(normalized string, display map): item i of the
        map is the original character behind normalized character i.
    """
    norm_chars: list[str] = []
    display_map: list[str] = []
    for ch in text:
        for norm_ch in unicodedata.normalize("NFKC", ch).lower():
            if norm_ch in _STRIP_CHARS or norm_ch.isspace():
                continue
            norm_chars.append(norm_ch)
            display_map.append(ch)
    return "".join(norm_chars), display_map


def _render_diff(
    transcript_map: list[str],
    reference_map: list[str],
    opcodes: list[tuple[str, int, int, int, int]],
) -> tuple[str, str]:
    """依 difflib opcodes 產出 TG 與 Anki 雙格式差異標記。

    Render dual-format diff markup (Telegram and Anki) from difflib
    opcodes.

    標記規則（STT 計畫 §3.3）：replace → 刪除線誤字＋粗體正字；
    delete（多唸）→ 刪除線；insert（漏唸）→ 粗體。TG 端所有原文
    片段皆經 html.escape。

    Markup rules (STT plan §3.3): replace → struck wrong text + bold
    correction; delete (extra speech) → strikethrough; insert (missed
    text) → bold. All original fragments are html-escaped on the TG
    side.

    Args:
        transcript_map: 逐字稿的顯示映射。Display map of the transcript.
        reference_map: 參考答案的顯示映射。Display map of the reference.
        opcodes: SequenceMatcher.get_opcodes() 的結果。Result of
            SequenceMatcher.get_opcodes().

    Returns:
        (tg_markup, anki_html) 雙格式差異字串。The dual-format diff
        strings (tg_markup, anki_html).
    """
    tg_parts: list[str] = []
    anki_parts: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        spoken = html.escape("".join(transcript_map[i1:i2]))
        expected = html.escape("".join(reference_map[j1:j2]))
        if tag == "equal":
            tg_parts.append(spoken)
            anki_parts.append(spoken)
        elif tag == "replace":
            tg_parts.append(f"<s>{spoken}</s><b>{expected}</b>")
            anki_parts.append(
                f'<span style="color:red">{spoken}</span>'
                f'<span style="color:green">{expected}</span>'
            )
        elif tag == "delete":  # 多唸 / extra speech
            tg_parts.append(f"<s>{spoken}</s>")
            anki_parts.append(f'<span style="color:red">{spoken}</span>')
        elif tag == "insert":  # 漏唸 / missed text
            tg_parts.append(f"<b>{expected}</b>")
            anki_parts.append(f'<span style="color:green">{expected}</span>')
    return "".join(tg_parts), "".join(anki_parts)


class STTDiffEvaluator(BaseAudioEvaluator):
    """本地 Whisper + difflib 的零成本語音評分器。

    Zero-cost audio evaluator using local Whisper plus difflib.

    Attributes:
        _whisper: 共用的 Whisper 轉錄客戶端。Shared Whisper
            transcription client.
    """

    def __init__(self) -> None:
        """初始化 STTDiffEvaluator。

        Initialize the STTDiffEvaluator.

        Raises:
            STTServiceError: STT_SERVER_URL 未設定時拋出。Raised when
                STT_SERVER_URL is not configured.
        """
        self._whisper = WhisperClient()
        logger.info("STT Diff Evaluator 初始化完成")

    async def evaluate_audio(
        self,
        audio_data: bytes,
        audio_filename: str,
        prompt_text: str,
        context_text: str,
        reference_answers: list[str],
        target_language: str | None = None,
        template_name: str = "prompts/audio_evaluator.j2",
    ) -> AudioEvaluationResult:
        """以 STT 逐字稿與參考答案的相似度評分。

        Score by similarity between the STT transcript and the
        reference answers.

        Args:
            audio_data: 音檔原始二進位資料。Raw audio bytes.
            audio_filename: 音檔檔名。Audio filename.
            prompt_text: 卡片 Prompt（本模式不使用）。Card prompt
                (unused in this mode).
            context_text: 卡片 Context（本模式不使用）。Card context
                (unused in this mode).
            reference_answers: 參考答案列表（比對基準）。Reference
                answers (comparison baseline).
            target_language: 目標語言 locale，決定 Whisper 語言參數。
                Target-language locale controlling Whisper's language.
            template_name: 評分樣板（本模式不使用，保留介面相容）。
                Evaluation template (unused; kept for interface parity).

        Returns:
            含分數、雙格式差異標記與逐字稿的評分結果。The evaluation
            result with score, dual-format diff markup, and transcript.

        Raises:
            STTServiceError: STT 服務呼叫失敗時拋出。Raised when the
                STT call fails.
        """
        label = "stt_diff"  # 零成本比對模式，無 LLM / zero-cost diff mode, no LLM

        transcript = await self._whisper.transcribe(
            audio_data=audio_data,
            audio_filename=audio_filename,
            target_language=target_language,
        )

        if not transcript.strip():
            return AudioEvaluationResult(
                score=0,
                feedback="未能偵測到語音內容。",
                transcript="（無語音）",
                evaluator_label=label,
            )

        if not reference_answers:
            return AudioEvaluationResult(
                score=0,
                feedback=(
                    "此卡片沒有參考答案 (References)，不支援 stt_diff 純比對模式。\n"
                    "請切換至 stt_llm 或其他評分模式。"
                ),
                transcript=transcript,
                evaluator_label=label,
            )

        norm_transcript, transcript_map = _normalize_with_map(transcript)

        # 對每條參考答案計算相似度，取最高者為比對對象
        # Compare against every reference and keep the best match.
        best_ratio = -1.0
        best_index = 0
        best_matcher: difflib.SequenceMatcher[str] | None = None
        best_ref_map: list[str] = []
        for idx, ref in enumerate(reference_answers):
            norm_ref, ref_map = _normalize_with_map(ref)
            matcher = difflib.SequenceMatcher(
                None, norm_transcript, norm_ref, autojunk=False
            )
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_index = idx
                best_matcher = matcher
                best_ref_map = ref_map

        assert best_matcher is not None  # reference_answers 非空必有結果
        score = max(0, min(100, round(best_ratio * 100)))
        tg_diff, anki_diff = _render_diff(
            transcript_map, best_ref_map, best_matcher.get_opcodes()
        )

        # 分數已由訊息/卡片其他欄位顯示，feedback 只保留差異標記本體
        # The score is shown elsewhere; feedback carries only the diff markup.
        feedback_tg = tg_diff
        feedback_anki = anki_diff

        logger.info(
            "stt_diff 評分完成: score=%d, ref=#%d/%d",
            score, best_index + 1, len(reference_answers),
        )
        return AudioEvaluationResult(
            score=score,
            feedback=feedback_tg,
            transcript=transcript,
            feedback_anki_html=feedback_anki,
            evaluator_label=label,
        )
