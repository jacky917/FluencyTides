"""
Anki 多卡建立的補償式交易工具 (Compensating Transaction)。

Compensating-transaction helper for multi-note Anki creation.

背景：AnkiConnect 沒有原生交易機制，而本專案多個 Handler 需要「一次建立
一組彼此關聯的卡片」（母卡＋子卡、Context＋Cloze 等）。若中途任一步失敗，
先前已寫入 Anki 的卡片會殘留為孤兒，且母卡的 JSON 欄位可能已被汙染。

Background: AnkiConnect has no native transactions, yet several handlers in
this project must create a group of interrelated notes at once (master plus
children, Context plus Cloze, etc.). If any step fails midway, notes already
written to Anki are left orphaned and the master note's JSON field may have
been polluted.

設計決策：
- 採用「記錄已建立的 Note ID → 失敗時反序刪除」的補償式交易，達成近似原子性。
  反序刪除是為了先移除依賴方（子卡持有 Master_Note_ID），再移除被依賴方。
- 回滾本身失敗時不再拋出，改以 ERROR 記錄殘留的 Note ID 供人工清理——
  因為此時原始例外更重要，不可被回滾的次要錯誤遮蔽。
- `__aexit__` 一律回傳 False（不吞例外），呼叫端仍會收到原始錯誤。

Design decisions:
- Uses a compensating transaction ("record created note IDs, delete them in
  reverse on failure") to approximate atomicity. Reverse order removes the
  dependent notes (children hold Master_Note_ID) before their dependencies.
- A failing rollback never raises; it logs the leftover note IDs at ERROR
  level for manual cleanup, because the original exception matters more and
  must not be masked by a secondary rollback error.
- `__aexit__` always returns False (never swallows), so callers still see the
  original error.

對應修復：docs/15_Bug_Scan_Report.md 的 S002 與 S003。
Addresses findings S002 and S003 in docs/15_Bug_Scan_Report.md.
"""

import logging
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.card_service import CardService

logger = logging.getLogger(__name__)


class AnkiNoteTransaction:
    """Anki 多卡建立的補償式交易情境管理器。

    Compensating-transaction context manager for multi-note creation.

    使用方式：以 `async with` 包住整組建卡流程，並改用本物件的
    `create_note()` 取代 `card_service.create_note()`。若區塊內任何位置
    拋出例外，已建立的卡片會被反序刪除，Anki 回到操作前的狀態。

    Usage: wrap the whole creation flow in `async with` and call this
    object's `create_note()` instead of `card_service.create_note()`. If any
    statement inside the block raises, the created notes are deleted in
    reverse order, returning Anki to its pre-operation state.

    Example:
        async with AnkiNoteTransaction(card_service) as tx:
            master_id = await tx.create_note(deck_name=..., ...)
            child_id = await tx.create_note(deck_name=..., ...)
            await AnkiJsonFieldManager.append_to_list(...)  # 失敗亦會回滾上面兩張卡

    Attributes:
        created_ids: 本次交易中已成功建立的 Note ID（依建立順序）。
            Note IDs successfully created in this transaction, in creation
            order.
    """

    def __init__(self, card_service: "CardService") -> None:
        """初始化交易物件。

        Initialize the transaction object.

        Args:
            card_service: 用於實際建立與刪除筆記的服務實例。The service
                instance used to actually create and delete notes.
        """
        self._card_service = card_service
        self.created_ids: list[int] = []

    async def create_note(
        self,
        deck_name: str,
        model_name: str,
        fields: dict[str, str],
        tags: list[str],
        allow_duplicate: bool = False,
    ) -> int:
        """建立筆記並登記到交易中，簽名與 CardService.create_note 一致。

        Create a note and register it with the transaction; the signature
        mirrors CardService.create_note.

        Args:
            deck_name: 目標牌組名稱。Target deck name.
            model_name: 筆記模型名稱。Note model name.
            fields: 欄位名稱到值的對應。Mapping of field names to values.
            tags: 標籤列表。Tag list.
            allow_duplicate: 是否允許重複卡片。Whether duplicates are allowed.

        Returns:
            新建立的 Note ID。The newly created note ID.

        Raises:
            FluencyTidesError: 建立失敗時由 CardService 拋出（交易會在
                `__aexit__` 回滾先前的卡片）。Raised by CardService on
                failure; earlier notes are rolled back in `__aexit__`.
        """
        note_id = await self._card_service.create_note(
            deck_name=deck_name,
            model_name=model_name,
            fields=fields,
            tags=tags,
            allow_duplicate=allow_duplicate,
        )
        self.created_ids.append(note_id)
        return note_id

    async def __aenter__(self) -> "AnkiNoteTransaction":
        """進入交易區塊。

        Enter the transaction block.

        Returns:
            交易物件本身。The transaction object itself.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """離開交易區塊；若有例外則反序刪除已建立的筆記。

        Exit the transaction block; on exception, delete created notes in
        reverse order.

        Args:
            exc_type: 例外類別，無例外時為 None。Exception class, or None.
            exc: 例外實例。The exception instance.
            tb: Traceback 物件。The traceback object.

        Returns:
            一律為 False，代表不吞噬例外。Always False, so the exception
            keeps propagating.
        """
        if exc_type is None:
            return False

        if not self.created_ids:
            logger.warning("多卡建立失敗且尚無已建立卡片，無需回滾: %s", exc)
            return False

        logger.warning(
            "多卡建立失敗，開始回滾 %d 張已建立的卡片: %s",
            len(self.created_ids),
            exc,
        )
        orphans: list[int] = []
        for note_id in reversed(self.created_ids):
            try:
                await self._card_service.delete_note(note_id)
                logger.info("已回滾筆記 note_id=%s", note_id)
            except Exception as rollback_error:  # noqa: BLE001 - 回滾失敗不得遮蔽原始例外
                orphans.append(note_id)
                logger.error(
                    "回滾失敗，殘留孤兒筆記 note_id=%s（請手動刪除）: %s",
                    note_id,
                    rollback_error,
                )

        if orphans:
            logger.error("以下筆記回滾失敗，需人工清理: %s", orphans)
        else:
            logger.info("回滾完成，Anki 已回到操作前狀態。")
        return False
