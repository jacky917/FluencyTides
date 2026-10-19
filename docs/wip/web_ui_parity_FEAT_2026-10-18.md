# FEAT — 網頁版補齊 TG 能力，並重做卡片瀏覽

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-18 |
| **性質** | 盤點 + 分階段計畫書 |
| **狀態** | 🚧 階段 A（卡片瀏覽）完成（df224ed，之後 765f913～9c2b85e 持續改進）；階段 B 的 `/setconfig` 部分改由能力路由完成（PR #25）；階段 B 其餘、C、D、E 尚未開始。2026-10-19 依能力路由 R2 更新階段 B、C 的內容 |
| **範圍** | `frontend/`；為了讓 TG 與網頁共用，需要調整 `backend/app/services/`、`backend/app/api/`、`backend/app/bot/handlers/` |
| **基準** | 分支 `claude/beautiful-lamarr-96c769`（PR #25）。它接在 `feat/llm-model-routing`（859031f，PR #24 已合併）之上，已含網頁版工具設定頁（f3e3f22、db8be22，能力路由 R1／R2 擴充） |
| **原則** | 邏輯只寫一份：抽到 service 層，TG handler 與 REST 端點呼叫同一個函式；網頁的流程是無狀態的請求，所需參數一次帶齊 |

---

## 1. 結論

- **卡片顯示：目前每一張卡都顯示不正常，不只是「部分」。** 卡片詳情 API 回傳 `{noteId, modelName, fields: {名稱: {value, order}}}`，前端型別卻是 `{note_id, model_name, fields: Record<string,string>}`：
  - 每個欄位都顯示 `[object Object]`。
  - 儲存與刪除都送到 `/cards/undefined`。
  - 網頁上只看得到 `vocabulary_mining` 的卡，而且只在知識圖譜中以節點出現。沒有任何卡片清單。
- **就算修好欄位，大多數筆記類型仍需要模板的 JavaScript 才看得懂。** JSON 欄位（錄音、參考答案、對話）、ruby、音檔都要靠模板。最省工、也最不會漏的做法，是直接用 Anki 已經渲染好的卡面（`cardsInfo` 的 `question`/`answer`），放進 sandbox iframe 顯示，再補一個 Anki 媒體代理端點。
- **TG 能力中，後端已有 REST 的只有 `/sync` 與 `/models`（現為 `/setconfig`，對應 `/api/v1/capability-routes`）。** 錄音評分、上傳音檔、刪除 JSON 條目、`/setconfig` 寫入、外語糾錯的預覽與重複處理、對話卡的建立細節都寫在 TG handler 裡，要先抽成 service。
- **建議順序：** 卡片瀏覽 → 小型管理功能 → 錄音評分 → 上傳音檔 → 建卡流程。前兩項不碰 TG handler，風險最低；錄音評分與上傳音檔是口說練習的核心，接著做。

---

## 2. TG 功能對照表

