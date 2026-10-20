# FEAT — 重新設計 Handlers：單一服務層，由後端宣告能力，網頁與 TG 共用

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-20 |
| **性質** | 服務層與 API 重新設計 + 實作工作項 |
| **狀態** | 🚧 實作中（2026-10-20 定案；D13 三頁版面統一已完成（dac7670）；§9 第 0 步殘骸清除已完成）。2026-10-20 已定：對照程式碼更正（§12）；本機腳本與網頁分流（D7）；清除殘骸（D8）；網頁建卡只做純手動填表（D9）；能力綁在 handler 上、以開關宣告（D2）；增刪改查都提供 `/handlers/{name}/notes/...` 版本，另保留通用的卡片查詢（D5）；`BaseHandler` 提供標準 CRUD，本次以單字卡 `TOEIC_Coach_Dark_v2` 為試點、不覆寫跑通整套流程（D10）；寫入後等同步完成（D1）；工具設定頁顯示 handler 能力（§6）；前端最後實作；口說卡網頁改欄位本次不開放（§5）；JSON 欄位改另開設計書 `json_field_editor_FEAT_2026-10-20.md`，本計畫只宣告欄位名稱（D3）；圖譜以 handler 的查詢為基礎、基底只畫單點（預設顯示 nid，可選任意欄位）、點節點開啟單張卡片，單字卡以關聯圖譜保留現有功能，本次的圖譜工作以移植現有實作為主，除單字卡外不開放任何 handler 的圖譜（D12）；媒體上傳另開設計書 `media_upload_FEAT_2026-10-20.md`；web_ui_parity 階段 A2（三頁版面統一與共用元件抽取）併入本計畫（D13）。§13 只剩之後再決定、不擋本計畫的事 |
| **範圍** | 新增 `backend/app/services/note_service.py`（單一服務層）；`backend/app/services/task_handlers/`（能力宣告、標準 CRUD、建立參數、註冊表、`__init__.py` 統一註冊）；`backend/app/api/handlers.py`（重寫）、新增 `backend/app/api/notes.py`、`backend/app/schemas/`、`backend/app/main.py`（掛新路由、移除 example 路由）；`backend/app/core/dependencies.py`、`backend/app/bot/dependencies.py`（注入服務層）；`backend/app/services/relation_service.py`（改名稱時同步更新關聯的兩端標籤，D12）；TG 呼叫 handler 的 5 處改呼叫服務層（§7）；殘骸清除（D8）；`frontend/src/api/client.ts`、`types/api.ts`、`CardGenerator.tsx`、`CardDetailModal.tsx`、`KnowledgeGraph.tsx`、`ToolSettings.tsx`、`lib/cardFields.ts`、`components/cards/`（`DeckPanel`、`CardViewPanel`）；三頁版面統一與共用元件抽取（D13）：`pages/Cards.tsx`、`lib/cardLayout.ts`、`hooks/usePanelWidths.ts`、`components/cards/panelStyles.ts`、`ErrorNote.tsx` 移到共用位置 |
| **不動** | 有覆寫的 handler 內部邏輯（口說卡的建卡與 JSON 動作、外語糾錯的建卡、jp 的 `execute_generate`）；TG 的對話流程與文字；`speaking_fsm.py` 自己組對話卡的做法（屬 web_ui_parity 階段 E）；`expression_fsm.py` 預覽用的 `execute_generate`（不寫 Anki）與「覆蓋舊卡」時的刪除（§7）；**本機批次腳本走的整條路**（D7）：`POST /verb-pair/generate-child-cards`、`POST /core-verb/generate-child-cards` 的路徑、請求、回應、驗證方式；`/jp/*`；直接呼叫 handler 或 AnkiConnect 的本機腳本；`GET /cards`、`GET /cards/{card_id}`、`/anki-media`、`/relations/*`、`/capability-routes` |
| **關聯文件** | `docs/wip/web_ui_parity_FEAT_2026-10-18.md` §7（問題 H1–H11 的盤點）、§4 階段 A2（已併入本計畫 D13）、§5 決策 5（`ValueError` 改 4xx，屬階段 B）；`docs/wip/json_field_editor_FEAT_2026-10-20.md`（JSON 欄位的表單編輯）、`docs/wip/media_upload_FEAT_2026-10-20.md`（網頁上傳媒體），兩者都排在本計畫之後 |

---

## 1. 問題

完整盤點見 `web_ui_parity_FEAT_2026-10-18.md` §7。歸納成五類：

1. **前端不知道每個 handler 能做什麼。**
   - 前端手動維護「可改欄位的 handler」白名單（`lib/cardFields.ts` 的 `FIELD_EDIT_HANDLERS`），後端一改就會失準。
   - Generate 頁把所有 handler 都列進下拉，而且不論選哪個都只送 `{ word }`，多數選了會失敗或建出空卡。
   - 後端也沒有誠實回報：`jp_verb_pair`、`jp_core_verb` 的建立與更新是空實作，卻回 200「建立成功」「更新成功」（H1、H5）；不支援圖譜的 5 個 handler 回空圖，與「沒有資料」分不出來（H6）。
   - 刪除完全沒有限制：前端對有 handler 的筆記一律允許刪除，連 jp 子卡、母卡也能刪——而 jp 子卡和母卡 JSON、MySQL 查重紀錄互相參照，通用刪除會讓三者對不上。
   - 單字卡實際上建不起來：`vocabulary_mining.execute_create` 寫的 `Meaning`、`Context` 在 TOEIC 兩種筆記類型都不存在，`CardService` 會拋 `FIELD_MISMATCH`（網頁、TG 都一樣）。
2. **建立參數沒有驗證，描述也不足。**
   - `input_schema` 手寫，不參與驗證：缺必填欄位照樣建卡（H2），jp 兩個的 schema 與實際參數對不上（H8）。
   - schema 沒有欄位名稱、說明、選項，前端無法產生表單（H7）。
   - `model_name` 不檢查是否屬於該 handler（H3）。
3. **網頁與 TG 走不同的路，行為不一致。** 兩邊都直接呼叫 handler 物件，各自在外面補邏輯：
   - **同步**：`AnkiClient` 的每個讀寫方法在呼叫 AnkiConnect 前都會先 `sync()`（10 秒防抖、失敗不拋錯，`infrastructure/anki/client.py:1209`），所以兩邊的「寫入前同步」其實都已存在。差別在**寫入後**：TG 寫入後以 `_sync_with_warning`（`callbacks.py:30`，`force=True`、`raise_errors=True`）立刻推上 AnkiWeb，失敗時提醒；REST 的建立、修改、刪除**寫入後不同步**，網頁改的卡要等下一次任何 Anki 操作（距上次同步超過 10 秒）或 Anki 自己同步才會到 AnkiWeb，失敗了也沒有提醒。
   - **刪除關聯**：`vocabulary_mining`、`speaking_coach`、`speaking_trilingual` 的刪除會連關聯一起刪；`expression_correction`、`jp_verb_pair`、`jp_core_verb` 只刪筆記，留下孤兒關聯。
   - **參數**：TG 傳什麼 handler 就收什麼，沒有驗證；`expression_fsm.py:904` 甚至傳 `relation_service=None`（以 `# type: ignore` 壓掉型別錯誤）。
   - **選 handler**：TG 各處自己決定用哪個 handler——`callbacks.py:126-133` 依欄位名稱猜是不是三語卡，`messages.py:103` 寫死 `speaking_coach`（三語卡的自訂頭像因此寫錯 handler）。
4. **路徑分組不對。**
   - `/handlers/decks`、`/handlers/cards/{note_id}` 跟 handler 無關，只是放在底下。
   - `GET /handlers/{name}/cards` 已經沒有呼叫方（前端 `listCards` 沒人用），3 個 handler 只回 `[]`。
5. **回應與錯誤形狀不一致。** 請求與回應都是 `dict`，OpenAPI 看不到欄位；`HTTPException` 與 `FluencyTidesError` 的錯誤格式不同（H9）；`expression_correction` 回傳 `list[int]`，基底類別與端點文件寫的卻是單一 `note_id`（H4）；`/handlers/cards/{note_id}` 直接回傳 AnkiConnect 的 camelCase 形狀。

## 2. 目標與非目標

**目標**

- G1 **網頁與 TG 呼叫同一個服務層**：建立、讀取、修改、執行動作、刪除筆記，都只有一份實作；驗證、能力檢查、寫入後同步都在這一層。REST 端點與 TG handler 只負責各自的輸入輸出（D11）。
- G2 **能力由後端宣告、前端照著點亮**：每個 handler 以開關宣告能做哪些操作，以及建卡走哪些管道（網頁、TG、本機腳本）；`GET /handlers` 回傳，前端只依此決定按鈕可用、反灰或隱藏，不再自己維護清單。工具設定頁可以看到全部 handler 的能力。
- G3 **能力一個 handler 一個 handler 逐一開放**：實作並驗收後才把開關打開；本計畫只建立機制，並以單字卡（`vocabulary_mining`／`TOEIC_Coach_Dark_v2`）為試點跑通網頁的增刪改查。沒開放的一律拋 `HandlerOperationUnsupportedError`（REST 回 400），不再有假成功。
- G4 **標準 CRUD 寫在父類別**：`BaseHandler` 提供單一筆記的標準建立、讀取、修改、刪除（D10）；試點不覆寫任何 CRUD 方法，直接用父類別跑通。成組卡片之後再加中間層父類別並覆寫。
- G5 增刪改查都有 `/handlers/{name}/notes/...` 的版本；另保留通用的卡片查詢（`/cards`、`/notes/{note_id}`），任何筆記類型都能瀏覽（D5）。
- G6 牌組清單移出 `/handlers`；移除沒人用的端點。
- G7 請求與回應都有 Pydantic 模型，錯誤都是 `ErrorResponse` 或 FastAPI 的 422 驗證格式。
- G8 **本機批次腳本照常可用**：`scripts/fastapi_client/` 下的 `generate_child_cards.py` 等腳本不需要任何修改，行為不變（D7）。

**非目標**

- 試點以外的 handler 在網頁開放新能力（之後依 G3 逐一做）。
- 網頁建卡的自動化（LLM 產生欄位內容）：網頁一律純手動填表，自動化走本機腳本（D9）。
- JSON 欄位的編輯（另開設計書 `json_field_editor_FEAT_2026-10-20.md`；本計畫只宣告欄位名稱，網頁上唯讀，D3）與具名動作的參數模型化。
- 筆記內容變動時同步知識圖譜的關聯（例如改了同義詞就增刪圖譜連線）。
- 成組卡片的中間層父類別與覆寫（D10「之後的繼承結構」）。
- 網頁上的外語糾錯預覽流程、`jp_verb_pair`／`jp_core_verb` 的網頁建卡（web_ui_parity 階段 E；做法見 D7「之後網頁要做時」）。
- 把本機批次腳本的流程（ES 搜尋、過濾、查重、上傳本機語音）搬到後端或網頁（D7）。
- **建卡時附加媒體**（語音、頭像、圖片）與 `POST /media` 等上傳端點（D9）。
- `speaking_fsm.py` 改用 handler 建對話卡：兩者的 Card_ID 格式與欄位不同，要先做 web_ui_parity §5 決策 4，留在階段 E。
- 刪除 JSON 條目、上傳音檔的網頁入口（階段 B、D）。服務層會提供動作的通用入口，網頁端點本計畫只預留路徑。
- handler 動作參數錯誤時拋的 `ValueError` 改成 4xx（web_ui_parity §5 決策 5，階段 B）。本計畫不開放網頁的動作端點，網頁碰不到這些 `ValueError`；TG 照舊由各處的 `except` 處理。
- 改動 TG 的對話流程與回覆文字。

## 3. 設計決策

### D1 單一服務層 `NoteService`

新增 `services/note_service.py`。網頁與 TG 對 handler 的操作都經過它；REST 端點與 TG 不再直接呼叫 `handler.execute_*`。

| 方法 | 做什麼 | 呼叫者 |
|---|---|---|
| `list_handlers()` | 名稱、標題、負責的筆記類型、能力、建立用 schema | REST |
| `create(handler, deck, note_type, params, *, tg_bot=None)` | 檢查能力 → 驗證參數 → `execute_create` → 寫入後同步；回傳 `note_ids` 與同步提醒 | REST、TG（單字卡、外語糾錯） |
| `read(handler, note_id)` | 檢查筆記歸這個 handler → `execute_read` | REST |
| `get(note_id)` | 通用讀取：欄位、負責的 handler、`permissions`；不經 handler | REST |
| `update_fields(handler, note_id, fields)` | 檢查筆記歸這個 handler、檢查能力 → `execute_update(action="update_fields")` → 寫入後同步 | REST |
| `run_action(note_id, action, params)` | 依筆記類型找 handler → 檢查 `actions` 能力 → `execute_update(action=…)` → 寫入後同步 | TG（錄音、音檔）；之後的階段 B、D 網頁端點 |
| `delete(handler, note_id)` | 檢查筆記歸這個 handler、檢查能力 → `execute_delete` → 寫入後同步 | REST |
| `graph(handler, deck)` | 檢查能力 → `execute_read_graph` | REST |

