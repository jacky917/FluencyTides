"""Request and response models for the capability route settings API.

能力路由設定 API 的請求與回應模型（能力路由計畫 capability_routing_FEAT_2026-10-19.md）。

See the capability routing plan, capability_routing_FEAT_2026-10-19.md.
Responses are organised by route key and capability: each route key lists the
state of each of its capabilities, and the selectable options are also split by
capability. The capabilities are ``llm`` (text LLM), ``audio_eval`` (recording
scoring strategy), ``audio_llm`` (the model that listens to audio files), and
``stt`` (speech to text).

回應以「用途鍵 × 能力」組織：每個用途鍵列出它各能力的狀態，可選項目也依能力分開。
能力：``llm``（文字 LLM）、``audio_eval``（錄音評分策略）、``audio_llm``（聽音檔的模型）、
``stt``（語音轉文字）。
"""

from pydantic import BaseModel, Field

from app.infrastructure.llm.endpoints import EFFORT_MAX_LENGTH, ENDPOINT_ID_MAX_LENGTH, MODEL_NAME_MAX_LENGTH
from app.infrastructure.llm.routing import RouteSource


class RouteUpdate(BaseModel):
    """The choice to store for one route key on one capability.

    寫入一個用途鍵在一種能力上的選擇。

    The length limits match the ``cfg_capability_routes`` columns and the endpoint
    settings. ``provider`` and ``effort`` are case-insensitive and have leading
    and trailing whitespace stripped; normalization happens in the service
    layer, shared by Telegram and the API.

    長度上限與 ``cfg_capability_routes`` 的欄位、端點設定一致。provider 與 effort 不分大小寫、
    前後空白會去掉（正規化在服務層，TG 與 API 共用）。

    Attributes:
        provider: who provides it: an endpoint ID for ``llm`` / ``audio_llm``, ``whisper`` for ``stt``,
            and a strategy name (``listen`` / ``stt_diff`` / ``stt_llm``) for ``audio_eval``
            由誰提供：``llm`` / ``audio_llm`` 為端點 ID、``stt`` 為 ``whisper``、``audio_eval``
            為策略名（``listen`` / ``stt_diff`` / ``stt_llm``）
        model: the model name (must be in that provider's whitelist, case-sensitive); omitted or null
            for ``audio_eval``
            模型名（須在該 provider 的白名單內，區分大小寫）；``audio_eval`` 省略或 null
        effort: the thinking depth; omitted, null, or an empty string when the model takes no effort
            思考深度；模型不使用 effort 時省略、null 或空字串
    """

    provider: str = Field(..., min_length=1, max_length=ENDPOINT_ID_MAX_LENGTH)
    model: str | None = Field(None, max_length=MODEL_NAME_MAX_LENGTH)
    effort: str | None = Field(None, max_length=EFFORT_MAX_LENGTH)


class ChoiceRead(BaseModel):
    """A provider, model, and effort choice.

    一組選擇。
    """

    provider: str
    model: str | None
    effort: str | None


class RejectedLayerRead(BaseModel):
    """A layer that was skipped, with the reason.

    被跳過的一層。
    """

    route_key: str
    reason: str


class CapabilityStateRead(BaseModel):
    """The state of one route key on one capability.

    一個用途鍵在一種能力上的狀態。

    Attributes:
        source: the layer that took effect
            生效的層
        effective: the provider, model, and effort actually in effect (the ``.env`` layer fills in the
            actual default effort; null means this model takes no effort)
            實際生效的 provider、模型、effort（``.env`` 那一層填實際的預設 effort；
            null＝此模型不使用 effort）
        label: the display label of the effective setting (for ``llm``, the model label written into cards)
            生效設定的顯示標籤（``llm`` 為寫進卡片的模型標籤）
        stored: the choice stored in the DB for this key on this capability
            DB 中這個鍵在這個能力上存的選擇
        rejected: the skipped layers and the reasons
            被跳過的層與原因
        db_unavailable: whether reading the DB failed and ``.env`` is used for now
            DB 讀取失敗、暫用 ``.env``
        problem: why the effective setting cannot be used right now (a package the provider needs is not
            installed or has the wrong version, or the ``.env`` setting is wrong); when set, ``label`` and
            ``effective.effort`` may be inaccurate. Null only means none of these problems was detected,
            not that the setting was verified to work.
            生效設定目前無法使用的原因（provider 需要的套件未安裝或版本不符、``.env`` 的
            設定有誤）；有值時 ``label`` 與 ``effective.effort`` 可能不準確。null 只表示沒有
            偵測到這些問題，不代表已驗證可用。
        declared: whether the key declares this capability; false means an orphan setting (can only be deleted)
            這個鍵是否宣告了這個能力；false＝孤兒設定（只能刪除）
        global_key: the global this capability falls back to: ``*`` (text) or ``*voice`` (voice)
            這個能力所屬的全域：``*``（文字）或 ``*voice``（語音）
        forced: the global's switch is on, so this key's own setting (if any) does not take effect now
            全域的開關開著，這個鍵自己的設定（如果有）目前不生效
    """

    source: RouteSource
    effective: ChoiceRead
    label: str
    stored: ChoiceRead | None
    rejected: list[RejectedLayerRead]
    db_unavailable: bool
    problem: str | None
    declared: bool = True
    global_key: str = "*"
    forced: bool = False


