# FEAT — 建卡草稿存在後端記憶體：全站同一時間只有一份草稿，生成改為背景工作

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-04（2026-11-07 依使用者決定改寫：只存記憶體、全站一把鎖、TG 建卡移到網頁、腳本例外） |
| **性質** | 新增機能設計 + 實作工作項 |
| **狀態** | 📝 設計已確認（2026-11-07），尚未實作 |
| **範圍** | 後端：預覽流程的草稿改為背景生成、存在後端記憶體（新的 `CreateSession`）；全站「建卡工作階段」鎖，網頁的建卡入口都要先通過它；新的 `/drafts` 端點、建卡帶 `draft_id`。前端：`components/generate/PreviewCreate.tsx` 改讀寫後端草稿、全站的生成完成通知、生成中的按鈕狀態、填表內容改存 localStorage。最後兩個階段：網頁補上對話卡（單語）與外語糾錯的建卡；網頁驗收後，依使用者指示移除 TG 的建卡（D10） |
| **不動** | LLM 呼叫本身（提示詞、模型路由、`execute_draft` 的輸出形狀、`cli_base.py` 的子程序呼叫方式）；建卡的寫入流程（`execute_create`、同步 AnkiWeb）；本機批次腳本的整條路（`/verb-pair/generate-child-cards`、`/core-verb/generate-child-cards`、直接呼叫 AnkiConnect 的腳本）；資料庫（不新增資料表）；TG 的錄音評分、上傳音檔、刪除條目、人脈卡 `/p` 的編輯、`/sync`、`/setconfig`；卡片瀏覽頁與 Card Generator 的版面 |
| **PR / 進度** | 分支 `feat/server-drafts`；只有計畫書，尚未實作 |
| **關聯文件** | `docs/archive/trilingual_web_create_FEAT_2026-10-30.md`（預覽流程；當時決定「草稿不存在後端」，本計畫取代該項）；`docs/wip/card_list_create_FEAT_2026-10-30.md`（兩頁共用 `CreatePanelBody`）；`docs/wip/backlog.md` W1、W2（單語對話卡、外語糾錯的網頁建卡，併入本計畫 D10，完成後從 backlog 刪除） |

> **改寫說明（2026-11-07）**：第一版（2026-11-04）把草稿存在網站 SQLite 的新表 `card_drafts`，每種卡片各一份。討論後使用者決定改成最簡單的設計：
> - 草稿只存在後端記憶體，不新增資料表；後端重啟時草稿遺失，接受這個代價（§8 R1）。
> - 全站同一時間只有一份草稿，不分卡片類型；生成後必須「建立」或「廢棄」，才能再生成。
> - ~~這把鎖包含 TG~~：第三輪改為 TG 不再建卡（見下）。
> - 例外：本機 Python 腳本走的 API 不受限制。
>
> 第一版的資料表、Alembic 遷移、舊草稿接手（import）都拿掉。
>
> **2026-11-07 第二輪決定**：填表建卡也受鎖限制（原 Q1）；腳本例外不寫程式（原 Q2）；生成中不能廢棄，等結果回來（含逾時失敗）才能建立或廢棄（原 Q4）；同時修改時後到的提示衝突（原 Q5）；舊的即時回應端點直接移除（原 Q7）；部署造成的草稿遺失與舊草稿都不處理（原 Q8）。
>
> **2026-11-07 第三輪決定**：TG 不再建立新卡。TG 要做到網頁那樣好用的「指定目標牌組」難度很高，所以 TG 的 `/newcard` 整個移除，TG 支援的卡片類型改由網頁提供；網頁已經有的（單字卡、人脈卡）只刪 TG 端。鎖只管網頁。生成中「生成」「廢棄」按鈕停用，「清除」右邊顯示旋轉的圖示（D8）。
>
> **2026-11-07 第四輪決定**：TG 建卡的移除與網頁補齊併入本計畫，放在最後（D10）：網頁先補上對話卡與外語糾錯並部署，**使用者在網頁確認沒問題、明確指示後**，才移除 TG 的建卡。後端重啟後的「草稿已遺失」提示要做，id 存 localStorage（D11）。
>
> **2026-11-07 第五輪決定**（§10 全部照建議）：對話卡的參考回答用多行文字框、一行一筆；移除 TG 建卡時，`/p` 找不到人時的「建立新的人」也一起移除；外語糾錯的原文重複時，網頁照 TG 的三個選項（覆蓋舊卡、允許重複、取消）。

---

## 1. 問題與動機

使用者的要求（2026-11-04）：**前端怎麼操作，資料都不會遺失；生成完成後，畫面一直留著，直到使用者按「建立」或「廢棄」。**

### 1.1 現況（已對照 `main` `4b99f04`）

- `POST /handlers/{name}/draft` 是同步請求：後端呼叫 LLM（`NoteService.draft` → `handler.execute_draft`），等 LLM 回來才回應。網頁顯示「通常要半分鐘到一分鐘」。
- 結果只存在瀏覽器：`PreviewCreate.tsx` 的 `useMutation` 在 `onSuccess` 把草稿寫進 sessionStorage（`generate_draft_v2:{handler}`），勾選、修改過的情境與標籤也存在同一個鍵。生成前的輸入在 `generate_form:{handler}`（sessionStorage）。
- 「生成中」是靠 TanStack Query 的 `useIsMutating(['draft', handler])`，只存在這一頁的 JavaScript 記憶體裡。
- 正式站經 Cloudflare Tunnel 與 Cloudflare Access 對外。後端是單一 uvicorn process（`backend/Dockerfile` 的 `CMD`，沒有 `--workers`）；TG bot 在同一個 process 裡（`app/main.py` 的 lifespan 啟動），aiogram 的 FSM 狀態也存在記憶體（`Dispatcher()` 預設的 MemoryStorage）。
- LLM 的逾時設定：`LLM_CLAUDE_CODE_TIMEOUT_SECONDS`、`LLM_ANTIGRAVITY_TIMEOUT_SECONDS` 預設 900 秒；設定說明寫 CLI 內建的結構化重試最壞約 190 秒。
- claude-code、antigravity 以 `asyncio.to_thread` + 同步 `subprocess.run` 呼叫 CLI（`infrastructure/llm/cli_base.py:486-496`）；這是 2026-08-27 計畫 D5 的約束（Windows 的 selector event loop 不支援 `create_subprocess_exec`）。取消 asyncio task 不會終止子程序。
- 建卡的入口：
  - 網頁：`POST /handlers/{name}/notes` → `NoteService.create`。
  - TG：`vocabulary_fsm.py:142`、`expression_fsm.py:913`、`person_profile.py:1239` 經 `NoteService.create`；`speaking_fsm.py:437` **直接** `anki_client.add_note`，不經服務層。TG 的外語糾錯在 `expression_fsm.py:623` 呼叫 `execute_generate`（LLM），結果存在 FSM 狀態裡等使用者勾選。
  - 本機腳本：`/verb-pair/generate-child-cards`、`/core-verb/generate-child-cards`（`execute_generate`，不經 `NoteService.create`），以及直接呼叫 AnkiConnect 的腳本。`backend/scripts` 裡沒有呼叫 `/handlers/.../notes` 的腳本。
