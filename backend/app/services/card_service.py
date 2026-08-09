"""
卡片基礎服務 (Card Service) 模組。

本模組已重構為純粹的底層 Repository 層。
職責：
1. 提供封裝後的 Anki CRUD 操作。
2. 在寫入 Anki 前，強制向 Anki 查詢實際欄位，執行嚴格的「防呆」與欄位預檢。
不再包含任何 LLM、Prompt 或 JSON Schema 等業務邏輯。

Card base service (Card Service) module.

This module has been refactored into a pure low-level repository layer.
Responsibilities: (1) provide encapsulated Anki CRUD operations; (2)
before writing to Anki, always query the actual model fields and run
strict field pre-validation. It no longer contains any LLM, prompt, or
JSON-schema business logic.
"""

import logging

from app.core.exceptions import (
    AnkiServiceError,
    DuplicateCardError,
)
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.services.anki_model_manager import AnkiModelManager

logger = logging.getLogger(__name__)


class FieldMismatchError(AnkiServiceError):
    """當嘗試寫入不存在的卡片欄位時拋出。

    Raised when attempting to write fields that do not exist on the model.

    Attributes:
        invalid_fields: 無效的欄位名稱集合。Set of invalid field names.
        available_fields: 模型實際可用的欄位清單。Fields available on the
            model.
    """

    error_code = "FIELD_MISMATCH"
    status_code = 400

    def __init__(self, model_name: str, invalid_fields: set[str], available_fields: list[str]) -> None:
        """初始化錯誤並組合訊息。

        Initialize the error and compose its message.

        Args:
            model_name: 目標模型名稱。Target model name.
            invalid_fields: 無效欄位集合。Invalid field names.
            available_fields: 可用欄位清單。Available field names.
        """
        self.invalid_fields = invalid_fields
        self.available_fields = available_fields
        message = (
            f"欄位不符合模型 {model_name} 的定義。 "
            f"無效欄位: {list(invalid_fields)}, "
            f"可用欄位: {available_fields}"
        )
        super().__init__(message=message)


