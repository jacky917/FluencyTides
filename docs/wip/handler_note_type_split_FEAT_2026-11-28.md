# FEAT — 把建卡從 handler 拆出來成為 creator：一種筆記類型只對應一個 handler，建卡流程可以有好幾個

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-28（同日依討論過的方向改寫：`Handler` 沿用現名代表卡片類型、新增 `Creator`） |
| **性質** | 重構設計（行為大致不變，改 handler 的分層與註冊表）＋實作工作項；只有 `JP_Context_Dark` 的歸屬會讓畫面上看得到變化（D3） |
| **狀態** | 🚧 實作完成、未開 PR（2026-11-28；S0–S4 完成，多面向審查的 8 項 Important 已修正，見 D11；TG 流程待部署後實測，見 §5 S4） |
| **範圍** | `BaseHandler` 拿掉建卡的部分，只代表「卡片類型」（建好之後的行為，每種筆記類型恰好一個）；新增 `creator.py` 的 `BaseCreator` 代表「建卡流程」（同一種筆記類型可以有好幾個）；`HandlerRegistry` 的 handler 依筆記類型一對一登記、creator 依名稱登記；jp 兩組改成 `jp_verbs/` 資料夾，新增 handler `jp_context`；建卡相關的呼叫端改找 creator。建卡端點改成 `/creators/...` 的第二階段這次不做（D7） |
| **不動** | Anki 的筆記類型、欄位與既有卡片（不改名、不搬資料）；資料庫（用途鍵 `recording_eval.<名稱>`、`jp_verb_pair` 等 LLM 用途鍵、`card_gen_log.card_type`、`rel_links.created_by` 的值都不變）；正式機設定；各 handler 建卡、改欄位、動作的實際行為 |
| **PR / 進度** | 分支 `feat/handler-creator-split`，接在 `feat/routing-three-layers`（PR #62，`RECORDING_EVAL` 宣告，2026-11-28 尚未合併）之後開出，PR 等 #62 合併後再開；`speaking_monologue` 開工之前做完（§6） |
| **關聯文件** | `docs/archive/handlers_api_redesign_FEAT_2026-10-20.md` D2（宣告要有實作支撐）、D4（「筆記類型 → handler」對照與「取第一個註冊的」）；`docs/wip/recording_eval_settings_FEAT_2026-11-26.md` D4（錄音評分由 handler 宣告，`voice.py` 依筆記類型找 handler）；`docs/wip/speaking_monologue_FEAT_2026-11-26.md` D1、§5.1（新卡也靠 `handler_for_note_type` 從筆記類型找 handler） |

---

## 1. 問題與動機

### 1.1 現況

一個 handler 同時負責兩件事：

1. **怎麼建卡**：表單或預覽流程、建卡參數、草稿、建完的後續處理、本機批次腳本的 `execute_generate`。
2. **建好之後這種卡能怎麼用**：讀取、改欄位、改 JSON 欄位、刪除、具名動作、欄位搜尋、圖譜、錄音評分。

第 2 件事只跟**筆記類型**有關：卡片建好以後，系統只知道它的 `modelName`，不知道是哪個流程建的。所以「建好之後的事」都要從筆記類型找 handler：

| 呼叫端 | 怎麼找 |
|---|---|
| `NoteService.get`（`GET /notes/{id}` 的權限與 `handler`） | `registry.handler_for_note_type`（`backend/app/services/note_service.py:614`） |
| `NoteService.run_action`（TG 的所有具名動作） | `registry.handler_for_note_type`（`note_service.py:839`） |
| TG 錄音評分讀 `RECORDING_EVAL` | `handler_registry.handler_for_note_type`（`feat/routing-three-layers` 的 `backend/app/bot/handlers/voice.py:254`） |
| 改欄位、改 JSON、刪除（網址帶 handler 名稱） | `registry.get_handler(name)`，再檢查筆記類型在它的 `supported_models` 裡（`note_service.py:923` 的 `_owning_handler`、`:1166` 的 `_require_owned`） |

但註冊表允許一種筆記類型對到好幾個 handler（`backend/app/services/task_handlers/registry.py:74` 的 `_by_note_type: dict[str, list[BaseHandler]]`）。為了讓「從筆記類型找」有唯一答案，現在的做法是：

- `handler_for_note_type` 取**第一個註冊的**（`registry.py:127`），註冊順序由 `task_handlers/__init__.py` 的 import 順序決定。
- 註冊時用 `_check_shared_note_types`（`registry.py:105`）檢查共用同一筆記類型的 handler「對既有筆記的能力」相同，比的是 `HandlerCapabilities.existing_note_ops()`（`capabilities.py:239`）：讀取、改欄位、改 JSON、刪除四個 `supported`，加上動作名稱。

目前唯一的共用是 `JP_Context_Dark`：`jp_verb_pair_handler.py:92` 與 `jp_core_verb_handler.py:104` 都把它列進 `supported_models`。

### 1.2 根因（已定位）

「建卡流程」可以有很多個，「建好之後的行為」每種筆記類型只能有一份。兩者放在同一個類別裡，同一種筆記類型有第二個建卡流程時，就會多出第二份「建好之後的行為」，只好靠「取第一個」加上一致性檢查來掩蓋。這個檢查注定比對不完整：

| 建好之後會用到、但 `existing_note_ops()` 沒有比對的 | 用在哪裡 |
|---|---|
| `RECORDING_EVAL`（審查 `feat/routing-three-layers` 時發現） | `voice.py` 錄音評分；`main.py` 啟動時試建評分器 |
| 各項能力的 `reason`（不開放的原因） | `GET /notes/{id}` 的 `permissions[*].reason` |
| `JSON_FIELDS`、`JSON_FIELD_SPECS` | `GET /notes/{id}` 的 `json_fields`、`update_json` |
| `MEDIA_FIELDS` | 欄位彈窗的上傳元件、`field_texts` 的寫入檢查 |
| `RELATION_LABEL_FIELD`、`after_json_update`、`reject_locked_fields` | 改欄位、改 JSON 時的同步與鎖定 |
| `search`、`graph` 與 `SEARCH_FIELDS`、`GRAPH` | 卡片頁的欄位搜尋、圖譜 |

每加一種「建好之後」的宣告，就要記得把它加進比對；漏了不會有任何錯誤，只會依註冊順序靜靜地用第一個。

### 1.3 已經看得到的後果

這些都是現在 `main` 上的行為，不是推測：

1. **核心動詞的 Context 卡顯示自他動詞的刪除工具**：`GET /notes/{id}` 對任何 `JP_Context_Dark` 都回 `jp_verb_pair` 的 `delete.reason`，也就是「刪除請用 `scripts/local_anki/JP_VerbPair/delete_child_cards.py`」（`jp_verb_pair_handler.py:60`），就算那張卡的母卡是核心動詞。兩個 handler 的 `reason` 不同，但 `existing_note_ops()` 不比 `reason`，所以註冊檢查通過。
2. **卡片清單的「自他動詞」也列出核心動詞的 Context 卡**：前端依 handler 的 `note_types` 查卡（`frontend/src/components/cards/CardListPanel.tsx:95-98`），兩個 jp handler 都含 `JP_Context_Dark`，選哪一個都會列出全部的 Context 卡。
3. **同一張 Context 卡從兩個網址都能讀**：`/handlers/jp_verb_pair/notes/{id}` 與 `/handlers/jp_core_verb/notes/{id}` 都回 200（`backend/tests/test_note_service.py:233-236` 把它當成預期行為鎖住）。
4. **註冊順序有意義**：`task_handlers/__init__.py:11` 的註解寫明「同一筆記類型有多個 handler 時取第一個註冊的」，調換 import 順序就會改變行為。

## 2. 目標與非目標

