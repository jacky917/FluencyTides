"""Keep the audio attached to reference answers when an importer rewrites References.

匯入腳本改寫 References 時，保留參考答案上已掛的音檔。

The Telegram bot stores the audio a user uploads for a reference answer
inside that answer's ``audios`` list in the ``References*`` JSON field
(``speaking_*_handler.add_audio``). The card JSON files the importers
read never contain those entries, so ``--update-existing`` used to
overwrite the field and silently drop every audio link, leaving the
media files orphaned.

TG bot 把使用者為參考答案上傳的音檔，存在 ``References*`` JSON 欄位中該筆答案的 ``audios`` 串列
（``speaking_*_handler.add_audio``）。匯入腳本讀的卡片 JSON 檔從來沒有這些項目，所以 ``--update-existing``
以前會整欄覆寫，靜默丟掉所有音檔關聯，媒體檔也變成孤兒。

:func:`preserve_reference_audios` merges the audios already in Anki into
the new field values, matching answers by their ``content``. An answer
with audio that no longer appears in the new JSON (for example after a
typo fix) is kept at the end of the list with its audio, and a warning
asks the user to merge it by hand; the rest of the update still goes
through, and no audio is dropped.

``preserve_reference_audios`` 以 ``content`` 比對答案，把 Anki 裡已有的音檔併入新的欄位值。
有音檔的答案若不在新的 JSON 裡（例如改了錯字），會連同音檔保留在清單最後，並記警告請使用者手動合併；
其餘的更新照常寫入，音檔不會遺失。
"""

from __future__ import annotations

import html
import json
import logging
from typing import Any

from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

logger = logging.getLogger(__name__)


def merge_reference_audios(
    existing: list[Any], new: list[Any]
) -> tuple[list[Any], list[str]]:
    """Return ``new`` with the audios of matching ``existing`` answers merged in.

    回傳併入了 ``existing`` 中對應答案音檔的 ``new``。

    Answers are matched by their ``content`` text. The existing audios of
    a sentence go to the first new answer with that text only, so a
    sentence listed twice does not get its audio twice; a file the new
    answer already lists is not added again. Existing answers with audio
    that match nothing are appended to the end unchanged. Items that are
    not dicts are copied as they are.

    以 ``content`` 文字比對答案。一句的既有音檔只加到第一個同文字的新答案，同一句列兩次時音檔不會重複；
    新答案已列出的檔案不再加入。找不到對應的帶音檔既有答案，原樣接在清單最後。不是字典的項目原樣複製。

    Args:
        existing: the answers currently stored in Anki
            目前存在 Anki 的答案
        new: the answers from the card JSON file; not modified
            卡片 JSON 檔中的答案；不會被修改

    Returns:
        a pair of the merged answers and the ``content`` of every existing
        answer with audio that had no match and was appended (empty when
        every audio found its answer)
        合併後的答案，以及找不到對應、因此接在最後的帶音檔既有答案 ``content``（音檔都找到對應時為空串列）
    """
    with_audio = [
        item for item in existing
        if isinstance(item, dict) and isinstance(item.get("audios"), list) and item["audios"]
    ]
    audios_by_content: dict[str, list[dict]] = {}
    for item in with_audio:
        audios_by_content.setdefault(str(item.get("content", "")), []).extend(item["audios"])

    merged: list[Any] = []
    matched: set[str] = set()
    for item in new:
        if not isinstance(item, dict):
            merged.append(item)
            continue
        content = str(item.get("content", ""))
        carried = audios_by_content.get(content) if content not in matched else None
        if not carried:
            merged.append(dict(item))
            continue
        matched.add(content)
        own = [a for a in item.get("audios") or [] if isinstance(a, dict)]
        own_files = {a.get("audio") for a in own}
        merged.append({**item, "audios": own + [a for a in carried if a.get("audio") not in own_files]})

    kept = [item for item in with_audio if str(item.get("content", "")) not in matched]
    merged.extend(dict(item) for item in kept)
    return merged, [str(item.get("content", "")) for item in kept]


async def preserve_reference_audios(
    client: Any,
    note_id: int,
    update_fields: dict[str, str],
    reference_fields: tuple[str, ...],
    present_keys: set[str],
    card_label: str,
) -> dict[str, str]:
    """Return ``update_fields`` adjusted so no reference audio is lost.

    回傳調整過的 ``update_fields``，確保參考答案的音檔不會遺失。

    For each reference field:

    - not written in the card JSON (only filled with ``"[]"`` by the
      importer): removed from the update, so Anki keeps its value;
    - written in the JSON: the audios already in Anki are merged in, and
      answers with audio missing from the JSON are kept at the end with a
      warning;
    - the value in Anki cannot be parsed: removed from the update with a
      warning.

    對每個參考答案欄位：

    - 卡片 JSON 沒寫（只是匯入腳本補上的 ``"[]"``）：從更新中移除，Anki 保留原值；
    - JSON 有寫：併入 Anki 裡已有的音檔，不在 JSON 裡的帶音檔答案保留在最後並記警告；
    - Anki 裡的值無法解析：從更新中移除並記警告。

    Args:
        client: the AnkiConnect client
            AnkiConnect 客戶端
        note_id: the note being updated
            要更新的筆記
        update_fields: the serialized field values about to be written; not
            modified
            準備寫入的序列化欄位值；不會被修改
        reference_fields: the names of the reference JSON fields
            參考答案 JSON 欄位的名稱
        present_keys: the field names actually written in the card JSON
            卡片 JSON 實際寫了的欄位名稱
        card_label: the card label used in log messages
            log 訊息中使用的卡片標籤

    Returns:
        the field values to write
        要寫入的欄位值

    Raises:
        AnkiConnectError: if the current note cannot be read from Anki
            若無法從 Anki 讀取目前的筆記
    """
    result = dict(update_fields)
    targets = [key for key in reference_fields if key in result]
    if not targets:
        return result

    notes = await client.get_notes_info([note_id])
    current = notes[0].fields if notes else {}
    for key in targets:
        if key not in present_keys:
            del result[key]
            continue
        raw = current.get(key, {}).get("value", "") if isinstance(current.get(key), dict) else ""
        try:
            existing = AnkiJsonFieldManager.parse_field_string(raw) if raw.strip() else []
            new = AnkiJsonFieldManager.parse_field_string(result[key])
        except AnkiFieldCorruptedError as e:
            logger.warning(f"⚠️ [{card_label}] {key} 無法解析（{e}），這個欄位不更新。")
            del result[key]
            continue
        merged, kept = merge_reference_audios(existing, new)
        if kept:
            logger.warning(
                f"⚠️ [{card_label}] {key} 有 {len(kept)} 句帶音檔的參考答案不在 JSON 裡，"
                f"已連同音檔保留在清單最後，請手動合併：{'、'.join(c[:20] for c in kept)}"
            )
        result[key] = html.escape(json.dumps(merged, ensure_ascii=False))
    return result
