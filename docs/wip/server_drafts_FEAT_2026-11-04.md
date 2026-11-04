# FEAT — 建卡草稿存在後端：生成改為背景工作，草稿只在「建立」或「廢棄」時消失

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-04 |
| **性質** | 新增機能設計 + 實作工作項 |
| **狀態** | 📝 草案，待使用者確認（§10 待決問題） |
| **範圍** | 後端：預覽流程的草稿改為背景生成並存在網站資料庫（新表 `card_drafts`、新的 `/drafts` 端點、建卡帶 `draft_id`）。前端：`components/generate/PreviewCreate.tsx` 改讀寫後端草稿、全站的生成完成通知、填表內容改存 localStorage |
| **不動** | LLM 呼叫本身（提示詞、模型路由、`execute_draft` 的輸出形狀）；建卡的寫入流程（`execute_create`、同步 AnkiWeb）；TG bot；卡片瀏覽頁與 Card Generator 的版面 |
| **PR / 進度** | 分支 `feat/server-drafts`；只有計畫書，尚未實作 |
| **關聯文件** | `docs/archive/trilingual_web_create_FEAT_2026-10-30.md`（預覽流程；當時決定「草稿不存在後端」，本計畫取代該項）；`docs/wip/card_list_create_FEAT_2026-10-30.md`（兩頁共用 `CreatePanelBody`）；`docs/wip/data_stores_AUDIT_2026-11-01.md`（網站 SQLite S3、S4）；`docs/wip/backlog.md` W2（外語糾錯網頁建卡會沿用預覽流程） |

---

## 1. 問題與動機

使用者的要求（2026-11-04）：**前端怎麼操作，資料都不會遺失；生成完成後，畫面一直留著，直到使用者按「建立」或「廢棄」。**

### 1.1 現況（已對照 `main` `4b99f04`）

- `POST /handlers/{name}/draft` 是同步請求：後端呼叫 LLM（`NoteService.draft` → `handler.execute_draft`），等 LLM 回來才回應。網頁顯示「通常要半分鐘到一分鐘」。
- 結果只存在瀏覽器：`PreviewCreate.tsx` 的 `useMutation` 在 `onSuccess` 把草稿寫進 sessionStorage（`generate_draft_v2:{handler}`），勾選、修改過的情境與標籤也存在同一個鍵。生成前的輸入在 `generate_form:{handler}`（sessionStorage）。
- 「生成中」是靠 TanStack Query 的 `useIsMutating(['draft', handler])`，只存在這一頁的 JavaScript 記憶體裡。
- 正式站經 Cloudflare Tunnel 與 Cloudflare Access 對外。後端是單一 uvicorn process（`backend/Dockerfile` 的 `CMD`，沒有 `--workers`）。
- LLM 的逾時設定：`LLM_CLAUDE_CODE_TIMEOUT_SECONDS`、`LLM_ANTIGRAVITY_TIMEOUT_SECONDS` 預設 900 秒；設定說明寫 CLI 內建的結構化重試最壞約 190 秒。

### 1.2 會遺失的情況

2026-11-03 以 Playwright + 假資料實測，加上對程式的推論：

| # | 情況 | 現在 | 原因 |
|---|---|---|---|
| L1 | 生成中重新整理、關掉分頁 | ❌ 結果遺失，回來也不顯示「生成中」，只剩輸入（實測） | 請求隨頁面中止；結果只會寫進發出請求的那一頁 |
| L2 | 生成中切到別的 App（iPhone） | ❌ 很可能遺失（推論，未實機驗證） | iOS 會暫停或終止背景的 Safari 分頁，請求斷掉的情況同 L1 |
| L3 | LLM 超過約 100 秒才回來 | ❌ 遺失，網頁顯示「無法生成」，但後端其實生成完了（推論，§8 R1 要實際確認） | Cloudflare 對單一 HTTP 回應的等待上限約 100 秒，逾時回 524；CLI 重試最壞約 190 秒 |
| L4 | 生成完成後關掉分頁、開新分頁 | ❌ 看不到（實測） | sessionStorage 每個分頁各自一份 |
| L5 | 換一台裝置（手機生成、電腦建立） | ❌ 看不到 | 草稿只在瀏覽器 |
| L6 | 部署時剛好在生成 | ❌ 遺失 | 後端重啟，請求中斷（每次合併到 main 都會部署） |
| L7 | 生成中切到 Card Generator 或卡片瀏覽頁 | ✅ 保留（實測） | 同一個分頁的 SPA，mutation 照樣完成 |
| L8 | 生成完成後重新整理 | ✅ 保留（實測） | sessionStorage 在同一個分頁內還在 |
| L9 | 草稿只能用「建立」或「廢棄」結束 | ⚠️ 預覽畫面還有「清除」，會直接刪掉草稿；「廢棄」沒有確認 | — |

