"""
Audio Evaluator 工廠模組。

使用工廠模式 (Factory Pattern) 根據環境變數
AUDIO_EVALUATOR_PROVIDER 動態實例化對應的語音評分器。

設計決策：
- 工廠函數而非工廠類別，因為不需要維持狀態。
- 延遲匯入具體實作類別，避免未安裝的 SDK 造成匯入錯誤。
  例如：若 PROVIDER='openai'，則不需要安裝 google-genai。
"""

import logging

from app.core.config import settings
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator

logger = logging.getLogger(__name__)


def create_audio_evaluator() -> BaseAudioEvaluator:
    """根據環境變數建立對應的 Audio Evaluator 實例。

    工廠模式確保業務邏輯層（Handlers、Services）完全不需要
    import 任何特定的 LLM SDK，實現完美的依賴反轉。

    Returns:
        BaseAudioEvaluator 的具體實作實例。

    Raises:
        ValueError: 當 AUDIO_EVALUATOR_PROVIDER 為不支援的值時。
    """
    provider = settings.AUDIO_EVALUATOR_PROVIDER.lower().strip()

    if provider == "openai":
        # 延遲匯入，避免在不使用 OpenAI 時仍需要安裝 openai 套件
        from app.infrastructure.audio_evaluator.openai_client import (
            OpenAIAudioEvaluator,
        )

        logger.info("Audio Evaluator 工廠: 建立 OpenAI 相容層實例。")
        return OpenAIAudioEvaluator()

    if provider == "gemini_native":
        # 延遲匯入，避免在不使用 Gemini 時仍需要安裝 google-genai 套件
        from app.infrastructure.audio_evaluator.gemini_client import (
            GeminiNativeAudioEvaluator,
        )

        logger.info("Audio Evaluator 工廠: 建立 Gemini Native SDK 實例。")
        return GeminiNativeAudioEvaluator()

    raise ValueError(
        f"不支援的 AUDIO_EVALUATOR_PROVIDER: '{provider}'。"
        f"可選值: 'openai', 'gemini_native'。"
    )
