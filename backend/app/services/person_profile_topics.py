"""Person card "AI insight and topics": the LLM first analyzes the person as a counselor, then suggests topics.

人脈卡「AI 分析與話題」：LLM 以諮商師的角度先分析這個人，再推薦見面時可以聊的話題。

Privacy: before anything is sent to the LLM, the names of the person and their family are replaced with tokens
(``〔本人〕``, ``〔家人1〕``), and the real display names are restored afterwards. It is the most basic string
matching: the full values of the name fields (the display name and its form without an honorific, the names in each
language, the reading) and of family names, plus the given name alone (taken from fields written as "family given"
separated by a space); kana names also get hiragana and katakana variants; any occurrence is replaced. Names of
unregistered third parties are not masked. Fields unrelated to topics, such as contact details, sensitive topics,
name mnemonics, and appearance, are not sent. For changing fields (interests, position, and so on), active entries
are sent as usual, and struck-out ones are also sent, marked 「以前：」 (past events are conversation material too).

隱私：送給 LLM 前，本人與家人的名字換成代號（``〔本人〕``、``〔家人1〕``），回來後再換回真正的稱呼。
做法是最基本的字串比對：姓名欄位（稱呼與去掉敬稱的部分、各語言的姓名、讀音）與家人名字的完整值，
以及只寫名字的情況（「姓 名」分開寫的欄位取出的名字），假名再加平假名、片假名兩種寫法，出現就換掉。
沒有登記的第三者名字不會被遮。聯絡方式、敏感話題、名字記憶法、外貌等與話題無關的欄位不送。
會變的欄位（興趣、職位等）有效的照常送，劃掉的也送並標成「以前：」（過去的事也是聊天素材）。

- The LLM uses the capability routing route key ``person_profile.topics`` (falling back to ``person_profile`` when
  not set separately).

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
from app.schemas.person_profile import PERSON_HISTORY_FIELDS, RETIRED_FIELDS, PersonCreateParams, entry_detail, is_active
from app.services.person_profile_ingest import WEEKDAYS, plain_text

logger = logging.getLogger(__name__)

TOPICS_ROUTE = route("person_profile.topics", "人脈卡：分析與想話題", capabilities={"llm"})

#: The person's token.
#: 本人的代號。
PERSON_TOKEN = "〔本人〕"

#: The person's name fields (all masked, never sent separately).
#: 本人的姓名欄位（全部遮起來，不另外送）。
NAME_FIELDS = ("Display_Name", "Name_ZH", "Name_EN", "Name_JA", "Name_JA_Kana", "Name_Romaji")

#: In fields written as "family given" separated by a space, which segment is the given name (also masked when
#: written alone).
#: 以空白分開「姓 名」的欄位裡，名字在第幾段（只寫名字時也要遮）。
GIVEN_NAME_POSITION = {"Name_JA": -1, "Name_JA_Kana": -1, "Name_Romaji": -1, "Name_EN": 0}

#: Fields never sent to the LLM: names (represented by tokens instead), data unrelated to topics or that should not
#: leave, and retired fields (``Title``).
#: 不送給 LLM 的欄位：姓名（另以代號表示）、與話題無關或不該外送的資料、不再使用的欄位（``Title``）。
EXCLUDED_FIELDS = (*NAME_FIELDS, "Honorific", "Name_Memo", "Appearance", "Sensitive", "Introducer_Note_ID",
                   "Contact_Cadence", *RETIRED_FIELDS)

#: Common honorific suffixes after a display name, so restoring does not produce 「田中さんさん」.
#: 稱呼後面常見的敬稱：還原時避免變成「田中さんさん」。
HONORIFICS = ("さん", "くん", "君", "ちゃん", "さま", "様", "先生", "桑", "san")

@dataclass
class NameMask:
    """A name ↔ token mapping: names are masked before sending and restored to display names afterwards.

    名字 ↔ 代號的對照：送出前遮名字，回來後換回稱呼。

    Attributes:
        names: name → token
            名字 → 代號
        restore: token → the display name to restore
            代號 → 要換回的稱呼
        family: family tokens with their relations (explained in the prompt)
            家人的代號與關係（給提示詞說明）
    """

    names: dict[str, str] = field(default_factory=dict)
    restore: dict[str, str] = field(default_factory=dict)
    family: list[tuple[str, str]] = field(default_factory=list)

    def add(self, token: str, display: str, names: list[str]) -> None:
        """Register one person: these names all map to the same token.

        登記一個人：這些名字換成同一個代號。

        Each name also registers its form without an honorific, and kana get both hiragana and katakana variants.
        A name already registered keeps its first token.

        每個名字另加去掉敬稱的部分；假名另加平假名、片假名兩種寫法。已登記的名字保留第一次的代號。

        Args:
            token: the person's token
                這個人的代號
            display: the display name restored in place of the token
                代號要換回的稱呼
            names: the names to mask; blank ones are ignored
                要遮的名字；空白的會忽略
        """
        self.restore[token] = display
        for name in names:
            name = name.strip()
            for value in {name, _strip_honorific(name)}:
                for variant in {value, _to_katakana(value), _to_hiragana(value)}:
                    if variant:
                        self.names.setdefault(variant, token)

    def mask(self, text: str) -> str:
        """Replace registered names with their tokens.

        把登記過的名字換成代號。

        Matching is on whole strings, longest first (「田中さん」 before 「田中」).

        完整字串比對，長的先換（「田中さん」先於「田中」）。

        Args:
            text: the text to mask
                要遮的文字

        Returns:
            the masked text
            遮好的文字
        """
        for name in sorted(self.names, key=len, reverse=True):
            text = text.replace(name, self.names[name])
        return text

    def unmask(self, text: str) -> str:
        """Restore tokens to display names.

        代號換回稱呼。

        When the display name already has an honorific, an honorific the LLM added after the token is dropped;
        otherwise it is kept.

        稱呼已含敬稱時，去掉 LLM 在代號後面多加的敬稱；否則保留。

        Args:
            text: the text to restore
                要還原的文字

        Returns:
            the restored text
            還原後的文字
        """
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
    """Convert hiragana to katakana (other characters unchanged).

    平假名轉片假名（其他字不變）。
    """
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


def _to_hiragana(text: str) -> str:
    """Convert katakana to hiragana (other characters unchanged).

    片假名轉平假名（其他字不變）。
    """
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in text)


def _strip_honorific(name: str) -> str:
    for honorific in HONORIFICS:
        if name.endswith(honorific) and len(name) > len(honorific):
            return name[: -len(honorific)].strip()
    return name


def build_mask(fields: dict[str, str], lists: dict[str, list[object]]) -> NameMask:
    """Build the mask from the person's name fields and the family list.

    依本人的姓名欄位與家人清單建立對照表。

    Family members without a name are skipped; the others get ``〔家人1〕``, ``〔家人2〕``, and so on in order.

    沒有名字的家人略過；其餘依序得到 ``〔家人1〕``、``〔家人2〕``……

    Args:
        fields: the card fields
            卡片欄位
        lists: the parsed lists; ``Family_JSON`` is read
            解析好的清單；讀取 ``Family_JSON``

    Returns:
        the mask
        對照表
    """
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


def build_prompt(
    fields: dict[str, str], lists: dict[str, list[object]], today: date, *, mask_text: bool = True
) -> tuple[str, NameMask]:
    """Render the prompt and return the mask with it.

    組出提示，一併回傳對照表。

    Args:
        fields: the card fields
            欄位
        lists: the parsed lists
            解析好的清單
        today: today's date
            今天
        mask_text: whether to mask names in the body text too (the default); when ``False`` the body text is sent
            as is (for example when a name sounds like an ordinary word and masking makes it unreadable), the name
            fields are still not sent, and returned tokens are still restored
            內文也遮名字（預設）；``False`` 時內文照原文送出（例如名字和一般詞同音、遮了反而看不懂），
            姓名欄位照樣不送，回來的代號照樣換回

    Returns:
        the prompt and the mask
        提示與對照表

    Raises:
        TemplateEngineError: if the prompt template is broken
            若提示詞模板有誤
    """
    mask = build_mask(fields, lists)
    m = mask.mask if mask_text else (lambda text: text)
    profile = []
    for name in PersonCreateParams.model_fields:
        if name in EXCLUDED_FIELDS:
            continue
        if name in PERSON_HISTORY_FIELDS:
            value = history_text(name, lists.get(name, []))
            title = PERSON_HISTORY_FIELDS[name].title
        else:
            value = plain_text(fields.get(name, ""))
            title = PersonCreateParams.model_fields[name].title or name
        if value:
            profile.append((title, m(value)))
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
    directions = {"received": "收到", "idea": "想要（還沒送）"}
    gifts = [
        f"{directions.get(str(g.get('direction') or ''), '送出')}{m(str(g.get('item', '')))}"
        + (f"（{g['date']}）" if g.get("date") else "")
        for g in lists.get("Gifts_JSON", [])[-8:]
        if isinstance(g, dict)
    ]
    # 目標：進行中的是最好的聊天素材（「考試準備得如何」）；達成的可以恭喜，放棄的不主動提
    goals = []
    for goal in lists.get("Goals_JSON", []):
        if not isinstance(goal, dict) or str(goal.get("status") or "open") == "dropped":
            continue
        kind = str(goal.get("kind") or "目標")
        line = f"{kind}：{m(str(goal.get('item', '')))}" + (f"（目標 {goal['target']}）" if goal.get("target") else "")
        if goal.get("status") == "done":
            line += f"（已達成{'，' + str(goal['closed']) if goal.get('closed') else ''}）"
        goals.append(line)
    prompt = get_template_engine().render(
        "prompts/anki/Person_Profile_Topics.j2",
        person_token=PERSON_TOKEN,
        family_tokens=mask.family if mask_text else [],
        text_unmasked=not mask_text,
        today=today.isoformat(),
        weekday=WEEKDAYS[today.weekday()],
        profile=profile,
        family=family,
        interactions=interactions,
        followups=followups,
        key_dates=key_dates,
        gifts=gifts,
        goals=goals,
        # 劃掉的敏感話題也算（過去的事一樣不該主動提）；欄位格式壞了讀不出清單時看原文
        has_sensitive=bool(lists.get("Sensitive")) or bool(plain_text(fields.get("Sensitive", ""))),
    )
    return prompt, mask


def history_text(name: str, items: list[object]) -> str:
    """Return one line of a changing field for the LLM.

    回傳會變的欄位給 LLM 的一行。

    Active entries are listed as usual; struck-out ones are marked as past events after 「以前：」 (they are
    conversation material too), with 「到…為止」 appended when they have an end date, except for ``Company``.

    有效的照常列出，劃掉的接在「以前：」後面標明是過去的事（也是聊天素材）；有結束日期的另加「到…為止」，
    ``Company`` 除外。

    Args:
        name: the field name
            欄位名稱
        items: the field's entries; entries that are not objects are ignored
            欄位的項目；不是物件的項目會忽略

    Returns:
        the line; empty if there are no entries
        這一行；沒有項目時為空字串
    """
    # 不是物件的項目是格式壞掉的（在 Anki 手改的），不當成過去的事送出
    entries = [item for item in items if isinstance(item, dict)]
    # entry_detail 附事件時間與說明（工作經歷、學歷）；職位的期間已經寫在裡面，不再接「到某天為止」
    current = [plain_text(entry_detail(name, item), 200) for item in entries if is_active(item)]
    past = []
    for item in entries:
        if is_active(item) or not (text := plain_text(entry_detail(name, item), 200)):
            continue
        ended = str(item.get("ended") or "")
        past.append(f"{text}（到 {ended} 為止）" if ended and name != "Company" else text)
    line = "、".join(text for text in current if text)
    if past:
        line += ("；" if line else "") + "以前：" + "、".join(past)
    return line


async def suggest(
    fields: dict[str, str], lists: dict[str, list[object]], today: date, *, mask_text: bool = True
) -> LLMPersonTopicsResult:
    """Ask the LLM to analyze this person and suggest topics; the returned text has the real names restored.

    請 LLM 分析這個人並推薦話題；回傳的文字已換回真正的稱呼。

    Args:
        fields: the card fields
            欄位
        lists: the parsed lists
            解析好的清單
        today: today's date
            今天
        mask_text: whether to mask names in the body text too; see :func:`build_prompt`
            內文也遮名字；見 ``build_prompt``

    Returns:
        the analysis and topics, with names restored
        分析與話題，名字已換回

    Raises:
        LLMGenerationError: if the LLM response does not match the format (the message names the model)
            若 LLM 回傳的格式不符（訊息帶模型名稱）
        LLMServiceError: if the LLM call fails (or a related error)
            若 LLM 呼叫失敗（或同類錯誤）
        TemplateEngineError: if the prompt template is broken
            若提示詞模板有誤
    """
    prompt, mask = build_prompt(fields, lists, today, mask_text=mask_text)
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
    """Restore the names in every string of the model.

    把模型裡所有文字的代號換回稱呼。
    """

    def walk(value: object) -> object:
        if isinstance(value, str):
            return mask.unmask(value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return type(model).model_validate(walk(model.model_dump()))