- **網址帶 handler 的方法都要先檢查「這張筆記的筆記類型確實歸這個 handler」**，不符回 400 `NOTE_NOT_OWNED_BY_HANDLER`。前端從 `GET /notes/{note_id}` 拿到 `handler` 後再組網址，正常不會不符。
- `run_action` 不帶 handler：TG 只知道筆記，由服務層依筆記類型找（D4）。之後開放網頁的動作端點時，網址帶 handler，比照上面先檢查。
- **同步規則**：
  - 寫入前不另外同步：`AnkiClient` 的每個方法呼叫前已經 `sync()`（防抖、失敗不擋）。TG 現在多餘的寫入前同步（`vocabulary_fsm.py:140`、`messages.py:87`）一併刪掉，行為不變。
  - 寫入後 `sync(raise_errors=True, force=True)`，**等同步完成才回應**（網頁與 TG 一致；TG 現在就是這樣）。失敗時回傳提醒文字而不是拋錯——就是 TG 現在 `_sync_with_warning` 的邏輯，搬進服務層（例如 `note_service.sync_after_write() -> str | None`）。回傳**純文字**（不含 TG 用的開頭換行與 HTML），REST 放進 `sync_warning`，TG 自己加上 `\n\n` 接在回覆後面。
  - 強制同步不受防抖，最久可能等到 `SYNC_TIMEOUT`（60 秒）；前端寫入時顯示「同步中」（R2）。
  - `_sync_with_warning` 保留為包裝服務層邏輯的薄函式，給還沒經過服務層的 TG 寫入點用（`commands.py` 的 `_handle_delete_entry`、`speaking_fsm.py`、`expression_fsm.py` 的覆蓋舊卡），避免兩份提醒文字。
- **依賴**：`NoteService` 由 `CardService`、`RelationService`、`AnkiClient`、註冊表組成，每個請求建立一次。REST 由 `core/dependencies.py` 新增 `get_note_service`；TG 由 `bot/dependencies.py` 的 middleware 放進 `data["note_service"]`（`relation_service` 已有，因此 `expression_fsm` 不必再傳 `None`）。

### D2 能力綁在 handler 上，以開關宣告

每個 handler 加一個類別屬性 `CAPABILITIES`，描述**服務層**能對這個 handler 做什麼（網頁與 TG 是同一份）。能力對 handler 負責的**所有**筆記類型一體適用；同一個 handler 底下的筆記類型行為不一致時，表示該拆 handler（D4）。

```json
{
  "create": {
    "supported": true,
    "channels": ["web", "tg"],
    "flow": "form",
    "deck": "selected",
    "note_type": "TOEIC_Coach_Dark_v2",
    "reason": null
  },
  "read": { "supported": true },
  "update_fields": { "supported": true },
  "delete": { "supported": true },
  "actions": [],
  "graph": { "supported": true, "links": true }
}
```

- **`supported`**：服務層是否開放這項操作。為 false 時必有 `reason`（中文，前端直接顯示）。依 G3，**有實作但還沒驗收開放的，也宣告 false**（例如口說卡的建卡在 TG 用得到，但網頁還沒開放，見 §5）。
- **`read`**：基底提供標準讀取，所有 handler 都開放（D10）。
- **`create.channels`**：這種卡實際能從哪些管道建立，值為 `"web"`、`"tg"`、`"script"` 的子集，可以是空陣列；是給人看的說明，前端依它決定 Generate 頁的顯示（§6）：含 `"web"` 可選；不含 `"web"` 但非空就反灰並顯示 `reason`；空陣列就不顯示。`channels` 不含 `"web"` 時必有 `reason`，說明要去哪裡建。
  - `"tg"` 不一定經過服務層：對話卡在 TG 是 `speaking_fsm.py` 自己組卡（§2 非目標），但對使用者而言確實能在 TG 建，所以照列。
  - `"script"` 同理，本機腳本不經服務層（D7）。
- **`create.flow`**：`"form"` 一張表單送出即可建立；`"preview"` 要先看 LLM 結果、再勾選要建的子卡（目前只有 `expression_correction`，TG 已有這個流程，網頁要等階段 E）。`supported` 為 false 時不出現。
- **`create.deck`**：`"selected"` 建在指定的牌組；`"fixed"` 時另帶 `fixed_deck`，表示會忽略指定的牌組。
- **`create.note_type`**：建卡建出的筆記類型（handler 負責多種類型時，只有其中一種能直接建立，例如外語糾錯建的是母卡）。不是另一層能力，只是建卡的設定。
- **`actions`**：`execute_update` 支援的動作（`update_fields` 另列）。服務層的 `run_action` 收到清單外的動作直接拒絕。
- **`graph`**：能否畫知識圖譜；`links` 表示圖譜有沒有關聯連線（有的話前端才顯示「建立連線」等操作，D12）。

註冊表啟動時檢查宣告是否有實作支撐，不一致就 `RuntimeError`，後端起不來。做法同 LLM 工廠的 `_verify_entry`（`infrastructure/llm/factory.py:94`）。只檢查「宣告開放 → 必須有實作」這個方向；有實作但宣告不開放是允許的（G3 的「實作了、還沒開放」）：

| 宣告 | 必須成立 |
|---|---|
| `create.supported` | `execute_create` 有實作（基底的標準建立或覆寫）；`create.note_type` 在 `supported_models` 內 |
| `update_fields.supported` 或 `actions` 非空 | `execute_update` 有實作 |
| `graph.links` | `graph.supported` 為 true，且 handler 使用 `RelationGraphMixin`（D12） |
| `channels` 含 `"web"` | `create.supported` 為 true 且 `flow` 為 `"form"` |
| `channels` 含 `"script"` | handler 有 `execute_generate` 方法（D7） |
| `supported` 為 false，或 `channels` 不含 `"web"` | `reason` 非空 |

`actions` 清單裡每個動作是否真的有實作，無法從程式結構判斷，由 §8 的測試逐一確認。

**註冊集中（H11）**：現在註冊靠 import 的副作用，分散四處——`task_handlers/__init__.py` import 4 個、`api/handlers.py` 重複 import 2 個、jp 兩個只在 `api/verb_pair.py`／`api/core_verb.py` import 時才註冊（只 import 套件的本機腳本因此看不到它們）、範例用的 `verb_pair_example` 經 `main.py` 掛 example 路由而被註冊（所以 `GET /handlers` 現在其實回 7 個）。改為 `task_handlers/__init__.py` 統一 import 6 個正式 handler，註冊順序由它決定（D4 的「第一個註冊」依此）；`api/handlers.py` 的 import 刪掉；`verb_pair_example` 整組移除（D8）。

### D3 建立參數

**基底的標準建立：手動填表**（試點用這個）

- 表單欄位就是 `create.note_type` 這個筆記類型的欄位，**向 Anki 查**（AnkiConnect `modelFieldNames`，`AnkiClient.get_model_field_names` 已有），依欄位順序排列，不在程式裡寫死。`GET /handlers` 回傳時即時查詢產生 `create_schema`。
- 每個欄位都是字串、選填；**第一個欄位必填**（Anki 不允許第一個欄位為空，也以它判斷重複）。
- 送出的 `params` 就是 `{欄位名: 值}`，交給 `CardService.create_note`；它會再嚴格比對欄位（多餘的欄位回 `FIELD_MISMATCH`）、補齊沒填的欄位、檢查重複（`DUPLICATE_CARD`）。
- **JSON 欄位與媒體欄位**：handler 宣告這兩類欄位的**名稱**（`JSON_FIELDS`、`MEDIA_FIELDS`，`_v2` 為 `Word_Family_JSON`、`Synonyms_JSON`、`Collocations_JSON` 與 `Word_Audio`、`Target_Example_Audio`）：
  - 建卡表單不列這些欄位，建出來留空（`_v2` 的模板在這些欄位為空時整段不顯示，`{{#Synonyms_JSON}}`，留空是安全的）。
  - `PATCH` 遇到它們回 400 `FIELD_NOT_EDITABLE`；網頁上唯讀顯示。
  - 前端不再用 `isStructuredField` 猜，改看宣告（`GET /handlers` 回傳 `json_fields`、`media_fields` 兩份名單）。
  - JSON 欄位的表單編輯見 `json_field_editor_FEAT_2026-10-20.md`（會把名單擴充成含資料格式的宣告）；媒體欄位等媒體上傳的設計（D9）。

**覆寫建立的 handler：`CreateParams` 模型**（既有的口說卡、外語糾錯）

- 這些 handler 的建立參數不是筆記欄位（例如口說卡的 `prompt` 會組成 `Prompt`、`Card_ID`、空的 JSON 陣列），所以各自宣告 Pydantic 的 `CreateParams`。
- **驗證**：`NoteService.create` 以 `CreateParams.model_validate()` 驗證，網頁與 TG 都經過；驗證通過後以 `model_dump()` 轉回 `dict` 交給 `execute_create`。
- **前端表單**：`create.flow` 為 `"form"` 時，`GET /handlers` 回傳 `CreateParams.model_json_schema()`。欄位名稱、說明用 `Field(title=…, description=…)`；選項用 `examples`；多行輸入以 `json_schema_extra={"x-ui": "textarea"}` 標示。本次網頁只開放試點，這部分先用在 TG 的驗證，網頁表單之後開放口說卡時再用。
- **內部參數**：`tg_bot` 不放在參數裡，由 `create()` 的關鍵字參數傳入：預設 `settings.TG_BOT_USERNAME`，TG 傳入 `bot.me()` 取得的值覆寫（`expression_fsm.py:900` 現在就這樣做），REST 不接受。
- **`expression_correction`**：TG 實際傳的是 `original_text`、`context`、`source_tag`、`llm_result`、`selected_indices`、`allow_duplicate`（`expression_fsm.py:906-914`），**沒有** `native_language`、`target_language`——那兩個只在沒給 `llm_result`、要由 handler 自己呼叫 LLM 時才用。所以除 `original_text` 外都設為選填，不能照搬現在 `input_schema` 的 `required`。
- 語言欄位：口說卡的 TG 流程提供 `en-US`、`ja-JP`、`zh-TW` 三個按鈕，也接受其他 BCP-47 代碼（`speaking_fsm.py:112-118`、`:298`）。`speaking_coach` 的 `CreateParams` 比照：`examples` 放這三個值，`pattern` 驗證格式，validator 沿用 TG 的正規化 `_normalize_language_code`（`ja-jp` → `ja-JP`）。

**兩者共通**

- REST：驗證錯誤 FastAPI **不會**自動轉成 422（端點內拋的 `ValidationError` 不是 `RequestValidationError`），要在 REST 層攔下、把每項錯誤的 `loc` 前面接上 `("body", "params")` 後改拋 `RequestValidationError`。回應是 FastAPI 標準的 422，`detail[].loc` 為 `["body", "params", 欄位名]`；前端的 `describeApiError` 已能處理（會去掉 `body`）。
- TG 不會遇到驗證錯誤：輸入由 FSM 逐步收集，驗證失敗代表程式錯誤，照常往上拋給各處的 `except` 與全域錯誤處理。
- 本次建卡只收文字（D9），沒有檔案欄位。

### D4 「筆記類型 → handler」對照

- 一個 handler 負責一種卡片；成組的卡片由同一個 handler 負責多個筆記類型（外語糾錯的母卡與子卡、jp 的母卡與 Context、Cloze）。
- 一個筆記類型只歸一個 handler。註冊表啟動時建立筆記類型到 handler 的對照，服務層依此找負責的 handler。
- 唯一的例外是 `JP_Context_Dark`，同時列在 `jp_verb_pair` 與 `jp_core_verb`（`JP_VerbPair_*` 三種也和範例用的 `verb_pair_example` 重疊，該 handler 在 D8 移除）。規則：同一類型有多個 handler 時，它們的 `read`、`update_fields`、`delete`、`actions` 必須相同，否則啟動失敗；相同時取第一個註冊的，結果不受影響。網址帶 handler 的操作，兩個 handler 都視為擁有它。
- 沒有 handler 的筆記類型（Notion_SRS、AI_QA 等）只能用通用查詢瀏覽，不能從網頁建、改、刪；要開放就先為它寫 handler。

通用讀取 `GET /notes/{note_id}` 回傳的 `permissions` 由此算出：

- **改欄位**、**刪除**：有 handler 且該能力 `supported` → 允許；否則不允許，`reason` 取 handler 宣告的 `reason`，沒有 handler 時為「沒有 handler 負責 {筆記類型}」。
- jp 類筆記的 `reason` 寫明由本機腳本管理，例如「由本機批次腳本管理，網頁只能檢視；刪除請用 `scripts/local_anki/JP_VerbPair/delete_child_cards.py`（會一併更新母卡 JSON 與查重紀錄）」。

前端因此可以刪掉 `FIELD_EDIT_HANDLERS`、`handlerForModel` 和「先查 handler 清單再決定能不能編輯」的邏輯。

### D5 路徑：增刪改查都有 `/handlers` 版本，另保留通用查詢

| 操作 | 端點 | 說明 |
|---|---|---|
| 列出 handler 與能力 | `GET /handlers` | |
| **建立** | `POST /handlers/{name}/notes` | 201 |
| **讀取** | `GET /handlers/{name}/notes/{note_id}` | handler 讀取：成組卡片之後會覆寫成附上整組（D10） |
| **修改欄位** | `PATCH /handlers/{name}/notes/{note_id}` | |
| **刪除** | `DELETE /handlers/{name}/notes/{note_id}` | |
| 動作（預留） | `POST /handlers/{name}/notes/{note_id}/actions/{action}` | 服務層的 `run_action` 本計畫就做（TG 要用），網頁端點在階段 B、D 開放 |
| 圖譜 | `GET /handlers/{name}/graph?deck=…` | |
| 通用：列出卡片 | `GET /cards`、`GET /cards/{card_id}`（既有，不動） | 任何筆記類型 |
| 通用：讀單一筆記 | `GET /notes/{note_id}` | 任何筆記類型；回傳 `handler` 與 `permissions`，前端靠它知道要呼叫哪個 `/handlers/{name}/...` |
| 牌組清單 | `GET /decks` | |

為什麼讀取也要有 handler 版本：成組的卡片光讀單一筆記看不出全貌（外語糾錯母卡要連子卡一起看、jp 母卡要看底下的子卡），怎麼把一組筆記拼起來只有 handler 知道。不做 `GET /handlers/{name}/notes`（列表）：目前沒有畫面需要，列表由 `GET /cards` 負責。

與現在的對照：