class CardService:
    """提供嚴謹防呆機制的 Anki 卡片 CRUD 基礎服務。

    Anki card CRUD base service with strict field validation guards.
    """

    def __init__(self, anki_client: AnkiClient, model_manager: AnkiModelManager) -> None:
        """初始化 CardService。

        Initialize the CardService.

        Args:
            anki_client: 注入的 AnkiConnect 客戶端。Injected AnkiConnect
                client.
            model_manager: 注入的模型管理器。Injected model manager.
        """
        self.anki_client = anki_client
        self.model_manager = model_manager

    async def _validate_fields(self, model_name: str, fields: dict[str, str]) -> None:
        """向 Anki 查詢實際模型欄位，驗證傳入欄位是否全部有效。

        Query Anki for actual model fields and validate the given ones.

        Args:
            model_name: 目標模型名稱。Target model name.
            fields: 準備寫入的欄位字典。Fields about to be written.

        Raises:
            FieldMismatchError: 若傳入了不存在的欄位。If nonexistent fields
                were provided.
        """
        try:
            available_fields = await self.anki_client.get_model_field_names(model_name)
        except AnkiConnectError as e:
            raise AnkiServiceError(f"無法取得模型 {model_name} 的欄位清單: {e}") from e

        available_set = set(available_fields)
        provided_set = set(fields.keys())
        invalid_fields = provided_set - available_set

        if invalid_fields:
            raise FieldMismatchError(model_name, invalid_fields, available_fields)

    async def create_note(
        self, deck_name: str, model_name: str, fields: dict[str, str], tags: list[str], allow_duplicate: bool = False
    ) -> int:
        """嚴謹建立新的 Anki 筆記。

        Create a new Anki note with strict validation.

        執行步驟：
        1. 確保牌組存在。
        2. 嚴格比對欄位是否符合模型定義。
        3. 將未提供的欄位自動補上空字串（避免 AnkiConnect 報錯）。
        4. 建立卡片。

        Steps: ensure the deck exists, strictly validate fields against
        the model, backfill missing fields with empty strings (to avoid
        AnkiConnect errors), then create the note.

        Args:
            deck_name: 目標牌組。Target deck.
            model_name: 目標模型。Target model.
            fields: 欄位資料。Field data.
            tags: 標籤。Tags.
            allow_duplicate: 是否允許重複建立（繞過 Anki 首欄位重複檢查）。
                Whether to allow duplicates (bypass first-field check).

        Returns:
            成功建立的 Note ID。ID of the created note.

        Raises:
            FieldMismatchError: 欄位錯誤。On invalid fields.
            DuplicateCardError: Anki 拒絕新增 (重複)。When Anki rejects the
                note as a duplicate.
        """
        await self.model_manager.ensure_deck_exists(deck_name)
        await self._validate_fields(model_name, fields)

        # 為了滿足 AnkiConnect 的要求，把模型內有但前端沒傳的欄位補上空字串
        available_fields = await self.anki_client.get_model_field_names(model_name)
        full_fields = {f: "" for f in available_fields}
        full_fields.update(fields)

        notes = [
            AnkiNote(
                deckName=deck_name,
                modelName=model_name,
                fields=full_fields,
                tags=tags,
                options=AnkiNoteOptions(
                    allowDuplicate=allow_duplicate,
                    duplicateScope="deck",
                    duplicateScopeOptions={
                        "deckName": deck_name,
                        "checkChildren": False,
                        "checkAllModels": False
                    }
                )
            )
        ]

        try:
            results = await self.anki_client.add_notes(notes)
            if not results or results[0] is None:
                # canAddNotes 拒絕了
                raise DuplicateCardError(
                    f"Anki 拒絕新增卡片至 {deck_name}，可能是因為首欄位內容重複。"
                )
            return results[0]
        except AnkiConnectError as e:
            logger.error("建立卡片失敗: %s", e)
            raise AnkiServiceError(f"建立卡片失敗: {e}") from e

    async def get_note(self, note_id: int) -> dict[str, object]:
        """讀取單一筆記詳細資訊。

        Read detailed info of a single note.

        Args:
            note_id: 目標筆記 ID。Target note ID.

        Returns:
            筆記資訊字典。Note info dict.

        Raises:
            AnkiServiceError: 找不到指定筆記時。When the note is not found.
        """
        notes_info = await self.anki_client.get_notes_info([note_id])
        if not notes_info:
            raise AnkiServiceError(f"找不到 Note ID: {note_id}")
        return notes_info[0].model_dump()

    async def get_notes_info(self, note_ids: list[int]) -> list[dict]:
        """批次讀取筆記資訊。

        Batch-read note info.

        Args:
            note_ids: 筆記 ID 列表。List of note IDs.

        Returns:
            筆記資訊字典列表。List of note info dicts.
        """
        if not note_ids:
            return []
        notes_info = await self.anki_client.get_notes_info(note_ids)
        return [n.model_dump() for n in notes_info]

    async def find_notes(self, query: str) -> list[int]:
        """使用 Anki 查詢語法尋找 Note IDs。

        Find note IDs using Anki search syntax.

        Args:
            query: Anki 查詢字串。Anki search query string.

        Returns:
            符合的 Note ID 列表。Matching note IDs.
        """
        return await self.anki_client.find_notes(query)

    async def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        """更新卡片欄位。

        Update note fields.

        會先向 Anki 查詢該 Note 的 model_name，再進行嚴格欄位預檢。
        Queries the note's model_name from Anki first, then runs strict
        field pre-validation before updating.

        Args:
            note_id: 目標筆記 ID。Target note ID.
            fields: 欲更新的欄位字典。Fields to update.
        """
        note_info = await self.get_note(note_id)
        model_name = note_info["modelName"]
        
        await self._validate_fields(model_name, fields)
        
        await self.anki_client.update_note_fields(note_id=note_id, fields=fields)

    async def delete_note(self, note_id: int) -> None:
        """刪除卡片。

        Delete a note.

        Args:
            note_id: 目標筆記 ID。Target note ID.
        """
        await self.anki_client.delete_notes([note_id])

    async def sync_anki(self, force: bool = False) -> None:
        """手動觸發 Anki 與 AnkiWeb 之間的同步。

        Manually trigger a sync between Anki and AnkiWeb.

        Args:
            force: 是否強制同步。Whether to force the sync.
        """
        await self.anki_client.sync(force=force)