**目標**
- G1 每種筆記類型恰好對應一個 handler，註冊第二個時直接報錯；「從筆記類型找」不再有「取第一個」，也不再依賴註冊順序。
- G2 建卡流程是另一種角色 `Creator`，同一種筆記類型可以有好幾個；它只宣告怎麼建，不宣告建好之後的行為。
- G3 `_check_shared_note_types`、`existing_note_ops()`、`handlers_for_note_type` 不再需要，刪掉。
- G4 §1.3 的第 1–3 點消失：Context 卡有自己的刪除說明、不再混在兩種 jp 卡的清單裡、只有一個網址讀得到。
- G5 既有卡片、資料庫、Anki 都不用動；第一階段的 API 形狀與前端不變（除了 §1.3 的 jp 部分）。

**非目標**
- 不改任何筆記類型或欄位，不搬資料。
- 不改各 handler 的實際邏輯（建卡、動作、圖譜的內容）；只搬家。
- 不在這次加新的建卡流程（例如「同一筆記類型的第二種建法」）；這次只讓它之後加得進來。
- 建卡端點改成 `/creators/...` 的第二階段（D7）：這次不做，等到真的要加同一筆記類型的第二種建法時再做（§8 Q2）。

## 3. 設計決策

### D1 命名：`Handler` 沿用現名代表卡片類型，新增 `Creator` 代表建卡流程

- **`BaseHandler`**（`task_handlers/base.py`，留在原處）：卡片類型，也就是「建好之後的行為」。每種筆記類型剛好一個 handler。
- **`BaseCreator`**（新檔 `task_handlers/creator.py`）：建卡流程。每個 creator 指向它產出的 handler；同一種筆記類型可以有好幾個 creator。

沿用 `Handler` 這個名字，而不是另取「CardType」之類的新名字，理由：

- 呼叫端裡「從筆記類型找 handler」的那一半本來就是卡片類型的意思：`handler_for_note_type`、`GET /notes/{id}` 回的 `handler`、`voice.py`、`run_action`、`main.py` 的啟動檢查，名字和程式都不用改，要改的只剩建卡的那一半（§4）。
- `/handlers/{name}/notes/{id}` 這組讀、改、刪的網址，以及前端的 `note.handler`、`HandlerInfo.note_types`，意思也正好是「負責這種筆記的」。
- 代價是第一階段的建卡網址 `/handlers/{name}/notes`（`POST`）、`/drafts`、`/fill` 帶的其實是 creator 名稱；因為一種建法的類型 handler 和 creator 同名（D6），第一階段不會出錯，第二階段（D7）再把網址改成 `/creators/...`。

### D2 成員怎麼分

判斷標準只有一條：**卡片建好之後，只拿著筆記（`modelName`）還需要知道的，留在 Handler；只在建卡當下用到的，搬到 Creator。**

**留在 `BaseHandler`**

| 成員 | 備註 |
|---|---|
| `handler_name`、`title` | 名稱沿用（D6） |
| `supported_models` | 每一個都只能屬於這個 handler（D4） |
| `CAPABILITIES: HandlerCapabilities` 的 `read`、`update_fields`、`update_json`、`delete`、`actions`、`search`、`graph` | `HandlerCapabilities` 拿掉 `create`、`fill`（D2 末段） |
| `GRAPH`、`RELATION_LABEL_FIELD` | |
| `JSON_FIELDS`、`JSON_FIELD_SPECS`、`MEDIA_FIELDS` | Creator 要用時向它指向的 handler 拿（D5） |
| `SEARCH_FIELDS`、`search_fields()` | 拿掉從 `CREATE_PARAMS` 自動產生的備援（D5） |
| `RECORDING_EVAL` | `feat/routing-three-layers` 新增的 |
| `execute_read`、`execute_update`、`execute_delete` | |
| `query_notes`、`execute_read_graph`、`note_types_for` | `note_types_for` 是圖譜與搜尋用的，一併留下 |
| `after_json_update`、`reject_locked_fields`、`field_texts` | `field_texts` 標準建立也用（D5） |
| `RelationGraphMixin`（`vocabulary_mining`） | 圖譜是建好之後的事，仍混入 handler |

**搬到 `BaseCreator`**

| 成員 | 備註 |
|---|---|
| `creator_name`、`title` | 名稱沿用現在的 `handler_name`（D6）；用 `creator_name` 而不是 `name`，log 與錯誤訊息裡一看就知道是哪一種 |
| `CAPABILITIES: CreatorCapabilities` 的 `create`（`CreateCapability`） | `channels`、`flow`、`deck`、`fixed_deck`、`note_type` 都不變 |
| `CAPABILITIES.fill`（AI 整理） | 歸 creator（§8 Q3 已決定）：它產生草稿、和預覽流程共用草稿槽位、可能建新卡；第一階段 `GET /handlers` 合併後的形狀不變，卡片頁的「AI 整理」按鈕照常（§6） |
| `HANDLER: ClassVar[str]`（新增） | 指向它產出的 handler（D5） |
| `PRODUCES: ClassVar[tuple[str, ...]]`（新增） | 會寫出哪些筆記類型（D5） |
| `CREATE_PARAMS`、`DRAFT_PARAMS`、`CREATE_TAGS` | |
| `execute_create`、`resolve_create_deck`、`after_create` | |
| `draft_context`、`execute_draft`、`find_draft_duplicate` | |
| `execute_generate`（jp 兩個、`expression_correction`） | 現在不在 `BaseHandler` 上，靠 `getattr` 檢查（`registry.py:245`）；改成 `BaseCreator` 的選用方法 |

`base.py` 裡不屬於任何一方的東西留在原處：`DuplicateResolution`、`DUPLICATE_*_KEY`、`field_text`、`clean_html`、`note_field_values`、`note_detail`、`field_value`。

`fill`（AI 整理）歸 creator 的理由（§8 Q3）：

- 它會把整理結果寫進現有的卡（像修改），也會建一張新卡（像建卡），兩邊都沾得上；但它的運作方式就是建卡流程：送一段描述給 LLM、在背景產生草稿、使用者勾選後才寫入，和三語卡、外語糾錯的預覽流程是同一套機制，共用全站一份草稿槽位，草稿以 creator 名稱記錄。
- 歸 handler 的話，handler 身上又會有「怎麼產生內容」的東西，正是這次要拆開的混合。
- 寫進現有卡片的那一步，最後仍呼叫 handler 的修改動作：creator 只負責產生，寫入照規矩交給 handler。

能力宣告的拆法：`capabilities.py` 的 `HandlerCapabilities` 拿掉 `create`、`fill` 兩欄，新增 `CreatorCapabilities(create, fill)`；兩邊的類別屬性都叫 `CAPABILITIES`（各自在自己的類別上，不會撞名）；刪除 `existing_note_ops()`。

### D3 `JP_Context_Dark` 歸新的 handler `jp_context`，jp 兩組放進 `jp_verbs/` 資料夾

（§8 Q1、Q5 已決定，2026-11-28。）

先釐清原本的樣子：原本就是兩個 handler、兩條建卡流程，**每個 handler 都負責三種卡**，只是其中 Context 卡兩個專案共用同一種筆記類型，所以 `JP_Context_Dark` 同時登記在兩個 handler 底下：

| 原本的 handler | 負責的筆記類型 |
|---|---|
| `jp_verb_pair`（自他動詞） | `JP_VerbPair_Master_Dark`、**`JP_Context_Dark`**、`JP_VerbPair_Cloze_Dark` |
| `jp_core_verb`（核心動詞） | `JP_CoreVerb_Master_Dark`、**`JP_Context_Dark`**、`JP_CoreVerb_Cloze_Dark` |

系統拿到一張 Context 卡、要決定它能不能改、能不能刪、刪除時顯示什麼時，只能取第一個註冊的 `jp_verb_pair`，就算那張卡是核心動詞產生的也一樣。這次的決定不是把建卡從一條變兩條（建卡原本就是兩條，之後也還是兩條），而是決定**共用的那種卡建好之後歸誰管**：另開一個 `jp_context` 專門管它。handler 從 2 個變 3 個，creator 維持 2 個。

拆完之後每種筆記類型只能有一個 handler：