- 兩個請求同時建同一張卡時，現在靠 Anki 的首欄位重複檢查擋下第二張（`allowDuplicate=false`、`duplicateScope="deck"`，回 409 `DUPLICATE_CARD`）；牌組不同時擋不住。

### 1.2 會遺失的情況

2026-11-03 以 Playwright + 假資料實測，加上對程式的推論：

| # | 情況 | 現在 | 原因 |
|---|---|---|---|
| L1 | 生成中重新整理、關掉分頁 | ❌ 結果遺失，回來也不顯示「生成中」，只剩輸入（實測） | 請求隨頁面中止；結果只會寫進發出請求的那一頁 |
| L2 | 生成中切到別的 App（iPhone） | ❌ 很可能遺失（推論，未實機驗證） | iOS 會暫停或終止背景的 Safari 分頁，請求斷掉的情況同 L1 |
| L3 | LLM 超過約 100 秒才回來 | ❌ 遺失，網頁顯示「無法生成」，但後端其實生成完了（推論，§8 R3 要實際確認） | Cloudflare 對單一 HTTP 回應的等待上限約 100 秒，逾時回 524；CLI 重試最壞約 190 秒 |
| L4 | 生成完成後關掉分頁、開新分頁 | ❌ 看不到（實測） | sessionStorage 每個分頁各自一份 |
| L5 | 換一台裝置（手機生成、電腦建立） | ❌ 看不到 | 草稿只在瀏覽器 |
| L6 | 後端重啟（部署、當機、NAS 重開機） | ❌ 遺失 | 請求中斷（每次合併到 main 且改到後端都會重建後端） |
| L7 | 生成中切到 Card Generator 或卡片瀏覽頁 | ✅ 保留（實測） | 同一個分頁的 SPA，mutation 照樣完成 |
| L8 | 生成完成後重新整理 | ✅ 保留（實測） | sessionStorage 在同一個分頁內還在 |
| L9 | 草稿只能用「建立」或「廢棄」結束 | ⚠️ 預覽畫面還有「清除」，會直接刪掉草稿；「廢棄」沒有確認 | — |

L1～L5 只改前端解決不了：結果還沒回到瀏覽器時，頁面就沒了。生成必須在後端跑完並存下來，網頁只是去讀它。**L6 本計畫不解決**（使用者 2026-11-07 決定只存記憶體，§8 R1）。

## 2. 目標與非目標

**目標**

- G1 按「生成」後，生成在後端背景執行，結果存在後端記憶體。之後網頁重新整理、關掉分頁、手機切到別的 App、換裝置、Cloudflare 斷線，都不影響結果（後端沒有重啟的前提下）。
- G2 **全站同一時間只有一份草稿**（不分卡片類型）。有草稿時，網頁不接受新的生成或建卡，直到這份草稿「建立」成功或「廢棄」。
- G3 打開建卡畫面（Card Generator 或卡片瀏覽頁的建卡面板）時，向後端讀目前的草稿：生成中顯示「生成中」，完成了顯示預覽，失敗了顯示原因與「重試」；草稿是別種卡片時，顯示「目前有一份○○的草稿」與前往按鈕。
- G4 預覽中的修改（勾選、卡面情境、補充、標籤）自動存回後端，換裝置也接得下去。
- G5 兩個分頁、兩台裝置同時操作時，同一時間只有一個動作會成功，其他的拿到「草稿已被其他地方處理」與最新的草稿，不會建出兩張卡。
- G6 生成完成時，使用者在網站的任何一頁都看得到通知。
- G7 生成前填到一半的輸入、填表流程（一般卡片）填到一半的內容，關掉分頁後還在（同一台裝置、同一個瀏覽器）。後端重啟後，草稿雖然不在，生成前的輸入還在本機，可以重新生成。
- G8 本機 Python 腳本走的 API 不受這把鎖限制。

**非目標**

- 後端重啟後保留草稿（L6）：只存記憶體，接受遺失。
- 同時有多份草稿（不同卡片類型各一份，或同一種卡片多份並列）。
- 改 LLM 的提示詞、模型或輸出。
- 「重新生成」一份已經成功的草稿：預覽流程計畫書決定不提供，維持不變。失敗後的「重試」不受這條限制。
- TG 建卡：最後階段移除（D10）。移除前 TG 建卡照舊運作、不受本計畫的鎖限制。TG 的其他功能（錄音評分、上傳音檔、刪除條目、人脈卡 `/p` 的編輯、`/sync`、`/setconfig`）不動。
- 多人、權限：這是個人工具，草稿不分使用者。

## 3. 設計決策

### D1 草稿只存在後端記憶體：`CreateSession`

- 新的 `app/services/create_session.py`：一個 process 層級的單例 `CreateSession`，在 lifespan 啟動時建立、放在 `app.state`，REST 的依賴注入都拿同一個實例。
- 只有一個槽位：`current: Draft | None`。`Draft` 是 dataclass（欄位見 §5）。
- 一把 `asyncio.Lock` 保護所有狀態轉換（建立、修改、重試、開始建立、完成、廢棄）。每次轉換在鎖內做完「檢查 → 改狀態」，不在鎖內等 LLM 或 Anki。
- 不存資料庫、不存檔案。後端重啟後槽位是空的（§8 R1）。
- 放棄的做法（第一版）：網站 SQLite 的新表 `card_drafts`，或 MySQL。使用者 2026-11-07 決定先用最簡單的設計。

