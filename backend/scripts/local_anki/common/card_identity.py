"""卡片 JSON 的「身分證」讀寫工具（``cardId`` + ``noteId``）。

Read/write helpers for the card JSON identity pair (``cardId`` +
``noteId``).

身分是卡片在 JSON 與 Anki 之間的唯一對應依據。它**成對存在**——兩個欄位
必須同時具備才算有效，缺一即視為損毀。存在判斷完全不看 ``Prompt``，因此
編輯卡片內容不會再讓匯入腳本誤判成新卡（見
``docs/archive/card_identity_writeback_FEAT_2026-08-11.md`` §3.2）。

The identity is the only link between a card in JSON and a note in Anki.
It is a **pair** — both fields must be present to count as valid, and a
missing half means corruption. Existence checks never look at ``Prompt``,
so editing card content no longer makes the importer treat it as a new
card (see the plan document, §3.2).

身分放在卡片物件**頂層**而非 ``fields`` 內：``fields`` 維持與 Anki note
model 一一對應的語意，而 ``noteId`` 本來就不是 model 欄位。

The identity lives at the **top level** of the card object rather than
inside ``fields``: ``fields`` mirrors the Anki note model one-to-one, and
``noteId`` is not a model field at all.

本模組被各 model 的 ``import_cards.py`` 與 ``clear_identity.py`` 共用，集中管理
JSON 的讀寫格式與原子替換，避免兩處各寫一份而逐漸分歧。

Shared by each model's ``import_cards.py`` and ``clear_identity.py`` so the JSON
read/write format and atomic replacement live in exactly one place.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

#: JSON 卡片物件中儲存 Anki 卡片 ID 的鍵名
KEY_CARD_ID = "cardId"
#: JSON 卡片物件中儲存 Anki note ID 的鍵名
KEY_NOTE_ID = "noteId"
#: 身分欄位在卡片物件中的標準位置（緊接 modelName 之後）
_CANONICAL_KEY_ORDER = ("deckName", "modelName", KEY_CARD_ID, KEY_NOTE_ID, "tags", "fields")


def read_identity(card: dict[str, Any]) -> tuple[str | None, int | None]:
    """讀取卡片物件的身分對。

    Read the identity pair from a card object.

    Args:
        card: JSON 中的單張卡片物件。A single card object from the JSON file.

    Returns:
        ``(cardId, noteId)``；缺漏或型別不符的一方回傳 ``None``。
        ``(cardId, noteId)``; either half is ``None`` when missing or of an
        unexpected type.
    """
    raw_card_id = card.get(KEY_CARD_ID)
    raw_note_id = card.get(KEY_NOTE_ID)

    card_id = str(raw_card_id).strip() if isinstance(raw_card_id, str) and raw_card_id.strip() else None
    # noteId 允許以字串形式手寫（使用者從 Anki 複製貼上時常見），一律轉為 int
    # noteId may be hand-written as a string (common when pasting from Anki);
    # normalise to int.
    note_id: int | None = None
    if isinstance(raw_note_id, bool):
        note_id = None  # bool 是 int 的子類，明確排除 / bool subclasses int; exclude
    elif isinstance(raw_note_id, int):
        note_id = raw_note_id
    elif isinstance(raw_note_id, str) and raw_note_id.strip().isdigit():
        note_id = int(raw_note_id.strip())

    return card_id, note_id


def identity_state(card: dict[str, Any]) -> str:
    """判斷卡片的身分完整度。

    Classify how complete a card's identity is.

    Args:
        card: JSON 中的單張卡片物件。A single card object from the JSON file.

    Returns:
        ``"complete"``（兩者皆有）、``"absent"``（兩者皆無）或
        ``"partial"``（只有其一，視為損毀）。One of ``"complete"`` (both
        present), ``"absent"`` (neither present), or ``"partial"`` (only one
        present, treated as corrupted).
    """
    card_id, note_id = read_identity(card)
    if card_id and note_id:
        return "complete"
    if not card_id and not note_id:
        return "absent"
    return "partial"


def set_identity(card: dict[str, Any], card_id: str, note_id: int) -> bool:
    """寫入身分對，並把鍵排到標準位置。

    Write the identity pair and move the keys to their canonical position.

    Args:
        card: 欲寫入的卡片物件（就地修改）。The card object to modify in place.
        card_id: Anki 卡片 ID。The Anki card ID.
        note_id: Anki note ID。The Anki note ID.

    Returns:
        內容是否真的改變；未改變時呼叫端可略過寫檔。Whether anything actually
        changed; callers can skip the file write when it did not.
    """
    if card.get(KEY_CARD_ID) == card_id and card.get(KEY_NOTE_ID) == note_id:
        return False

    card[KEY_CARD_ID] = card_id
    card[KEY_NOTE_ID] = note_id
    _reorder_keys(card)
    return True


def clear_identity(card: dict[str, Any]) -> bool:
    """移除卡片物件的身分對。

    Remove the identity pair from a card object.

    Args:
        card: 欲清除的卡片物件（就地修改）。The card object to modify in place.

    Returns:
        是否真的移除了任何鍵。Whether any key was actually removed.
    """
    removed = False
    for key in (KEY_CARD_ID, KEY_NOTE_ID):
        if key in card:
            del card[key]
            removed = True
    return removed


def _reorder_keys(card: dict[str, Any]) -> None:
    """把卡片物件的鍵重排為標準順序（就地）。

    Reorder a card object's keys into the canonical order, in place.

    未列於標準順序中的鍵會保留在最後，順序不變——不擅自丟棄使用者自行
    加入的欄位。

    Keys not in the canonical list are kept at the end in their original
    order; user-added fields are never silently dropped.

    Args:
        card: 欲重排的卡片物件。The card object to reorder.
    """
    ordered = {k: card[k] for k in _CANONICAL_KEY_ORDER if k in card}
    ordered.update({k: v for k, v in card.items() if k not in ordered})
    card.clear()
    card.update(ordered)


def load_cards(file_path: Path) -> list[dict[str, Any]]:
    """讀取卡片 JSON 檔。

    Load a card JSON file.

    Args:
        file_path: JSON 檔案路徑。Path to the JSON file.

    Returns:
        卡片物件列表。The list of card objects.

    Raises:
        ValueError: 檔案頂層不是陣列時拋出。Raised when the top level is not
            a JSON array.
    """
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{file_path.name} 的頂層必須是 JSON 陣列，實際為 {type(data).__name__}")
    return data


def save_cards(file_path: Path, cards: list[dict[str, Any]]) -> None:
    """以原子替換寫回卡片 JSON 檔。

    Write the card JSON file back using an atomic replacement.

    先寫入同目錄的暫存檔再 ``os.replace``，避免中途中斷留下半截 JSON——
    ``jsons/`` 已列入 ``.gitignore``，這些手寫內容沒有版控可回復。

    Writes to a temporary file in the same directory and then calls
    ``os.replace``, so an interrupted run cannot leave a truncated JSON:
    ``jsons/`` is git-ignored, and this hand-written content has no version
    control to fall back on.

    格式與既有檔案一致（``indent=2``、``ensure_ascii=False``、結尾換行），
    讓 diff 只出現在真正改動的欄位上。

    The format matches the existing files (``indent=2``,
    ``ensure_ascii=False``, trailing newline) so diffs show only the fields
    that actually changed.

    Args:
        file_path: 目標 JSON 檔案路徑。Target JSON file path.
        cards: 欲寫入的卡片物件列表。The card objects to write.
    """
    payload = json.dumps(cards, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=f".{file_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            # 先落盤再替換：os.replace 只保證「不會出現半截檔案」，不保證內容
            # 已寫入磁碟。jsons/ 未進版控，若斷電後替換完成而內容是空的，就無從復原。
            # Flush before replacing: os.replace only guarantees no partial file,
            # not that the bytes reached the disk. jsons/ has no version control, so
            # a post-power-loss empty file would be unrecoverable.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, file_path)
    except BaseException:
        # 失敗時清掉暫存檔，避免在 jsons/ 留下垃圾
        # Remove the temp file on failure so no debris is left in jsons/.
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
