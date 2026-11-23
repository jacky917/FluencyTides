"""Person card "record in one paragraph": the LLM turns a text into suggestions the user picks before writing.

人脈卡「用一段話記錄」：一段文字經 LLM 整理成建議，使用者勾選後才寫入。

A text (or text transcribed from speech) is structured by the LLM into suggestions for one interaction, follow-ups,
and profile data; nothing is written until the user picks them (person card plan §3.13 P5).

一段文字（或語音轉成的文字）經 LLM 整理成一筆互動、待追蹤與人物資料的
建議；使用者勾選後才寫入（人脈卡計畫書 §3.13 P5）。

- The LLM and speech-to-text use the capability routing route key ``person_profile`` (configurable through
  ``/setconfig``).
- The LLM's suggestions get a format check here first (invalid ones are dropped), and the handler validates them
  once more when writing.
- A text field suggestion either replaces the field or is appended to it; a suggestion for a changing field
  (interests, position, and so on) either "adds an entry" or "strikes out an entry", and the dates are always
  filled in by the system (registration date plan §4.6).

- LLM 與語音轉文字走能力路由的用途鍵 ``person_profile``（``/setconfig`` 可設定）。
- LLM 的建議在這裡先過一次格式檢查（不合格的丟掉），寫入時 handler 還會再驗證一次。
- 文字欄位的建議是取代或接在後面；會變的欄位（興趣、職位等）的建議是「新增一筆」或「劃掉某筆」，
  日期都由系統填（登錄日計畫書 §4.6）。
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from app.core.dependencies import get_template_engine
from app.core.exceptions import AnkiFieldCorruptedError, FluencyTidesError, LLMGenerationError, NoteNotFoundError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.llm.routing import Route, route
from app.schemas.llm.person_profile import (
    IngestListUpdate,
    LLMPersonCreateResult,
    LLMPersonFillResult,
    LLMPersonIngestResult,
)
from app.schemas.person_profile import (
    GOAL_STATUSES,
    HISTORY_PERIOD_KEYS,
    ITEM_EDIT_KEYS,
    NOTE_FIELDS,
    PERSON_HISTORY_FIELDS,
    PERSON_JSON_ITEMS,
    PERSON_NOTE_TYPE,
    RETIRED_FIELDS,
    FamilyMember,
    Followup,
    Goal,
    Interaction,
    PersonCreateParams,
    append_field_html,
    entry_detail,
    entry_key,
    entry_text,
    history_from_text,
    is_active,
    item_fingerprint,
    merged_note,
    normalize_period,
    period_order_error,
)
from app.services.task_handlers.base import note_field_values

if TYPE_CHECKING:
    from app.services.card_service import CardService
    from app.services.note_service import NoteService

logger = logging.getLogger(__name__)

INGEST_ROUTE = route("person_profile", "人脈卡：TG 用一段話記錄（文字或語音）", capabilities={"llm", "stt"})

#: ``update`` adds to an existing card (as in Telegram); ``create`` makes a new card from a description.
#: ``update`` 補現有的卡（同 TG）；``create`` 從一段描述建新卡。
IngestMode = Literal["update", "create"]

WEEKDAYS = "一二三四五六日"

#: The text fields the LLM may suggest replacing or appending to. The display name and contact dates are maintained
#: by the user or the system; the introducer is another note's ID, which the LLM cannot know; changing fields go
#: through the add / strike-out of :data:`PERSON_HISTORY_FIELDS`.
#: LLM 可以建議取代或接在後面的文字欄位。稱呼與聯絡日由使用者或系統維護；介紹人是另一筆筆記的 ID，
#: LLM 無從得知；會變的欄位走 ``PERSON_HISTORY_FIELDS`` 的新增、劃掉。
UPDATABLE_FIELDS: tuple[str, ...] = tuple(
    name
    for name in PersonCreateParams.model_fields
    if name not in ("Display_Name", "Last_Contact", "Contact_Cadence", "Introducer_Note_ID")
    and name not in PERSON_HISTORY_FIELDS
    and name not in RETIRED_FIELDS
)

_BREAK = re.compile(r"<\s*(br|/div|/p|/li)\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Proposal:
    """One suggestion.

    一項建議。

    Attributes:
        kind: ``display_name`` (only when creating a card), ``interaction``, ``followup``, ``goal``, ``family``,
            ``field``, ``history_add``, or ``history_end``
            ``display_name``（只有建新卡）、``interaction``、``followup``、``goal``、``family``、``field``、
            ``history_add`` 或 ``history_end``
        data: the display name is ``{"value"}``; the interaction, follow-up, goal or family item (validated; a goal
            and a family member also carry ``reason``); a text field suggestion is ``{"field", "value", "mode", "reason"}``; adding an
            entry is ``{"field", "item", "text", "reason"}`` (``item`` goes to ``add_history``); striking out an
            entry is ``{"field", "index", "fingerprint", "text", "reason"}``
            稱呼為 ``{"value"}``；互動、待追蹤、目標或家人的項目（已驗證；目標與家人另附 ``reason``）；文字欄位的建議為
            ``{"field", "value", "mode", "reason"}``；新增一筆為 ``{"field", "item", "text", "reason"}``
            （``item`` 交給 ``add_history``）；劃掉一筆為 ``{"field", "index", "fingerprint", "text", "reason"}``
    """

    kind: str
    data: dict[str, object]


def plain_text(value: str, limit: int = 300) -> str:
    """Convert a field's HTML to one line of plain text (for the LLM).

    欄位 HTML 轉成一行純文字（給 LLM 看）。

    Line breaks become 「；」, tags are removed, entities are unescaped, and whitespace is collapsed.

    換行變成「；」，去掉標籤、還原實體字元、合併空白。

    Args:
        value: the field HTML; may be empty
            欄位 HTML；可為空
        limit: the maximum length; longer text is cut and ends with 「…」
            最長長度；超過時截斷並加「…」

    Returns:
        the plain text
        純文字
    """
    text = html.unescape(_TAG.sub("", _BREAK.sub("；", value or "")))
    text = re.sub(r"\s+", " ", text).strip(" ；")
    return text if len(text) <= limit else text[:limit] + "…"


def _multiline(name: str) -> bool:
    info = PersonCreateParams.model_fields.get(name)
    extra = info.json_schema_extra if info else None
    return isinstance(extra, dict) and extra.get("x-ui") == "textarea"


def _history_lines(name: str, items: list[object]) -> tuple[list[str], list[str]]:
    """Return what the LLM sees of a changing field: the active entries and the past ones.

    回傳會變的欄位給 LLM 看的：有效的與過去的項目。

    Each active entry is numbered (``#index+1``, used when striking it out); past entries are struck-out ones or
    positions with an end date. Each entry carries its event time and description (:func:`entry_detail`; work
    history and education have descriptions); active ones also carry 「登錄 日期」, written differently from the
    event time (「2015 起」) so the LLM can tell which is the time of the event and which is just the day it was
    recorded.

    有效的每筆附編號（``#序號+1``，劃掉時用），以及過去的（劃掉的，或有離職年月的職位）。每筆附事件時間與說明
    （``entry_detail``，工作經歷與學歷有說明）；有效的另外附「登錄 日期」：寫法和事件時間
    （「2015 起」）不同，LLM 才分得出哪個是那件事的時間、哪個只是記下的日子。
    """
    current, past = [], []
    for index, item in enumerate(items):
        text = plain_text(entry_detail(name, item), 200)
        # 不是物件的項目是格式壞掉的：不列（也不能被劃掉）
        if not text or not isinstance(item, dict):
            continue
        # 以前的工作有離職年月，不一定劃掉過；is_active 兩個都看
        if is_active(item):
            added = str(item.get("added") or "")
            current.append(f"#{index + 1} {text}" + (f"（登錄 {added}）" if added else ""))
        else:
            past.append(text)
    return current, past


def build_prompt(
    fields: dict[str, str],
    lists: dict[str, list[object]],
    text: str,
    today: date,
    mode: IngestMode = "update",
    *,
    optional_interaction: bool = False,
) -> str:
    """Render the prompt: today, what is already known about this person, the user's text, and updatable fields.

    組出提示：今天、這個人目前已知的資料、使用者記下的話、可更新的欄位。

    The known data is included to avoid repeated suggestions. In ``create`` mode the prompt describes a new
    person instead (plan D3): it asks for a display name, the interaction becomes optional, there is no known data,
    and nothing can be struck out.

    已知資料是為了避免重複建議。``create`` 模式改為描述一個新的人（計畫書 D3）：要稱呼、互動可以沒有、沒有已知
    資料、不能劃掉。

    Args:
        fields: the person's current fields (empty in ``create`` mode)
            這個人目前的欄位（``create`` 模式為空）
        lists: the parsed lists (empty in ``create`` mode)
            解析好的清單（``create`` 模式為空）
        text: the text the user recorded
            使用者記下的話
        today: today's date
            今天
        mode: ``update`` (add to an existing card, as in Telegram) or ``create`` (a new card)
            ``update``（補現有的卡，同 TG）或 ``create``（建新卡）
        optional_interaction: in ``update`` mode, whether the interaction may be left out (the web: the text is
            often what the user learned about the person, not one conversation); Telegram always asks for one
            ``update`` 模式下互動可不可以沒有（網頁：描述的常是對他的了解，而不是一次聊天）；TG 一律要一筆

    Returns:
        the prompt
        提示

    Raises:
        TemplateEngineError: if the prompt template is broken
            若提示詞模板有誤
    """
    known = [
        (name, PersonCreateParams.model_fields[name].title or name, plain_text(fields.get(name, "")))
        for name in PersonCreateParams.model_fields
        if name not in PERSON_HISTORY_FIELDS and name not in RETIRED_FIELDS and plain_text(fields.get(name, ""))
    ]
    known_lists = []
    for name, spec in PERSON_HISTORY_FIELDS.items():
        current, past = _history_lines(name, lists.get(name, []))
        if current or past:
            known_lists.append((name, spec.title, current, past))
    updatable = [
        (name, PersonCreateParams.model_fields[name].title or name, PersonCreateParams.model_fields[name].description)
        for name in UPDATABLE_FIELDS
    ]
    list_fields = [(name, spec.title, spec.single, spec.split) for name, spec in PERSON_HISTORY_FIELDS.items()]
    return get_template_engine().render(
        "prompts/anki/Person_Profile_Ingest.j2",
        mode=mode,
        display_name=plain_text(fields.get("Display_Name", "")),
        today=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        known_fields=known,
        known_lists=known_lists,
        open_followups=_open_followups(lists),
        known_goals=_known_goals(lists),
        known_family=_known_family(lists),
        recent_interactions=_recent_interactions(lists),
        optional_interaction=optional_interaction,
        updatable_fields=updatable,
        list_fields=list_fields,
        text=text,
    )


def _open_followups(lists: Mapping[str, list[object]]) -> list[str]:
    """Return the follow-ups not done yet.

    回傳還沒完成的待追蹤。
    """
    return [
        str(f.get("item", ""))
        for f in lists.get("Followups_JSON", [])
        if isinstance(f, dict) and f.get("status", "open") != "done"
    ]


def _known_goals(lists: Mapping[str, list[object]]) -> list[str]:
    """Return the goals, each with its target time and (when not open) its status.

    回傳目標，各附目標時間與（不是進行中時的）狀態。
    """
    return [
        f"{g.get('kind') or '目標'}：{g.get('item', '')}"
        + (f"（目標 {g['target']}）" if g.get("target") else "")
        + ("" if str(g.get("status") or "open") == "open" else f"（{GOAL_STATUSES.get(str(g['status']), '')}）")
        for g in lists.get("Goals_JSON", [])
        if isinstance(g, dict)
    ]


def _known_family(lists: Mapping[str, list[object]]) -> list[str]:
    """Return the family members, one line each.

    回傳家人，一人一行。
    """
    return [_family_text(f) for f in lists.get("Family_JSON", []) if isinstance(f, dict)]


def _recent_interactions(lists: Mapping[str, list[object]], limit: int = 3) -> list[str]:
    """Return the latest interactions, newest first, as 「date：topics」.

    回傳最近的互動（新到舊），每筆「日期：聊了什麼」。
    """
    recent = sorted(
        (i for i in lists.get("Interactions_JSON", []) if isinstance(i, dict)),
        key=lambda i: str(i.get("date", "")),
        reverse=True,
    )[:limit]
    return [f"{i.get('date', '')}：{i.get('topics', '')}" for i in recent]


def known_summary(fields: Mapping[str, str], lists: Mapping[str, list[object]]) -> dict[str, object]:
    """Return what is already on the card, as the web "AI 整理" dialog shows it next to each proposal.

    回傳卡片上已經有的資料，網頁「AI 整理」對話框在每項建議旁邊顯示。

    It is the same data the prompt lists as known (:func:`build_prompt`), so the user sees what the LLM was told
    and can tell a real addition from a repeat. Entries keep the prompt's wording; struck-out entries of a changing
    field are listed separately.

    和提示列出的已知資料相同（``build_prompt``），使用者看得到 LLM 知道了什麼，分得出是新的還是重複。項目沿用提示的
    寫法；會變的欄位裡劃掉的另外列。

    Args:
        fields: the person's current fields
            這個人目前的欄位
        lists: the parsed lists
            解析好的清單

    Returns:
        ``{"fields": {name: text}, "lists": {name: {"current", "past"}}, "followups", "goals", "family",
        "interactions", "items": {name: [entry]}}``; empty parts are left out of ``fields``, ``lists`` and
        ``items``. ``items`` are the original entries the dialog lets the user edit (see :func:`known_items`)
        ``{"fields": {欄位: 文字}, "lists": {欄位: {"current", "past"}}, "followups", "goals", "family",
        "interactions", "items": {欄位: [項目]}}``；``fields``、``lists``、``items`` 裡沒有內容的不列。``items`` 是
        對話框讓使用者修改的原本項目（見 ``known_items``）
    """
    known_lists: dict[str, dict[str, list[str]]] = {}
    for name in PERSON_HISTORY_FIELDS:
        current, past = _history_lines(name, lists.get(name, []))
        if current or past:
            # 去掉給 LLM 看的編號與登錄日（網頁上用不到）
            known_lists[name] = {"current": [_without_marks(line) for line in current], "past": past}
    return {
        "fields": {name: plain_text(fields.get(name, ""), 1000) for name in UPDATABLE_FIELDS if plain_text(fields.get(name, ""))},
        "lists": known_lists,
        "followups": _open_followups(lists),
        "goals": _known_goals(lists),
        "family": _known_family(lists),
        "interactions": _recent_interactions(lists),
        "items": known_items(lists),
    }


#: The lists whose original entries the dialog shows and lets the user edit (with ``edit_item``).
#: 對話框列出、讓使用者修改（以 ``edit_item``）原本項目的清單。
EDITABLE_LISTS: tuple[str, ...] = (*PERSON_HISTORY_FIELDS, "Family_JSON", "Goals_JSON", "Followups_JSON", "Interactions_JSON")


def known_items(lists: Mapping[str, list[object]]) -> dict[str, list[dict[str, object]]]:
    """Return the original entries of the lists, in the shape ``edit_item`` needs to change one.

    回傳各清單原本的項目，形狀是 ``edit_item`` 修改一筆時需要的。

    Each entry carries its index and fingerprint (to find the same entry when writing), the editable values
    (exactly :data:`ITEM_EDIT_KEYS` of that list; a key the entry does not have is ``None``), a one-line text and
    whether it is still active. Done follow-ups are left out; only the latest three interactions are listed.

    每筆帶序號與指紋（寫入時找回同一筆）、能改的值（剛好是那個清單的 ``ITEM_EDIT_KEYS``；那一筆沒有的鍵為
    ``None``）、一行文字，以及是否仍有效。已完成的待追蹤不列；互動只列最近三筆。

    Args:
        lists: the parsed lists
            解析好的清單

    Returns:
        ``{list name: [{"index", "fingerprint", "values", "text", "active"}]}``; lists without entries are left out
        ``{清單名: [{"index", "fingerprint", "values", "text", "active"}]}``；沒有項目的清單不列
    """
    result: dict[str, list[dict[str, object]]] = {}
    for name in EDITABLE_LISTS:
        keys = ITEM_EDIT_KEYS[name]
        entries: list[dict[str, object]] = []
        for index, item in enumerate(lists.get(name, [])):
            if not isinstance(item, dict):
                continue
            if name == "Followups_JSON" and item.get("status", "open") == "done":
                continue
            entries.append({
                "index": index,
                "fingerprint": item_fingerprint(item),
                "values": {key: item.get(key) for key in keys},
                "text": _item_text(name, item),
                "active": is_active(item) if name in PERSON_HISTORY_FIELDS else True,
            })
        if name == "Interactions_JSON":
            entries = sorted(entries, key=lambda e: str((e["values"] or {}).get("date") or ""), reverse=True)[:3]  # type: ignore[union-attr]
        if entries:
            result[name] = entries
    return result


def _item_text(name: str, item: Mapping[str, object]) -> str:
    """Return one original entry as a line of text.

    回傳一筆原本項目的一行文字。
    """
    if name in PERSON_HISTORY_FIELDS:
        return plain_text(entry_detail(name, item), 200)
    if name == "Family_JSON":
        return _family_text(item)
    if name == "Goals_JSON":
        target = f"（目標 {item['target']}）" if item.get("target") else ""
        return f"{item.get('kind') or '目標'}：{item.get('item', '')}{target}"
    if name == "Interactions_JSON":
        return f"{item.get('date', '')}：{item.get('topics', '')}"
    return plain_text(str(item.get("item") or ""), 200)


def _without_marks(line: str) -> str:
    """Drop the 「#n 」 number and the 「（登錄 date）」 the prompt adds to an active entry.

    去掉提示裡有效項目前面的「#n 」編號與後面的「（登錄 日期）」。
    """
    return re.sub(r"（登錄 [^）]*）$", "", re.sub(r"^#\d+ ", "", line))


async def ingest(
    fields: dict[str, str],
    lists: dict[str, list[object]],
    text: str,
    today: date,
    *,
    mode: IngestMode = "update",
    llm_route: Route | None = None,
    optional_interaction: bool = False,
) -> LLMPersonIngestResult:
    """Ask the LLM to structure a text.

    呼叫 LLM 整理一段話。

    Args:
        fields: the person's current fields (empty in ``create`` mode)
            這個人目前的欄位（``create`` 模式為空）
        lists: the parsed lists (empty in ``create`` mode)
            解析好的清單（``create`` 模式為空）
        text: the text the user recorded
            使用者記下的話
        today: today's date
            今天
        mode: ``update`` or ``create`` (see :func:`build_prompt`); ``create`` returns an
            :class:`LLMPersonCreateResult`
            ``update`` 或 ``create``（見 ``build_prompt``）；``create`` 回傳 ``LLMPersonCreateResult``
        llm_route: the route whose model is used; ``None`` means ``person_profile`` (Telegram), the web passes
            ``person_profile.fill`` (``person_profile_fill.FILL_ROUTE``)
            用哪個用途鍵的模型；``None`` 為 ``person_profile``（TG），網頁傳 ``person_profile.fill``
            （``person_profile_fill.FILL_ROUTE``）
        optional_interaction: in ``update`` mode, let the interaction be left out (see :func:`build_prompt`); the
            result is then an :class:`LLMPersonFillResult`
            ``update`` 模式下互動可以沒有（見 ``build_prompt``）；結果為 ``LLMPersonFillResult``

    Returns:
        the validated LLM result; turn it into proposals with :func:`proposals_from`
        驗證過的 LLM 結果；以 ``proposals_from`` 整理成建議

    Raises:
        LLMGenerationError: if the LLM response does not match the format (the message names the model)
            若 LLM 回傳的格式不符（訊息帶模型名稱）
        LLMServiceError: if the LLM call fails (or a related error)
            若 LLM 呼叫失敗（或同類錯誤）
        TemplateEngineError: if the prompt template is broken (a code or deployment error)
            若提示詞模板有誤（程式或部署錯誤）
    """
    schema: type[LLMPersonIngestResult] = LLMPersonIngestResult
    if mode == "create":
        schema = LLMPersonCreateResult
    elif optional_interaction:
        schema = LLMPersonFillResult
    client = await (llm_route or INGEST_ROUTE).client()
    result = await client.generate_structured_data(
        system_prompt="You are a careful assistant that extracts facts into strict JSON. Never invent facts.",
        user_prompt=build_prompt(fields, lists, text, today, mode, optional_interaction=optional_interaction),
        response_schema=schema.model_json_schema(),
    )
    try:
        return schema.model_validate(result.parsed_data)
    except ValidationError as e:
        # 調整提示詞時要看哪個模型、哪種輸出常出錯：記下前幾個錯誤與原始輸出的開頭
        logger.warning(
            "人脈卡整理：%s 回傳的格式不對：%s；原始輸出：%.500s", result.model_name, e.errors()[:3], result.raw_content
        )
        raise LLMGenerationError(f"模型 {result.model_name} 回傳的格式不對，請再試一次。") from e


async def transcribe(audio: bytes, filename: str) -> str:
    """Transcribe speech to text (with the ``stt`` setting of route key ``person_profile``).

    語音轉文字（用途鍵 ``person_profile`` 的 ``stt`` 設定）。

    Args:
        audio: the audio content
            音檔內容
        filename: the audio file name
            音檔檔名

    Returns:
        the transcribed text
        轉出的文字

    Raises:
        AudioEvaluatorUnavailableError: if speech-to-text is unavailable
            若語音轉文字無法使用
        STTServiceError: if speech-to-text is unavailable
            若語音轉文字無法使用
    """
    from app.infrastructure.audio_evaluator.factory import build_whisper

    resolved = await INGEST_ROUTE.resolve("stt")
    return await build_whisper(resolved.provider, resolved.model).transcribe(audio, filename)


def proposals_from(
    result: LLMPersonIngestResult, fields: dict[str, str], lists: dict[str, list[object]], today: date
) -> list[Proposal]:
    """Turn the LLM result into proposals the user can pick, dropping malformed ones.

    把 LLM 的結果整理成可勾選的建議：格式不對的丟掉。

    An interaction whose date is invalid gets today's date instead. A create-mode result
    (:class:`LLMPersonCreateResult`) starts with the display name, and may have no interaction.

    日期不對的互動改成今天。建新卡的結果（``LLMPersonCreateResult``）第一項是稱呼，也可能沒有互動。

    Args:
        result: the LLM result
            LLM 的結果
        fields: the person's current fields (to tell whether a text field suggestion is a repeat)
            這個人目前的欄位（判斷文字欄位的建議是否重複）
        lists: the parsed lists (for changing fields: find the entry to strike out and tell whether an addition is
            a repeat)
            解析好的清單（會變的欄位：找出要劃掉的那筆、判斷新增是否重複）
        today: today's date
            今天

    Returns:
        the proposals, in the order display name (create mode), interaction, follow-ups, goals, family members,
        text fields, changing fields; a family member already on the card is left out
        建議，依稱呼（建新卡）、互動、待追蹤、目標、家人、文字欄位、會變的欄位的順序；卡片上已有的家人不列
    """
    proposals: list[Proposal] = []
    if isinstance(result, LLMPersonCreateResult):
        # 稱呼是建卡必填的：LLM 沒給也列出來（空的），由使用者填
        proposals.append(Proposal("display_name", {"value": plain_text(result.display_name, 100)}))
    if result.interaction is not None:
        interaction = result.interaction.model_dump()
        try:
            item = Interaction.model_validate(interaction)
        except ValidationError:
            item = Interaction.model_validate({**interaction, "date": today.isoformat()})
        data = {k: v for k, v in item.model_dump().items() if v not in ("", None)}
        if data.get("topics") or data.get("updates"):
            proposals.append(Proposal("interaction", data))

    for followup in result.followups:
        try:
            validated = Followup.model_validate(followup.model_dump())
        except ValidationError:
            logger.info("略過格式不對的待追蹤建議: %s", followup)
            continue
        proposals.append(Proposal("followup", {
            k: v for k, v in validated.model_dump().items() if k in ("item", "type", "due") and v not in ("", None)
        }))

    known_goals = {str(g.get("item") or "").strip() for g in lists.get("Goals_JSON", []) if isinstance(g, dict)}
    for goal in result.goals:
        data = goal.model_dump(exclude={"reason"})
        try:
            validated_goal = Goal.model_validate(data)
        except ValidationError:
            # 目標時間看不懂時拿掉時間，目標本身照樣建議
            try:
                validated_goal = Goal.model_validate({**data, "target": ""})
            except ValidationError:
                logger.warning("略過格式不對的目標建議: %s（%s）", goal.item, goal.reason)
                continue
            logger.warning("目標時間看不懂，拿掉時間: %r（%s）", goal.target, goal.item)
        if validated_goal.item in known_goals:
            continue
        known_goals.add(validated_goal.item)
        proposals.append(Proposal("goal", {
            **{k: v for k, v in validated_goal.model_dump().items() if k in ("kind", "item", "target") and v},
            "reason": goal.reason,
        }))

    known_family = {_family_key(f) for f in lists.get("Family_JSON", []) if isinstance(f, dict)}
    for member in result.family:
        try:
            validated_member = FamilyMember.model_validate(member.model_dump(exclude={"reason"}))
        except ValidationError:
            logger.warning("略過格式不對的家人建議: %s（%s）", member.relation, member.reason)
            continue
        member_data = {k: v for k, v in validated_member.model_dump().items() if k in ("relation", "name", "note") and v}
        if _family_key(member_data) in known_family:
            continue
        known_family.add(_family_key(member_data))
        proposals.append(Proposal("family", {**member_data, "reason": member.reason}))

    for update in result.field_updates:
        name, value = update.field, update.value.strip()
        if name not in UPDATABLE_FIELDS or not value:
            continue
        mode = update.mode if _multiline(name) else "replace"
        current = plain_text(fields.get(name, ""), 10_000)
        if mode == "replace":
            if value == current:
                continue
            try:
                validated = PersonCreateParams.model_validate({"Display_Name": "x", name: value})
            except ValidationError:
                logger.info("略過格式不對的欄位建議: %s=%s", name, value)
                continue
            # 存整理過的寫法（例如生日 3/15 → --03-15）
            value = str(getattr(validated, name))
            if value == current:
                continue
        elif value in current:
            continue
        proposals.append(Proposal("field", {"field": name, "value": value, "mode": mode, "reason": update.reason}))

    ended: set[tuple[str, int]] = set()
    for change in result.list_updates:
        name = change.field
        if name not in PERSON_HISTORY_FIELDS:
            logger.warning("略過不認得的清單欄位建議: %s（%s）", name, change.reason)
            continue
        items = lists.get(name, [])
        if change.op == "end":
            index = change.entry - 1
            if not (0 <= index < len(items)) or not is_active(items[index]) or (name, index) in ended:
                # 換工作時 end 對不上、add 卻留著，新舊職位會同時有效：記下有效的編號方便調提示詞
                logger.warning(
                    "略過對不上的劃掉建議: %s #%s（有效的編號 %s；理由：%s）",
                    name, change.entry, [i + 1 for i, item in enumerate(items) if is_active(item)], change.reason,
                )
                continue
            ended.add((name, index))
            proposals.append(Proposal("history_end", {
                "field": name, "index": index, "fingerprint": item_fingerprint(items[index]),
                "text": entry_text(name, items[index]), "reason": change.reason,
            }))
            continue
        if name == "Company":
            item = _position_item(change, today)
        else:
            item = _history_item(name, change.value, change.job_title, today)
            if item is not None:
                _add_periods(name, item, change)
                if change.note.strip() and name in NOTE_FIELDS:
                    item["note"] = change.note.strip()
                elif change.note.strip():
                    logger.warning("略過用不到的說明: %s=%r（%s）", name, change.note, change.reason)
        if item is None:
            logger.warning("略過格式不對的新增建議: %s=%r（職稱 %r；理由：%s）", name, change.value, change.job_title,
                           change.reason)
            continue
        if _already_known(name, items, item):
            continue
        proposals.append(Proposal("history_add", {
            "field": name, "item": {k: v for k, v in item.items() if k not in ("added", "ended")},
            "text": entry_detail(name, item), "reason": change.reason,
        }))
    return proposals


def _family_key(member: Mapping[str, object]) -> tuple[str, str]:
    """Return what identifies a family member: the relation, and the name or (without one) the note.

    回傳辨認一位家人的鍵：關係，加上名字（沒有名字時用說明）。
    """
    relation = str(member.get("relation") or "").strip()
    return relation, str(member.get("name") or "").strip() or str(member.get("note") or "").strip()


def _family_text(member: Mapping[str, object]) -> str:
    """Return a family member as one line for the prompt, for example 「弟弟（健太）：在大阪工作」.

    回傳提示裡的一位家人，例如「弟弟（健太）：在大阪工作」。
    """
    name = plain_text(str(member.get("name") or ""), 40)
    note = plain_text(str(member.get("note") or ""), 120)
    return plain_text(str(member.get("relation") or ""), 20) + (f"（{name}）" if name else "") + (f"：{note}" if note else "")


def _already_known(name: str, items: list[object], item: dict[str, object]) -> bool:
    """Return whether a suggested entry is already recorded (no need to list it for the user to pick).

    回傳新增建議是不是已經記過的（不必再列給使用者勾）。

    - Ordinary fields: it matches an active entry, and this time it brings neither an event time that entry still
      lacks nor a new description (if it does, the handler fills them in).
    - Current position: a current position with the same name exists, and this time it brings neither a start date
      that entry still lacks nor a new description (if it does, the handler fills them in).
    - Past position: one with the same name is still current (that should be handled by ``end``; adding another
      would make two entries), or company, job title, start, and end are all the same.

    - 一般欄位：和某筆有效的內容相同，而且這次沒帶來那一筆還空著的事件時間或新的說明（有的話 handler 會補進去）。
    - 現職：同名的現職已經有，而且這次沒帶來那一筆還空著的到職年月或新的說明（有的話 handler 會補進去）。
    - 以前的工作：同名的還是現職（應該由 ``end`` 處理，另外新增會變成兩筆），或公司・職稱與起訖都相同。
    """
    text = entry_key(name, item)
    current = {entry_key(name, entry): entry for entry in items if is_active(entry)}
    if name != "Company":
        target = current.get(text)
        if target is None:
            return False
        keys = HISTORY_PERIOD_KEYS.get(name, ())
        if not isinstance(target, dict):
            return True
        if merged_note(str(target.get("note") or ""), str(item.get("note") or "")) is not None:
            return False
        return not any(item.get(key) and not str(target.get(key) or "").strip() for key in keys)
    if not is_active(item):
        if text in current:
            logger.warning("略過和現職同名的以前的工作（應該用 end）: %s", entry_text(name, item))
            return True
        key = (text, item.get("start", ""), item.get("end", ""))
        return any(
            isinstance(entry, dict)
            and (entry_key(name, entry), entry.get("start", ""), entry.get("end", "")) == key
            for entry in items
        )
    target = current.get(text)
    if not isinstance(target, dict):
        return False
    if merged_note(str(target.get("note") or ""), str(item.get("note") or "")) is not None:
        return False
    return not (item.get("start") and not str(target.get("start") or "").strip())


def _position_item(change: IngestListUpdate, today: date) -> dict[str, object] | None:
    """Build the suggested position entry: company and job title, plus start, end, and description.

    職位的新增建議：公司、職稱，加上任職起訖與說明。

    A problem with the dates does not drop the whole entry: an unreadable start is removed; an unreadable end
    becomes ``?`` (an end from the LLM means a past position, and removing it would make it current); when start and
    end are reversed, the start is removed and the end becomes ``?``.

    年月有問題時不丟掉整筆：看不懂的到職拿掉；看不懂的
    離職填 ``?``（LLM 給了離職就是以前的工作，拿掉會變成現職）；前後顛倒時拿掉到職、離職改成 ``?``。
    """
    # 公司與職稱都可以空（§17）：只有任職時間或說明的經歷也留著，整筆全空的由模型擋
    item: dict[str, object] = {"company": change.value.strip(), "title": change.job_title.strip(),
                               "added": today.isoformat(), "ended": ""}
    for key, raw in (("start", change.time_start), ("end", change.time_end)):
        value = normalize_period(raw, allow_unknown=key == "end")
        if value is None:
            logger.warning("看不懂的%s年月: %r（%s，%s）", "離職" if key == "end" else "到職", raw,
                           entry_text("Company", item), change.reason)
            if key == "end":
                item["end"] = "?"  # 仍是以前的工作，只是不知道時間
        elif value:
            item[key] = value
    error = period_order_error(str(item.get("start") or ""), str(item.get("end") or ""))
    if error:
        logger.warning("略過前後顛倒的任職年月（%s）: %s", error, change.reason)
        item.pop("start", None)
        item["end"] = "?"  # 仍是以前的工作，只是不知道時間
    if change.note.strip():
        item["note"] = change.note.strip()
    try:
        return PERSON_HISTORY_FIELDS["Company"].model.model_validate(item).model_dump()
    except ValidationError as e:
        logger.warning("略過不合格的工作經歷建議（%s）: %r（%s）", e.errors()[0].get("msg", ""), item, change.reason)
        return None


def _add_periods(name: str, item: dict[str, object], change: IngestListUpdate) -> None:
    """Put the suggested event times of a non-position field (:data:`HISTORY_PERIOD_KEYS`) on the entry.

    職位以外的事件時間（``HISTORY_PERIOD_KEYS``）放進項目。

    ``time_start`` goes to the first key and ``time_end`` to the second (graduation for education). Unusable times
    are degraded rather than dropping the whole entry (times can be added later with a long press), and each case is
    logged to help tune the prompt:

    - An unreadable time is skipped.
    - When the whole entry fails validation (for example graduation before enrollment), all times are removed.
    - Times the field does not use (``time_end`` for a field with one time key, any time for a field without
      times) are not used.
    - When a splittable field suggests several items at once (「登山、潛水」), no time is set: writing splits them
      into several entries, and the same time on each would be recorded wrongly (TG does not ask for times when
      several interests are entered at once either).

    ``time_start`` 放第一個鍵、``time_end`` 放第二個
    （學歷的畢業）。降級而不丟掉整筆（時間之後可以長按補），每種都記 log 方便調提示詞：

    - 看不懂的那個時間略過。
    - 整筆驗證不過（例如畢業早於入學）時拿掉全部時間。
    - 這個欄位用不到的時間（只有一個時間鍵的欄位給了 ``time_end``、沒有時間的欄位給了時間）不用。
    - 可切的欄位一次建議了好幾項（「登山、潛水」）時不放時間：寫入時會切成多筆，同一個時間套到每一筆會記錯
      （TG 一次輸入多個興趣時也不問時間）。
    """
    keys = HISTORY_PERIOD_KEYS.get(name, ())
    raws = (change.time_start, change.time_end)
    if any(raw.strip() for raw in raws[len(keys):]):
        logger.warning("略過這個欄位用不到的時間: %s time_start=%r time_end=%r（%s）", name, *raws, change.reason)
    if not keys or not any(raw.strip() for raw in raws[:len(keys)]):
        return
    if PERSON_HISTORY_FIELDS[name].split and len(history_from_text(name, str(item.get("value") or ""), "")) > 1:
        logger.warning("一次建議多項的時間不放（會套到每一項）: %s %r time_start=%r（%s）", name, item.get("value"),
                       change.time_start, change.reason)
        return
    for key, raw in zip(keys, raws, strict=False):
        value = normalize_period(raw)
        if value is None:
            logger.warning("看不懂的時間: %s %s=%r（%s）", name, key, raw, change.reason)
        elif value:
            item[key] = value
    try:
        item.update(PERSON_HISTORY_FIELDS[name].model.model_validate(item).model_dump())
    except ValidationError as e:
        logger.warning("略過不合理的時間（%s）: %s %r time_start=%r time_end=%r（%s）", e.errors()[0].get("msg", ""),
                       name, item.get("value"), *raws, change.reason)
        for key in keys:
            item.pop(key, None)


def _history_item(name: str, value: str, job_title: str, today: date) -> dict[str, object] | None:
    """Build one entry for an add suggestion, checked with the field's model and dated by the system.

    新增建議的一筆（以該欄位的模型檢查，日期由系統填）。

    Returns ``None`` if the value is malformed.

    格式不對時為 ``None``。
    """
    entries = history_from_text(name, value, today.isoformat(), title=job_title)
    if not entries:
        return None
    model = PERSON_HISTORY_FIELDS[name].model
    try:
        for entry in entries:
            model.model_validate(entry)
    except ValidationError:
        return None
    if name == "Company":
        return dict(entries[0])
    # 可切的欄位寫入時 handler 會再切成多筆；這裡保留原文，預覽才看得出是一起建議的
    return {"value": value.strip(), "added": today.isoformat(), "ended": ""}


def field_html(field: str, current: str, value: str, mode: str) -> str:
    """Return the HTML to write for a field suggestion.

    回傳欄位建議要寫入的 HTML。

    Args:
        field: the field name (currently unused)
            欄位名稱（目前沒有用到）
        current: the field's current HTML
            欄位目前的 HTML
        value: the suggested plain text; it is escaped and line breaks become ``<br>``
            建議的純文字；會跳脫並把換行換成 ``<br>``
        mode: ``append`` to append to ``current`` (the separator is described in :func:`append_field_html`);
            anything else replaces it
            ``append`` 時接在 ``current`` 後面（分隔見 ``append_field_html``）；其他值則取代

    Returns:
        the field HTML to write
        要寫入的欄位 HTML
    """
    new = html.escape(value.strip()).replace("\n", "<br>")
    return append_field_html(current, new) if mode == "append" else new


# =============================================================================
# 讀卡片與寫入勾選的建議（TG 與網頁共用）
# =============================================================================

#: The handler that owns person cards.
#: 人脈卡的 handler。
HANDLER_NAME = "person_profile"

#: The message when the note is gone or is not a person card.
#: 筆記不在或不是人脈卡時的訊息。
PERSON_NOT_FOUND = "Anki 裡找不到這張人脈卡（可能已刪除或尚未同步）。"

#: The proposals written with an action that takes the item as is.
#: 直接以項目當參數寫入的建議。
_ITEM_ACTIONS = {"interaction": "add_interaction", "followup": "add_followup"}


async def load_person(
    card_service: CardService, note_id: int
) -> tuple[dict[str, str], dict[str, list[object]], list[str]] | None:
    """Load a person: plain fields, parsed lists, and the note's tags; ``None`` when the note is not a person card.

    讀出一個人：一般欄位、解析好的清單與筆記的標籤；不是人脈卡時為 ``None``。

    The lists include the JSON lists and the history fields (``PERSON_HISTORY_FIELDS``). A list whose format is
    broken (including old text not yet run through ``migrate_history_fields.py``) is treated as empty and a warning
    is logged; display code marks it, and the handler refuses to write it (no overwrite).

    清單含 JSON 清單與會變的欄位（``PERSON_HISTORY_FIELDS``）。格式壞掉的清單（包括還沒跑
    ``migrate_history_fields.py`` 的舊文字）當成空的，記一筆 warning；顯示的地方會標出，寫入時 handler 會拒絕
    （不覆寫）。

    Args:
        card_service: the card service
            卡片服務
        note_id: the note ID
            筆記 ID

    Returns:
        ``(fields, lists, tags)``, or ``None`` when the note is not a person card
        ``(欄位, 清單, 標籤)``；不是人脈卡時為 ``None``

    Raises:
        AnkiConnectError: if AnkiConnect is unreachable
            若 AnkiConnect 連不上
        NoteNotFoundError: if the note does not exist
            若筆記不存在
    """
    note = await card_service.get_note(note_id)
    if str(note.get("modelName")) != PERSON_NOTE_TYPE:
        return None
    fields = note_field_values(note)
    lists: dict[str, list[object]] = {}
    broken = []
    for name in (*PERSON_JSON_ITEMS, *PERSON_HISTORY_FIELDS):
        try:
            lists[name] = AnkiJsonFieldManager.parse_field_string(fields.get(name, ""))
        except AnkiFieldCorruptedError:
            lists[name] = []
            broken.append(name)
    if broken:
        logger.warning("人脈卡 note %s 的清單格式壞了（當成空的）: %s", note_id, "、".join(broken))
    return fields, lists, [str(tag) for tag in note.get("tags") or []]


def proposal_action(kind: str, data: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    """Return the named action and parameters that write one proposal (other than text fields).

    回傳一項建議（文字欄位以外）要執行的具名動作與參數。

    Args:
        kind: the proposal kind (see :class:`Proposal`)
            建議的種類（見 ``Proposal``）
        data: the proposal data
            建議的內容

    Returns:
        ``(action, params)`` for ``NoteService.run_action``
        給 ``NoteService.run_action`` 的 ``(動作, 參數)``

    Raises:
        ValueError: if the kind is not one written by an action (a programming error)
            若種類不是以動作寫入的（程式錯誤）
    """
    if kind in _ITEM_ACTIONS:
        return _ITEM_ACTIONS[kind], {"item": dict(data)}
    if kind == "goal":
        return "add_item", {"field": "Goals_JSON", "item": {k: v for k, v in data.items() if k != "reason"}}
    if kind == "family":
        return "add_item", {"field": "Family_JSON", "item": {k: v for k, v in data.items() if k != "reason"}}
    if kind == "history_add":
        return "add_history", {"field": data["field"], "item": data["item"]}
    if kind == "history_end":
        return "set_history_status", {
            "field": data["field"], "index": data["index"], "fingerprint": data["fingerprint"], "ended": True,
        }
    if kind == "edit_item":
        return "edit_item", {
            "field": data["field"], "index": data["index"], "fingerprint": data["fingerprint"],
            "values": data["values"], "expected": data.get("expected"),
        }
    raise ValueError(f"未知的建議種類 {kind!r}")


async def apply_proposals(
    note_service: NoteService,
    card_service: CardService,
    note_id: int,
    proposals: Sequence[Mapping[str, object]],
    indexes: Iterable[int],
    saved: set[int],
    errors: dict[int, str] | None = None,
    *,
    force_sync: bool = False,
) -> str | None:
    """Write the chosen proposals to a person card, skipping the ones already written.

    把勾選的建議寫進人脈卡，已經寫過的略過。

    Interaction, follow-up, goal and changing-field proposals are each written with their own action; text field
    proposals are combined into one ``update_fields``, computed from the field's value at the time of writing (so
    an "append" lands after what is there now, even if the card changed after the proposals were made). ``saved``
    is updated in place as each proposal is written, so when a write fails midway the caller still knows what was
    written, and calling again with the same ``saved`` writes only the rest without duplicating.

    互動、待追蹤、目標與會變的欄位建議各以自己的動作寫入，文字欄位的建議合成一次 ``update_fields``，以寫入當下的
    欄位內容計算（建議產生後卡片又被改過，「接在後面」也會接在現在的內容之後）。每寫入一項就就地加進 ``saved``：
    中途失敗時呼叫端仍知道寫了哪些，帶同一個 ``saved`` 再呼叫一次只補寫剩下的，不會重複。

    Everything is written first and AnkiWeb is synced once at the end (not after every write, which made a dozen
    proposals take a dozen syncs). The sync also runs when a write fails midway, so what was written is still
    uploaded.

    全部寫完才同步 AnkiWeb 一次（不是每寫一項同步一次：十幾項建議就要同步十幾次）。中途寫入失敗時也照樣同步，
    已經寫進去的還是會上傳。

    By default the first rejection stops the writing (Telegram). With ``errors`` (the web), a proposal the handler
    rejects gets its reason recorded there and the rest are still written; the text field proposals stand or fall
    together, since they are one write. An Anki connection failure always stops, as the rest would fail too.

    預設第一個被拒絕的項目就停止（TG）。帶 ``errors`` 時（網頁），handler 拒絕的項目把原因記在那裡、其他的照常寫入；
    文字欄位的建議是同一次寫入，會一起成功或一起失敗。Anki 連線失敗一律停止，因為剩下的也會失敗。

    Args:
        note_service: the note service
            筆記服務
        card_service: the card service (to read the current text fields)
            卡片服務（讀目前的文字欄位）
        note_id: the person card
            人脈卡
        proposals: every proposal, each ``{"kind", "data"}`` (see :class:`Proposal`); ``display_name`` proposals
            are not written here (they become the display name when a card is created)
            所有建議，每項 ``{"kind", "data"}``（見 ``Proposal``）；``display_name`` 不在這裡寫（建卡時當稱呼）
        indexes: the indexes of the chosen proposals
            勾選的建議的序號
        saved: the indexes already written; updated in place
            已寫入的序號，會就地更新
        errors: when given, collects ``{index: reason}`` for rejected proposals instead of raising; cleared for the
            proposals written this time
            有給時，收集被拒絕的項目 ``{序號: 原因}`` 而不拋出；這次寫入成功的項目會清掉
        force_sync: sync at the end even if nothing is written here (the caller wrote something without syncing,
            for example the new card of "AI 整理")
            這裡什麼都沒寫入時也要同步（呼叫端已經寫了東西但還沒同步，例如「AI 整理」剛建的新卡）

    Returns:
        the sync warning, or ``None`` when the sync succeeded or nothing needed syncing
        同步提醒；同步成功或不需要同步時為 ``None``

    Raises:
        AnkiConnectError: if AnkiConnect fails while writing
            若寫入時 AnkiConnect 失敗
        FluencyTidesError: if the handler rejects a proposal (for example an entry to strike out has changed) or
            the card is gone, and ``errors`` is not given; ``saved`` holds what was written before
            若 handler 拒絕某一項（例如要劃掉的那筆已經變了）或卡片不在，且沒有給 ``errors``；``saved`` 是在那之前
            寫入的
    """
    pending = [i for i in indexes if i not in saved and proposals[i]["kind"] != "display_name"]
    written_before = len(saved)
    try:
        await _write_each(note_service, card_service, note_id, proposals, pending, saved, errors)
    finally:
        # 寫完（或寫到一半失敗）才同步一次：有寫入東西才需要
        warning = await note_service.sync_after_write() if force_sync or len(saved) > written_before else None
    return warning


async def _write_each(
    note_service: NoteService,
    card_service: CardService,
    note_id: int,
    proposals: Sequence[Mapping[str, object]],
    pending: list[int],
    saved: set[int],
    errors: dict[int, str] | None,
) -> None:
    """Write the pending proposals without syncing (see :func:`apply_proposals`).

    寫入還沒寫的建議，不同步（見 ``apply_proposals``）。
    """
    for i in pending:
        kind = str(proposals[i]["kind"])
        if kind in ("field", "edit_field"):
            continue
        data = proposals[i]["data"]
        assert isinstance(data, Mapping)
        action, params = proposal_action(kind, data)
        try:
            await note_service.run_action(note_id, action, params, sync=False)
        except FluencyTidesError as e:
            if errors is None:
                raise
            errors[i] = e.message
            continue
        saved.add(i)
        if errors is not None:
            errors.pop(i, None)
    # 修改原本的文字欄位排在建議之前：先取代、再接上新的內容
    field_indexes = sorted(
        (i for i in pending if proposals[i]["kind"] in ("edit_field", "field")),
        key=lambda i: proposals[i]["kind"] != "edit_field",
    )
    if field_indexes:
        try:
            await _write_fields(note_service, card_service, note_id, proposals, field_indexes)
        except FluencyTidesError as e:
            if errors is None:
                raise
            errors.update({i: e.message for i in field_indexes})
            return
        saved.update(field_indexes)
        if errors is not None:
            for i in field_indexes:
                errors.pop(i, None)


async def _write_fields(
    note_service: NoteService,
    card_service: CardService,
    note_id: int,
    proposals: Sequence[Mapping[str, object]],
    indexes: list[int],
) -> None:
    """Write the text field proposals as one ``update_fields``, from the fields' current values, without syncing.

    把文字欄位的建議合成一次 ``update_fields`` 寫入，以欄位目前的內容計算，不同步。

    Raises:
        AnkiConnectError: if AnkiConnect fails
            若 AnkiConnect 失敗
        FluencyTidesError: if the card is gone or the handler rejects the fields
            若卡片不在或 handler 拒絕這些欄位
    """
    loaded = await load_person(card_service, note_id)
    if loaded is None:
        raise NoteNotFoundError(PERSON_NOT_FOUND)
    current, changes = loaded[0], {}
    for i in indexes:
        update = proposals[i]["data"]
        assert isinstance(update, Mapping)
        name = str(update["field"])
        # 修改原本的內容是整段取代（mode 固定 replace）
        mode = "replace" if proposals[i]["kind"] == "edit_field" else str(update["mode"])
        changes[name] = field_html(name, changes.get(name, current.get(name, "")), str(update["value"]), mode)
    await note_service.update_fields(HANDLER_NAME, note_id, changes, sync=False)