| 現在 | 改為 |
|---|---|
| `GET /handlers/` | `GET /handlers`（回傳加上 `title`、`capabilities`、`create_schema`；`input_schema` 移除） |
| `POST /handlers/{name}/create` | `POST /handlers/{name}/notes` |
| `GET /handlers/{name}/graph?deck_name=…` | `GET /handlers/{name}/graph?deck=…`（參數改名，與 `GET /cards` 一致；不支援時回 400） |
| `GET /handlers/cards/{note_id}` | `GET /notes/{note_id}`（通用）與 `GET /handlers/{name}/notes/{note_id}`（handler） |
| `PUT /handlers/{name}/cards/{note_id}`（`action: update_fields`） | `PATCH /handlers/{name}/notes/{note_id}` |
| `PUT /handlers/{name}/cards/{note_id}`（其他 action） | 預留 `POST /handlers/{name}/notes/{note_id}/actions/{action}` |
| `DELETE /handlers/{name}/cards/{note_id}` | `DELETE /handlers/{name}/notes/{note_id}` |
| `GET /handlers/decks` | `GET /decks`（回應不變） |
| `GET /handlers/{name}/cards` | 移除（沒有呼叫方，被 `GET /cards` 取代） |

- 新路由（`/notes`、`/decks`）與 `/handlers` 一樣掛 `verify_api_key`。
- 舊路徑在同一個 PR 移除，不保留別名：只有網頁前端在用，前後端同一次 push 部署。部署時前後端容器先後重建，中間可能有數秒新舊不一致，屬可接受範圍。
- `GET /handlers/` 不會變成 404：FastAPI 預設的 `redirect_slashes` 會 307 轉到 `/handlers`。

### D6 錯誤代碼

| 情況 | 狀態碼 | `error_code` |
|---|---|---|
| handler 不存在 | 404 | `HANDLER_NOT_FOUND`（既有） |
| 操作或動作不支援 | 400 | `HANDLER_OPERATION_UNSUPPORTED`（新增），`message` 為能力的 `reason` |
| 筆記不歸網址上的 handler | 400 | `NOTE_NOT_OWNED_BY_HANDLER`（新增） |
| 建立時 `note_type` 不是該 handler 的 `create.note_type` | 422 | `NOTE_TYPE_NOT_CREATABLE`（新增） |
| 建立參數驗證失敗 | 422 | FastAPI 標準格式（`detail[]`） |
| 直接修改宣告為 JSON、媒體的欄位 | 400 | `FIELD_NOT_EDITABLE`（新增，D3） |
| 寫入的欄位不存在於該筆記類型 | 400 | `FIELD_MISMATCH`（既有，`CardService` 的 `FieldMismatchError`；`update_note_fields` 與 `create_note` 都已嚴格比對，服務層不再自己檢查） |
| 建立時首欄位重複 | 409 | `DUPLICATE_CARD`（既有） |
| 筆記不存在 | 404 | `NOTE_NOT_FOUND`（新增；原本 `CardService.get_note` 拋 `AnkiServiceError`、回 502，第 1 步已改拋這個） |
| 查詢（含圖譜）的 `note_type` 不歸這個 handler、`label_field` 不是這些筆記類型的欄位、關聯圖譜帶了 `label_field` | 422 | `INVALID_NOTE_QUERY`（新增；第 1 步實作時補列，§4.6） |
| 修改時的 `fields` 不是 `{欄位名: 值}` | 422 | `INVALID_NOTE_FIELDS`（新增；第 1 步實作時補列。REST 的請求模型會先擋，這裡防 TG 等內部呼叫傳錯） |

都是服務層拋的 `FluencyTidesError` 子類；REST 由既有的全域 handler 轉成 `ErrorResponse`，TG 由各處既有的 `except FluencyTidesError` 與 `bot/handlers/errors.py` 通知使用者。`api/handlers.py` 現有的 `HTTPException` 全部移除。

### D7 本機批次腳本與網頁分流：流程分開、核心共用

jp 子卡（`JP_Context_Dark`、`JP_VerbPair_Cloze_Dark`、`JP_CoreVerb_Cloze_Dark`）由本機批次腳本產生。腳本是「混合式」：

| 步驟 | 在哪裡做 |
|---|---|
| 掃母卡、挑未滿額的動詞 | 腳本直接讀 AnkiConnect |
| 搜遊戲台詞、讀音／喘息句／形態素過濾、查重並組 `context_dialogue` | 腳本直接連 Elasticsearch 與語料庫（MySQL） |
| **呼叫 LLM、建 Context 與 Cloze 子卡、回寫母卡 JSON** | **後端** `POST /verb-pair/generate-child-cards`、`POST /core-verb/generate-child-cards` → handler 的 `execute_generate` |
| 上傳語音與頭像到 Anki 媒體 | 腳本從本機目錄（`JP_VERB_PAIR_VOICE_DIR` 等）讀檔，經 AnkiConnect 上傳 |

這兩個端點直接 `JPVerbPairHandler()`／`JPCoreVerbHandler()` 實例化 handler（不經註冊表），呼叫不在 `BaseHandler` 介面裡的 `execute_generate`；腳本送 Cloudflare Access 標頭、不送 `X-API-Key`（端點本來就沒掛 `verify_api_key`）。

**決定**：

- **流程分開**：批次流程留在本機腳本，不搬進服務層，也不為了網頁改寫。批次一次跑數百筆，若走服務層，每筆寫入後的強制同步會大幅拖慢。
- **核心共用**：`execute_generate`（提示詞、LLM、挖空定位、子卡欄位、母卡 JSON 結構）只有一份。將來網頁要建 jp 子卡時，**不另寫一套生成邏輯**，只另寫網頁自己的流程，最後呼叫同一個 `execute_generate`。
- **本計畫對這條路的保護**（G8）：
  - 兩個端點的路徑、請求模型（`GenerateChildCardsRequest`）、回應形狀（`{"status", "message", "data"}`；腳本讀 `data` 的 `kept_dialog` 上傳媒體、讀 `llm_model` 寫生成紀錄，`execute_generate` 還回 `context_note_id`、`cloze_note_id`）、驗證方式（不要求 `X-API-Key`）都不改。
  - LLM 用途鍵 `jp_verb_pair`、`jp_core_verb` 不改：腳本經 `GET /capability-routes`（`scripts/common/llm_label.py`）用它們標記生成紀錄。
  - `execute_generate` 的簽名與內部邏輯不改；jp 兩個 handler 只刪空實作（`execute_create`、`execute_update`、`execute_read_graph`、`execute_read_list`、`execute_delete`、`get_input_schema`），並加上能力宣告與 `title`。
  - `BaseHandler` 改完後，jp 兩個 handler 仍要能無參數實例化（端點直接 `new`）：抽象方法只剩 `handler_name`、`supported_models`、`title`，三者它們都有實作。
  - 註冊檢查寫錯會讓整個後端起不來，腳本端點也跟著不能用，所以 §8 有專門的腳本保護測試，§10 有實跑驗收。
- **能力宣告**：jp 兩個的建立 `supported: false`、`channels: ["script"]`、`reason`「子卡由本機批次腳本 generate_child_cards.py 產生」；改欄位、刪除 `supported: false`，`reason` 寫明由本機腳本管理與刪除工具路徑（D4）；`actions` 為空；讀取開放（標準讀取）。
- **之後網頁要做時**（不在本計畫）：給 jp handler 加一個 `generate_child_cards` 動作，轉呼叫 `execute_generate`；網頁走 `POST /handlers/{name}/notes/{母卡 id}/actions/generate_child_cards`。到時要先補：Anki 媒體上傳端點（D9）、對話輸入表單、`ValueError` 改 4xx（階段 B）。腳本端點維持原樣。
- 這些端點與 `config`、`jp` 路由沒有 `X-API-Key` 驗證。若之後要補，腳本要同時改成送 `X-API-Key`，否則會一起壞掉（不在本計畫）。

### D8 殘骸清除

不屬於網頁、TG、本機腳本任何一條路，而且會干擾本計畫的東西，在本計畫一併移除：

| 項目 | 現況 | 為什麼要清 |
|---|---|---|
| `verb_pair_example` 一整組：`app/services/task_handlers/example/`、`app/api/example/`、`app/schemas/llm/example/`、`app/templates/prompts/anki/example/`、`main.py` 的 example 路由與 import、`tests/test_llm_routing.py` 的 `_PRODUCTION_ROUTES` 中該鍵 | 範例用 handler，由 `POST /example/verb-pair/create` 呼叫；repo 內沒有任何呼叫方 | 它被註冊進 `/handlers`，而且和 `jp_verb_pair` 共用 `JP_VerbPair_*` 三種筆記類型但能力不同，會讓 D4 的啟動檢查失敗 |
| `POST /verb-pair/create` 與 `CreateVerbPairRequest` | 函式只有 `pass`，回 201 `null` | 假成功；腳本不用它（母卡由 `scripts/local_anki/*/create_master_card.py` 直接經 AnkiConnect 建立） |
| `PUT /verb-pair/{note_id}/update` 與 `UpdateVerbPairAudioRequest` | 呼叫 jp 的空 `execute_update`，回 200「已成功更新音檔資訊」卻什麼都沒改；沒有呼叫方 | 假成功；改繼承「不支援」後會變成 400，留著沒有意義 |
| `vocabulary_mining.supported_models` 的 `TOEIC_Coach_Dark`（舊版） | 實機 Anki 有這個筆記類型但 **0 張筆記**；800 張單字卡都在 `TOEIC_Coach_Dark_v2`（欄位不同的改版） | 單字卡只認 `_v2`，handler 的能力才能一體適用（D2） |
| `vocabulary_mining.supported_models` 的 `Default` | 實機 Anki 沒有這個筆記類型（`Default` 通常是牌組名稱） | 不存在的類型會出現在 `/handlers` |
| `speaking_coach.supported_models` 的 `Speaking_Coach_Light` | 2026-08-09 第 9 階段重構時寫入的預留名稱；repo 沒有定義檔，實機 Anki 也沒有這個筆記類型 | 同上 |
| `app/schemas/llm/verb_pair.py`（2026-10-20 實作時補列） | 只有一段 docstring「目前的生成邏輯已移至 example/verb_pair.py」，沒有任何類別 | 指向已移除的 example；唯一的引用者是下一列 |
| `app/scratch/verify_imports.py`（2026-10-20 實作時補列；原本誤記為「不受影響」） | import 了不存在的 `task_handlers.verb_pair_handler` 與 `schemas.llm.verb_pair.VerbPairGenerationResult`，執行必定失敗；沒有任何地方引用 `scratch/` | 已壞掉的一次性檢查腳本 |

清除後要確認的事：

- 舊版 `TOEIC_Coach_Dark` 只是從 handler 的清單拿掉；Anki 裡的筆記類型本身、repo 的定義檔（`app/anki_models/TOEIC_Coach_Dark*`）與提示詞模板不刪（§13）。
- `verb_pair_example` 的能力路由用途鍵消失後，資料庫 `capability_routes` 裡若有它的設定列，會變成「孤兒鍵」：工具設定頁照常列出、可刪除，不會出錯（`capability_route_service` 已處理）。
- `/verb-pair/generate-child-cards` 與它的 `GenerateChildCardsRequest` 留在 `api/verb_pair.py`，不動。

**實作結果（2026-10-20，§9 第 0 步）**：

- 確認沒有在用的證據：
  - repo 內（程式、腳本、前端、測試）除了這些檔案彼此之間，沒有任何引用。
  - 正式機的請求 log 涵蓋 2026-08-31 至 2026-10-20：`/example/verb-pair/create`、`POST /verb-pair/create`、`PUT /verb-pair/{id}/update` 都是 0 次；同期 `verb-pair`／`core-verb` 的 `generate-child-cards` 合計約 5,700 次（對照組，證明 log 有記錄這類請求）。
  - 正式機資料庫的 `capability_routes` 表沒有任何設定列，移除 `verb_pair_example` 用途鍵不會留下孤兒鍵。
  - 筆記類型的筆記數取自本機 Anki（與正式站經 AnkiWeb 同步同一份收藏）。
- 行為上唯一的差異：TG 建單字卡用的預設筆記類型（`supported_models[0]`）從 `TOEIC_Coach_Dark` 變成 `TOEIC_Coach_Dark_v2`。兩者都缺 `Meaning`、`Context` 欄位，建卡照樣以 `FIELD_MISMATCH` 失敗，直到第 2 步改用基底的標準建立。
- 驗證：`ruff` 通過；`pytest tests` 948 個、`scripts/fastapi_client/JP_CoreVerb/tests` 83 個通過；三支批次腳本 `--help` 正常；實際載入的 app 只剩兩個 `generate-child-cards` 端點，註冊的 handler 恰為 6 個。

### D9 建卡入口：網頁只做純手動填表

| 誰在建卡 | 卡片種類（本次） | 走哪條 | 內容從哪來 |
|---|---|---|---|
| 網頁 Generate 頁 | `create.channels` 含 `"web"` 的：單字卡 | `POST /handlers/{name}/notes` → `NoteService.create` → `execute_create` | **使用者手動填表**，純文字 |
| TG | 單字卡、外語糾錯（經服務層）；對話卡（`speaking_fsm.py` 自己組卡） | 直接呼叫 `NoteService.create`（不經 HTTP），與上一列同一份邏輯 | TG 對話流程收集；外語糾錯由 LLM 產生後讓使用者勾選 |
| 本機批次腳本 | jp 子卡（Context、Cloze） | `POST /verb-pair/generate-child-cards`、`POST /core-verb/generate-child-cards` → `execute_generate`（不經服務層，D7） | 自動化：ES 搜台詞＋LLM |

