"""
STT + 純文字 LLM 低成本語音評分器 (stt_llm)。

STT + text-only LLM low-cost audio evaluator (stt_llm).

流程：本地 Whisper 轉錄 → 逐字稿以純文字（不含任何 audio bytes）
送交輕量 LLM，沿用卡片對應的 Jinja2 評分樣板（含三語卡專屬樣板），
以能力路由 ``llm`` 指定的便宜純文字模型完成語意評分。

Pipeline: local Whisper transcription → the transcript is sent as plain
text (no audio bytes whatsoever) to a lightweight LLM, reusing the
card's own Jinja2 evaluation template (including per-language
trilingual templates), scored by the cheap text-only model chosen by
the ``llm`` capability route.

設計決策：
- 評分用的文字 LLM 來自能力路由的 ``llm``（同一個用途鍵，例如 ``recording_eval``），
  Whisper 來自 ``stt``；兩者都由 ``audio_evaluator/factory.py`` 建好傳入。
  ``.env`` 那一層的 ``llm`` 預設為 ``AUDIO_*`` 合成的 ``audio`` 端點加上
  ``STT_LLM_MODEL_NAME``，與能力路由之前的行為相同。
- 空逐字稿在本層直接攔截回 0 分，不浪費 LLM 呼叫。
- 結構化輸出、重試與 JSON 清理交給 LLM 客戶端（與建卡相同）。
- transcript 欄位一律以 STT 結果覆寫（LLM 只會複讀輸入）。

Design decisions:
- The text LLM comes from the ``llm`` capability of the same route key
  (e.g. ``recording_eval``) and Whisper from ``stt``; the factory builds
  both. The ``.env`` default for ``llm`` is the ``audio`` endpoint
  synthesized from ``AUDIO_*`` with ``STT_LLM_MODEL_NAME``, as before.
- An empty transcript short-circuits to score 0 without an LLM call.
- Structured output, retries and JSON cleanup are the LLM client's job.
- The transcript field is always overwritten with the STT result (the
  LLM would merely echo its input).
"""

import logging

from pydantic import ValidationError

from app.core.dependencies import get_template_engine
from app.core.exceptions import LLMServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.infrastructure.llm.base import BaseLLMClient
from app.infrastructure.stt.whisper_client import WhisperClient
from app.schemas.llm.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)

# LLM 評分用的 JSON Schema（與 openai_client 相同的三欄位約束）
# JSON Schema for LLM scoring (same three-field constraint as openai_client).
_EVALUATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "總分 0-100"},
        "feedback": {"type": "string", "description": "AI 評語"},
        "transcript": {"type": "string", "description": "語音逐字稿"},
    },
    "required": ["score", "feedback", "transcript"],
    "additionalProperties": False,
}


class STTLLMEvaluator(BaseAudioEvaluator):
    """本地 Whisper + 純文字 LLM 的低成本語音評分器。

    Low-cost audio evaluator using local Whisper plus a text-only LLM.

    Attributes:
        _whisper: Whisper 轉錄客戶端（能力路由的 ``stt``）。The Whisper
            client from the ``stt`` capability.
        _llm: 評分用的文字 LLM 客戶端（能力路由的 ``llm``）。The text LLM
            client from the ``llm`` capability.
    """

    def __init__(self, whisper: WhisperClient, llm: BaseLLMClient) -> None:
        """初始化 STTLLMEvaluator。

        Initialize the STTLLMEvaluator.

        Args:
            whisper: 轉錄用的 Whisper 客戶端。The Whisper client.
            llm: 評分用的文字 LLM 客戶端。The text LLM client.
        """
        self._whisper = whisper
        self._llm = llm
        logger.info("STT LLM Evaluator 初始化完成（%s）", llm.model_label)

    @property
    def _label(self) -> str:
        """評分模式標籤：``stt+<模型名>``。The mode label."""
        return f"stt+{self._llm.model_name}"

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
        """轉錄後以純文字 LLM 依卡片樣板評分。

        Transcribe, then score the transcript with a text-only LLM using
        the card's template.

        Args:
            audio_data: 音檔原始二進位資料。Raw audio bytes.
            audio_filename: 音檔檔名。Audio filename.
            prompt_text: 卡片 Prompt。Card prompt.
            context_text: 卡片 Context。Card context.
            reference_answers: 參考答案列表。Reference answers.
            reference_familiarity: 每句參考答案的熟識度（同 base）。Familiarity of each
                reference (see the base class).
            target_language: 目標語言 locale。Target-language locale.
            template_name: 評分樣板路徑（三語卡自動沿用專屬樣板）。
                Evaluation template path (trilingual cards keep their
                per-language templates).

        Returns:
            結構化評分結果，transcript 以 STT 結果覆寫。The structured
            evaluation result with transcript overwritten by STT output.

        Raises:
            STTServiceError: STT 服務呼叫失敗時拋出。Raised when the
                STT call fails.
            LLMServiceError: LLM 呼叫失敗或輸出格式不符時拋出。Raised
                when the LLM call fails or the output is malformed.
        """
        user_transcript = await self._whisper.transcribe(
            audio_data=audio_data,
            audio_filename=audio_filename,
            target_language=target_language,
        )

        if not user_transcript.strip():
            # 空語音直接攔截，不浪費 LLM 呼叫
            # Empty speech short-circuits without an LLM call.
            return AudioEvaluationResult(
                score=0,
                feedback="未能偵測到語音內容。",
                transcript="（無語音）",
                evaluator_label=self._label,
            )

        engine = get_template_engine()
        system_prompt = engine.render(
            template_name,
            prompt_text=prompt_text,
            context_text=context_text,
            reference_answers=reference_answers,
            reference_familiarity=reference_familiarity or [],
            target_language=target_language,
            user_transcript=user_transcript,
            disable_markdown=False,
        )

        generated = await self._llm.generate_structured_data(
            system_prompt=system_prompt,
            user_prompt=f"使用者的語音辨識逐字稿如下：\n{user_transcript}",
            response_schema=_EVALUATION_SCHEMA,
        )
        try:
            result = AudioEvaluationResult(**generated.parsed_data)
        except (TypeError, ValidationError) as e:
            logger.error("STT LLM Evaluator 回傳格式錯誤: %s", generated.raw_content[:500])
            raise LLMServiceError(f"語音評分結果格式錯誤: {e}") from e

        # transcript 一律以 STT 結果為準（LLM 只是複讀輸入）
        # The transcript is always the STT output (the LLM just echoes).
        result.transcript = user_transcript
        result.evaluator_label = self._label
        logger.info(
            "stt_llm 評分完成: score=%d (%s)", result.score, self._llm.model_label
        )
        return result