### D2 全站一把鎖：「建卡工作階段」

- 槽位不是空的（不論狀態），就是「有一份建卡工作進行中」。這段期間：
  - 網頁的「生成」（`POST /handlers/{name}/drafts`）：回 409 `CREATE_SESSION_BUSY`，附上目前的草稿。
  - 網頁的填表建卡（單字卡、人脈卡等沒有預覽流程的卡片，`POST /handlers/{name}/notes` 不帶 `draft_id`）：同樣回 409 `CREATE_SESSION_BUSY`。使用者 2026-11-07 決定：降低設計難度，也沒有同時生成多張卡的需求。
- 槽位是空的時候，網頁的填表建卡也要「佔一下」槽位：請求進來時在鎖內確認槽位是空的，並把槽位設成 `creating`（沒有生成結果的那種），寫入 Anki 後清空。這樣填表建卡和另一邊的「生成」不會同時發生。
- 鎖的檢查放在**網頁的入口**（`app/api/drafts.py` 與 `app/api/handlers.py` 的建卡端點），不放進 `NoteService.create`：TG 也呼叫 `NoteService.create`，放在裡面會連 TG 一起擋（D10）。
- 例外：本機腳本不受限制（G8），**不必寫任何程式**，因為腳本建卡的三種方式都不經過上鎖的入口（`NoteService.create` 與 `/drafts`）：
  - 呼叫後端專用的端點：`JP_VerbPair`、`JP_CoreVerb` 的 `generate_child_cards.py` 打 `/verb-pair/generate-child-cards`、`/core-verb/generate-child-cards`，後端在裡面呼叫 handler 的 `execute_generate`。
  - 在腳本自己的 Python process 裡 import handler 的程式：例如 `Expression_Correction/generate_expression_cards.py` 自己建 `AnkiClient`、直接呼叫 `execute_create`。鎖在後端的記憶體裡，腳本的 process 碰不到。
  - 直接連 AnkiConnect：例如 `import_cards_from_json.py`、`Speaking_Trilingual_Dark/import_cards.py`。
  - `backend/scripts` 裡沒有呼叫 `/handlers/.../notes` 的腳本（2026-11-07 確認）。日後若有腳本要走這個端點，就會受鎖限制，到時再另外設計。

### D3 狀態與轉換

```
              POST /drafts                  完成                    開始建立                  寫入成功
 （空槽位）──────────────► generating ──────────► ready ─────────────────► creating ──────────────► （空槽位）
                                │                   │  ▲                     │
                                │ LLM 失敗           │  │ 寫入失敗（422、Anki 錯誤）退回
                                ▼                   │  └─────────────────────┘
                             failed ── 重試 ──► generating
                                │                   │ 使用者確認「廢棄」
                                └── 廢棄 ──────────┴──────► （空槽位）
```

- `generating`：生成中。網頁顯示「生成中」與開始時間；**不能廢棄、不能建立**，要等結果回來（D8）。
- `ready`：可預覽、修改、建立、廢棄。
- `failed`：顯示原因；可以「重試」（同樣的輸入）、「改輸入後重試」（回到輸入畫面，帶著原本的輸入）、「廢棄」。
- `creating`：建立中（正在寫入 Anki）。不能修改、不能廢棄、不能再按建立；其他分頁顯示「建立中」。
- 每次轉換都把 `version` 加一。需要前提狀態的轉換（修改、重試、開始建立）帶上請求方看到的 `version`，不符就回 409 與最新的草稿（D6）。

### D4 生成改為背景工作

- `POST /handlers/{name}/drafts` 先在鎖內確認槽位是空的、驗證輸入（`DRAFT_PARAMS`），建一份 `generating` 的草稿放進槽位，再啟動背景生成，立刻回 202 與草稿。請求在一秒內結束，不受 Cloudflare 100 秒限制。
- 背景生成：`asyncio.create_task` 執行草稿生成（查既有標籤 → `execute_draft` → 整理建議標籤），完成後在鎖內寫回 `ready` 與結果，失敗寫回 `failed` 與錯誤（`error_code`、`message`，與現在 502、422 的內容相同）。`CreateSession` 持有 task 的參照，task 結束時移除。
- **背景工作不能沿用請求裡的 `NoteService`**：`get_note_service` 每個請求各建一個（`core/dependencies.py:243`），依賴的 `RelationService` 綁著請求範圍的語料庫 DB session，回應送出後就關閉。所以把 `NoteService.draft` 拆成兩段：
  - 「檢查能力、驗證輸入」：留在請求裡（`NoteService.validate_draft_input`）。
  - 「生成」：抽成只依賴 handler 註冊表與 `CardService` 的函式（`generate_draft(registry, card_service, handler_name, parameters)`）。`CardService` 只需要 `AnkiClient` 與 `AnkiModelManager`，兩者都可以用 lifespan 建好的單例組出來，不綁請求。
- 生成中不能廢棄（D8），所以結果回來時槽位裡一定還是同一份草稿。寫回時仍比對 `id` 當作保險，不符就丟掉結果並記 log。

### D5 預覽中的修改自動存回後端

- 草稿分成兩部分：
  - `result`：後端產生、之後不再改的生成結果（`params` 與 `suggested_tags`，即現在 `DraftResponse` 的內容）。
  - `state`：網頁的編輯狀態（勾選、卡面情境、補充、標籤），也就是現在 sessionStorage 裡的 `DraftState`。後端只當成 JSON 存取、檢查大小（上限 256 KB），不解讀內容。建立時網頁照舊把勾選結果組成建立參數送出（`selectedParams`），後端照舊以 `CREATE_PARAMS` 驗證。