- **網頁不做自動化**：網頁開放的建卡一律是手動填表；要用 LLM 或批次產生內容，走本機腳本。
- `POST /handlers/{name}/notes` 是網頁與 TG 的通用建卡入口；`generate-child-cards` 是腳本專用入口，網頁不呼叫。
- 本次建卡只收文字，不附媒體。音檔欄位（例如 `_v2` 的 `Word_Audio`）建卡時留空，表單不列（D3）。

**之後的媒體上傳**：已另開設計書 `media_upload_FEAT_2026-10-20.md`，以下是摘要。

- **兩步驟**：先 `POST /media` 上傳單一檔案，後端存進 Anki 媒體資料夾並回傳**後端產生的檔名**；再於 `POST /handlers/{name}/notes` 的 `params` 填那個檔名。前端可把兩步包成一次送出。不採單一 multipart 請求：建卡請求維持 JSON，上傳端點也能給「既有筆記加音檔」（階段 D）與網頁建 jp 子卡共用，建卡失敗時不必重傳。
- **由 handler 決定要不要**：在參數欄位標 `x-ui: audio` 或 `image`，前端看到就畫成檔案選擇或錄音；沒宣告的 handler 表單裡就沒有上傳。服務層要檢查填入的檔名是 `/media` 發出的格式。
- `/media` 要處理：檔名由後端產生（前綴＋UUID；AnkiConnect `storeMediaFile` 遇同名會覆蓋）；瀏覽器錄音（webm）以既有的 `FfmpegClient.convert_to_mp3` 轉成 mp3；限制類型與大小（整個檔案以 base64 經 AnkiConnect 傳送）。
- 現有的 `/storage/upload` 存到 MinIO，不是 Anki 媒體，不能直接沿用。

### D10 繼承：`BaseHandler` 提供標準 CRUD，本次以單字卡試點、不覆寫

**`BaseHandler` 的標準實作**（單一筆記，沒有成組關係的卡片都適用）：

| 方法 | 標準行為 |
|---|---|
| `execute_create` | 手動填表建立：`params` 就是 `{欄位名: 值}`，寫進 `create.note_type`，建在指定牌組（D3） |
| `execute_read` | 讀單一筆記：欄位（依順序）、標籤、卡片 ID（與通用讀取同一個形狀，§4.3） |
| `execute_update`（`action="update_fields"`） | 只寫有變的欄位；宣告為 JSON、媒體的欄位拒絕（D3）；handler 宣告的關聯標籤欄位（`RELATION_LABEL_FIELD`）有變時，同步更新關聯表兩端的標籤（D12）；其他 action 拋不支援 |
| `execute_delete` | 先刪筆記、再刪關聯（`relation_service.delete_relations_for_note`）。反過來的話，筆記刪除失敗時關聯已經沒了、無法復原；先刪筆記的話，萬一關聯刪除失敗，留下的孤兒關聯會被 `/relations/sync`（`RelationService.sync_with_anki`）清掉 |
| `execute_read_graph` | 標準圖譜：用 handler 的查詢列出筆記，每張筆記一個節點，名稱預設為 nid、可指定任意欄位；沒有連線（D12） |

- 標準實作有寫好，不代表開放：開不開放由 `CAPABILITIES` 決定（D2、G3）。
- 各 handler 現有的 `execute_delete` 全部刪掉，改繼承基底（`vocabulary_mining`、`speaking_coach`、`speaking_trilingual` 的實作本來就和標準實作相同）。
- `execute_create` 的回傳型別改為 `list[int]`。`speaking_coach`、`speaking_trilingual` 現在回傳 `int`，改成回傳單元素陣列。
- 移除 `execute_read_list`（`GET /handlers/{name}/cards` 移除後沒有呼叫方）、`get_input_schema`（D3 取代）。
- `handler_name`、`supported_models` 保留；新增 `title`（中文名稱）：單字卡、對話卡、三語對話卡、外語糾錯、自他動詞、核心動詞。

**試點：單字卡（`vocabulary_mining`，只負責 `TOEIC_Coach_Dark_v2`）**

- 增刪改查**全部用基底的標準實作，不覆寫**，先跑通整套流程：
  - 建立：刪掉現在壞掉的 `execute_create`（寫 `Meaning`、`Context`），改用基底的手動填表建立。
  - 讀取、刪除：用基底。
  - 修改：刪掉現在的 `execute_update` 覆寫。它多做的「改 `Expression` 時同步更新關聯表的標籤」改由基底依宣告 `RELATION_LABEL_FIELD = "Expression"` 通用處理（D12），行為保留。
- 圖譜不屬於 CRUD：單字卡的圖譜要保留現在的關聯連線，改用 `RelationGraphMixin`（D12），刪掉自己寫的 `execute_read_graph`。CRUD 因此**完全沒有覆寫**。
- TG 的「建單字卡」也因此能用了：它改呼叫 `NoteService.create`，傳 `{"Expression": 單字}`，建在 `Default` 牌組的 `TOEIC_Coach_Dark_v2`，只有單字、其餘欄位空白——和 TG 現在的回覆文字「單字卡已建立（釋義尚未自動產生）」一致（§7）。

**之後的繼承結構**（不在本計畫實作，先記下方向）：

```text
BaseHandler                       標準 CRUD（單一筆記）
├── VocabularyMiningHandler       單字卡（本次試點，不覆寫 CRUD）
├── SpeakingCoachHandler          對話卡（覆寫建立與 JSON 動作，既有）
├── SpeakingTrilingualHandler     三語對話卡（同上）
└── GroupedHandler                成組卡片：從任一成員找出整組；讀取時附上整組；刪除時整組處理
    ├── ExpressionCorrectionHandler    母卡＋子卡（子卡用 Master_Note_ID 指回母卡）
    └── JpMasterChildHandler            母卡＋Context＋Cloze（子卡記在母卡 JSON；刪除要同步查重紀錄）
        ├── JPVerbPairHandler
        └── JPCoreVerbHandler
```

- 成組卡片要開放讀取以外的操作時，才加中間層父類別並覆寫；在沒有實際行為可驗證之前不先建空殼。
- 兩個 jp handler 現在各寫一份、相似度很高，`JpMasterChildHandler` 可以收掉重複的部分。
- TG「外語糾錯覆蓋舊卡」的刪除不受影響：那是外語糾錯流程內部的操作，照舊直接刪除（§7），不經 `NoteService.delete`。

### D11 三個入口如何接線、共用哪些邏輯

```text
入口        網頁                          TG                                  本機腳本
            REST /handlers/...、/notes    vocabulary_fsm、expression_fsm、    fastapi_client/*：HTTP
                                          voice、callbacks、messages          local_anki/*：直接連
              │                             │                                   │
服務層      NoteService：能力檢查、參數驗證、筆記歸屬檢查、寫入後同步            （不經過）
              │                             │                                   │
Handler     BaseHandler 標準 CRUD ← 各 handler 覆寫；圖譜；具名動作              execute_generate（jp）
              │                             │                                   │
資料層      CardService（欄位嚴格比對、重複檢查）、RelationService、AnkiClient、AnkiJsonFieldManager
```

各條路徑：

| 入口 | 操作 | 路徑 |
|---|---|---|
| 網頁 | 建單字卡 | `POST /handlers/vocabulary_mining/notes` → `NoteService.create`（檢查能力、驗證必填）→ `BaseHandler.execute_create` → `CardService.create_note` → 寫入後同步 |
| 網頁 | 讀、改、刪 | `/handlers/{name}/notes/{id}` → `NoteService.read`／`update_fields`／`delete`（檢查歸屬與能力）→ 基底或覆寫的方法 → `CardService` → 寫入後同步（讀取不同步） |
| 網頁 | 瀏覽 | `GET /cards`、`GET /notes/{id}` → 直接讀 Anki，不經 handler |
| 網頁 | 圖譜 | `GET /handlers/{name}/graph` → `NoteService.graph`（檢查能力）→ `execute_read_graph`（基底：handler 的查詢＋單點；單字卡：再加關聯連線，D12）；點節點 → `GET /handlers/{name}/notes/{note_id}` |
| TG | 建單字卡 | `vocabulary_fsm` → `NoteService.create` → 同網頁那一條 |
| TG | 建外語糾錯 | `expression_fsm` → `NoteService.create` → `ExpressionCorrectionHandler.execute_create`（覆寫） |
| TG | 錄音、上傳音檔 | `voice`／`callbacks`／`messages` → `NoteService.run_action` → 口說卡 handler 的 `execute_update`（覆寫） |
| TG | 建對話卡 | `speaking_fsm` 自己組卡 → `AnkiClient.add_note`（不經服務層與 handler，§2 非目標） |
| 腳本 | jp 子卡 | HTTP `generate-child-cards` → `execute_generate` → `CardService`（不經服務層） |
| 腳本 | 其他（匯入、建母卡、刪子卡等） | 直接用 `AnkiClient`／`CardService`，或直接呼叫 handler（`generate_expression_cards.py`） |

共用到哪一層：

| 邏輯 | 網頁 | TG | 腳本 |
|---|---|---|---|
| 能力檢查、參數驗證、寫入後同步（`NoteService`） | ✅ | ✅ | ❌ |
| handler 的建卡、更新、生成邏輯 | ✅ | ✅ | 部分（`execute_generate`、外語糾錯的 `execute_create`） |
| 欄位嚴格比對、重複檢查（`CardService`） | ✅ | ✅ | ✅（經 `CardService` 的腳本） |
| AnkiConnect 存取（`AnkiClient`） | ✅ | ✅ | ✅ |

### D12 圖譜：以 handler 的查詢為基礎，基底只畫單點，可依筆記類型與任意欄位顯示

圖譜和建、讀、改、刪一樣是 handler 的能力：`graph.supported` 開放的 handler 才會出現在 Knowledge Graph 頁的選單裡。

**基底（`BaseHandler.execute_read_graph`）：簡單的單點**

1. 用 **handler 的查詢** `query_notes(deck, note_type)` 列出筆記。基底的查詢是「這個 handler 負責的筆記類型，在指定牌組裡的筆記」；可以再指定只看其中一種筆記類型。成組卡片之後可覆寫成只取母卡。之後若要開放「依 handler 列出筆記」的端點，用的也是這個方法。
2. 每張筆記一個節點，沒有連線：

   ```json
   { "id": "1712345678901", "note_id": 1712345678901, "note_type": "TOEIC_Coach_Dark_v2", "label": "1712345678901" }
   ```

3. **名稱預設顯示 nid**：每張筆記一定有 note ID，所以任何筆記類型都能直接畫，不需要知道它有哪些欄位。
4. **可指定任意欄位當名稱**：請求帶 `label_field`（例如 `Expression`），節點名稱就改用那個欄位的值（去掉 HTML）；欄位不存在於這些筆記的筆記類型回 422，某張筆記該欄位為空時退回顯示 nid。回應附上可選的欄位清單（`label_fields`），前端做成下拉。
5. handler 可宣告預設的名稱欄位與副標欄位（`GRAPH = GraphSpec(default_label="Expression", subtitle="Target_Translation")`），沒宣告就是 nid、沒有副標。欄位名在註冊時以 `app/anki_models/*.json` 的 `inOrderFields` 檢查，寫錯就啟動失敗。
6. **點節點**：前端用節點的 `note_id` 呼叫 handler 讀取 `GET /handlers/{name}/notes/{note_id}`，打開單張卡片的詳情。

**可依筆記類型分開看**：一個 handler 負責多種筆記類型時（例如外語糾錯的母卡與子卡），前端先選 handler、再選筆記類型（請求帶 `note_type`），不選就是全部。

**關聯圖譜（`RelationGraphMixin`）：給用知識關聯表的卡片**

現在單字卡的圖譜比單點多了三樣東西，都來自關聯表（SQLite），不是筆記本身：

- 依卡片的學習狀態（新卡、學習中、複習、暫停）上色；
- 關聯連線（同義詞、搭配詞等）；
- 「幽靈節點」：連線另一端只記了文字、沒有對應筆記的詞。

這部分抽成 `RelationGraphMixin`，覆寫 `execute_read_graph`：先用基底的查詢與單點，再加上狀態、連線、幽靈節點。單字卡用它，**現有圖譜的功能完全保留**；其他卡片要用關聯圖譜，掛上這個 mixin 即可，不必改架構。用了它的 handler，`graph.links` 為 true，前端才顯示「建立連線、刪除連線」等操作。

關聯表用「文字」辨認節點（每條連線記了兩端的 `source_label`、`target_label`），所以用這個 mixin 的 handler 要宣告哪個欄位是那段文字：`RELATION_LABEL_FIELD = "Expression"`；節點的名稱固定用這個欄位，不開放 `label_field` 切換（切換了連線會對不上）。

**順帶修正的既有問題**：

- 單字卡現在讀的翻譯、詞性欄位是 `Meaning`、`PartOfSpeech`，`_v2` 根本沒有這兩個欄位（對應的是 `Target_Translation`、`Target_POS`），所以圖譜上的翻譯永遠是空的。改由 `GraphSpec.subtitle` 宣告 `Target_Translation`，註冊時就會檢查欄位存在。
- **改名稱時同步連線的文字**：把單字 `anyway` 改成 `anyways` 時，關聯表裡若還是 `anyway`，圖譜會分裂成兩個節點。現在的覆寫只更新了「這張卡當起點」的連線（`update_source_label`），當終點的沒更新。改為基底的標準修改在 `RELATION_LABEL_FIELD` 有變時，同步更新兩端（`RelationService` 新增 `update_labels_for_note`，取代 `update_source_label`）——宣告式，不必覆寫修改。

**本次的範圍**：

- 這次的圖譜工作幾乎是**移植現有實作**：
  - 基底的單點圖譜（列出筆記、每張一個點、名稱預設 nid、可指定欄位、點開單張卡片）寫好並以單元測試驗證。
  - `RelationGraphMixin` 是把現在 `vocabulary_mining.execute_read_graph` 的邏輯原樣搬過去，只修正翻譯欄位名。
