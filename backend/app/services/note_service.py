"""
筆記服務層（handlers 計畫書 D1）：網頁與 TG 對 handler 的操作都經過這裡。

Note service layer: every web and Telegram operation on a handler goes
through here.

負責 handler 本身不管的事：能力檢查、建立參數驗證、「筆記歸這個 handler」的檢查、寫入後同步。
handler 只管怎麼建、怎麼讀、怎麼改（``BaseHandler`` 的標準實作或各自的覆寫）。

It owns what handlers do not: capability checks, create-parameter
validation, note-ownership checks and the sync after writes.
"""

from __future__ import annotations

import hashlib
import logging
from functools import cache
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, create_model

from app.core.config import settings
from app.core.exceptions import (
    AnkiFieldCorruptedError,
    FieldNotEditableError,
    HandlerOperationUnsupportedError,
    JsonFieldConflictError,
    NoteNotOwnedByHandlerError,
    NoteTypeNotCreatableError,
)
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.services.task_handlers.base import note_detail, note_field_values
from app.services.task_handlers.registry import handler_registry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.infrastructure.anki.client import AnkiClient
    from app.services.card_service import CardService
    from app.services.relation_service import RelationService
    from app.services.task_handlers.base import BaseHandler
    from app.services.task_handlers.capabilities import Operation
    from app.services.task_handlers.registry import HandlerRegistry

logger = logging.getLogger(__name__)

#: 查不到 Anki 欄位、無法產生標準建立表單時，加在建卡原因裡的說明（R8）。
ANKI_UNREACHABLE = "無法連線 Anki，暫時無法取得建卡表單"


