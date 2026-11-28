"""Handler module for the Japanese context child cards (JP_Context_Dark) shared by JP_VerbPair and JP_CoreVerb.

自他動詞（JP_VerbPair）與核心動詞（JP_CoreVerb）共用的日文語境子卡（JP_Context_Dark）的 handler 模組。

Both projects' batch scripts write Context cards with the same note type; which project a card belongs to is told by
its ``Master_Note_ID`` field, not by the note type. Since a note type has exactly one handler, the Context cards have
this handler of their own, and the two projects' creators both produce them
(handler/creator split plan, ``handler_note_type_split_FEAT_2026-11-28.md``, D3).

兩個專案的批次腳本都用同一種筆記類型寫 Context 卡；一張卡屬於哪個專案，看的是它的 ``Master_Note_ID`` 欄位，
不是筆記類型。一種筆記類型恰好一個 handler，所以 Context 卡有自己的這個 handler，兩個專案的 creator 都會產出它
（handler 拆分計畫書 ``handler_note_type_split_FEAT_2026-11-28.md`` D3）。
"""

from typing import override

from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import GraphCapability, HandlerCapabilities, Operation
from app.services.task_handlers.registry import register_handler

#: The note type of the Context child cards.
#: Context 子卡的筆記類型。
CONTEXT_NOTE_TYPE = "JP_Context_Dark"


@register_handler
class JPContextHandler(BaseHandler):
    """Handler for the Japanese context child cards shared by the two verb projects.

    兩個日文動詞專案共用的語境子卡處理器。

    The cards are managed by the local batch scripts, so the web can only view them; deleting goes through the delete
    tool of the project the card's master belongs to.

    卡片由本機批次腳本管理，網頁只能檢視；刪除要用卡片的母卡所屬專案的刪除工具。
    """

    # 母卡 JSON 與查重紀錄互相參照，網頁只能檢視（handlers 計畫書 D4）；哪個專案的卡要看 Master_Note_ID
    CAPABILITIES = HandlerCapabilities(
        update_fields=Operation(reason="由本機批次腳本管理，網頁只能檢視"),
        delete=Operation(
            reason="由本機批次腳本管理；刪除請依母卡所屬的專案，用 scripts/local_anki/JP_VerbPair/delete_child_cards.py"
            " 或 scripts/local_anki/JP_CoreVerb/delete_child_cards.py（會一併更新母卡 JSON 與查重紀錄）"
        ),
        graph=GraphCapability(reason="尚未實作"),
    )

    @property
    @override
    def handler_name(self) -> str:
        """The handler name used in URLs.

        處理器名稱，出現在網址裡。
        """
        return "jp_context"

    @property
    @override
    def title(self) -> str:
        """The Chinese display title.

        中文名稱。
        """
        return "日文語境卡"

    @property
    @override
    def supported_models(self) -> list[str]:
        """The Anki note types this handler owns.

        這個處理器負責的 Anki 筆記類型。
        """
        return [CONTEXT_NOTE_TYPE]
