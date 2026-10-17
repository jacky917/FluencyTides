"""LLM 用途設定 API 的請求與回應模型（模型路由計畫 D6）。

Request / response models for the LLM route settings API.
"""

from pydantic import BaseModel, Field

from app.infrastructure.llm.endpoints import EFFORT_MAX_LENGTH, ENDPOINT_ID_MAX_LENGTH, MODEL_NAME_MAX_LENGTH
from app.infrastructure.llm.routing import RouteSource


class LLMRouteUpdate(BaseModel):
    """寫入一個用途鍵的選擇。The choice to store for a route key.

    長度上限與 ``llm_routes`` 的欄位一致。端點與 effort 不分大小寫、前後空白會去掉
    （正規化在服務層，TG 與 API 共用）。

    Length limits match the ``llm_routes`` columns; normalization happens in
    the service layer, shared with the Telegram menu.

    Attributes:
        endpoint: 端點 ID。Endpoint ID.
        model: 模型名（須在該端點的白名單內，區分大小寫）。Model name (whitelisted).
        effort: 思考深度；模型不使用 effort 時省略、null 或空字串。Effort, or null.
    """

    endpoint: str = Field(..., min_length=1, max_length=ENDPOINT_ID_MAX_LENGTH)
    model: str = Field(..., min_length=1, max_length=MODEL_NAME_MAX_LENGTH)
    effort: str | None = Field(None, max_length=EFFORT_MAX_LENGTH)


class RouteChoiceRead(BaseModel):
    """一組選擇。A choice."""

    endpoint: str
    model: str
    effort: str | None


class RejectedLayerRead(BaseModel):
    """被跳過的一層。A skipped layer."""

    route_key: str
    reason: str


class LLMRouteRead(BaseModel):
    """一個用途鍵的狀態。The state of one route key.

    Attributes:
        route_key: 用途鍵；``*`` 為全域。The route key.
        description: 說明；孤兒鍵為 null。Description.
        declared: 是否為程式中宣告的鍵（False＝DB 裡的孤兒列，可刪除）。
            False for orphan rows.
        source: 生效的層。The winning layer.
        effective: 實際生效的端點、模型、effort（``.env`` 那一層填實際的預設 effort；
            null＝此模型不使用 effort）。The effective setting.
        label: 生效設定對應的卡片標籤。The resulting label.
        stored: DB 中這個鍵本身存的選擇。The stored row, if any.
        rejected: 被跳過的層與原因。Skipped layers.
        db_unavailable: DB 讀取失敗、暫用 ``.env``。Whether the DB was unreadable.
        problem: 生效設定目前無法使用的原因（端點需要的套件未安裝或版本不符、``.env`` 的
            端點設定有誤）；有值時 ``label`` 與 ``effective.effort`` 可能不準確。null 只表示
            沒有偵測到這兩類問題，不代表已驗證可用。Why the effective setting cannot
            be used right now, or null (which does not mean it was verified).
    """

    route_key: str
    description: str | None
    declared: bool
    source: RouteSource
    effective: RouteChoiceRead
    label: str
    stored: RouteChoiceRead | None
    rejected: list[RejectedLayerRead]
    db_unavailable: bool
    problem: str | None


class ModelOptionRead(BaseModel):
    """白名單中的一個模型；``efforts`` 為空串列＝此模型不使用 effort。

    A whitelisted model; an empty ``efforts`` list means it takes no effort.
    """

    name: str
    efforts: list[str]


class EndpointOptionRead(BaseModel):
    """可選的端點與其白名單（不含金鑰）。A selectable endpoint (no secrets)."""

    id: str
    type: str
    models: list[ModelOptionRead]


class LLMRoutesResponse(BaseModel):
    """全部用途鍵的狀態與可選項目。All route states plus the selectable options.

    Attributes:
        routes: 各用途的狀態（全域、已宣告、孤兒鍵）。The routes.
        endpoints: 可選的端點與白名單。The selectable endpoints.
        db_unavailable: 讀不到資料庫：各列為 ``.env`` 預設，孤兒鍵不在清單中。
            Whether the database could not be read.
    """

    routes: list[LLMRouteRead]
    endpoints: list[EndpointOptionRead]
    db_unavailable: bool


class LLMRouteDeleteResponse(BaseModel):
    """恢復預設的結果。The result of restoring the default.

    Attributes:
        deleted: 是否真的刪了一列（原本沒有設定時為 false）。Whether a row
            was removed.
        route: 刪除後的狀態；孤兒鍵刪除後不再存在、或組不出狀態時為 null（以
            ``route_unavailable`` 區分）。The state after, or null.
        route_unavailable: 刪除已提交，但組不出刪除後的狀態（詳見後端 log；請重新 GET
            查看）。True when the delete committed but the state could not be built.
    """

    deleted: bool
    route: LLMRouteRead | None
    route_unavailable: bool
