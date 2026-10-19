"""
Audio Evaluator 工廠模組：依能力路由建立錄音評分器（能力路由計畫 capability_routing_FEAT_2026-10-19.md
§3.2、§8.3）。

Audio evaluator factory: builds the evaluator from the capability routes.

評分器不再讀全域 settings，而是依用途鍵（例如 ``recording_eval`` 或
``recording_eval.speaking_trilingual``）解析出的能力組成：

| ``audio_eval`` 策略 | 用到的能力 | 評分器 |
|---|---|---|
| ``listen`` | ``audio_llm`` | 端點類型決定：gemini-native → :class:`GeminiNativeAudioEvaluator`；openai →
  :class:`OpenAIAudioEvaluator`（``audio_transport=image_url`` 時為 :class:`ProxyAudioEvaluator`） |
| ``stt_diff`` | ``stt`` | :class:`STTDiffEvaluator` |
| ``stt_llm`` | ``stt``、``llm`` | :class:`STTLLMEvaluator` |

每個能力各自沿「分支 → 流程 → 全域 → .env」解析（見 :mod:`app.infrastructure.llm.routing`）。
建好的評分器依「設定組合」快取；設定一有寫入或刪除（路由快取世代改變）就整個清掉。

具體實作延遲匯入，未安裝的 SDK 不會影響其他策略（例如只用 stt_diff 時不需要 google-genai）。
建不出評分器（金鑰或伺服器未設定、套件未安裝、策略無效）時拋
:class:`~app.core.exceptions.AudioEvaluatorUnavailableError`，訊息說明是哪個能力、哪一層的設定。

English summary:
    Builds the recording evaluator from the capability routes of a route
    key: the ``audio_eval`` strategy picks the evaluator and the other
    capabilities supply its models. Evaluators are cached per combination
    and dropped whenever a route setting changes. Implementations are
    imported lazily.
"""

import logging
from dataclasses import dataclass

from app.core.config import settings
from app.core.exceptions import AudioEvaluatorUnavailableError, LLMServiceError, STTServiceError
from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.infrastructure.llm import routing
from app.infrastructure.llm.capabilities import (
    CAPABILITY_AUDIO_EVAL,
    CAPABILITY_AUDIO_LLM,
    CAPABILITY_LLM,
    CAPABILITY_STT,
    STRATEGIES,
    STRATEGY_CAPABILITIES,
    STRATEGY_LISTEN,
    STRATEGY_STT_DIFF,
    STT_WHISPER,
)
from app.infrastructure.llm.endpoints import (
    AUDIO_TRANSPORT_IMAGE_URL,
    TYPE_GEMINI_NATIVE,
    TYPE_OPENAI,
    get_endpoint,
)
from app.infrastructure.llm.routing import ResolvedRoute, describe_import_error, is_internal_import
from app.infrastructure.stt.whisper_client import WhisperClient

logger = logging.getLogger(__name__)

_SOURCE_NAMES = {"route": "用途", "parent": "流程", "global": "全域", "env": ".env"}


@dataclass(frozen=True)
class EvaluatorPlan:
    """一個用途鍵的評分器組成：策略與它用到的各能力的解析結果。

    What an evaluator for a route key is made of.

    Attributes:
        route_key: 用途鍵。The route key.
        strategy: ``audio_eval`` 的解析結果。The resolved strategy.
        routes: 策略用到的能力 → 解析結果。The resolved capabilities the
            strategy uses.
    """

    route_key: str
    strategy: ResolvedRoute
    routes: dict[str, ResolvedRoute]

    @property
    def strategy_name(self) -> str:
        """策略名。The strategy name."""
        return self.strategy.provider

    @property
    def cache_key(self) -> tuple[object, ...]:
        """快取鍵：策略與各能力生效的 (provider, 模型, effort)。The cache key."""
        parts: list[object] = [self.strategy_name]
        for capability in sorted(self.routes):
            resolved = self.routes[capability]
            choice = resolved.effective_choice
            parts.append(
                (capability, choice.endpoint, choice.model, choice.effort, resolved.choice is None)
                if choice is not None
                else (capability, None)
            )
        return tuple(parts)