| Handler | 筆記類型 | Creator |
|---|---|---|
| `jp_context`（`JPContextHandler`） | `JP_Context_Dark` | 沒有同名的 creator（兩個 jp creator 都會寫出它） |
| `jp_verb_pair`（`JPVerbPairHandler`） | `JP_VerbPair_Master_Dark`、`JP_VerbPair_Cloze_Dark` | `jp_verb_pair`（`JPVerbPairCreator`，`script`）：`HANDLER = "jp_verb_pair"`，`PRODUCES = ("JP_Context_Dark", "JP_VerbPair_Cloze_Dark")` |
| `jp_core_verb`（`JPCoreVerbHandler`） | `JP_CoreVerb_Master_Dark`、`JP_CoreVerb_Cloze_Dark` | `jp_core_verb`（`JPCoreVerbCreator`，`script`）：`HANDLER = "jp_core_verb"`，`PRODUCES = ("JP_Context_Dark", "JP_CoreVerb_Cloze_Dark")` |

檔案：

```
task_handlers/jp_verbs/
├── __init__.py
├── context.py      # JPContextHandler（JP_Context_Dark，handler 名稱 jp_context）
├── verb_pair.py    # JPVerbPairCreator，以及 JPVerbPairHandler（母卡＋Cloze）
└── core_verb.py    # JPCoreVerbCreator，以及 JPCoreVerbHandler（母卡＋Cloze）
```

原本的 `jp_verb_pair_handler.py`、`jp_core_verb_handler.py` 刪除，內容搬進 `verb_pair.py`、`core_verb.py`。

和討論方向的差異（都已決定）：

1. **母卡與 Cloze 的 handler 和各自的 creator 放在同一個檔案**。討論時只列出 `verb_pair.py` 放 `JPVerbPairCreator`、`core_verb.py` 放 `JPCoreVerbCreator`，但母卡與 Cloze 卡（`JP_VerbPair_*`、`JP_CoreVerb_*`）也各需要一個 handler。理由：
   - 這兩個 handler 只負責自己專案的母卡與 Cloze，和 creator 屬於同一個專案；和「只有一種建法的類型，handler 和 creator 放同一個檔案」（D10）的規則一致。
   - 兩個 creator 原本的程式（`execute_generate` 約 300 行）和這兩個 handler 共用模組層級的常數（`CARD_TYPE`、`CHILD_CARD_LLM`）與輔助函式，放同一個檔案就不用再多一層 import。
2. **資料夾叫 `jp_verbs/`，不叫討論時的 `jp_context/`**。理由：
   - 資料夾裡放的是日文動詞的三種卡（自他動詞、核心動詞，以及兩者共用的 Context 卡），三個 handler 裡只有一個是 Context。叫 `jp_context/` 的話，要找自他動詞或核心動詞的程式的人，得先知道它們藏在「Context」底下。
   - 資料夾名稱和 handler 名稱分開，不會混淆：handler 名稱仍是 `jp_context`（Q1），它住在 `jp_verbs/context.py`；`jp_verbs` 不是任何 handler 或 creator 的名稱，不會出現在網址、用途鍵或資料庫裡，之後要改也只是搬檔案。
3. **Context 的 handler 放在 `context.py`，不叫討論時的 `handler.py`**。資料夾裡有三個 handler，`handler.py` 看不出是哪一個；以 `context.py`、`verb_pair.py`、`core_verb.py` 三個並列，檔名就對應卡片。

`JP_Context_Dark` 另開 handler 的理由：

- 這正好是筆記類型本來的樣子：`JP_Context_Dark` 是兩個專案共用的「通用文脈子卡」模型（`backend/scripts/local_anki/JP_VerbPair/import_models.py:13`、`JP_CoreVerb/import_models.py:13`）；一張 Context 卡屬於哪個專案，看的是它的 `Master_Note_ID` 欄位，不是筆記類型。
- `jp_context` 的 `delete.reason` 可以照實寫：「依母卡所屬的專案，用 `scripts/local_anki/JP_VerbPair/delete_child_cards.py` 或 `JP_CoreVerb/delete_child_cards.py`」，解掉 §1.3 第 1 點。
- 兩種 jp 卡的清單只列自己的母卡與 Cloze 卡，解掉 §1.3 第 2 點；Context 卡在卡片頁另有「日文語境卡」可以選。
- Context 卡只能從 `/handlers/jp_context/...` 讀，解掉 §1.3 第 3 點。
- 兩個專案的 Context 卡建好之後的行為本來就完全一樣（只讀、由本機腳本管理），沒有理由分成兩份；新名稱只有 `jp_context` 一個，它沒有任何存量資料，不需要遷移。

沒有採用的替代方案：

- **兩種 jp 合成一個 handler（五種筆記類型）**：規則也成立，但卡片頁會把「自他動詞」與「核心動詞」併成一組，失去現在的分類；而且一個 handler 底下有兩個 creator，要等第二階段才支援（D7），等於被迫連第二階段一起做。
- **依卡片內容（`Master_Note_ID` 指向的母卡）判斷 Context 卡歸誰**：每次查詢都要多讀一次母卡，換來的只是兩份一模一樣的行為（D9）。

Q5 的三處差異之外，不受影響的東西：LLM 用途鍵 `jp_verb_pair`、`jp_core_verb` 與 `CARD_TYPE` 是程式裡的字串，跟著搬家、值不變，資料庫的能力路由與 `card_gen_log`、`rel_links` 照樣對得上；只有測試裡寫死模組路徑的兩處（`test_llm_routing.py:42-43`、`test_recording_eval_routing.py:448`）要跟著改。

### D4 註冊表：handler 依筆記類型一對一登記，creator 依名稱登記

```python
class HandlerRegistry:
    _handlers: dict[str, BaseHandler]               # handler_name → handler
    _handler_by_note_type: dict[str, BaseHandler]   # 筆記類型 → handler（一對一）
    _creators: dict[str, BaseCreator]               # creator_name → creator
```

| 現在 | 之後 | 說明 |
|---|---|---|
| `register(handler)`、`@register_handler` | 保留，只收 handler | |
| — | `register_creator(creator)`、`@register_creator` | 新增 |
| `_by_note_type: dict[str, list[BaseHandler]]` | `_handler_by_note_type: dict[str, BaseHandler]` | 筆記類型已經有 handler 時直接報錯（`ValueError`，訊息列出兩個 handler 名稱），而且不留下任何一半的註冊 |
| `handler_for_note_type(note_type)`（取第一個） | 名稱不變，回唯一的那一個 | 呼叫端都不用改 |
| `handlers_for_note_type(note_type)` | 刪除 | 只有測試在用 |
| `get_handler(name)`、`all_handlers()` | 名稱不變，只找 handler | 順序只影響清單排序，不再影響行為 |
| — | `get_creator(name)`、`all_creators()` | 新增；找不到時拋 `HandlerNotFoundError`（錯誤碼 `HANDLER_NOT_FOUND` 不變，訊息改說「建卡流程」） |
| `list_all_handlers()` | 刪除 | 沒有任何呼叫端（死碼） |
| `_check_shared_note_types` | 刪除 | 見 D8 |
| `validate_handler` | `validate_handler`、`validate_creator` | 把現在的檢查表依 D2 分兩半，條件不變 |
| — | `check_creators()` | 新增：所有模組 import 完之後呼叫一次（D5） |

`validate_handler`（`registry.py:213`）的檢查依角色分配：

- **handler**：`title`、`supported_models` 非空；`update_json` 要有 `JSON_FIELD_SPECS`；`JSON_FIELD_SPECS ⊂ JSON_FIELDS`；`MEDIA_FIELDS` 的種類；JSON 與媒體不重疊；`search` 要有 `SEARCH_FIELDS` 且鍵不重複、不用保留字；宣告動作要覆寫 `execute_update`；樹狀圖譜要覆寫 `execute_read_graph`；`graph.links` 與 `RelationGraphMixin` 一致；`_unknown_declared_fields`。
- **creator**：`title` 非空；`script` 通道要有 `execute_generate`；`CREATE_PARAMS` 與覆寫 `execute_create` 的對應；預覽流程要有 `DRAFT_PARAMS`、`CREATE_PARAMS`、`execute_draft`。

