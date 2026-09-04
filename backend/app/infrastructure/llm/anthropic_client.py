"""
Anthropic 官方 API 結構化輸出客戶端模組（**佔位 STUB，尚未實作**）。

本模組為未來「直接呼叫 Anthropic 官方 API」預留的擴充點，目前僅保留命名與
路由，任何實例化都會拋出 ``LLMServiceError``。實作前請先閱讀
``docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md`` §D11。

三個 provider 的定位區隔：

===================  ===================================  ==========================
Provider             管道                                  適用情境
===================  ===================================  ==========================
預設（google 等）      OpenAI 相容 API（``LLMClient``）        現行計費管線
``claude-code``      本機 headless CLI                     訂閱額度、手動批次
``anthropic``        官方 Anthropic API（**本模組**）        需要計費 API 的穩定性、
                                                          併發、Batch API 半價
===================  ===================================  ==========================

英文摘要 / English summary:
    Placeholder for a future first-party Anthropic API provider. Reserves the
    naming and factory routing; every instantiation raises ``LLMServiceError``
    until implemented. Sits alongside the OpenAI-compatible ``LLMClient`` and
    the headless-CLI ``ClaudeCodeLLMClient``.

--------------------------------------------------------------------------
實作指引（Implementation checklist）
--------------------------------------------------------------------------

**1. 依賴與客戶端**

``requirements.txt`` 加入 ``anthropic``；本模組維持惰性 import（工廠已對
provider 模組採惰性載入），避免未安裝時影響其他 provider 啟動::

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.LLM_ANTHROPIC_API_KEY)

注意本檔名為 ``anthropic_client.py`` 而非 ``anthropic.py``，以免遮蔽套件。

**2. 結構化輸出（本 provider 的核心）**

官方 API 以 ``output_config.format`` 約束輸出（非已棄用的 ``output_format``
參數）。因本專案的 ``generate_structured_data`` 收到的是 JSON Schema dict
（而非 Pydantic class），走 raw schema 形式::

    response = await client.messages.create(
        model=settings.LLM_MODEL_NAME,          # 預設建議 "claude-opus-5"
        max_tokens=16000,                        # 非串流建議值；超長輸出改用 .stream()
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={
            "format": {"type": "json_schema", "schema": resolved_schema},
            "effort": settings.LLM_ANTHROPIC_EFFORT,   # low/medium/high/xhigh/max
        },
    )
    text = next(b.text for b in response.content if b.type == "text")
    parsed_data = json.loads(text)

**兩個 schema 前處理的坑**（與 claude-code provider 相同的教訓）：

- Pydantic 的 ``model_json_schema()`` 會產生 ``$defs``/``$ref``，先以
  ``LLMClient._resolve_json_schema()``（classmethod，免實例化）展平。
- Pydantic **不會**自動加上 ``additionalProperties: false``，而 json_schema
  格式要求它與 ``required`` 皆齊備；需在送出前補齊。

**3. 必須處理 refusal**

Opus 5 / Fable 5 等模型的安全分類器可能拒答：HTTP 200、
``stop_reason == "refusal"``、``stop_details.category`` 說明類別。**讀
``content`` 前必須先檢查 ``stop_reason``**，否則會拿到空內容而誤判。
建議把 refusal 映射為 ``LLMServiceError``，訊息帶上生成腳本錯誤分級表
可識別的字串（見下方第 5 點），讓管線走「記錄失敗並跳過此句」的既有路徑
——本專案的 galgame 台詞正是容易觸發拒答的內容類型。

亦可考慮啟用伺服器端 fallback（beta ``server-side-fallback-2026-07-01``
搭配 ``fallbacks="default"``），由伺服器依拒答類別自動改派模型。

**4. 思考與力度**

Opus 5 預設即開啟 adaptive thinking，通常**省略 ``thinking`` 參數**即可；
深度以 ``output_config.effort`` 控制。切勿使用已移除的
``thinking={"type": "enabled", "budget_tokens": N}``（會回 400）。

**5. 介面契約（與另外兩個 provider 一致，不可偏離）**

- 簽名：``generate_structured_data(system_prompt, user_prompt, response_schema)
  -> LLMGenerateResult``
- 失敗一律拋 ``LLMServiceError``；重試耗盡的訊息需包含
  ``"LLM API 在所有重試後仍回傳空內容"``，速率限制的訊息需包含 ``"Quota"``
  ——生成腳本的錯誤分級表以這兩個字串決定「跳句」或「暫停 60 秒」
- ``LLMGenerateResult.model_name`` 回填 ``(anthropic){model}``；若加入
  effort 設定則比照 claude-code 追加 ``@{effort}``，並同步更新
  ``generate_child_cards.py`` 的標籤行（見計劃文件 D7）
- 錯誤處理採「由具體到廣泛」的例外鏈（``NotFoundError`` → ``RateLimitError``
  → ``APIStatusError`` → ``APIConnectionError``），不要只 catch 一個大類

**6. 需要新增的設定（``app/core/config.py``）**

刻意尚未加入，避免產生無人讀取的死設定。實作時一併新增::

    LLM_ANTHROPIC_API_KEY: str = ""           # 官方 API 金鑰（與 LLM_API_KEY 分離）
    LLM_ANTHROPIC_EFFORT: str = "high"        # low/medium/high/xhigh/max
    LLM_ANTHROPIC_MAX_TOKENS: int = 16000     # 非串流上限

注意 ``LLM_ANTHROPIC_EFFORT`` 與 ``LLM_CLAUDE_CODE_EFFORT`` 是**兩個不同
provider 的獨立設定**——這正是設定前綴採 provider 全名（而非籠統的
``LLM_CLAUDE_*``）的原因。

**7. 額外可考慮**

- Batch API（``client.messages.batches``）半價，適合本專案數百張卡的量產
- Prompt caching：每張卡的 prompt 約 18KB，其中世界觀與規則段落完全固定，
  在該段落尾端下 ``cache_control`` 斷點可大幅降低成本
"""