| # | TG 功能 | 邏輯目前在哪 | 已有的 REST | 網頁缺什麼 |
|---|---|---|---|---|
| 1a | 外語糾錯（6 步輸入 → LLM 預覽 → 選子卡寫入） | LLM 呼叫與寫入在 `expression_handler.py`（`execute_generate` :84、`execute_create` :151）。**重複查詢、覆蓋時刪舊卡、子卡編號解析只在 `fsm/expression_fsm.py`** | `POST /handlers/expression_correction/create`（可帶 `llm_result`、`selected_indices`、`allow_duplicate`） | 預覽端點（只呼叫 LLM、不寫入，並回傳重複資訊）、create 支援「覆蓋」（先建新卡，成功後才刪舊母卡與子卡） |
| 1b | 對話卡（牌組、語言、提示句、情境、參考回答） | **只在 `fsm/speaking_fsm.py`**：自己組 note、直接 `add_note`。`SpeakingCoachHandler.execute_create` 的行為不同（Card_ID 格式、References、tags 都不一樣） | `POST /handlers/speaking_coach/create`（行為與 TG 不同） | handler 補上 `references[]` 與語言正規化，FSM 改呼叫它 |
| 1c | 單字卡 | `vocabulary_mining_handler.py:81`（沒有 LLM，Meaning 是佔位字） | 已有，CardGenerator 在用 | 功能上不缺。另見 §6 的既有 bug |
| 2 | 錄音評分（提交錄音 → STT + AI 評分 → 寫回 Recordings） | **流程只在 `bot/handlers/voice.py:63`**：下載、同步、讀 Prompt/Context/References、三語分支、存媒體、評分、錯誤對應。評分器與寫回是共用的 | 只有 `PUT .../cards/{id}` 的 `add_recording`（只接受已評好分的紀錄） | `RecordingService.evaluate_and_save()`，以及 multipart `POST /cards/{note_id}/recordings`；瀏覽器錄音格式要轉檔（見 §4.3） |
| 3 | 上傳音檔到 References / Prompt_Audios（頭像、說話者） | 寫入欄位的部分共用（`execute_update(action=add_audio)`）；**頭像清單、自訂頭像、選 handler 只在 TG**（`callbacks.py`、`messages.py`、`media.py`） | 只有 `add_audio`，而且檔案要已經在 Anki 媒體庫 | `GET /avatars`、`POST /avatars`、multipart `POST /cards/{note_id}/audio` |
| 4 | 刪除 JSON 欄位條目（含三語 `_ZH/_JA/_EN`） | **只在 `commands.py:241 _handle_delete_entry`** | 只有 Recordings 的 `delete_recording` | `DELETE /cards/{note_id}/fields/{field}/{index}`，背後是新的 service 函式 |
| 5a | `/sync`（清理失效關聯） | `relation_service.sync_with_anki` | `POST /relations/sync`；前端已有 `syncRelations` | 只缺網頁上的按鈕 |
| 5b | `/setconfig`（MODIFY_* 執行期設定） | ✅ 已由能力路由取代（`docs/archive/capability_routing_FEAT_2026-10-19.md`，PR #25）：舊 `/setconfig`、`callbacks_config.py`、`MODIFY_*` 已移除 | `GET /config` 只剩診斷用的 `runtime` | TG `/setconfig` 與網頁工具設定頁（`/api/v1/capability-routes`），設定存在資料庫 |
| 5c | `/models` | `llm_route_service.py`，已共用 | `GET/PUT/DELETE /llm-routes` | 已完成（`LlmRoutes.tsx`） |
| 6 | `/start`、`/help`、`/newcard` 選單；TTS、`gen` 按鈕 | 選單與說明；TTS 與 `gen` 都回覆「尚未開放」 | — | 網頁不需要對應。TTS 在兩邊都沒有實作 |

---

## 3. 卡片顯示的問題

### 3.1 所有筆記類型共同的問題

| # | 問題 | 位置 |
|---|---|---|
| A | 卡片詳情的回傳格式與前端型別不符，每個欄位都是 `[object Object]`，儲存與刪除送到 `/cards/undefined` | `api/handlers.py:309`、`frontend/src/types/api.ts` 的 `CardDetail`、`CardDetailModal.tsx` |
| B | 網頁只能看到 `vocabulary_mining`。知識圖譜寫死這個 handler；其他 4 個 handler 的 `/cards` 回傳 `[]`；6 種筆記類型沒有 handler | `KnowledgeGraph.tsx:75`、`expression_handler.py:324` 等 |
| C | 沒有渲染器，JSON、HTML、ruby、音檔都會以原始文字顯示 | `CardDetailModal.tsx:116-141` |
| D | JSON 欄位的存法不一致，有的被 HTML 跳脫（`&quot;`），有的夾著 `<div>`/`<br>`，直接 `JSON.parse` 會失敗 | 後端用 `json_modifier.py:58-65` 處理 |
| E | 音檔與頭像是 Anki 媒體庫的檔名，網頁沒有端點可以取得 | `client.py:868 retrieve_media_file` 只在後端使用 |
| F | 沒有牌組樹；`notesInfo` 不含牌組名 | `/handlers/decks` 是扁平清單，但已包含每一層的父牌組 |

### 3.2 各筆記類型修好 A、B 之後仍會壞的地方

| 筆記類型 | 仍會壞的地方 |
|---|---|
| TOEIC_Coach_Dark / _v2 | `*_JSON` 由模板 JS 渲染（網頁的 modal 會隱藏名稱含 JSON 的欄位）；`Audio` 是 `[sound:]` |
| Speaking_Coach_Dark | Prompt_Audios、Recordings、References 都是 JSON，整張卡由 JS 畫出；評語 `comment` 是 HTML |
| Speaking_Trilingual_Dark | 同上，再分成 `_ZH/_JA/_EN` |
| Expression_Master_Dark | Context、Detailed_Explanation 是 JSON；Error_Comparison 含 `<u>` 標記 |
| Expression_Micro_Dark | Phrase_Audios 是 JSON |
| JP_CoreVerb / JP_VerbPair Master | 單字是 `絡[から]まる` 格式，要 `{{furigana:}}` 才會變成 ruby；`*_Data_JSON` 由 JS 畫出 |
| JP_*_Cloze_Dark | `Full_Sentence_HTML` 含標記；Verb_Analysis_JSON 中的振假名由模板自己的 regex 轉換 |
| JP_Context_Dark | Dialog_JSON 是 LINE 式對話。實際資料還有舊的模型名 `JP_VerbPair_Context_Dark`，沒有任何 handler 認得 |
| AI_QA、Notion_SRS、Shadowing_Breakdown、Contrast_Coach、Conversation_Coach、Voice_Shadowing | 沒有 handler，網頁到不了；JSON 部分也靠 JS |

