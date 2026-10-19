"""
能力與可選的 provider（能力路由計畫 capability_routing_FEAT_2026-10-19.md §3、§6.3、§6.4）。

一個「能力」是一種工具；設定表的每一列是「用途鍵 × 能力 → (provider, 模型, effort)」。
本模組定義有哪些能力、每種能力可選哪些 provider 與模型（白名單）、``.env`` 那一層的預設，
以及檢查一組選擇是否合格。解析順序與快取在 :mod:`app.infrastructure.llm.routing`。

| 能力 | provider | 模型 | 白名單來源 |
|---|---|---|---|
| ``llm`` | LLM 端點 ID | 端點的文字模型（可帶 effort） | ``LLM_ENDPOINTS__<ID>__MODELS`` 等 |
| ``audio_llm`` | 能聽音檔的端點 ID | 端點的音訊模型 | ``LLM_ENDPOINTS__<ID>__AUDIO_MODELS``、``AUDIO_MODELS`` |
| ``stt`` | ``whisper`` | Whisper 模型 | ``STT_MODELS``＋``STT_MODEL_NAME`` |
| ``audio_eval`` | 策略 ``listen`` / ``stt_diff`` / ``stt_llm`` | 無 | 程式中的固定清單（用途鍵可再限縮） |

``audio_eval`` 只記策略；策略需要的其他能力（:data:`STRATEGY_CAPABILITIES`）以同一個用途鍵
各自解析。本模組不 import 任何客戶端類別。

English summary:
    The capabilities a route key can configure, the providers and models
    selectable for each (whitelists, all from ``.env`` or fixed lists),
    the ``.env`` defaults, and choice validation.
"""

from dataclasses import dataclass

from app.core.config import settings
from app.infrastructure.llm.endpoints import (
    AUDIO_ENDPOINT_ID,
    Endpoint,
    ModelOption,
    coerce_entries,
    get_endpoint,
    list_audio_endpoints,
    list_endpoints,
    parse_audio_whitelist,
)

# 能力（計畫書 §3.1、§4.2）
CAPABILITY_LLM = "llm"
CAPABILITY_AUDIO_LLM = "audio_llm"
CAPABILITY_STT = "stt"
CAPABILITY_AUDIO_EVAL = "audio_eval"

# 顯示順序：先文字生成，再錄音評分的策略與它用到的能力
CAPABILITY_ORDER: tuple[str, ...] = (CAPABILITY_LLM, CAPABILITY_AUDIO_EVAL, CAPABILITY_AUDIO_LLM, CAPABILITY_STT)
CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_ORDER)

# 給人看的能力名稱（TG 選單、錯誤訊息）
CAPABILITY_TITLES: dict[str, str] = {
    CAPABILITY_LLM: "文字 LLM",
    CAPABILITY_AUDIO_EVAL: "評分策略",
    CAPABILITY_AUDIO_LLM: "聽音檔的模型",
    CAPABILITY_STT: "語音轉文字",
}

# 錄音評分策略（audio_eval 的 provider）
STRATEGY_LISTEN = "listen"
STRATEGY_STT_DIFF = "stt_diff"
STRATEGY_STT_LLM = "stt_llm"
STRATEGIES: tuple[str, ...] = (STRATEGY_LISTEN, STRATEGY_STT_DIFF, STRATEGY_STT_LLM)

STRATEGY_TITLES: dict[str, str] = {
    STRATEGY_LISTEN: "模型直接聽音檔評分",
    STRATEGY_STT_DIFF: "Whisper 轉文字後與參考答案逐字比對（零成本）",
    STRATEGY_STT_LLM: "Whisper 轉文字後由文字 LLM 評分（低成本）",
}

# 各策略會用到的其他能力（以同一個用途鍵解析）
STRATEGY_CAPABILITIES: dict[str, tuple[str, ...]] = {
    STRATEGY_LISTEN: (CAPABILITY_AUDIO_LLM,),
    STRATEGY_STT_DIFF: (CAPABILITY_STT,),
    STRATEGY_STT_LLM: (CAPABILITY_STT, CAPABILITY_LLM),
}

# AUDIO_EVALUATOR_PROVIDER（.env）→ 策略
_ENV_STRATEGIES: dict[str, str] = {
    "gemini_native": STRATEGY_LISTEN,
    "openai": STRATEGY_LISTEN,
    "proxy": STRATEGY_LISTEN,
    STRATEGY_STT_DIFF: STRATEGY_STT_DIFF,
    STRATEGY_STT_LLM: STRATEGY_STT_LLM,
}

# stt 的 provider：自架 Whisper（Speaches）
STT_WHISPER = "whisper"

# 工具能力的 provider 類型（顯示用）
TYPE_STRATEGY = "strategy"
TYPE_WHISPER = "whisper"


