"""Enum definitions for target languages.

目標語言相關的枚舉定義。
"""

import re
from enum import Enum

#: The basic BCP-47 form: a 2-3 letter language code, optionally with a 2-letter region code
#: (for example en, ja-JP, zh-TW).
#: BCP-47 基本形式：2-3 字母語言碼，可帶 2 字母地區碼（如 en、ja-JP、zh-TW）。
LANG_CODE_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z]{2})?$"
_LANG_CODE_RE = re.compile(LANG_CODE_PATTERN)


class TargetLanguage(str, Enum):
    """The target-language enumeration.

    目標語言的枚舉。

    Specifies the target language the FSM or LLM should use.

    用於指定 FSM 或 LLM 應該使用的目標語言。
    """
    EN_US = "en-US"
    JA_JP = "ja-JP"
    ZH_TW = "zh-TW"
    OTHER = "other"


def normalize_language_code(raw: str) -> str | None:
    """Validate and normalize a language code.

    驗證並正規化語言代碼（TG 的對話卡流程與對話卡的建立參數共用）。

    Shared by the Telegram dialog-card flow and the dialog card's create
    parameters. Surrounding whitespace is stripped first. Known
    :class:`TargetLanguage` enum values are matched first (case-insensitively),
    except ``other``, which is never accepted as input; other inputs are
    validated against the basic BCP-47 form and case-normalized (lowercase
    language code, uppercase region code, for example ``ja-jp`` → ``ja-JP``).

    TG 的對話卡流程與對話卡的建立參數共用。先去掉前後空白。
    優先比對 ``TargetLanguage`` 列舉的已知值（不分大小寫），但 ``other`` 不接受為輸入；
    其餘輸入以 BCP-47 基本形式驗證後正規化大小寫
    （語言碼小寫、地區碼大寫，如 ``ja-jp`` → ``ja-JP``）。

    Args:
        raw: the raw string
            原始字串

    Returns:
        the normalized language code, or ``None`` for empty or invalid input
        (callers should re-prompt instead of writing arbitrary text into the card)
        正規化後的語言代碼；輸入為空或不合法時回傳 ``None``
        （呼叫端應提示重新輸入，而非把任意文字寫進卡片）
    """
    candidate = raw.strip()
    if not candidate:
        return None
    # 已知列舉值：直接採用其標準寫法（'other' 除外——它是內部
    # 佔位語意，寫進卡片對 LLM 提示無意義，不接受手動輸入）
    for lang in TargetLanguage:
        if lang is TargetLanguage.OTHER:
            continue
        if candidate.lower() == lang.value.lower():
            return lang.value
    if not _LANG_CODE_RE.match(candidate):
        return None
    if "-" in candidate:
        lang_part, region_part = candidate.split("-", 1)
        return f"{lang_part.lower()}-{region_part.upper()}"
    return candidate.lower()