- 依 G3，**除了單字卡，這次不開放任何 handler 的圖譜**；基底的單點圖譜等之後輪到某種卡片、驗收後再開。

**不做的**：

- `GET /relations/graph`（不經 handler、把所有筆記混在一起畫的全域圖譜）與 `RelationService.get_graph_data`：沒有呼叫方，和上面重複，列在 §13 的殘骸清單，建議之後移除。
- 建卡時自動把「只記了文字的連線」接到新卡上（關聯表的模型註解有提到這個構想）：之後再說。

### D13 前端：三頁版面統一與共用元件抽取（原 web_ui_parity 階段 A2）

2026-10-20 自 `web_ui_parity_FEAT_2026-10-18.md` 階段 A2 併入：Generate、Graph 要依本計畫的 API 改寫，版面也一起統一，避免同一批頁面改兩次。

**目標**：Generate、Graph 改成與 Cards 相同的模式——左側是 Cards 的牌組樹，右側一個主區塊。**牌組只由左側的樹決定**：選了哪一層，Generate 就在那一層新增卡片、Graph 就顯示那一層（含子牌組）的圖；右側不再有牌組下拉。

| 頁面 | 左側 | 右側 |
|---|---|---|
| Cards | 牌組樹 | 卡片清單 + 卡面（不變） |
| Generate | 牌組樹 | 建卡表單：handler 下拉（依 `create.channels`）、依 `create_schema` 產生的欄位、生成按鈕；生成中與結果（§6） |
| Graph | 牌組樹 | 工具列（handler 與筆記類型、名稱欄位、建立連線、同步、標籤樣式與字級、全螢幕）+ 力導向圖（§6、D12） |

**共用元件**（從 Cards 抽出，Cards 改用後畫面與行為不變）：

| 元件 | 來源 | 調整 |
|---|---|---|
| `SplitLayout` | 現在寫在 `pages/Cards.tsx`，斷點在 `lib/cardLayout.ts` | 依內容區寬度並排或疊成一欄、可拖曳分隔線、並排時填滿視窗高度；支援 2 欄（Generate、Graph）與 3 欄（Cards） |
| `usePanelWidths` | `hooks/usePanelWidths.ts` | 改為以頁面 ID 參數化，各頁各自記住寬度 |
| `DeckTreePanel` | `components/cards/DeckPanel.tsx` | 移到共用位置；牌組清單改 `GET /decks`；展開狀態各頁分開記 |
| `Panel` | `components/cards/panelStyles.ts` | 固定標題列、自己捲動的外框，做成元件 |
| `ErrorNote` | `components/cards/ErrorNote.tsx` | 移到共用位置 |
| `PageHeader` | 各頁頂部的標題區 | 新抽出 |

**選取牌組的語意**：

- 牌組樹的「全部牌組」：Graph 照現狀查全部（不帶 `deck`）；Generate 不允許，未選具體牌組時生成按鈕停用並提示先選牌組。
- Graph 查詢經 handler 的查詢（D12），Anki 的 `deck:"…"` 會包含子牌組。
- 各頁選取的牌組分開記（目前 Graph 存在 localStorage `kg_selectedDeck`，Cards 存在網址）。

**Graph 全螢幕**（2026-10-20 決定：圖佔滿整個螢幕，左側選單、網站頂列與瀏覽器的分頁列／網址列都隱藏；按 Esc 或再按一次按鈕退出）：

- 以 Fullscreen API（`element.requestFullscreen()`）放大圖的區塊，這是瀏覽器原生的全螢幕，會一併隱藏瀏覽器介面；`ForceGraph2D` 要改成傳入量到的容器寬高（目前沒傳、預設用整個視窗大小），進出全螢幕時重新量。
- 全螢幕時只有被放大的元素看得到：`CardDetailModal` 與「選擇關聯類型」視窗目前掛在頁面最外層，要移進被放大的容器內；`Toaster` 掛在 `App.tsx`，全螢幕時看不到。

## 4. API 規格

### 4.1 `GET /api/v1/handlers`

```json
[
  {
    "name": "vocabulary_mining",
    "title": "單字卡",
    "note_types": ["TOEIC_Coach_Dark_v2"],
    "capabilities": {
      "create": { "supported": true, "channels": ["web", "tg"], "flow": "form", "deck": "selected", "note_type": "TOEIC_Coach_Dark_v2", "reason": null },
      "read": { "supported": true },
      "update_fields": { "supported": true },
      "delete": { "supported": true },
      "actions": [],
      "graph": { "supported": true, "links": true }
    },
    "create_schema": {
      "type": "object",
      "properties": {
        "Expression": { "type": "string", "title": "Expression" },
        "Target_Translation": { "type": "string", "title": "Target_Translation" },
        "Target_Example_Sentence": { "type": "string", "title": "Target_Example_Sentence" }
      },
      "required": ["Expression"]
    }
  },
  {
    "name": "jp_verb_pair",
    "title": "自他動詞",
    "note_types": ["JP_VerbPair_Master_Dark", "JP_Context_Dark", "JP_VerbPair_Cloze_Dark"],
    "capabilities": {
      "create": { "supported": false, "channels": ["script"], "reason": "子卡由本機批次腳本 generate_child_cards.py 產生" },
      "read": { "supported": true },
      "update_fields": { "supported": false, "reason": "由本機批次腳本管理，網頁只能檢視" },
      "delete": { "supported": false, "reason": "請用 scripts/local_anki/JP_VerbPair/delete_child_cards.py 刪除，會一併更新母卡 JSON 與查重紀錄" },
      "actions": [],
      "graph": { "supported": false, "reason": "尚未實作" }
    },
    "create_schema": null
  }
]
```

- `note_types` 是 `supported_models`（這個 handler 負責的所有筆記類型）。
- 另回傳 `json_fields`、`media_fields`（欄位名稱清單，D3），範例省略。
- `create_schema`：`create.supported` 且 `flow` 為 `"form"` 時才有。標準建立是向 Anki 查欄位即時產生（範例節錄 3 個欄位；JSON、媒體欄位不列，D3）；覆寫建立的 handler 是 `CreateParams.model_json_schema()`。
- 前端依 `create.channels` 決定 Generate 頁的顯示（§6）；Cards 頁依 `GET /notes/{id}` 的 `permissions` 決定能否改、能否刪；工具設定頁直接把這份資料列成表（§6）。

### 4.2 `POST /api/v1/handlers/{name}/notes`

請求（單字卡，標準的手動填表建立）：

```json
{ "deck": "English::TOEIC", "note_type": "TOEIC_Coach_Dark_v2", "params": { "Expression": "allocate", "Target_Translation": "分配" } }
```

建立參數放在 `params`：標準建立時就是筆記欄位；覆寫建立的 handler 是它的 `CreateParams`（例如口說卡的 `prompt`）。

回應（201）：

```json
{ "note_ids": [1712345678901], "deck": "English::TOEIC", "sync_warning": null }
```

- `note_ids` 一律是陣列（`expression_correction` 一次會建母卡與多張子卡）。
- `sync_warning` 在寫入後同步失敗時為提醒文字，卡片仍已建立。
- `deck` 不存在時 `CardService.create_note` 會自動建立（`ensure_deck_exists`），不會回錯誤。
- `note_type` 不是該 handler 的 `create.note_type` → 422 `NOTE_TYPE_NOT_CREATABLE`；建立不開放 → 400 `HANDLER_OPERATION_UNSUPPORTED`；第一個欄位空白 → 422；首欄位重複 → 409 `DUPLICATE_CARD`。

### 4.3 `GET /api/v1/notes/{note_id}`（通用）與 `GET /api/v1/handlers/{name}/notes/{note_id}`（handler）

```json
{
  "note_id": 1712345678901,
  "note_type": "TOEIC_Coach_Dark_v2",
  "tags": ["Auto_Migrated", "TOEIC_LLM"],
  "cards": [1712345678902],
  "fields": [
    { "name": "Expression", "value": "anyway" },
    { "name": "Word_Audio", "value": "[sound:01-01.mp3]" }
  ],
  "handler": "vocabulary_mining",
  "permissions": {
    "update_fields": { "allowed": true, "reason": null },
    "delete": { "allowed": true, "reason": null }
  }
}
```

- 兩者回傳同一個形狀。handler 讀取在成組卡片覆寫後，會多一個相關資料區塊（例如母卡底下的子卡）；本次都是基底的標準讀取，內容相同。
- 現在 `/handlers/cards/{note_id}` 回的是 AnkiConnect 的 camelCase（`noteId`、`modelName`、`fields: {名稱: {value, order}}`），改為上面的 snake_case 形狀。
- `fields` 依筆記類型的欄位順序（`order`）排成陣列，前端不必再自己排序。
- `permissions` 依 D4 計算；`allowed` 為 false 時 `reason` 必有值。沒有 handler 的筆記（只能用通用讀取）：`handler` 為 null，兩項都不允許，`reason` 為「沒有 handler 負責 {筆記類型}」。

### 4.4 `PATCH /api/v1/handlers/{name}/notes/{note_id}`

```json
{ "fields": { "Target_Translation": "無論如何；儘管如此" } }
```

只送有變的欄位。回應為修改後的完整筆記（與 4.3 相同形狀）加上 `sync_warning`，前端可直接更新畫面。不開放 → 400。

### 4.5 `DELETE /api/v1/handlers/{name}/notes/{note_id}`

回 200 `{ "sync_warning": null }`。由 handler 的 `execute_delete` 刪除（D10）；不開放時回 400 `HANDLER_OPERATION_UNSUPPORTED`，`message` 為 `reason`，Anki 與關聯都不會被動到。

### 4.6 `GET /api/v1/handlers/{name}/graph?deck=…&note_type=…&label_field=…`

```json
{
  "nodes": [ { "id": "1712345678901", "note_id": 1712345678901, "note_type": "TOEIC_Coach_Dark_v2", "label": "1712345678901" } ],
  "links": [],
  "label_fields": ["Expression", "Target_Translation", "Target_POS"],
  "label_field": null
}
```

- 三個參數都可省略。`deck` 省略時查全部牌組——後端現在就是這樣；`All Decks` 只是前端下拉的特殊值，由 `client.ts` 轉成不帶參數，改後照舊。Anki 的 `deck:"…"` 會包含子牌組。
- `note_type` 必須是這個 handler 負責的筆記類型之一，否則 422；`label_field` 必須是這些筆記的欄位之一，否則 422（都是 `INVALID_NOTE_QUERY`）。`label_fields` 向 Anki 查這些筆記類型的欄位（依順序、不重複），所以沒有筆記時也有選項。
- 請求不帶 `label_field` 表示用 handler 宣告的預設（`GRAPH.default_label`），沒宣告就顯示 nid。回應的 `label_field` 是**實際採用**的欄位，null 表示顯示 nid（前端下拉以它為目前值）。
- 用 `RelationGraphMixin` 的 handler（本次只有單字卡）：節點另有 `status`、`translation`（副標，沿用現在的鍵名）、`group`、`val`，`links` 有內容，另有幽靈節點（沒有 `note_id`）；節點名稱固定為 `RELATION_LABEL_FIELD`，`label_field` 不適用（帶了回 422）。這部分的形狀與現在相同，Knowledge Graph 頁的繪圖不必改。
- 不支援時回 400。

### 4.7 `GET /api/v1/decks`

回應與現在的 `/handlers/decks` 相同。

## 5. 各 handler 的能力（初始值）

依 G3，網頁只開放試點；TG 已經在用的能力保持開放，否則 TG 會壞：

| handler | `title` | 負責的筆記類型 | 建立 | `channels` | 讀取 | 改欄位 | 刪除 | 動作 | 圖譜 |
|---|---|---|---|---|---|---|---|---|---|
| `vocabulary_mining` | 單字卡 | `TOEIC_Coach_Dark_v2` | ✅ 標準手動填表 | `web`、`tg` | ✅ | ✅ | ✅ | — | ✅ |
| `speaking_coach` | 對話卡 | `Speaking_Coach_Dark` | ❌（「網頁建卡尚未開放；可在 TG 建立」） | `tg` | ✅ | ❌（「對話卡的欄位修改尚未開放」） | ❌（「對話卡的刪除尚未開放」） | `add_audio`、`add_recording`、`delete_recording` | ❌ 口說卡沒有知識圖譜 |
| `speaking_trilingual` | 三語對話卡 | `Speaking_Trilingual_Dark` | ❌（「網頁建卡尚未開放」） | 空 | ✅ | ❌（同上） | ❌（同上） | 同上 | ❌ 同上 |
| `expression_correction` | 外語糾錯 | `Expression_Master_Dark`、`Expression_Micro_Dark` | ✅ 覆寫（只建母卡＋子卡） | `tg`（「需要先預覽 LLM 結果再勾選子卡，目前只能在 TG 建立」） | ✅ | ❌（「目前只支援新增音檔」） | ❌（「外語糾錯的母子卡刪除尚未開放」） | `add_audio` | ❌ 尚未實作 |
| `jp_verb_pair` | 自他動詞 | `JP_VerbPair_Master_Dark`、`JP_Context_Dark`、`JP_VerbPair_Cloze_Dark` | ❌ | `script` | ✅ | ❌（由本機批次腳本管理） | ❌（請用本機刪除工具） | — | ❌ 尚未實作 |
| `jp_core_verb` | 核心動詞 | `JP_CoreVerb_Master_Dark`、`JP_Context_Dark`、`JP_CoreVerb_Cloze_Dark` | ❌ | `script` | ✅ | ❌（同上） | ❌（同上） | — | ❌ 尚未實作 |

