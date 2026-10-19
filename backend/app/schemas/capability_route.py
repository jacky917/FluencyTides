"""能力路由設定 API 的請求與回應模型（能力路由計畫 capability_routing_FEAT_2026-10-19.md）。

Request / response models for the capability route settings API.

回應以「用途鍵 × 能力」組織：每個用途鍵列出它各能力的狀態，可選項目也依能力分開。
能力：``llm``（文字 LLM）、``audio_eval``（錄音評分策略）、``audio_llm``（聽音檔的模型）、
``stt``（語音轉文字）。
Responses are organised by route key and capability.
"""

from pydantic import BaseModel, Field

from app.infrastructure.llm.endpoints import EFFORT_MAX_LENGTH, ENDPOINT_ID_MAX_LENGTH, MODEL_NAME_MAX_LENGTH
from app.infrastructure.llm.routing import RouteSource


class RouteUpdate(BaseModel):
    """寫入一個用途鍵在一種能力上的選擇。The choice to store for a key and capability.

    長度上限與 ``capability_routes`` 的欄位、端點設定一致。provider 與 effort 不分大小寫、
    前後空白會去掉（正規化在服務層，TG 與 API 共用）。

    Length limits match the columns; normalization happens in the service
    layer, shared with the Telegram menu.

    Attributes:
        provider: 由誰提供：``llm`` / ``audio_llm`` 為端點 ID、``stt`` 為 ``whisper``、``audio_eval``
            為策略名（``listen`` / ``stt_diff`` / ``stt_llm``）。Who provides it.
        model: 模型名（須在該 provider 的白名單內，區分大小寫）；``audio_eval`` 省略或 null。
            Model name (whitelisted); omitted for ``audio_eval``.
        effort: 思考深度；模型不使用 effort 時省略、null 或空字串。Effort, or null.
    """

    provider: str = Field(..., min_length=1, max_length=ENDPOINT_ID_MAX_LENGTH)
    model: str | None = Field(None, max_length=MODEL_NAME_MAX_LENGTH)
    effort: str | None = Field(None, max_length=EFFORT_MAX_LENGTH)


class ChoiceRead(BaseModel):
    """一組選擇。A choice."""

    provider: str
    model: str | None
    effort: str | None


class RejectedLayerRead(BaseModel):
    """被跳過的一層。A skipped layer."""

    route_key: str
    reason: str


class CapabilityStateRead(BaseModel):
    """一個用途鍵在一種能力上的狀態。The state of one key for one capability.

    Attributes:
        source: 生效的層。The winning layer.
        effective: 實際生效的 provider、模型、effort（``.env`` 那一層填實際的預設 effort；
            null＝此模型不使用 effort）。The effective setting.
        label: 生效設定的顯示標籤（``llm`` 為寫進卡片的模型標籤）。The display label.
        stored: DB 中這個鍵在這個能力上存的選擇。The stored row, if any.
        rejected: 被跳過的層與原因。Skipped layers.
        db_unavailable: DB 讀取失敗、暫用 ``.env``。Whether the DB was unreadable.
        problem: 生效設定目前無法使用的原因（provider 需要的套件未安裝或版本不符、``.env`` 的
            設定有誤）；有值時 ``label`` 與 ``effective.effort`` 可能不準確。null 只表示沒有
            偵測到這些問題，不代表已驗證可用。Why the effective setting cannot be
            used right now, or null (which does not mean it was verified).
        declared: 這個鍵是否宣告了這個能力；false＝孤兒設定（只能刪除）。Whether the key
            declares this capability; false rows can only be deleted.
    """

    source: RouteSource
    effective: ChoiceRead
    label: str
    stored: ChoiceRead | None
    rejected: list[RejectedLayerRead]
    db_unavailable: bool
    problem: str | None
    declared: bool = True


class RouteRead(BaseModel):
    """一個用途鍵與它各能力的狀態。One route key and its capabilities.

    Attributes:
        route_key: 用途鍵；``*`` 為全域。The route key.
        description: 說明；孤兒鍵為 null。Description.
        declared: 是否為程式中宣告的鍵（False＝DB 裡的孤兒列，只能刪除）。False for
            orphan rows.
        capabilities: 能力 → 狀態。宣告的鍵列出它宣告的能力（加上 DB 中存了值、但沒有宣告的
            能力）；全域列出所有能力；孤兒鍵列出 DB 中存了值的能力。Capability to state.
    """

    route_key: str
    description: str | None
    declared: bool
    capabilities: dict[str, CapabilityStateRead]


class ModelOptionRead(BaseModel):
    """白名單中的一個模型；``efforts`` 為空串列＝此模型不使用 effort。

    A whitelisted model; an empty ``efforts`` list means it takes no effort.
    """

    name: str
    efforts: list[str]


class ProviderOptionRead(BaseModel):
    """可選的 provider 與其白名單（不含金鑰）。A selectable provider (no secrets).

    Attributes:
        id: provider ID（端點 ID、``whisper`` 或策略名）。Provider ID.
        type: 類型（端點類型，例如 ``claude-code``；``whisper``；策略為 ``strategy``）。Its type.
        models: 可選的模型；空串列＝這個 provider 不選模型（策略）。Selectable models.
        title: 說明（策略才有）。Description, for strategies.
    """

    id: str
    type: str
    models: list[ModelOptionRead]
    title: str | None = None


class CapabilityRoutesResponse(BaseModel):
    """全部用途鍵的狀態與可選項目。All route states plus the selectable options.

    Attributes:
        routes: 各用途鍵的狀態（全域、已宣告、孤兒鍵）。The routes.
        providers: 能力 → 可選的 provider 與白名單（``audio_eval`` 為全部策略；用途鍵的限縮見
            ``strategies``）。Capability to its options.
        strategies: 有限縮評分策略的用途鍵 → 允許的策略（沒列出的鍵可用全部策略）。
            Route keys that restrict the strategies, with the allowed ones.
        db_unavailable: 讀不到資料庫：各列為 ``.env`` 預設，孤兒鍵不在清單中。
            Whether the database could not be read.
    """

    routes: list[RouteRead]
    providers: dict[str, list[ProviderOptionRead]]
    strategies: dict[str, list[str]] = Field(default_factory=dict)
    db_unavailable: bool


class RouteDeleteResponse(BaseModel):
    """恢復預設的結果。The result of restoring the default.

    Attributes:
        deleted: 是否真的刪了一列（原本沒有設定時為 false）。Whether a row
            was removed.
        route: 刪除後這個用途鍵的狀態；孤兒鍵刪光後不再存在、或組不出狀態時為 null（以
            ``route_unavailable`` 區分）。The state after, or null.
        route_unavailable: 刪除已提交，但組不出刪除後的狀態（詳見後端 log；請重新 GET
            查看）。True when the delete committed but the state could not be built.
    """

    deleted: bool
    route: RouteRead | None
    route_unavailable: bool
