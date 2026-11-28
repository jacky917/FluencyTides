"""Pydantic V2 schemas for card generation input and API errors.

卡片生成輸入與 API 錯誤回應的 Pydantic V2 Schema 定義模組。

Defines the card generation input read by the local batch script
``scripts/local_anki/import_cards_with_llm.py``, and the unified error
format returned for ``FluencyTidesError`` (see ``main.py``) and declared
in most routers' ``responses``. That script currently calls the removed
``CardService.generate_card`` and cannot run (``docs/wip/backlog.md``
H1), so ``CardGenerateRequest`` has no working caller.

本模組定義本機批次腳本 ``scripts/local_anki/import_cards_with_llm.py``
讀取的卡片生成輸入，以及 ``FluencyTidesError`` 回傳（見 ``main.py``）、
多數路由在 ``responses`` 宣告的統一錯誤格式。該腳本目前呼叫已移除的
``CardService.generate_card``，無法執行（``docs/wip/backlog.md`` H1），
所以 ``CardGenerateRequest`` 沒有能用的呼叫端。

All cross-boundary data structures are strongly validated with Pydantic V2;
raw dicts and ``typing.Any`` are strictly forbidden.

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 ``typing.Any``。

Design decisions:
    - ``ErrorResponse`` follows the unified format required by
      03_Acceptance_Criteria.md §2, with the ``error_code``, ``message``, and
      ``details`` fields.
    - The model and deck fields of ``CardGenerateRequest``
      (``model_file_name``, ``model_name``, ``deck_name``, and so on) have
      no defaults, so the caller always knows which model and deck it is
      operating on.
    - Web card creation does not use ``CardGenerateRequest``; it goes
      through the handler API (``POST /api/v1/handlers/{handler_name}/notes``).

設計決策：
    - ``ErrorResponse`` 遵循 03_Acceptance_Criteria.md §2 的統一格式要求，
      包含 ``error_code``, ``message``, ``details`` 三個欄位。
    - ``CardGenerateRequest`` 的模型與牌組欄位（``model_file_name``,
      ``model_name``, ``deck_name`` 等）不提供預設值，確保呼叫端明確知道
      自己在操作哪個模型與牌組。
    - 網頁建卡不使用 ``CardGenerateRequest``，走 handler API
      （``POST /api/v1/handlers/{handler_name}/notes``）。
"""

from pydantic import BaseModel, Field


# ============================================================================
# 卡片生成的輸入 Schema
# ============================================================================


class CardGenerateRequest(BaseModel):
    """The input of one card generation.

    一次卡片生成的輸入。

    Callers must supply every required field; the system offers no
    default model or deck configuration. ``deck_name`` supports
    ``'::'``-separated nested decks.

    呼叫端必須完整提供所有必填欄位，系統不提供預設的模型或牌組配置。
    ``deck_name`` 支援 ``'::'`` 分隔的巢狀牌組。
    """

    user_input: str = Field(
        ...,
        min_length=1,
        description="使用者輸入的原始文字（例如一個英文單字或句子）",
    )
    deck_name: str = Field(
        ...,
        min_length=1,
        description="目標 Anki 牌組名稱",
    )
    model_file_name: str = Field(
        ...,
        min_length=1,
        description="模型定義 JSON 檔名（如 'TOEIC_Coach_Dark.json'）",
    )
    model_name: str = Field(
        ...,
        min_length=1,
        description="Anki 筆記類型名稱（如 'TOEIC_Coach_Dark'）",
    )
    system_prompt: str | None = Field(
        default=None,
        description=(
            "可選的自訂 System Prompt。若為 None，"
            "系統將根據 model_name 從 Jinja2 模板自動載入預設 Prompt。"
        ),
    )
    primary_field_name: str = Field(
        default="Expression",
        description="主要欄位名稱，用於防重複檢查",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="附加至卡片的標籤列表",
    )
    extra_fields: dict[str, str] | None = Field(
        default=None,
        description=(
            "使用者手動提供的固定欄位（如音檔佔位符），"
            "不經過 LLM 生成，直接合併進最終 Note。"
        ),
    )


# ============================================================================
# 統一錯誤回應 Schema
# ============================================================================


class ErrorResponse(BaseModel):
    """The unified error response format.

    統一錯誤回應格式。

    Meets 03_Acceptance_Criteria.md §2: a unified JSON response with
    ``error_code``, ``message``, and ``details``. Raw tracebacks must never be
    exposed directly to the frontend; ``details`` shows full information only
    in development.

    符合 03_Acceptance_Criteria.md §2 的要求：
    回傳統一格式的 JSON，包含 ``error_code``, ``message``, ``details``。
    不允許將原始 traceback 直接暴露給前端；``details`` 僅在開發環境顯示完整資訊。
    """

    error_code: str = Field(
        ...,
        description="機器可讀的錯誤代碼（如 'DUPLICATE_CARD'）",
    )
    message: str = Field(
        ...,
        description="使用者可讀的錯誤訊息",
    )
    details: str | None = Field(
        default=None,
        description="除錯用的額外細節",
    )