async def plan_for(route_key: str) -> EvaluatorPlan:
    """解析用途鍵的策略，以及策略用到的能力。Resolve the strategy and what it needs.

    策略無效時（只會發生在 ``.env`` 那一層：DB 層不在白名單會被跳過）不解析其他能力，
    交給 :func:`build_evaluator` 報錯。
    """
    # 所有能力由同一份快照解析：讀取期間有寫入時，不會把新策略配上舊模型
    snapshot, read_ok = await routing.stored_choices()
    strategy = routing.resolve_with(route_key, snapshot, read_ok, CAPABILITY_AUDIO_EVAL)
    capabilities = STRATEGY_CAPABILITIES.get(strategy.provider, ())
    routes = {
        capability: routing.resolve_with(route_key, snapshot, read_ok, capability) for capability in capabilities
    }
    return EvaluatorPlan(route_key, strategy, routes)


def _where(resolved: ResolvedRoute) -> str:
    """「recording_eval 的 audio_llm（來源：全域）」這樣的說明。Where a setting came from."""
    return f"'{resolved.route_key}' 的 {resolved.capability}（來源：{_SOURCE_NAMES[resolved.source]}）"


def _unavailable(resolved: ResolvedRoute, reason: str) -> AudioEvaluatorUnavailableError:
    return AudioEvaluatorUnavailableError(
        f"錄音評分器無法建立：{_where(resolved)} 設定為 {resolved.provider} / {resolved.model or '（無）'}，"
        f"{reason}；可用 /setconfig 或網頁的工具設定修改。"
    )


def build_listen_evaluator(endpoint_id: str, model: str) -> BaseAudioEvaluator:
    """依端點建立「直接聽音檔」的評分器。Build a listening evaluator for an endpoint.

    Raises:
        AudioEvaluatorUnavailableError: 端點不存在或不能聽音檔。
        LLMServiceError: 評分器初始化失敗（金鑰或模型未設定）。
        ImportError: 端點需要的套件未安裝或版本不符。
    """
    endpoint = get_endpoint(endpoint_id)
    if endpoint is None:
        raise AudioEvaluatorUnavailableError(f"端點 '{endpoint_id}' 不存在")
    api_key = endpoint.api_key.get_secret_value() if endpoint.api_key else ""
    if endpoint.type == TYPE_GEMINI_NATIVE:
        from app.infrastructure.audio_evaluator.gemini_client import GeminiNativeAudioEvaluator

        return GeminiNativeAudioEvaluator(api_key=api_key, model=model, api_key_setting=endpoint.api_key_setting)
    if endpoint.type == TYPE_OPENAI:
        if endpoint.audio_transport == AUDIO_TRANSPORT_IMAGE_URL:
            from app.infrastructure.audio_evaluator.proxy_client import ProxyAudioEvaluator

            return ProxyAudioEvaluator(
                api_key=api_key, model=model, base_url=endpoint.base_url, api_key_setting=endpoint.api_key_setting
            )
        from app.infrastructure.audio_evaluator.openai_client import OpenAIAudioEvaluator

        return OpenAIAudioEvaluator(
            api_key=api_key, model=model, base_url=endpoint.base_url, api_key_setting=endpoint.api_key_setting
        )
    raise AudioEvaluatorUnavailableError(f"端點 '{endpoint_id}'（{endpoint.type}）不能聽音檔")


def build_whisper(provider: str, model: str) -> WhisperClient:
    """建立語音轉文字客戶端。Build the speech-to-text client.

    Raises:
        AudioEvaluatorUnavailableError: provider 不是 whisper。
        STTServiceError: ``STT_SERVER_URL`` 或模型未設定。
    """
    if provider != STT_WHISPER:
        raise AudioEvaluatorUnavailableError(f"語音轉文字只支援 '{STT_WHISPER}'，不是 '{provider}'")
    return WhisperClient(server_url=settings.STT_SERVER_URL, model=model, api_key=settings.STT_API_KEY)


