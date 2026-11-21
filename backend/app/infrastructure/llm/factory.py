"""LLM client factory module (plan D9: registry + unified argument passing).

LLM 客戶端工廠模組（計畫 D9：註冊表 + 統一參數傳遞）。

Takes the client class from the registry according to ``settings.LLM_PROVIDER`` (or the type of the given
endpoint) and creates an instance. Registry values are ``_ProviderEntry``: a module name, a class name, plus two
**static** capability flags (``is_cli`` / ``supports_effort``). The flags are declared statically so queries
such as "is this provider a CLI" or "does it support effort" need not import any provider module; the
diagnostics endpoint and the error messages must keep working when the environment is broken (missing
dependencies). The flags are **checked against the class's own declarations at load time** and raise on
mismatch, so they cannot drift.

A module is loaded with ``importlib`` only when selected, so third-party dependencies (jsonschema / anthropic /
google-genai) do not become startup prerequisites of other providers. Unregistered provider values (google /
openai / relay names / empty) always return the default :class:`~app.infrastructure.llm.client.LLMClient`.

Every client takes the same three arguments ``model`` / ``effort`` / ``endpoint``; providers that do not support
effort are rejected by :class:`~app.infrastructure.llm.base.BaseLLMClient` according to ``SUPPORTS_EFFORT``.

When an endpoint is given (model-routing plan llm_model_routing_FEAT_2026-10-15.md D5), the class is chosen by
the endpoint type and the endpoint is passed to the constructor; the URL, key, and label prefix all come from the
endpoint. When no endpoint is given: if ``LLM_PROVIDER`` names an endpoint defined in ``LLM_ENDPOINTS``, that
endpoint is used; otherwise the path is exactly the same as before endpoints were introduced (no ``endpoint`` is
passed, and the client reads the legacy single-set settings).

To add a provider, add one row to ``_REGISTRY``; wrong flags are caught at the first resolution.

依 ``settings.LLM_PROVIDER``（或指定端點的類型）從註冊表取得客戶端類別並建立實例。註冊表的值是
``_ProviderEntry``：模組名、類別名，加上兩個**靜態**能力旗標（``is_cli`` /
``supports_effort``）。旗標靜態宣告是為了讓「這個 provider 是不是 CLI」「支不支援
effort」這類查詢不必 import 任何 provider 模組——診斷端點與錯誤訊息都要在
環境壞掉（依賴缺失）時仍能運作。旗標與類別本身的宣告在**載入時核對**，不一致
即拋錯，所以不會漂移。

模組在被選中時才 ``importlib`` 載入——第三方依賴（jsonschema / anthropic /
google-genai）不成為其他 provider 的啟動前提。未註冊的 provider 值（google /
openai / 各中轉站名 / 空）一律回傳預設的 ``LLMClient``。

所有客戶端一律收 ``model`` / ``effort`` / ``endpoint`` 三個參數；不支援 effort 的
provider 由 ``BaseLLMClient`` 依 ``SUPPORTS_EFFORT`` 拒絕。

指定端點（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D5）時改依端點類型選
類別，並把端點傳給建構子——網址、金鑰與標籤前綴都取自端點。未指定端點時：
``LLM_PROVIDER`` 指向 ``LLM_ENDPOINTS`` 中定義的端點就走它；否則路徑與導入端點之前
完全相同（不傳 ``endpoint``，客戶端讀舊式單組設定）。

新增 provider 的作法：在 ``_REGISTRY`` 加一列即可；旗標填錯會在第一次解析時被抓到。
"""

import importlib
import logging
from dataclasses import dataclass

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm.base import BaseLLMClient, EffortArg
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.endpoints import env_endpoint_id, env_endpoint_is_custom, get_endpoint, skip_reason
from app.infrastructure.llm.labels import normalize_provider

logger = logging.getLogger(__name__)

# 觸發 headless CLI provider 的 LLM_PROVIDER 值
CLAUDE_CODE_PROVIDER = "claude-code"
ANTIGRAVITY_PROVIDER = "antigravity"

# 觸發 Anthropic 官方 API provider 的 LLM_PROVIDER 值（目前為 STUB）
ANTHROPIC_PROVIDER = "anthropic"

# 觸發 Gemini 原生 SDK provider 的 LLM_PROVIDER 值
GEMINI_NATIVE_PROVIDER = "gemini-native"


@dataclass(frozen=True)
class _ProviderEntry:
    """One row of the registry.

    註冊表的一列。

    Attributes:
        module: the module name under ``app.infrastructure.llm``
            ``app.infrastructure.llm`` 下的模組名
        class_name: the client class name inside that module
            該模組內的客戶端類別名
        is_cli: whether it is a headless CLI type (driven by subprocess)
            是否為 headless CLI 型（subprocess 驅動）
        supports_effort: whether an ``effort`` override is accepted
            是否接受 ``effort`` 覆寫
    """

    module: str
    class_name: str
    is_cli: bool
    supports_effort: bool


