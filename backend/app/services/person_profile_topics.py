"""
人脈卡「AI 分析與話題」：LLM 以諮商師的角度先分析這個人，再推薦見面時可以聊的話題。

Person profile "AI insight and topics": the LLM first reads the person like
a counselor, then suggests conversation topics.

隱私：送給 LLM 前，本人與家人的名字換成代號（``〔本人〕``、``〔家人1〕``），回來後再換回真正的稱呼。
做法是最基本的字串比對：姓名欄位（稱呼與去掉敬稱的部分、各語言的姓名、讀音）與家人名字的完整值，
以及只寫名字的情況（「姓 名」分開寫的欄位取出的名字），假名再加平假名、片假名兩種寫法，出現就換掉。
沒有登記的第三者名字不會被遮。聯絡方式、敏感話題、名字記憶法、外貌等與話題無關的欄位不送。

Names of the person and their family are replaced with tokens before the
LLM sees them and restored afterwards.

- LLM 走能力路由的用途鍵 ``person_profile.topics``（沒有另外設定時沿用 ``person_profile``）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.dependencies import get_template_engine
from app.core.exceptions import LLMGenerationError
from app.infrastructure.llm.routing import route
from app.schemas.llm.person_profile import LLMPersonTopicsResult
from app.schemas.person_profile import PersonCreateParams
from app.services.person_profile_ingest import WEEKDAYS, plain_text

logger = logging.getLogger(__name__)

TOPICS_ROUTE = route("person_profile.topics", "人脈卡：分析與想話題", capabilities={"llm"})

#: 本人的代號。The person's token.
PERSON_TOKEN = "〔本人〕"

#: 本人的姓名欄位（全部遮起來，不另外送）。The person's name fields.
NAME_FIELDS = ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")

#: 以空白分開「姓 名」的欄位裡，名字在第幾段（只寫名字時也要遮）。Where the given name sits.
GIVEN_NAME_POSITION = {"Name_JA": -1, "Name_JA_Kana": -1, "Name_Romaji": -1, "Name_EN": 0}

#: 不送給 LLM 的欄位：姓名（另以代號表示）、與話題無關或不該外送的資料。Fields never sent.
EXCLUDED_FIELDS = (*NAME_FIELDS, "Honorific", "Name_Memo", "Appearance", "Sensitive", "Introducer_Note_ID",
                   "Contact_Cadence")

#: 稱呼後面常見的敬稱：還原時避免變成「田中さんさん」。Honorific suffixes.
HONORIFICS = ("さん", "くん", "君", "ちゃん", "さま", "様", "先生", "桑", "san")

@dataclass
class NameMask:
    """名字 ↔ 代號的對照：送出前遮名字，回來後換回稱呼。Masks names and restores them.

    Attributes:
        names: 名字 → 代號。Name to token.
        restore: 代號 → 要換回的稱呼。Token to the display name.
        family: 家人的代號與關係（給提示詞說明）。Family tokens with relations.
    """

    names: dict[str, str] = field(default_factory=dict)
    restore: dict[str, str] = field(default_factory=dict)
    family: list[tuple[str, str]] = field(default_factory=list)

    def add(self, token: str, display: str, names: list[str]) -> None:
        """登記一個人：這些名字（稱呼另加去掉敬稱的部分；假名另加平假名、片假名兩種寫法）換成同一個代號。

        Register a person's names (plus hiragana/katakana variants) under one token.
        """
        self.restore[token] = display
        for name in names:
            name = name.strip()
            for value in {name, _strip_honorific(name)}:
                for variant in {value, _to_katakana(value), _to_hiragana(value)}:
                    if variant:
                        self.names.setdefault(variant, token)

    def mask(self, text: str) -> str:
        """把登記過的名字換成代號：完整字串比對，長的先換（「田中さん」先於「田中」）。Replace names."""
        for name in sorted(self.names, key=len, reverse=True):
            text = text.replace(name, self.names[name])
        return text

    def unmask(self, text: str) -> str:
        """代號換回稱呼；稱呼已含敬稱時，去掉 LLM 在代號後面多加的敬稱。Restore the names."""
        for token, display in self.restore.items():
            suffix = "|".join(re.escape(h) for h in HONORIFICS)
            has_honorific = _strip_honorific(display) != display
            text = re.sub(
                re.escape(token) + f"({suffix})?",
                lambda m, d=display, h=has_honorific: d if h or not m.group(1) else d + m.group(1),
                text,
            )
        return text


def _to_katakana(text: str) -> str:
    """平假名轉片假名（其他字不變）。Hiragana to katakana."""
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


def _to_hiragana(text: str) -> str:
    """片假名轉平假名（其他字不變）。Katakana to hiragana."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def _strip_honorific(name: str) -> str:
    for honorific in HONORIFICS:
        if name.endswith(honorific) and len(name) > len(honorific):
            return name[: -len(honorific)].strip()
    return name


