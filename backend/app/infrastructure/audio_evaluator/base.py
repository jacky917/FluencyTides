"""Abstract base class for audio evaluators.

Audio Evaluator 抽象基底類別。

Defines a unified interface for speech evaluators via the Strategy Pattern,
fully decoupling the business layer from any specific LLM SDK implementation.

使用策略模式 (Strategy Pattern) 定義語音評分器的統一介面，
讓業務邏輯層完全解耦於特定的 LLM SDK 實作。

Design decisions:
- Uses ABC instead of Protocol, because subclasses must be forced to implement
  ``evaluate_audio``, and shared logic (for example retries or caching) may be
  added to the base class later.
- Returns the ``AudioEvaluationResult`` Pydantic model instead of a dict, for
  type safety and to follow the project's "zero Any" principle.

設計決策：
- 使用 ABC 而非 Protocol，因為此處需要強制子類實作 ``evaluate_audio``，
  且未來可能在基底類中增加共用邏輯（如重試、快取）。
- 回傳 ``AudioEvaluationResult`` Pydantic 模型而非 dict，
  確保型別安全並遵守專案「零 Any」原則。
"""

import abc

from app.schemas.llm.speaking import AudioEvaluationResult


class AudioNotScoredError(Exception):
    """An error meaning this recording could not be scored at all (not a score of 0).

    表示這次無法評分（不是 0 分）的例外。

    Examples are that no speech was detected, or that the card has no reference
    answers to compare against.

    例如沒偵測到語音、卡片沒有參考答案可比對。

    Callers do not write a scoring record and relay ``reason`` to the user.
    Previously ``score=0`` plus an explanation was returned, so downstream code
    could not tell "really scored 0" from "not scored": the card got an extra
    0-point record, and Telegram also showed "scoring complete".

    呼叫端不寫入評分紀錄，把 ``reason`` 轉告使用者。以前是回傳 ``score=0`` 加說明，下游分不出
    「真的 0 分」與「沒有評分」，卡片上會多一筆 0 分紀錄、TG 也顯示「評分完成」。

    Attributes:
        reason: the user-facing reason, in plain text
            給使用者看的原因（純文字）
    """

    def __init__(self, reason: str) -> None:
        """Create the error with the reason to show the user.

        以要給使用者看的原因建立此例外。

        Args:
            reason: the user-facing reason, in plain text
                給使用者看的原因（純文字）
        """
        super().__init__(reason)
        self.reason = reason


class BaseAudioEvaluator(abc.ABC):
    """Abstract base class for speech evaluators.

    語音評分器的抽象基底類別。

    Every provider implementation (OpenAI-compatible layer, Google native SDK,
    and so on) must inherit from this class and implement ``evaluate_audio``.

    所有供應商實作（OpenAI 相容層、Google 原生 SDK 等）
    都必須繼承此類別並實作 ``evaluate_audio`` 方法。
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
        reference_familiarity: list[int | None] | None = None,
    ) -> AudioEvaluationResult:
        """Evaluate the user's speech and produce a structured scoring result.

        評估使用者語音，產出結構化評分結果。

        Args:
            audio_data: the raw audio bytes (.ogg format)
                音檔的原始二進位資料（.ogg 格式）
            audio_filename: the original audio file name, including its extension
                音檔的原始檔名（含副檔名）
            prompt_text: the card-front Prompt (the other speaker's utterance)
                卡片正面的 Prompt（對方的發言）
            context_text: the card-front Context (the background situation)
                卡片正面的 Context（文脈、背景情境）
            reference_answers: the plain-text reference replies (zero or more)
                參考範本回覆的純文字列表（0..* 筆）
            target_language: the target language, for example ``'en-US'``
                目標語言（如 ``'en-US'``）
            template_name: the Jinja2 template for the evaluation prompt. The default is the
                existing generic template; trilingual cards pass a per-language template with the
                same variable interface.
                評分提示詞 j2 樣板（預設為既有通用樣板；三語卡傳入 per-language 樣板，變數介面相同）
            reference_familiarity: the familiarity (0–9, ``None`` when unmarked) of each reference
                answer, aligned one-to-one with ``reference_answers``; ``None`` or empty means there
                is none (not a trilingual card). LLM evaluators pass it to the template as
                ``reference_familiarity`` (only the trilingual templates use it); stt_diff notes the
                matched one in its feedback.
                每句參考答案的熟識度（0–9，未標為 ``None``），與 ``reference_answers`` 一一對應；
                ``None`` 或空表示都沒有（非三語卡）。LLM 類評分器交給樣板的
                ``reference_familiarity``（只有三語樣板會用），stt_diff 在 feedback 註明比對到的那句。

        Returns:
            an ``AudioEvaluationResult`` containing score, feedback and transcript
            包含 score、feedback、transcript 的 ``AudioEvaluationResult``

        Raises:
            AudioNotScoredError: if the recording cannot be scored at all (raised by stt_diff when
                no speech is detected or the card has no reference answers)
                若這次完全無法評分（stt_diff 在沒偵測到語音或卡片沒有參考答案時拋出）
            LLMServiceError: if the LLM API call fails
                若 LLM API 呼叫失敗
            STTServiceError: if the STT service call fails (STT-based evaluators)
                若 STT 服務呼叫失敗（以 STT 為基礎的評分器）
        """
        ...
