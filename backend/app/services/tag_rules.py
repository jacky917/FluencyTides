"""標籤規則：哪些是系統標籤、使用者加的標籤要符合什麼格式（計畫書 ``docs/wip/trilingual_search_tags_FEAT_2026-10-30.md`` §5.1）。

Tag rules: which tags are system tags and what a user-added tag must look like.

系統標籤由程式管理（建卡固定加的、腳本或流程標記用的、開關用的），不推薦、也不能在建卡時手動加。
"""

from __future__ import annotations

import re
from functools import cache

from app.core.exceptions import TagInvalidError
from app.schemas.person_profile import TEXT_UNMASKED_TAG

#: 程式或 Anki 自己加的標籤（各 handler 的 ``CREATE_TAGS`` 另外從註冊表收集）。
#: Tags added by code or Anki itself; handlers' ``CREATE_TAGS`` are collected separately.
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

#: 以這些開頭的標籤都是系統標籤（例如記錄生成模型的 ``LLM::(claude-code)opus@high``）。
SYSTEM_TAG_PREFIXES = ("LLM::",)

#: 建卡時最多加幾個標籤、每個最長幾個字。Limits on tags given at creation.
MAX_TAGS = 20
MAX_TAG_LENGTH = 100

_WHITESPACE = re.compile(r"\s")


@cache
def _system_tags() -> frozenset[str]:
    """固定的系統標籤加上各 handler 建卡固定加的標籤（第一次用到時才讀註冊表，避免循環 import）。"""
    from app.services.task_handlers.registry import handler_registry

    create_tags = {tag for handler in handler_registry.all_handlers() for tag in handler.CREATE_TAGS}
    return _FIXED_SYSTEM_TAGS | create_tags


def is_system_tag(tag: str) -> bool:
    """是否為系統標籤（不區分大小寫，Anki 的標籤本身就不分大小寫）。Whether the tag is a system tag."""
    folded = tag.casefold()
    return any(folded == t.casefold() for t in _system_tags()) or any(
        folded.startswith(prefix.casefold()) for prefix in SYSTEM_TAG_PREFIXES
    )


def normalize_tags(tags: list[str]) -> list[str]:
    """整理使用者加的標籤：去前後空白、略過空的、去重（不分大小寫，保留第一次的寫法與順序）。

    Normalise user tags: trim, skip blanks, de-duplicate case-insensitively.

    Raises:
        TagInvalidError: 含空白、是系統標籤、超過長度或數量上限；訊息指出是哪一個。
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
