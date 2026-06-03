"""
卡片生成 API 請求/回應 Pydantic V2 Schema 定義模組。

本模組定義了卡片生成端點 (POST /api/v1/cards/generate) 所需的
請求與回應資料結構，以及專案統一的錯誤回應格式。

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 typing.Any。

設計決策：
    - ErrorResponse 遵循 03_Acceptance_Criteria.md §2 的統一格式要求，
      包含 error_code, message, details 三個欄位。
    - CardGenerateRequest 要求前端完整傳入所有參數（model_file_name,
      model_name, deck_name 等），不提供預設值，確保呼叫端明確知道
      自己在操作哪個模型與牌組。
"""

from pydantic import BaseModel, Field


# ============================================================================
# 卡片生成 API 請求 Schema
# ============================================================================


class CardGenerateRequest(BaseModel):
    """卡片生成 API 的請求結構。

    前端或 Telegram Bot 必須完整提供所有欄位，
    系統不提供預設的模型或牌組配置。

    Attributes:
        user_input: 使用者輸入的原始文字（例如一個英文單字或句子）。
        deck_name: 目標 Anki 牌組名稱，支援 '::' 分隔的巢狀牌組。
        model_file_name: 模型定義 JSON 檔名（如 'TOEIC_Coach_Dark.json'）。
        model_name: Anki 筆記類型名稱（如 'TOEIC_Coach_Dark'）。
        system_prompt: 可選的自訂 System Prompt。若未提供，
                       系統將根據 model_name 從 Jinja2 模板自動載入。
        primary_field_name: 主要欄位名稱，用於防重複檢查。
        tags: 附加至卡片的標籤列表。
        extra_fields: 使用者手動提供的固定欄位（如音檔佔位符），
                      不經過 LLM 生成，直接合併進最終 Note。
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
# 卡片生成 API 回應 Schema
# ============================================================================


class CardGenerateResponse(BaseModel):
    """卡片生成成功的回應結構。

    Attributes:
        note_id: 成功建立後 Anki 回傳的筆記 ID。
        deck_name: 卡片所在的牌組名稱。
        model_name: 使用的 Anki 筆記類型名稱。
        message: 人類可讀的成功訊息。
    """

    note_id: int = Field(
        ...,
        description="成功建立後 Anki 回傳的筆記 ID",
    )
    deck_name: str = Field(
        ...,
        description="卡片所在的牌組名稱",
    )
    model_name: str = Field(
        ...,
        description="使用的 Anki 筆記類型名稱",
    )
    message: str = Field(
        default="卡片生成成功",
        description="人類可讀的成功訊息",
    )


# ============================================================================
# 卡片更新 API 請求 Schema
# ============================================================================


class CardUpdateRequest(BaseModel):
    """卡片更新 API 的請求結構。

    Attributes:
        fields: 要更新的卡片欄位字典。
    """

    fields: dict[str, str] = Field(
        ...,
        description="要更新的卡片欄位字典（例如 {'Expression': 'apple', 'Meaning': '蘋果'}）",
    )


# ============================================================================
# 統一錯誤回應 Schema
# ============================================================================


class ErrorResponse(BaseModel):
    """統一錯誤回應格式。

    符合 03_Acceptance_Criteria.md §2 的要求：
    回傳統一格式的 JSON，包含 error_code, message, details。
    不允許將原始 traceback 直接暴露給前端。

    Attributes:
        error_code: 機器可讀的錯誤代碼（如 'DUPLICATE_CARD'）。
        message: 使用者可讀的錯誤訊息。
        details: 除錯用的額外細節（僅在開發環境顯示完整資訊）。
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
