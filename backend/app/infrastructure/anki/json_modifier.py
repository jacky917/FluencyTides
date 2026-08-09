"""
Anki JSON 欄位安全讀寫工具模組。

Utilities for safely reading and writing JSON array/object fields stored
inside Anki note fields.
"""

import json
import logging
import html
import re
from typing import Any, TYPE_CHECKING

from app.core.exceptions import AnkiFieldCorruptedError

if TYPE_CHECKING:
    from app.services.card_service import CardService

logger = logging.getLogger(__name__)

class AnkiJsonFieldManager:
    """提供針對 Anki 卡片中 JSON 陣列/物件欄位的安全讀寫工具類。

    Utility class for safe read/write access to JSON array/object fields in
    Anki cards.

    由於 Anki 有時會自動在欄位內容外層加上 HTML 標籤（例如編輯過後），
    此工具類負責在解析 JSON 前濾除這些雜訊，並提供快捷的陣列操作方法。

    Because Anki sometimes wraps field content in HTML tags (e.g. after
    editing), this class strips that noise before JSON parsing and offers
    convenient list-manipulation helpers.
    """

    @staticmethod
    def parse_field_string(field_str: str) -> list[Any]:
        """安全解析給定的字串為 JSON 陣列，過濾 HTML 標籤。

        Safely parse the given string into a JSON array, filtering out HTML
        tags; returns an empty list on failure.

        Args:
            field_str: Anki 欄位的原始字串內容。Raw Anki field string.

        Returns:
            解析出的 JSON 陣列；空欄位回傳空陣列。The parsed JSON list, or
            an empty list for an empty field.

        Raises:
            AnkiFieldCorruptedError: 欄位含內容但無法解析為 JSON 陣列時拋出，
                以避免後續寫入靜默清空既有資料（S001）。Raised when the field
                has content that cannot be parsed as a JSON list, preventing
                follow-up writes from silently wiping existing data (S001).
        """
        if not field_str:
            return []

        field_str = str(field_str).replace("<div>", "").replace("</div>", "").replace("<br>", "").replace("&nbsp;", " ").replace("\n", "").strip()

        # 嘗試修復舊版未 Escape 導致的 JSON 損毀 (向後相容舊資料)
        field_str = re.sub(r'class="\\&quot;(.*?)\\&quot;"', r'class=\"\1\"', field_str)
        field_str = re.sub(r'class="([^"]*?)"', r'class=\"\1\"', field_str)

        # 將 Anki 中的 HTML Entity 解析回正常字元 (必須在 Regex 修復之後執行)
        field_str = html.unescape(field_str)

        if not field_str.strip():
            return []
        try:
            items = json.loads(field_str)
        except json.JSONDecodeError as e:
            logger.error("JSON 解析失敗（欄位可能損毀）。字串前段: %s", field_str[:30])
            raise AnkiFieldCorruptedError(
                f"Anki 欄位 JSON 損毀，無法解析（前段: {field_str[:30]!r}）。"
                "為避免資料遺失已中止操作，請先手動修復該欄位。"
            ) from e
        if not isinstance(items, list):
            logger.error("解析結果並非 list 型別: %s", type(items).__name__)
            raise AnkiFieldCorruptedError(
                f"Anki 欄位內容為 {type(items).__name__} 而非 JSON 陣列，已中止操作。"
            )
        return items

    @classmethod
    async def safe_read_list(cls, card_service: "CardService", note_id: int, field_name: str) -> list[Any]:
        """從 Anki 讀取特定筆記的特定欄位並安全解析為 JSON 陣列。

        Read a specific field of a note from Anki and safely parse it as a
        JSON array.

        Args:
            card_service: 卡片服務實例。The CardService instance.
            note_id: 目標筆記 ID。Target note ID.
            field_name: 目標欄位名稱。Target field name.

        Returns:
            解析出的 JSON 陣列。The parsed JSON list.
        """
        note = await card_service.get_note(note_id)
        field_str = note["fields"].get(field_name, {}).get("value", "[]")
        return cls.parse_field_string(field_str)

    @staticmethod
    async def update_field(card_service: "CardService", note_id: int, field_name: str, data: Any) -> None:
        """將資料序列化為 JSON 字串並寫回 Anki 欄位。

        Serialize the data to a JSON string and write it back to the Anki
        field.

        Args:
            card_service: 卡片服務實例。The CardService instance.
            note_id: 目標筆記 ID。Target note ID.
            field_name: 目標欄位名稱。Target field name.
            data: 欲序列化寫入的資料。Data to serialize and store.
        """
        new_field_str = json.dumps(data, ensure_ascii=False)
        # 對 JSON 字串進行 HTML 轉義，避免 Anki 的富文本編輯器將內含的 HTML (如 <u>) 錯誤解析並損毀 JSON
        new_field_str = html.escape(new_field_str)
        await card_service.update_note_fields(note_id, {field_name: new_field_str})
        logger.debug("成功更新筆記 %d 的 JSON 欄位: %s", note_id, field_name)

    @classmethod
    async def append_to_list(cls, card_service: "CardService", note_id: int, field_name: str, item: Any) -> None:
        """附加元素至目標 JSON 陣列欄位尾端。

        Append an item to the end of the target JSON array field.

        Args:
            card_service: 卡片服務實例。The CardService instance.
            note_id: 目標筆記 ID。Target note ID.
            field_name: 目標欄位名稱。Target field name.
            item: 欲附加的元素。Item to append.
        """
        items = await cls.safe_read_list(card_service, note_id, field_name)
        items.append(item)
        await cls.update_field(card_service, note_id, field_name, items)

    @classmethod
    async def insert_to_list(cls, card_service: "CardService", note_id: int, field_name: str, index: int, item: Any) -> None:
        """在特定索引位置插入元素至目標 JSON 陣列欄位。

        Insert an item at the given index of the target JSON array field;
        appends when the index is out of range.

        Args:
            card_service: 卡片服務實例。The CardService instance.
            note_id: 目標筆記 ID。Target note ID.
            field_name: 目標欄位名稱。Target field name.
            index: 插入位置索引。Insertion index.
            item: 欲插入的元素。Item to insert.
        """
        items = await cls.safe_read_list(card_service, note_id, field_name)
        if 0 <= index <= len(items):
            items.insert(index, item)
        else:
            items.append(item)
        await cls.update_field(card_service, note_id, field_name, items)

    @classmethod
    async def remove_from_list(cls, card_service: "CardService", note_id: int, field_name: str, index: int) -> bool:
        """移除目標 JSON 陣列欄位中特定索引的元素。

        Remove the item at the given index from the target JSON array field.

        Args:
            card_service: 卡片服務實例。The CardService instance.
            note_id: 目標筆記 ID。Target note ID.
            field_name: 目標欄位名稱。Target field name.
            index: 欲移除的索引。Index to remove.

        Returns:
            bool: 成功移除回傳 True；索引超出範圍回傳 False。True if
                removed; False when the index is out of range.
        """
        items = await cls.safe_read_list(card_service, note_id, field_name)
        if 0 <= index < len(items):
            items.pop(index)
            await cls.update_field(card_service, note_id, field_name, items)
            return True
        logger.warning("欲刪除的索引 %d 超出陣列範圍", index)
        return False
