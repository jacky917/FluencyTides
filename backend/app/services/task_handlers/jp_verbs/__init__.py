"""Handlers and creators of the Japanese verb cards (JP_VerbPair, JP_CoreVerb, and their shared Context cards).

日文動詞卡（自他動詞、核心動詞，以及兩者共用的 Context 卡）的 handler 與 creator。

- ``verb_pair.py``: the JP_VerbPair master and Cloze cards' handler, and the creator of its child cards
- ``core_verb.py``: the same for JP_CoreVerb
- ``context.py``: the handler of the Context cards both creators produce (``jp_context``)

- ``verb_pair.py``：自他動詞母卡與 Cloze 卡的 handler，以及它的子卡 creator
- ``core_verb.py``：核心動詞的同樣兩者
- ``context.py``：兩個 creator 都會產出的 Context 卡的 handler（``jp_context``）

The folder is named after what it holds rather than after the ``jp_context`` handler
(handler/creator split plan, ``handler_note_type_split_FEAT_2026-11-28.md``, D3).

資料夾依內容命名，不用 ``jp_context`` 這個 handler 的名稱（handler 拆分計畫書 ``handler_note_type_split_FEAT_2026-11-28.md`` D3）。
"""

# 依這個順序註冊：GET /handlers 依註冊順序列出
# Registration order is the listing order of GET /handlers.
from app.services.task_handlers.jp_verbs import verb_pair, core_verb, context  # noqa: F401
