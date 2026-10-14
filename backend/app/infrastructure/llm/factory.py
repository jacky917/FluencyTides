"""
LLM 客戶端工廠模組（計畫 D9：註冊表 + 統一參數傳遞）。

依 ``settings.LLM_PROVIDER`` 從註冊表取得客戶端類別並建立實例。註冊表的值是
``_ProviderEntry``：模組名、類別名，加上兩個**靜態**能力旗標（``is_cli`` /
``supports_effort``）。旗標靜態宣告是為了讓「這個 provider 是不是 CLI」「支不支援
effort」這類查詢不必 import 任何 provider 模組——診斷端點與錯誤訊息都要在
環境壞掉（依賴缺失）時仍能運作。旗標與類別本身的宣告在**載入時核對**，不一致
即拋錯，所以不會漂移。

模組在被選中時才 ``importlib`` 載入——第三方依賴（jsonschema / anthropic /
google-genai）不成為其他 provider 的啟動前提。未註冊的 provider 值（google /
openai / 各中轉站名 / 空）一律回傳預設的 :class:`~app.infrastructure.llm.client.LLMClient`。

所有客戶端一律收 ``model`` / ``effort`` 兩個覆寫參數；不支援 effort 的 provider
由 :class:`~app.infrastructure.llm.base.BaseLLMClient` 依 ``SUPPORTS_EFFORT`` 拒絕。

新增 provider 的作法：在 ``_REGISTRY`` 加一列即可；旗標填錯會在第一次解析時被抓到。

English summary:
    Registry-based LLM client factory routing on ``LLM_PROVIDER``. Entries
    carry static ``is_cli`` / ``supports_effort`` flags so capability
    queries never import provider modules; the flags are verified against
    the class on first load so they cannot drift. Unknown providers fall
    back to the OpenAI-compatible ``LLMClient``.
"""

import importlib
import logging
from dataclasses import dataclass

from app.core.config import settings
from app.infrastructure.llm.base import BaseLLMClient
from app.infrastructure.llm.client import LLMClient
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
    """註冊表的一列。One registry row.

    Attributes:
        module: ``app.infrastructure.llm`` 下的模組名。Module name.
        class_name: 該模組內的客戶端類別名。Client class name.
        is_cli: 是否為 headless CLI 型（subprocess 驅動）。Whether the
            provider is a subprocess-driven CLI.
        supports_effort: 是否接受 ``effort`` 覆寫。Whether effort is accepted.
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
    """載入時核對註冊列的靜態宣告與類別本身一致，防止兩處漂移。

    Verify the registry row's static declarations against the loaded class.

    測試常以 ``MagicMock`` 替換類別；非真實類別時跳過核對。
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
    """依 provider 值取得客戶端類別（不實例化）。

    Resolve the client class for a provider without instantiating it.

    Args:
        provider: provider 值；None 讀 ``settings.LLM_PROVIDER``。Provider
            value, or None to read settings.

    Returns:
        對應的客戶端類別；未註冊時為 ``LLMClient``。The client class, or
        ``LLMClient`` for unregistered providers.

    Raises:
        RuntimeError: 註冊列的靜態旗標與類別宣告不一致（開發期錯誤）。
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
    """該 provider 是否為 headless CLI 型；純查表，不 import 任何 provider 模組。

    Whether the provider is a subprocess-driven CLI. A pure lookup: never
    imports provider modules, so it works even when their dependencies are
    missing (the diagnostics endpoint relies on this).
    """
    entry = _REGISTRY.get(normalize_provider(provider))
    return bool(entry and entry.is_cli)


def providers_supporting_effort() -> list[str]:
    """支援 ``effort`` 覆寫的 provider 值清單（純查表，供錯誤訊息用）。

    Providers registered as supporting effort; a pure lookup for messages.
    """
    return [key for key, entry in _REGISTRY.items() if entry.supports_effort]


def create_llm_client(
    *, model: str | None = None, effort: str | None = None,
) -> BaseLLMClient:
    """依設定建立對應的 LLM 客戶端（可覆寫模型 / 思考深度）。

    Create the LLM client matching the configured provider, optionally
    overriding the model and effort for this instance only.

    覆寫只作用在回傳的這個實例（請求範圍），不改 settings、不動
    ``app.state.llm_client``。

    Args:
        model: 覆寫模型名；None 沿用 ``LLM_MODEL_NAME``。Model override.
        effort: 覆寫思考深度；None 沿用該 provider 的設定。不支援的
            provider 給值即拋錯。Effort override.

    Returns:
        依 ``LLM_PROVIDER`` 回傳對應的客戶端。The client for the provider.

    Raises:
        LLMServiceError: 所選客戶端初始化失敗時（如 API 金鑰未設、effort 值
            非法或不受支援、找不到 CLI 執行檔），或選到尚未實作的 provider。
    """
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