class NoteService:
    """網頁與 TG 共用的筆記操作。每個請求（或每則 TG 更新）建立一次。

    Note operations shared by the web and Telegram; built once per request.

    Attributes:
        card_service: 卡片服務。Card service.
        relation_service: 關聯服務。Relation service.
        registry: handler 註冊表。The handler registry.
    """

    def __init__(
        self,
        card_service: CardService,
        relation_service: RelationService,
        registry: HandlerRegistry = handler_registry,
    ) -> None:
        """初始化服務。Initialize the service.

        Args:
            card_service: 卡片服務。Card service.
            relation_service: 關聯服務。Relation service.
            registry: handler 註冊表；測試可換成自己的。The registry.
        """
        self.card_service = card_service
        self.relation_service = relation_service
        self.registry = registry

    @property
    def anki_client(self) -> AnkiClient:
        """``CardService`` 用的 AnkiConnect 客戶端。The AnkiConnect client."""
        return self.card_service.anki_client

    # =========================================================================
    # 列出 handler
    # =========================================================================

    async def list_handlers(self) -> list[dict[str, object]]:
        """所有 handler 的名稱、標題、筆記類型、能力與建卡表單（§4.1）。

        Every handler with its capabilities and create form.

        標準建立的表單向 Anki 查欄位即時產生；Anki 連不上時該 handler 的 ``create_schema`` 為 null、
        ``create.reason`` 註明原因，其餘照常回傳（R8）。

        Returns:
            handler 清單，依註冊順序。The handlers, in registration order.
        """
        result = []
        for handler in self.registry.all_handlers():
            capabilities = handler.CAPABILITIES.to_dict()
            try:
                create_schema = await self._create_schema(handler)
            except AnkiConnectError as exc:
                logger.warning("無法取得 %s 的建卡欄位: %s", handler.handler_name, exc)
                create_schema = None
                create = dict(capabilities["create"])  # type: ignore[arg-type]
                create["reason"] = ANKI_UNREACHABLE
                capabilities["create"] = create
            result.append({
                "name": handler.handler_name,
                "title": handler.title,
                "note_types": list(handler.supported_models),
                "capabilities": capabilities,
                "create_schema": create_schema,
                "json_fields": list(handler.JSON_FIELDS),
                "json_field_specs": json_field_specs(handler),
                "media_fields": list(handler.MEDIA_FIELDS),
            })
        return result

    async def _create_schema(self, handler: BaseHandler) -> dict[str, object] | None:
        """網頁建卡表單的 JSON Schema；不開放或不是表單流程時為 None（D3）。

        The create form schema, or None when there is no form.

        Raises:
            AnkiConnectError: 標準建立查欄位失敗。When the field lookup fails.
        """
        create = handler.CAPABILITIES.create
        if not create.supported or create.flow != "form" or create.note_type is None:
            return None
        if handler.CREATE_PARAMS is not None:
            return handler.CREATE_PARAMS.model_json_schema()
        model = await self._standard_params_model(handler, create.note_type)
        return model.model_json_schema(by_alias=True)

    async def _standard_params_model(self, handler: BaseHandler, note_type: str) -> type[BaseModel]:
        """標準建立的參數模型：向 Anki 查欄位，JSON、媒體欄位不列（D3）。

        The standard create parameter model built from Anki's field list.
        """
        names = await self.anki_client.get_model_field_names(note_type)
        locked = frozenset(handler.JSON_FIELDS) | frozenset(handler.MEDIA_FIELDS)
        return standard_params_model(note_type, tuple(names), locked)

    # =========================================================================
    # 建立、讀取、修改、刪除
    # =========================================================================

    async def create(
        self,
        handler_name: str,
        deck: str,
        note_type: str,
        params: Mapping[str, object],
        *,
        tg_bot: str | None = None,
    ) -> dict[str, object]:
        """建卡：檢查能力 → 驗證參數 → ``execute_create`` → 寫入後同步（§4.2）。

        Create cards: capability check, parameter validation, the handler's
        create, then the sync after writing.

        Args:
            handler_name: handler 名稱。The handler.
            deck: 牌組；``create.deck`` 為 ``fixed`` 時忽略。The deck, ignored when fixed.
            note_type: 必須是 ``create.note_type``。Must be ``create.note_type``.
            params: 建立參數（標準建立是筆記欄位；否則是 ``CREATE_PARAMS``）。The parameters.
            tg_bot: 寫進卡片的 TG Bot 名稱；預設 ``settings.TG_BOT_USERNAME``，TG 傳入自己的覆寫。
                只用於宣告了 ``CREATE_PARAMS`` 的 handler。The bot username.

        Returns:
            ``{"note_ids", "deck", "sync_warning"}``。

        Raises:
            HandlerNotFoundError: handler 不存在。Unknown handler.
            HandlerOperationUnsupportedError: 不開放建卡。Creation not offered.
            NoteTypeNotCreatableError: 筆記類型不是能建立的那種。Wrong note type.
            pydantic.ValidationError: 參數驗證失敗（REST 轉成 422）。Invalid parameters.
            FieldNotEditableError: 填了 JSON、媒體欄位。A locked field was given.
            FieldMismatchError: 欄位不屬於這個筆記類型。Unknown fields.
            DuplicateCardError: 首欄位重複。Duplicate first field.
        """
        handler = self.registry.get_handler(handler_name)
        create = handler.CAPABILITIES.create
        if not create.supported:
            raise HandlerOperationUnsupportedError(create.reason or "")
        if note_type != create.note_type:
            raise NoteTypeNotCreatableError(f"「{handler.title}」只能建立 {create.note_type}，不能建立 {note_type}")
        if create.deck == "fixed" and create.fixed_deck:
            deck = create.fixed_deck

        if handler.CREATE_PARAMS is not None:
            parameters = handler.CREATE_PARAMS.model_validate(dict(params)).model_dump()
            parameters["tg_bot"] = tg_bot if tg_bot is not None else (settings.TG_BOT_USERNAME or "")
        else:
            model = await self._standard_params_model(handler, note_type)
            parameters = model.model_validate(dict(params)).model_dump(by_alias=True)

        note_ids = await handler.execute_create(self.card_service, self.relation_service, deck, note_type, parameters)
        return {"note_ids": list(note_ids), "deck": deck, "sync_warning": await self.sync_after_write()}

    async def read(self, handler_name: str, note_id: int) -> dict[str, object]:
        """handler 讀取（§4.3）：成組卡片的 handler 之後會附上整組。

        Read a note through its handler.

        Raises:
            HandlerNotFoundError: handler 不存在。Unknown handler.
            HandlerOperationUnsupportedError: 不開放讀取。Reading not offered.
            NoteNotFoundError: 筆記不存在。Missing note.
            NoteNotOwnedByHandlerError: 筆記不歸這個 handler。Not this handler's note.
        """
        handler = self.registry.get_handler(handler_name)
        _require(handler.CAPABILITIES.read)
        detail = await handler.execute_read(self.card_service, self.relation_service, note_id)
        # 讀取沒有副作用，讀完再檢查歸屬，省一次查詢
        _require_owned(handler, str(detail["note_type"]))
        return detail

    async def get(self, note_id: int) -> dict[str, object]:
        """通用讀取：任何筆記類型都能讀，附上負責的 handler 與權限（§4.3、D4）。

        Generic read of any note, with its handler and permissions.

        Raises:
            NoteNotFoundError: 筆記不存在。Missing note.
        """
        note = await self.card_service.get_note(note_id)
        detail = note_detail(note)
        note_type = str(detail["note_type"])
        handler = self.registry.handler_for_note_type(note_type)
        if handler is None:
            reason = f"沒有 handler 負責 {note_type}"
            permissions = {
                name: {"allowed": False, "reason": reason} for name in ("update_fields", "update_json", "delete")
            }
            json_fields: dict[str, object] = {}
        else:
            caps = handler.CAPABILITIES
            permissions = {
                "update_fields": _permission(caps.update_fields),
                "update_json": _permission(caps.update_json),
                "delete": _permission(caps.delete),
            }
            values = note_field_values(note)
            json_fields = {name: json_field_state(values.get(name, "")) for name in handler.JSON_FIELD_SPECS}
        return {
            **detail,
            "handler": handler.handler_name if handler else None,
            "permissions": permissions,
            "json_fields": json_fields,
        }

    async def update_fields(
        self, handler_name: str, note_id: int, fields: Mapping[str, object]
    ) -> dict[str, object]:
        """修改欄位（§4.4）：只寫有變的欄位，回傳修改後的完整筆記與同步提醒。

        Update fields and return the updated note with the sync warning.

        Raises:
            HandlerNotFoundError: handler 不存在。Unknown handler.
            HandlerOperationUnsupportedError: 不開放修改。Updating not offered.
            NoteNotFoundError: 筆記不存在。Missing note.
            NoteNotOwnedByHandlerError: 筆記不歸這個 handler。Not this handler's note.
            FieldNotEditableError: 改了 JSON、媒體欄位。A locked field was given.
            FieldMismatchError: 欄位不屬於這個筆記類型。Unknown fields.
        """
        handler = await self._owning_handler(handler_name, note_id, "update_fields")
        await handler.execute_update(
            self.card_service, self.relation_service, note_id, {"action": "update_fields", "fields": dict(fields)}
        )
        sync_warning = await self.sync_after_write()
        detail = note_detail(await self.card_service.get_note(note_id))
        return {**detail, "sync_warning": sync_warning}

    async def update_json(
        self, handler_name: str, note_id: int, field_name: str, items: list[object], version: str
    ) -> dict[str, object]:
        """整欄寫入一個 JSON 欄位（json_field_editor 計畫 D4）：檢查能力與歸屬 → 版本 → 格式 → 寫入
        → handler 的善後 → 同步。

        Write a whole JSON field: capability and ownership, version, format,
        write, the handler's follow-up, then the sync.

        Args:
            handler_name: handler 名稱。The handler.
            note_id: 筆記 ID。The note.
            field_name: JSON 欄位，必須在 ``JSON_FIELD_SPECS`` 內。The field.
            items: 整個清單。The whole list.
            version: 讀取時拿到的版本（欄位原始字串的雜湊）。The version read.

        Returns:
            ``{"items", "version", "sync_warning"}``：寫入後的清單與新的版本。The new state.

        Raises:
            HandlerOperationUnsupportedError: 不開放 JSON 編輯。Not offered.
            NoteNotOwnedByHandlerError: 筆記不歸這個 handler。Not this handler's note.
            FieldNotEditableError: 這個欄位不能以表單編輯。The field is not editable.
            JsonFieldConflictError: 版本不符（別處改過）。The field changed elsewhere.
            pydantic.ValidationError: 某一筆格式不對（REST 轉成 422，loc 指到第幾筆）。Invalid items.
        """
        handler = await self._owning_handler(handler_name, note_id, "update_json")
        spec = handler.JSON_FIELD_SPECS.get(field_name)
        if spec is None:
            raise FieldNotEditableError(f"{field_name} 不能以表單編輯")
        raw = note_field_values(await self.card_service.get_note(note_id)).get(field_name, "")
        if json_field_version(raw) != version:
            raise JsonFieldConflictError(f"「{spec.title}」在別處被改過，請重新載入後再改")
        validated = TypeAdapter(list[spec.item]).validate_python(items)  # type: ignore[name-defined]
        data = [item.model_dump(mode="json") for item in validated]
        await AnkiJsonFieldManager.update_field(self.card_service, note_id, field_name, data)
        await handler.after_json_update(self.card_service, note_id, field_name, data)
        sync_warning = await self.sync_after_write()
        current = note_field_values(await self.card_service.get_note(note_id)).get(field_name, "")
        return {**json_field_state(current), "sync_warning": sync_warning}

    async def run_action(self, note_id: int, action: str, params: Mapping[str, object]) -> str | None:
        """具名動作（例如口說卡的 ``add_audio``）：依筆記類型找 handler，不帶 handler 名稱。

        Run a named action; the handler is found from the note type.

        Args:
            note_id: 筆記 ID。The note.
            action: 動作名稱，必須在 ``CAPABILITIES.actions`` 內。The action.
            params: 動作參數。The action parameters.

        Returns:
            同步提醒；同步成功時為 None。The sync warning, or None.

        Raises:
            NoteNotFoundError: 筆記不存在。Missing note.
            HandlerOperationUnsupportedError: 沒有 handler 負責，或動作不開放。No handler,
                or the action is not offered.
        """
        note = await self.card_service.get_note(note_id)
        note_type = str(note["modelName"])
        handler = self.registry.handler_for_note_type(note_type)
        if handler is None:
            raise HandlerOperationUnsupportedError(f"沒有 handler 負責 {note_type}")
        if action not in handler.CAPABILITIES.actions:
            raise HandlerOperationUnsupportedError(f"「{handler.title}」不支援動作：{action}")
        await handler.execute_update(
            self.card_service, self.relation_service, note_id, {**params, "action": action}
        )
        return await self.sync_after_write()

    async def delete(self, handler_name: str, note_id: int) -> str | None:
        """刪除（§4.5）：不開放時 Anki 與關聯都不會被動到。

        Delete a note; nothing is touched when deleting is not offered.

        Returns:
            同步提醒；同步成功時為 None。The sync warning, or None.

        Raises:
            HandlerNotFoundError: handler 不存在。Unknown handler.
            HandlerOperationUnsupportedError: 不開放刪除。Deleting not offered.
            NoteNotFoundError: 筆記不存在。Missing note.
            NoteNotOwnedByHandlerError: 筆記不歸這個 handler。Not this handler's note.
        """
        handler = await self._owning_handler(handler_name, note_id, "delete")
        await handler.execute_delete(self.card_service, self.relation_service, note_id)
        return await self.sync_after_write()

    async def graph(
        self,
        handler_name: str,
        deck: str | None = None,
        *,
        note_type: str | None = None,
        label_field: str | None = None,
    ) -> dict[str, object]:
        """handler 的圖譜（§4.6）。Read a handler's graph.

        Raises:
            HandlerNotFoundError: handler 不存在。Unknown handler.
            HandlerOperationUnsupportedError: 不開放圖譜。Graph not offered.
            InvalidNoteQueryError: 參數不適用。Parameters that do not apply.
        """
        handler = self.registry.get_handler(handler_name)
        graph = handler.CAPABILITIES.graph
        if not graph.supported:
            raise HandlerOperationUnsupportedError(graph.reason or "")
        return await handler.execute_read_graph(
            self.card_service, self.relation_service, deck, note_type=note_type, label_field=label_field
        )

    async def _owning_handler(self, handler_name: str, note_id: int, operation: str) -> BaseHandler:
        """寫入前的檢查：handler 存在、操作開放、筆記存在且歸它。

        Pre-write checks: the handler exists, offers the operation, and owns
        the note.
        """
        handler = self.registry.get_handler(handler_name)
        _require(getattr(handler.CAPABILITIES, operation))
        note = await self.card_service.get_note(note_id)
        _require_owned(handler, str(note["modelName"]))
        return handler

    # =========================================================================
    # 寫入後同步
    # =========================================================================

    async def sync_after_write(self) -> str | None:
        """寫入後強制同步並等它完成；失敗時回傳提醒文字而不是拋錯（D1）。

        Force a sync after writing and wait for it; return a warning
        instead of raising on failure.

        Returns:
            提醒文字（純文字，TG 自己加換行）；同步成功時為 None。The warning, or None.
        """
        return await sync_warning(self.anki_client)