### D5 Creator 怎麼指向 handler

Creator 不複製 handler 的宣告，而是向它指向的 handler 拿：

- **`HANDLER`**：這個 creator 產出、列在底下的 handler（網頁的建卡選單、`GET /handlers` 的合併，見 D7）。`create.note_type` 有值時，必須在這個 handler 的 `supported_models` 裡。
- **`PRODUCES`**：這個 creator 會寫出哪些筆記類型，預設是 `(create.note_type,)`；jp 兩個（Context＋Cloze）、`expression_correction`（Master＋Micro）照實列出（三語卡的子卡和主卡是同一種筆記類型，用預設即可）。每一個都必須有 handler。為什麼除了 `HANDLER` 還要 `PRODUCES`：jp 的 creator 會寫出兩個 handler 的筆記（`jp_context` 與自己的），只指向一個 handler 說不完整；而 `HANDLER` 仍需要，因為網頁和 `GET /handlers` 要知道它列在哪一個底下。
- **`check_creators()`**：`HANDLER` 存在、`create.note_type` 屬於它、`PRODUCES` 每一個都有 handler，然後把 handler 綁到 creator 上（`BaseCreator.handler` 回傳它）。因為 `jp_context` 的 handler 與兩個 jp creator 在不同模組，這個檢查要等全部註冊完才能做：`task_handlers/__init__.py` 在 import 完所有模組後呼叫一次，不過就 `RuntimeError`（和現在註冊失敗一樣，啟動失敗）。
- **標準建立的媒體與 JSON 欄位**：`NoteService._standard_params_model`（`note_service.py:194`）、`_create_media_fields`（`:1124`）改讀 `creator.handler` 的 `MEDIA_FIELDS`、`JSON_FIELDS`；標準建立的 `execute_create` 呼叫 `self.handler.field_texts(...)`。
- **欄位搜尋不再從 `CREATE_PARAMS` 產生**：`search_fields()` 現在沒宣告 `SEARCH_FIELDS` 時會從 `CREATE_PARAMS` 產生（`base.py:184`），這讓 handler 依賴建卡參數。實際上開放搜尋的只有 `person_profile`（`person_profile_handler.py:405`）與 `speaking_trilingual`（`speaking_trilingual_handler.py:247`），兩個都有明確宣告 `SEARCH_FIELDS`，備援沒有人用。拿掉備援，`search` 開放就必須宣告 `SEARCH_FIELDS`（`validate_handler` 檢查）。
- **標籤**：`NoteService.tags`（`note_service.py:343`）查的是「這些筆記類型用過的標籤」，屬於 handler；建卡畫面的標籤自動完成用 creator 的 `HANDLER` 去查。`tag_rules`（`tag_rules.py:71`）收集系統標籤改成走 `all_creators()` 的 `CREATE_TAGS`。

### D6 名稱相容：creator 沿用現在的 `handler_name`

- creator 沿用現在的 `handler_name`：`vocabulary_mining`、`expression_correction`、`speaking_coach`、`speaking_trilingual`、`person_profile`、`jp_verb_pair`、`jp_core_verb`。建卡 API 的網址、草稿的 `handler_name`、`card_gen_log.card_type` 都不變。
- 一種建法的類型，handler 和 creator 同名，所以 handler 的名稱也不變：`recording_eval.speaking_coach`、`recording_eval.speaking_trilingual` 這類用途鍵、`GET /notes/{id}` 的 `handler`、前端 localStorage 鍵裡的名稱都照舊。（鍵的前綴另外由 `feat/retire-generate` 從 `generate_*` 改成 `create_*`，見 `docs/wip/retire_generate_page_FEAT_2026-11-23.md` D5；與這次拆分無關。）
- 唯一的新名稱是 handler `jp_context`，它沒有任何存量資料。

| 存在哪裡 | 鍵 | 之後指的是 |
|---|---|---|
| 資料庫的能力路由 | `recording_eval.speaking_coach`、`recording_eval.speaking_trilingual` | handler 的 `RECORDING_EVAL.route`（值不變） |
| 資料庫的能力路由 | `jp_verb_pair`、`jp_core_verb`（`CHILD_CARD_LLM`） | 模組層級的 `route(...)` 常數，跟著搬到 `jp_verbs/verb_pair.py`、`core_verb.py`，鍵不變 |
| 資料庫 `card_gen_log.card_type`、語料庫 `rel_links.created_by` | `jp_verb_pair` 等（`jp_verb_pair_handler.py:44` 的 `CARD_TYPE`） | 模組自己的常數，與註冊表無關 |
| 記憶體裡的草稿 | `Draft.handler_name`（`create_session.py:125`） | creator 名稱；草稿只在記憶體，重啟就清空（`create_session.py:21`） |
| 前端 localStorage | `create_handler`、`create_created:<名稱>`（原本是 `generate_*`，`feat/retire-generate` 改名）、圖譜的 `<名稱>\|<筆記類型>\|<牌組>` | 第一階段名稱不變；第二階段也沿用同樣的值 |
| Anki | 筆記類型、欄位、標籤 | 完全不動 |

### D7 API：形狀不變；第二階段這次不做

（§8 Q2 已決定，2026-11-28：這次只做第一階段。第二階段等到真的要加同一筆記類型的第二種建法時，另開計畫書；下面留著它的方向。）

為什麼第二階段先不做：

- 第一階段已經解決這次要解決的問題：「取第一個註冊的」、一致性檢查，以及 §1.3 jp 的三個錯誤行為。
- 第二階段的好處是名詞一致（建卡網址叫 creator），並拿掉「每個 handler 最多一個 creator、而且同名」的限制。但現在沒有任何一種筆記類型有第二種建法，這條限制目前沒有擋到任何事。
- 第二階段要改前端十幾個檔案（§4 Frontend），現在做是提早付成本、沒有立即的好處。
- 什麼時候要做有明確的訊號：真的加了同一筆記類型的第二個 creator 時，`check_creators()` 會在啟動時直接擋下來，那時再做。

**第一階段（這次做）**：`/handlers` 的網址與 `GET /handlers` 的回應形狀不變，前端不用改。

- `GET /handlers`（`NoteService.list_handlers`，`note_service.py:130`）仍是「每個 handler 一筆」：`name`、`title`、`note_types`、`json_fields`、`json_field_specs`、`media_fields`、`search_fields` 取自 handler；`capabilities` 由 handler 的能力加上 `HANDLER` 指向它的 creator 的 `create`、`fill` 合併；`create_schema`、`draft_schema` 取自 creator。沒有 creator 的 handler（`jp_context`），`create` 為不開放，`channels` 為 `script`，`reason`「由「自他動詞」、「核心動詞」的本機批次腳本產生」（依產出它的 creator 組出來：產出者全是本機批次腳本時這樣寫，否則寫「…的建卡流程一併產生」）。
- 為了維持「一個 handler 一筆」，第一階段限制**每個 handler 最多一個 creator，而且 creator 和它的 `HANDLER` 同名**（`check_creators()` 檢查；第一階段的網址只有一個名稱，要能同時當兩種用）。現在的 7 個 creator 都符合；要加同一筆記類型的第二種建法時，就必須先做第二階段。
- 建卡端點帶的名稱是 handler、卻沒有同名 creator 時（`jp_context`），回 400 `HANDLER_OPERATION_UNSUPPORTED` 並附上上面的原因，和其他不開放建卡的類型一致；名稱既不是 creator 也不是 handler 才回 404 `HANDLER_NOT_FOUND`（D11 第 2 點）。
- 各端點依用途找角色：

