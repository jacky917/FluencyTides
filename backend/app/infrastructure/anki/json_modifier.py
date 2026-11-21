"""Utilities for safely reading and writing JSON array/object fields stored inside Anki note fields.

Anki JSON 欄位安全讀寫工具模組。
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
    """Utility class for safe read/write access to JSON array/object fields in Anki cards.

    提供針對 Anki 卡片中 JSON 陣列/物件欄位的安全讀寫工具類。

    Because Anki sometimes wraps field content in HTML tags automatically (for example, after
    editing), this class strips that noise before JSON parsing and offers
    convenient list-manipulation helpers.

    由於 Anki 有時會自動在欄位內容外層加上 HTML 標籤（例如編輯過後），
    此工具類負責在解析 JSON 前濾除這些雜訊，並提供快捷的陣列操作方法。
    """

    @staticmethod
    def parse_field_string(field_str: str) -> list[Any]:
        """Safely parse the given string into a JSON array, filtering out HTML tags.

        安全解析給定的字串為 JSON 陣列，過濾 HTML 標籤。

        Also repairs JSON broken by unescaped ``class="..."`` attributes in old data, then unescapes HTML entities.

        也會修復舊資料中未跳脫的 ``class="..."`` 屬性造成的 JSON 損毀，再把 HTML Entity 還原成一般字元。

        Args:
            field_str: the raw string content of the Anki field
                Anki 欄位的原始字串內容

        Returns:
            the parsed JSON list; an empty list for an empty field
            解析出的 JSON 陣列；空欄位回傳空陣列

        Raises:
            AnkiFieldCorruptedError: if the field has content that cannot be parsed as a JSON array, so that
                follow-up writes never silently wipe existing data (S001)
                若欄位含內容但無法解析為 JSON 陣列，以避免後續寫入靜默清空既有資料（S001）
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
        """Read a specific field of a note from Anki and safely parse it as a JSON array.

        從 Anki 讀取特定筆記的特定欄位並安全解析為 JSON 陣列。

        Args:
            card_service: the card service instance
                卡片服務實例
            note_id: the target note ID
                目標筆記 ID
            field_name: the target field name
                目標欄位名稱

        Returns:
            the parsed JSON list; an empty list if the field is missing or empty
            解析出的 JSON 陣列；欄位不存在或為空時回傳空陣列

        Raises:
            AnkiFieldCorruptedError: if the field content cannot be parsed as a JSON array
                若欄位內容無法解析為 JSON 陣列
        """
        note = await card_service.get_note(note_id)
        field_str = note["fields"].get(field_name, {}).get("value", "[]")
        return cls.parse_field_string(field_str)

    @staticmethod
    def serialize(data: Any) -> str:
        """Convert data into the string to write into an Anki field (JSON, then HTML-escaped).

        資料轉成要寫進 Anki 欄位的字串（JSON 再做 HTML 轉義）。

        HTML escaping keeps Anki's rich-text editor from misparsing embedded HTML (such as ``<u>``) and corrupting
        the JSON. Use it to build the field value yourself when writing it together with other fields in one call.

        HTML 轉義避免 Anki 的富文本編輯器將內含的 HTML（如 ``<u>``）錯誤解析並損毀 JSON。
        要和其他欄位在同一次寫入時用它自己組欄位值。

        Args:
            data: the JSON-serializable data
                可序列化為 JSON 的資料

        Returns:
            the HTML-escaped JSON string
            經 HTML 轉義的 JSON 字串
        """
        return html.escape(json.dumps(data, ensure_ascii=False))

    @staticmethod
    async def update_field(card_service: "CardService", note_id: int, field_name: str, data: Any) -> None:
        """Serialize the data to a JSON string and write it back to the Anki field.

        將資料序列化為 JSON 字串並寫回 Anki 欄位。

        The value is built with ``serialize``, so it is HTML-escaped.

        欄位值以 ``serialize`` 產生，因此會經過 HTML 轉義。

        Args:
            card_service: the card service instance
                卡片服務實例
            note_id: the target note ID
                目標筆記 ID
            field_name: the target field name
                目標欄位名稱
            data: the data to serialize and write
                欲序列化寫入的資料
        """
        new_field_str = AnkiJsonFieldManager.serialize(data)
        await card_service.update_note_fields(note_id, {field_name: new_field_str})
        logger.debug("成功更新筆記 %d 的 JSON 欄位: %s", note_id, field_name)

    @classmethod
    async def append_to_list(cls, card_service: "CardService", note_id: int, field_name: str, item: Any) -> None:
        """Append an item to the end of the target JSON array field.

        附加元素至目標 JSON 陣列欄位尾端。

        Args:
            card_service: the card service instance
                卡片服務實例
            note_id: the target note ID
                目標筆記 ID
            field_name: the target field name
                目標欄位名稱
            item: the item to append
                欲附加的元素

        Raises:
            AnkiFieldCorruptedError: if the existing field content cannot be parsed as a JSON array; nothing is
                written
                若既有欄位內容無法解析為 JSON 陣列；此時不會寫入
        """
        items = await cls.safe_read_list(card_service, note_id, field_name)
        items.append(item)
        await cls.update_field(card_service, note_id, field_name, items)

    @classmethod
    async def insert_to_list(cls, card_service: "CardService", note_id: int, field_name: str, index: int, item: Any) -> None:
        """Insert an item at the given index of the target JSON array field.

        在特定索引位置插入元素至目標 JSON 陣列欄位。

        Appends the item when the index is out of range (negative or greater than the list length).

        索引超出範圍（負數或大於陣列長度）時改為附加到尾端。

        Args:
            card_service: the card service instance
                卡片服務實例
            note_id: the target note ID
                目標筆記 ID
            field_name: the target field name
                目標欄位名稱
            index: the insertion index
                插入位置索引
            item: the item to insert
                欲插入的元素

        Raises:
            AnkiFieldCorruptedError: if the existing field content cannot be parsed as a JSON array; nothing is
                written
                若既有欄位內容無法解析為 JSON 陣列；此時不會寫入
        """
        items = await cls.safe_read_list(card_service, note_id, field_name)
        if 0 <= index <= len(items):
            items.insert(index, item)
        else:
            items.append(item)
        await cls.update_field(card_service, note_id, field_name, items)

    @classmethod
    async def remove_from_list(cls, card_service: "CardService", note_id: int, field_name: str, index: int) -> bool:
        """Remove the item at the given index from the target JSON array field.

        移除目標 JSON 陣列欄位中特定索引的元素。

        Args:
            card_service: the card service instance
                卡片服務實例
            note_id: the target note ID
                目標筆記 ID
            field_name: the target field name
                目標欄位名稱
            index: the index to remove; negative indexes count as out of range
                欲移除的索引；負數視為超出範圍

        Returns:
            ``True`` if removed; ``False`` when the index is out of range (nothing is written)
            成功移除回傳 ``True``；索引超出範圍回傳 ``False``（不會寫入）

        Raises:
            AnkiFieldCorruptedError: if the existing field content cannot be parsed as a JSON array
                若既有欄位內容無法解析為 JSON 陣列
        """
        items = await cls.safe_read_list(card_service, note_id, field_name)
        if 0 <= index < len(items):
            items.pop(index)
            await cls.update_field(card_service, note_id, field_name, items)
            return True
        logger.warning("欲刪除的索引 %d 超出陣列範圍", index)
        return False
