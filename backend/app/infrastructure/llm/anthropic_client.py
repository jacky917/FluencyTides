"""Structured-output client module for the first-party Anthropic API (**placeholder STUB, not implemented**).

Anthropic 官方 API 結構化輸出客戶端模組（**佔位 STUB，尚未實作**）。

This module is the extension point reserved for "calling the first-party Anthropic API directly" in the future.
For now it only reserves the naming and the routing; every instantiation raises ``LLMServiceError``. Before
implementing it, read ``docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md`` §D11.

本模組為未來「直接呼叫 Anthropic 官方 API」預留的擴充點，目前僅保留命名與
路由，任何實例化都會拋出 ``LLMServiceError``。實作前請先閱讀
``docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md`` §D11。

How the three providers differ:

===================  ===================================  ==========================
Provider             Channel                              Use case
===================  ===================================  ==========================
default (google...)  OpenAI-compatible API (``LLMClient``)  current billed pipeline
``claude-code``      local headless CLI                   subscription quota, manual
                                                          batches
``anthropic``        first-party Anthropic API (**this    stability of a billed API,
                     module**)                            concurrency, half-price
                                                          Batch API
===================  ===================================  ==========================

三個 provider 的定位區隔：

===================  ===================================  ==========================
Provider             管道                                  適用情境
===================  ===================================  ==========================
預設（google 等）      OpenAI 相容 API（``LLMClient``）        現行計費管線
``claude-code``      本機 headless CLI                     訂閱額度、手動批次
``anthropic``        官方 Anthropic API（**本模組**）        需要計費 API 的穩定性、
                                                          併發、Batch API 半價
===================  ===================================  ==========================

--------------------------------------------------------------------------
實作指引（Implementation checklist）
--------------------------------------------------------------------------

**1. Dependency and client**

Add ``anthropic`` to ``requirements.txt``; keep the import in this module lazy (the factory already loads
provider modules lazily) so that, when it is not installed, other providers still start::

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.LLM_ANTHROPIC_API_KEY)

Note that this file is named ``anthropic_client.py`` rather than ``anthropic.py`` so it does not shadow the
package.

**2. Structured output (the core of this provider)**

The official API constrains output with ``output_config.format`` (not the deprecated ``output_format``
parameter). Because this project's ``generate_structured_data`` receives a JSON Schema dict (not a Pydantic
class), use the raw-schema form (see the code example in the Chinese section below).

**Two schema-preprocessing traps** (the same lessons as the claude-code provider):

- Pydantic's ``model_json_schema()`` produces ``$defs``/``$ref``; flatten them first with
  ``schema_utils.resolve_json_schema()``.
- Pydantic does **not** add ``additionalProperties: false`` automatically, while the json_schema format
  requires both it and ``required``; add them before sending.

**3. Refusals must be handled**

The safety classifier of models such as Opus 5 / Fable 5 may refuse: HTTP 200, ``stop_reason == "refusal"``,
and ``stop_details.category`` giving the category. **Check ``stop_reason`` before reading ``content``**,
otherwise you get empty content and misjudge it. Map a refusal to ``LLMServiceError``, with a message containing
a string the generation scripts' error-grading table recognizes (see point 5 below), so the pipeline takes the
existing "log the failure and skip this sentence" path; this project's galgame lines are exactly the kind of
content that tends to trigger refusals.

Enabling server-side fallback may also be considered (beta ``server-side-fallback-2026-07-01`` with
``fallbacks="default"``), letting the server reassign the model automatically according to the refusal category.

**4. Thinking and effort**

Opus 5 enables adaptive thinking by default, so usually **omit the ``thinking`` parameter**; control the depth
with ``output_config.effort``. Never use the removed ``thinking={"type": "enabled", "budget_tokens": N}`` (it
returns 400).

**5. Interface contract (same as the other two providers; must not deviate)**

- signature: ``generate_structured_data(system_prompt, user_prompt, response_schema) -> LLMGenerateResult``
- every failure raises ``LLMServiceError``; the retry-exhaustion message must contain
  ``"LLM API 在所有重試後仍回傳空內容"`` and the rate-limit message must contain ``"Quota"``; the generation
  scripts' error-grading table uses these two strings to decide between "skip the sentence" and "pause 60
  seconds"
- fill ``LLMGenerateResult.model_name`` with ``(anthropic){model}``; if an effort setting is added, append
  ``@{effort}`` as claude-code does, and update the label line of ``generate_child_cards.py`` accordingly (see
  plan document D7)
- handle errors with an exception chain "from specific to broad" (``NotFoundError`` → ``RateLimitError`` →
  ``APIStatusError`` → ``APIConnectionError``); do not catch only one broad class

**6. Settings to add (``app/core/config.py``)**

Deliberately not added yet, to avoid dead settings that nobody reads. Add them during implementation (see the
code example in the Chinese section below).

Note that ``LLM_ANTHROPIC_EFFORT`` and ``LLM_CLAUDE_CODE_EFFORT`` are **independent settings of two different
providers**; this is exactly why setting prefixes use the full provider name (not a vague ``LLM_CLAUDE_*``).

**7. Further options**

- the Batch API (``client.messages.batches``) is half price, suitable for this project's production of hundreds
  of cards
- prompt caching: each card's prompt is about 18KB, of which the world-setting and rules sections are completely
  fixed; placing a ``cache_control`` breakpoint at the end of those sections can cut costs substantially

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
  ``schema_utils.resolve_json_schema()`` 展平。
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
from typing import ClassVar

from app.core.exceptions import LLMServiceError
from app.infrastructure.llm.base import OMIT_EFFORT, BaseLLMClient, EffortArg
from app.infrastructure.llm.endpoints import TYPE_ANTHROPIC, Endpoint
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)

# 未實作時的統一錯誤訊息
_NOT_IMPLEMENTED_MESSAGE = (
    "LLM_PROVIDER='anthropic'（Anthropic 官方 API）尚未實作，目前僅為佔位。"
    "請改用 'claude-code'（本機訂閱 CLI）或既有的 OpenAI 相容 provider；"
    "若要實作，請見 app/infrastructure/llm/anthropic_client.py 的模組 docstring "
    "與 docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md §D11。"
)


class AnthropicLLMClient(BaseLLMClient):
    """Client for the first-party Anthropic API (placeholder, not implemented).

    Anthropic 官方 API 客戶端（佔位，尚未實作）。

    Already attached to :class:`~app.infrastructure.llm.base.BaseLLMClient`, with the same interface contract as
    the other providers, so nothing upstream or downstream needs to change once it is implemented. See the module
    docstring for the implementation steps; for exceptions use the three subclasses ``LLMRetryExhaustedError`` /
    ``LLMQuotaError`` / ``LLMRefusalError`` (plan D5) rather than raising only the base class.

    已掛上 ``BaseLLMClient``，介面契約與
    其他 provider 一致，實作後上下游無須任何改動。實作步驟見模組 docstring；
    例外請用 ``LLMRetryExhaustedError`` / ``LLMQuotaError`` / ``LLMRefusalError``
    三個子類（計畫 D5），而非只拋基類。
    """

    PROVIDER_NAME: ClassVar[str] = "anthropic"  # 須與工廠註冊鍵一致，載入時核對
    ENDPOINT_TYPES: ClassVar[frozenset[str]] = frozenset({TYPE_ANTHROPIC})
    # 佔位期間為 False：不能把一個建構即拋錯的 stub 列進「支援 effort 的 provider」
    # 錯誤訊息裡誤導使用者。實作本體（output_config.effort）時改 True，並同步改工廠註冊列。
    SUPPORTS_EFFORT: ClassVar[bool] = False

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: EffortArg = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        """Raise unconditionally because this client is not implemented; the message echoes any overrides received.

        尚未實作，一律拋錯；訊息回顯收到的覆寫值。

        It deliberately raises in the constructor (not at call time), so a mistaken ``LLM_PROVIDER=anthropic``
        surfaces the moment the app starts or a script begins, rather than failing midway through a batch after
        some cards have already been created. The constructor accepts ``model`` / ``effort`` to make sure the
        overrides passed by the factory are never silently dropped (usable directly once implemented).

        刻意在建構子（而非呼叫時）拋錯：讓誤設 ``LLM_PROVIDER=anthropic``
        的情況在應用啟動或腳本開跑的當下就暴露，而不是在批次跑到一半、
        已建立部分卡片之後才失敗。建構子收 ``model`` / ``effort`` 是為了
        確保工廠傳入的覆寫不會被靜默丟棄（實作時直接可用）。

        Args:
            model: the model-name override
                覆寫模型名
            effort: the thinking-depth override
                覆寫思考深度
            endpoint: the endpoint (unused by the stub)
                端點（stub 不使用）

        Raises:
            LLMServiceError: always
                一律拋出
        """
        overrides = ", ".join(
            f"{name}={value!r}" for name, value in (("model", model), ("effort", effort))
            if value is not None
        )
        suffix = f"（收到覆寫：{overrides}）" if overrides else ""
        raise LLMServiceError(_NOT_IMPLEMENTED_MESSAGE + suffix)

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """Raise unconditionally because this client is not implemented.

        尚未實作，一律拋錯。

        The constructor already blocks the normal path; this is the second line of defence when the constructor is
        bypassed (for example ``__new__``, subclassing, test doubles), making sure no empty result is returned
        silently.

        建構子已擋下一般路徑，此處為繞過建構子（如 ``__new__``、子類化、
        測試替身）時的第二道防線，確保不會靜默回傳空結果。

        Args:
            system_prompt: the system prompt
                系統提示
            user_prompt: the user input
                使用者輸入
            response_schema: the JSON Schema definition dict
                JSON Schema 定義字典

        Raises:
            LLMServiceError: always
                一律拋出
        """
        raise LLMServiceError(_NOT_IMPLEMENTED_MESSAGE)