- 網頁在使用者修改後 800 ms 送 `PATCH /drafts/{id}`，帶 `version`。版本不符時回 409 `DRAFT_VERSION_CONFLICT` 與最新的草稿；網頁顯示「這份草稿在其他地方改過了」，提供「載入最新」與「用這裡的覆蓋」。正常使用不會發生（同一時間只有一份草稿、通常只在一個地方編輯），真的發生時後到的一方看到這個提示（使用者 2026-11-07 決定）。
- 存檔失敗（網路斷了）：編輯狀態先存一份在 localStorage（`draft_state_backup:{draft_id}`），預覽畫面顯示「尚未儲存」；恢復連線後自動重送，成功後刪除備份。
- 預覽畫面顯示儲存狀態：「已儲存」、「儲存中…」、「尚未儲存（離線）」。

### D6 同時操作：每個動作先「搶到」草稿再做

- 所有動作都在 `CreateSession` 的鎖內完成狀態檢查與轉換，鎖外才去呼叫 LLM 或 Anki。所以兩個請求同時到時，只有先拿到鎖、且狀態與版本相符的那個會成功：

| 同時發生的事 | 先到的 | 後到的 |
|---|---|---|
| 兩邊按「生成」（或一邊生成、一邊填表建卡） | 佔到空槽位 | 409 `CREATE_SESSION_BUSY` ＋ 目前的草稿 |
| 兩邊按「建立」 | `ready` → `creating`，去寫 Anki | 409 `DRAFT_BUSY`（狀態已是 `creating`）＋ 目前的草稿；前端顯示「建立中」 |
| 兩邊按「重試」 | `failed` → `generating` | 409 `DRAFT_NOT_FAILED` ＋ 目前的草稿；前端顯示「生成中」 |
| 兩邊修改預覽 | 寫入、`version` 加一 | 409 `DRAFT_VERSION_CONFLICT`（D5） |
| 一邊建立中、一邊按「廢棄」 | 建立繼續 | 409 `DRAFT_BUSY`：「正在建立，請稍候」 |
| 生成中按「廢棄」或「建立」 | — | 409 `DRAFT_BUSY`：「還在生成，請等結果回來」 |

- 這解決第一版留下的漏洞：兩個分頁同時按「建立」，而且左側選了不同牌組時，Anki 的重複檢查（只看同一個牌組）擋不住，會建出兩張。

### D7 建立時帶 `draft_id`，成功後清空槽位

- `POST /handlers/{name}/notes` 加選填的 `draft_id` 與 `draft_version`。
  - 帶 `draft_id`：在鎖內確認槽位裡是這份草稿、狀態是 `ready`、版本相符、handler 相同，改成 `creating`，鎖外寫入 Anki。
  - 不帶 `draft_id`（填表建卡）：依 D2，槽位必須是空的。
- 寫入 Anki 成功：清空槽位，回 201。清空只是改記憶體，不會失敗，所以第一版的 `draft_warning`（刪草稿失敗）不再需要。
- 寫入失敗（422、Anki 錯誤）：退回 `ready`，網頁照舊顯示錯誤，草稿還在。
- 回應在路上遺失（建立成功但網頁沒收到）：網頁重新讀取時槽位是空的，顯示「這份草稿已不在（可能已建立，或在其他地方廢棄）」，附上到卡片瀏覽頁的連結。

### D8 「廢棄」要確認；預覽畫面拿掉「清除」

- 「廢棄」改成先確認（對話框：「廢棄這份草稿？生成的內容會一起刪除，無法復原。」），確認後 `DELETE /drafts/{id}`，清空槽位。
- **生成中不能廢棄**（使用者 2026-11-07 決定）：要等結果回來，變成 `ready` 或 `failed`（含逾時）之後，才能建立或廢棄。網頁在生成中把「廢棄」停用（見下面「生成中的畫面」）。
  - 理由：claude-code、antigravity 的 CLI 子程序在執行緒裡跑（§1.1），取消 task 停不下它，CLI 會在背景跑完並計費。等結果回來再處理，就不必處理「取消」與「丟掉過時的結果」，改動最小。
  - 最久要等到 LLM 的逾時（`LLM_CLAUDE_CODE_TIMEOUT_SECONDS`、`LLM_ANTIGRAVITY_TIMEOUT_SECONDS`，預設 900 秒）才會變成 `failed`；走 HTTP 的 LLM 依各自的逾時設定。
  - 不改 `cli_base.py` 的呼叫方式。
- **生成中的畫面**（使用者 2026-11-07 決定）：維持輸入畫面，輸入欄位停用；按鈕列依序是「生成」「廢棄」「清除」，三個都停用，「清除」右邊放一個旋轉的圖示（`Loader2` 加 `animate-spin`，與建卡按鈕現在的圖示相同）與文字「生成中…」。
  - 現在的旋轉圖示在「生成」按鈕裡面（`PreviewCreate.tsx` 的輸入表單），改成放在「清除」右邊；「生成」按鈕在生成中顯示原本的圖示與文字，只是停用。
  - 「廢棄」原本只在預覽畫面出現，生成中也顯示在輸入畫面（停用），讓人知道結果回來後才能廢棄。
  - 按鈕下方保留現在的說明（「LLM 正在寫…可以先去別頁，生成好會通知」），另加開始時間。
- **建立中的畫面**：預覽畫面的「建立」按鈕維持現在的寫法（按鈕內旋轉圖示與「建立中、同步中…」）；「廢棄」停用。
- 預覽畫面的「清除」拿掉，它的效果與「廢棄」重疊，卻沒有確認。輸入畫面的「清除」保留，只清輸入。

### D9 網頁讀取草稿與通知

- 建卡畫面打開時：`GET /drafts/current` 取目前的草稿（全站最多一份）。
  - 是這種卡片的草稿：依狀態顯示生成中、預覽、失敗或建立中。
  - 是別種卡片的草稿：不顯示輸入畫面，改顯示「目前有一份○○的草稿待處理」與「前往」（切到那種卡片）、「廢棄」。
  - 沒有草稿：顯示輸入畫面（預覽流程）或表單（填表流程）。
  - `generating`、`creating`：每 3 秒重新讀取一次（TanStack Query `refetchInterval`），變成其他狀態後停止。
  - 分頁回到前景（`visibilitychange`）時立刻讀一次：手機從別的 App 回來會馬上看到結果。
