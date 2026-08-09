"""
Audio Evaluator 抽象基底類別。

Abstract base class for audio evaluators.

使用策略模式 (Strategy Pattern) 定義語音評分器的統一介面，
讓業務邏輯層完全解耦於特定的 LLM SDK 實作。

Defines a unified interface for speech evaluators via the Strategy Pattern,
fully decoupling the business layer from any specific LLM SDK implementation.

設計決策：
- 使用 ABC 而非 Protocol，因為此處需要強制子類實作 evaluate_audio，
  且未來可能在基底類中增加共用邏輯（如重試、快取）。
- 回傳 AudioEvaluationResult Pydantic 模型而非 dict，
  確保型別安全並遵守專案「零 Any」原則。

Design decisions:
- Uses ABC instead of Protocol to force subclasses to implement
  evaluate_audio, and to allow shared logic (retry, caching) in the base
  class later.
- Returns the AudioEvaluationResult Pydantic model instead of a dict for
  type safety, following the project's "zero Any" principle.
"""

import abc

from app.schemas.llm.speaking import AudioEvaluationResult


class BaseAudioEvaluator(abc.ABC):
    """語音評分器的抽象基底類別。

    Abstract base class for speech evaluators.

    所有供應商實作（OpenAI 相容層、Google 原生 SDK 等）
    都必須繼承此類別並實作 evaluate_audio 方法。

    Every provider implementation (OpenAI-compatible layer, Google native
    SDK, etc.) must inherit from this class and implement evaluate_audio.
    """

    @abc.abstractmethod
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
        """評估使用者語音，產出結構化評分結果。

        Evaluate the user's speech and produce a structured scoring result.

        Args:
            audio_data: 音檔的原始二進位資料（.ogg 格式）。Raw audio bytes
                (.ogg format).
            audio_filename: 音檔的原始檔名（含副檔名）。Original audio file
                name including its extension.
            prompt_text: 卡片正面的 Prompt（對方的發言）。The card-front
                prompt (the interlocutor's utterance).
            context_text: 卡片正面的 Context（文脈、背景情境）。The
                card-front context (background situation).
            reference_answers: 參考範本回覆的純文字列表（0..* 筆）。Plain-text
                reference answers (0 or more).
            target_language: 目標語言（如 'en-US'）。Target language, e.g.
                'en-US'.
            template_name: 評分提示詞 j2 樣板（預設為既有通用樣板；
                三語卡傳入 per-language 樣板，變數介面相同）。Jinja2 template
                for the evaluation prompt; trilingual cards pass a
                per-language template with the same variable interface.

        Returns:
            AudioEvaluationResult 包含 score、feedback、transcript。
            AudioEvaluationResult containing score, feedback and transcript.

        Raises:
            LLMServiceError: LLM API 呼叫失敗時。Raised when the LLM API call
                fails.
        """
        ...