| 端點 | 第一階段找誰 |
|---|---|
| `POST /handlers/{name}/notes`、`POST /{name}/drafts`、`POST /{name}/fill`、`/drafts/...` 的重新生成與建立 | `get_creator(name)` |
| `GET /{name}/tags`、`GET/PATCH/DELETE /{name}/notes/{id}`、`PUT /{name}/notes/{id}/json/{field}`、`POST /{name}/search-query`、`GET /{name}/graph` | `get_handler(name)`（和現在一樣） |
| `GET /notes/{id}`、TG 的 `run_action` | `handler_for_note_type(modelName)`（和現在一樣，但答案唯一） |

**第二階段（這次不做，留作日後的方向）**：

- 建卡端點改成 `/creators/{name}/notes`、`/creators/{name}/drafts`、`/creators/{name}/fill`；新增 `GET /creators`。`GET /handlers` 只回 handler 自己的欄位。
- 前端的 `HandlerInfo` 拿掉建卡的欄位，新增 `CreatorInfo`；`ServerDraft.handler` 改名 `creator`。
- 解除「每個 handler 最多一個 creator、而且同名」的限制。

單人使用、前後端同一次部署，不需要保留舊網址的別名。

### D8 `_check_shared_note_types` 刪除，改成筆記類型重複註冊就報錯

可以刪，連同 `existing_note_ops()` 一起。理由：

- 它要保證的是「從筆記類型找到的 handler，不管是哪一個，行為都一樣」。拆完之後從筆記類型只會找到一個 handler（D4 的註冊檢查），「哪一個」的問題不存在，沒有東西需要比對。
- creator 可以有很多個，但它們沒有「建好之後的行為」可以不一致，因為那些宣告根本不在 `BaseCreator` 上。
- 取代它的是更嚴格、更簡單的規則：**同一筆記類型註冊第二個 handler 就報錯**。§1.2 列出的漏比對項目（`RECORDING_EVAL`、`reason`、JSON／媒體欄位……）自然全部涵蓋，之後新增的宣告也不用記得加進比對。
- `existing_note_ops()` 除了這個檢查與 `test_handler_declarations.py:133` 之外沒有呼叫端。
- `task_handlers/__init__.py:11-13` 的「取第一個註冊的」註解改成「順序只決定清單排序」。

### D9 考慮過、沒有採用的做法

| 做法 | 為什麼不採用 |
|---|---|
| **最小改**：`BaseHandler` 不拆，多一個「這個 handler 是不是筆記類型的擁有者」的宣告，`handler_for_note_type` 只看擁有者 | 能解掉「取第一個」與一致性檢查，但非擁有者身上仍掛著一整套「建好之後」的宣告（例如 `jp_core_verb` 的 `delete.reason`），寫了卻永遠不會被用到，讀程式的人分不出哪些有效 |
| **另取新名字（例如 `CardType` 與 `Creator`），`Handler` 這個詞整個退場** | 意思一樣，但 `handler_for_note_type`、`get_handler`、`/handlers/{name}/notes/{id}`、`note.handler` 這些原本就對的名字也要全部改，改動量大一倍，換來的只是名詞 |
| **每種筆記類型一個 handler（一對一，jp 母卡、Cloze 也各自一個）** | jp 兩組各三種、`expression_correction` 兩種，都要拆成好幾個 handler；但卡片頁、圖譜、欄位搜尋都是以「一組筆記類型」為單位（例如 `tags`、`query_notes`、`note_types_for`），會多出一層「handler 群組」，比現在更複雜 |
| **把兩種 jp 合成一個 handler（五種筆記類型），底下兩個 creator** | 也符合規則，但卡片頁會把「自他動詞」與「核心動詞」合成一組，失去現在的分類；而且第一階段要求「一個 handler 最多一個 creator」，必須連第二階段一起做（§8 Q1 已決定不採用） |
| **共用筆記類型時改成依卡片內容（例如 `Master_Note_ID` 的母卡）判斷歸誰** | 每次查詢都要多讀一次 Anki，而且 Context 卡在兩個專案的行為本來就一樣，沒有需要分開的地方 |

### D10 只有一種建法的類型：分成兩個類別，放在同一個檔案

（§8 Q6 已決定，2026-11-28：採寫法 A。）考慮過的兩種寫法：

```python
# 寫法 A：分成兩個類別，放在同一個檔案（採用）
@register_handler
class SpeakingCoachHandler(BaseHandler):
    CAPABILITIES = HandlerCapabilities(actions=SPEAKING_ACTIONS)
    RECORDING_EVAL = RecordingEvaluation(RECORDING_EVAL_COACH, _recording_target)
    ...

@register_creator
class SpeakingCoachCreator(BaseCreator):
    HANDLER = "speaking_coach"
    CAPABILITIES = CreatorCapabilities(create=CreateCapability(supported=True, channels=("web", "tg"), ...))
    CREATE_PARAMS = SpeakingCoachCreateParams
    ...

# 寫法 B：一個類別同時繼承兩者
@register_handler
@register_creator
class SpeakingCoachHandler(BaseHandler, BaseCreator):
    CAPABILITIES = HandlerCapabilities(actions=SPEAKING_ACTIONS)
    CREATE_CAPABILITIES = CreatorCapabilities(...)   # 不能也叫 CAPABILITIES
    HANDLER = "speaking_coach"                       # 指向自己
    ...
```

| | 寫法 A：兩個類別 | 寫法 B：一個類別繼承兩者 |
|---|---|---|
| 角色的邊界 | 清楚：creator 只能經 `self.handler` 碰建好之後的宣告，一看就知道哪裡跨了角色 | 模糊：建卡的程式可以直接讀 `self.JSON_FIELDS`、呼叫 `self.execute_update`，又回到現在「兩件事混在一起」的狀態 |
| 撞名 | 不會：兩邊都叫 `CAPABILITIES`、都有 `title`，各在自己的類別上 | 會：`CAPABILITIES` 兩邊型別不同，要把其中一邊改名（例如 `CREATE_CAPABILITIES`），兩種寫法的 creator 屬性名稱因此不一致；`title`、名稱也只能有一份 |
| 註冊與檢查 | 各註冊一次、各檢查一份 | 同一個實例註冊進兩份索引；`validate_*` 要處理「同時是兩者」；MRO 決定同名方法用誰的，多一個要記的規則 |
| 加第二種建法時 | 新增一個 creator 類別即可，既有的不用動 | 必須先把這個類別拆成寫法 A，才能加 |
| 測試 | 可以檢查「creator 沒有任何建好之後的宣告」 | 檢查不了，因為它就是 handler |
| 樣板 | 多一個類別、`title` 寫兩次（可以讓 creator 預設沿用 handler 的 `title`） | 少一個類別，改動的行數較少 |
| 共用的輔助函式 | 放在模組層級，兩個類別都能用（現在大多已經是這樣） | 可以是實例方法 |

**採用寫法 A**：寫法 B 省下的只有樣板，卻讓 D8 刪掉的那種「靠紀律維持一致」又回來，而且撞名一定會發生。寫法 A 多出的樣板可以用「creator 的 `title` 預設沿用 handler」減少。

展開來說：

- **撞名**：兩個角色都有 `CAPABILITIES`，型別不同（對既有卡片的能力 vs. 建卡能力），寫法 B 只能把其中一個改名，結果有些 creator 的屬性叫 `CAPABILITIES`、有些叫 `CREATE_CAPABILITIES`，同一個東西兩種名字。
- **邊界**：寫法 B 的建卡程式可以直接讀 `self.JSON_FIELDS`、呼叫 `self.execute_update`，邊界只能靠自律；這次刪掉的一致性檢查，正是「靠自律維持」出問題的例子。寫法 A 的 creator 碰 handler 的東西一定要經過 `self.handler`，一眼看得出哪裡跨了角色，也能寫測試檢查「creator 身上沒有建好之後的宣告」。
- **之後加第二種建法**：寫法 B 必須先把類別拆成寫法 A 才能加；寫法 A 只要再新增一個 creator 類別，既有的不用動。