async def sync_warning(anki_client: AnkiClient) -> str | None:
    """強制同步 AnkiWeb；失敗時回傳給使用者看的提醒（資料已寫進電腦版 Anki，只是沒同步）。

    Force a sync with AnkiWeb and return a user-facing warning on failure.

    給還沒經過服務層的寫入點（TG 的 ``_sync_with_warning``）共用同一份提醒文字。

    Args:
        anki_client: AnkiConnect 客戶端。The AnkiConnect client.

    Returns:
        提醒文字；同步成功時為 None。The warning, or None.
    """
    try:
        await anki_client.sync(raise_errors=True, force=True)
    except AnkiConnectError as exc:
        logger.warning("Anki 同步失敗: %s", exc)
        # 衝突時「上傳」會以電腦版覆蓋 AnkiWeb（手機上還沒同步的複習紀錄會遺失），不能寫成無副作用
        if "衝突" in str(exc) or "背景同步" in str(exc):
            return "⚠️ 已寫入 Anki，但同步衝突：請在電腦版 Anki 手動同步（選「上傳」會以電腦版覆蓋 AnkiWeb）。"
        return "⚠️ 已寫入 Anki，但同步 AnkiWeb 失敗；下次操作會再試。"
    return None


@cache
def standard_params_model(
    note_type: str, field_names: tuple[str, ...], locked: frozenset[str]
) -> type[BaseModel]:
    """標準建立的參數模型（D3）：每個欄位都是選填字串，第一個欄位必填。

    The standard create parameter model: optional string fields, the first
    one required.

    JSON、媒體欄位不列；多餘的鍵不在這裡擋，交給 handler（``FIELD_NOT_EDITABLE``）與
    ``CardService``（``FIELD_MISMATCH``）回報。欄位名可能含空白等字元，所以以別名對應。

    Locked fields are left out; unknown keys pass through so the handler and
    ``CardService`` report them. Field names map through aliases.

    Args:
        note_type: 筆記類型（模型名稱用）。The note type, used to name the model.
        field_names: Anki 的欄位名，依順序。Anki's field names, in order.
        locked: JSON、媒體欄位。Locked fields.

    Returns:
        Pydantic 模型。The model.
    """
    definitions: dict[str, object] = {}
    for index, name in enumerate(field_names):
        if name in locked:
            continue
        if index == 0:
            # Anki 不允許第一個欄位為空，也以它判斷重複
            first = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
            definitions[f"field_{index}"] = (first, Field(alias=name, title=name))
        else:
            definitions[f"field_{index}"] = (str, Field(default="", alias=name, title=name))
    return create_model(  # type: ignore[call-overload]
        f"Create_{note_type}",
        __config__=ConfigDict(extra="allow"),
        **definitions,
    )