- 全站通知：版面層級掛一個 `DraftWatcher`。只在已知有 `generating` 的草稿時，每 10 秒讀 `GET /drafts/current`；草稿從 `generating` 變成 `ready` 或 `failed` 時跳 toast（「三語草稿已生成」附「前往」按鈕，或「生成失敗」）。取代現在 mutation `onSuccess` 的 toast。
- 不用 SSE、WebSocket：Cloudflare Access 加 Tunnel 的環境下，輪詢最單純也最不容易出錯；只有一份草稿，負擔可以忽略。

### D10 最後階段：TG 建卡移到網頁，網頁驗收後才移除 TG 端

使用者 2026-11-07 決定：TG 要做到網頁那樣好用的「指定目標牌組」難度很高（TG 的牌組現在是寫死在程式裡：單字卡 `Default`、外語糾錯 `日本語::外語糾錯::母卡片`、人脈卡 `人脈`；只有對話卡在流程第一步讓人手打牌組名），所以 TG 不再建立新卡，改由網頁提供。分三步，順序不能換：

**D10.1 網頁補上對話卡（單語）的建卡**（backlog W1，填表流程）

- `speaking_coach` 的 `CreateCapability` 開放 `web`：`flow="form"`、`deck="selected"`（左側牌組樹選的牌組）。
- `SpeakingCoachCreateParams` 補上參考回答 `references`：TG 流程有收（`speaking_fsm.py` 組成 `References` 的 JSON），handler 現在沒有（`execute_create` 一律寫 `[]`）。
- 網頁的表單要能輸入多筆參考回答。現在的建卡表單（`lib/createForm.ts`）不支援陣列欄位：補上「多行文字框、一行一筆」的輸入（`x-ui: lines`），送出時拆成陣列、去掉空行（使用者 2026-11-07 決定）。
- Card_ID：handler 用 uuid4，TG 用 `SC_%Y%m%d_%H%M%S`。網頁沿用 handler 的 uuid4（同一秒建兩張不會撞）；TG 建的舊卡不改。標籤沿用 handler 的 `Speaking_Coach`、`HandlerGenerated`（TG 加的是 `TelegramBot`、`Speaking_Coach`）。
- 受 D2 的鎖限制（填表建卡）。

**D10.2 網頁補上外語糾錯的建卡**（backlog W2，預覽流程，用本計畫的 `CreateSession`）

- `expression_correction` 的 `CreateCapability` 開放 `web`（`flow="preview"`、`deck="fixed"`，母卡 `日本語::外語糾錯::母卡片`、子卡 `…::子卡片`，與 TG 相同）。
- 新增 `DRAFT_PARAMS`：母語、目標語言、原文、情境、來源標籤、自己的文法修正（選填）、自己的重新組織（選填），即 TG 流程 6 步收的內容（`ExpressionCreateParams` 已有這些欄位）。
- 新增 `execute_draft`：呼叫現有的 `execute_generate`（LLM），回傳建立參數的草稿（上面的輸入，加上 `llm_result`；`selected_indices` 預設全選）。
- **重複檢查與覆蓋舊卡**：TG 在呼叫 LLM 前先查同一句原文有沒有建過（`expression_fsm.py:428` 的 `_check_duplicate_before_generate`），有的話讓人選「覆蓋舊卡」「允許重複」「取消」；選覆蓋時，新卡寫入成功後才刪舊的母卡與子卡。這段邏輯只在 TG 的流程裡，要搬進 handler，網頁與（移除前的）TG 共用：
  - 網頁按「生成」時，後端先查重複；有重複就回 409 `DUPLICATE_FOUND` 與舊卡的摘要，**不建草稿、不呼叫 LLM**。網頁顯示舊卡，讓人選「覆蓋舊卡」「允許重複」「取消」；選了前兩個就帶著決定重送 `POST /drafts`。
  - 決定存在草稿的 `input`，建立時照做：覆蓋＝先建新卡、成功後刪舊的母卡與子卡；允許重複＝`allow_duplicate=true`。
- 網頁的預覽畫面：新的 `ExpressionDraftPreview`（母卡內容，加上子卡清單與勾選），放在 `PreviewCreate` 依卡片類型切換的預覽區（與三語卡的 `TrilingualDraftPreview` 同一種做法）。
- 受 D2 的鎖限制。

**D10.3 移除 TG 的建卡**（D10.1、D10.2 部署後，**使用者在網頁確認沒問題並明確指示**才做）

- 移除 `/newcard`：`bot/handlers/newcard_menu.py`、`bot/handlers/fsm/vocabulary_fsm.py`、`expression_fsm.py`、`speaking_fsm.py`，與 `bot/dispatcher.py` 裡的註冊。
- 移除人脈卡在 TG 的建立：`/newcard` 選人脈卡，以及 `/p` 找不到人時的「建立新的人」（`person_profile.py` 的 `start_person_create`、`_create_person` 等；使用者 2026-11-07 決定一起移除，找不到人時改回覆「到網頁建立」與網址）。`/p` 的找人、看摘要、記互動、改資料、換照片都保留。
- 改文字：`/help`（`commands.py`）、`errors.py` 的 `RETRY_HINT`、`messages.py`、`bot/utils/texts.py` 等所有提到 `/newcard` 的地方，改成「建卡請到網頁」並附網址。
- 各 handler 的 `CreateCapability.channels` 拿掉 `tg`。
- 跟著刪掉或改寫的測試（例如 `tests/test_bot_replies.py`）。
- 移除後，所有網頁以外的建卡入口只剩本機腳本（不受鎖限制，D2）。

**過渡期**（D10.3 之前）：TG 經 `NoteService.create` 建卡時**不**受鎖限制，鎖的檢查只在網頁的入口（D2）。網頁有一份草稿時 TG 仍然可以建卡；兩邊建同一張卡時，靠 Anki 的首欄位重複檢查擋下（§1.1）。

### D11 生成前的輸入與填表內容改存 localStorage