@dataclass(frozen=True)
class RouteChoice:
    """一組選擇（DB 中的一列，或 ``.env`` 那一層的預設；尚未比對白名單）。

    One choice: a stored row or an ``.env`` default, not yet checked.

    Attributes:
        endpoint: provider：``llm`` / ``audio_llm`` 為端點 ID，``stt`` 為 ``whisper``，
            ``audio_eval`` 為策略名。The provider (endpoint ID, ``whisper`` or a strategy).
        model: 模型名；策略類為空字串。Model name, empty for strategies.
        effort: 思考深度；None＝不使用 effort（空字串正規化為 None）。
            Effort, or None ("" normalizes to None).
    """

    endpoint: str
    model: str
    effort: str | None

    def __post_init__(self) -> None:
        if self.effort == "":
            object.__setattr__(self, "effort", None)

    @property
    def provider(self) -> str:
        """provider（與 ``endpoint`` 相同；``endpoint`` 是 R1 前的舊名）。The provider."""
        return self.endpoint


@dataclass(frozen=True)
class ProviderOption:
    """某能力可選的一個 provider 與它的模型白名單。One selectable provider.

    Attributes:
        id: provider ID。Provider ID.
        type: 類型（端點類型、``whisper`` 或 ``strategy``）。Its type.
        models: 可選的模型；空＝這個 provider 不選模型（策略）。Selectable
            models; empty for providers without a model.
        title: 給人看的說明（策略才有）。Description, for strategies.
    """

    id: str
    type: str
    models: tuple[ModelOption, ...] = ()
    title: str | None = None

    @property
    def needs_model(self) -> bool:
        """是否要選模型。Whether a model must be chosen."""
        return bool(self.models)

    def find_model(self, name: str) -> ModelOption | None:
        """白名單中名為 ``name`` 的模型。The model named ``name``."""
        return next((option for option in self.models if option.name == name), None)


def env_strategy() -> str:
    """``.env`` 的 ``AUDIO_EVALUATOR_PROVIDER`` 換成策略；不認得的值原樣（之後檢查時報錯）。

    The ``.env`` default strategy; unknown values are kept so they surface.
    """
    raw = (settings.AUDIO_EVALUATOR_PROVIDER or "").strip().lower()
    return _ENV_STRATEGIES.get(raw, raw)


def stt_models() -> tuple[ModelOption, ...]:
    """whisper 可選的模型：``STT_MODELS`` 加上 ``STT_MODEL_NAME``。The Whisper whitelist."""
    entries = coerce_entries(settings.STT_MODELS, setting_name="STT_MODELS") or ()
    models = list(parse_audio_whitelist(entries, setting_name="STT_MODELS"))
    default = (settings.STT_MODEL_NAME or "").strip()
    if default and all(option.name != default for option in models):
        models.append(ModelOption(default))
    return tuple(models)


def stt_configured() -> bool:
    """STT 伺服器是否已設定（``STT_SERVER_URL``）。Whether the STT server is configured."""
    return bool((settings.STT_SERVER_URL or "").strip())


def _endpoint_option(endpoint: Endpoint, *, audio: bool) -> ProviderOption:
    return ProviderOption(endpoint.id, endpoint.type, endpoint.audio_models if audio else endpoint.models)


def list_providers(capability: str, *, strategies: frozenset[str] | None = None) -> list[ProviderOption]:
    """某能力可選的 provider（白名單非空的才列）。The selectable providers of a capability.

    Args:
        capability: 能力。The capability.
        strategies: ``audio_eval`` 時只列這些策略（用途鍵的限縮）；None 為全部。
            Strategies allowed by the route key, or None for all.

    Raises:
        ValueError: 未知的能力。Unknown capability.
    """
    if capability == CAPABILITY_LLM:
        return [_endpoint_option(endpoint, audio=False) for endpoint in list_endpoints()]
    if capability == CAPABILITY_AUDIO_LLM:
        return [_endpoint_option(endpoint, audio=True) for endpoint in list_audio_endpoints()]
    if capability == CAPABILITY_STT:
        models = stt_models()
        return [ProviderOption(STT_WHISPER, TYPE_WHISPER, models)] if stt_configured() and models else []
    if capability == CAPABILITY_AUDIO_EVAL:
        return [
            ProviderOption(strategy, TYPE_STRATEGY, title=STRATEGY_TITLES[strategy])
            for strategy in STRATEGIES
            if strategies is None or strategy in strategies
        ]
    raise ValueError(f"未知的能力 {capability!r}")