### 3.3 建議做法

1. **新增通用的瀏覽端點，不經過 handler。** 例如 `GET /api/v1/cards?deck=日本語::外語糾錯&page=1`：
   - 以 `findCards('deck:"…"')` 查詢，Anki 的 deck 搜尋會包含子牌組，「選一層時含子牌組」不用另外處理。
   - 再用 `cardsInfo` 取回 `cardId、noteId、deckName、modelName、fields、question、answer`。
   - 牌組名要跳脫 `"`、`_`、`*`（目前各 handler 都沒有跳脫）。
   - 分頁。
2. **牌組樹在前端組。** 把 `/handlers/decks` 的名稱以 `::` 切開，組成可展開的樹；選一層就查那一層。
3. **卡面用 Anki 自己渲染的 HTML 顯示。** 放進 `<iframe sandbox="allow-scripts" srcdoc=…>`，內容是 `cardsInfo` 的 `answer`（正面、背面可切換）：
   - 17 種筆記類型一次涵蓋，ruby 與 JS 畫出的 JSON 區塊都保留。
   - 注入 `<base href="/api/v1/anki-media/">`，並新增媒體代理端點（後端用 `retrieveMediaFile` 取檔），`<audio>`、`<img>` 的檔名就能載入。
   - 模板裡打本機 AnkiConnect（`127.0.0.1:8765`）的請求在 sandbox 中會失敗，不影響顯示；TG 深連結按鈕則可以點。
4. **編輯改成依 `modelName` 找負責的 handler**（`/handlers/` 回傳的 `supported_models`），不再寫死 `vocabulary_mining`。欄位編輯器先只支援純文字欄位，JSON 欄位交給 §4 的專用操作。
5. **驗收：** 每種筆記類型至少找一張實際的卡截圖。要在有真實 Anki 資料的環境做；本 worktree 的測試環境沒有連 Anki。

---

## 4. 分階段計畫

每個階段都接在 `feat/llm-model-routing` 上，可以單獨驗收；每階段結束都要通過下列檢查，並在瀏覽器實際走過該階段的每個流程：

```bash
cd frontend && npm run lint && npm test && npm run build
```

```bash
cd backend && python -m pytest tests -q
```

### 階段 A：卡片瀏覽（先做）

- 後端：`GET /cards`（§3.3 第 1 點）、`GET /anki-media/{filename}`（媒體代理，只允許單一檔名、不允許路徑）。卡片詳情的回傳格式改成前端看得懂的形狀，或前端改型別，二選一（見 §5 決策 3）。
- 前端：新增「Cards」頁，左邊是牌組樹，右邊是卡片清單與 iframe 卡面；修好 `CardDetailModal`。知識圖譜維持原狀。
- 測試：牌組樹組裝與牌組名跳脫的單元測試；後端端點測試（mock AnkiConnect）。
- **不碰 TG handler。**

### 階段 B：小型管理功能

- 網頁加「同步關聯」按鈕（用既有的 `POST /relations/sync`）。
- ~~`/setconfig`~~：✅ 已由能力路由完成（`docs/archive/capability_routing_FEAT_2026-10-19.md`，PR #25）：評分策略與模型搬進能力路由的資料表，舊的暫時設定移除，TG `/models` 改名為 `/setconfig`，網頁的 LLM 模型設定頁擴充成工具設定頁。
- 刪除 JSON 條目：抽出 service 函式（含三語欄位白名單、`last` 索引、`AnkiJsonFieldManager`、寫入前後同步）；新增 `DELETE /cards/{note_id}/fields/{field}/{index}`，TG 的 `_handle_delete_entry` 改呼叫它。
- 前端：卡片頁對 Recordings、References、Prompt_Audios 每一筆提供刪除。（原本的「設定頁（MODIFY_* 清單與修改）」已由工具設定頁取代。）
- `execute_update` 的 `ValueError` 改成 `FluencyTidesError` 子類、回 4xx（§5 決策 5）。

### 階段 C：錄音評分（口說核心）