- 口說卡的建卡「有實作但不開放」：它們覆寫了 `execute_create`，但網頁表單這次不開放（G3）；TG 的對話卡本來就不經 handler。三語卡在 TG 沒有建卡入口，所以 `channels` 為空、Generate 頁不顯示。
- 外語糾錯的建卡開放，是因為 TG 經服務層在用；網頁看到的是反灰。
- 口說卡的改欄位不開放：TG 不使用 `update_fields`（TG 只用 `add_recording`、`add_audio` 兩個動作），所以不影響 TG；網頁暫時不能改口說卡的欄位（R4），等之後輪到口說卡時連同建卡一起驗收再開。
- 動作都是 TG 在用的，保持開放。`expression_correction` 的 `add_audio` 目前沒有任何呼叫方（外語糾錯卡模板的按鈕是 `ttsaudio`，TG 回「尚未開放」），保留宣告給之後的音檔功能。

## 6. 前端改動

前端在後端與 TG 完成後最後實作（§9）。

- **`api/client.ts`、`types/api.ts`**：路徑與型別照 §4 改。`HandlerInfo` 改為 `name`、`title`、`note_types`、`capabilities`、`create_schema`；`CardDetail` 改為 §4.3 的 `NoteDetail`；`HandlerCreatePayload`／`HandlerCreateResponse`／`HandlerUpdatePayload`／`HandlerActionResponse` 依 §4 改寫或移除；刪掉沒人用的 `listCards`。
- **Generate 頁**（版面見 D13）：
  - handler 下拉依 `capabilities.create.channels` 顯示 `title`：

    | 條件 | 顯示 |
    |---|---|
    | 含 `"web"` | 可選 |
    | 非空但不含 `"web"` | 反灰、不可選，旁邊顯示 `create.reason` |
    | 空陣列 | 不顯示 |

    本次結果：單字卡可選；對話卡、外語糾錯、自他動詞、核心動詞反灰；三語對話卡不顯示。前端不自行判斷，只看這個欄位。
  - 欄位由 `create_schema` 產生：`title`、`description`、`required`、`default`、`examples`（可輸入的下拉）、`x-ui: textarea`；不認得的 `x-ui` 值當一般文字欄位。取代現在寫死的單一 `word` 輸入框。
  - 左側沒選具體牌組時，生成按鈕停用並提示。
  - 送出後顯示「建立中、同步中」；422 時把 `detail[].loc` 的最後一段對到欄位，錯誤標在欄位旁。成功時顯示建立的筆記數，有 `sync_warning` 時一併顯示，並連到 Cards 頁的該牌組。
- **`CardDetailModal`**：讀取改 `GET /notes/{id}`，依回傳的 `permissions` 啟用儲存、刪除按鈕，不允許時顯示 `reason`；儲存改 `PATCH /handlers/{handler}/notes/{id}`、刪除改 `DELETE /handlers/{handler}/notes/{id}`（`handler` 取自讀取結果），用回應直接更新畫面；寫入時顯示「同步中」。**現在的前端對有 handler 的筆記一律開放刪除**（`fieldEditPolicy` 的 `canDelete: true`），包括 jp 類，改後只有單字卡能刪。刪除 `lib/cardFields.ts` 的 `FIELD_EDIT_HANDLERS`、`handlerForModel`、`fieldEditPolicy`（改為直接讀 `permissions`）、`orderedFields`（後端已排序）。JSON 欄位與媒體欄位依 `GET /handlers` 的 `json_fields`、`media_fields` 唯讀顯示，不再用 `isStructuredField` 猜（D3）。
- **讀單張筆記的三處**都改 `GET /notes/{id}` 與 `NoteDetail`：`CardDetailModal`、`components/cards/CardViewPanel.tsx`（欄位面板）、`pages/KnowledgeGraph.tsx`（點節點看詳情）。
- **Knowledge Graph**（D12）：
  - handler 下拉只列 `capabilities.graph.supported` 為 true 的（本次只有單字卡；現在是寫死 `vocabulary_mining`）。選了 handler 後，若它負責多種筆記類型，再顯示筆記類型下拉；若圖譜不是關聯圖譜，顯示「名稱欄位」下拉（選項來自回應的 `label_fields`，預設 nid）。
  - 點節點：用 `note_id` 呼叫 `GET /handlers/{name}/notes/{note_id}`，打開卡片詳情；幽靈節點沒有 `note_id`，不開。
  - 「建立連線」等關聯操作只在 `capabilities.graph.links` 為 true 時顯示。
  - 單字卡的節點副標現在會有內容；查詢參數改用 `deck`；牌組改由左側的牌組樹決定，頁面內的牌組下拉移除（D13）。
- **Cards**：改用 D13 抽出的共用元件，畫面與行為不變；牌組樹改 `GET /decks`。
- **三頁版面**：見 D13。
- **工具設定頁（`pages/ToolSettings.tsx`，路徑 `/tools`）新增「Handler 能力」區塊**，唯讀，資料來自 `GET /handlers`：
  - 每個 handler 一列：`title`（`name`）、負責的筆記類型。
  - 建立、讀取、改欄位、刪除、圖譜各一欄，標示開放與否，不開放的顯示 `reason`。
  - 建立另列 `channels`（網頁／TG／本機腳本）、`flow`、牌組規則；另列 `actions`。
  - 用途是一眼看到目前開放到哪裡，以及之後逐一開放（G3）的進度。沒有 handler 的筆記類型不在這裡（需要另一個「列出 Anki 筆記類型」的端點，不在本計畫）。

## 7. TG 的改動

TG 呼叫 handler 寫入的地方都改成呼叫 `NoteService`，對話流程與回覆文字不變：

| 位置 | 現在 | 改為 |
|---|---|---|
| `fsm/vocabulary_fsm.py:133-152` | 取 `vocabulary_mining` handler；自己同步、以 `supported_models[0]`（`TOEIC_Coach_Dark`）`execute_create` 傳 `{"word": 單字}`、再 `_sync_with_warning`。因欄位不符，現在必定失敗 | `NoteService.create("vocabulary_mining", deck="Default", note_type=create.note_type, params={"Expression": 單字})`，用回傳的 `sync_warning` 顯示提醒；刪掉寫入前的同步。**改後能建起來**：`TOEIC_Coach_Dark_v2`、只有單字，與現在的回覆「單字卡已建立（釋義尚未自動產生）」一致 |
| `fsm/expression_fsm.py:899-915` | 取 `expression_correction` handler，`execute_create`，傳 `relation_service=None` | `NoteService.create(..., tg_bot=bot_user.username)`；`relation_service` 由服務層提供，移除 `# type: ignore` |
| `voice.py:304-316` | 依欄位名稱決定 handler，`execute_update(action="add_recording")` | `NoteService.run_action(note_id, "add_recording", …)` |
| `callbacks.py:126-167` | 依欄位名稱猜是否三語卡再取 handler，`execute_update(action="add_audio")`，再 `_sync_with_warning` | `NoteService.run_action(...)`，用回傳的 `sync_warning` |
| `messages.py:86-130` | 寫入前同步；寫死取 `speaking_coach`，`execute_update(action="add_audio")`，再 `_sync_with_warning` | `NoteService.run_action(...)`；由筆記類型找 handler，順便修好三語卡的自訂頭像寫錯 handler 的問題（web_ui_parity 階段 D 提到的 `messages.py:102`）；刪掉寫入前的同步 |

- **外語糾錯覆蓋舊卡不變**：`expression_fsm.py:948-965` 的 `_delete_old_cards` 在新卡建好後直接 `delete_notes` 刪舊母卡與子卡，`:931-935` 最後以 `_sync_with_warning` 同步。外語糾錯的刪除不開放（§5），而且這是流程內部的操作，照舊不經 `NoteService.delete`。因為 `NoteService.create` 已在建卡後同步過一次，覆蓋時最後這次同步要保留（把刪除推上 AnkiWeb），提醒取這一次的。
- `expression_fsm.py` 用到服務層的函式（`_execute_card_creation` 與呼叫它的 `handle_confirm_all`、`handle_card_selection_text`）要加 `note_service` 參數，由 middleware 注入。
- `voice.py` 的 `handler_name` 仍用來選錄音評分器（`recording_eval.evaluator_for_handler`），這部分不動；只有寫入改走服務層。
- 同步：上面這些地方原本自己呼叫的 `anki_client.sync()` 與 `_sync_with_warning`，改由服務層負責（D1），避免同步兩次。實作時逐一確認每處的同步時機與服務層相同。
- `expression_fsm.py:619` 的 `execute_generate`（預覽，只呼叫 LLM、不寫 Anki）照舊直接呼叫 handler。
- `speaking_fsm.py` 自己組對話卡、不經 handler，本計畫不動（§2 非目標）。
- `commands.py` 的 `_handle_delete_entry` 屬階段 B，本計畫不動。
- `jp_verb_pair`、`jp_core_verb` 刪掉空的 `execute_create`、`execute_update`、`execute_read_graph` 等；TG 沒有呼叫它們。本機腳本走的 `execute_generate` 與兩個 `generate-child-cards` 端點不動（D7）；唯一呼叫空 `execute_update` 的 `PUT /verb-pair/{note_id}/update` 在 D8 移除。

## 8. 測試

- 註冊表：D2 表格的每一條規則，違反時啟動失敗；同一筆記類型多個 handler 且能力不同時啟動失敗；`task_handlers` 套件 import 後恰好 6 個 handler 已註冊（沒有 `verb_pair_example`）；§5 的初始值以快照比對。
- **`BaseHandler` 標準 CRUD**（以假的 Anki 客戶端測）：
  - 建立：欄位寫進 `create.note_type`、建在指定牌組；回傳 `list[int]`；多餘欄位由 `CardService` 拋 `FIELD_MISMATCH`。
  - 讀取：欄位依順序、形狀同 §4.3。
  - 修改：只寫有變的欄位；`update_fields` 以外的 action 拋不支援。
  - 刪除：先刪筆記再刪關聯；筆記刪除失敗時不刪關聯。
- **試點（單字卡）**：`vocabulary_mining` 沒有覆寫任何 `execute_*`；修改 `Expression` 時關聯表兩端的標籤都更新；`PATCH` JSON、媒體欄位回 `FIELD_NOT_EDITABLE`；建卡表單不含這些欄位；`create_schema` 依 Anki 查到的 `_v2` 欄位產生，第一個欄位 `Expression` 必填。
- **圖譜**（D12）：基底對任意筆記類型都能出圖，名稱預設為 nid；`label_field` 指定欄位時改用該欄位、空值退回 nid、不存在回 422；`note_type` 篩選有效、不屬於 handler 回 422；`RelationGraphMixin` 的學習狀態、關聯連線、幽靈節點與現在 `vocabulary_mining` 的輸出一致（以現有測資比對，差別只在副標改讀 `Target_Translation` 後有值）；`GraphSpec`、`RELATION_LABEL_FIELD` 的欄位名不存在 → 啟動失敗；改 `Expression` 後關聯表兩端的標籤都更新。
- **本機腳本保護**（D7，G8）：
  - `POST /verb-pair/generate-child-cards`、`POST /core-verb/generate-child-cards`：以 mock 的 `execute_generate` 打端點，斷言路徑存在、不帶 `X-API-Key` 也能呼叫、請求欄位與現在相同（`core-verb` 的 `target_verb_span` 可省略）、回應為 `{"status", "message", "data"}` 且 `data` 原樣回傳 `execute_generate` 的結果。
  - `JPVerbPairHandler()`、`JPCoreVerbHandler()` 可無參數實例化，`execute_generate` 的參數名不變（`card_service`、`relation_service`、`parameters`）。
  - 宣告 `channels` 含 `"script"` 的 handler 都有 `execute_generate`。
  - LLM 用途鍵 `jp_verb_pair`、`jp_core_verb` 仍已宣告（`tests/test_llm_routing.py` 既有測試）。
  - 腳本端既有測試照跑：`scripts/fastapi_client/JP_CoreVerb/tests/`（`backend/tests` 之外，要另外指定）。
- 殘骸清除（D8）：`/example/verb-pair/create`、`POST /verb-pair/create`、`PUT /verb-pair/{id}/update` 回 404 或 405；`tests/test_llm_routing.py` 拿掉 `verb_pair_example` 後照過。
- `NoteService`：
  - `create`：缺必填 → `ValidationError`；`note_type` 不對 → `NOTE_TYPE_NOT_CREATABLE`；不開放 → `HANDLER_OPERATION_UNSUPPORTED`；成功時寫入後強制同步一次並等它完成；寫入後同步失敗時回傳提醒、不拋錯；`tg_bot` 預設從設定補上、可被覆寫；`expression_correction` 只給 TG 實際傳的參數（無語言欄位）也能通過驗證。
  - `read`、`update_fields`、`delete`：筆記不歸網址上的 handler → `NOTE_NOT_OWNED_BY_HANDLER`；不開放 → 不支援，Anki 與關聯都沒被動到；`JP_Context_Dark` 從兩個 jp handler 的網址都能讀。
  - `run_action`：動作不在 `actions` 內 → 不支援；每個 handler 宣告的每個動作都真的能執行。
  - `get`：`permissions` 依 D4 計算（含各種 `reason`）；筆記不存在 → `NOTE_NOT_FOUND`。
- REST：`GET /handlers` 的內容（快照比對）；各端點的狀態碼與錯誤代碼（§3 D6）；422 的 `loc` 為 `["body", "params", 欄位]`；舊路徑（`/handlers/{name}/create`、`/handlers/cards/*`、`/handlers/decks`、`/handlers/{name}/cards`、`PUT /handlers/{name}/cards/{id}`）回 404 或 405。
  - `tests/test_card_browse_api.py:406-415` 現在斷言 `/handlers/cards/{note_id}` 回 camelCase，改為斷言 `/notes/{note_id}` 的新形狀。