import logging

from app.core.exceptions import LLMServiceError
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)

# 觸發本 provider 的 LLM_PROVIDER 值（與工廠共用）
ANTHROPIC_PROVIDER = "anthropic"

# 未實作時的統一錯誤訊息
_NOT_IMPLEMENTED_MESSAGE = (
    "LLM_PROVIDER='anthropic'（Anthropic 官方 API）尚未實作，目前僅為佔位。"
    "請改用 'claude-code'（本機訂閱 CLI）或既有的 OpenAI 相容 provider；"
    "若要實作，請見 app/infrastructure/llm/anthropic_client.py 的模組 docstring "
    "與 docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md §D11。"
)


class AnthropicLLMClient:
    """Anthropic 官方 API 客戶端（佔位，尚未實作）。

    Placeholder for the first-party Anthropic API client; not implemented.

    介面契約與 :class:`~app.infrastructure.llm.client.LLMClient` 及
    :class:`~app.infrastructure.llm.claude_code_client.ClaudeCodeLLMClient`
    完全一致，實作後上下游無須任何改動。實作步驟見模組 docstring。
    """

    def __init__(self) -> None:
        """尚未實作，一律拋錯。

        Not implemented; always raises.

        刻意在建構子（而非呼叫時）拋錯：讓誤設 ``LLM_PROVIDER=anthropic``
        的情況在應用啟動或腳本開跑的當下就暴露，而不是在批次跑到一半、
        已建立部分卡片之後才失敗。

        Deliberately raises at construction so a misconfigured provider
        surfaces at startup rather than midway through a batch.

        Raises:
            LLMServiceError: 一律拋出。Always.
        """
        raise LLMServiceError(_NOT_IMPLEMENTED_MESSAGE)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """尚未實作，一律拋錯。

        Not implemented; always raises.

        建構子已擋下一般路徑，此處為繞過建構子（如 ``__new__``、子類化、
        測試替身）時的第二道防線，確保不會靜默回傳空結果。

        Second line of defence for paths that bypass ``__init__``.

        Args:
            system_prompt: 系統提示。The system prompt.
            user_prompt: 使用者輸入。The user input.
            response_schema: JSON Schema 定義字典。JSON Schema dict.

        Raises:
            LLMServiceError: 一律拋出。Always.
        """
        raise LLMServiceError(_NOT_IMPLEMENTED_MESSAGE)