def json_field_version(raw: str) -> str:
    """JSON 欄位的版本：原始字串的 SHA-256 前 16 碼（json_field_editor 計畫 D3）。The field's version."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def json_field_state(raw: str) -> dict[str, object]:
    """讀取時回傳的一個 JSON 欄位：解析好的清單、版本與錯誤（壞掉時清單為空、不能編輯）。

    One JSON field as returned by the read: items, version and error.
    """
    try:
        items: list[object] = AnkiJsonFieldManager.parse_field_string(raw)
        error = None
    except AnkiFieldCorruptedError as exc:
        items, error = [], exc.message
    return {"items": items, "version": json_field_version(raw), "error": error}


def json_field_specs(handler: BaseHandler) -> dict[str, object]:
    """``GET /handlers`` 的 JSON 欄位格式：標題與每一筆的 JSON Schema。The JSON field formats."""
    return {
        name: {"title": spec.title, "item_schema": spec.item.model_json_schema()}
        for name, spec in handler.JSON_FIELD_SPECS.items()
    }


def _require(operation: Operation) -> None:
    """操作不開放時拋錯，訊息為能力宣告的原因。Raise when the operation is closed."""
    if not operation.supported:
        raise HandlerOperationUnsupportedError(operation.reason or "")


def _require_owned(handler: BaseHandler, note_type: str) -> None:
    """筆記的類型要歸這個 handler（D1）。The note type must belong to the handler."""
    if note_type not in handler.supported_models:
        raise NoteNotOwnedByHandlerError(f"{note_type} 的筆記不歸「{handler.title}」負責")


def _permission(operation: Operation) -> dict[str, object]:
    """通用讀取的權限項目；不允許時必有原因。A permission entry for the generic read."""
    return {"allowed": operation.supported, "reason": None if operation.supported else operation.reason}
