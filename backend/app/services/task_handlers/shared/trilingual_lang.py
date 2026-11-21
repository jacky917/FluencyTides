"""Language constants and mappings for trilingual speaking cards (Speaking_Trilingual_Dark); single source of truth.

三語口說卡（Speaking_Trilingual_Dark）的語言常數與映射（單一事實來源）。

The handler (field reads and writes) and the TG bot (STT, TTS, and scoring prompt selection) share this module,
so language mappings are not scattered. Cards have **no Target_Language field**: the language always comes
from the field-name suffix (``Recordings_JA``) or the language code in the deep link's index slot.

handler（欄位讀寫）與 TG bot（STT/TTS/評分提示詞選檔）共用本模組，
避免語言映射散落多處。卡片**不設 Target_Language 欄位**——語言一律由
欄位名後綴（``Recordings_JA``）或深連結 index 槽位的語言碼決定。
"""

from __future__ import annotations

#: The supported language codes (their order is the display order on the card and in reports).
#: 支援的語言碼（順序即卡面/報告的顯示順序）
LANG_CODES: tuple[str, ...] = ("ZH", "JA", "EN")

#: Language code to the BCP-47 language tag used by STT and TTS.
#: 語言碼 → STT / TTS 用的 BCP-47 語言標籤
LANG_TO_LOCALE: dict[str, str] = {
    "ZH": "zh-TW",
    "JA": "ja-JP",
    "EN": "en-US",
}

#: Language code to the LLM prompt j2 template (under templates/prompts/anki/; each writes its system prompt
#: in that language to avoid cross-language hallucination).
#: 語言碼 → LLM 提示詞 j2 樣板（templates/prompts/anki/ 下，
#: 各自以該語言撰寫系統提示詞，避免跨語言幻覺）
LANG_TEMPLATE_MAP: dict[str, str] = {
    "ZH": "Speaking_Trilingual_ZH.j2",
    "JA": "Speaking_Trilingual_JA.j2",
    "EN": "Speaking_Trilingual_EN.j2",
}

#: Language code to display name (for bot messages and section names).
#: 語言碼 → 顯示名（bot 訊息/section 名用）
LANG_DISPLAY: dict[str, str] = {
    "ZH": "中",
    "JA": "日",
    "EN": "英",
}


def recordings_field(lang: str) -> str:
    """Return the Recordings field name for a language code, for example ``Recordings_JA``.

    語言碼 → Recordings 欄位名（如 ``Recordings_JA``）。

    Args:
        lang: the language code (ZH, JA, EN)
            語言碼（ZH/JA/EN）

    Returns:
        the Recordings field name
        Recordings 欄位名
    """
    return f"Recordings_{lang}"


def references_field(lang: str) -> str:
    """Return the References field name for a language code, for example ``References_JA``.

    語言碼 → References 欄位名（如 ``References_JA``）。

    Args:
        lang: the language code (ZH, JA, EN)
            語言碼（ZH/JA/EN）

    Returns:
        the References field name
        References 欄位名
    """
    return f"References_{lang}"


def lang_from_field(field_name: str) -> str | None:
    """Parse the language code from a field-name suffix.

    由欄位名後綴解析語言碼。

    Args:
        field_name: the field name, for example ``Recordings_JA`` or ``References_EN``
            欄位名（如 ``Recordings_JA`` / ``References_EN``）

    Returns:
        the language code, or ``None`` for a field without a suffix (an existing Speaking_Coach_Dark field);
        callers use this to branch between the new and old card paths
        語言碼；無後綴（Speaking_Coach_Dark 既有欄位）回傳 ``None``——呼叫端以此分流新舊卡片路徑
    """
    for lang in LANG_CODES:
        if field_name.endswith(f"_{lang}"):
            return lang
    return None


def is_lang_code(value: str) -> bool:
    """Check whether a deep link's index slot is a language code (the Prompt_Audios language path).

    判斷深連結 index 槽位是否為語言碼（Prompt_Audios 語言路徑）。

    Args:
        value: the value of the deep link's index slot
            深連結 index 槽位值

    Returns:
        whether it is a valid language code
        是否為合法語言碼
    """
    return value in LANG_CODES
