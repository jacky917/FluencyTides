"""三語口說卡（Speaking_Trilingual_Dark）的語言常數與映射（單一事實來源）。

handler（欄位讀寫）與 TG bot（STT/TTS/評分提示詞選檔）共用本模組，
避免語言映射散落多處。卡片**不設 Target_Language 欄位**——語言一律由
欄位名後綴（``Recordings_JA``）或深連結 index 槽位的語言碼決定。

Language constants and mappings for trilingual speaking cards
(Speaking_Trilingual_Dark) — single source of truth.

Shared by the handler (field I/O) and the Telegram bot (STT/TTS/scoring
prompt selection) so language mappings never scatter. Cards have no
Target_Language field; the language always comes from the field-name
suffix (``Recordings_JA``) or the deep-link index slot's language code.
"""

from __future__ import annotations

#: 支援的語言碼（順序即卡面/報告的顯示順序）
LANG_CODES: tuple[str, ...] = ("ZH", "JA", "EN")

#: 語言碼 → STT / TTS 用的 BCP-47 語言標籤
LANG_TO_LOCALE: dict[str, str] = {
    "ZH": "zh-TW",
    "JA": "ja-JP",
    "EN": "en-US",
}

#: 語言碼 → LLM 提示詞 j2 樣板（templates/prompts/anki/ 下，
#: 各自以該語言撰寫系統提示詞，避免跨語言幻覺）
LANG_TEMPLATE_MAP: dict[str, str] = {
    "ZH": "Speaking_Trilingual_ZH.j2",
    "JA": "Speaking_Trilingual_JA.j2",
    "EN": "Speaking_Trilingual_EN.j2",
}

#: 語言碼 → 顯示名（bot 訊息/section 名用）
LANG_DISPLAY: dict[str, str] = {
    "ZH": "中",
    "JA": "日",
    "EN": "英",
}


def recordings_field(lang: str) -> str:
    """語言碼 → Recordings 欄位名（如 ``Recordings_JA``）。

    Language code to Recordings field name (e.g. ``Recordings_JA``).

    Args:
        lang: 語言碼（ZH/JA/EN）。Language code (ZH/JA/EN).

    Returns:
        Recordings 欄位名。The Recordings field name.
    """
    return f"Recordings_{lang}"


def references_field(lang: str) -> str:
    """語言碼 → References 欄位名（如 ``References_JA``）。

    Language code to References field name (e.g. ``References_JA``).

    Args:
        lang: 語言碼（ZH/JA/EN）。Language code (ZH/JA/EN).

    Returns:
        References 欄位名。The References field name.
    """
    return f"References_{lang}"


def lang_from_field(field_name: str) -> str | None:
    """由欄位名後綴解析語言碼。

    Parse the language code from a field-name suffix.

    Args:
        field_name: 欄位名（如 ``Recordings_JA`` / ``References_EN``）。
            Field name, e.g. ``Recordings_JA`` / ``References_EN``.

    Returns:
        str | None: 語言碼；無後綴（Speaking_Coach_Dark 既有欄位）回傳
        ``None``——呼叫端以此分流新舊卡片路徑。The language code, or
        ``None`` for suffix-less legacy Speaking_Coach_Dark fields —
        callers use this to branch between new/old card paths.
    """
    for lang in LANG_CODES:
        if field_name.endswith(f"_{lang}"):
            return lang
    return None


def is_lang_code(value: str) -> bool:
    """判斷深連結 index 槽位是否為語言碼（Prompt_Audios 語言路徑）。

    Check whether the deep-link index slot is a language code (the
    Prompt_Audios language path).

    Args:
        value: 深連結 index 槽位值。Deep-link index slot value.

    Returns:
        是否為合法語言碼。True if it is a valid language code.
    """
    return value in LANG_CODES
