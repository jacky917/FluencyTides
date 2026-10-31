"""目標語言相關的枚舉定義。

Enum definitions for target languages.
"""

import re
from enum import Enum

#: BCP-47 基本形式：2-3 字母語言碼，可帶 2 字母地區碼（如 en、ja-JP、zh-TW）。
LANG_CODE_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z]{2})?$"
_LANG_CODE_RE = re.compile(LANG_CODE_PATTERN)


class TargetLanguage(str, Enum):
    """目標語言的枚舉。

    Target-language enumeration.

    用於指定 FSM 或 LLM 應該使用的目標語言。

    Specifies the target language the FSM or LLM should use.
    """
    EN_US = "en-US"
    JA_JP = "ja-JP"
    ZH_TW = "zh-TW"
    OTHER = "other"


def normalize_language_code(raw: str) -> str | None:
    """驗證並正規化語言代碼（TG 的對話卡流程與對話卡的建立參數共用）。

    Validate and normalize a language code.

    優先比對 :class:`TargetLanguage` 列舉的已知值（不分大小寫）；
    其餘輸入以 BCP-47 基本形式驗證後正規化大小寫
    （語言碼小寫、地區碼大寫，如 ``ja-jp`` → ``ja-JP``）。

    Known :class:`TargetLanguage` enum values are matched first
    (case-insensitively); other inputs are validated against the basic
    BCP-47 form and case-normalized (lowercase language, uppercase region,
    e.g. ``ja-jp`` → ``ja-JP``).

    Args:
        raw: 原始字串。The raw string.

    Returns:
        正規化後的語言代碼；輸入不合法時回傳 ``None``
        （呼叫端應提示重新輸入，而非把任意文字寫進卡片）。
        The normalized language code, or ``None`` for invalid input
        (callers should re-prompt instead of writing arbitrary text
        into the card).
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
