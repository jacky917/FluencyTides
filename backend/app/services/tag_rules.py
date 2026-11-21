"""Tag rules: which tags are system tags and what a user-added tag must look like.

標籤規則：哪些是系統標籤、使用者加的標籤要符合什麼格式。

See plan ``docs/archive/trilingual_search_tags_FEAT_2026-10-30.md`` §5.1.

見計畫書 ``docs/archive/trilingual_search_tags_FEAT_2026-10-30.md`` §5.1。

System tags are managed by code (tags always added at card creation, markers used by scripts or flows, and toggles);
they are never suggested and cannot be added by hand at card creation.

系統標籤由程式管理（建卡固定加的、腳本或流程標記用的、開關用的），不推薦、也不能在建卡時手動加。
"""

from __future__ import annotations

import re
from functools import cache

from app.core.exceptions import TagInvalidError
from app.schemas.person_profile import HIDDEN_TAG_PREFIX, TEXT_UNMASKED_TAG

#: Tags added by code or Anki itself; each handler's ``CREATE_TAGS`` are collected separately from the registry.
#: 程式或 Anki 自己加的標籤（各 handler 的 ``CREATE_TAGS`` 另外從註冊表收集）。
_FIXED_SYSTEM_TAGS = frozenset({
    "HandlerGenerated",
    "TelegramBot",
    "Sample",
    "ManualCreated",
    "Auto_Generated",
    "Auto_Migrated",
    "Migrated",
    "Notion_Sync",
    "leech",
    "__E2E_TEMP__",
    "Expression_Correction",
    "MicroPoint",
    "Speaking_Coach",
    "FluencyTides::Generated",
    TEXT_UNMASKED_TAG,
})

#: Tags starting with any of these are system tags (for example ``LLM::(claude-code)opus@high``, which records the
#: generating model, and ``人脈卡隱藏::Appearance``, which hides a person card's text field).
#: 以這些開頭的標籤都是系統標籤（例如記錄生成模型的 ``LLM::(claude-code)opus@high``、人脈卡隱藏文字欄位的
#: ``人脈卡隱藏::Appearance``）。
SYSTEM_TAG_PREFIXES = ("LLM::", HIDDEN_TAG_PREFIX)

#: The maximum number of tags given at card creation.
#: 建卡時最多加幾個標籤。
MAX_TAGS = 20
#: The maximum length of each tag, in characters.
#: 每個標籤最長幾個字。
MAX_TAG_LENGTH = 100

_WHITESPACE = re.compile(r"\s")


@cache
def _folded_system_tags() -> frozenset[str]:
    """Return the fixed system tags plus every handler's creation tags, case-folded.

    回傳固定的系統標籤加上各 handler 建卡固定加的標籤，轉成小寫。

    The registry is read only on first use, to avoid a circular import.

    第一次用到時才讀註冊表，避免循環 import。
    """
    from app.services.task_handlers.registry import handler_registry

    create_tags = {tag for handler in handler_registry.all_handlers() for tag in handler.CREATE_TAGS}
    return frozenset(tag.casefold() for tag in _FIXED_SYSTEM_TAGS | create_tags)


_FOLDED_PREFIXES = tuple(prefix.casefold() for prefix in SYSTEM_TAG_PREFIXES)


def is_system_tag(tag: str) -> bool:
    """Return whether ``tag`` is a system tag.

    回傳是否為系統標籤。

    The check is case-insensitive, because Anki tags are case-insensitive themselves.

    不區分大小寫，Anki 的標籤本身就不分大小寫。

    Args:
        tag: the tag to check
            要檢查的標籤

    Returns:
        ``True`` if ``tag`` is a fixed system tag, a handler's creation tag, or starts with a system prefix
        若 ``tag`` 是固定系統標籤、handler 建卡標籤，或以系統前綴開頭，則為 ``True``
    """
    folded = tag.casefold()
    return folded in _folded_system_tags() or folded.startswith(_FOLDED_PREFIXES)


def normalize_tags(tags: list[str]) -> list[str]:
    """Normalize user-added tags: trim, skip blanks, and de-duplicate.

    整理使用者加的標籤：去前後空白、略過空的、去重。

    De-duplication is case-insensitive and keeps the first spelling and the original order.

    去重不分大小寫，保留第一次的寫法與順序。

    Args:
        tags: the raw tags the user gave
            使用者給的原始標籤

    Returns:
        the cleaned tags; empty if every tag was blank
        整理後的標籤；全是空白時為空串列

    Raises:
        TagInvalidError: if a tag contains whitespace, is a system tag, or exceeds the length limit, or if there are
            too many tags; the message names the offending one
            若含空白、是系統標籤、超過長度或數量上限；訊息指出是哪一個
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = raw.strip()
        if not tag:
            continue
        if _WHITESPACE.search(tag):
            raise TagInvalidError(f"標籤「{tag}」含空白；Anki 的標籤不能有空白（可以用 _ 或 :: 代替）")
        if len(tag) > MAX_TAG_LENGTH:
            raise TagInvalidError(f"標籤「{tag[:20]}…」超過 {MAX_TAG_LENGTH} 字")
        if is_system_tag(tag):
            raise TagInvalidError(f"「{tag}」是系統標籤，由程式管理，不能手動加")
        if tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        result.append(tag)
    if len(result) > MAX_TAGS:
        raise TagInvalidError(f"標籤最多 {MAX_TAGS} 個，收到 {len(result)} 個")
    return result