L1～L6 只改前端都解決不了：結果還沒回到瀏覽器時，頁面就沒了。要做到「怎麼操作都不遺失」，生成必須在後端跑完並存下來，網頁只是去讀它。

## 2. 目標與非目標

**目標**

- G1 按「生成」後，生成在後端背景執行，結果存在後端。之後網頁重新整理、關掉分頁、手機切到別的 App、換裝置、Cloudflare 斷線，都不影響結果。
- G2 打開建卡畫面（Card Generator 或卡片瀏覽頁的建卡面板）時，向後端讀這種卡片的草稿：生成中顯示「生成中」，完成了顯示預覽，失敗了顯示原因與「重試」。
- G3 預覽中的修改（勾選、卡面情境、補充、標籤）自動存回後端，換裝置也接得下去。
- G4 草稿只在兩種情況消失：「建立」成功，或使用者確認「廢棄」。沒有自動過期。
- G5 部署或後端重啟時正在生成的草稿，回來顯示「生成被中斷」與「重試」。重試沿用當時的輸入，不必重填。
- G6 生成完成時，使用者在網站的任何一頁都看得到通知。
- G7 生成前填到一半的輸入、填表流程（一般卡片）填到一半的內容，關掉分頁後還在（同一台裝置、同一個瀏覽器）。

**非目標**

- 改 LLM 的提示詞、模型或輸出。
- 「重新生成」一份已經成功的草稿：預覽流程計畫書決定不提供，維持不變。失敗後的「重試」不受這條限制。
- 生成完成時用 TG 通知（§10 Q3）。
- 填表流程的內容存到後端：一般卡片沒有 LLM 等待，只改存 localStorage（G7）。
- 多人、權限：這是個人工具，草稿不分使用者。

## 3. 設計決策

### D1 草稿存在網站資料庫的新表

- 存在現有的網站 SQLite（本機 `backend/fluencytides.db`、正式機 `/app/data/fluencytides.db`），新表 `card_drafts`（§5）。以 SQLModel 定義，並附 Alembic 遷移；`create_db_and_tables()` 啟動時也會補建新表，與 `capability_routes` 相同的做法。
- 不放 MySQL `fluencytides_corpus`：那是語料庫，正在重建中（`corpus_db_rebuild_FEAT`）；草稿是網站自己的狀態，跟 `capability_routes` 同類。
- 放棄的做法：存檔案或 Redis。專案沒有 Redis；檔案要自己處理並發與原子寫入，SQLite 已經有了。

### D2 生成改為背景工作

- `POST /handlers/{name}/drafts` 驗證輸入後，**先**在資料庫建一筆 `generating` 的草稿，再在背景啟動生成，立刻回 202 與草稿內容。請求在一秒內結束，不受 Cloudflare 100 秒限制。
- 背景工作由 `DraftRunner` 管理：`asyncio.create_task` 執行 `NoteService` 現有的草稿生成（查既有標籤 → `execute_draft` → 整理建議標籤），完成後在**新的 DB session** 寫回 `ready` 與結果，失敗寫回 `failed` 與錯誤（`error_code`、`message`，與現在 502、422 的內容相同）。
  - Runner 持有 task 的參照（`dict[draft_id, Task]`），避免 task 被回收；task 結束時移除。
  - 現在是單一 uvicorn process，所以 in-process 背景工作可行。日後若改成多個 worker，要改成工作佇列（§8 R4）。
- 放棄的做法：
  - FastAPI `BackgroundTasks`：在回應送出後才跑，但取不到 task 參照，不能取消（D7 廢棄生成中的草稿要取消）。
  - 前端改長輪詢或延長逾時：解決不了 L1、L2、L4～L6。

