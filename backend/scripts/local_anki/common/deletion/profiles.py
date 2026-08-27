"""刪除/完整性/清理工具的專案描述子（ProjectProfile）。

Project profiles for the deletion / integrity / cleanup toolkit.

JP_VerbPair 與 JP_CoreVerb 的刪除工具共用同一套核心邏輯
（child_deleter / integrity / cleanup），兩專案的差異全部收斂在此檔的
ProjectProfile 欄位：模型名、母卡 JSON 欄位清單、牌組、媒體前綴與
verb_lemma 的抽取方式。

All differences between the JP_VerbPair and JP_CoreVerb deletion tools are
captured by the ProjectProfile fields here: model names, master JSON
fields, decks, media prefix and the verb-lemma extraction strategy.

注意：REGISTRY 是媒體保護的依據——`media_scan.collect_required_media`
以「所有已註冊專案」的引用聯集決定哪些媒體不可刪。新增卡片專案時
必須同步在此註冊，否則其媒體會被其他專案的清理工具視為孤兒。
The REGISTRY drives media protection: collect_required_media unions the
references of every registered project. A new card project MUST register
here, or its media will look orphaned to the other projects' tools.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from app.core.config import settings
from scripts.common.database.log_repository import (
    PROJECT_JP_CORE_VERB,
    PROJECT_JP_VERB_PAIR,
)

# 與 funnel.strip_furigana 同一條去標音規則（``見[み]る`` → ``見る``）。
# Same furigana-stripping rule as funnel.strip_furigana.
_FURIGANA_PATTERN = re.compile(r"\[.*?\]")


def _field_value(fields: dict, name: str) -> str:
    """從 AnkiConnect 原始 fields 字典取出欄位字串值。

    Extract a field's string value from a raw AnkiConnect fields dict.

    Args:
        fields: ``{欄位名: {"value": ..., "order": ...}}`` 形式的字典。
            Dict shaped like ``{name: {"value": ..., "order": ...}}``.
        name: 欄位名稱。Field name.

    Returns:
        str: 欄位值；欄位不存在時回傳空字串。The value, or "" if absent.
    """
    entry = fields.get(name, {})
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "")
    return str(getattr(entry, "value", "") or "")


def _verb_pair_lemma(cloze_fields: dict, master_fields: dict | None) -> str:
    """JP_VerbPair：從 Cloze 卡的 Verb_Pair_JSON 取實際使用的動詞原型。

    JP_VerbPair: read the actually-used verb lemma from the cloze card's
    Verb_Pair_JSON field.

    Args:
        cloze_fields: Cloze 卡 fields 原始字典。Raw cloze fields dict.
        master_fields: 母卡 fields（此專案用不到，介面統一而收）。Master
            fields (unused for this project; kept for a uniform interface).

    Returns:
        str: 動詞原型；解析失敗時回傳空字串。The lemma, or "" on failure.
    """
    raw = _field_value(cloze_fields, "Verb_Pair_JSON")
    if not raw:
        return ""
    try:
        vp = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    used_type = vp.get("used", "")
    if used_type == "intransitive":
        return vp.get("intransitive", "") or ""
    if used_type == "transitive":
        return vp.get("transitive", "") or ""
    return ""


def _core_verb_lemma(cloze_fields: dict, master_fields: dict | None) -> str:
    """JP_CoreVerb：從母卡 Word 欄去標音取得動詞原型。

    JP_CoreVerb: derive the lemma by stripping furigana from the master
    card's Word field.

    為什麼不用 Cloze 卡的 Verb_Analysis_JSON？其中的 ``word`` 在純假名
    動詞的情境下已退化為假名表記，與 DB 的 verb_lemma（母卡 Word 去標音）
    不保證一致；母卡 Word 才是生成時寫入 DB 的單一事實來源
    （generate_child_cards.py 的 ``strip_furigana(word_display)``）。
    The cloze card's Verb_Analysis_JSON ``word`` may have degraded to pure
    kana; the master Word field is the single source the generator used
    when writing verb_lemma to the DB.

    Args:
        cloze_fields: Cloze 卡 fields（此專案用不到）。Raw cloze fields
            (unused for this project).
        master_fields: 母卡 fields 原始字典；母卡已不存在時為 None。Raw
            master fields dict, or None when the master is gone.

    Returns:
        str: 動詞原型；母卡不存在或無 Word 時回傳空字串。The lemma, or ""
        when unavailable.
    """
    if not master_fields:
        return ""
    return _FURIGANA_PATTERN.sub("", _field_value(master_fields, "Word")).strip()


@dataclass(frozen=True)
class ProjectProfile:
    """單一卡片專案在刪除工具鏈中的全部差異點。

    Every project-specific knob used by the deletion toolkit.

    Attributes:
        project_key: generated_sentences_log.project 的值。DB project value.
        display_name: 報告輸出用的專案名稱。Human-readable project name.
        master_model: 母卡模型名。Master note model name.
        cloze_model: Cloze 子卡模型名。Cloze child note model name.
        context_model: Context 子卡模型名（兩專案共用 JP_Context_Dark）。
            Context child model (shared between the two projects).
        master_json_fields: 母卡上存放子卡紀錄的 JSON 欄位清單。Master
            JSON fields holding child-card records.
        root_deck: 根牌組（其下有 ::Master / ::Cloze / ::Context）。Root
            deck containing the Master/Cloze/Context subdecks.
        source_game: 媒體檔名前綴用的遊戲代號。Game id used as the media
            filename prefix.
        game_name_jp: scripts 表 source 欄的遊戲日文名（孤兒修復反查用）。
            Japanese game name in scripts.source, used by orphan repair.
        extract_verb_lemma: ``(cloze_fields, master_fields|None) -> lemma``
            的抽取函式。Lemma extraction hook.
    """
    project_key: str
    display_name: str
    master_model: str
    cloze_model: str
    context_model: str
    master_json_fields: tuple[str, ...]
    root_deck: str
    source_game: str
    game_name_jp: str
    extract_verb_lemma: Callable[[dict, dict | None], str] = field(compare=False)


def _core_verb_root_deck() -> str:
    """由 JP_CORE_VERB_MASTER_DECK 推導根牌組（去掉尾端 ``::Master``）。

    Derive the CoreVerb root deck from JP_CORE_VERB_MASTER_DECK by
    stripping the trailing ``::Master`` segment.

    Returns:
        str: 根牌組名稱。The root deck name.
    """
    master_deck = getattr(settings, "JP_CORE_VERB_MASTER_DECK", "日本語::核心動詞::Master")
    if master_deck.endswith("::Master"):
        return master_deck[: -len("::Master")]
    return master_deck


def build_registry() -> dict[str, ProjectProfile]:
    """組出全部專案的 profile 註冊表（每次呼叫即時讀 settings）。

    Build the full project registry, reading settings at call time.

    Returns:
        dict[str, ProjectProfile]: ``{project_key: profile}``。
    """
    jp_verb_pair = ProjectProfile(
        project_key=PROJECT_JP_VERB_PAIR,
        display_name="JP_VerbPair",
        master_model="JP_VerbPair_Master_Dark",
        cloze_model="JP_VerbPair_Cloze_Dark",
        context_model="JP_Context_Dark",
        master_json_fields=("Intransitive_Data_JSON", "Transitive_Data_JSON"),
        root_deck="日本語::自他動詞",
        source_game=settings.JP_VERB_PAIR_SOURCE_GAME,
        game_name_jp=settings.JP_VERB_PAIR_GAME_NAME_JP,
        extract_verb_lemma=_verb_pair_lemma,
    )
    jp_core_verb = ProjectProfile(
        project_key=PROJECT_JP_CORE_VERB,
        display_name="JP_CoreVerb",
        master_model="JP_CoreVerb_Master_Dark",
        cloze_model="JP_CoreVerb_Cloze_Dark",
        context_model="JP_Context_Dark",
        master_json_fields=("Word_Data_JSON",),
        root_deck=_core_verb_root_deck(),
        source_game=getattr(
            settings, "JP_CORE_VERB_SOURCE_GAME", settings.JP_VERB_PAIR_SOURCE_GAME
        ),
        game_name_jp=getattr(
            settings, "JP_CORE_VERB_GAME_NAME_JP", settings.JP_VERB_PAIR_GAME_NAME_JP
        ),
        extract_verb_lemma=_core_verb_lemma,
    )
    return {p.project_key: p for p in (jp_verb_pair, jp_core_verb)}


def get_profile(project_key: str) -> ProjectProfile:
    """取得單一專案的 profile。

    Fetch one project's profile.

    Args:
        project_key: 專案識別（log_repository.KNOWN_PROJECTS）。Project key.

    Returns:
        ProjectProfile: 對應的 profile。The matching profile.

    Raises:
        KeyError: project_key 未註冊時。If the key is not registered.
    """
    return build_registry()[project_key]
