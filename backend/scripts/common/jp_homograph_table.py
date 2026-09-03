"""日文同表層多讀動詞表（掃母卡建構，判讀腳本與生卡腳本共用）。

Homograph table for Japanese verbs: kanji surfaces that carry more than one
reading across the master cards of one project. Shared by the judging
script and the generation scripts.

同一個漢字表層（汚す）可能屬於多張母卡、各自讀音不同（けがす / よごす）。
ES 用表層搜句子分不出讀音，必須靠語境判讀（計畫 §1.2）；本模組只負責
回答「哪些表層需要判讀、各有哪些候選讀音」。不落設定檔——母卡改動即
自動反映（計畫 §3.1）。

專案差異（母卡牌組、動詞欄位名）取自刪卡工具鏈的 ``ProjectProfile``，
本模組不寫死任何專案。
"""

import re
from dataclasses import dataclass, field

from app.infrastructure.anki.client import AnkiClient
from scripts.common.verb_lemma import canonical_verb_lemma
from scripts.local_anki.common.deletion.profiles import ProjectProfile

# 母卡欄位裡多個動詞的分隔符（與 generate_child_cards._parse_verb_field 一致）
_SEPARATORS = re.compile(r"[,、/・]")
# base[ruby] → ruby（``埋[う]まる`` → ``うまる``）。base 排除空白，否則
# ``聞[き]き 返[かえ]す`` 的中間送假名「き」會被當成 base 一起吃掉，讀音變成
# ``きかえす``（正確為 ``ききかえす``）；與 JP_VerbPair 的 _parse_verb_field
# 使用同一條規則。
# The base group excludes whitespace so okurigana between ruby groups is
# preserved.
_FURIGANA_TO_KANA = re.compile(r"[^\s\[\]]+\[([^\]]+)\]")
_HIRAGANA_ONLY = re.compile(r"^[ぁ-んー]+$")
# Anki 的 ruby 分隔空白（見 scripts/common/verb_lemma.py 頂部說明）
_WHITESPACE = re.compile(r"\s+")


def reading_of(part: str) -> str:
    """從母卡的帶標音表記導出讀音（純平假名）。

    Derive the hiragana reading from a furigana-annotated surface.

    ``埋[う]まる`` → ``うまる``、``聞[き]き 返[かえ]す`` → ``ききかえす``；
    純假名表記原樣回傳；無標音且含漢字（或含片假名）時回空字串——讀音無從
    得知，呼叫端據此跳過。
    Returns "" when the reading cannot be derived (kanji without furigana,
    or katakana surfaces).

    Args:
        part: 母卡欄位中的單一動詞表記。One verb entry from a master field.

    Returns:
        str: 讀音；取不到時為空字串。The reading, or "" when unavailable.
    """
    part = part.strip()
    if "[" in part:
        reading = _WHITESPACE.sub("", _FURIGANA_TO_KANA.sub(r"\1", part))
        return reading if _HIRAGANA_ONLY.match(reading) else ""
    part = _WHITESPACE.sub("", part)
    return part if _HIRAGANA_ONLY.match(part) else ""


@dataclass
class HomographEntry:
    """一個多讀表層。One multi-reading surface.

    Attributes:
        surface: 表層（去標音），如 ``汚す``。The kanji surface.
        readings: ``{讀音: [母卡 note id, …]}``。Reading → master note ids.
    """

    surface: str
    readings: dict[str, list[int]] = field(default_factory=dict)

    @property
    def candidates(self) -> list[str]:
        """候選讀音（排序穩定）。Sorted candidate readings."""
        return sorted(self.readings)


def build_homograph_table(notes: list, profile: ProjectProfile) -> dict[str, HomographEntry]:
    """從母卡 notes 建多讀表，只保留讀音數 ≥ 2 的表層。

    Build the homograph table from master notes, keeping only surfaces with
    two or more distinct readings.

    「同表層、同讀音、不同母卡」（繋がる 出現在兩張母卡）不算多讀，不收——
    讀音判讀對它們無效（計畫 §7.1）。

    Args:
        notes: ``AnkiClient.get_notes_info`` 回傳的母卡列表。Master notes.
        profile: 專案 profile（提供 ``master_verb_fields``）。Project profile.

    Returns:
        dict[str, HomographEntry]: ``{表層: entry}``。Surface → entry.
    """
    seen: dict[str, dict[str, set[int]]] = {}
    for note in notes:
        fields = getattr(note, "fields", None) or {}
        for key in profile.master_verb_fields:
            raw = fields.get(key, {})
            raw = raw.get("value", "") if isinstance(raw, dict) else str(getattr(raw, "value", "") or "")
            for part in _SEPARATORS.split(raw):
                part = part.strip()
                if not part:
                    continue
                surface = canonical_verb_lemma(part)
                reading = reading_of(part)
                if not surface or not reading:
                    continue
                seen.setdefault(surface, {}).setdefault(reading, set()).add(int(note.noteId))

    table: dict[str, HomographEntry] = {}
    for surface, readings in seen.items():
        if len(readings) >= 2:
            table[surface] = HomographEntry(
                surface=surface,
                readings={r: sorted(ids) for r, ids in sorted(readings.items())},
            )
    return table


async def load_homograph_table(anki_client: AnkiClient, profile: ProjectProfile) -> dict[str, HomographEntry]:
    """從 Anki 撈該專案全部母卡並建多讀表。

    Fetch every master note of the project from Anki and build the table.

    Args:
        anki_client: Anki 連線客戶端。Anki client.
        profile: 專案 profile。Project profile.

    Returns:
        dict[str, HomographEntry]: 多讀表。The homograph table.
    """
    note_ids = await anki_client.find_notes(f'"deck:{profile.master_deck}"')
    if not note_ids:
        return {}
    notes = await anki_client.get_notes_info(note_ids)
    return build_homograph_table(notes, profile)
