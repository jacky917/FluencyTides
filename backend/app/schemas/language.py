"""目標語言相關的枚舉定義。

Enum definitions for target languages.
"""

from enum import Enum

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