# provider 值 → 註冊列。模組在被選中時才載入；旗標於載入時與類別核對。
_REGISTRY: dict[str, _ProviderEntry] = {
    CLAUDE_CODE_PROVIDER: _ProviderEntry(
        "claude_code_client", "ClaudeCodeLLMClient", is_cli=True, supports_effort=True,
    ),
    ANTIGRAVITY_PROVIDER: _ProviderEntry(
        "antigravity_client", "AntigravityLLMClient", is_cli=True, supports_effort=True,
    ),
    GEMINI_NATIVE_PROVIDER: _ProviderEntry(
        "gemini_native_client", "GeminiNativeLLMClient", is_cli=False, supports_effort=False,
    ),
    # 佔位 stub：實作本體時把 supports_effort 改 True（連同類別的 SUPPORTS_EFFORT）
    ANTHROPIC_PROVIDER: _ProviderEntry(
        "anthropic_client", "AnthropicLLMClient", is_cli=False, supports_effort=False,
    ),
}


def _verify_entry(key: str, entry: _ProviderEntry, client_cls: object) -> None:
    """Verify at load time that the registry row's static declarations match the class, preventing drift.

    載入時核對註冊列的靜態宣告與類別本身一致，防止兩處漂移。

    Tests often replace the class with ``MagicMock``; the check is skipped when it is not a real class.

    測試常以 ``MagicMock`` 替換類別；非真實類別時跳過核對。

    Raises:
        RuntimeError: if ``PROVIDER_NAME``, ``is_cli``, or ``supports_effort`` disagrees with the class
            若 ``PROVIDER_NAME``、``is_cli`` 或 ``supports_effort`` 與類別不一致
    """
    if not isinstance(client_cls, type):
        return
    # 以 MRO 名稱判斷而不 import cli_base：核對不該把 jsonschema 拉進非 CLI 路徑
    is_cli = any(base.__name__ == "HeadlessCliLLMClient" for base in client_cls.__mro__)
    problems: list[str] = []
    if getattr(client_cls, "PROVIDER_NAME", None) != key:
        problems.append(f"PROVIDER_NAME={getattr(client_cls, 'PROVIDER_NAME', None)!r} ≠ 註冊鍵 {key!r}")
    if is_cli != entry.is_cli:
        problems.append(f"is_cli 註冊為 {entry.is_cli} 但類別{'是' if is_cli else '不是'} HeadlessCliLLMClient")
    if bool(getattr(client_cls, "SUPPORTS_EFFORT", False)) != entry.supports_effort:
        problems.append(
            f"supports_effort 註冊為 {entry.supports_effort} 但類別 SUPPORTS_EFFORT="
            f"{getattr(client_cls, 'SUPPORTS_EFFORT', None)}"
        )
    if problems:
        raise RuntimeError(
            f"LLM provider 註冊表與 {client_cls.__name__} 宣告不一致: {'; '.join(problems)}"
        )


def resolve_client_class(provider: str | None = None) -> type[BaseLLMClient]:
    """Resolve the client class for a provider value without instantiating it.

    依 provider 值取得客戶端類別（不實例化）。

    Args:
        provider: the provider value; ``None`` reads ``settings.LLM_PROVIDER``
            provider 值；None 讀 ``settings.LLM_PROVIDER``

    Returns:
        the matching client class; ``LLMClient`` when unregistered
        對應的客戶端類別；未註冊時為 ``LLMClient``

    Raises:
        RuntimeError: if the row's static flags disagree with the class declarations (a development-time error)
            若註冊列的靜態旗標與類別宣告不一致（開發期錯誤）
    """
    key = normalize_provider(settings.LLM_PROVIDER if provider is None else provider)
    entry = _REGISTRY.get(key)
    if entry is None:
        return LLMClient

    module = importlib.import_module(f"app.infrastructure.llm.{entry.module}")
    client_cls = getattr(module, entry.class_name)
    _verify_entry(key, entry, client_cls)
    return client_cls


def is_cli_provider(provider: str | None) -> bool:
    """Return whether the provider is a headless CLI type.

    回傳該 provider 是否為 headless CLI 型。

    A pure lookup that never imports provider modules, so it works even when their dependencies are missing (the
    diagnostics endpoint relies on this).

    純查表，不 import 任何 provider 模組，因此依賴缺失時仍可運作（診斷端點依賴這點）。

    Args:
        provider: the provider value; case and surrounding whitespace are ignored
            provider 值；忽略大小寫與前後空白

    Returns:
        ``True`` if the provider is registered as a CLI
        provider 註冊為 CLI 型時為 ``True``
    """
    entry = _REGISTRY.get(normalize_provider(provider))
    return bool(entry and entry.is_cli)


