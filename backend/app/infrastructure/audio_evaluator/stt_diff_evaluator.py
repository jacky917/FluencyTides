"""STT + difflib zero-cost audio evaluator (stt_diff).

STT + difflib 零成本語音評分器 (stt_diff)。

Pipeline: local Whisper transcription → character-level difflib
comparison against the reference answers → similarity converted to a
score, producing dual-format diff markup for Telegram (<s>/<b>) and Anki
(<span style>). Apart from STT there is no network IO and no API cost.

流程：本地 Whisper 轉錄 → 與參考答案做字元級 difflib 比對 →
相似度轉分數，並產出 TG（<s>/<b>）與 Anki（<span style>）雙格式
差異標記。全程除 STT 外零網路 IO、零 API 費用。

Design decisions:
- Character-level diff (no tokenization): works well for Japanese, which
  has no spaces; before comparison the text is NFKC-normalized, stripped
  of punctuation and whitespace, and English is lowercased.
- The display diff restores the original characters through a
  "normalized index → original character" map (punctuation is omitted
  from the display; this is expected).
- A card without reference_answers, or a recording with no detected
  speech, is not scored: :class:`AudioNotScoredError` is raised instead
  of returning a score.

設計決策：
- 逐字元比對（非分詞）：對無空格的日文即可良好運作；比對前做
  NFKC 正規化、剝除標點空白、英文小寫化。
- 顯示用差異以「正規化索引 → 原始字元」映射還原原文字元
  （標點在顯示中省略，屬預期行為）。
- 無 reference_answers 的卡片，或沒偵測到語音的錄音不評分：拋出
  ``AudioNotScoredError``，而不是回傳分數。
"""

import difflib
import html
import logging
import unicodedata

from app.infrastructure.audio_evaluator.base import AudioNotScoredError, BaseAudioEvaluator
from app.infrastructure.stt.whisper_client import WhisperClient
from app.schemas.llm.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)

# 比對前剝除的標點與空白字元集
#
# 注意：不可納入 ー（U+30FC 長音符）。它在日文中並非標點，而是承載語意的
# 音韻字元——剝除後「ビール／ビル」「コード／コド」會被判為相同，長音的
# 發音錯誤將完全無法偵測，且差異顯示也會缺字。半形 ｰ（U+FF70）經 NFKC 會
# 正規化為 U+30FC，一併保留。
#
# Punctuation and whitespace stripped before comparison.
#
# Note: ー (U+30FC, prolonged sound mark) must NOT be listed here. It is a
# meaning-bearing phonemic character in Japanese, not punctuation —
# stripping it makes "ビール"/"ビル" and "コード"/"コド" compare equal, hides
# long-vowel pronunciation errors entirely, and drops the character from the
# rendered diff. Half-width ｰ (U+FF70) NFKC-normalizes to U+30FC and is
# likewise preserved.
_STRIP_CHARS = set(
    "。、．，,.!?！？「」『』（）()［］[]｛｝{}…・：:；;〜~－-—\"'‘’“”　 \t\r\n"
)


