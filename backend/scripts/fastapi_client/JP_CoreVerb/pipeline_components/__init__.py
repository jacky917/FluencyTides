"""JP_CoreVerb 選句管線元件套件。

Sentence-selection pipeline components for JP_CoreVerb: a validator, a
diversity selector, and the funnel orchestrator, all dependency-injected
and settings-free.

包含三個純函數優先的模組，對應計劃 docs/14_Core_Verb_Card_Plan.md §6：

- ``candidate_validator``：fugashi token 級候選驗證器（§6.1），tagger 注入式。
- ``diversity_selector``：搭配/活用形分桶與 zigzag 兩段式配額分配（§6.3-6.5）。
- ``funnel``：漏斗編排 ``run_selection_funnel``（§6.6），
  ``generate_child_cards.py`` 與 ``test_search_verb.py`` 雙入口共用的單一事實來源。

設計原則：
    1. 漏斗內不 import settings、不讀 .env——所有設定以 dataclass 注入。
    2. fugashi tagger 由呼叫端注入，模組本身不依賴 fugashi（便於以假 token 測試）。
    3. 分桶誤判只影響多樣性品質、不影響卡片正確性（降級安全）。
"""
