"""Capabilities and their selectable providers.

能力與可選的 provider（能力路由計畫 capability_routing_FEAT_2026-10-19.md §3、§6.3、§6.4）。

See the capability-routing plan capability_routing_FEAT_2026-10-19.md §3, §6.3, and §6.4. A "capability" is a
kind of tool; each row of the settings table is "route key × capability → (provider, model, effort)". This
module defines which capabilities exist, which providers and models each capability may select (the
allowlists), the defaults of the ``.env`` layer, and the check of whether a choice is valid. The resolution
order and caching live in :mod:`app.infrastructure.llm.routing`.

一個「能力」是一種工具；設定表的每一列是「用途鍵 × 能力 → (provider, 模型, effort)」。
本模組定義有哪些能力、每種能力可選哪些 provider 與模型（白名單）、``.env`` 那一層的預設，
以及檢查一組選擇是否合格。解析順序與快取在 ``app.infrastructure.llm.routing``。

| capability | provider | model | allowlist source |
|---|---|---|---|
| ``llm`` | LLM endpoint ID | endpoint text models (may carry effort) | ``LLM_ENDPOINTS__<ID>__MODELS`` etc. |
| ``audio_llm`` | audio endpoint ID | endpoint audio models | ``LLM_ENDPOINTS__<ID>__AUDIO_MODELS``, ``AUDIO_MODELS`` |
| ``stt`` | ``whisper`` | Whisper models | ``STT_MODELS`` + ``STT_MODEL_NAME`` |
| ``audio_eval`` | ``listen`` / ``stt_diff`` / ``stt_llm`` | none | fixed list in code (route key may narrow) |

| 能力 | provider | 模型 | 白名單來源 |
|---|---|---|---|
| ``llm`` | LLM 端點 ID | 端點的文字模型（可帶 effort） | ``LLM_ENDPOINTS__<ID>__MODELS`` 等 |
| ``audio_llm`` | 能聽音檔的端點 ID | 端點的音訊模型 | ``LLM_ENDPOINTS__<ID>__AUDIO_MODELS``、``AUDIO_MODELS`` |
| ``stt`` | ``whisper`` | Whisper 模型 | ``STT_MODELS``＋``STT_MODEL_NAME`` |
| ``audio_eval`` | 策略 ``listen`` / ``stt_diff`` / ``stt_llm`` | 無 | 程式中的固定清單（用途鍵可再限縮） |

``audio_eval`` records only the strategy; the other capabilities a strategy needs (:data:`STRATEGY_CAPABILITIES`)
are each resolved with the same route key. This module imports no client class.

``audio_eval`` 只記策略；策略需要的其他能力（``STRATEGY_CAPABILITIES``）以同一個用途鍵
各自解析。本模組不 import 任何客戶端類別。
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
    """One choice (a row in the DB, or a default of the ``.env`` layer; not yet checked against the allowlist).

    一組選擇（DB 中的一列，或 ``.env`` 那一層的預設；尚未比對白名單）。

    Attributes:
        endpoint: the provider: an endpoint ID for ``llm`` / ``audio_llm``, ``whisper`` for ``stt``, a strategy
            name for ``audio_eval``
            provider：``llm`` / ``audio_llm`` 為端點 ID，``stt`` 為 ``whisper``，``audio_eval`` 為策略名
        model: the model name; an empty string for strategies
            模型名；策略類為空字串
        effort: the thinking depth; ``None`` means no effort (an empty string is normalized to ``None``)
            思考深度；None＝不使用 effort（空字串正規化為 None）
    """

    endpoint: str
    model: str
    effort: str | None

    def __post_init__(self) -> None:
        """Normalize an empty-string ``effort`` to ``None``.

        把空字串的 ``effort`` 正規化為 None。
        """
        if self.effort == "":
            object.__setattr__(self, "effort", None)

    @property
    def provider(self) -> str:
        """The provider (same as ``endpoint``; ``endpoint`` is the name from before R1).

        provider（與 ``endpoint`` 相同；``endpoint`` 是 R1 前的舊名）。
        """
        return self.endpoint


@dataclass(frozen=True)
class ProviderOption:
    """One selectable provider of a capability, with its model allowlist.

    某能力可選的一個 provider 與它的模型白名單。

    Attributes:
        id: the provider ID
            provider ID
        type: the type (an endpoint type, ``whisper``, or ``strategy``)
            類型（端點類型、``whisper`` 或 ``strategy``）
        models: the selectable models; empty means this provider takes no model (strategies)
            可選的模型；空＝這個 provider 不選模型（策略）
        title: the human-readable description (strategies only)
            給人看的說明（策略才有）
    """

    id: str
    type: str
    models: tuple[ModelOption, ...] = ()
    title: str | None = None

    @property
    def needs_model(self) -> bool:
        """Whether a model must be chosen.

        是否要選模型。
        """
        return bool(self.models)

    def find_model(self, name: str) -> ModelOption | None:
        """Return the model named ``name`` in the allowlist.

        回傳白名單中名為 ``name`` 的模型。

        Args:
            name: the model name to look up
                要查找的模型名

        Returns:
            the matching model, or ``None`` if it is not in the allowlist
            符合的模型；不在白名單時為 None
        """
        return next((option for option in self.models if option.name == name), None)


def env_strategy() -> str:
    """Return the strategy that the ``.env`` ``AUDIO_EVALUATOR_PROVIDER`` maps to.

    把 ``.env`` 的 ``AUDIO_EVALUATOR_PROVIDER`` 換成策略。

    Unrecognized values are returned as is (after stripping and lowercasing), so the later check reports them.

    不認得的值原樣回傳（去空白、小寫後），之後檢查時報錯。

    Returns:
        the strategy name, or the normalized raw value when it is not recognized
        策略名；不認得時為正規化後的原值
    """
    raw = (settings.AUDIO_EVALUATOR_PROVIDER or "").strip().lower()
    return _ENV_STRATEGIES.get(raw, raw)


def stt_models() -> tuple[ModelOption, ...]:
    """Return the models selectable for whisper: ``STT_MODELS`` plus ``STT_MODEL_NAME``.

    回傳 whisper 可選的模型：``STT_MODELS`` 加上 ``STT_MODEL_NAME``。

    ``STT_MODEL_NAME`` is appended at the end only when it is set and not already listed.

    ``STT_MODEL_NAME`` 只在有設定且未列出時才附加在最後。

    Returns:
        the Whisper allowlist; may be empty
        Whisper 白名單；可能為空
    """
    entries = coerce_entries(settings.STT_MODELS, setting_name="STT_MODELS") or ()
    models = list(parse_audio_whitelist(entries, setting_name="STT_MODELS"))
    default = (settings.STT_MODEL_NAME or "").strip()
    if default and all(option.name != default for option in models):
        models.append(ModelOption(default))
    return tuple(models)


def stt_configured() -> bool:
    """Return whether the STT server is configured (``STT_SERVER_URL``).

    回傳 STT 伺服器是否已設定（``STT_SERVER_URL``）。

    Returns:
        ``True`` if ``STT_SERVER_URL`` is non-blank
        ``STT_SERVER_URL`` 非空白時為 ``True``
    """
    return bool((settings.STT_SERVER_URL or "").strip())


def _endpoint_option(endpoint: Endpoint, *, audio: bool) -> ProviderOption:
    return ProviderOption(endpoint.id, endpoint.type, endpoint.audio_models if audio else endpoint.models)


def list_providers(capability: str, *, strategies: frozenset[str] | None = None) -> list[ProviderOption]:
    """Return the selectable providers of a capability.

    回傳某能力可選的 provider。

    For ``llm`` and ``audio_llm`` these are the endpoints from ``list_endpoints()`` / ``list_audio_endpoints()``;
    ``stt`` lists whisper only when the STT server is configured and its allowlist is non-empty.

    ``llm`` 與 ``audio_llm`` 為 ``list_endpoints()`` / ``list_audio_endpoints()`` 的端點；
    ``stt`` 只在 STT 伺服器已設定且白名單非空時才列出 whisper。

    Args:
        capability: the capability
            能力
        strategies: for ``audio_eval``, list only these strategies (the route key's narrowing); ``None`` means
            all
            ``audio_eval`` 時只列這些策略（用途鍵的限縮）；None 為全部

    Returns:
        the selectable providers; may be empty
        可選的 provider；可能為空

    Raises:
        ValueError: if the capability is unknown
            若能力未知
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
    """Return the ``.env``-layer default of a capability.

    回傳 ``.env`` 那一層的預設。

    Args:
        capability: the capability
            能力

    Returns:
        the default choice; ``None`` for ``llm`` (its client takes the legacy no-argument path, see routing) and
        for unknown capabilities
        預設選擇；``llm`` 為 None（客戶端走不帶參數的舊路徑，見 routing），未知能力也為 None
    """
    if capability == CAPABILITY_AUDIO_LLM:
        return RouteChoice(AUDIO_ENDPOINT_ID, (settings.AUDIO_MODEL_NAME or "").strip(), None)
    if capability == CAPABILITY_STT:
        return RouteChoice(STT_WHISPER, (settings.STT_MODEL_NAME or "").strip(), None)
    if capability == CAPABILITY_AUDIO_EVAL:
        return RouteChoice(env_strategy(), "", None)
    return None


def audio_text_default() -> RouteChoice | None:
    """Return the ``.env``-layer default of ``llm`` for recording evaluation.

    回傳錄音評分的 ``llm`` 在 ``.env`` 那一層的預設：audio 端點加上 ``STT_LLM_MODEL_NAME``。

    The default is the audio endpoint plus ``STT_LLM_MODEL_NAME``, the same as ``stt_llm`` before capability
    routing (credentials stay ``AUDIO_*``), so using Claude Code for card building does not also switch
    recording evaluation to Claude Code.

    與能力路由之前的 ``stt_llm`` 相同（憑證沿用 ``AUDIO_*``），不會因為建卡用 Claude Code
    就把錄音評分也改成 Claude Code。

    Returns:
        the default choice; ``None`` when ``STT_LLM_MODEL_NAME`` is blank (falling back to ``LLM_*``)
        預設選擇；``STT_LLM_MODEL_NAME`` 留空時為 None（退回 ``LLM_*``）
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
    """Check whether a choice is within the allowlist.

    檢查一組選擇是否在白名單內。

    An unknown capability is reported as a reason rather than raised.

    未知的能力以原因回報，不拋例外。

    Args:
        capability: the capability
            能力
        choice: the choice
            選擇
        strategies: for ``audio_eval``, the strategies the route key allows; ``None`` means all
            ``audio_eval`` 時用途鍵允許的策略；None 為全部

    Returns:
        the reason the choice is invalid; ``None`` when it is valid
        不合格的原因；合格時為 None
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
