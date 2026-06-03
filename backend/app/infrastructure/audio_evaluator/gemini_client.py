"""
Google 原生 SDK (google-genai) 語音評分器實作。

使用 Google 官方 Generative AI SDK 直接上傳音檔給 Gemini 進行分析。
相比 OpenAI 相容層，原生 SDK 對 Gemini 的多模態功能支援最完整，
包括原生的 File Upload API 和音訊格式處理。

設計決策：
- 使用 google-genai（新版 SDK）而非 google-generativeai（舊版），
  因為新版 SDK 提供更好的非同步支援和型別提示。
- 直接使用 inline_data 而非 File API 上傳，避免檔案管理的額外複雜度。
  語音檔案通常很小（<5MB），inline_data 完全足夠。
"""

import json
import logging

from google import genai
from google.genai import types

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.schemas.speaking import AudioEvaluationResult

logger = logging.getLogger(__name__)


def _build_evaluation_prompt(
    prompt_text: str,
    reference_answers: list[str],
) -> str:
    """組裝語音評分的 Prompt。

    與 OpenAI 版本使用相同的評分邏輯，確保兩個策略的評分標準一致。

    Args:
        prompt_text: 卡片 Prompt (對方的發言)。
        reference_answers: 參考範本列表。

    Returns:
        組裝完成的評分提示詞。
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
        "feedback (繁體中文評語), transcript (語音逐字稿)。\n"
        "不要加任何 Markdown 標記或程式碼區塊。"
    )


class GeminiNativeAudioEvaluator(BaseAudioEvaluator):
    """使用 Google 原生 SDK 的語音評分器。

    透過 google-genai SDK 直接傳送音檔給 Gemini，
    利用原生的多模態推論能力進行語音分析與評分。

    Attributes:
        _client: google-genai Client 實例。
        _model_name: 使用的 Gemini 模型名稱。
    """

    def __init__(self) -> None:
        """初始化 Gemini 原生語音評分器。

        Raises:
            LLMServiceError: 當 GEMINI_NATIVE_API_KEY 未設定時。
        """
        api_key = settings.GEMINI_NATIVE_API_KEY
        if not api_key:
            raise LLMServiceError(
                "GEMINI_NATIVE_API_KEY 未設定，"
                "無法初始化 Gemini Native Audio Evaluator。"
            )

        self._client = genai.Client(api_key=api_key)
        self._model_name = settings.GEMINI_NATIVE_MODEL
        logger.info(
            "Gemini Native Audio Evaluator 初始化完成，模型: %s",
            self._model_name,
        )

    async def evaluate_audio(
        self,
        audio_data: bytes,
        audio_filename: str,
        prompt_text: str,
        reference_answers: list[str],
    ) -> AudioEvaluationResult:
        """透過 Gemini 原生 SDK 評估語音。

        使用 inline_data 直接在請求中傳送音檔二進位資料，
        避免額外的 File API 上傳步驟。

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
        evaluation_prompt = _build_evaluation_prompt(
            prompt_text, reference_answers
        )

        # 根據副檔名決定 MIME 類型
        mime_type = "audio/ogg"
        if audio_filename.endswith(".wav"):
            mime_type = "audio/wav"
        elif audio_filename.endswith(".mp3"):
            mime_type = "audio/mpeg"

        # 組裝多模態內容：Prompt 文字 + 音檔 inline data
        contents: list[types.Part | str] = [
            evaluation_prompt,
            types.Part.from_bytes(
                data=audio_data,
                mime_type=mime_type,
            ),
        ]

        try:
            # google-genai 的 generate_content 是同步的，
            # 但 aio 子模組提供非同步版本
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            )
        except Exception as e:
            logger.error("Gemini Native Audio Evaluator API 呼叫失敗: %s", e)
            raise LLMServiceError(
                f"Gemini Native Audio Evaluator API 失敗: {e}"
            ) from e

        content = response.text
        if not content:
            raise LLMServiceError(
                "Gemini Native Audio Evaluator 回傳空內容。"
            )

        # 清理可能的 Markdown 格式
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            parsed: dict[str, object] = json.loads(cleaned)
            return AudioEvaluationResult(**parsed)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(
                "Gemini Native Audio Evaluator 回傳格式錯誤: %s", content
            )
            raise LLMServiceError(
                f"語音評分結果格式錯誤: {e}"
            ) from e
