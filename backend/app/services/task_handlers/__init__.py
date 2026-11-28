"""Task handlers package.

Task Handlers 套件。

Provides the handler and creator implementations of the task-driven architecture: a handler is what a kind of
card can do once it exists (exactly one per note type), and a creator is one way to create cards (several per note
type allowed; handler/creator split plan, ``handler_note_type_split_FEAT_2026-11-28.md``).

提供任務驅動架構（Task-Driven Architecture）的 handler 與 creator 實作：handler 是一種卡片建好之後能做什麼
（每種筆記類型恰好一個），creator 是一種建卡方式（同一種筆記類型可以有好幾個；
handler 拆分計畫書 ``handler_note_type_split_FEAT_2026-11-28.md``）。
"""

# 所有正式 handler 與 creator 都在這裡 import，讓 @register_handler、@register_creator 在 import 套件時就執行。
# 註冊順序只決定 GET /handlers 的排列順序：每種筆記類型只有一個 handler，順序不影響行為（handler 拆分計畫書 D8）。
# Every handler and creator is imported here so registration happens on package import; the order only decides
# the listing order of GET /handlers, since each note type has exactly one handler.
from app.services.task_handlers import (  # noqa: F401
    vocabulary_mining_handler,
    expression_handler,
    speaking_coach_handler,
    speaking_trilingual_handler,
    jp_verbs,
    person_profile_handler,
)
from app.services.task_handlers.registry import handler_registry

# creator 可能寫出別的模組的 handler 負責的筆記類型（jp 的 Context 卡），所以全部註冊完才檢查、綁定 handler
# A creator may write note types owned by handlers in other modules, so creators are checked and bound last.
handler_registry.check_creators()
