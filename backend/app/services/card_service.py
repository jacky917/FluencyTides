"""Card base service (Card Service) module.

卡片基礎服務 (Card Service) 模組。

This module has been refactored into a pure low-level repository layer.
Responsibilities:
1. Provide encapsulated Anki CRUD operations.
2. Before writing to Anki, always query Anki for the actual fields and run strict "foolproofing" and field
   pre-validation.
It no longer contains any LLM, prompt, or JSON Schema business logic.

本模組已重構為純粹的底層 Repository 層。
職責：
1. 提供封裝後的 Anki CRUD 操作。
2. 在寫入 Anki 前，強制向 Anki 查詢實際欄位，執行嚴格的「防呆」與欄位預檢。
不再包含任何 LLM、Prompt 或 JSON Schema 等業務邏輯。
"""

import logging

from app.core.exceptions import (
    AnkiServiceError,
    DuplicateCardError,
    NoteNotFoundError,
)
from app.schemas.anki import AnkiNote, AnkiNoteOptions
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.services.anki_model_manager import AnkiModelManager

logger = logging.getLogger(__name__)


class FieldMismatchError(AnkiServiceError):
    """Raised when attempting to write card fields that do not exist on the model.

    當嘗試寫入不存在的卡片欄位時拋出。

    Attributes:
        invalid_fields: the set of invalid field names
            無效的欄位名稱集合
        available_fields: the fields actually available on the model
            模型實際可用的欄位清單
    """

    error_code = "FIELD_MISMATCH"
    status_code = 400

    def __init__(self, model_name: str, invalid_fields: set[str], available_fields: list[str]) -> None:
        """Initialize the error and compose its message.

        初始化錯誤並組合訊息。

        Args:
            model_name: the target model name
                目標模型名稱
            invalid_fields: the invalid field names
                無效欄位集合
            available_fields: the available field names
                可用欄位清單
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
    """An Anki card CRUD base service with strict foolproofing guards.

    提供嚴謹防呆機制的 Anki 卡片 CRUD 基礎服務。
    """

    def __init__(self, anki_client: AnkiClient, model_manager: AnkiModelManager) -> None:
        """Initialize the CardService.

        初始化 CardService。

        Args:
            anki_client: the injected AnkiConnect client
                注入的 AnkiConnect 客戶端
            model_manager: the injected model manager
                注入的模型管理器
        """
        self.anki_client = anki_client
        self.model_manager = model_manager

    async def _validate_fields(self, model_name: str, fields: dict[str, str]) -> None:
        """Query Anki for the model's actual fields and check that every given field is valid.

        向 Anki 查詢實際模型欄位，驗證傳入欄位是否全部有效。

        Args:
            model_name: the target model name
                目標模型名稱
            fields: the fields about to be written
                準備寫入的欄位字典

        Raises:
            AnkiServiceError: if the model's field list cannot be fetched from Anki
                若無法從 Anki 取得模型的欄位清單
            FieldMismatchError: if a field that does not exist was given
                若傳入了不存在的欄位
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
        """Create a new Anki note with strict validation.

        嚴謹建立新的 Anki 筆記。

        Steps:
        1. Ensure the deck exists.
        2. Strictly check that the fields match the model definition.
        3. Fill fields that were not provided with empty strings (to avoid AnkiConnect errors).
        4. Create the card.

        執行步驟：
        1. 確保牌組存在。
        2. 嚴格比對欄位是否符合模型定義。
        3. 將未提供的欄位自動補上空字串（避免 AnkiConnect 報錯）。
        4. 建立卡片。

        Args:
            deck_name: the target deck
                目標牌組
            model_name: the target model
                目標模型
            fields: the field data
                欄位資料
            tags: the tags
                標籤
            allow_duplicate: whether to allow duplicates (bypassing Anki's first-field duplicate check, scoped to
                this deck)
                是否允許重複建立（繞過 Anki 首欄位重複檢查，範圍為此牌組）

        Returns:
            the ID of the created note
            成功建立的 Note ID

        Raises:
            AnkiServiceError: if the field list cannot be fetched or AnkiConnect fails while adding the note
                若無法取得欄位清單，或新增時 AnkiConnect 失敗
            DuplicateCardError: if Anki rejects the note (likely a duplicate)
                若 Anki 拒絕新增（可能是重複）
            FieldMismatchError: if a field does not exist on the model
                若欄位不存在於模型
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
        """Read the detailed information of a single note.

        讀取單一筆記詳細資訊。

        Args:
            note_id: the target note ID
                目標筆記 ID

        Returns:
            the note information dict
            筆記資訊字典

        Raises:
            NoteNotFoundError: if the note cannot be found
                若找不到指定筆記
        """
        notes_info = await self.anki_client.get_notes_info([note_id])
        if not notes_info:
            raise NoteNotFoundError(f"找不到 Note ID: {note_id}（可能已刪除或尚未同步）")
        return notes_info[0].model_dump()

    async def get_notes_info(self, note_ids: list[int]) -> list[dict]:
        """Read the information of several notes in one batch.

        批次讀取筆記資訊。

        Args:
            note_ids: the note IDs
                筆記 ID 列表

        Returns:
            the note information dicts; an empty list when ``note_ids`` is empty (Anki is not queried)
            筆記資訊字典列表；``note_ids`` 為空時回傳空串列（不查詢 Anki）
        """
        if not note_ids:
            return []
        notes_info = await self.anki_client.get_notes_info(note_ids)
        return [n.model_dump() for n in notes_info]

    async def find_notes(self, query: str) -> list[int]:
        """Find note IDs using Anki search syntax.

        使用 Anki 查詢語法尋找 Note IDs。

        Args:
            query: the Anki search query string
                Anki 查詢字串

        Returns:
            the matching note IDs
            符合的 Note ID 列表
        """
        return await self.anki_client.find_notes(query)

    async def update_note_fields(
        self, note_id: int, fields: dict[str, str], *, model_name: str | None = None
    ) -> None:
        """Update a card's fields.

        更新卡片欄位。

        When ``model_name`` is not given, first query Anki for the note's note type, then run strict field
        pre-validation.

        沒給 ``model_name`` 時先向 Anki 查詢該 Note 的筆記類型，再進行嚴格欄位預檢。

        Args:
            note_id: the target note ID
                目標筆記 ID
            fields: the fields to update
                欲更新的欄位字典
            model_name: the note type, passed when the caller has already read the note, to save one query
                呼叫方已讀過筆記時傳入，省一次查詢

        Raises:
            AnkiServiceError: if the model's field list cannot be fetched
                若無法取得模型的欄位清單
            FieldMismatchError: if a field does not exist on the model
                若欄位不存在於模型
            NoteNotFoundError: if ``model_name`` is not given and the note cannot be found
                若未給 ``model_name`` 且找不到筆記
        """
        if model_name is None:
            note_info = await self.get_note(note_id)
            model_name = str(note_info["modelName"])

        await self._validate_fields(model_name, fields)
        
        await self.anki_client.update_note_fields(note_id=note_id, fields=fields)

    async def delete_note(self, note_id: int) -> None:
        """Delete a card.

        刪除卡片。

        Args:
            note_id: the target note ID
                目標筆記 ID
        """
        await self.anki_client.delete_notes([note_id])

    async def sync_anki(self, force: bool = False) -> None:
        """Manually trigger a sync between Anki and AnkiWeb.

        手動觸發 Anki 與 AnkiWeb 之間的同步。

        A failure is only logged as a warning and not raised (``AnkiClient.sync()`` defaults to
        ``raise_errors=False``), so the caller cannot tell whether the sync succeeded; when the result matters, call
        ``AnkiClient.sync(raise_errors=True, force=True)`` instead, as ``POST /anki/sync`` does.

        失敗時只記 warning、不拋錯（``AnkiClient.sync()`` 的預設 ``raise_errors=False``），
        呼叫端無法得知同步是否成功；需要結果時改呼叫
        ``AnkiClient.sync(raise_errors=True, force=True)``，如 ``POST /anki/sync``。

        Args:
            force: skip the 10-second debounce and always send the sync
                略過 10 秒防抖、一定送出同步
        """
        await self.anki_client.sync(force=force)