- TG：既有測試全過。`tests/test_bot_replies.py` 現在 mock `handler_registry.get_handler`（約 `:311`、`:336`、`:569`、`:724-749`、`:799`、`:861`、`:934`），改為 mock `NoteService`，斷言呼叫了服務層而不是 handler；建單字卡斷言傳的是 `{"Expression": 單字}` 與 `_v2`；外語糾錯覆蓋舊卡的測試維持斷言直接刪除。
- 前端：`SplitLayout` 的並排／疊放斷點與 `usePanelWidths` 依頁面 ID 分開記（沿用 `cardLayout.test.ts` 並擴充）；schema 轉表單欄位的純函式測試（`title`、`required`、`examples`、`x-ui`）；`channels` 轉成「可選／反灰／不顯示」的純函式測試；`permissions` 決定可否編輯、刪除（取代 `cardFields.test.ts` 的 handler 白名單與 `orderedFields` 測試）。

## 9. 實作順序

0. ✅ 殘骸清除（D8）：`verb_pair_example` 一整組、`POST /verb-pair/create`、`PUT /verb-pair/{id}/update`、`supported_models` 裡的舊版 TOEIC、`Default`、`Speaking_Coach_Light`。先做，讓第 1 步的註冊表檢查不必顧慮範例 handler。
1. ✅ 後端基底：錯誤類別（`HandlerOperationUnsupportedError`、`NoteNotFoundError`、`NoteTypeNotCreatableError`、`NoteNotOwnedByHandlerError`、`FieldNotEditableError`）；`BaseHandler` 的標準 CRUD 與標準圖譜（`query_notes`、`GraphSpec`）、`RelationGraphMixin`（由現在 `vocabulary_mining` 的圖譜邏輯抽出）、`RELATION_LABEL_FIELD`、`JSON_FIELDS`／`MEDIA_FIELDS` 名稱宣告；`RelationService.update_labels_for_note`；預設拋不支援的方法、移除的方法、`CAPABILITIES`；註冊表檢查與「筆記類型 → handler」對照；`task_handlers/__init__.py` 統一註冊。
2. ✅ 各 handler：依 §5 宣告能力（`title` 已在第 1 步加上）；口說卡、外語糾錯的 `CreateParams`；刪掉空實作與各自的 `execute_delete`（`execute_read_list`、`get_input_schema` 留到第 5 步）；`execute_create` 回傳改 `list[int]`；單字卡刪掉 `execute_create`、`execute_update`、`execute_read_graph` 覆寫，改掛 `RelationGraphMixin`，宣告 `GRAPH`、`RELATION_LABEL_FIELD`、`JSON_FIELDS`、`MEDIA_FIELDS`。jp 兩個只動到這些，`execute_generate` 不碰；本步結束就跑 §8 的腳本保護測試。
3. `NoteService`（含寫入後同步的提醒邏輯、歸屬檢查）與它的測試。
4. TG 的 5 處改呼叫 `NoteService`（§7 表格），`bot/dependencies.py` 注入；`_sync_with_warning` 改為包裝服務層；TG 既有測試全過。
5. REST 端點：`/handlers`、`/handlers/{name}/notes/...`、`/notes/{note_id}`、`/decks`，移除舊路徑；從 `BaseHandler` 與各 handler 移除 `get_input_schema`、`execute_read_list`；`main.py` 掛新路由；測試。`api/verb_pair.py`、`api/core_verb.py` 的 `generate-child-cards` 不動。
6. 前端：client 與型別；從 Cards 抽出共用元件（D13），Cards 改用後畫面與行為不變；`CardViewPanel` 與 `CardDetailModal`；工具設定頁的「Handler 能力」區塊。
7. 前端：Knowledge Graph 改用共用版面（左側牌組樹）、新的圖譜參數與點開卡片、全螢幕（D12、D13）。
8. 前端：Generate 頁改用共用版面，依 `channels` 與 `create_schema` 產生下拉與表單（§6、D13）。

**第 1 步實作結果（2026-10-20）**：

- 新增的檔案：
  - `task_handlers/capabilities.py`：`Operation`、`CreateCapability`、`GraphCapability`、`HandlerCapabilities`、`GraphSpec`（不可變的資料類別）。不開放又沒寫 `reason` 時自動補「尚未開放」，開放時預設為 null（開放的能力不會帶著「尚未開放」回給前端）。宣告的**形狀**在建立時就檢查，寫錯在 import 時就失敗：開放建卡但 `channels` 不含 `web` 時必須寫 `reason`（說明要去哪裡建）；`channels` 只能是三種值；`channels` 含 `web` 時必須開放且 `flow` 為 `form`；開放建卡時必須有 `note_type`、`flow`、`deck`，且 `channels` 至少有 `web` 或 `tg`；`deck` 為 `fixed` 時必須有 `fixed_deck`；`links` 只能在開放圖譜時宣告。
  - `task_handlers/model_fields.py`：讀 `app/anki_models/*.json` 的 `inOrderFields`，給註冊檢查宣告的欄位用（註冊在 import 時，還沒有 AnkiConnect）。
  - `task_handlers/relation_graph.py`：`RelationGraphMixin`。
  - `tests/test_handler_base.py`：55 個測試，涵蓋 §8 的註冊表、標準 CRUD、圖譜、`update_labels_for_note` 各項。
- `BaseHandler`：類別屬性 `CAPABILITIES`（預設只開放讀取）、`GRAPH`、`RELATION_LABEL_FIELD`、`JSON_FIELDS`、`MEDIA_FIELDS`、`CREATE_TAGS`（標準建立加的標籤，預設 `HandlerGenerated`）；標準的 `execute_create`（回傳 `list[int]`）、`execute_read`、`execute_update`、`execute_delete`、`query_notes`、`execute_read_graph`；`note_types_for`（查詢範圍內的筆記類型，查詢與圖譜共用這一個檢查）；共用工具 `note_detail`（§4.3 的形狀，NoteService 的通用讀取也用它）、`clean_html`、`field_value`。
- `CardService.update_note_fields` 新增選填的 `model_name`：標準修改已經讀過筆記比對欄位，傳入後不必再讀一次。
- 註冊表：註冊前檢查，不過就 `RuntimeError`，而且不留下任何註冊；新增 `handler_for_note_type`、`handlers_for_note_type`、`all_handlers`。`task_handlers/__init__.py` 統一 import 6 個 handler，`api/handlers.py` 的 import 刪掉。實際註冊結果：6 個，`JP_Context_Dark` 依序對到 `jp_verb_pair`、`jp_core_verb`。
- `CardService.get_note` 找不到筆記時改拋 `NoteNotFoundError`（404）。確認過沒有程式專門攔 `AnkiServiceError` 而依賴這個情況。
- `RelationService.update_labels_for_note`：同一次 commit 更新起點與終點兩端；資料庫驅動沒回報筆數時回傳 None（比照 `sync_with_anki`）。`update_source_label` 已刪除，單字卡現有的 `execute_update` 改呼叫新方法，「改名後當終點的連線沒跟著改」這個問題在第 1 步就修好了。單字卡自己的 `_clean_html` 也改用基底的 `clean_html`。
- 註冊檢查宣告的欄位時，負責的筆記類型缺定義檔會直接指出缺哪個檔，不誤報成欄位寫錯。
- `RelationGraphMixin` 與原本 `vocabulary_mining.execute_read_graph` 的一致性：以同一份測資（含學習狀態、同義詞、搭配詞、兩端都沒有筆記的關聯）、副標與詞性指回舊的 `Meaning`、`PartOfSpeech`，兩者的 `nodes`、`links` 與查關聯的參數完全相同（一次性比對；舊實作第 2 步刪除，所以測試檔裡寫的是預期結果本身）。

與上文不同、已照實作調整的地方：

| # | 計畫 | 實作 | 原因 |
|---|---|---|---|
| 1 | `title` 在第 2 步加 | 第 1 步就加（6 個 handler） | `title` 是抽象屬性，不加的話 handler 無法實例化 |
| 2 | `get_input_schema`、`execute_read_list` 在第 2 步移除 | 留到第 5 步，跟著舊的 `GET /handlers/`、`GET /handlers/{name}/cards` 一起移除；在那之前仍是抽象方法 | 現行的 `api/handlers.py` 還在用。第 5 步之後抽象方法才只剩 `handler_name`、`supported_models`、`title`（D7） |
| 3 | D6 沒有查詢參數、欄位格式的錯誤 | 新增 `InvalidNoteQueryError`（422 `INVALID_NOTE_QUERY`）、`InvalidNoteFieldsError`（422 `INVALID_NOTE_FIELDS`） | §4.6 的三種 422 需要一個錯誤代碼，不限圖譜（之後依 handler 列出筆記也用）；`fields` 格式錯誤原本會變成 500。已補進 D6 表 |
| 4 | `RelationGraphMixin` 是 mixin | 它本身繼承 `BaseHandler`，用它的 handler 改繼承它（`class X(RelationGraphMixin)`） | 型別上能直接用基底的屬性與 `query_notes`；之後與 `GroupedHandler` 多重繼承時，兩者共用同一個基底，MRO 照常運作 |
| 5 | 註冊檢查依 D2 表格 | 另外檢查：用 `RelationGraphMixin` 必須宣告 `RELATION_LABEL_FIELD`；開放圖譜時 `links` 必須與「是否用 mixin」一致（雙向）；`RELATION_LABEL_FIELD`、`JSON_FIELDS`、`MEDIA_FIELDS`、`GRAPH` 的欄位至少存在於一個負責的筆記類型的定義檔；共用筆記類型的比對包含 `read` | D12「用了它的 handler，`graph.links` 為 true」與「欄位名在註冊時檢查」的落實 |
| 6 | §4.6 的 `label_field` 回 null 表示用預設 | 回應的 `label_field` 是實際採用的欄位，null 表示 nid | 前端下拉要知道目前選的是哪個欄位；已改 §4.6 |
| 7 | 標準修改「只寫有變的欄位」 | 先讀筆記比對，值相同的欄位不送；全部相同時不寫入、也不碰關聯 | 前端送整份表單時也不會多寫；`RELATION_LABEL_FIELD` 沒變就不更新關聯 |

審查後修正（2026-10-20）：開放能力的預設原因、修改時重複讀筆記、`fields` 格式錯誤回 500、`rowcount` 未回報、缺定義檔的錯誤訊息、重複的欄位讀取與筆記類型檢查、單字卡仍用舊的單端標籤更新，都已照上面各點修正。

驗證：`ruff` 通過；`pytest tests` 1003 個（原 948 ＋ 新 55）、`scripts/fastapi_client/JP_CoreVerb/tests` 83 個通過；三支批次腳本 `--help` 正常；app 的 OpenAPI 仍有兩個 `generate-child-cards` 端點。

**第 2 步實作結果（2026-10-20）**：

- 6 個 handler 依 §5 宣告 `CAPABILITIES`；能力以快照測試鎖定（`tests/test_handler_declarations.py`）。`JP_Context_Dark` 兩個 jp handler 的讀取、改欄位、刪除、動作相同，註冊檢查通過。
- **單字卡（試點）**：改繼承 `RelationGraphMixin`，`execute_create`、`execute_update`、`execute_delete`、`execute_read_graph` 全部刪掉，增刪改查都是基底。宣告 `GRAPH = GraphSpec(subtitle="Target_Translation", pos="Target_POS")`、`RELATION_LABEL_FIELD = "Expression"`、`JSON_FIELDS`（三個 JSON 欄位）、`MEDIA_FIELDS`（`Word_Audio`、`Target_Example_Audio`）、`CREATE_TAGS = ("VocabularyMining", "HandlerGenerated")`（沿用原本的標籤）。
- **建立參數**：`BaseHandler` 新增類別屬性 `CREATE_PARAMS`。口說卡 `SpeakingCoachCreateParams`（`prompt` 必填；`target_language` 預設 `en-US`，`examples` 三個值、`pattern` 驗證，validator 與 TG 共用正規化）、三語卡 `SpeakingTrilingualCreateParams`（`prompt` 必填）、外語糾錯 `ExpressionCreateParams`（只有 `original_text` 必填；預設值與 handler 讀參數時的預設相同，例如語言預設「中文」「日文」，所以 `model_dump()` 交給 `execute_create` 結果不變）。都不含 `tg_bot`。
- 註冊檢查新增兩條：開放建卡且覆寫了 `execute_create` → 必須宣告 `CREATE_PARAMS`；宣告了 `CREATE_PARAMS` → 必須覆寫 `execute_create`（標準建立的參數就是筆記欄位）。
- 語言代碼的正規化從 `bot/handlers/fsm/speaking_fsm.py` 的 `_normalize_language_code` 搬到 `app/schemas/language.py` 的 `normalize_language_code`（連同 `LANG_CODE_PATTERN`），TG 與 `CreateParams` 共用；服務層不能反過來 import TG 模組。
- 口說卡、三語卡的 `execute_create` 改回傳單元素陣列；外語糾錯的回傳型別改為 `list[int]`（本來就回陣列）；母卡牌組抽成常數 `MASTER_DECK`。口說卡兩個 handler 共用動作清單 `SPEAKING_ACTIONS`。
- 刪掉的空實作與重複實作：口說卡、三語卡、外語糾錯的 `execute_read_graph`、`execute_delete`；jp 兩個的 `execute_create`、`execute_update`、`execute_delete`、`execute_read_graph`。`get_input_schema`、`execute_read_list` 依第 1 步的決定留到第 5 步。jp 兩個的 `execute_generate` 以 AST 比對與改前**完全相同**。
- 舊的 `GET /handlers/{name}/graph` 回傳型別改為 `dict[str, object]`：圖譜回應多了 `label_fields`、`label_field`，舊的型別標註會讓 FastAPI 的回應驗證失敗。
- **本機腳本保護測試**（§8）：以真實 app、設定了 `API_SECRET_KEY` 打兩個 `generate-child-cards`，不帶 `X-API-Key` 回 200；回應形狀 `{"status", "message", "data"}` 且 `data` 原樣回傳；請求欄位不變、`core-verb` 的 `target_verb_span` 可省略、少欄位回 422；`execute_generate` 的參數名不變；`channels` 含 `script` 的 handler 都有 `execute_generate`；D8 移除的三個殘骸端點回 404／405。

