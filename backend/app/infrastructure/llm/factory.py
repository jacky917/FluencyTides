"""
LLM 客戶端工廠模組。

依 ``settings.LLM_PROVIDER`` 決定要建立哪一種 LLM 客戶端：

- ``claude-code``：:class:`ClaudeCodeLLMClient`，驅動本機已登入的
  Claude Code CLI (headless)，使用訂閱額度。
- ``anthropic``：:class:`AnthropicLLMClient`，Anthropic 官方 API。
  **目前為佔位 STUB，尚未實作**，實例化即拋 ``LLMServiceError``。
- 其他（google / openai / 各中轉站）：:class:`LLMClient`，走 OpenAI 相容
  計費 API。此為預設路徑，行為與本工廠導入前完全一致。

三者介面相同（``generate_structured_data`` → ``LLMGenerateResult``，失敗拋
``LLMServiceError``），故所有呼叫端只需將 ``LLMClient()`` 換成
``create_llm_client()``，無須任何其他改動。

新增 provider 的作法：在本檔加入一個常數與一個惰性 import 的分支即可——
**分支內的 import 必須維持惰性**，否則該 provider 的第三方依賴會變成所有
其他 provider 的啟動前提（見下方註解）。

English summary:
    LLM client factory routing on ``LLM_PROVIDER``: the headless Claude Code
    CLI (subscription quota), the first-party Anthropic API (stub, not yet
    implemented), or the default OpenAI-compatible ``LLMClient``. All three
    share one interface, so call sites only swap the constructor. Provider
    modules are imported lazily so their dependencies never become a startup
    requirement for the other providers.
"""

import logging
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.exceptions.infrastructure import LLMServiceError
from app.infrastructure.llm.client import LLMClient

if TYPE_CHECKING:  # 僅供型別檢查，執行期不載入（見下方惰性 import 說明）
    from app.infrastructure.llm.anthropic_client import AnthropicLLMClient
    from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient

logger = logging.getLogger(__name__)

# 觸發 headless CLI provider 的 LLM_PROVIDER 值
CLAUDE_CODE_PROVIDER = "claude-code"

# 觸發 Anthropic 官方 API provider 的 LLM_PROVIDER 值（目前為 STUB）
ANTHROPIC_PROVIDER = "anthropic"


def create_llm_client(
    *, model: str | None = None, effort: str | None = None,
) -> "LLMClient | ClaudeCodeLLMClient | AnthropicLLMClient":
    """依設定建立對應的 LLM 客戶端（可覆寫模型 / 思考深度）。

    Create the LLM client matching the configured provider, optionally
    overriding the model and effort for this instance only.

    覆寫只作用在回傳的這個實例（請求範圍），不改 settings、不動
    ``app.state.llm_client``。供讀音判讀端點等「每次可指定模型」的用途
    （docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.2）。
    Overrides are instance-scoped: settings and the app-wide client are
    untouched.

    Args:
        model: 覆寫模型名；None 沿用 ``LLM_MODEL_NAME``。Model override.
        effort: 覆寫思考深度；None 沿用 ``LLM_CLAUDE_CODE_EFFORT``。僅
            claude-code 支援，其他 provider 給值即拋錯。Effort override
            (claude-code only).

    Returns:
        依 ``LLM_PROVIDER`` 回傳對應的客戶端；未匹配任何專屬 provider 時
        回傳預設的 ``LLMClient``。The client matching ``LLM_PROVIDER``,
        falling back to the default ``LLMClient``.

    Raises:
        LLMServiceError: 所選客戶端初始化失敗時（如 API 金鑰未設、effort 值
            非法、找不到 claude 執行檔），或選到尚未實作的 ``anthropic``
            provider。Raised when the selected client fails to initialize, or
            when the not-yet-implemented ``anthropic`` provider is selected.
    """
    provider = (settings.LLM_PROVIDER or "").strip().lower()

    # 各 provider 模組一律採惰性 import：預設路徑不載入它們，也就不需要它們的
    # 第三方依賴（claude-code 需要 jsonschema、anthropic 將需要 anthropic 套件）
    # 存在，確保新 provider 對既有部署零影響。
    # Provider modules are imported lazily: the default path never loads them,
    # so their third-party dependencies never become a startup requirement.
    if provider == CLAUDE_CODE_PROVIDER:
        from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient

        logger.info("LLM Provider = %s，使用本機 Claude Code CLI。", provider)
        return ClaudeCodeLLMClient(model=model, effort=effort)

    if provider == ANTHROPIC_PROVIDER:
        from app.infrastructure.llm.anthropic_client import AnthropicLLMClient

        logger.info("LLM Provider = %s，使用 Anthropic 官方 API。", provider)
        return AnthropicLLMClient()

    if effort is not None:
        raise LLMServiceError(
            f"effort 覆寫僅 claude-code provider 支援（目前 LLM_PROVIDER='{provider or 'google'}'）。"
        )
    return LLMClient(model=model)