def build_mask(fields: dict[str, str], lists: dict[str, list[object]]) -> NameMask:
    """依本人的姓名欄位與家人清單建立對照表。Build the mask from the name fields and family."""
    mask = NameMask()
    display = plain_text(fields.get("Display_Name", ""), 60)
    names = [plain_text(fields.get(name, ""), 60) for name in NAME_FIELDS]
    # 只寫名字的情況：從「姓 名」分開寫的欄位取出名字（英文名在前，其他在後）
    for name, position in GIVEN_NAME_POSITION.items():
        parts = plain_text(fields.get(name, ""), 60).split()
        if len(parts) >= 2:
            names.append(parts[position])
    mask.add(PERSON_TOKEN, display, names)
    number = 0
    for member in lists.get("Family_JSON", []):
        if not isinstance(member, dict) or not str(member.get("name", "")).strip():
            continue
        number += 1
        token = f"〔家人{number}〕"
        name = str(member["name"]).strip()
        mask.add(token, name, [name])
        mask.family.append((token, str(member.get("relation", "") or "家人")))
    return mask


def build_prompt(fields: dict[str, str], lists: dict[str, list[object]], today: date) -> tuple[str, NameMask]:
    """組出提示（所有文字都先遮名字），一併回傳對照表。Render the masked prompt and return the mask."""
    mask = build_mask(fields, lists)
    m = mask.mask
    profile = [
        (PersonCreateParams.model_fields[name].title or name, m(plain_text(fields.get(name, ""))))
        for name in PersonCreateParams.model_fields
        if name not in EXCLUDED_FIELDS and plain_text(fields.get(name, ""))
    ]
    family = []
    for member in lists.get("Family_JSON", []):
        if not isinstance(member, dict):
            continue
        parts = [str(member.get("relation", "") or "家人"), m(str(member.get("name", "") or ""))]
        line = "：".join(p for p in parts if p)
        if member.get("birth_year"):
            line += f"（{member['birth_year']} 年生）"
        if member.get("note"):
            line += f"，{m(str(member['note']))}"
        family.append(line)
    interactions = []
    for item in sorted(
        (i for i in lists.get("Interactions_JSON", []) if isinstance(i, dict)),
        key=lambda i: str(i.get("date", "")),
        reverse=True,
    )[:5]:
        details = [str(item.get("topics", "") or "")]
        for key, label in (("updates", "近況"), ("place", "地點"), ("quote", "他說")):
            if item.get(key):
                details.append(f"{label}：{item[key]}")
        interactions.append(f"{item.get('date', '')}：" + m("；".join(d for d in details if d)))
    labels = {"ask": "下次要問", "my_promise": "我答應的", "their_promise": "他答應的"}
    followups = [
        f"{labels.get(str(f.get('type') or 'ask'), '待追蹤')}：{m(str(f.get('item', '')))}"
        + (f"（期限 {f['due']}）" if f.get("due") else "")
        for f in lists.get("Followups_JSON", [])
        if isinstance(f, dict) and f.get("status", "open") != "done"
    ]
    key_dates = [
        f"{m(str(d.get('label', '')))}：{d.get('date', '')}" + ("（每年）" if d.get("yearly") else "")
        for d in lists.get("Key_Dates_JSON", [])[:10]
        if isinstance(d, dict)
    ]
    gifts = [
        f"{'收到' if g.get('direction') == 'received' else '送出'}{m(str(g.get('item', '')))}"
        + (f"（{g['date']}）" if g.get("date") else "")
        for g in lists.get("Gifts_JSON", [])[-5:]
        if isinstance(g, dict)
    ]
    prompt = get_template_engine().render(
        "prompts/anki/Person_Profile_Topics.j2",
        person_token=PERSON_TOKEN,
        family_tokens=mask.family,
        today=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        profile=profile,
        family=family,
        interactions=interactions,
        followups=followups,
        key_dates=key_dates,
        gifts=gifts,
        has_sensitive=bool(plain_text(fields.get("Sensitive", ""))),
    )
    return prompt, mask


async def suggest(fields: dict[str, str], lists: dict[str, list[object]], today: date) -> LLMPersonTopicsResult:
    """請 LLM 分析這個人並推薦話題；回傳的文字已換回真正的稱呼。Ask the LLM; names restored.

    Raises:
        LLMServiceError 等：LLM 呼叫失敗。When the LLM call fails.
        LLMGenerationError: LLM 回傳的格式不符（訊息帶模型名稱）。On a malformed response.
        TemplateEngineError: 提示詞模板有誤。When the prompt template is broken.
    """
    prompt, mask = build_prompt(fields, lists, today)
    client = await TOPICS_ROUTE.client()
    result = await client.generate_structured_data(
        system_prompt="You are a warm, perceptive counselor. Reply in strict JSON. Never invent facts.",
        user_prompt=prompt,
        response_schema=LLMPersonTopicsResult.model_json_schema(),
    )
    try:
        parsed = LLMPersonTopicsResult.model_validate(result.parsed_data)
    except ValidationError as e:
        logger.warning(
            "人脈卡話題：%s 回傳的格式不對：%s；原始輸出：%.500s", result.model_name, e.errors()[:3], result.raw_content
        )
        raise LLMGenerationError(f"模型 {result.model_name} 回傳的格式不對，請再試一次。") from e
    return _unmask_model(parsed, mask)


_Model = TypeVar("_Model", bound=BaseModel)


def _unmask_model(model: _Model, mask: NameMask) -> _Model:
    """把模型裡所有文字的代號換回稱呼。Restore names in every string of the model."""

    def walk(value: object) -> object:
        if isinstance(value, str):
            return mask.unmask(value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return type(model).model_validate(walk(model.model_dump()))
