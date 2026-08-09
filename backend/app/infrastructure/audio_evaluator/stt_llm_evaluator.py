"""
STT + 純文字 LLM 低成本語音評分器 (stt_llm)。

STT + text-only LLM low-cost audio evaluator (stt_llm).

流程：本地 Whisper 轉錄 → 逐字稿以純文字（不含任何 audio bytes）
送交輕量 LLM，沿用卡片對應的 Jinja2 評分樣板（含三語卡專屬樣板），
以 STT_LLM_MODEL_NAME 指定的便宜純文字模型完成語意評分。

Pipeline: local Whisper transcription → the transcript is sent as plain
text (no audio bytes whatsoever) to a lightweight LLM, reusing the
card's own Jinja2 evaluation template (including per-language
trilingual templates), scored by the cheap text-only model named in
STT_LLM_MODEL_NAME.

設計決策：
- 憑證沿用 AUDIO_API_KEY / AUDIO_BASE_URL（與 openai_client 一致），
  僅模型換為 STT_LLM_MODEL_NAME（呼叫時讀取，支援 /setconfig 熱切換）。
- 空逐字稿在本層直接攔截回 0 分，不浪費 LLM 呼叫。
- 結構化輸出、超時、Markdown 清理與解析邏輯比照 openai_client 現況；
  LLM 呼叫不重試（統一重試策略屬獨立重構，見計畫 §2.9）。
- transcript 欄位一律以 STT 結果覆寫（LLM 只會複讀輸入）。

Design decisions:
- Credentials reuse AUDIO_API_KEY / AUDIO_BASE_URL (matching
  openai_client); only the model switches to STT_LLM_MODEL_NAME (read
  per call to honor /setconfig hot swaps).
- An empty transcript short-circuits to score 0 without an LLM call.
- Structured output, timeout, markdown cleanup, and parsing mirror the
  current openai_client; no LLM retries (a unified retry strategy is a
  separate refactor, plan §2.9).
- The transcript field is always overwritten with the STT result (the
  LLM would merely echo its input).
"""

import asyncio
import json
import logging

import httpx
import openai
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.dependencies import get_template_engine
from app.core.exceptions import LLMServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
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
        _whisper: 共用的 Whisper 轉錄客戶端。Shared Whisper
            transcription client.
        _client: OpenAI 相容的純文字 LLM 客戶端。OpenAI-compatible
            text-only LLM client.
    """

    def __init__(self) -> None:
        """初始化 STTLLMEvaluator。

        Initialize the STTLLMEvaluator.

        Raises:
            STTServiceError: STT_SERVER_URL 未設定時拋出。Raised when
                STT_SERVER_URL is not configured.
            LLMServiceError: AUDIO_API_KEY 未設定時拋出。Raised when
                AUDIO_API_KEY is not configured.
        """
        self._whisper = WhisperClient()
        if not settings.AUDIO_API_KEY:
            raise LLMServiceError(
                "AUDIO_API_KEY 未設定，無法初始化 STT LLM Evaluator。"
            )
        self._client = AsyncOpenAI(
            api_key=settings.AUDIO_API_KEY,
            base_url=settings.AUDIO_BASE_URL,
        )
        logger.info("STT LLM Evaluator 初始化完成")

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
        """轉錄後以純文字 LLM 依卡片樣板評分。

        Transcribe, then score the transcript with a text-only LLM using
        the card's template.

        Args:
            audio_data: 音檔原始二進位資料。Raw audio bytes.
            audio_filename: 音檔檔名。Audio filename.
            prompt_text: 卡片 Prompt。Card prompt.
            context_text: 卡片 Context。Card context.
            reference_answers: 參考答案列表。Reference answers.
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
                evaluator_label=f"stt+{settings.STT_LLM_MODEL_NAME}",
            )

        engine = get_template_engine()
        system_prompt = engine.render(
            template_name,
            prompt_text=prompt_text,
            context_text=context_text,
            reference_answers=reference_answers,
            target_language=target_language,
            user_transcript=user_transcript,
            disable_markdown=False,
        )

        structured_format: dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "audio_evaluation",
                "schema": _EVALUATION_SCHEMA,
                "strict": True,
            },
        }
        # 模型名於呼叫時讀取，支援 /setconfig 熱切換
        # Read the model name per call to honor /setconfig hot swaps.
        model_name = settings.STT_LLM_MODEL_NAME

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": (
                                "使用者的語音辨識逐字稿如下：\n"
                                f"{user_transcript}"
                            ),
                        },
                    ],
                    response_format=structured_format,  # type: ignore[arg-type]
                    temperature=0.3,
                ),
                timeout=90.0,
            )
        except asyncio.TimeoutError as e:
            logger.error("STT LLM Evaluator API 呼叫超時 (Timeout)")
            raise LLMServiceError("STT LLM Evaluator API 超時失敗") from e
        except (openai.OpenAIError, httpx.RequestError) as e:
            logger.error("STT LLM Evaluator API 呼叫失敗: %s", e)
            raise LLMServiceError(f"STT LLM Evaluator API 失敗: {e}") from e

        message = response.choices[0].message if response.choices else None
        content = message.content if message else None
        if not content:
            raise LLMServiceError("STT LLM Evaluator 回傳空內容。")

        # 清理可能的 Markdown 格式（比照 openai_client）
        # Strip possible markdown fences (mirrors openai_client).
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            decoder = json.JSONDecoder()
            parsed, _ = decoder.raw_decode(cleaned.lstrip())
            result = AudioEvaluationResult(**parsed)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("STT LLM Evaluator 回傳格式錯誤: %s", content)
            raise LLMServiceError(f"語音評分結果格式錯誤: {e}") from e

        # transcript 一律以 STT 結果為準（LLM 只是複讀輸入）
        # The transcript is always the STT output (the LLM just echoes).
        result.transcript = user_transcript
        result.evaluator_label = f"stt+{model_name}"
        logger.info(
            "stt_llm 評分完成: score=%d (model=%s)", result.score, model_name
        )
        return result