放在同一個檔案的理由：兩個類別共用的輔助函式（三語卡的語言判斷、人脈卡的欄位定義等）現在大多已經是模組層級的函式，放同一個檔案不用多一層 import，打開一個檔案就看到這種卡的全貌。

### D11 審查後的修正（2026-11-28，多面向審查：程式、測試、註解、錯誤處理、型別）

審查沒有 Critical；以下 8 項 Important 已修正：

1. **`fill` 沒有測試保護**：審查者把人脈卡的 `fill` 從 `GET /handlers` 拿掉，原本的測試照樣全過。新增 `test_creation_capabilities_come_from_the_creator`，逐筆比對合併後的能力鍵、handler 的能力與 creator 的 `create`／`fill`；`test_handler_declarations._caps()` 改走正式的 `NoteService.list_handlers()`，不再在測試裡重寫一次合併。
2. **沒有 creator 的 handler 建卡回 404**：`POST /handlers/jp_context/notes`、`/drafts`、`/fill` 原本回「找不到建卡流程」。`NoteService._creator()` 改成：名稱是 handler 但沒有 creator 時，回 400 `HANDLER_OPERATION_UNSUPPORTED` 與 `GET /handlers` 顯示的原因（D7）。
3. **晚註冊的 creator 不會被綁定**：`check_creators()` 通過之後再 `register_creator` 的 creator，原本永遠不會檢查也不會綁定。現在註冊當下就跑一次 `check_creators()`，不通過就撤回這筆註冊。
4. **`execute_generate` 的檢查漏了 TG**：註冊表只在 `script` 通道時檢查，而 TG 的外語糾錯（`channels` 沒有 `script`）也呼叫它。以 `test_tg_expression_flow_has_execute_generate` 守住簽名，並修正 `BaseCreator` 的說明；沒有為此新增宣告欄位。
5. **`create.note_type`／`PRODUCES` 的檢查太鬆**：`create.note_type` 有值就檢查它歸 `HANDLER`（原本只在開放建卡時）；宣告了 `PRODUCES` 時也必須包含 `create.note_type`。
6. **`GET /handlers` 默默取第一個 creator**：新增 `HandlerRegistry.creator_for_handler()`，遇到多個時拋錯；`list_handlers` 改用它，第二階段解除限制時一定會回頭改這裡。
7. **過時的註解**：搬到 creator 之後仍寫「handler」的地方（`tag_rules`、jp 的 `CARD_TYPE`、`ExpressionCreateParams`、`create_session` 的 `handler_name`、`cloze_positioning` 等）改成 creator；`CreatorNotFoundError` 改寫成「錯誤碼與狀態不變，訊息改說建卡流程」；`base.py` 的草稿輔助改寫成「不屬於任何一方」；各模組 docstring 補上「放 handler 與 creator」。
8. **計畫書代號撞號**：程式裡指 handlers 計畫書的 D 編號補上「handlers 計畫書」，`base.py`、`note_service.py` 的模組 docstring 註明裸寫的 D 編號指 handlers 計畫書；本計畫一律寫成「handler 拆分計畫書」／「handler/creator split plan」，並改用檔名，不寫 `docs/wip/` 路徑（歸檔後路徑會變）。

審查提到、這次沒做的建議：handler 身上不能有 creator 專屬成員的反向檢查、完整「筆記類型 → handler」對照表的測試、`bind()` 驗證傳入的 handler、`generate_expression_cards.py` 的寬 catch 與沒關連線、`CHANNELS` 改由 `Channel` 推導、jp 的 `JP_Context_Dark` 字串常數共用。

## 4. 改動清單

以下以 `feat/routing-three-layers` 合併後的 `main` 為基準。因為 handler 沿用原名，「從筆記類型找 handler」與讀、改、刪的呼叫端大多不用改；要改的是建卡的那一半。

### Backend：handler 與 creator 本體

| 檔案 | 改動 |
|---|---|
| `app/services/task_handlers/capabilities.py` | `HandlerCapabilities` 拿掉 `create`、`fill`；新增 `CreatorCapabilities(create, fill)`；刪除 `existing_note_ops()` |
| `app/services/task_handlers/base.py` | `BaseHandler` 拿掉 D2「搬到 Creator」的成員；`search_fields()` 拿掉 `CREATE_PARAMS` 備援；共用的輔助函式與 `DuplicateResolution` 留著 |
| `app/services/task_handlers/creator.py`（新增） | `BaseCreator`：標準建立與草稿相關的預設實作，`HANDLER`、`PRODUCES`、`handler` |
| `app/services/task_handlers/registry.py` | D4 全部：handler 一對一索引、creator 索引、`register_creator`、`get_creator`、`all_creators`、`check_creators()`、`validate_creator`；刪除 `_check_shared_note_types`、`handlers_for_note_type`、`list_all_handlers` |
| `app/services/task_handlers/__init__.py` | 改 import `jp_verbs` 套件；最後呼叫 `handler_registry.check_creators()`；改寫順序的註解 |
| `app/services/task_handlers/vocabulary_mining_handler.py` | 拆出 `VocabularyMiningCreator`（D10 寫法 A，同一個檔案） |
| `app/services/task_handlers/expression_handler.py` | 拆出 `ExpressionCorrectionCreator`（預覽流程、`execute_generate`）；`PRODUCES` 列 Master 與 Micro |
| `app/services/task_handlers/speaking_coach_handler.py` | 拆出 `SpeakingCoachCreator`；`RECORDING_EVAL` 留在 handler |
| `app/services/task_handlers/speaking_trilingual_handler.py` | 拆出 `SpeakingTrilingualCreator`（建立參數、草稿、`resolve_create_deck`）；`RECORDING_EVAL`、`SEARCH_FIELDS`、樹狀圖譜留在 handler |
| `app/services/task_handlers/person_profile_handler.py` | 拆出 `PersonProfileCreator`（建立參數、`fill`）；動作、JSON 欄位、照片、搜尋留在 handler；`new_card_id` 等模組函式不動（`scripts/local_anki/Person_Profile_Dark/backfill_card_ids.py:35` 在用） |
| `app/services/task_handlers/jp_verbs/`（新增）`context.py`、`verb_pair.py`、`core_verb.py` | D3：`JPContextHandler`（handler 名稱 `jp_context`；只讀，改欄位、刪除不開放並寫明兩個刪除工具）；`JPVerbPairHandler`＋`JPVerbPairCreator`；`JPCoreVerbHandler`＋`JPCoreVerbCreator`；`CARD_TYPE`、`CHILD_CARD_LLM` 跟著搬，值不變 |
| `app/services/task_handlers/jp_verb_pair_handler.py`、`jp_core_verb_handler.py` | 刪除（內容搬進 `jp_verbs/`） |
| `app/services/task_handlers/shared/cloze_positioning.py` | 只有文件字串提到舊檔名，跟著改 |

### Backend：呼叫端

| 檔案:行 | 現在 | 改成 |
|---|---|---|
| `app/services/note_service.py:130` `list_handlers` | `all_handlers()` | 不變，再合併 `HANDLER` 指向它的 creator（D7） |
| `note_service.py:173` `_create_schema`、`:194` `_standard_params_model`、`:1124` `_create_media_fields`、`:1142` `_draft_schema` | 收 `BaseHandler` | 收 `BaseCreator`，欄位宣告向 `creator.handler` 拿（D5） |
| `note_service.py:211` `create`、`:374` `validate_draft_input`、`:408` `require_fill`、`:427` `resolve_draft_duplicate`、`:503` `generate_draft` | `get_handler(name)` | `get_creator(name)` |
| `note_service.py:343` `tags`、`:556` `read`、`:592` `get`、`:749` `search_query`、`:805` `run_action`、`:878` `graph`、`:923` `_owning_handler`、`:1079`、`:1166` | `get_handler`／`handler_for_note_type` | 不變 |
| `app/bot/handlers/voice.py:254`、`app/main.py:159` | `handler_for_note_type`、`all_handlers()` 讀 `RECORDING_EVAL` | 不變 |
| `app/services/tag_rules.py:71` | `all_handlers()` 的 `CREATE_TAGS` | `all_creators()` |
| `app/bot/handlers/fsm/expression_fsm.py:687` | `get_handler("expression_correction").execute_generate` | `get_creator(...)` |
| `app/bot/handlers/fsm/vocabulary_fsm.py:143` | `get_handler(HANDLER_NAME).CAPABILITIES.create.note_type` | `get_creator(...)` |
| `app/api/verb_pair.py:16,75`、`app/api/core_verb.py:25,88` | import `jp_*_handler` 並自己 `JPVerbPairHandler()` | 改成 `handler_registry.get_creator("jp_verb_pair")` 等，不再自己建實例 |
| `app/api/handlers.py`、`app/api/drafts.py` | 經 `NoteService`，本身不碰註冊表 | 第一階段不用改程式；文件字串依 D7 表格說明網址上的名稱是哪種角色 |
| `app/services/recording_eval.py`、`task_handlers/shared/recording_spec.py` | 文件字串寫「handler 名」 | 不變（handler 仍是 handler） |
| `scripts/local_anki/Expression_Correction/generate_expression_cards.py:71` | `get_handler("expression_correction")` | `get_creator(...)` |