def env_default(capability: str) -> RouteChoice | None:
    """``.env`` 那一層的預設；``llm`` 為 None（客戶端走不帶參數的舊路徑，見 routing）。

    The ``.env`` default of a capability; None for ``llm``, whose client is
    built exactly as before routing existed.
    """
    if capability == CAPABILITY_AUDIO_LLM:
        return RouteChoice(AUDIO_ENDPOINT_ID, (settings.AUDIO_MODEL_NAME or "").strip(), None)
    if capability == CAPABILITY_STT:
        return RouteChoice(STT_WHISPER, (settings.STT_MODEL_NAME or "").strip(), None)
    if capability == CAPABILITY_AUDIO_EVAL:
        return RouteChoice(env_strategy(), "", None)
    return None


def audio_text_default() -> RouteChoice | None:
    """錄音評分的 ``llm`` 在 ``.env`` 那一層的預設：audio 端點加上 ``STT_LLM_MODEL_NAME``。

    與能力路由之前的 ``stt_llm`` 相同（憑證沿用 ``AUDIO_*``），不會因為建卡用 Claude Code
    就把錄音評分也改成 Claude Code。``STT_LLM_MODEL_NAME`` 留空時為 None（退回 ``LLM_*``）。

    The ``.env`` default of ``llm`` for recording evaluation: the ``audio``
    endpoint with ``STT_LLM_MODEL_NAME``, as ``stt_llm`` did before.
    """
    model = (settings.STT_LLM_MODEL_NAME or "").strip()
    return RouteChoice(AUDIO_ENDPOINT_ID, model, None) if model else None


def _check_model(option: ModelOption | None, choice: RouteChoice, owner: str) -> str | None:
    if option is None:
        return f"模型 '{choice.model}' 不在 {owner} 的白名單"
    if choice.effort:
        return f"模型 '{choice.model}' 不使用 effort，卻設定了 '{choice.effort}'"
    return None


def check_choice(
    capability: str, choice: RouteChoice, *, strategies: frozenset[str] | None = None
) -> str | None:
    """檢查一組選擇是否在白名單內。Check a choice against the whitelist.

    Args:
        capability: 能力。The capability.
        choice: 選擇。The choice.
        strategies: ``audio_eval`` 時用途鍵允許的策略；None 為全部。Allowed strategies.

    Returns:
        不合格的原因；合格時為 None。Why it is invalid, or None.
    """
    if capability == CAPABILITY_LLM:
        return _check_llm(choice)
    if capability == CAPABILITY_AUDIO_LLM:
        endpoint = get_endpoint(choice.endpoint)
        if endpoint is None:
            return f"端點 '{choice.endpoint}' 不存在"
        if not endpoint.listed:
            return f"端點 '{choice.endpoint}' 不可選（{endpoint.api_key_setting} 未設定或僅供 .env 那一層使用）"
        if not endpoint.audio_models:
            return f"端點 '{choice.endpoint}' 沒有音訊模型白名單，不能聽音檔"
        return _check_model(endpoint.find_audio_model(choice.model), choice, f"端點 '{endpoint.id}' 音訊模型")
    if capability == CAPABILITY_STT:
        if choice.endpoint != STT_WHISPER:
            return f"語音轉文字的 provider 只能是 '{STT_WHISPER}'，不是 '{choice.endpoint}'"
        if not stt_configured():
            return "STT_SERVER_URL 未設定，無法使用 whisper"
        option = next((option for option in stt_models() if option.name == choice.model), None)
        return _check_model(option, choice, "whisper")
    if capability == CAPABILITY_AUDIO_EVAL:
        if choice.endpoint not in STRATEGIES:
            return f"評分策略只能是 {' / '.join(STRATEGIES)}，不是 '{choice.endpoint}'"
        if strategies is not None and choice.endpoint not in strategies:
            return f"這個用途不允許策略 '{choice.endpoint}'（可用：{' / '.join(s for s in STRATEGIES if s in strategies)}）"
        if choice.model or choice.effort:
            return "評分策略不選模型與 effort（模型請設在它用到的能力上）"
        return None
    return f"不支援的能力 '{capability}'"


def _check_llm(choice: RouteChoice) -> str | None:
    endpoint = get_endpoint(choice.endpoint)
    if endpoint is None:
        return f"端點 '{choice.endpoint}' 不存在"
    if not endpoint.listed:
        return f"端點 '{choice.endpoint}' 僅供 .env 那一層使用，不可選擇"
    option = endpoint.find_model(choice.model)
    if option is None:
        return f"模型 '{choice.model}' 不在端點 '{endpoint.id}' 的白名單"
    if option.allows(choice.effort):
        return None
    if not option.efforts:
        return f"模型 '{choice.model}' 不使用 effort，卻設定了 '{choice.effort}'"
    return (
        f"effort '{choice.effort or '（未設定）'}' 不在模型 '{choice.model}' 的白名單"
        f"（可用：{'|'.join(option.efforts)}）"
    )