- `generate_form:{handler}`、`generate_tags:{handler}` 從 sessionStorage 改成 localStorage：關掉分頁再開還在，同一個瀏覽器的分頁共用（`useLocalStorage` 已會跨分頁同步，`hooks/useStoredState.ts`）。
- 按「生成」後，輸入也存在後端草稿的 `input`。生成成功後清掉本機的輸入，與現在相同；失敗時保留，「改輸入後重試」直接帶回。
- 後端重啟、草稿遺失時：本機的輸入只在「生成成功後」才會清掉，所以要等到 `ready` 才清。
- 「草稿已遺失」的提示（使用者 2026-11-07 決定要做）：網頁在 localStorage 記住目前草稿的 `id`（鍵 `current_draft_id`；建立草稿時寫入，建立成功或廢棄時刪除）。建卡畫面讀到後端沒有草稿、本機卻記得有一份時，顯示一行「伺服器重新啟動過，上一份草稿已遺失，可以用原本的輸入重新生成」，並刪掉這個 `id`。
- 仍用 sessionStorage 的：卡片瀏覽頁的成功提示（`generate_created:{handler}`）、劇情流程圖各分頁的捲動位置（`StoryGraph.tsx`）。選的牌組、卡片類型、面板寬度、卡片瀏覽的位置本來就在 localStorage，不動。

### D12 舊端點與舊草稿

- 舊的即時回應端點 `POST /handlers/{name}/draft`（請求等到 LLM 生成完才回應）**這次直接移除**（使用者 2026-11-07 決定）。
  - 代價：CI 先部署後端、再部署前端，中間約幾十秒的空窗內，還沒更新的舊前端按「生成」會失敗。重新整理後就是新前端。
  - 因此後端與前端必須在同一個 PR 合併（§7）。
- 上線前留在 sessionStorage 的舊草稿（`generate_draft_v2:{handler}`）：新前端不讀，等於遺失，不處理（使用者 2026-11-07 決定）。

### D13 適用所有預覽流程的卡片

- 依 handler 的 `DRAFT_PARAMS` 與 `capabilities.create.flow == "preview"` 判斷，不寫死三語卡。目前只有三語對話卡；D10.2 的外語糾錯直接沿用。

## 4. API

前綴 `/api/v1`，全部需要 `X-API-Key`（與現在的 handlers 路由相同）。錯誤一律是 `{"error_code", "message"}`；附上目前草稿的 409 另帶 `draft`。

### 4.1 草稿物件