### D3 狀態與轉換

```
              POST /drafts                  完成                 建立成功（同一個請求刪除）
 （沒有草稿）──────────────► generating ──────────► ready ───────────────────────► （沒有草稿）
                                 │                   │
                                 │ LLM 失敗、         │ 使用者確認「廢棄」
                                 │ 後端重啟中斷        ▼
                                 ▼               （沒有草稿）
                              failed ── 重試 ──► generating
                                 │
                                 └── 廢棄 ──► （沒有草稿）
```

- `generating`：生成中。網頁顯示「生成中」與開始時間；可以「廢棄」（取消生成）。
- `ready`：可預覽、修改、建立、廢棄。
- `failed`：顯示原因；可以「重試」（同樣的輸入）、「改輸入後重試」（回到輸入畫面，帶著原本的輸入）、「廢棄」。
- 重啟中斷：啟動時把所有 `generating` 改成 `failed`，`error_code=DRAFT_INTERRUPTED`，訊息「生成時伺服器重新啟動，生成中斷了」。關機（lifespan 的 shutdown）時也取消進行中的 task 並寫回同樣的狀態，啟動時的修正是保險。

### D4 每種卡片同一時間只有一份草稿

- 同一個 handler 已有草稿（不論狀態）時，再按「生成」回 409 `DRAFT_EXISTS`，回應附上既有草稿。網頁本來就是這樣：有草稿時顯示預覽，不顯示輸入畫面，所以實際上不會發生；這條是防兩台裝置同時按。
- 以 handler 為單位的 `asyncio.Lock` 保證「檢查 → 建立」不會被並發的請求穿插（單一 process 足夠；§8 R4）。
- 放棄的做法：同一種卡片可以有多份草稿並列，例如一次生成三個場景，再逐一建立。會需要草稿清單畫面；先不做，見 §10 Q1。

### D5 預覽中的修改自動存回後端

- 草稿分成兩部分：
  - `result`：後端產生、之後不再改的生成結果（`params` 與 `suggested_tags`，即現在 `DraftResponse` 的內容）。
  - `state`：網頁的編輯狀態（勾選、卡面情境、補充、標籤），也就是現在 sessionStorage 裡的 `DraftState`。後端只當成 JSON 存取、檢查大小（上限 256 KB），不解讀內容。建立時網頁照舊把勾選結果組成建立參數送出（`selectedParams`），後端照舊以 `CREATE_PARAMS` 驗證。
- 網頁在使用者修改後 800 ms 送 `PATCH /drafts/{id}`，帶 `version`（樂觀鎖）。後端在 `version` 相符時寫入並加一；不符時回 409 `DRAFT_VERSION_CONFLICT` 與最新的草稿。
- 衝突（另一台裝置也在改）：網頁顯示「這份草稿在另一個裝置上改過了」，提供「載入最新」與「用這裡的覆蓋」（§10 Q2）。
- 存檔失敗（網路斷了）：編輯狀態先存一份在 localStorage（`draft_state_backup:{draft_id}`），預覽畫面顯示「尚未儲存」；恢復連線後自動重送，成功後刪除備份。這樣在「還沒存回後端就關掉分頁」的情況下，同一台裝置回來也不會遺失。
- 預覽畫面顯示儲存狀態：「已儲存」、「儲存中…」、「尚未儲存（離線）」。

### D6 建立時帶 `draft_id`，成功後由後端刪除草稿

- `POST /handlers/{name}/notes` 加選填的 `draft_id`。寫入 Anki 成功後，在同一個請求刪除草稿。網頁不必再另外呼叫刪除，不會出現「卡片建了、草稿還在，又建一次」。
- 刪除失敗（例如資料庫被鎖）只記 log，回應照常是 201，另附 `draft_warning`。卡片已經建了，不能讓建卡失敗。
- 建立失敗（422、Anki 錯誤）時草稿不動，網頁照舊顯示錯誤，草稿還在。
- 回應在路上遺失（建立成功但網頁沒收到）：重新整理後草稿已經不在，網頁顯示「這份草稿已不在（可能已建立，或在其他裝置廢棄）」，附上到卡片瀏覽頁的連結。個人工具，這個程度可以接受（§8 R5）。

