"""
人脈卡「用一段話記錄」：一段文字（或語音轉成的文字）經 LLM 整理成一筆互動、待追蹤與人物資料的
建議；使用者勾選後才寫入（人脈卡計畫書 §3.13 P5）。

Person profile "record in one paragraph": an LLM turns free text into an
interaction, follow-ups and profile suggestions; nothing is written until
the user picks them.

- LLM 與語音轉文字走能力路由的用途鍵 ``person_profile``（``/setconfig`` 可設定）。
- LLM 的建議在這裡先過一次格式檢查（不合格的丟掉），寫入時 handler 還會再驗證一次。
- 文字欄位的建議是取代或接在後面；會變的欄位（興趣、職位等）的建議是「新增一筆」或「劃掉某筆」，
  日期都由系統填（登錄日計畫書 §4.6）。
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from app.core.dependencies import get_template_engine
from app.core.exceptions import LLMGenerationError
from app.infrastructure.llm.routing import route
from app.schemas.llm.person_profile import IngestListUpdate, LLMPersonIngestResult
from app.schemas.person_profile import (
    GOAL_STATUSES,
    HISTORY_PERIOD_KEYS,
    PERSON_HISTORY_FIELDS,
    RETIRED_FIELDS,
    Followup,
    Goal,
    Interaction,
    PersonCreateParams,
    append_field_html,
    entry_detail,
    entry_text,
    history_from_text,
    is_active,
    item_fingerprint,
    normalize_period,
    period_order_error,
)

logger = logging.getLogger(__name__)

INGEST_ROUTE = route("person_profile", "人脈卡：整理一段話", capabilities={"llm", "stt"})

WEEKDAYS = "一二三四五六日"

#: LLM 可以建議取代或接在後面的文字欄位。稱呼與聯絡日由使用者或系統維護；介紹人是另一筆筆記的 ID，
#: LLM 無從得知；會變的欄位走 :data:`PERSON_HISTORY_FIELDS` 的新增、劃掉。Text fields the LLM may suggest.
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
    """一項建議。One suggestion.

    Attributes:
        kind: ``interaction``、``followup``、``goal``、``field``、``history_add`` 或 ``history_end``。The kind.
        data: 互動、待追蹤或目標的項目（已驗證；目標另附 ``reason``）；文字欄位的建議為 ``{"field", "value", "mode", "reason"}``；
            新增一筆為 ``{"field", "item", "text", "reason"}``（``item`` 交給 ``add_history``）；
            劃掉一筆為 ``{"field", "index", "fingerprint", "text", "reason"}``。
    """

    kind: str
    data: dict[str, object]


def plain_text(value: str, limit: int = 300) -> str:
    """欄位 HTML 轉成一行純文字（給 LLM 看）。A field's HTML as one line of text."""
    text = html.unescape(_TAG.sub("", _BREAK.sub("；", value or "")))
    text = re.sub(r"\s+", " ", text).strip(" ；")
    return text if len(text) <= limit else text[:limit] + "…"


def _multiline(name: str) -> bool:
    info = PersonCreateParams.model_fields.get(name)
    extra = info.json_schema_extra if info else None
    return isinstance(extra, dict) and extra.get("x-ui") == "textarea"


