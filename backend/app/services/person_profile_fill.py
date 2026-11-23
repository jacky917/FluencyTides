"""The person card "AI 整理" on the web: a description becomes proposals the user edits and ticks before writing.

網頁人脈卡的「AI 整理」：一段描述整理成建議，使用者編輯、勾選後才寫入。

Plan ``docs/wip/person_llm_fill_FEAT_2026-11-23.md``. It reuses Telegram's "record in one paragraph"
(``person_profile_ingest``: the prompt, the LLM output, ``proposals_from`` and ``apply_proposals``) and the web
draft slot (``CreateSession``), and adds only what the web needs:

- Two modes (D1): ``update`` adds to an existing card; ``create`` first creates a card with the display name, then
  writes the other proposals to it exactly like ``update``.
- Its own route key ``person_profile.fill`` (D2), so the model is chosen separately from Telegram's.
- The draft input carries ``purpose: "fill"``, so retrying and writing pick this path, while the slot, the lock,
  saving the editing state and discarding stay shared with the preview flow (D5).
- Edited proposals are checked again here before writing (D7); a proposal that fails is reported with its reason
  and the rest are still written.

計畫書 ``docs/wip/person_llm_fill_FEAT_2026-11-23.md``。沿用 TG 的「用一段話記錄」（``person_profile_ingest``：
提示詞、LLM 輸出、``proposals_from`` 與 ``apply_proposals``）與網頁的草稿槽位（``CreateSession``），只補網頁需要的：

- 兩種模式（D1）：``update`` 補現有的卡；``create`` 先用稱呼建卡，再把其他建議當成 ``update`` 寫進新卡。
- 自己的用途鍵 ``person_profile.fill``（D2），模型和 TG 分開選。
- 草稿輸入帶 ``purpose: "fill"``，重試與寫入依它走這條路；槽位、鎖、存編輯狀態與廢棄和預覽流程共用（D5）。
- 編輯過的建議寫入前在這裡再檢查一次（D7）；不合格的項目回報原因，其他的照常寫入。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.core import clock
from app.core.exceptions import AnkiServiceError, NoteNotFoundError
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.llm.routing import route
from app.schemas.person_profile import (
    HISTORY_PERIOD_KEYS,
    PERSON_HISTORY_FIELDS,
    PERSON_NOTE_TYPE,
    FamilyMember,
    Followup,
    Goal,
    Interaction,
    PersonCreateParams,
    history_from_text,
)
from app.services import person_profile_ingest as ingest_service

if TYPE_CHECKING:
    from app.schemas.person_fill import ProposalIn
    from app.services.card_service import CardService
    from app.services.note_service import NoteService

logger = logging.getLogger(__name__)

#: The route of the web dialog: its own model setting, separate from Telegram's; text only, so no ``stt``.
#: 網頁對話框的用途鍵：和 TG 分開設定模型；只有文字輸入，所以不宣告 ``stt``。
FILL_ROUTE = route("person_profile.fill", "人脈卡：網頁 AI 整理", capabilities={"llm"})

#: The draft input key and value that mark an "AI 整理" draft.
#: 標記「AI 整理」草稿的輸入鍵與值。
PURPOSE_KEY = "purpose"
FILL_PURPOSE = "fill"


def is_fill_draft(draft_input: Mapping[str, object] | None) -> bool:
    """Return whether a draft input belongs to "AI 整理" (rather than the preview create flow).

    回傳草稿輸入是不是「AI 整理」的（而不是預覽建卡流程）。

    Args:
        draft_input: the draft's ``input``; ``None`` for a form-based creation hold
            草稿的 ``input``；填表建卡的佔用為 ``None``

    Returns:
        whether it is an "AI 整理" draft
        是不是「AI 整理」的草稿
    """
    return bool(draft_input) and draft_input.get(PURPOSE_KEY) == FILL_PURPOSE  # type: ignore[union-attr]


def draft_input(text: str, note_id: int | None, deck: str | None) -> dict[str, object]:
    """Return the draft input stored in the slot.

    回傳存進槽位的草稿輸入。

    Args:
        text: the description
            描述
        note_id: the card to add to; ``None`` for a new card
            要補的卡；建新卡為 ``None``
        deck: the deck of a new card (ignored when adding to a card)
            建新卡的牌組（補舊卡時不用）

    Returns:
        ``{"purpose": "fill", "text", "note_id", "deck"}``
        ``{"purpose": "fill", "text", "note_id", "deck"}``
    """
    return {PURPOSE_KEY: FILL_PURPOSE, "text": text, "note_id": note_id, "deck": None if note_id else deck}


async def generate(card_service: CardService, parameters: Mapping[str, object]) -> dict[str, object]:
    """Organize the description into proposals, in the background (nothing is written to Anki).

    在背景把描述整理成建議（不寫入 Anki）。

    For ``update`` the card's current fields and lists go into the prompt, so known things are not suggested
    again; for ``create`` there is nothing known. The result has the shape the draft slot expects
    (``{"params", "suggested_tags"}``); the proposals live in ``params``.

    ``update`` 會把這張卡目前的欄位與清單放進提示，已經記過的不會再建議；``create`` 沒有已知資料。結果是草稿槽位要的
    形狀（``{"params", "suggested_tags"}``），建議放在 ``params`` 裡。

    Args:
        card_service: the card service (AnkiConnect only; this runs after the request has ended)
            卡片服務（只用 AnkiConnect；在請求結束後執行）
        parameters: the draft input (see :func:`draft_input`)
            草稿輸入（見 ``draft_input``）

    Returns:
        ``{"params": {"mode", "note_id", "deck", "proposals", "known", "labels", "saved", "errors"},
        "suggested_tags": []}``; ``known`` is what the card already has (``person_profile_ingest.known_summary``),
        ``None`` for a new card; ``labels`` names the keys of list items (``person_profile_ingest.item_labels``)
        ``{"params": {"mode", "note_id", "deck", "proposals", "known", "labels", "saved", "errors"},
        "suggested_tags": []}``；``known`` 是卡片上已有的資料（``person_profile_ingest.known_summary``），建新卡時為
        ``None``；``labels`` 是清單項目各鍵的名稱（``person_profile_ingest.item_labels``）

    Raises:
        AnkiServiceError: if the card cannot be read because AnkiConnect fails
            若 AnkiConnect 失敗、讀不到卡片
        LLMGenerationError: if the LLM response does not match the format
            若 LLM 回傳的格式不符
        LLMServiceError: if the LLM call fails
            若 LLM 呼叫失敗
        NoteNotFoundError: if the card to add to is gone or is not a person card
            若要補的卡不在或不是人脈卡
    """
    note_id = parameters.get("note_id")
    mode: ingest_service.IngestMode = "update" if note_id else "create"
    fields: dict[str, str] = {}
    lists: dict[str, list[object]] = {}
    if note_id:
        try:
            loaded = await ingest_service.load_person(card_service, int(note_id))  # type: ignore[arg-type]
        except AnkiConnectError as e:
            raise AnkiServiceError(f"讀不到這張人脈卡：{e}") from e
        if loaded is None:
            raise NoteNotFoundError(ingest_service.PERSON_NOT_FOUND)
        fields, lists = loaded[0], loaded[1]
    today = clock.today()
    # 網頁補舊卡時描述的常是對他的了解、不是一次聊天：互動可以沒有（TG 仍一定要一筆）
    result = await ingest_service.ingest(
        fields, lists, str(parameters["text"]), today, mode=mode, llm_route=FILL_ROUTE, optional_interaction=True
    )
    proposals = [{"kind": p.kind, "data": p.data} for p in ingest_service.proposals_from(result, fields, lists, today)]
    return {
        "params": {
            "mode": mode,
            "note_id": note_id,
            "deck": parameters.get("deck"),
            "proposals": proposals,
            # 卡片上已經有的資料（和提示裡的相同），對話框在每項建議旁邊顯示；建新卡時沒有
            "known": ingest_service.known_summary(fields, lists) if note_id else None,
            "labels": ingest_service.item_labels(),
            "saved": [],
            "errors": {},
        },
        "suggested_tags": [],
    }


def normalize(kind: str, data: Mapping[str, object]) -> dict[str, object]:
    """Check an edited proposal and return the data to write, in the same shape as ``proposals_from`` gives.

    檢查一項編輯過的建議，回傳要寫入的內容（形狀同 ``proposals_from`` 給的）。

    The item models are the ones the handler uses (``Interaction``, ``Followup``, ``Goal``, ``FamilyMember``,
    ``PersonCreateParams``), so an item that passes here is the same as one the LLM suggested; the handler still
    validates when writing.
    Empty values are dropped, as in ``proposals_from``.

    項目模型就是 handler 用的（``Interaction``、``Followup``、``Goal``、``FamilyMember``、``PersonCreateParams``），
    通過這裡的項目和 LLM 建議的一樣；handler 寫入時照樣驗證。空值和 ``proposals_from`` 一樣拿掉。

    Args:
        kind: the proposal kind
            建議的種類
        data: the edited data
            編輯後的內容

    Returns:
        the data to write
        要寫入的內容

    Raises:
        ValueError: if the data is not valid for its kind (``pydantic.ValidationError`` is a subclass); the message
            says why
            若內容不符這種建議的格式（``pydantic.ValidationError`` 是它的子類別）；訊息說明原因
    """
    values = dict(data)
    if kind == "display_name":
        value = str(values.get("value") or "").strip()
        if not value:
            raise ValueError("稱呼不能是空的")
        PersonCreateParams.model_validate({"Display_Name": value})
        return {"value": value}
    if kind == "interaction":
        item = Interaction.model_validate(values)
        return {k: v for k, v in item.model_dump().items() if v not in ("", None)}
    if kind == "family":
        member = FamilyMember.model_validate({k: v for k, v in values.items() if k != "reason"})
        return {
            **{k: v for k, v in member.model_dump().items() if k in ("relation", "name", "note") and v},
            "reason": str(values.get("reason") or ""),
        }
    if kind == "followup":
        followup = Followup.model_validate(values)
        return {k: v for k, v in followup.model_dump().items() if k in ("item", "type", "due") and v not in ("", None)}
    if kind == "goal":
        goal = Goal.model_validate({k: v for k, v in values.items() if k != "reason"})
        return {
            **{k: v for k, v in goal.model_dump().items() if k in ("kind", "item", "target") and v},
            "reason": str(values.get("reason") or ""),
        }
    if kind == "field":
        name, value, mode = str(values.get("field")), str(values.get("value") or "").strip(), values.get("mode")
        if name not in ingest_service.UPDATABLE_FIELDS:
            raise ValueError(f"「{name}」不能由 AI 整理寫入")
        if not value:
            raise ValueError("內容不能是空的")
        if mode not in ("append", "replace"):
            raise ValueError("要選取代或接在後面")
        if mode == "replace":
            # 存整理過的寫法（例如生日 3/15 → --03-15）；格式不對時拋出
            value = str(getattr(PersonCreateParams.model_validate({"Display_Name": "x", name: value}), name))
        return {"field": name, "value": value, "mode": mode, "reason": str(values.get("reason") or "")}
    if kind == "history_add":
        name, item = str(values.get("field")), values.get("item")
        if name not in PERSON_HISTORY_FIELDS or not isinstance(item, Mapping):
            raise ValueError("新增的那一筆格式不對")
        # 可切的欄位（興趣等）一次寫了好幾項又填了時間：寫入時會切成多筆，同一個時間套到每一筆會記錯
        # （TG 與整理都刻意不這樣做），請使用者分開
        if (
            PERSON_HISTORY_FIELDS[name].split
            and any(str(item.get(key) or "").strip() for key in HISTORY_PERIOD_KEYS.get(name, ()))
            and len(history_from_text(name, str(item.get("value") or ""), "")) > 1
        ):
            raise ValueError("一次寫了好幾項時不能填時間：拿掉時間，或一項一項新增")
        # 內容與事件時間由 handler 的 add_history 驗證（不合格時回報在這一項）
        return {**values, "field": name, "item": dict(item)}
    if kind == "history_end":
        name, index, fingerprint = values.get("field"), values.get("index"), values.get("fingerprint")
        if name not in PERSON_HISTORY_FIELDS or not isinstance(index, int) or not isinstance(fingerprint, str):
            raise ValueError("要劃掉的那一筆格式不對")
        return dict(values)
    if kind == "edit_field":
        # 修改卡片原本的文字欄位：整段取代；清空也可以（空字串）
        name = str(values.get("field"))
        if name not in ingest_service.UPDATABLE_FIELDS:
            raise ValueError(f"「{name}」不能在這裡修改")
        value = str(values.get("value") or "").strip()
        if value:
            value = str(getattr(PersonCreateParams.model_validate({"Display_Name": "x", name: value}), name))
        return {"field": name, "value": value}
    if kind == "edit_item":
        # 修改卡片原本的一筆：內容由 handler 的 edit_item 驗證（序號、指紋與原值對不上時回報在這一項）
        name, index, fingerprint = values.get("field"), values.get("index"), values.get("fingerprint")
        new_values, expected = values.get("values"), values.get("expected")
        if (
            name not in ingest_service.EDITABLE_LISTS
            or not isinstance(index, int)
            or not isinstance(fingerprint, str)
            or not isinstance(new_values, Mapping)
        ):
            raise ValueError("要修改的那一筆格式不對")
        return {
            "field": name,
            "index": index,
            "fingerprint": fingerprint,
            "values": dict(new_values),
            "expected": dict(expected) if isinstance(expected, Mapping) else None,
        }
    raise ValueError(f"不認得的建議種類 {kind!r}")


def _reason(error: ValueError) -> str:
    """Return a short reason for a rejected proposal.

    回傳建議被拒絕的簡短原因。
    """
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        return f"{where}：{first.get('msg', '')}" if where else str(first.get("msg", ""))
    return str(error)


@dataclass
class ApplyProgress:
    """How far writing an "AI 整理" draft got, kept even when writing stops midway.

    「AI 整理」草稿寫到哪裡；寫到一半中斷時也留著。

    Attributes:
        note_id: the card written to; set as soon as a new card is created
            寫入的卡；建新卡時一建好就填上
        saved: the indexes of the proposals written
            已寫入的建議序號
        errors: the proposals not written, with the reasons
            沒寫入的建議與原因
        warning: the sync warning of the last write
            最後一次寫入的同步提醒
    """

    note_id: int | None
    saved: set[int] = field(default_factory=set)
    errors: dict[int, str] = field(default_factory=dict)
    warning: str | None = None

    @classmethod
    def from_params(cls, params: Mapping[str, object]) -> ApplyProgress:
        """Resume from a draft's ``result["params"]``.

        從草稿的 ``result["params"]`` 接續。

        Args:
            params: the draft's ``result["params"]``
                草稿的 ``result["params"]``

        Returns:
            the progress so far
            目前的進度
        """
        note_id = params.get("note_id")
        saved = params.get("saved") or []
        return cls(
            note_id=int(note_id) if note_id else None,  # type: ignore[arg-type]
            saved={int(i) for i in saved},  # type: ignore[union-attr]
        )

    def to_params(self) -> dict[str, object]:
        """Return the progress to merge into the draft's ``result["params"]``.

        回傳要併進草稿 ``result["params"]`` 的進度。

        Returns:
            ``{"note_id", "saved", "errors"}`` (error keys are strings, as in JSON)
            ``{"note_id", "saved", "errors"}``（錯誤的鍵是字串，同 JSON）
        """
        return {
            "note_id": self.note_id,
            "saved": sorted(self.saved),
            "errors": {str(i): message for i, message in sorted(self.errors.items())},
        }


async def apply(
    note_service: NoteService,
    card_service: CardService,
    params: Mapping[str, object],
    proposals: Sequence[ProposalIn],
    selected: Sequence[int],
    progress: ApplyProgress,
) -> bool:
    """Write the ticked proposals of an "AI 整理" draft.

    寫入「AI 整理」草稿裡勾選的建議。

    Each ticked proposal is checked with :func:`normalize`; one that fails is reported in ``progress.errors`` and
    left out. For a new card (no ``note_id`` yet) the card is first created with the display name in the draft's
    deck, and ``progress.note_id`` is set right away, so a later retry adds to that card instead of creating
    another. The rest go through ``person_profile_ingest.apply_proposals``, collecting rejections instead of
    stopping. ``progress`` is updated in place throughout, so it is accurate even when an Anki failure stops the
    writing.

    每項勾選的建議以 ``normalize`` 檢查，不合格的記在 ``progress.errors`` 並略過。建新卡時（還沒有 ``note_id``）先以
    稱呼在草稿的牌組建卡，並立刻填上 ``progress.note_id``：之後重試會補這張卡，不會再建一張。其他的經
    ``person_profile_ingest.apply_proposals`` 寫入，被拒絕的收集起來而不停止。``progress`` 全程就地更新，Anki
    失敗而中斷時也是準確的。

    Args:
        note_service: the note service
            筆記服務
        card_service: the card service
            卡片服務
        params: the draft's ``result["params"]`` (mode, note ID, deck)
            草稿的 ``result["params"]``（模式、Note ID、牌組）
        proposals: every proposal as edited on the web, in the draft's order
            網頁編輯後的全部建議，順序同草稿
        selected: the indexes of the ticked proposals
            勾選的建議序號
        progress: the progress so far, updated in place
            目前的進度，會就地更新

    Returns:
        whether every ticked proposal has been written
        勾選的建議是否都寫入了

    Raises:
        AnkiConnectError: if AnkiConnect fails (``progress`` holds what was written before)
            若 AnkiConnect 失敗（``progress`` 是在那之前寫入的）
        FluencyTidesError: if creating the new card fails
            若建新卡失敗
    """
    chosen = sorted({i for i in selected if 0 <= i < len(proposals)})
    ready: list[dict[str, object]] = [{"kind": p.kind, "data": dict(p.data)} for p in proposals]
    progress.errors = {}
    for i in chosen:
        if i in progress.saved:
            continue
        try:
            ready[i]["data"] = normalize(proposals[i].kind, proposals[i].data)
        except ValueError as e:
            progress.errors[i] = _reason(e)
    writable = [i for i in chosen if i not in progress.errors]

    created_now = False
    if progress.note_id is None:
        names = [i for i, p in enumerate(proposals) if p.kind == "display_name"]
        if not names or names[0] not in writable:
            if names and names[0] not in progress.errors:
                progress.errors[names[0]] = "建新卡要有稱呼"
            # 沒有稱呼就不能建卡：其他項目也還不能寫
            return False
        name_index = names[0]
        name_data = ready[name_index]["data"]
        assert isinstance(name_data, Mapping)
        created = await note_service.create(
            ingest_service.HANDLER_NAME,
            str(params.get("deck") or ""),
            PERSON_NOTE_TYPE,
            {"Display_Name": name_data["value"]},
            # 建好的卡和其他建議一起，最後才同步一次（apply_proposals 的 force_sync）
            sync=False,
        )
        note_ids = created["note_ids"]
        assert isinstance(note_ids, list) and note_ids
        progress.note_id = int(note_ids[0])
        created_now = True
        progress.saved.add(name_index)
        warning = created.get("sync_warning")
        progress.warning = str(warning) if warning else None
        logger.info("AI 整理：建立人脈卡 %s（%s）", progress.note_id, name_data["value"])

    warning = await ingest_service.apply_proposals(
        note_service,
        card_service,
        progress.note_id,
        ready,
        writable,
        progress.saved,
        progress.errors,
        force_sync=created_now,
    )
    progress.warning = warning or progress.warning
    return all(i in progress.saved for i in chosen)