### D7 「廢棄」要確認；預覽畫面拿掉「清除」

- 「廢棄」改成先確認（對話框：「廢棄這份草稿？生成的 N 句會一起刪除，無法復原。」），確認後 `DELETE /drafts/{id}`。生成中的草稿也能廢棄：取消背景 task，不寫回結果。
- 預覽畫面的「清除」拿掉，它的效果與「廢棄」重疊，卻沒有確認。輸入畫面的「清除」保留，只清輸入。
- 符合使用者要求：草稿只會因「建立」或「廢棄」消失。

### D8 網頁讀取草稿與通知

- 建卡畫面打開、選了某種預覽流程的卡片時：`GET /drafts?handler={name}` 取這種卡片的草稿（D4 保證最多一份）。
  - `generating`：每 3 秒重新讀取一次（TanStack Query `refetchInterval`），完成後停止。
  - 分頁回到前景（`visibilitychange`）時立刻讀一次：手機從別的 App 回來會馬上看到結果。
- 全站通知：版面層級掛一個 `DraftWatcher`。只在已知有 `generating` 的草稿時，每 10 秒讀 `GET /drafts?status=generating`；某份草稿從 `generating` 變成 `ready` 或 `failed` 時跳 toast（「三語草稿已生成」附「前往」按鈕，或「生成失敗」）。取代現在 mutation `onSuccess` 的 toast。
- 不用 SSE、WebSocket：Cloudflare Access 加 Tunnel 的環境下，輪詢最單純也最不容易出錯；草稿數量極少，負擔可以忽略。
- 放棄的做法：只在建卡畫面輪詢。使用者在別頁時就收不到完成通知（G6）。

### D9 生成前的輸入與填表內容改存 localStorage

- `generate_form:{handler}`、`generate_tags:{handler}` 從 sessionStorage 改成 localStorage：關掉分頁再開還在，同一個瀏覽器的分頁共用（`useLocalStorage` 已會跟著其他分頁同步）。
- 按「生成」後，輸入存在後端草稿的 `input`。生成成功後清掉本機的輸入，與現在相同；失敗時保留，「改輸入後重試」直接帶回。
- 卡片瀏覽頁的成功提示（`generate_created:{handler}`）維持 sessionStorage：它是這一次操作的提示，不是資料。

### D10 上線時接手舊的本機草稿

- 上線後第一次打開建卡畫面時，若 sessionStorage 有舊的 `generate_draft_v2:{handler}`，而後端沒有這種卡片的草稿：網頁以 `POST /handlers/{name}/drafts/import`（`result` 與 `state`）建成一份 `ready` 草稿，成功後刪除本機的舊鍵。
- 後端已經有草稿時，不覆蓋，本機的舊草稿保留在 sessionStorage，並提示「這個分頁還有一份舊草稿」，可以「改用舊草稿」或「刪除舊草稿」。
- 這段相容程式在下一個版本移除（backlog 記一筆）。

### D11 舊端點保留一版

- `POST /handlers/{name}/draft`（同步）保留到下一個版本再移除。CI 在同一次部署中先後部署後端與前端，中間有短暫空窗，保留可以避免舊前端在這段時間打到不存在的端點。標記為 deprecated，backlog 記一筆。

### D12 適用所有預覽流程的卡片

- 依 handler 的 `DRAFT_PARAMS` 與 `capabilities.create.flow == "preview"` 判斷，不寫死三語卡。目前只有三語對話卡；backlog W2（外語糾錯網頁建卡）之後直接沿用。

## 4. API

前綴 `/api/v1`，全部需要 `X-API-Key`（與現在的 handlers 路由相同）。錯誤一律是 `{"error_code", "message"}`。

### 4.1 草稿物件

```json
{
  "id": "5f0c…",
  "handler": "speaking_trilingual",
  "status": "generating | ready | failed",
  "input": { "background": "…", "intent": "…", "audience": "…", "audience_note": "…" },
  "result": { "params": { … }, "suggested_tags": [ { "tag": "Workplace", "existing": true } ] },
  "state": { … },
  "error": { "error_code": "LLM_SERVICE_ERROR", "message": "…" },
  "version": 3,
  "created_at": "2026-11-04T09:00:00Z",
  "updated_at": "2026-11-04T09:00:42Z",
  "generation_started_at": "2026-11-04T09:00:00Z"
}
```