```json
{
  "id": "5f0c…",
  "handler": "speaking_trilingual",
  "status": "generating | ready | failed | creating",
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

- `result` 只有 `ready`、`creating` 時有值；`error` 只有 `failed` 時有值；`state` 在網頁第一次存檔前是 `null`。
- 填表建卡佔槽位時（D2）也是一份 `creating` 的草稿，`input`、`result` 為 `null`；其他頁面讀到時顯示「正在建立一張○○」。

### 4.2 端點

| 方法與路徑 | 用途 | 成功 | 主要錯誤 |
|---|---|---|---|
| `GET /drafts/current` | 讀目前的草稿 | 200 草稿；沒有草稿時 200 `null` | — |
| `POST /handlers/{name}/drafts` | 開始生成（body：`{params}`，即現在的草稿輸入；外語糾錯另可帶 `duplicate_decision`：`overwrite`、`allow`，D10.2） | 202 草稿 | 404 handler 不存在；400 `HANDLER_OPERATION_UNSUPPORTED`（沒有預覽流程）；409 `CREATE_SESSION_BUSY`；409 `DUPLICATE_FOUND`（外語糾錯，附舊卡摘要）；422 輸入驗證失敗 |
| `PATCH /drafts/{id}` | 存編輯狀態（body：`{state, version}`） | 200 草稿 | 404 `DRAFT_NOT_FOUND`；409 `DRAFT_VERSION_CONFLICT`；409 `DRAFT_BUSY`（不是 `ready`）；413 `state` 太大 |
| `POST /drafts/{id}/retry` | 失敗後重試（body：`{version}`，選填 `params`：給了就換成新的輸入） | 202 草稿 | 404；409 `DRAFT_NOT_FAILED`；422 |
| `DELETE /drafts/{id}` | 廢棄（只限 `ready`、`failed`，D8） | 204 | 404；409 `DRAFT_BUSY`（`generating`、`creating`） |
| `POST /handlers/{name}/notes` | 既有的建卡；body 加選填 `draft_id`、`draft_version` | 201 | 既有的錯誤；409 `CREATE_SESSION_BUSY`（不帶 `draft_id` 而槽位不是空的）；404 `DRAFT_NOT_FOUND`（帶了 `draft_id` 但槽位裡不是它）；409 `DRAFT_BUSY`、`DRAFT_VERSION_CONFLICT` |

- 所有 `{id}` 端點都比對槽位裡的草稿 `id`：舊分頁拿著已經結束的草稿操作時回 404，不會誤動到新的一份。
- 草稿的 `error` 沿用生成失敗時現有的錯誤碼，例如 `LLM_SERVICE_ERROR`（現在回 502）、`LLM_GENERATION_FAILED`（現在回 422）；背景生成沒有 HTTP 回應，只把錯誤碼與訊息存進草稿。

## 5. 記憶體裡的資料

`Draft`（dataclass，存在 `CreateSession.current`）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | `str` | UUID4 hex |
| `handler_name` | `str` | handler 名稱 |
| `status` | `Literal["generating", "ready", "failed", "creating"]` | |
| `input` | `dict \| None` | 生成的輸入（`DRAFT_PARAMS` 驗證後） |
| `result` | `dict \| None` | 生成結果 |
| `state` | `dict \| None` | 網頁的編輯狀態（上限 256 KB） |
| `error_code`、`error_message` | `str \| None` | 失敗時 |
| `version` | `int` | 每次轉換加一 |
| `created_at`、`updated_at`、`generation_started_at` | `datetime` | UTC |

`CreateSession` 另外持有：`lock: asyncio.Lock`、`task: asyncio.Task | None`（目前的背景生成）。

## 6. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `app/services/create_session.py`（新） | `Draft`、`CreateSession`（槽位、鎖、狀態轉換、版本檢查、背景 task）與 `generate_draft()`（不綁請求的生成，D4） |
| `app/schemas/drafts.py`（新） | `DraftOut`、`StartDraftRequest`、`UpdateDraftRequest`、`RetryDraftRequest` |
| `app/services/note_service.py` | `draft()` 拆成「驗證輸入」與「生成」（D4）；`create()` 本身不變（TG 照舊呼叫，D10） |
| `app/api/drafts.py`（新）、`app/api/handlers.py` | 新端點；建卡端點接受 `draft_id`、`draft_version`，在呼叫 `NoteService.create` 前後經 `CreateSession` 佔用與釋放槽位（D2、D7）；移除舊的 `POST /handlers/{name}/draft` |
| `app/core/exceptions/…`、全域 handler | `CreateSessionBusyError`、`DraftNotFoundError`、`DraftBusyError`、`DraftVersionConflictError`、`DraftNotFailedError` 對應的錯誤碼與狀態碼；409 的回應附 `draft` |
| `app/core/dependencies.py` | 注入同一個 `CreateSession`（從 `app.state` 取） |
| `app/main.py` | lifespan 建立 `CreateSession`；關機時取消背景 task（CLI 子程序停不下來，見 §8 R2） |
| `backend/Dockerfile` | `CMD` 旁加註解：草稿與鎖都在記憶體，不能加 `--workers`（§8 R4） |

### Frontend

| 檔案 | 改動 |
|---|---|
| `src/api/client.ts`、`src/types/api.ts` | 草稿端點與型別；`createNote` 加 `draft_id`、`draft_version`；409 `CREATE_SESSION_BUSY` 的處理 |
| `src/hooks/useServerDraft.ts`（新） | 讀取、輪詢、回到前景時重讀、自動存檔（防抖、版本、離線備份與重送） |
| `src/components/generate/PreviewCreate.tsx` | 改用 `useServerDraft`：生成中、失敗（重試、改輸入後重試）、預覽、建立中、儲存狀態、衝突處理；「廢棄」加確認；預覽畫面拿掉「清除」 |
| `src/components/generate/CreatePanelBody.tsx` | 有別種卡片的草稿時顯示提示與「前往」「廢棄」（D9）；填表內容改用 localStorage（D11） |
| `src/components/shared/DraftWatcher.tsx`（新）、`src/App.tsx` | 全站完成通知 |

### 最後階段（D10）

| 檔案 | 改動 |
|---|---|
| `app/services/task_handlers/speaking_coach_handler.py` | 開放 `web`；`CREATE_PARAMS` 加 `references`；`execute_create` 寫入參考回答（D10.1） |
| `frontend/src/lib/createForm.ts`、`components/generate/formParts.tsx` | 建卡表單支援「多行文字框、一行一筆」的陣列欄位（多筆參考回答，D10.1） |
| `app/services/task_handlers/expression_handler.py` | 開放 `web`；`DRAFT_PARAMS`、`execute_draft`；重複檢查與覆蓋舊卡自 `expression_fsm.py` 搬進來（D10.2） |
| `app/api/drafts.py`、`app/services/create_session.py` | `DUPLICATE_FOUND` 與 `duplicate_decision` |
| `frontend/src/components/generate/ExpressionDraftPreview.tsx`（新）、`PreviewCreate.tsx` | 外語糾錯的預覽與子卡勾選；重複時的「覆蓋舊卡」「允許重複」「取消」 |
| D10.3：`bot/handlers/newcard_menu.py`、`bot/handlers/fsm/*.py`、`bot/handlers/person_profile.py`、`bot/dispatcher.py`、`bot/handlers/commands.py`、`errors.py`、`messages.py`、`bot/utils/texts.py`、各 handler 的 `channels`、相關測試 | 移除 TG 建卡與改文字（使用者指示後才做） |

### 測試

- 後端（pytest）：狀態轉換（生成成功、LLM 失敗、重試、廢棄、生成中不能廢棄或建立）；D6 表格的每一種同時操作（以兩個並發的 coroutine 模擬）；`CREATE_SESSION_BUSY`（網頁生成、網頁填表）；建卡帶 `draft_id` 時成功清空、失敗退回 `ready`；`state` 大小上限；腳本端點不受影響。LLM 以假的 handler 取代。
- 前端（vitest）：自動存檔的防抖與版本處理、離線備份與重送（純函式部分）。
- 最後階段：對話卡建立參數含參考回答、Card_ID 為 uuid4；外語糾錯的 `execute_draft`、重複時回 `DUPLICATE_FOUND` 且不呼叫 LLM、覆蓋時新卡成功後才刪舊卡、新卡失敗時舊卡不動；D10.3 之後 `/newcard` 不再註冊、`/help` 不再提到它。
- 瀏覽器（Playwright + 假 API）：§9 的流程項目。

## 7. 實作順序

| PR | 階段 | 內容 | 上線條件 |
|---|---|---|---|
| PR 1 | P1 後端核心 | `CreateSession`、`generate_draft()` 的拆分、狀態轉換與 D6 的並發測試 | |
| | P2 後端端點 | `/drafts` 各端點、建卡的 `draft_id`、填表建卡的 D2 檢查、移除舊的即時回應端點 | |
| | P3 前端草稿 | `useServerDraft`、`PreviewCreate` 改寫、廢棄確認、生成中的按鈕、儲存狀態與衝突、別種卡片草稿的提示 | |
| | P4 前端其餘 | `DraftWatcher`、localStorage 與「草稿已遺失」提示（D11） | |
| | P5 驗證 | 測試、lint、build；瀏覽器實測；部署後在正式站實測 §9.1（含手機） | 部署 |
| PR 2 | P6 對話卡網頁建卡 | D10.1 | |
| | P7 外語糾錯網頁建卡 | D10.2 | |
| | P8 驗證 | 測試、lint、build；部署後在正式站實測 §9.2 | 部署後**等使用者確認網頁沒問題** |
| PR 3 | P9 移除 TG 建卡 | D10.3；驗收 §9.3 | **使用者明確指示後才開始** |

- PR 1 必須前後端一起合併：舊的即時回應端點直接移除（D12），後端不再向下相容。
- PR 2 依賴 PR 1：外語糾錯的網頁建卡用 PR 1 的 `CreateSession`。
- PR 3 依賴 PR 2 上線並經使用者確認：先移除 TG 的話，對話卡與外語糾錯會暫時沒有地方建。

## 8. 風險與未知

- **R1 後端重啟時草稿遺失**（使用者接受）：部署、當機、NAS 重開機都會讓槽位清空，連 `ready` 的草稿也沒了。每次合併到 main 且改到後端時都會重建後端。緩解：生成前的輸入存在本機 localStorage，要到 `ready` 才清（D11），重啟後可以重新生成；但生成結果與預覽中的修改無法救回。前端會提示「草稿已遺失」（D11）。
- **R2 生成中要等到結果回來**：生成中不能廢棄（D8），LLM 卡住時最久要等到逾時（CLI 預設 900 秒）才會變成 `failed`，這段期間網頁不能建卡。後端關機時，claude-code、antigravity 的子程序在執行緒裡跑 `subprocess.run`，不會被取消；asyncio 的預設執行緒池會等這些執行緒，容器可能要等到 Docker 的停止逾時才被強制終止。重啟後槽位本來就是空的，不影響正確性。
- **R3 Cloudflare 的逾時**：「約 100 秒」是 Cloudflare 一般方案的已知限制，本專案的正式站還沒實際確認過。實作前在正式機的 log 查有沒有超過 100 秒的 `/draft` 請求，以及前端是否收過 524，用來確認 L3 確實發生過。無論結果如何，D4 都解決這個問題。
- **R4 單一 process 的前提**：草稿、鎖、背景工作都在記憶體，依賴單一 uvicorn process（TG bot 也在同一個 process）。日後若加 `--workers` 或把 TG bot 拆出去，要改存資料庫並用條件式 UPDATE 取代 `asyncio.Lock`。在 `Dockerfile` 的 `CMD` 旁加註解提醒。
- **R5 本機與正式站各自一份**：本機後端與正式站後端的記憶體互不相通，各自有自己的槽位。本機開發不會擋到正式站，反之亦然。
- **R6 建卡回應遺失**：D7 的情況下，網頁只知道「草稿不在了」，不知道卡片建了沒有。個人工具，這個程度可以接受。
- **R7 費用**：重試會再呼叫一次 LLM。只有失敗時才能重試，與現在「失敗後再按一次」相同。
- **R8 忘了處理的草稿會一直擋住建卡**：沒有自動過期（使用者要求），生成後放著不管，網頁的建卡會一直被擋；網頁任何建卡畫面都能看到並廢棄這份草稿（D9）。

## 9. 驗收標準

### 9.1 草稿與鎖（PR 1）

- [ ] 生成中重新整理、關掉分頁再開：回來顯示「生成中」，完成後顯示預覽（L1）
- [ ] iPhone：按生成後切到別的 App 一分鐘以上再回來，結果在（L2，實機）
- [ ] 生成超過 100 秒（以假 LLM 延遲測試）：網頁不出錯，完成後顯示預覽（L3）
- [ ] 生成完成後關掉分頁、開新分頁：預覽還在，含勾選與修改（L4）
- [ ] 手機生成、在電腦打開建卡畫面：看得到同一份草稿並能建立（L5）
- [ ] 後端重啟：草稿不在，生成前的輸入還在、可以重新生成（R1）
- [ ] 有一份三語卡草稿時：Card Generator 選單字卡、卡片瀏覽頁的建卡面板，都顯示「目前有一份三語對話卡的草稿」與前往、廢棄，不能填表建卡
- [ ] 兩個分頁同時按「建立」（左側選不同牌組）：只建出一張；另一個分頁顯示「建立中」，之後顯示已建立
- [ ] 兩個分頁同時修改同一份草稿：後存的一方看到衝突提示，可以選擇載入最新或覆蓋
- [ ] 預覽中修改後立刻關掉分頁：回來時修改還在；離線時顯示「尚未儲存」，恢復連線後自動存好
- [ ] 「廢棄」要確認；預覽畫面沒有「清除」；草稿只因「建立」成功、「廢棄」或後端重啟消失
- [ ] 生成中沒有「廢棄」按鈕；直接呼叫 `DELETE` 或建卡回 409；結果回來（含失敗）後才能廢棄或建立
- [ ] 建立成功後槽位清空、可以再生成；建立失敗時草稿還在
- [ ] 在別的頁面等生成：完成時跳通知，按「前往」回到建卡畫面
- [ ] 生成前填到一半的輸入、一般卡片填到一半的內容，關掉分頁再開還在
- [ ] 有草稿時，本機腳本 `JP_VerbPair/generate_child_cards.py --limit 0 --dry-run`、`JP_CoreVerb/generate_child_cards.py --limit 0 --dry-run` 照常跑完
- [ ] 後端重啟後打開建卡畫面：顯示「伺服器重新啟動過，上一份草稿已遺失」，只顯示一次

### 9.2 網頁補上對話卡與外語糾錯（PR 2）

- [ ] Card Generator 與卡片瀏覽頁的建卡面板可以選對話卡：填目標台詞、情境、目標語言、多筆參考回答，建在左側選的牌組；Anki 裡 `References` 是參考回答的 JSON、`Card_ID` 是 uuid4
- [ ] 對話卡建好後，從卡面的 🎤 按鈕跳到 TG 錄音評分照常可用
- [ ] 外語糾錯：填六項輸入 → 生成（背景）→ 預覽母卡與子卡、勾選 → 建立；母卡在 `日本語::外語糾錯::母卡片`、子卡在 `…::子卡片`
- [ ] 外語糾錯的原文建過時：按生成先顯示舊卡，不呼叫 LLM；選「覆蓋舊卡」建立成功後舊的母卡與子卡被刪；選「允許重複」照常建立；選「取消」回到輸入
- [ ] 以上兩種卡都受鎖限制：有草稿時不能填表建對話卡、不能生成外語糾錯

### 9.3 移除 TG 建卡（PR 3，使用者指示後）

- [ ] TG 輸入 `/newcard` 不再出現選單，回覆「建卡請到網頁」與網址；`/help` 不再提到 `/newcard`
- [ ] `/p` 找不到人時不再提供建立；找人、看摘要、記互動、改資料、換照片照常
- [ ] TG 的錄音評分、上傳音檔、刪除條目、`/sync`、`/setconfig` 照常
- [ ] 工具設定頁的「Handler 能力」不再列出 TG 建卡

### 各 PR 共同

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

## 10. 待決問題

目前沒有。各輪的決定見檔頭的改寫說明。