`HANDLER_NAME = "person_profile"`（`bot/handlers/person_profile.py:112`、`services/person_profile_ingest.py:947`）同時拿來建卡與改欄位；handler 與 creator 同名，不用改。其他 `scripts/` 只 import handler 模組裡的函式（`person_profile_handler.new_card_id`、`shared.trilingual_familiarity`），不碰註冊表與 handler 類別（已 grep 確認）。

### Frontend

**第一階段**：程式不用改。看得到的變化只有資料驅動的部分：卡片頁、工具設定頁的「Handler 能力」、卡片頁建卡畫面的卡片類型清單（不能選，附原因）多出「日文語境卡」，兩種 jp 卡的 `note_types` 少了 `JP_Context_Dark`。

**第二階段**（這次不做；日後做時的範圍）：

| 檔案 | 改動 |
|---|---|
| `src/types/api.ts:80` `HandlerInfo`、`:221` 草稿的 `handler` | `HandlerInfo` 拿掉 `create_schema`、`draft_schema` 與建卡能力；新增 `CreatorInfo`；草稿的 `handler` 改名 `creator` |
| `src/api/client.ts:104-160` | 新增 `listCreators`；`createNote`、`startDraft`、`startFill` 走 `/creators/...`；讀、改、刪、搜尋、圖譜（`:243-320`）不變 |
| `src/components/create/CreatePanelBody.tsx`、`PreviewCreate.tsx`、`TagInput.tsx`（原本在 `components/generate/`，`feat/retire-generate` 搬過來）、`src/hooks/useCreatedNotice.ts`、`src/hooks/useOpenCreateForm.ts`（寫入的是草稿的 `handler`）、`src/components/shared/DraftWatcher.tsx`、`src/components/person/PersonFillDialog.tsx`、`src/components/cards/CardViewPanel.tsx:140` | 改用 creator 清單 |
| `src/components/settings/HandlerCapabilitiesSection.tsx`、`src/lib/handlerCapabilities.ts` | 每個 handler 底下列它的 creator |

### 測試

| 檔案 | 改動 |
|---|---|
| `tests/test_handler_base.py:229-346` | 註冊表的測試：`test_shared_note_type_requires_same_existing_note_ops`（`:307`）換成「同一筆記類型註冊第二個 handler 報錯、什麼都沒留下」；新增「同一 handler 可以有好幾個 creator」（第一階段的限制改到 `check_creators()` 測）；`:338` 的 `JP_Context_Dark` 對到 `jp_context`；`:345-346` 的 import 改到 `jp_verbs/` |
| `tests/test_handler_declarations.py` | `:23-24` 的 import 改路徑；`:48` 的能力快照改成 handler＋creator 兩份；`:131-133` 的共用檢查刪掉，換成「`JP_Context_Dark` 屬於 `jp_context`」與「jp 兩個 creator 的 `PRODUCES` 都有 handler」；`:138`、`:228`、`:390` 改用 `all_creators()` |
| `tests/test_llm_routing.py:42-43`、`tests/test_recording_eval_routing.py:448` | 用途鍵對應的模組路徑改成 `jp_verbs.verb_pair`、`jp_verbs.core_verb`（鍵不變） |
| `tests/test_note_service.py:233-236` | 「兩個 jp 網址都能讀 Context 卡」改成「只有 `jp_context` 讀得到，jp 兩個回 `NOTE_NOT_OWNED_BY_HANDLER`」 |
| `tests/test_note_service.py:349-355` | `test_closed_permissions_carry_reasons` 的 Context 卡 `handler` 從 `"jp_verb_pair"  # 第一個註冊的` 改成 `"jp_context"`，`reason` 要提到兩個刪除工具 |
| `tests/test_handlers_api.py:73` 一帶 | `GET /handlers` 多一筆 `jp_context`，jp 兩筆的 `note_types` 少 Context |
| `tests/test_field_search.py:89`、`tests/test_trilingual_create.py:103`、`tests/test_person_profile_handler.py:78-83` | 用到建卡的部分改 `get_creator` |
| 新增 | `check_creators()` 的失敗情境（`HANDLER` 不存在、`create.note_type` 不屬於它、`PRODUCES` 沒有 handler、第一階段一個 handler 兩個 creator）；`search` 開放卻沒宣告 `SEARCH_FIELDS` 失敗；`BaseCreator` 的子類別沒有任何建好之後的宣告 |
| 前端（第二階段才改，這次不動） | `src/lib/handlerCapabilities.test.ts` 等跟著型別改 |

## 5. 實作順序

全部在一個分支、一個 PR；每個 commit 結束時測試都是綠的。

**S0 錄下改之前的輸出（安全網）**：在還沒動程式的 `main` 上，寫一個暫時的測試，把 `NoteService.list_handlers()`（Anki 用 mock）與每種筆記類型的 `NoteService.get()` 權限輸出成 JSON 存到暫存目錄。之後每一步都和它比對；預期的差異只有 jp 與 `jp_context`（D3）。這個測試不 commit。

```powershell
cd backend
python -m pytest tests -q
```

**S1 `BaseCreator` 與註冊表的新 API（還沒有人用）**：`capabilities.py` 新增 `CreatorCapabilities`、`creator.py`、註冊表加 creator 索引與 `check_creators()`。`BaseHandler` 與舊的檢查先不動。只加新的單元測試。

**S2 拆出 creator、搬 jp**：五個一般的 handler 各拆出 creator（D10 寫法 A）、jp 搬進 `jp_verbs/`，`NoteService` 的建卡方法與 §4「呼叫端」表格同一個 commit 改完。這一步拆不開：建卡的成員一旦從 handler 搬走，`NoteService.create` 等就必須同時改找 creator。改完跑全部測試，再跑 S0 的比對。

```powershell
cd backend
python -m pytest tests -q
```

**S3 收尾**：`BaseHandler` 拿掉已搬走的成員、`HandlerCapabilities` 拿掉 `create`、`fill`；刪 `_check_shared_note_types`、`existing_note_ops()`、`handlers_for_note_type`、`list_all_handlers`；`_by_note_type` 改成一對一；更新 `__init__.py` 的註解與各處文件字串。確認沒有殘留：

```powershell
git grep -nE "handlers_for_note_type|existing_note_ops|_check_shared_note_types|list_all_handlers|jp_verb_pair_handler|jp_core_verb_handler" -- backend
```

預期只剩文件字串裡提到舊檔名的歷史說明（若有，一併改掉後應該沒有輸出）。

**S4 本機實測**：起本機後端與前端，確認：卡片頁的七種卡加上「日文語境卡」都列得出來，各自只列自己的筆記類型；點一張核心動詞的 Context 卡，刪除的說明提到兩個刪除工具；網頁建卡（單字卡、對話卡、三語卡、人脈卡）與預覽流程正常；TG 錄音評分（對話卡、三語卡）、TG 具名動作（人脈卡）正常。