- `result` 只有 `ready` 時有值；`error` 只有 `failed` 時有值；`state` 在網頁第一次存檔前是 `null`。

### 4.2 端點

| 方法與路徑 | 用途 | 成功 | 主要錯誤 |
|---|---|---|---|
| `POST /handlers/{name}/drafts` | 開始生成（body：`{params}`，即現在的草稿輸入） | 202 草稿 | 404 handler 不存在；400 `HANDLER_OPERATION_UNSUPPORTED`（沒有預覽流程）；409 `DRAFT_EXISTS`（附既有草稿）；422 輸入驗證失敗 |
| `GET /drafts` | 列出草稿（query：`handler`、`status`） | 200 陣列 | — |
| `GET /drafts/{id}` | 讀一份 | 200 草稿 | 404 `DRAFT_NOT_FOUND` |
| `PATCH /drafts/{id}` | 存編輯狀態（body：`{state, version}`） | 200 草稿 | 404；409 `DRAFT_VERSION_CONFLICT`（附最新草稿）；409 `DRAFT_NOT_READY`；413 `state` 太大 |
| `POST /drafts/{id}/retry` | 失敗後重試（body 選填：`{params}`，給了就換成新的輸入） | 202 草稿 | 404；409 `DRAFT_NOT_FAILED`；422 |
| `DELETE /drafts/{id}` | 廢棄（生成中則取消） | 204 | 404 |
| `POST /handlers/{name}/drafts/import` | D10：把舊的本機草稿建成 `ready` 草稿（body：`{result, state}`） | 201 草稿 | 409 `DRAFT_EXISTS`；422 |
| `POST /handlers/{name}/notes` | 既有的建卡；body 加選填 `draft_id` | 201，另附 `draft_warning`（刪草稿失敗時） | 既有的錯誤；`draft_id` 不存在時不報錯（可能已在別處刪除），只記 log |

- `HANDLER_OPERATION_UNSUPPORTED`（400）與現在 `POST /handlers/{name}/draft` 的錯誤相同。
- 草稿的 `error` 沿用生成失敗時現有的錯誤碼，例如 `LLM_SERVICE_ERROR`（現在回 502）、`LLM_GENERATION_FAILED`（現在回 422）；背景生成沒有 HTTP 回應，只把錯誤碼與訊息存進草稿。

## 5. 資料表 `card_drafts`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | TEXT PK | UUID4 hex |
| `handler_name` | TEXT，索引 | handler 名稱 |
| `status` | TEXT | `generating`、`ready`、`failed` |
| `input_json` | TEXT | 生成的輸入（`DRAFT_PARAMS` 驗證後） |
| `result_json` | TEXT NULL | 生成結果 |
| `state_json` | TEXT NULL | 網頁的編輯狀態（上限 256 KB） |
| `error_code` | TEXT NULL | 失敗時 |
| `error_message` | TEXT NULL | 失敗時 |
| `version` | INTEGER | 樂觀鎖，每次寫入加一 |
| `created_at`、`updated_at`、`generation_started_at` | DATETIME | UTC |

- 約束與索引名稱照 `conventions.py` 的命名規則；Alembic 遷移與 `capability_routes`（`c3a9e5d1f7b2`）同一種寫法。
- 正式機的 SQLite 在 volume 裡，部署後由 `create_db_and_tables()` 建表；同時要在正式機跑 `alembic upgrade head`，維持遷移紀錄一致（§9 驗收項目）。
- `data_stores_AUDIT` 的 S3、S4 補上這張表。

