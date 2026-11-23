"""Note service layer (handlers plan D1): every web and Telegram operation on a handler goes through here.

筆記服務層（handlers 計畫書 D1）：網頁與 TG 對 handler 的操作都經過這裡。

It takes care of what handlers themselves do not: capability checks, create-parameter validation, the "this note
belongs to this handler" check, and the sync after writes. Handlers only care about how to create, read, and update
(the standard implementation in ``BaseHandler`` or their own overrides).

負責 handler 本身不管的事：能力檢查、建立參數驗證、「筆記歸這個 handler」的檢查、寫入後同步。
handler 只管怎麼建、怎麼讀、怎麼改（``BaseHandler`` 的標準實作或各自的覆寫）。
"""

from __future__ import annotations

import hashlib
import html
import logging
from functools import cache
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, create_model

from app.core.config import settings
from app.core.exceptions import (
    AnkiFieldCorruptedError,
    DuplicateFoundError,
    FieldNotEditableError,
    HandlerOperationUnsupportedError,
    InvalidNoteFieldsError,
    JsonFieldConflictError,
    NoteNotOwnedByHandlerError,
    NoteTypeNotCreatableError,
    TagInvalidError,
)
from app.services.tag_rules import is_system_tag, normalize_tags

#: How many existing tags go into the prompt when generating a draft (the most used ones first).
#: 生成草稿時放進提示詞的既有標籤數（依次數取前幾個）。
MAX_PROMPT_TAGS = 50
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.anki.search import contains_term, note_types_query, tag_term
from app.services.media_commit import committed_media, staged_names
from app.services.media_names import MEDIA_KINDS
from app.services.media_staging import media_staging
from app.services.task_handlers.base import (
    DRAFT_SYSTEM_KEYS,
    DUPLICATE_CHECK_SKIPPED_KEY,
    DuplicateDecision,
    DuplicateResolution,
    note_detail,
    note_field_values,
)
from app.services.task_handlers.capabilities import ANY_FIELD_KEY, TAG_SEARCH_KEY
from app.services.task_handlers.registry import handler_registry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.infrastructure.anki.client import AnkiClient
    from app.services.card_service import CardService
    from app.services.media_staging import MediaStaging
    from app.services.relation_service import RelationService
    from app.services.task_handlers.base import BaseHandler
    from app.services.task_handlers.capabilities import Operation
    from app.services.task_handlers.registry import HandlerRegistry

logger = logging.getLogger(__name__)

#: The explanation added to the create reason when Anki's fields cannot be looked up and the standard create form
#: cannot be built (R8).
#: 查不到 Anki 欄位、無法產生標準建立表單時，加在建卡原因裡的說明（R8）。
ANKI_UNREACHABLE = "無法連線 Anki，暫時無法取得建卡表單"