def _normalize_with_map(text: str) -> tuple[str, list[str]]:
    """Normalize a string while keeping a "normalized position → original character" display map.

    正規化字串並保留「正規化位置 → 原始字元」的顯示映射。

    Applies NFKC normalization and lowercasing character by character,
    stripping punctuation and whitespace; each normalized character maps to
    one original character (when NFKC expands one character into several,
    every expanded character maps back to the same original character).

    逐字元做 NFKC 正規化與小寫化，剝除標點/空白；每個正規化後字元
    對應一個原始字元（NFKC 一對多展開時，展開出的每個字元都映射回
    同一原始字元）。

    Args:
        text: the original string
            原始字串

    Returns:
        (normalized string, display map), where item i of the map is the
        original character behind character i of the normalized string
        (正規化字串, 顯示映射)，映射第 i 項為正規化字串第 i 個字元
        對應的原始字元
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
    """Render dual-format diff markup (Telegram and Anki) from difflib opcodes.

    依 difflib opcodes 產出 TG 與 Anki 雙格式差異標記。

    Markup rules (STT plan §3.3): replace → struck-through wrong text plus
    bold correct text; delete (extra speech) → strikethrough; insert
    (missed text) → bold. On the TG side every original fragment goes
    through html.escape. Anki uses red for wrong text and green for correct
    text.

    標記規則（STT 計畫 §3.3）：replace → 刪除線誤字＋粗體正字；
    delete（多唸）→ 刪除線；insert（漏唸）→ 粗體。TG 端所有原文
    片段皆經 html.escape。Anki 端誤字為紅色、正字為綠色。

    Args:
        transcript_map: the display map of the transcript
            逐字稿的顯示映射
        reference_map: the display map of the reference answer
            參考答案的顯示映射
        opcodes: the result of ``SequenceMatcher.get_opcodes()``
            ``SequenceMatcher.get_opcodes()`` 的結果

    Returns:
        the dual-format diff strings ``(tg_markup, anki_html)``
        ``(tg_markup, anki_html)`` 雙格式差異字串
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
    """A zero-cost audio evaluator using local Whisper plus difflib.

    本地 Whisper + difflib 的零成本語音評分器。

    Attributes:
        _whisper: the shared Whisper transcription client
            共用的 Whisper 轉錄客戶端
    """

    def __init__(self, whisper: WhisperClient) -> None:
        """Initialize the STTDiffEvaluator.

        初始化 STTDiffEvaluator。

        Args:
            whisper: the Whisper client used for transcription (the ``stt`` capability)
                轉錄用的 Whisper 客戶端（能力路由的 ``stt``）
        """
        self._whisper = whisper
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
        reference_familiarity: list[int | None] | None = None,
    ) -> AudioEvaluationResult:
        """Score by the similarity between the STT transcript and the reference answers.

        以 STT 逐字稿與參考答案的相似度評分。

        The reference with the highest similarity is used for the score and
        the diff.

        取相似度最高的參考答案計算分數與差異。

        Args:
            audio_data: the raw audio bytes
                音檔原始二進位資料
            audio_filename: the audio file name
                音檔檔名
            prompt_text: the card Prompt (unused in this mode)
                卡片 Prompt（本模式不使用）
            context_text: the card Context (unused in this mode)
                卡片 Context（本模式不使用）
            reference_answers: the reference answers (the comparison baseline)
                參考答案列表（比對基準）
            target_language: the target-language locale, which decides Whisper's language parameter
                目標語言 locale，決定 Whisper 語言參數
            template_name: the evaluation template (unused in this mode; kept for interface parity)
                評分樣板（本模式不使用，保留介面相容）
            reference_familiarity: the familiarity of each reference answer; when the matched one is
                marked, the start of the feedback notes it
                每句參考答案的熟識度；比對到的那句有標時，feedback 開頭註明

        Returns:
            the evaluation result with the score, the dual-format diff markup
            (possibly with a familiarity note) and the transcript
            含分數、雙格式差異標記（可能帶熟識度註記）與逐字稿的評分結果

        Raises:
            AudioNotScoredError: if no speech is detected or the card has no reference answers
                若沒偵測到語音或卡片沒有參考答案
            STTServiceError: if the STT service call fails
                若 STT 服務呼叫失敗
        """
        label = "stt_diff"  # 零成本比對模式，無 LLM / zero-cost diff mode, no LLM

        transcript = await self._whisper.transcribe(
            audio_data=audio_data,
            audio_filename=audio_filename,
            target_language=target_language,
        )

        # 以下兩種情況都不是評分結果（不是 0 分）：拋出讓呼叫端不寫入紀錄、照實告訴使用者
        if not transcript.strip():
            raise AudioNotScoredError("沒有偵測到語音。")

        if not reference_answers:
            raise AudioNotScoredError(
                "這張卡沒有參考答案，比對模式無法評分；管理員可用 /setconfig 把錄音評分策略改成 stt_llm 或 listen。"
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

        # 分數已由訊息/卡片其他欄位顯示，feedback 放差異標記（三語卡另在開頭註明比對到的說法的熟識度）
        # The score is shown elsewhere; feedback carries the diff markup (plus,
        # for trilingual cards, the matched reference's familiarity).
        feedback_tg = tg_diff
        feedback_anki = anki_diff
        # 三語卡：比對到的參考答案有熟識度時註明（同一題常有正式與隨意的不同說法）
        # Trilingual cards: name the matched reference's familiarity.
        familiarity = (
            reference_familiarity[best_index]
            if reference_familiarity and best_index < len(reference_familiarity)
            else None
        )
        if familiarity is not None:
            note = f"比對對象：熟識度 {familiarity} 的說法"
            feedback_tg = f"{note}\n{feedback_tg}"
            feedback_anki = f"{note}<br>{feedback_anki}"

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