- 抽出 `RecordingService.evaluate_and_save(note_id, field, audio_bytes, filename)`，把 `voice.py` 的步驟 2～4 移進去（同步、讀取、三語分支、**取得評分器**、存媒體、評分、寫回、錯誤對應）。TG 的 `process_voice_handler` 改呼叫它。
- **評分器已改由能力路由提供**（R2）：依卡片的 handler 呼叫 `services/recording_eval.py` 的 `evaluator_for_handler()`，建不出來時拋 `AudioEvaluatorUnavailableError`（503）；不再有 `app.state.audio_evaluator`，也不需要 `get_audio_evaluator`。評分器要在存媒體之前取得（TG 目前就是這個順序），設定有問題時不留下沒有紀錄引用的音檔。
- 新增 `POST /cards/{note_id}/recordings`（multipart）。
- **音檔格式：** 瀏覽器 `MediaRecorder` 錄出 webm/opus（Chrome）或 mp4（Safari），但評分器假設 `.ogg`。後端用 ffmpeg 轉成 ogg/opus（`FfmpegClient` 目前只有 `convert_to_mp3`，要補一個方法）。
- 前端：卡片頁上的錄音按鈕（錄音、試聽、送出），顯示分數、逐字稿、評語。請求要有長逾時，STT 加 LLM 可能要幾十秒。
- 測試：service 層以假評分器測試三語分支與寫回；端點測試；TG 的既有測試要全過。

### 階段 D：上傳音檔與頭像

- 抽出上傳音檔的 service（放置規則：一般 Prompt_Audios 依索引插入、References 加到 `items[idx].audios`、三語 Prompt_Audios 以語言碼取代），TG 的 `callbacks.py` 與 `messages.py` 都改呼叫它。這也會修好既有 bug：自訂頭像的路徑一律用 `speaking_coach`，三語卡會出錯（`messages.py:102`）。
- 新增 `GET /avatars`、`POST /avatars`（multipart）、`POST /cards/{note_id}/audio`（multipart：欄位、索引、頭像、說話者）。網頁可以接受 mp3 等檔案，TG 目前只收語音訊息。

### 階段 E：建卡流程

- 對話卡：`SpeakingCoachHandler.execute_create` 補上 `references[]` 與語言正規化，對齊 TG 的行為（Card_ID 格式要決定用哪一種，見 §5 決策 4），`speaking_fsm.py` 改呼叫它。網頁做成一頁表單。
- 外語糾錯：把重複查詢與刪除母卡群組移進 `ExpressionCorrectionHandler`，新增預覽端點（回傳 LLM 結果與 `duplicate`），create 支援覆蓋決策（先建後刪）。`expression_fsm.py` 改呼叫這些函式。網頁做成「輸入 → 預覽 → 勾選子卡 → 寫入」三步。
- 單字卡：沿用既有 CardGenerator。

---

## 5. 需要確認的決策

1. **（已由能力路由計畫取代）** `/config` 不再新增寫入端點；設定的寫入走能力路由的 API（`/api/v1/capability-routes`，掛 `verify_api_key`）。`GET /config` 仍是診斷用的唯讀端點，目前沒有認證，是否補上另議。
2. **卡面用 iframe 顯示 Anki 的渲染結果，還是逐一寫 React 渲染器。** **建議：** 先用 iframe，一次涵蓋所有類型；需要互動的部分（錄音、刪除條目、上傳音檔）放在 iframe 外的 React 操作面板，不去改模板。
3. **卡片詳情 API 的格式。** 改後端會影響 `/handlers/cards/{id}` 的其他使用者（腳本），改前端只動網頁。**建議：** 前端改型別，後端不動。
4. **對話卡的 Card_ID 格式。** TG 用 `SC_%Y%m%d_%H%M%S`，handler 用 uuid4。**建議：** 統一用 handler 的 uuid4（同一秒建兩張不會撞）。
5. **`execute_update` 的 `ValueError` 目前會變成 500，並發 TG 當機警報。** 網頁會更容易觸發，例如索引超出範圍。**建議：** 在階段 B 改成丟 `FluencyTidesError` 的子類，回 4xx。

---

## 6. 盤點時發現的既有問題（不在本計畫範圍）

- `backend/tests/test_jp_verb_reading_judgments.py:231` 的斷言寫在 `with mock.patch…` 區塊之外，比對的是開發者 `.env` 的 `LLM_MODEL_NAME`。在沒有 `backend/.env` 的環境（CI、worktree）會失敗：本 worktree 是 809 passed、1 failed。把斷言移進 `with` 區塊即可。
- `vocabulary_mining_handler.py:108-112` 寫入的 `Meaning`/`Context` 在 TOEIC_Coach_Dark 上不存在。
- 各 handler 的 Anki 查詢沒有跳脫牌組名。
- `npm run build` 的 `tsc -b` 會改寫已追蹤的 `vite.config.js`、`vite.config.d.ts`。建議把它們從版本控制移除，或在 tsconfig 設定不輸出。
- LLM 模型設定頁的 503 有兩種情況共用同一個 `error_code`：寫入前讀不到 DB（確定沒寫入），以及寫入時異常（結果未知）。網頁只能用不斷言的措辭。若後端拆成兩個 code，網頁就能說得更準確。