class RouteRead(BaseModel):
    """One route key and the state of its capabilities.

    一個用途鍵與它各能力的狀態。

    Attributes:
        route_key: the route key; ``*`` and ``*voice`` are the text and voice globals
            用途鍵；``*`` 與 ``*voice`` 為文字與語音全域
        description: the description; null for orphan keys
            說明；孤兒鍵為 null
        declared: whether the key is declared in code (``False`` means an orphan row in the DB, which can only
            be deleted)
            是否為程式中宣告的鍵（False＝DB 裡的孤兒列，只能刪除）
        capabilities: capability mapped to state. A declared key lists the capabilities it declares (plus any
            capability that has a stored value in the DB but is not declared); a global key lists that global's
            capabilities; an orphan key lists the capabilities that have stored values in the DB.
            能力 → 狀態。宣告的鍵列出它宣告的能力（加上 DB 中存了值、但沒有宣告的
            能力）；全域鍵列出該全域的能力；孤兒鍵列出 DB 中存了值的能力。
        switch: whether the global's "global switch" is on (every route on that side uses the global as a
            whole); null for a key that is not a global, and for a global while the settings cannot be read; a
            single-capability write response (``PUT``/``DELETE`` of a capability) does not carry it either
            全域的「全局生效」開關是否開著（那一邊的用途都整組用全域）；不是全域鍵時為 null，讀不到設定時全域也是
            null；只改一個能力的回應（能力的 ``PUT``／``DELETE``）也不帶它
    """

    route_key: str
    description: str | None
    declared: bool
    capabilities: dict[str, CapabilityStateRead]
    switch: bool | None = None


class GlobalSwitchUpdate(BaseModel):
    """The new state of a global's "global switch".

    全域「全局生效」開關的新狀態。

    Attributes:
        enabled: ``true`` to turn it on, ``false`` to turn it off
            ``true`` 為打開，``false`` 為關掉
    """

    enabled: bool


class ModelOptionRead(BaseModel):
    """A model in the whitelist.

    白名單中的一個模型。

    An empty ``efforts`` list means this model takes no effort.

    ``efforts`` 為空串列＝此模型不使用 effort。
    """

    name: str
    efforts: list[str]


class ProviderOptionRead(BaseModel):
    """A selectable provider and its whitelist, without secrets.

    可選的 provider 與其白名單（不含金鑰）。

    Attributes:
        id: the provider ID (an endpoint ID, ``whisper``, or a strategy name)
            provider ID（端點 ID、``whisper`` 或策略名）
        type: the type (the endpoint type, for example ``claude-code``; ``whisper``; ``strategy`` for strategies)
            類型（端點類型，例如 ``claude-code``；``whisper``；策略為 ``strategy``）
        models: the selectable models; an empty list means this provider has no model to choose (strategies)
            可選的模型；空串列＝這個 provider 不選模型（策略）
        title: the description (strategies only)
            說明（策略才有）
    """

    id: str
    type: str
    models: list[ModelOptionRead]
    title: str | None = None


class CapabilityRoutesResponse(BaseModel):
    """The state of every route key plus the selectable options.

    全部用途鍵的狀態與可選項目。

    Attributes:
        routes: the state of each route key (global, declared, and orphan keys)
            各用途鍵的狀態（全域、已宣告、孤兒鍵）
        providers: capability mapped to the selectable providers and their whitelists (all strategies for
            ``audio_eval``; see ``strategies`` for per-key restrictions)
            能力 → 可選的 provider 與白名單（``audio_eval`` 為全部策略；用途鍵的限縮見
            ``strategies``）
        strategies: route keys that restrict the scoring strategies, mapped to the allowed strategies (keys not
            listed may use every strategy)
            有限縮評分策略的用途鍵 → 允許的策略（沒列出的鍵可用全部策略）
        db_unavailable: whether the database could not be read; each row then shows the ``.env`` default and
            orphan keys are not listed
            讀不到資料庫：各列為 ``.env`` 預設，孤兒鍵不在清單中
    """

    routes: list[RouteRead]
    providers: dict[str, list[ProviderOptionRead]]
    strategies: dict[str, list[str]] = Field(default_factory=dict)
    db_unavailable: bool


class RouteDeleteResponse(BaseModel):
    """The result of restoring the default.

    恢復預設的結果。

    Attributes:
        deleted: whether a row was actually deleted (false when nothing was set)
            是否真的刪了一列（原本沒有設定時為 false）
        route: the state of this route key after the delete; null when an orphan key no longer exists after
            all its rows are deleted, or when the state cannot be built (``route_unavailable`` tells them apart)
            刪除後這個用途鍵的狀態；孤兒鍵刪光後不再存在、或組不出狀態時為 null（以
            ``route_unavailable`` 區分）
        route_unavailable: whether the delete committed but the post-delete state could not be built (see the
            backend log; GET again to see it)
            刪除已提交，但組不出刪除後的狀態（詳見後端 log；請重新 GET 查看）
    """

    deleted: bool
    route: RouteRead | None
    route_unavailable: bool