## 6. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `app/infrastructure/database/models.py` | 新增 `CardDraft` |
| `alembic/versions/<rev>_card_drafts.py`（新） | 建表 |
| `app/schemas/drafts.py`（新） | `DraftOut`、`StartDraftRequest`、`UpdateDraftRequest`、`RetryDraftRequest`、`ImportDraftRequest` |
| `app/services/draft_service.py`（新） | `DraftService`（CRUD、狀態轉換、版本檢查、handler 鎖）與 `DraftRunner`（背景 task、取消、關機與啟動的中斷處理） |
| `app/services/note_service.py` | 把現在 `draft()` 的「驗證輸入」與「生成」拆開，讓 runner 呼叫生成那一段；`create()` 接受 `draft_id`，成功後刪除草稿 |
| `app/api/drafts.py`（新）、`app/api/handlers.py` | 新端點；舊 `POST /draft` 標記 deprecated |
| `app/core/exceptions/…`、全域 handler | `DraftNotFoundError`、`DraftExistsError`、`DraftVersionConflictError`、`DraftNotReadyError`、`DraftNotFailedError` 對應的錯誤碼與狀態碼 |
| `app/core/dependencies.py` | 注入 `DraftService` |
| `app/main.py` | lifespan：啟動時把 `generating` 標為中斷；關機時取消 task 並寫回 |

### Frontend

| 檔案 | 改動 |
|---|---|
| `src/api/client.ts`、`src/types/api.ts` | 草稿端點與型別；`createNote` 加 `draft_id` |
| `src/hooks/useServerDraft.ts`（新） | 讀取、輪詢、回到前景時重讀、自動存檔（防抖、版本、離線備份與重送） |
| `src/components/generate/PreviewCreate.tsx` | 改用 `useServerDraft`：生成中、失敗（重試、改輸入後重試）、預覽、儲存狀態、衝突處理；「廢棄」加確認；預覽畫面拿掉「清除」 |
| `src/components/shared/DraftWatcher.tsx`（新）、`src/App.tsx` | 全站完成通知 |
| `src/components/generate/CreatePanelBody.tsx` | 填表內容改用 localStorage（D9） |
| `src/lib/legacyDraftImport.ts`（新） | D10 的接手舊草稿 |

### 測試

- 後端（pytest）：狀態轉換（生成成功、LLM 失敗、取消、重試）、啟動時的中斷處理、`DRAFT_EXISTS` 與並發建立、版本衝突、`state` 大小上限、建卡帶 `draft_id` 時成功刪除與失敗保留、import。LLM 以假的 handler 取代。
- 前端（vitest）：自動存檔的防抖與版本處理、離線備份與重送、舊草稿接手的判斷（純函式部分）。
- 瀏覽器（Playwright + 假 API）：§9 的流程項目。

## 7. 實作順序

- **P1 後端資料層**：`CardDraft`、遷移、`DraftService`（不含背景工作）與測試。
- **P2 背景生成**：`DraftRunner`、lifespan 的中斷處理、`POST /drafts`、`retry`、`DELETE` 的取消，以及測試。
- **P3 其餘端點**：`GET`、`PATCH`（版本）、`import`、建卡的 `draft_id`。舊端點標 deprecated。
- **P4 前端草稿**：`useServerDraft`、`PreviewCreate` 改寫、廢棄確認、儲存狀態與衝突。
- **P5 前端其餘**：`DraftWatcher`、localStorage（D9）、舊草稿接手（D10）。
- **P6 驗證**：測試、lint、build；瀏覽器實測；部署後在正式站實測 §9（含手機）。

P1～P3 可以先合併（後端向下相容，舊前端照樣用舊端點），P4、P5 再一個 PR。

## 8. 風險與未知