S4 結果（2026-11-28）：本機後端（這條分支，`TG_BOT_TOKEN` 設空，不碰正式機的 webhook）連 `192.168.50.172` 的 Anki 與 MySQL，前端用 vite 開發伺服器。

- 卡片頁：卡片類型清單有「日文語境卡」；選它只列 `JP_Context_Dark`，選自他動詞、核心動詞只列各自的母卡與 Cloze，筆記類型子選單也只剩兩種。
- 核心動詞的 Context 卡打開「筆記欄位」：刪除說明列出 `JP_VerbPair` 與 `JP_CoreVerb` 兩個刪除工具（拆分前只寫 `JP_VerbPair`）。
- 建卡畫面：單字卡、外語糾錯、對話卡、三語對話卡、人脈卡的表單都正常出現（人脈卡有「用一段話建立」）；自他動詞、核心動詞、日文語境卡不能選並附原因。
- 工具設定頁的「Handler 能力」有日文語境卡與兩個刪除工具的說明；知識圖譜的單字卡關聯圖、三語卡主題樹照常回傳。
- 與這條分支的基底（`feat/routing-three-layers`）同時起在另一個埠比對真實 Anki 的 `GET /handlers`：只差 jp 兩筆的 `note_types` 與新增的 `jp_context`。單字卡關聯圖在兩邊都沒有連線，是既有狀態。
- `POST /handlers/jp_context/notes` 回 400 `HANDLER_OPERATION_UNSUPPORTED` 與原因。
- 寫入：經使用者同意，在「測試」牌組各建一張單字卡（標準建立）與人脈卡（覆寫建立），欄位、系統標籤（`VocabularyMining`／`PersonProfile`＋`HandlerGenerated`）與使用者標籤、人脈卡的系統欄位都正確，隨即從 `DELETE /handlers/{name}/notes/{id}` 刪掉，之後讀取回 404；同步沒有提醒。沒有跑 LLM 的預覽流程。
- 沒測到：TG 的錄音評分與具名動作（本機開機器人會搶走正式機的 webhook），留到部署後實測。
- 前端：`npm run lint` 0 個錯誤（18 個既有的 `no-explicit-any` 警告）、`npm test` 439 個通過、`npm run build` 成功（前端程式這次沒改）。

```powershell
cd frontend
npm run lint
npm test
npm run build
```

第二階段（D7 的建卡端點與前端拆分）不在這次的範圍；之後要做時另開計畫書、分支與 PR。

## 6. 風險與未知

- **S2 一次改很多檔案**：五個 handler、jp 搬家、`NoteService` 的建卡方法同一個 commit。應對：S0 的輸出比對當作「行為不變」的閘門，差異只能出現在 jp；`test_handler_declarations.py` 的能力快照也會抓到宣告搬錯的地方。
- **和進行中的計畫書衝突**：`speaking_monologue_FEAT_2026-11-26.md` §5.1 的主題樹用 `handler_for_note_type` 把筆記轉成樹節點。handler 沿用原名，這部分不衝突；但長文卡的建卡流程要寫成 creator。已決定本案先做（§8 Q4）：長文卡若先做，新卡會照舊寫成「一個 handler 包辦一切」，拆分時要多拆一個；本案先做，長文卡一開始就寫成「handler＋creator」，不用寫兩次。`speaking_monologue` 的計畫書開工前要依本案改寫建卡的段落。
- **`feat/routing-three-layers` 還沒合併**：`RECORDING_EVAL`、`recording_spec.py`、`voice.py` 的新寫法都在那個分支。已決定等它合併後才開始（§8 Q4），理由：
  - `RECORDING_EVAL` 就是那個分支新增的，也是這次拆分的起因（審查時發現共用筆記類型的檢查沒涵蓋它）；等它合併後再拆，`RECORDING_EVAL` 直接放進 handler，不用處理兩邊的合併。
  - 兩案都會改 `base.py`、`speaking_coach_handler.py`、`speaking_trilingual_handler.py`，同時進行一定衝突，而那個分支當時還沒提交。
  - 因為 handler 沿用原名，`voice.py`、`main.py` 的 `handler_for_note_type`、`all_handlers()` 本案都不用改，衝突只會在上面三個檔案。
- **本機腳本**：`scripts/fastapi_client/JP_*/generate_child_cards.py` 經 `POST /verb-pair/generate-child-cards`、`/core-verb/...` 呼叫，網址不變；`generate_expression_cards.py` 直接 import 註冊表，S2 一併改。
- **`fill` 歸 creator 之後的前端判斷**（§8 Q3 已決定）：AI 整理會寫進「現有的筆記」或「新建一張」，兩邊都沾得上，決定歸 creator。卡片頁顯示「AI 整理」按鈕的判斷（`CardViewPanel.tsx:140`，依 `note_types` 找開放 `fill` 的那筆）在第一階段因為合併的回應不變而照常運作；第二階段要改成「這張卡的 handler 底下，有開放 `fill` 的 creator」。
- 其他無已知風險。

## 7. 驗收標準

- [ ] 同一筆記類型註冊第二個 handler 時，啟動失敗並指出兩個名稱；註冊表沒有留下任何一半的狀態。
- [ ] S3 的 `git grep` 沒有輸出。
- [ ] 調換 `task_handlers/__init__.py` 的 import 順序，除了 `GET /handlers` 的排序之外，測試結果不變。
- [ ] `BaseCreator` 的子類別都沒有 D2「留在 Handler」的成員（測試檢查）。
- [ ] `GET /notes/{id}` 對核心動詞的 Context 卡，`handler` 為 `jp_context`，`delete.reason` 提到兩個刪除工具，不再只寫 `JP_VerbPair`。
- [ ] 卡片頁選「自他動詞」只列 `JP_VerbPair_*`，選「核心動詞」只列 `JP_CoreVerb_*`，「日文語境卡」列 `JP_Context_Dark`。
- [ ] S0 的輸出比對：除了 jp 兩筆與新的 `jp_context`，`GET /handlers` 與各筆記類型的權限完全相同。
- [ ] TG 錄音評分（對話卡、三語卡）照常；沒有宣告 `RECORDING_EVAL` 的卡片仍回「不支援錄音評分」。
- [ ] 後端 `python -m pytest tests -q`、前端 `npm run lint`、`npm test`、`npm run build` 全綠。
- [ ] Anki、資料庫、正式機設定都沒有被改動。

## 8. 已決定（2026-11-28）

使用者對六個事項的回覆是「都照建議走」：

| | 事項 | 決定 | 寫進 |
|---|---|---|---|
| Q1 | `JP_Context_Dark` 放哪裡 | 另開 handler `jp_context`（日文語境卡）；不把兩種 jp 合成一個 handler | D3、D9 |
| Q2 | 要不要做第二階段 | 這次不做；等到真的要加同一筆記類型的第二種建法時，另開計畫書 | D7、§2 非目標、§5 |
| Q3 | `fill`（AI 整理）歸哪邊 | 歸 creator | D2、§6 |
| Q4 | 時機 | `feat/routing-three-layers` 合併之後、`speaking_monologue` 開工之前 | 欄位表「PR / 進度」、§6 |
| Q5 | jp 資料夾的名稱與檔案分法 | 資料夾叫 `jp_verbs/`；母卡＋Cloze 的 handler 和各自的 creator 放同一個檔案；Context 的 handler 放 `context.py` | D3 |
| Q6 | 只有一種建法的類型怎麼寫 | 兩個類別，放在同一個檔案（寫法 A） | D10 |

Q5 的建議原本有兩種讀法（照討論叫 `jp_context/`，或改成 `jp_verbs/`），定為 `jp_verbs/`，理由見 D3 第 2 點：資料夾裡三個 handler 只有一個是 Context，而且資料夾名稱不會出現在網址、用途鍵或資料庫裡，和 handler 名稱 `jp_context` 分開比較不會混淆。
