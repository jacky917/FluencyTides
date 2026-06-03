"""
Audio Evaluator 抽象基底類別。

使用策略模式 (Strategy Pattern) 定義語音評分器的統一介面，
讓業務邏輯層完全解耦於特定的 LLM SDK 實作。

設計決策：
- 使用 ABC 而非 Protocol，因為此處需要強制子類實作 evaluate_audio，
  且未來可能在基底類中增加共用邏輯（如重試、快取）。
- 回傳 AudioEvaluationResult Pydantic 模型而非 dict，
  確保型別安全並遵守專案「零 Any」原則。
"""

import abc

from app.schemas.speaking import AudioEvaluationResult


class BaseAudioEvaluator(abc.ABC):
    """語音評分器的抽象基底類別。

    所有供應商實作（OpenAI 相容層、Google 原生 SDK 等）
    都必須繼承此類別並實作 evaluate_audio 方法。
    """

    @abc.abstractmethod
    async def evaluate_audio(
        self,
        audio_data: bytes,
        audio_filename: str,
        prompt_text: str,
        reference_answers: list[str],
    ) -> AudioEvaluationResult:
        """評估使用者語音，產出結構化評分結果。

        Args:
            audio_data: 音檔的原始二進位資料（.ogg 格式）。
            audio_filename: 音檔的原始檔名（含副檔名）。
            prompt_text: 卡片正面的 Prompt（對方的發言）。
            reference_answers: 參考範本回覆的純文字列表（0..* 筆）。

        Returns:
            AudioEvaluationResult 包含 score、status_code、feedback、transcript。

        Raises:
            LLMServiceError: LLM API 呼叫失敗時。
        """
        ...