- **R1 Cloudflare 的逾時**：「約 100 秒」是 Cloudflare 一般方案的已知限制，本專案的正式站還沒實際確認過。實作前在正式機的 log 查有沒有超過 100 秒的 `/draft` 請求，以及前端是否收過 524，用來確認 L3 確實發生過。無論結果如何，D2 都解決這個問題。
- **R2 取消 LLM 呼叫**：claude-code、antigravity 用子程序呼叫 CLI。task 被取消時，子程序要一併終止，否則會在背景跑完、白花費用。實作時確認各 LLM client 對 `CancelledError` 的處理，必要時補上終止子程序。
- **R3 關機時間**：部署時容器收到 SIGTERM。lifespan 的 shutdown 要在幾秒內完成（取消 task、寫回 `failed`），否則被強制終止。即使來不及寫回，啟動時的修正也會補上（D3）。
- **R4 單一 process 的前提**：背景工作與 handler 鎖都依賴單一 uvicorn process。日後若加 `--workers`，要改成資料庫層級的唯一約束與工作佇列。在 `Dockerfile` 的 `CMD` 旁加註解提醒。
- **R5 建卡回應遺失**：D6 的情況下，網頁只知道「草稿不在了」，不知道卡片建了沒有。要更完整，可以讓後端在刪除前把 `note_ids` 留在一筆「已建立」紀錄裡保留一天；目前判斷不值得，先不做。
- **R6 SQLite 並發寫入**：背景 task 與 API 請求同時寫入，可能遇到短暫的 `database is locked`。草稿寫入量極少；寫入時重試一次，並設定 `busy_timeout`。
- **R7 費用**：重試會再呼叫一次 LLM。只有失敗時才能重試，與現在「失敗後再按一次」相同。
- **R8 舊草稿接手**：只處理「開著舊分頁、上線後重新整理」的情況；舊分頁沒有重新整理就按建立，打的是舊的建卡請求（不帶 `draft_id`），照樣成功。

## 9. 驗收標準

- [ ] 生成中重新整理、關掉分頁再開：回來顯示「生成中」，完成後顯示預覽（L1）
- [ ] iPhone：按生成後切到別的 App 一分鐘以上再回來，結果在（L2，實機）
- [ ] 生成超過 100 秒（以假 LLM 延遲測試）：網頁不出錯，完成後顯示預覽（L3）
- [ ] 生成完成後關掉分頁、開新分頁：預覽還在，含勾選與修改（L4）
- [ ] 手機生成、在電腦打開建卡畫面：看得到同一份草稿並能建立（L5）
- [ ] 生成中重啟後端：回來顯示「生成被中斷」，「重試」沿用原本的輸入（L6、G5）
- [ ] 預覽中修改後立刻關掉分頁：回來時修改還在；離線時顯示「尚未儲存」，恢復連線後自動存好
- [ ] 兩個分頁同時修改同一份草稿：後存的一方看到衝突提示，可以選擇載入最新或覆蓋
- [ ] 「廢棄」要確認；預覽畫面沒有「清除」；草稿只因「建立」成功或「廢棄」消失
- [ ] 建立成功後草稿從後端消失；建立失敗時草稿還在
- [ ] 在別的頁面等生成：完成時跳通知，按「前往」回到建卡畫面
- [ ] 上線前留在 sessionStorage 的舊草稿，上線後重新整理會接手成後端草稿
- [ ] 生成前填到一半的輸入、一般卡片填到一半的內容，關掉分頁再開還在
- [ ] 正式機跑過 `alembic upgrade head`，`card_drafts` 存在且遷移紀錄一致
- [ ] 後端測試、前端測試、lint、build 通過：

```bash
cd backend
python -m pytest tests -q
```

```bash
cd backend
ruff check app scripts tests
```

```bash
cd frontend
npm test
```

```bash
cd frontend
npm run lint
```

```bash
cd frontend
npm run build
```

## 10. 待決問題（請使用者決定）

| # | 問題 | 建議 |
|---|---|---|
| Q1 | 同一種卡片要不要允許多份草稿並列（例如一次生成好幾個場景，再逐一建立）？ | 先一份（D4）。網頁本來就是一份，改多份需要草稿清單畫面；有需要再另案 |
| Q2 | 兩台裝置同時改同一份草稿時，要提示衝突，還是直接以後存的為準？ | 提示衝突（D5）。直接覆蓋可能無聲地丟掉另一邊的勾選 |
| Q3 | 生成完成時要不要也發 TG 通知？ | 這次不做。網頁的全站通知（D8）已涵蓋開著網站的情況；TG 通知可以之後加，後端已有發 TG 訊息的機制 |
| Q4 | 舊的同步端點 `POST /handlers/{name}/draft` 要保留一版，還是這次直接移除？ | 保留一版（D11），下一個 PR 移除 |
| Q5 | 生成中的草稿要不要能「廢棄」（取消 LLM 呼叫）？ | 要（D7），但取決於 R2 能不能確實終止子程序；做不到時改成「完成後自動丟掉結果」 |