def _history_lines(name: str, items: list[object]) -> tuple[list[str], list[str]]:
    """會變的欄位給 LLM 看的：有效的每筆附編號（``#序號+1``，劃掉時用），以及過去的（劃掉的，或有離職年月的
    職位）。每筆附事件時間（:func:`entry_detail`，職位另附說明）；有效的另外附「登錄 日期」：寫法和事件時間
    （「2015 起」）不同，LLM 才分得出哪個是那件事的時間、哪個只是記下的日子。

    The current entries numbered for "end" proposals, and the past ones.
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


def build_prompt(fields: dict[str, str], lists: dict[str, list[object]], text: str, today: date) -> str:
    """組出提示：今天、這個人目前已知的資料（避免重複建議）、使用者記下的話、可更新的欄位。

    Render the prompt with today, the known profile, the text and the fields.
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
    open_followups = [
        str(f.get("item", ""))
        for f in lists.get("Followups_JSON", [])
        if isinstance(f, dict) and f.get("status", "open") != "done"
    ]
    recent = sorted(
        (i for i in lists.get("Interactions_JSON", []) if isinstance(i, dict)),
        key=lambda i: str(i.get("date", "")),
        reverse=True,
    )[:3]
    updatable = [
        (name, PersonCreateParams.model_fields[name].title or name, PersonCreateParams.model_fields[name].description)
        for name in UPDATABLE_FIELDS
    ]
    list_fields = [(name, spec.title, spec.single, spec.split) for name, spec in PERSON_HISTORY_FIELDS.items()]
    return get_template_engine().render(
        "prompts/anki/Person_Profile_Ingest.j2",
        display_name=plain_text(fields.get("Display_Name", "")),
        today=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        known_fields=known,
        known_lists=known_lists,
        open_followups=open_followups,
        known_goals=[
            f"{g.get('kind') or '目標'}：{g.get('item', '')}" + (f"（目標 {g['target']}）" if g.get("target") else "")
            + ("" if str(g.get("status") or "open") == "open" else f"（{GOAL_STATUSES.get(str(g['status']), '')}）")
            for g in lists.get("Goals_JSON", []) if isinstance(g, dict)
        ],
        recent_interactions=[f"{i.get('date', '')}：{i.get('topics', '')}" for i in recent],
        updatable_fields=updatable,
        list_fields=list_fields,
        text=text,
    )


async def ingest(
    fields: dict[str, str], lists: dict[str, list[object]], text: str, today: date
) -> LLMPersonIngestResult:
    """呼叫 LLM 整理一段話。Ask the LLM to structure the text.

    Raises:
        LLMServiceError 等：LLM 呼叫失敗。When the LLM call fails.
        LLMGenerationError: LLM 回傳的格式不符（訊息帶模型名稱）。On a malformed response.
        TemplateEngineError: 提示詞模板有誤（程式或部署錯誤）。When the prompt template is broken.
    """
    client = await INGEST_ROUTE.client()
    result = await client.generate_structured_data(
        system_prompt="You are a careful assistant that extracts facts into strict JSON. Never invent facts.",
        user_prompt=build_prompt(fields, lists, text, today),
        response_schema=LLMPersonIngestResult.model_json_schema(),
    )
    try:
        return LLMPersonIngestResult.model_validate(result.parsed_data)
    except ValidationError as e:
        # 調整提示詞時要看哪個模型、哪種輸出常出錯：記下前幾個錯誤與原始輸出的開頭
        logger.warning(
            "人脈卡整理：%s 回傳的格式不對：%s；原始輸出：%.500s", result.model_name, e.errors()[:3], result.raw_content
        )
        raise LLMGenerationError(f"模型 {result.model_name} 回傳的格式不對，請再試一次。") from e


async def transcribe(audio: bytes, filename: str) -> str:
    """語音轉文字（用途鍵 ``person_profile`` 的 ``stt`` 設定）。Speech to text.

    Raises:
        STTServiceError、AudioEvaluatorUnavailableError：語音轉文字無法使用。When STT is unavailable.
    """
    from app.infrastructure.audio_evaluator.factory import build_whisper

    resolved = await INGEST_ROUTE.resolve("stt")
    return await build_whisper(resolved.provider, resolved.model).transcribe(audio, filename)


def proposals_from(
    result: LLMPersonIngestResult, fields: dict[str, str], lists: dict[str, list[object]], today: date
) -> list[Proposal]:
    """把 LLM 的結果整理成可勾選的建議：格式不對的丟掉（日期不對的互動改成今天）。

    Turn the LLM result into proposals, dropping malformed ones.

    Args:
        result: LLM 的結果。The LLM result.
        fields: 這個人目前的欄位（判斷文字欄位的建議是否重複）。The current fields.
        lists: 解析好的清單（會變的欄位：找出要劃掉的那筆、判斷新增是否重複）。The parsed lists.
        today: 今天。Today.

    Returns:
        建議，依互動、待追蹤、目標、文字欄位、會變的欄位的順序。The proposals.
    """
    proposals: list[Proposal] = []
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


