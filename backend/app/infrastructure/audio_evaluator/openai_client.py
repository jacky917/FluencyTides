"""
OpenAI 相容層語音評分器實作。

使用 AsyncOpenAI SDK 將音檔轉為 Base64 並透過
Chat Completions API 的 input_audio 功能傳送給 LLM 進行評分。

適用場景：
- 使用 OpenAI GPT-4o-audio-preview 等支援音訊輸入的模型。
- 使用其他提供 OpenAI 相容端點的服務商。

設計決策：
- 使用 response_format 強制 JSON Schema 輸出，
  與現有 LLMClient 的 generate_structured_data 邏輯一致。
"""

import base64
import json
import logging

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.schemas.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)

# LLM 評分用的 JSON Schema，強制約束輸出格式
_EVALUATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "總分 0-100"},
        "status_code": {
            "type": "integer",
            "description": "0=吻合範例且高分, 1=有瑕疵或低分, 2=無匹配但自然",
        },
        "feedback": {"type": "string", "description": "AI 評語"},
        "transcript": {"type": "string", "description": "語音逐字稿"},
    },
    "required": ["score", "status_code", "feedback", "transcript"],
    "additionalProperties": False,
}


def _build_evaluation_prompt(
    prompt_text: str,
    reference_answers: list[str],
) -> str:
    """組裝語音評分的系統提示詞。

    根據參考答案數量動態調整 Prompt 的評分標準，
    確保 LLM 能正確區分三種 status_code。

    Args:
        prompt_text: 卡片 Prompt (對方的發言)。
        reference_answers: 參考範本列表。

    Returns:
        組裝完成的系統提示詞。
    """
    refs_section = ""
    if reference_answers:
        refs_list = "\n".join(
            f"  {i + 1}. {ans}" for i, ans in enumerate(reference_answers)
        )
        refs_section = f"\n## 參考範本回覆\n{refs_list}\n"
    else:
        refs_section = "\n## 參考範本回覆\n（無範本，請根據 Prompt 語境判斷）\n"

    return (
        "你是一位專業的外語口說教練。請根據以下資訊評估使用者的語音回覆。\n\n"
        f"## 對方的發言 (Prompt)\n{prompt_text}\n"
        f"{refs_section}\n"
        "## 評分規則\n"
        "- status_code=0: 語意完全吻合參考範本且表達自然，score >= 80\n"
        "- status_code=1: 接近範本或無範本但有語法/發音瑕疵，score < 80\n"
        "- status_code=2: 無匹配範本，但回覆整體自然流暢，score >= 80\n\n"
        "## 輸出要求\n"
        "只回傳 JSON，包含 score (0-100), status_code (0/1/2), "
        "feedback (繁體中文評語), transcript (語音逐字稿)。"
    )


class OpenAIAudioEvaluator(BaseAudioEvaluator):
    """使用 OpenAI 相容 API 的語音評分器。

    將音檔編碼為 Base64 後，透過 Chat Completions 的
    多模態輸入功能發送給 LLM 進行語音分析與評分。

    Attributes:
        _client: AsyncOpenAI 客戶端實例。
        _model_name: 使用的模型名稱。
    """

    def __init__(self) -> None:
        """初始化 OpenAI 相容層語音評分器。

        使用與 LLMClient 相同的 API Key 和 Base URL。

        Raises:
            LLMServiceError: 當必要設定未提供時。
        """
        if not settings.LLM_API_KEY:
            raise LLMServiceError(
                "LLM_API_KEY 未設定，無法初始化 OpenAI Audio Evaluator。"
            )

        self._client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )
        self._model_name = settings.LLM_MODEL_NAME
        logger.info(
            "OpenAI Audio Evaluator 初始化完成，模型: %s", self._model_name
        )

    async def evaluate_audio(
        self,
        audio_data: bytes,
        audio_filename: str,
        prompt_text: str,
        reference_answers: list[str],
    ) -> AudioEvaluationResult:
        """透過 OpenAI 相容 API 評估語音。

        將音檔編碼為 Base64，嵌入 Chat Completions 的 user message 中，
        並強制使用 JSON Schema response_format 確保結構化輸出。

        Args:
            audio_data: 音檔二進位資料。
            audio_filename: 音檔檔名。
            prompt_text: 卡片 Prompt。
            reference_answers: 參考範本列表。

        Returns:
            AudioEvaluationResult 結構化評分結果。

        Raises:
            LLMServiceError: API 呼叫失敗或輸出格式不符時。
        """
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
        system_prompt = _build_evaluation_prompt(prompt_text, reference_answers)

        # 組裝含音訊的多模態訊息
        user_content: list[dict[str, object]] = [
            {"type": "text", "text": "請評估以下語音回覆："},
            {
                "type": "input_audio",
                "input_audio": {
                    "data": audio_b64,
                    "format": "ogg" if audio_filename.endswith(".ogg") else "wav",
                },
            },
        ]

        structured_format: dict[str, object] = {
            "type": "json_schema",
            "json_schema": {
                "name": "audio_evaluation",
                "schema": _EVALUATION_SCHEMA,
                "strict": True,
            },
        }

        try:
            response = await self._client.chat.completions.create(
                model=self._model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                response_format=structured_format,
                temperature=0.3,
            )
        except Exception as e:
            logger.error("OpenAI Audio Evaluator API 呼叫失敗: %s", e)
            raise LLMServiceError(
                f"OpenAI Audio Evaluator API 失敗: {e}"
            ) from e

        content = response.choices[0].message.content
        if not content:
            raise LLMServiceError("OpenAI Audio Evaluator 回傳空內容。")

        try:
            parsed: dict[str, object] = json.loads(content)
            return AudioEvaluationResult(**parsed)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("OpenAI Audio Evaluator 回傳格式錯誤: %s", content)
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
