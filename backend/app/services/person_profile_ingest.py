"""
人脈卡「用一段話記錄」：一段文字（或語音轉成的文字）經 LLM 整理成一筆互動、待追蹤與人物資料的
建議；使用者勾選後才寫入（人脈卡計畫書 §3.13 P5）。

Person profile "record in one paragraph": an LLM turns free text into an
interaction, follow-ups and profile suggestions; nothing is written until
the user picks them.

- LLM 與語音轉文字走能力路由的用途鍵 ``person_profile``（``/setconfig`` 可設定）。
- LLM 的建議在這裡先過一次格式檢查（不合格的丟掉），寫入時 handler 還會再驗證一次。
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
from app.schemas.llm.person_profile import LLMPersonIngestResult
from app.schemas.person_profile import Followup, Interaction, PersonCreateParams

logger = logging.getLogger(__name__)

INGEST_ROUTE = route("person_profile", "人脈卡：整理一段話", capabilities={"llm", "stt"})

WEEKDAYS = "一二三四五六日"

#: LLM 可以建議更新的欄位：一般欄位，稱呼與聯絡日由使用者或系統維護。Fields the LLM may suggest.
UPDATABLE_FIELDS: tuple[str, ...] = tuple(
    name for name in PersonCreateParams.model_fields if name not in ("Display_Name", "Last_Contact", "Contact_Cadence")
)

_BREAK = re.compile(r"<\s*(br|/div|/p|/li)\s*/?\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Proposal:
    """一項建議。One suggestion.

    Attributes:
        kind: ``interaction``、``followup`` 或 ``field``。The kind.
        data: 互動或待追蹤的項目（已驗證）；欄位建議為 ``{"field", "value", "mode", "reason"}``。
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


def build_prompt(fields: dict[str, str], lists: dict[str, list[object]], text: str, today: date) -> str:
    """組出提示：今天、這個人目前已知的資料（避免重複建議）、使用者記下的話、可更新的欄位。

    Render the prompt with today, the known profile, the text and the fields.
    """
    known = [
        (name, PersonCreateParams.model_fields[name].title or name, plain_text(fields.get(name, "")))
        for name in PersonCreateParams.model_fields
        if plain_text(fields.get(name, ""))
    ]
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
    return get_template_engine().render(
        "prompts/anki/Person_Profile_Ingest.j2",
        display_name=plain_text(fields.get("Display_Name", "")),
        today=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        known_fields=known,
        open_followups=open_followups,
        recent_interactions=[f"{i.get('date', '')}：{i.get('topics', '')}" for i in recent],
        updatable_fields=updatable,
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


def proposals_from(result: LLMPersonIngestResult, fields: dict[str, str], today: date) -> list[Proposal]:
    """把 LLM 的結果整理成可勾選的建議：格式不對的丟掉（日期不對的互動改成今天）。

    Turn the LLM result into proposals, dropping malformed ones.

    Args:
        result: LLM 的結果。The LLM result.
        fields: 這個人目前的欄位（判斷欄位建議是否重複）。The current fields.
        today: 今天。Today.

    Returns:
        建議，依互動、待追蹤、欄位的順序。The proposals.
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
                PersonCreateParams.model_validate({"Display_Name": "x", name: value})
            except ValidationError:
                logger.info("略過格式不對的欄位建議: %s=%s", name, value)
                continue
        elif value in current:
            continue
        proposals.append(Proposal("field", {"field": name, "value": value, "mode": mode, "reason": update.reason}))
    return proposals


def field_html(current: str, value: str, mode: str) -> str:
    """欄位建議要寫入的 HTML（接在後面時以換行分隔）。The field HTML to write."""
    new = html.escape(value.strip()).replace("\n", "<br>")
    if mode == "append" and current.strip():
        return f"{current}<br>{new}"
    return new