def _already_known(name: str, items: list[object], item: dict[str, object]) -> bool:
    """新增建議是不是已經記過的（不必再列給使用者勾）。Whether a suggested entry is already recorded.

    - 一般欄位：和某筆有效的內容相同，而且這次沒帶來那一筆還空著的事件時間（有的話 handler 會補進去）。
    - 現職：同名的現職已經有，而且這次沒帶來那一筆還空著的到職年月或說明（有的話 handler 會補進去）。
    - 以前的工作：同名的還是現職（應該由 ``end`` 處理，另外新增會變成兩筆），或公司・職稱與起訖都相同。
    """
    text = entry_text(name, item)
    current = {entry_text(name, entry): entry for entry in items if is_active(entry)}
    if name != "Company":
        target = current.get(text)
        if target is None:
            return False
        keys = HISTORY_PERIOD_KEYS.get(name, ())
        return not any(item.get(key) and isinstance(target, dict) and not str(target.get(key) or "").strip()
                       for key in keys)
    if not is_active(item):
        if text in current:
            logger.warning("略過和現職同名的以前的工作（應該用 end）: %s", text)
            return True
        key = (text, item.get("start", ""), item.get("end", ""))
        return any(
            isinstance(entry, dict)
            and (entry_text(name, entry), entry.get("start", ""), entry.get("end", "")) == key
            for entry in items
        )
    target = current.get(text)
    if not isinstance(target, dict):
        return False
    return not any(item.get(key) and not str(target.get(key) or "").strip() for key in ("start", "note"))


def _position_item(change: IngestListUpdate, today: date) -> dict[str, object] | None:
    """職位的新增建議：公司、職稱，加上任職起訖與說明。年月有問題時不丟掉整筆：看不懂的到職拿掉；看不懂的
    離職填 ``?``（LLM 給了離職就是以前的工作，拿掉會變成現職）；前後顛倒時拿掉到職、離職改成 ``?``。

    The suggested position; unusable periods are degraded, not the whole entry.
    """
    item = _history_item("Company", change.value, change.job_title, today)
    if item is None:
        return None
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
    if change.job_note.strip():
        item["note"] = change.job_note.strip()
    try:
        return PERSON_HISTORY_FIELDS["Company"].model.model_validate(item).model_dump()
    except ValidationError:
        return None


def _add_periods(name: str, item: dict[str, object], change: IngestListUpdate) -> None:
    """職位以外的事件時間（:data:`HISTORY_PERIOD_KEYS`）：``time_start`` 放第一個鍵、``time_end`` 放第二個
    （學歷的畢業）。降級而不丟掉整筆（時間之後可以長按補），每種都記 log 方便調提示詞：

    - 看不懂的那個時間略過。
    - 整筆驗證不過（例如畢業早於入學）時拿掉全部時間。
    - 這個欄位用不到的時間（只有一個時間鍵的欄位給了 ``time_end``、沒有時間的欄位給了時間）不用。
    - 可切的欄位一次建議了好幾項（「登山、潛水」）時不放時間：寫入時會切成多筆，同一個時間套到每一筆會記錯
      （TG 一次輸入多個興趣時也不問時間）。

    Put the suggested times on the entry; unusable times are dropped, not the entry.
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
    """新增建議的一筆（以該欄位的模型檢查，日期由系統填）；格式不對時為 None。The entry, or None."""
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
    """欄位建議要寫入的 HTML（接在後面的分隔見 :func:`append_field_html`）。The field HTML to write."""
    new = html.escape(value.strip()).replace("\n", "<br>")
    return append_field_html(current, new) if mode == "append" else new