class NoteService:
    """Note operations shared by the web and Telegram, built once per request (or per Telegram update).

    網頁與 TG 共用的筆記操作。每個請求（或每則 TG 更新）建立一次。

    Attributes:
        card_service: the card service
            卡片服務
        relation_service: the relation service
            關聯服務
        registry: the handler registry
            handler 註冊表
        staging: the staging area of web uploads
            網頁上傳的暫存區
    """

    def __init__(
        self,
        card_service: CardService,
        relation_service: RelationService,
        registry: HandlerRegistry = handler_registry,
        staging: MediaStaging | None = None,
    ) -> None:
        """Initialize the service.

        初始化服務。

        Args:
            card_service: the card service
                卡片服務
            relation_service: the relation service
                關聯服務
            registry: the handler registry; tests may pass their own
                handler 註冊表；測試可換成自己的
            staging: the staging area of web uploads; ``None`` uses the site-wide ``media_staging``
                網頁上傳的暫存區；省略時用全站共用的 ``media_staging``
        """
        self.card_service = card_service
        self.relation_service = relation_service
        self.registry = registry
        self.staging = staging or media_staging

    @property
    def anki_client(self) -> AnkiClient:
        """The AnkiConnect client used by ``CardService``.

        ``CardService`` 用的 AnkiConnect 客戶端。
        """
        return self.card_service.anki_client

    # =========================================================================
    # 列出 handler
    # =========================================================================

    async def list_handlers(self) -> list[dict[str, object]]:
        """Return every handler's name, title, note types, capabilities, and create form (§4.1).

        回傳所有 handler 的名稱、標題、筆記類型、能力與建卡表單（§4.1）。

        The standard create form is built on the fly from Anki's field list; when Anki is unreachable, that
        handler's ``create_schema`` is null and ``create.reason`` explains why, and everything else is returned as
        usual (R8).

        標準建立的表單向 Anki 查欄位即時產生；Anki 連不上時該 handler 的 ``create_schema`` 為 null、
        ``create.reason`` 註明原因，其餘照常回傳（R8）。

        Returns:
            the handlers, in registration order
            handler 清單，依註冊順序
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
                "draft_schema": _draft_schema(handler),
                "json_fields": list(handler.JSON_FIELDS),
                "json_field_specs": json_field_specs(handler),
                "media_fields": dict(handler.MEDIA_FIELDS),
                "search_fields": [
                    {"key": f.key, "title": f.title} for f in handler.search_fields()
                ] if handler.CAPABILITIES.search.supported else [],
            })
        return result

    async def _create_schema(self, handler: BaseHandler) -> dict[str, object] | None:
        """Return the JSON Schema of the web create form (D3).

        回傳網頁建卡表單的 JSON Schema（D3）。

        Returns:
            the schema, or ``None`` when creation is not offered or is not a form flow
            schema；不開放或不是表單流程時為 ``None``

        Raises:
            AnkiConnectError: if looking up the fields for standard creation fails
                若標準建立查欄位失敗
        """
        create = handler.CAPABILITIES.create
        if not create.supported or create.flow != "form" or create.note_type is None:
            return None
        if handler.CREATE_PARAMS is not None:
            return handler.CREATE_PARAMS.model_json_schema()
        model = await self._standard_params_model(handler, create.note_type)
        return model.model_json_schema(by_alias=True)

    async def _standard_params_model(self, handler: BaseHandler, note_type: str) -> type[BaseModel]:
        """Return the standard create parameter model built from Anki's field list.

        回傳標準建立的參數模型。

        JSON fields are left out and media fields are marked as upload widgets (D3, media_upload D3).

        向 Anki 查欄位，JSON 欄位不列，媒體欄位標成上傳元件（D3、media_upload D3）。
        """
        names = await self.anki_client.get_model_field_names(note_type)
        media = tuple(sorted(handler.MEDIA_FIELDS.items()))
        return standard_params_model(note_type, tuple(names), frozenset(handler.JSON_FIELDS), media)

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
        tags: list[str] | None = None,
    ) -> dict[str, object]:
        """Create cards (§4.2).

        建卡（§4.2）。

        Steps: check the capability → validate the parameters and tags → commit the uploaded media and
        ``execute_create`` → add the tags → follow-up processing (``after_create``, for example deleting the old
        card when overwriting) → sync after writing.

        步驟：檢查能力 → 驗證參數與標籤 → 存入上傳的媒體並 ``execute_create`` → 加標籤 → 後續處理
        （``after_create``，例如覆蓋時刪舊卡）→ 寫入後同步。

        When a media field holds an uploaded file name, the file is only committed from the staging area into Anki
        here; if ``execute_create`` fails it is rolled back, Anki is as before the submission, and the staged file
        stays so the request can simply be sent again (``media_commit``).

        媒體欄位填了上傳的檔名時，那些檔案在這裡才從暫存區存進 Anki；``execute_create`` 失敗就撤回，Anki 和
        送出前一樣，暫存檔留著可以直接重送（``media_commit``）。

        Args:
            handler_name: the handler name
                handler 名稱
            deck: the deck; ignored when ``create.deck`` is ``fixed``
                牌組；``create.deck`` 為 ``fixed`` 時忽略
            note_type: must be ``create.note_type``
                必須是 ``create.note_type``
            params: the create parameters (note fields for standard creation; otherwise ``CREATE_PARAMS``)
                建立參數（標準建立是筆記欄位；否則是 ``CREATE_PARAMS``）
            tg_bot: the TG bot name written into the card; defaults to ``settings.TG_BOT_USERNAME``, and TG passes
                its own to override it. Used only by handlers that declare ``CREATE_PARAMS``.
                寫進卡片的 TG Bot 名稱；預設 ``settings.TG_BOT_USERNAME``，TG 傳入自己的覆寫。
                只用於宣告了 ``CREATE_PARAMS`` 的 handler
            tags: the user's tags (cleaned by ``tag_rules.normalize_tags``); added separately after creation, so
                handlers' create code need not care about them
                使用者加的標籤（``tag_rules.normalize_tags`` 整理）；建卡後另外加上，各 handler 的
                建卡程式不用管

        Returns:
            ``{"note_ids", "deck", "sync_warning"}``; ``sync_warning`` holds the warnings of tagging, follow-up
            processing, and sync (newline-separated)
            ``{"note_ids", "deck", "sync_warning"}``；``sync_warning`` 是加標籤、後續處理、同步的提醒（換行分隔）

        Raises:
            DuplicateCardError: if the first field is a duplicate
                若首欄位重複
            FieldMismatchError: if a field does not belong to this note type
                若欄位不屬於這個筆記類型
            FieldNotEditableError: if a JSON field was given
                若填了 JSON 欄位
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if creation is not offered
                若不開放建卡
            MediaFieldInvalidError: if a media field does not hold an uploaded file name (checked before creating)
                若媒體欄位填的不是上傳取得的檔名（建卡之前檢查）
            MediaUploadExpiredError: if an upload used is no longer in the staging area (nothing is written)
                若用到的上傳檔已不在暫存區（什麼都沒寫）
            NoteTypeNotCreatableError: if the note type is not the one that can be created
                若筆記類型不是能建立的那種
            ParentNoteInvalidError: if the chosen parent card cannot hold child cards (checked before creating)
                若選的上層卡不能放子卡（建卡之前檢查）
            TagInvalidError: if a tag is invalid (checked before creating, so no card is created)
                若標籤不合格（在建卡之前檢查，不會建出卡片）
            pydantic.ValidationError: if parameter validation fails (REST turns it into 422)
                若參數驗證失敗（REST 轉成 422）
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

        user_tags = normalize_tags(tags or [])
        # 例如三語卡的子卡：放進上層卡的同名子牌組（在加標籤、存媒體之前決定，失敗就什麼都沒寫）
        deck = await handler.resolve_create_deck(self.card_service, deck, parameters)

        media = staged_names(parameters, _create_media_fields(handler))
        async with committed_media(self.anki_client, self.staging, media):
            note_ids = await handler.execute_create(
                self.card_service, self.relation_service, deck, note_type, parameters
            )
        tag_warning = await self._add_tags(list(note_ids), user_tags)
        # 後續處理（例如覆蓋時刪舊卡）在同步之前：刪除也在這一次同步推上 AnkiWeb
        try:
            after_warning = await handler.after_create(self.card_service, parameters, list(note_ids))
        except Exception:  # noqa: BLE001 - 卡片已寫入：不能報成建立失敗，否則草稿退回可建立、使用者會再建一組
            logger.exception("%s 建卡後的後續處理失敗（note_ids=%s）", handler_name, list(note_ids))
            after_warning = "⚠️ 卡片已建立，但建立後的後續處理失敗（例如沒刪掉要覆蓋的舊卡），請到 Cards 頁確認。"
        sync = await self.sync_after_write()
        warning = "\n".join(w for w in (tag_warning, after_warning, sync) if w) or None
        return {"note_ids": list(note_ids), "deck": deck, "sync_warning": warning}

    async def _add_tags(self, note_ids: list[int], tags: list[str]) -> str | None:
        """Add the user's tags after creation.

        建卡後加上使用者的標籤。

        On failure the cards already exist, so a warning is returned instead of raising (so the user does not think
        creation failed and create them again).

        失敗時卡片已建立，回傳提醒而不拋錯（避免使用者以為沒建成而重建）。
        """
        if not tags or not note_ids:
            return None
        try:
            await self.anki_client.add_tags(note_ids, " ".join(tags))
        except AnkiConnectError as exc:
            logger.warning("筆記 %s 加標籤 %s 失敗: %s", note_ids, tags, exc)
            return f"卡片已建立，但標籤沒加上（{'、'.join(tags)}）：{exc}"
        return None

    async def tags(self, handler_name: str) -> list[dict[str, object]]:
        """Return the tags used by this handler's note types, with counts (for autocomplete and LLM suggestions).

        回傳這個 handler 的筆記類型用過的標籤與次數（自動完成與 LLM 推薦用）。

        Args:
            handler_name: the handler name
                handler 名稱

        Returns:
            ``[{"tag", "count"}]``, most used first (ties in case-insensitive order), system tags excluded
            ``[{"tag", "count"}]``，依次數排序（同次數依不分大小寫的字母順序），排除系統標籤

        Raises:
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if reading is not offered
                若不開放讀取
        """
        handler = self.registry.get_handler(handler_name)
        _require(handler.CAPABILITIES.read)
        note_ids = await self.anki_client.find_notes(note_types_query(list(handler.supported_models)))
        notes = await self.anki_client.get_notes_info(note_ids) if note_ids else []
        counts: dict[str, int] = {}
        for note in notes:
            for tag in note.tags:
                if not is_system_tag(tag):
                    counts[tag] = counts.get(tag, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
        return [{"tag": tag, "count": count} for tag, count in ranked]

    def validate_draft_input(self, handler_name: str, params: Mapping[str, object]) -> dict[str, object]:
        """Validate the draft input of the web preview flow (server_drafts plan D4).

        網頁預覽流程的草稿輸入：檢查能力 → 以 ``DRAFT_PARAMS`` 驗證（server_drafts 計畫書 D4）。

        It checks the capability, then validates with ``DRAFT_PARAMS``. It is called inside the request so errors go
        straight back to the web page; the validated input is then handed to :meth:`generate_draft` in the
        background.

        在請求裡呼叫，錯誤直接回給網頁；驗證過的輸入再交給背景的 ``generate_draft``。

        Args:
            handler_name: the handler name
                handler 名稱
            params: the draft input (``DRAFT_PARAMS``)
                草稿輸入（``DRAFT_PARAMS``）

        Returns:
            the validated input
            驗證後的輸入

        Raises:
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if there is no web preview flow
                若沒有網頁的預覽流程
            pydantic.ValidationError: if input validation fails (REST turns it into 422)
                若輸入驗證失敗（REST 轉成 422）
        """
        handler = self.registry.get_handler(handler_name)
        if handler.DRAFT_PARAMS is None or _draft_schema(handler) is None:
            raise HandlerOperationUnsupportedError(f"「{handler.title}」沒有網頁的預覽建卡流程")
        return handler.DRAFT_PARAMS.model_validate(dict(params)).model_dump()

    async def resolve_draft_duplicate(
        self,
        handler_name: str,
        parameters: dict[str, object],
        decision: DuplicateDecision | None,
        *,
        previous: DuplicateResolution | None = None,
    ) -> dict[str, object]:
        """Check for a duplicate before generating a draft (server_drafts plan D10.2).

        生成草稿之前查重複（server_drafts 計畫書 D10.2）。

        When there is a duplicate and no decision yet, it stops here without calling the LLM.

        - System keys already in the input (``DRAFT_SYSTEM_KEYS``) are always removed first; what this check finds
          is what counts.
        - When there is a duplicate and a decision has been made (overwrite, allow duplicate), the decision and the
          old card's ID are put into the input (``DuplicateResolution``) and stored in the draft, and
          ``execute_draft`` later turns them into create parameters. Without a duplicate no decision is carried.
        - ``previous`` (the decision of the last attempt, when retrying) is reused only if this check finds the
          **same** old card: if it is a different one, the user must look and decide again; an "overwrite" for card
          A must not be applied to card B the user has never seen.
        - When the Anki query fails, the check is skipped and generation goes ahead, and the reason is recorded in
          the input (``DUPLICATE_CHECK_SKIPPED_KEY``) so the web page can warn; if it really is a duplicate,
          creation is stopped by Anki's first-field check (409 ``DUPLICATE_CARD``), and the web page can switch to
          allowing the duplicate and create again.

        Currently only expression correction implements ``find_draft_duplicate``; every other handler never finds a
        duplicate.

        有重複又還沒決定時擋下，不呼叫 LLM。

        - 輸入裡原有的系統鍵（``DRAFT_SYSTEM_KEYS``）一律先拿掉，以這次查到的為準。
        - 有重複而且已經決定（覆蓋、允許重複）時，把決定與舊卡 ID 放進輸入（``DuplicateResolution``），存在草稿裡，
          ``execute_draft`` 再轉成建立參數。沒有重複時不帶決定。
        - ``previous``（重試時上一次的決定）只在這次查到的還是**同一張**舊卡時沿用：換了一張就要使用者重新看過、
          重新決定，不能把對 A 卡的「覆蓋」套到沒看過的 B 卡上。
        - Anki 查詢失敗時略過檢查照常生成，並在輸入記下原因（``DUPLICATE_CHECK_SKIPPED_KEY``）讓網頁提醒；
          真的重複時，建卡會被 Anki 的首欄位檢查擋下（409 ``DUPLICATE_CARD``），網頁可以改成允許重複再建。

        目前只有外語糾錯實作了 ``find_draft_duplicate``，其他 handler 一律查不到重複。

        Args:
            handler_name: the handler name
                handler 名稱
            parameters: the input validated by ``validate_draft_input``
                ``validate_draft_input`` 驗證過的輸入
            decision: the user's decision after seeing the old card this time; ``None`` on the first submission
                使用者這次看過舊卡後的決定；第一次送出時為 ``None``
            previous: the decision stored in the draft last time (when retrying)
                上一次存在草稿裡的決定（重試時）

        Returns:
            the input to store in the draft
            要存進草稿的輸入

        Raises:
            DuplicateFoundError: if there is a duplicate and no decision yet (the response includes the old card)
                若有重複、還沒決定（回應附上舊卡）
        """
        handler = self.registry.get_handler(handler_name)
        clean = {k: v for k, v in parameters.items() if k not in DRAFT_SYSTEM_KEYS}
        try:
            duplicate = await handler.find_draft_duplicate(self.card_service, clean)
        except AnkiConnectError as exc:
            logger.warning("生成草稿前查重複失敗，略過檢查（handler=%s, input=%r）：%s", handler_name, clean, exc)
            return {**clean, DUPLICATE_CHECK_SKIPPED_KEY: f"無法確認 Anki 是否已有同一張卡（{exc}）"}
        if duplicate is None:
            return clean
        note_id = int(str(duplicate["note_id"]))
        if decision is None and previous is not None and previous.note_id == note_id:
            decision = previous.decision
        if decision is None:
            raise DuplicateFoundError("Anki 已有同一張卡：要覆蓋舊卡、另建新卡，還是取消？", duplicate)
        return DuplicateResolution(decision, note_id).into_input(clean)

    async def generate_draft(self, handler_name: str, parameters: dict[str, object]) -> dict[str, object]:
        """Ask the LLM to generate a draft (nothing is written to Anki).

        請 LLM 生成草稿（不寫入 Anki）。

        The input must first go through :meth:`validate_draft_input`. This runs in the background
        (``CreateSession``) long after the request has ended: it uses only the AnkiConnect singleton (through
        ``self.anki_client`` and ``card_service``, to look up existing tags and the handler's ``draft_context``) and
        the handler registry, and ``execute_draft`` takes no service. ``relation_service`` must not be used here: it
        is bound to the request's DB session, which is closed once the response is sent (server_drafts plan D4,
        §11.1). An Anki failure while reading the tags or the context only leaves them out.

        輸入要先經 ``validate_draft_input``。在背景執行（``CreateSession``），請求早已結束：只用到
        AnkiConnect 單例（經 ``self.anki_client`` 與 ``card_service``，查既有標籤與 handler 的 ``draft_context``）
        與 handler 註冊表，``execute_draft`` 不接任何服務。不能在這裡用 ``relation_service``：它綁著請求的 DB
        session，回應送出後就關閉了（server_drafts 計畫書 D4、§11.1）。讀標籤或情境時 Anki 失敗，只是不提供那部分。

        Args:
            handler_name: the handler name
                handler 名稱
            parameters: the validated draft input
                驗證過的草稿輸入

        Returns:
            ``{"params": draft of the create parameters, "suggested_tags": [{"tag", "existing"}]}``; invalid and
            system tags are removed from the suggestions, and a tag differing from an existing one only in case
            takes the existing spelling
            ``{"params": 建立參數的草稿, "suggested_tags": [{"tag", "existing"}]}``。建議的標籤去掉不合格與
            系統標籤；與既有標籤只差大小寫時改用既有的寫法

        Raises:
            LLMGenerationError: if the LLM response is malformed
                若 LLM 回傳格式不對
            LLMServiceError: if the LLM call fails (or a related error)
                若 LLM 呼叫失敗（或同類錯誤）
        """
        handler = self.registry.get_handler(handler_name)
        try:
            existing = [str(item["tag"]) for item in await self.tags(handler_name)]
        except AnkiConnectError as exc:
            # 既有標籤只是給 LLM 參考：Anki 連不上時照樣生成草稿（建卡時才真的需要 Anki）
            logger.warning("生成草稿時查不到既有標籤，改為不提供：%s", exc)
            existing = []
        try:
            context = await handler.draft_context(self.card_service, parameters)
        except AnkiConnectError as exc:
            # 情境（例如三語卡的牌組路徑）只是給 LLM 參考：Anki 連不上時照樣生成
            logger.warning("生成草稿時讀不到 %s 的情境，改為不提供：%s", handler_name, exc)
            context = {}
        draft = dict(await handler.execute_draft(parameters, existing[:MAX_PROMPT_TAGS], context))
        raw_tags = draft.pop("tags", None)
        return {"params": draft, "suggested_tags": _suggested_tags(raw_tags, existing)}

    async def read(self, handler_name: str, note_id: int) -> dict[str, object]:
        """Read a note through its handler (§4.3).

        handler 讀取（§4.3）。

        Handlers of card groups will later attach the whole group.

        成組卡片的 handler 之後會附上整組。

        Args:
            handler_name: the handler name
                handler 名稱
            note_id: the note ID
                筆記 ID

        Returns:
            the note detail from the handler
            handler 回傳的筆記內容

        Raises:
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if reading is not offered
                若不開放讀取
            NoteNotFoundError: if the note does not exist
                若筆記不存在
            NoteNotOwnedByHandlerError: if the note does not belong to this handler
                若筆記不歸這個 handler
        """
        handler = self.registry.get_handler(handler_name)
        _require(handler.CAPABILITIES.read)
        detail = await handler.execute_read(self.card_service, self.relation_service, note_id)
        # 讀取沒有副作用，讀完再檢查歸屬，省一次查詢
        _require_owned(handler, str(detail["note_type"]))
        return detail

    async def get(self, note_id: int) -> dict[str, object]:
        """Read any note generically, with its handler and permissions (§4.3, D4).

        通用讀取：任何筆記類型都能讀，附上負責的 handler 與權限（§4.3、D4）。

        Args:
            note_id: the note ID
                筆記 ID

        Returns:
            the note detail plus ``handler`` (``None`` if no handler owns the note type), ``permissions``, and
            ``json_fields``; without a handler every permission is denied with a reason
            筆記內容加上 ``handler``（沒有 handler 負責時為 ``None``）、``permissions`` 與 ``json_fields``；
            沒有 handler 時每個權限都不允許並附原因

        Raises:
            NoteNotFoundError: if the note does not exist
                若筆記不存在
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
        """Update fields (§4.4): write only the changed fields, and return the updated note with the sync warning.

        修改欄位（§4.4）：只寫有變的欄位，回傳修改後的完整筆記與同步提醒。

        When a media field is changed to an uploaded file name, the file is only committed to Anki here and is
        rolled back if the write fails (as in :meth:`create`). The replaced old file is not deleted: other notes may
        still use it.

        媒體欄位換成上傳的檔名時，檔案在這裡才存進 Anki，寫入失敗就撤回（同 ``create``）。換掉的舊檔案
        不刪：可能還有其他筆記在用。

        Args:
            handler_name: the handler name
                handler 名稱
            note_id: the note ID
                筆記 ID
            fields: the fields to change, keyed by field name
                要改的欄位，以欄位名稱為鍵

        Returns:
            the full updated note plus ``sync_warning``
            修改後的完整筆記，加上 ``sync_warning``

        Raises:
            FieldMismatchError: if a field does not belong to this note type
                若欄位不屬於這個筆記類型
            FieldNotEditableError: if a JSON field was changed
                若改了 JSON 欄位
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if updating is not offered
                若不開放修改
            MediaFieldInvalidError: if a media field does not hold an uploaded file name
                若媒體欄位填的不是上傳取得的檔名
            MediaUploadExpiredError: if an upload used is no longer in the staging area (nothing is written)
                若用到的上傳檔已不在暫存區（什麼都沒寫）
            NoteNotFoundError: if the note does not exist
                若筆記不存在
            NoteNotOwnedByHandlerError: if the note does not belong to this handler
                若筆記不歸這個 handler
        """
        handler = await self._owning_handler(handler_name, note_id, "update_fields")
        media = staged_names(fields, handler.MEDIA_FIELDS)
        async with committed_media(self.anki_client, self.staging, media):
            await handler.execute_update(
                self.card_service, self.relation_service, note_id, {"action": "update_fields", "fields": dict(fields)}
            )
        sync_warning = await self.sync_after_write()
        detail = note_detail(await self.card_service.get_note(note_id))
        return {**detail, "sync_warning": sync_warning}

    async def update_json(
        self, handler_name: str, note_id: int, field_name: str, items: list[object], version: str
    ) -> dict[str, object]:
        """Write a whole JSON field (json_field_editor plan D4).

        整欄寫入一個 JSON 欄位（json_field_editor 計畫 D4）。

        Steps: check capability and ownership → version → format → write → the handler's follow-up → sync.

        步驟：檢查能力與歸屬 → 版本 → 格式 → 寫入 → handler 的善後 → 同步。

        Args:
            handler_name: the handler name
                handler 名稱
            note_id: the note ID
                筆記 ID
            field_name: the JSON field; must be in ``JSON_FIELD_SPECS``
                JSON 欄位，必須在 ``JSON_FIELD_SPECS`` 內
            items: the whole list
                整個清單
            version: the version obtained when reading (a hash of the field's raw string)
                讀取時拿到的版本（欄位原始字串的雜湊）

        Returns:
            ``{"items", "version", "error", "sync_warning"}``, the list and new version after writing
            ``{"items", "version", "error", "sync_warning"}``，寫入後的清單與新的版本

        Raises:
            FieldNotEditableError: if this field cannot be edited with a form
                若這個欄位不能以表單編輯
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if JSON editing is not offered
                若不開放 JSON 編輯
            JsonFieldConflictError: if the version does not match (changed elsewhere)
                若版本不符（別處改過）
            NoteNotFoundError: if the note does not exist
                若筆記不存在
            NoteNotOwnedByHandlerError: if the note does not belong to this handler
                若筆記不歸這個 handler
            pydantic.ValidationError: if an item is malformed (REST turns it into 422, with loc pointing at the item)
                若某一筆格式不對（REST 轉成 422，loc 指到第幾筆）
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

    def search_query(self, handler_name: str, conditions: list[tuple[str, str]]) -> str:
        """Turn web field-search conditions ("field button + keyword") into an Anki query; conditions are ANDed.

        網頁欄位搜尋：把「欄位按鈕＋關鍵字」的條件組成 Anki 查詢（條件之間是「且」）。

        - Limited to the note types this handler owns.
        - Each condition matches the Anki fields of its field button, and any one of them "containing" the keyword
          counts; key ``*`` means any field, and key ``#`` means a tag (having this tag or one of its child tags,
          without partial matching).
        - Keywords match literally (wildcards and the like are escaped); a keyword containing ``&``, ``<``, and so
          on also matches its HTML-escaped form (JSON fields and the Anki editor store escaped text). Blank keywords
          are skipped.

        - 限定這個 handler 負責的筆記類型。
        - 每個條件比對欄位按鈕對應的 Anki 欄位，任一個「包含」關鍵字就算；鍵 ``*`` 表示任何欄位，
          鍵 ``#`` 表示標籤（有這個標籤或它的子標籤，不做部分比對）。
        - 關鍵字照字面比對（萬用字元等會被跳脫）；含 ``&``、``<`` 等時也比對 HTML 轉義後的寫法
          （JSON 欄位與 Anki 編輯器存的是轉義後的文字）。空白的關鍵字略過。

        Args:
            handler_name: the handler name
                handler 名稱
            conditions: ``(field button key, keyword)`` pairs
                ``(欄位按鈕的鍵, 關鍵字)``

        Returns:
            the Anki query, handed to the card list search
            Anki 查詢，交給卡片清單的搜尋

        Raises:
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if field search is not offered
                若不開放欄位搜尋
            InvalidNoteFieldsError: if a field key cannot be searched
                若是不能搜尋的欄位鍵
        """
        handler = self.registry.get_handler(handler_name)
        _require(handler.CAPABILITIES.search)
        fields = {f.key: f.fields for f in handler.search_fields()}
        terms = [note_types_query(list(handler.supported_models))]
        for key, value in conditions:
            value = value.strip()
            if not value:
                continue
            if key == TAG_SEARCH_KEY:
                terms.append(tag_term(value))
                continue
            if key != ANY_FIELD_KEY and key not in fields:
                raise InvalidNoteFieldsError(f"「{handler.title}」不能搜尋這個欄位：{key}")
            targets: tuple[str | None, ...] = (None,) if key == ANY_FIELD_KEY else fields[key]
            variants = dict.fromkeys((value, html.escape(value)))
            alternatives = [contains_term(field, variant) for field in targets for variant in variants]
            terms.append(alternatives[0] if len(alternatives) == 1 else "(" + " OR ".join(alternatives) + ")")
        return " ".join(terms)

    async def run_action(self, note_id: int, action: str, params: Mapping[str, object]) -> str | None:
        """Run a named action (for example ``add_audio`` of speaking cards).

        執行具名動作（例如口說卡的 ``add_audio``）。

        The handler is found from the note type; no handler name is given.

        依筆記類型找 handler，不帶 handler 名稱。

        Args:
            note_id: the note ID
                筆記 ID
            action: the action name; must be in ``CAPABILITIES.actions``
                動作名稱，必須在 ``CAPABILITIES.actions`` 內
            params: the action parameters
                動作參數

        Returns:
            the sync warning, or ``None`` if the sync succeeded
            同步提醒；同步成功時為 ``None``

        Raises:
            HandlerOperationUnsupportedError: if no handler owns the note, or the action is not offered
                若沒有 handler 負責，或動作不開放
            NoteNotFoundError: if the note does not exist
                若筆記不存在
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
        """Delete a note (§4.5); when deleting is not offered, neither Anki nor relations are touched.

        刪除（§4.5）：不開放時 Anki 與關聯都不會被動到。

        Args:
            handler_name: the handler name
                handler 名稱
            note_id: the note ID
                筆記 ID

        Returns:
            the sync warning, or ``None`` if the sync succeeded
            同步提醒；同步成功時為 ``None``

        Raises:
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if deleting is not offered
                若不開放刪除
            NoteNotFoundError: if the note does not exist
                若筆記不存在
            NoteNotOwnedByHandlerError: if the note does not belong to this handler
                若筆記不歸這個 handler
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
        depth: int = 1,
    ) -> dict[str, object]:
        """Read a handler's graph (§4.6).

        讀取 handler 的圖譜（§4.6）。

        Args:
            handler_name: the handler name
                handler 名稱
            deck: the deck to limit to; ``None`` means no limit
                要限定的牌組；``None`` 表示不限定
            note_type: the note type to limit to; ``None`` means no limit
                要限定的筆記類型；``None`` 表示不限定
            label_field: the field used as node labels; ``None`` uses the handler's default
                作為節點標籤的欄位；``None`` 時用 handler 的預設
            depth: how many levels a tree graph expands, ``0`` meaning all; other graphs ignore it
                樹狀圖譜展開的層數，``0`` 表示全部；其他圖譜不使用

        Returns:
            the graph from the handler
            handler 回傳的圖譜

        Raises:
            HandlerNotFoundError: if the handler does not exist
                若 handler 不存在
            HandlerOperationUnsupportedError: if the graph is not offered
                若不開放圖譜
            InvalidNoteQueryError: if a parameter does not apply
                若參數不適用
        """
        handler = self.registry.get_handler(handler_name)
        graph = handler.CAPABILITIES.graph
        if not graph.supported:
            raise HandlerOperationUnsupportedError(graph.reason or "")
        return await handler.execute_read_graph(
            self.card_service, self.relation_service, deck, note_type=note_type, label_field=label_field, depth=depth
        )

    async def _owning_handler(self, handler_name: str, note_id: int, operation: str) -> BaseHandler:
        """Run the pre-write checks: the handler exists, offers the operation, and the note exists and is its own.

        寫入前的檢查：handler 存在、操作開放、筆記存在且歸它。
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
        """Force a sync after writing and wait for it to finish; return a warning instead of raising on failure (D1).

        寫入後強制同步並等它完成；失敗時回傳提醒文字而不是拋錯（D1）。

        Returns:
            the warning (plain text; TG adds its own line breaks), or ``None`` if the sync succeeded
            提醒文字（純文字，TG 自己加換行）；同步成功時為 ``None``
        """
        return await sync_warning(self.anki_client)


async def sync_warning(anki_client: AnkiClient) -> str | None:
    """Force a sync with AnkiWeb and return a user-facing warning on failure.

    強制同步 AnkiWeb；失敗時回傳給使用者看的提醒。

    On failure the data is already written to desktop Anki, just not synced. Write points that do not yet go
    through the service layer (TG's ``_sync_with_warning``) share the same warning text.

    失敗時資料已寫進電腦版 Anki，只是沒同步。給還沒經過服務層的寫入點（TG 的 ``_sync_with_warning``）共用同一份
    提醒文字。

    Args:
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端

    Returns:
        the warning (a separate one for sync conflicts), or ``None`` if the sync succeeded
        提醒文字（同步衝突另有一種）；同步成功時為 ``None``
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
    note_type: str,
    field_names: tuple[str, ...],
    locked: frozenset[str],
    media: tuple[tuple[str, str], ...] = (),
) -> type[BaseModel]:
    """Build the standard create parameter model (D3): every field is an optional string, the first one required.

    標準建立的參數模型（D3）：每個欄位都是選填字串，第一個欄位必填。

    JSON fields are left out; media fields are listed and marked with ``x-ui`` (``audio``, ``image``) in the schema,
    so the web page shows an upload widget and submits the file name returned by ``POST /media`` (media_upload plan
    D3). Extra keys are not rejected here; the handler (``FIELD_NOT_EDITABLE``) and ``CardService``
    (``FIELD_MISMATCH``) report them. Field names may contain spaces and other characters, so they map through
    aliases. The result is cached per argument set.

    JSON 欄位不列；媒體欄位照列，並在 schema 標上 ``x-ui``（``audio``、``image``），網頁顯示上傳元件、
    送出 ``POST /media`` 回傳的檔名（media_upload 計畫書 D3）。多餘的鍵不在這裡擋，交給 handler
    （``FIELD_NOT_EDITABLE``）與 ``CardService``（``FIELD_MISMATCH``）回報。欄位名可能含空白等字元，
    所以以別名對應。結果依參數組合快取。

    Args:
        note_type: the note type (used to name the model)
            筆記類型（模型名稱用）
        field_names: Anki's field names, in order
            Anki 的欄位名，依順序
        locked: the JSON fields
            JSON 欄位
        media: ``(media field, kind)`` pairs; a tuple rather than a dict so it can be cached
            ``(媒體欄位, 種類)``；為了能快取，用 tuple 而不是 dict

    Returns:
        the Pydantic model
        Pydantic 模型
    """
    kinds = dict(media)
    definitions: dict[str, object] = {}
    for index, name in enumerate(field_names):
        if name in locked:
            continue
        if index == 0:
            # Anki 不允許第一個欄位為空，也以它判斷重複
            first = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
            definitions[f"field_{index}"] = (first, Field(alias=name, title=name))
        elif name in kinds:
            extra = {"x-ui": kinds[name]}
            definitions[f"field_{index}"] = (str, Field(default="", alias=name, title=name, json_schema_extra=extra))
        else:
            definitions[f"field_{index}"] = (str, Field(default="", alias=name, title=name))
    return create_model(  # type: ignore[call-overload]
        f"Create_{note_type}",
        __config__=ConfigDict(extra="allow"),
        **definitions,
    )


def json_field_version(raw: str) -> str:
    """Return the version of a JSON field: the first 16 hex digits of the raw string's SHA-256.

    回傳 JSON 欄位的版本：原始字串的 SHA-256 前 16 碼。

    See json_field_editor plan D3.

    見 json_field_editor 計畫 D3。

    Args:
        raw: the field's raw string
            欄位的原始字串

    Returns:
        the version
        版本
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def json_field_state(raw: str) -> dict[str, object]:
    """Return one JSON field as returned by a read: the parsed list, the version, and the error.

    回傳讀取時的一個 JSON 欄位：解析好的清單、版本與錯誤。

    Args:
        raw: the field's raw string
            欄位的原始字串

    Returns:
        ``{"items", "version", "error"}``; when the field is corrupted, ``items`` is empty and ``error`` holds
        the message (it cannot be edited); otherwise ``error`` is ``None``
        ``{"items", "version", "error"}``；壞掉時清單為空、``error`` 為訊息（不能編輯）；否則 ``error`` 為 ``None``
    """
    try:
        items: list[object] = AnkiJsonFieldManager.parse_field_string(raw)
        error = None
    except AnkiFieldCorruptedError as exc:
        items, error = [], exc.message
    return {"items": items, "version": json_field_version(raw), "error": error}


def json_field_specs(handler: BaseHandler) -> dict[str, object]:
    """Return the JSON field formats for ``GET /handlers``: the title and the JSON Schema of each item.

    回傳 ``GET /handlers`` 的 JSON 欄位格式：標題與每一筆的 JSON Schema。

    Args:
        handler: the handler
            handler

    Returns:
        ``{field name: {"title", "item_schema"}}``
        ``{欄位名稱: {"title", "item_schema"}}``
    """
    return {
        name: {"title": spec.title, "item_schema": spec.item.model_json_schema()}
        for name, spec in handler.JSON_FIELD_SPECS.items()
    }


def _suggested_tags(raw: object, existing: list[str]) -> list[dict[str, object]]:
    """Clean up the LLM's suggested tags and mark which already exist.

    整理 LLM 建議的標籤。

    Invalid ones (containing whitespace, system tags, and so on) are dropped and duplicates removed; a tag
    differing from an existing one only in case takes the existing spelling.

    不合格（含空白、系統標籤等）的丟掉、去重；與既有標籤只差大小寫時用既有的寫法。
    """
    known = {tag.casefold(): tag for tag in existing}
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        try:
            cleaned = normalize_tags([str(item)])
        except TagInvalidError:
            continue
        if not cleaned or cleaned[0].casefold() in seen:
            continue
        tag = known.get(cleaned[0].casefold(), cleaned[0])
        seen.add(tag.casefold())
        result.append({"tag": tag, "existing": tag.casefold() in known})
    return result


def _create_media_fields(handler: BaseHandler) -> list[str]:
    """Return the create parameters that hold media.

    回傳建卡參數裡的媒體欄位。

    For standard creation these are ``MEDIA_FIELDS``; for handlers overriding ``CREATE_PARAMS`` they are the
    parameters whose schema is marked ``x-ui: audio`` / ``image`` (the handler checks them and builds the fields
    itself; this only commits the files to Anki).

    標準建立就是 ``MEDIA_FIELDS``；覆寫 ``CREATE_PARAMS`` 的是 schema 標了
    ``x-ui: audio``／``image`` 的參數（handler 自己檢查、組欄位，這裡只負責把檔案存進 Anki）。
    """
    if handler.CREATE_PARAMS is None:
        return list(handler.MEDIA_FIELDS)
    properties = handler.CREATE_PARAMS.model_json_schema().get("properties", {})
    return [name for name, prop in properties.items() if isinstance(prop, dict) and prop.get("x-ui") in MEDIA_KINDS]


def _draft_schema(handler: BaseHandler) -> dict[str, object] | None:
    """Return the draft form of the web preview flow (the JSON Schema of ``DRAFT_PARAMS``).

    回傳網頁預覽流程的草稿表單（``DRAFT_PARAMS`` 的 JSON Schema）。

    Returns ``None`` when the handler has no such flow.

    沒有這個流程時為 ``None``。
    """
    create = handler.CAPABILITIES.create
    if handler.DRAFT_PARAMS is None or not create.supported or create.flow != "preview" or "web" not in create.channels:
        return None
    return handler.DRAFT_PARAMS.model_json_schema()


def _require(operation: Operation) -> None:
    """Raise when the operation is not offered, with the capability's declared reason as the message.

    操作不開放時拋錯，訊息為能力宣告的原因。
    """
    if not operation.supported:
        raise HandlerOperationUnsupportedError(operation.reason or "")


def _require_owned(handler: BaseHandler, note_type: str) -> None:
    """Raise unless the note type belongs to the handler (D1).

    筆記的類型要歸這個 handler（D1），否則拋錯。
    """
    if note_type not in handler.supported_models:
        raise NoteNotOwnedByHandlerError(f"{note_type} 的筆記不歸「{handler.title}」負責")


def _permission(operation: Operation) -> dict[str, object]:
    """Return a permission entry for the generic read; a denied entry carries the capability's reason.

    回傳通用讀取的權限項目；不允許時附上能力宣告的原因。
    """
    return {"allowed": operation.supported, "reason": None if operation.supported else operation.reason}