def providers_supporting_effort() -> list[str]:
    """Return the provider values that support an ``effort`` override (a pure lookup, for error messages).

    回傳支援 ``effort`` 覆寫的 provider 值清單（純查表，供錯誤訊息用）。

    Returns:
        the provider values in registration order
        依註冊順序排列的 provider 值
    """
    return [key for key, entry in _REGISTRY.items() if entry.supports_effort]


def create_llm_client(
    *,
    endpoint: str | None = None,
    model: str | None = None,
    effort: EffortArg = None,
) -> BaseLLMClient:
    """Create the matching LLM client from settings (optionally naming an endpoint or overriding model / effort).

    依設定建立對應的 LLM 客戶端（可指定端點、覆寫模型 / 思考深度）。

    Overrides apply only to the returned instance (request scope); settings and ``app.state.llm_client`` are not
    changed.

    覆寫只作用在回傳的這個實例（請求範圍），不改 settings、不動
    ``app.state.llm_client``。

    Args:
        endpoint: the endpoint ID (see :mod:`~app.infrastructure.llm.endpoints`); ``None`` uses ``LLM_PROVIDER``
            (when it names an endpoint defined in ``LLM_ENDPOINTS``, that endpoint is used; otherwise the legacy
            single-set settings are read)
            端點 ID（見 ``app.infrastructure.llm.endpoints``）；None
            用 ``LLM_PROVIDER``（它指向 ``LLM_ENDPOINTS`` 定義的端點時走該端點，
            否則讀舊式單組設定）
        model: the model-name override; ``None`` keeps ``LLM_MODEL_NAME`` (usually give it together with an
            endpoint, because ``None`` ignores the endpoint's allowlist)
            覆寫模型名；None 沿用 ``LLM_MODEL_NAME``（指定端點時通常應一併
            指定，None 不看端點的白名單）
        effort: the thinking-depth override; ``None`` keeps the provider's setting, ``OMIT_EFFORT`` means not
            sending one. Giving a value to an unsupported provider raises.
            覆寫思考深度；None 沿用該 provider 的設定，``OMIT_EFFORT``
            表示不傳。不支援的 provider 給值即拋錯。

    Returns:
        the matching client
        對應的客戶端

    Raises:
        LLMServiceError: if the endpoint does not exist, the chosen client fails to initialize (for example the
            API key is unset, the effort value is invalid or unsupported, the CLI executable is not found), or a
            not-yet-implemented provider is selected
            若端點不存在、所選客戶端初始化失敗（如 API 金鑰未設、
            effort 值非法或不受支援、找不到 CLI 執行檔），或選到尚未實作的 provider
    """
    if endpoint is not None:
        return _create_for_endpoint(endpoint, model=model, effort=effort)
    if env_endpoint_is_custom():
        # LLM_PROVIDER 指向 LLM_ENDPOINTS 的端點：用它的網址與金鑰，而不是舊的
        # LLM_BASE_URL / LLM_API_KEY；寫錯被跳過時明確報「端點不存在」
        return _create_for_endpoint(env_endpoint_id(), model=model, effort=effort)

    provider = normalize_provider(settings.LLM_PROVIDER)
    client_cls = resolve_client_class(provider)

    if client_cls is LLMClient:
        logger.debug("LLM Provider = '%s'，使用 OpenAI 相容 LLMClient。", provider or "google")
    else:
        # getattr 而非直取 __name__：測試以 MagicMock 替換類別時沒有此屬性
        logger.info(
            "LLM Provider = %s，使用 %s。",
            provider,
            getattr(client_cls, "__name__", repr(client_cls)),
        )

    return client_cls(model=model, effort=effort)


def _create_for_endpoint(
    endpoint_id: str, *, model: str | None, effort: EffortArg
) -> BaseLLMClient:
    """Create the client matching the endpoint's type and hand the endpoint to its constructor.

    依端點類型建立客戶端，並把端點交給建構子。

    Raises:
        LLMServiceError: if the endpoint does not exist (undefined, or skipped because of a wrong setting) or the
            client fails to initialize
            若端點不存在（未定義，或設定有誤已被跳過）或客戶端初始化失敗
    """
    endpoint = get_endpoint(endpoint_id)
    if endpoint is None:
        reason = skip_reason(endpoint_id)
        detail = (
            f"LLM_ENDPOINTS__{endpoint_id.strip().upper()} 設定有誤已被跳過——{reason}"
            if reason
            else "未在 .env 定義"
        )
        raise LLMServiceError(f"LLM 端點 '{endpoint_id}' 不存在：{detail}。")
    # openai 類不在註冊表，resolve_client_class 回 LLMClient
    client_cls = resolve_client_class(endpoint.type)
    logger.debug(
        "LLM 端點 = %s（%s），使用 %s。",
        endpoint.id,
        endpoint.type,
        getattr(client_cls, "__name__", repr(client_cls)),
    )
    return client_cls(model=model, effort=effort, endpoint=endpoint)