這一步造成的行為變化（舊的 REST 端點與 TG 都還沒改，第 3～5 步才接上服務層）：

| 地方 | 改前 | 改後 |
|---|---|---|
| 單字卡圖譜 | 節點副標、詞性永遠空白（讀不存在的 `Meaning`、`PartOfSpeech`） | 有值；回應多 `label_fields`、`label_field` |
| 單字卡改欄位（`PUT /handlers/vocabulary_mining/cards/{id}`） | 任何欄位都寫 | JSON、媒體欄位回 400 `FIELD_NOT_EDITABLE`；值沒變的欄位不寫 |
| 單字卡建卡（網頁 Generate、TG） | 寫 `Meaning`、`Context` → `FIELD_MISMATCH` | 傳的是 `{"word": …}` → 仍是 `FIELD_MISMATCH`。網頁第 8 步、TG 第 4 步改傳欄位後才能建 |
| 外語糾錯、jp 的刪除（舊 `DELETE` 端點） | 只刪筆記 | 先刪筆記再刪關聯（jp 的 `execute_generate` 會建關聯，原本刪卡會留下孤兒關聯）。網頁的刪除按鈕要到第 6 步才依能力關閉 |
| jp 的改欄位（舊 `PUT` 端點，`update_fields`） | 空實作，回 200 但什麼都沒改 | 基底的標準修改會真的寫入。網頁不顯示編輯（`FIELD_EDIT_HANDLERS` 不含 jp），只有直接呼叫 API 會碰到；第 3 步的服務層、第 5 步的新端點依能力擋下 |

驗證：`ruff` 通過；`pytest tests` 1041 個（第 2 步新增 38 個）、`scripts/fastapi_client/JP_CoreVerb/tests` 83 個通過；三支批次腳本 `--help` 正常；`scripts/local_anki/Expression_Correction/generate_expression_cards.py` 可正常 import；OpenAPI 仍有兩個 `generate-child-cards` 端點。

每一步結束都要通過：

```bash
cd backend && python -m ruff check app scripts tests && python -m pytest tests -q
```

```bash
cd backend && python -m pytest scripts/fastapi_client/JP_CoreVerb/tests -q
```

本機腳本仍能載入、解析參數（不連 ES、不呼叫 API）：

```bash
cd backend && python scripts/fastapi_client/JP_VerbPair/generate_child_cards.py --help && python scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py --help && python scripts/fastapi_client/JP_VerbPair/mark_skipped_es_data.py --help
```

```bash
cd frontend && npm run lint && npm test && npm run build
```

## 10. 驗收

- [ ] `GET /handlers` 回傳 6 個 handler 的能力，與 §5 一致（沒有 `verb_pair_example`、舊版 TOEIC、`Default`、`Speaking_Coach_Light`）
- [ ] 工具設定頁的「Handler 能力」區塊與 `GET /handlers` 一致，不開放的項目顯示原因
- [ ] 三頁版面（D13）：Cards 改用共用元件後畫面與行為不變；Generate、Graph 左側是牌組樹，牌組只由它決定；三頁各自記住面板寬度與選取的牌組；窄螢幕時疊成一欄
- [ ] Graph 全螢幕：圖佔滿整個螢幕（瀏覽器介面也隱藏），點節點的卡片詳情與「選擇關聯類型」視窗在全螢幕中看得到，Esc 退出
- [ ] **試點：網頁上單字卡的增刪改查全部跑通**
  - Generate 頁：單字卡可選，其他依 §6 反灰或不顯示；表單欄位與 Anki 裡 `_v2` 的欄位一致；`Expression` 空白時無法送出；建出的卡在左側所選牌組、筆記類型為 `_v2`；重複的單字回「已有相同卡片」；建立後 AnkiWeb 上看得到（證明有同步）
  - Cards 頁：單字卡可讀、可改欄位、可刪除；刪除後關聯一併消失；改與刪都同步到 AnkiWeb
- [ ] Cards 頁：其他筆記依 §5 顯示唯讀或不可刪除與原因（jp 類顯示刪除工具路徑；沒有 handler 的筆記顯示「沒有 handler 負責」）
- [ ] 對 `jp_verb_pair` 呼叫建立回 400 並帶原因，不再回「建立成功」
- [ ] Knowledge Graph：單字卡的圖譜照常顯示（連線、顏色、幽靈節點），牌組選「全部」時正常；滑鼠移到單字節點上會顯示翻譯（現在是空的）；點節點打開那張卡的詳情；在 Cards 頁改了某張單字卡的 `Expression` 後，圖譜上的節點跟著改名、連線不斷
- [ ] **本機腳本**（部署後，在本機以平常的 `.env` 執行）：
  - `JP_VerbPair/generate_child_cards.py --limit 0 --dry-run` 與 `JP_CoreVerb/generate_child_cards.py --limit 0 --dry-run` 正常跑完，預計數量與部署前相同（部署前先各跑一次記下數量）
  - 兩支各以 `--limit 1` 實跑一次：子卡建出、母卡 JSON 已回寫、語音與頭像已上傳、生成紀錄有寫入（會花一次 LLM 費用）
- [ ] TG：建單字卡**能成功**（`_v2`、只有單字）、外語糾錯（含覆蓋舊卡）、錄音評分、上傳音檔（一般對話卡與三語卡各一次，含自訂頭像）都正常，同步提醒照舊出現

## 11. 風險

| # | 風險 | 應對 |
|---|---|---|
| R1 | TG 的 5 處改走服務層，行為可能有細微差異（同步時機、錯誤訊息） | §7 逐處對照；§10 的 TG 驗收逐項實測 |
| R2 | 網頁的寫入多了一次寫入後的強制同步，而且等它完成才回應（強制同步不受防抖，`SYNC_TIMEOUT` 為 60 秒） | 已決定等同步完成（與 TG 一致，`sync_warning` 才有意義）；前端寫入時顯示「同步中」 |
| R3 | 前後端容器先後重建，中間數秒新舊 API 不一致 | 可接受；若要避免，舊路徑保留一個版本的別名再移除 |
| R4 | 網頁能做的事暫時比現在少：建、刪、改欄位只剩單字卡（現在口說卡也能改欄位） | 依 G3 刻意收斂，之後逐一開放；工具設定頁可看到進度 |
| R5 | 刪除改為由基底統一刪關聯 | 只會刪掉指向已刪除筆記的關聯，這些原本就會被 `/relations/sync` 清掉 |
| R6 | 改壞本機批次腳本：jp handler 的能力宣告或註冊檢查寫錯，後端起不來，腳本端點跟著不能用；或不小心動到端點的請求、回應形狀 | D7 列出不能動的部分；§8 的腳本保護測試；§9 第 2 步結束就跑；§10 部署後 dry-run 與 `--limit 1` 實跑 |
| R7 | 移除 `verb_pair_example` 後，`capability_routes` 裡它的設定列變成孤兒鍵 | 工具設定頁照常列出、可手動刪除，不影響其他用途 |
| R8 | `GET /handlers` 每次都向 Anki 查欄位產生 `create_schema`，Anki 沒開時這支端點會失敗 | 查不到時 `create_schema` 回 null、`create.reason` 註明「無法連線 Anki」，其餘能力照常回傳 |

## 12. 2026-10-20 對照程式碼的更正

初版有以下與現行程式碼不符之處，已改正於上文：

| # | 初版寫法 | 實際 | 改在 |
|---|---|---|---|
| 1 | 外語糾錯 handler 叫 `expression` | 註冊名稱是 `expression_correction`（`expression_handler.py:52`），TG 與腳本都用這個名稱取 handler；它會出現在網址裡，沿用現名 | 全文 |
| 2 | REST 的建立、修改、刪除「完全不同步」，寫入前沒先同步 | `AnkiClient` 每個方法呼叫前都會 `sync()`（防抖、失敗不擋）；REST 缺的只有寫入後的強制同步與提醒 | §1、D1、R2 |
| 3 | 服務層寫入前、寫入後各同步一次 | 寫入前已由 `AnkiClient` 處理，服務層只做寫入後的強制同步 | D1、§8 |
| 4 | 只有 `vocabulary_mining` 的刪除會刪關聯 | `speaking_coach`、`speaking_trilingual` 也會；不會的是 `expression_correction` 與 jp 兩個 | §1 |
| 5 | 服務層「先刪關聯、再刪筆記」 | 現有 3 個 handler 都是先刪筆記；反過來在筆記刪除失敗時會丟失關聯，維持先刪筆記 | D10 |
| 6 | `UNKNOWN_NOTE_FIELD`（422）由服務層檢查 | `CardService.update_note_fields`／`create_note` 已嚴格比對欄位，拋 `FIELD_MISMATCH`（400），沿用 | D6 |
| 7 | 筆記不存在「目前轉成 500」 | `CardService.get_note` 拋 `AnkiServiceError`，回 502 | D6 |
| 8 | Pydantic 驗證錯誤由「REST 層轉成 422」 | 端點內拋的 `ValidationError` FastAPI 不會自動轉，要明確改拋 `RequestValidationError` | D3 |
| 9 | 建立請求的參數放在 `fields` | 與 `PATCH` 的 Anki 欄位 `fields` 同名但意義不同，改叫 `params` | §4.2 |
| 10 | `expression` 的 `CreateParams` 照 `input_schema` | TG 不傳語言欄位，照搬 `required` 會讓 TG 建卡失敗 | D3 |
| 11 | jp 兩個的 `execute_update`「TG 與腳本都沒有呼叫」 | 正確，但 `PUT /verb-pair/{note_id}/update` 會呼叫（回假成功）；移除 | D8 |
| 12 | 外語糾錯的 TG 改動只有建卡 | 覆蓋舊卡時 TG 自己 `delete_notes`、不刪關聯；外語糾錯刪除不開放，這段維持原樣並保留它的同步 | §7 |
| 13 | `BaseHandler` 只改三個方法的預設行為 | `execute_read_list`、`get_input_schema` 也已沒有用途；`execute_create` 回傳型別與實際不符 | D10 |
| 14 | 註冊分散（H11）未處理 | D4「取第一個註冊的」依賴註冊順序；jp 兩個只在 import API 模組時才註冊，一併集中到 `__init__.py` | D2 |
| 15 | 圖譜「取代現在的特殊值 `All Decks`」 | 後端本來就以省略代表全部，`All Decks` 是前端的值；後端改的只是參數名 `deck_name` → `deck` | §4.6、D5 |
| 16 | 前端讀單張筆記只列 `CardDetailModal`、Cards | `CardViewPanel`、`KnowledgeGraph` 也讀 `/handlers/cards/{id}` | §6 |
| 17 | 測試未提既有測試的改寫 | `test_bot_replies.py` mock 了 `get_handler`、`test_card_browse_api.py` 斷言舊路徑的形狀 | §8 |
| 18 | TG 改動行號 | 依現行程式碼更新 | §7、§9 |
| 19 | handler 共 6 個 | 範例用的 `verb_pair_example` 也被註冊，`GET /handlers` 實際回 7 個，且與 `jp_verb_pair` 共用筆記類型 | D2、D4、D8 |
| 20 | `Speaking_Coach_Light`、`Default`、`TOEIC_Coach_Dark` 是單字卡／對話卡在用的筆記類型 | 實機 Anki：前兩個不存在，`TOEIC_Coach_Dark` 0 張筆記（800 張都在 `_v2`） | D8 |
| 21 | 刪除一律可以 | jp 類筆記和母卡 JSON、查重紀錄互相參照，通用刪除會讓三者對不上；本機有專用刪除工具 | D10、§5 |
| 22 | 只有建卡能力，前端無法分辨「不能建」與「要在別處建」 | 新增 `create.channels` | D2、§6 |

## 13. 待決事項

**之後再決定的事（不擋本計畫）**

| 項目 | 現況 |
|---|---|
| 舊版 `TOEIC_Coach_Dark` 的筆記類型、定義檔、提示詞模板 | Anki 裡 0 張筆記；本計畫只從 handler 清單拿掉，要不要刪由你決定（Anki 裡的要你自己操作） |
| `GET /relations/graph`、前端 `getKnowledgeGraph`、`RelationService.get_graph_data` | 沒有呼叫方；與 handler 的標準圖譜重複（D12） |
| `DELETE /relations/by-note/{note_id}` | 沒有呼叫方；`RelationService.delete_relations_by_note_id` 與 `delete_relations_for_note` 是兩個一樣的方法 |
| 通用 LLM 建卡的殘骸：`AnkiModelManager` 的 `get_model_schema`、`create_note_from_llm_response`、`list_available_models`、`get_model_detail`、`can_add_note`、`sync_to_ankiweb`；`schemas/card.py` 的 `CardGenerateRequest` | 沒有呼叫方；網頁不做自動化（D9），本機腳本可能重用，先保留 |
| `scripts/local_anki/import_cards_with_llm.py` | 呼叫已不存在的 `CardService.generate_card`，執行必定失敗 |
| `/storage/*`（MinIO 上傳、列出、預簽、刪除） | repo 內沒有呼叫方 |
| `verb-pair`、`core-verb`、`config`、`jp` 路由沒有 `X-API-Key` 驗證 | 若要補，本機腳本要同時改成送 `X-API-Key`（D7） |
| 沒有 handler 的筆記類型（Notion_SRS 712 張、Shadowing_Breakdown 206 張、AI_QA 42 張、Conversation_Coach 31 張、Voice_Shadowing 17 張、Contrast_Coach 1 張） | 目前只有 `scripts/local_anki/import_cards_from_json.py` 能建；要在網頁開放就先為它們建 handler（基底的標準 CRUD 與圖譜可直接用） |