def _build_part(resolved: ResolvedRoute) -> object:
    """建立一個能力的工具（聽音檔的評分器、Whisper 或 LLM 客戶端），失敗時說明是哪個設定。

    Build the tool for one capability, explaining which setting failed.

    Raises:
        AudioEvaluatorUnavailableError: 建不出來。
    """
    try:
        if resolved.capability == CAPABILITY_AUDIO_LLM:
            return build_listen_evaluator(resolved.provider, resolved.model)
        if resolved.capability == CAPABILITY_STT:
            return build_whisper(resolved.provider, resolved.model)
        if resolved.capability == CAPABILITY_LLM:
            return routing.client_from(resolved)
    except AudioEvaluatorUnavailableError as error:
        raise _unavailable(resolved, error.message) from error
    except (LLMServiceError, STTServiceError) as error:
        raise _unavailable(resolved, error.message) from error
    except ImportError as error:
        if is_internal_import(error):
            raise
        raise _unavailable(resolved, describe_import_error(resolved.provider, error)) from error
    raise ValueError(f"評分器用不到能力 {resolved.capability!r}")


def build_evaluator(plan: EvaluatorPlan) -> BaseAudioEvaluator:
    """依評分器組成建立評分器（不快取）。Build the evaluator for a plan, uncached.

    Raises:
        AudioEvaluatorUnavailableError: 策略無效，或策略用到的某個能力建不出工具。
    """
    strategy = plan.strategy_name
    if strategy not in STRATEGIES:
        raise AudioEvaluatorUnavailableError(
            f"錄音評分器無法建立：{_where(plan.strategy)} 的策略 '{strategy}' 無效"
            f"（可用：{' / '.join(STRATEGIES)}；.env 的 AUDIO_EVALUATOR_PROVIDER 可用 "
            "gemini_native / openai / proxy / stt_diff / stt_llm）；可用 /setconfig 或網頁的工具設定改設策略。"
        )
    if strategy == STRATEGY_LISTEN:
        evaluator = _build_part(plan.routes[CAPABILITY_AUDIO_LLM])
        assert isinstance(evaluator, BaseAudioEvaluator)
        return evaluator

    whisper = _build_part(plan.routes[CAPABILITY_STT])
    assert isinstance(whisper, WhisperClient)
    if strategy == STRATEGY_STT_DIFF:
        from app.infrastructure.audio_evaluator.stt_diff_evaluator import STTDiffEvaluator

        return STTDiffEvaluator(whisper)

    from app.infrastructure.audio_evaluator.stt_llm_evaluator import STTLLMEvaluator
    from app.infrastructure.llm.base import BaseLLMClient

    llm = _build_part(plan.routes[CAPABILITY_LLM])
    assert isinstance(llm, BaseLLMClient)
    return STTLLMEvaluator(whisper, llm)


# 設定組合 → 評分器；路由快取世代改變（有寫入或刪除）時整個清掉
_evaluators: dict[tuple[object, ...], BaseAudioEvaluator] = {}
_evaluators_generation: int | None = None


def clear_cache() -> None:
    """清掉已建立的評分器（測試或設定改變時用）。Drop every cached evaluator."""
    global _evaluators_generation
    _evaluators.clear()
    _evaluators_generation = None


async def evaluator_for(route_key: str) -> tuple[BaseAudioEvaluator, EvaluatorPlan]:
    """依用途鍵目前的設定取得評分器（依設定組合快取）。The evaluator for a route key.

    Args:
        route_key: 用途鍵（例如 ``recording_eval`` 或 ``recording_eval.speaking_trilingual``）。
            The route key.

    Returns:
        ``(評分器, 評分器組成)``。The evaluator and what it is made of.

    Raises:
        AudioEvaluatorUnavailableError: 建不出評分器（訊息說明是哪個設定）。
    """
    global _evaluators_generation
    generation = routing.cache_generation()
    if generation != _evaluators_generation:
        _evaluators.clear()
        _evaluators_generation = generation
    plan = await plan_for(route_key)
    evaluator = _evaluators.get(plan.cache_key)
    if evaluator is None:
        evaluator = build_evaluator(plan)
        _evaluators[plan.cache_key] = evaluator
        logger.info(
            "錄音評分器已建立：%s → %s（%s）",
            route_key, plan.strategy_name, type(evaluator).__name__,
        )
    return evaluator, plan
