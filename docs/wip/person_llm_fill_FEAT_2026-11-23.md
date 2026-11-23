# FEAT — 人脈卡網頁的「AI 整理」：一段描述經 LLM 放到對的欄位，編輯、勾選後寫入（建新卡與補舊卡）

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-23 |
| **性質** | 新功能（網頁），沿用 TG 既有的整理邏輯與網頁既有的草稿機制 |
| **狀態** | 🚧 實作中 |
| **範圍** | 網頁的人脈卡「AI 整理」對話框：輸入一段描述 → LLM 在背景整理成建議 → 逐項編輯、勾選 → 寫入。兩種模式：**補舊卡**（卡面上的按鈕）與**建新卡**（建卡畫面的入口）。草稿可暫存、丟棄、重試，和三語卡共用全站一份草稿的鎖；模型用獨立的用途鍵。TG「用一段話記錄」的寫入邏輯搬到 service 層，TG 與網頁共用 |
| **不動** | TG「用一段話記錄」的行為（提示詞的補舊卡部分、建議格式、寫入結果都不變，只是寫入的程式搬家）；人脈卡的填表建卡與欄位編輯；三語卡等其他預覽流程；草稿只存在記憶體的取捨（server_drafts R1） |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/archive/person_profile_card_FEAT_2026-10-23.md` §3.13、P5（TG「用一段話記錄」）、`server_drafts_FEAT_2026-11-04.md`（草稿機制、D2 全站一把鎖）、`person_field_history_FEAT_2026-11-07.md`（會變的欄位）、`person_career_goals_FEAT_2026-11-14.md`（目標） |

---

## 1. 問題與動機

- 使用者想在網頁上用一段話記人脈：描述今天聊了什麼、對他多了解了什麼，由 LLM 放到對的位置。**內容都是使用者提供的**，LLM 只負責分類與重新組織語言，不補充、不推測。
- 這件事在 TG 已經有了：人脈卡計畫 P5（2026-10-24，`04c81aa`）的「🧠 用一段話記錄」——一段文字或語音經 LLM 整理成一筆互動、待追蹤、目標、文字欄位（取代或接在後面）與會變的欄位（新增一筆或劃掉某筆）的建議，勾選後才寫入。提示詞第一條就是「只記文中有的事，絕不推測或補充」。
- 網頁沒有，原因是那份計畫的範圍只有 TG（「不動：網頁前端」，G7 是不開 Anki 也能在 TG 維護人脈），而網頁第一次接上 LLM 預覽流程是之後的三語卡（2026-10-30），草稿機制更晚（2026-11-07）；之後沒有人回頭把它搬上網頁。
- 兩邊各有一半：TG 有整理邏輯（提示詞、LLM 輸出格式、`proposals_from`、寫入規則），但建議存在 TG 對話的狀態、寫入寫在 bot 裡；網頁有草稿機制（背景生成、暫存、丟棄、重試、鎖）與可編輯的預覽，但沒有接人脈卡。

## 2. 目標與非目標

**目標**
- G1 卡面上（人脈卡）有「AI 整理」，打開對話框：輸入一段描述 → 整理 → 逐項編輯與勾選 → 寫入這張卡（**補舊卡**）。
- G2 建卡畫面選人脈卡時，也能用同一個對話框從一段描述**建新卡**：整理出稱呼、姓名與其他資料，編輯、勾選後建立。
- G3 每一項建議都能改內容、決定要不要（同三語卡的預覽），並看得到「依據你哪句話」。
- G4 草稿可暫存（關掉對話框、重新整理頁面都還在）、可丟棄、生成失敗可重試；全站同時只有一份草稿，和三語卡等預覽流程共用同一把鎖。
- G5 模型可以單獨設定：新增用途鍵 `person_profile.fill`，在工具設定的能力路由裡和 TG 的 `person_profile` 分開選。
- G6 底層全部沿用現成的：LLM 請求走能力路由、提示詞沿用 `Person_Profile_Ingest.j2`（依模式修改）、LLM 輸出的 Pydantic 模型沿用、寫入走 handler 既有的動作與驗證。

**非目標**
- 語音輸入：網頁的對話框只有文字輸入（使用者 2026-11-23 決定）。本專案網頁的錄音是給口說卡評分口音用的，不是輸入方式；TG「用一段話記錄」的語音照舊。
- 拍名片、上傳圖片辨識（要能看圖的模型，另外再議）。
- LLM 自己查資料、補使用者沒說的事。
- 改變 TG 的操作流程（TG 只是改呼叫共用的寫入函式）。
- 草稿存進資料庫（後端重啟就消失，同三語卡，server_drafts R1）。

## 3. 設計決策

### D1 補舊卡與建新卡是兩種模式，共用同一條路
- 補舊卡：讀這張卡目前的欄位與清單 → 整理 → 建議 → 寫入這張卡。
- 建新卡：沒有已知資料 → 整理（多了稱呼、姓名等）→ 建議 → **先用稱呼與文字欄位建立卡片，再把其他建議當成「補這張新卡」寫入**。這樣互動、待追蹤、目標、會變的欄位（含事件時間、說明）都走和補舊卡完全一樣的動作，不必讓建卡參數另外支援它們。
- 兩種模式的差別只在：提示詞的說明與可填欄位（D3）、LLM 輸出模型（D4）、寫入的第一步（D7）。

### D2 LLM 請求與用途鍵
- 沿用 `person_profile_ingest.ingest()`、`build_prompt()`、`proposals_from()`；`ingest()` 多一個參數指定用途鍵，TG 照舊用 `person_profile`，網頁用新的 `person_profile.fill`（「人脈卡：網頁整理一段話」，能力 `llm`）。
- 用途鍵在能力路由登記一行即可（`route(...)`），設定存在 `cfg_capability_routes`，工具設定頁自動出現。只宣告 `llm`：網頁只有文字輸入，不需要 `stt`。

### D3 提示詞：沿用 `Person_Profile_Ingest.j2`，依模式分支
同一個模板加一個 `mode`（`update` / `create`）變數，共用的整理原則（只記文中有的事、日期換算、待追蹤、目標、會變的欄位與文字欄位的規則、輸出格式）不重寫。兩種模式不同的地方：

| 段落 | 補舊卡（`update`，與 TG 相同） | 建新卡（`create`） |
|---|---|---|
| 開頭的情境 | 使用者剛和「X」見面或聊天，記下發生了什麼 | 使用者描述一個要新建人脈卡的人：怎麼認識、對他的了解，可能也包含這次見面聊了什麼 |
| 已知資料 | 列出目前的欄位、清單（含 `#` 編號）、待追蹤、目標、最近的互動 | 沒有這一段 |
| 互動 | 一定要有一筆 | **文中有提到見面或聊天才填**，沒有就 `null` |
| 劃掉（`op: "end"`） | 可以用 | 不能用（沒有已知的項目）；以前的工作等直接 `add` 並帶結束時間 |
| 可填的文字欄位 | `UPDATABLE_FIELDS`（排除稱呼與姓名：補舊卡時不該讓 LLM 改名字） | 再加上姓名相關欄位（`Name_ZH`、`Name_EN`、`Name_JA`、`Name_JA_Kana`、`Name_Romaji`、`Honorific`）與認識的資料（`Met_Date`、`Met_Where`、`Met_Via`） |
| 稱呼 | 不輸出 | 輸出 `display_name`：平常怎麼稱呼他；文中沒有明確的稱呼時，用最常出現的叫法 |
| 輸出範例 | 現有的範例 | 另一組建新卡的範例（`interaction` 可為 `null`、有 `display_name`、沒有 `end`） |

- 「重新組織語言」維持現有的原則：簡潔、用使用者的語言、人名與對方的原話保留原文；不加內容。
- 補舊卡的提示詞內容不變（只是包在 `{% if mode == "update" %}` 裡），TG 的結果不受影響；改完用現有的 TG 測試確認。

### D4 LLM 輸出的 Pydantic 模型
- 補舊卡：沿用 `LLMPersonIngestResult`，不改。
- 建新卡：新增 `LLMPersonCreateResult(LLMPersonIngestResult)`，多 `display_name: str`，`interaction` 改成 `IngestInteraction | None`。其他欄位（待追蹤、目標、文字欄位、會變的欄位）的模型都沿用。
- `proposals_from()` 支援沒有互動與建新卡的可填欄位；建新卡時稱呼成為一項必填的建議（`kind: "display_name"`），不能取消勾選，可以改。

### D5 草稿：沿用 `CreateSession`，人脈卡宣告整理用的草稿輸入
- 人脈卡 handler 宣告草稿輸入 `PersonFillDraftParams`：`text`（必填，1–4000 字）與 `note_id`（選填；有值是補舊卡，沒有是建新卡）、建新卡時的 `deck`。
- 開始：沿用 `POST /handlers/person_profile/drafts`。背景生成時 handler 的 `draft_context` 讀出那張卡的欄位與清單（補舊卡），`execute_draft` 呼叫 `ingest()` 並轉成建議。
- 生成結果放在現有的 `result.params` 裡：`{"mode", "note_id", "proposals": [...]}`（`suggested_tags` 為空；人脈卡的標籤是固定的），**草稿機制的資料形狀不必改**。
- 暫存：網頁的編輯與勾選存在草稿的 `state`（沿用 `PATCH /drafts/{id}`，帶版本）。丟棄、重試沿用現有端點。
- 鎖：沿用全站同時只有一份草稿（server_drafts D2）。有人脈卡草稿時不能開始三語卡草稿，反之亦然，回 409 並附上目前的草稿。
- handler 的能力多一項 `fill: Operation`（網頁的 AI 整理），網頁依它決定要不要顯示按鈕；沒有預覽建卡流程的 handler 預設不支援。

### D6 寫入勾選的建議：搬成共用函式，TG 與網頁都用
- 把 TG 的 `_save_proposals`／`_proposal_action`（`bot/handlers/person_profile.py`）搬到 `person_profile_ingest.apply_proposals(note_service, card_service, note_id, proposals, saved)`：互動、待追蹤、目標、會變的欄位各用自己的動作（`add_interaction`、`add_followup`、`add_item`、`add_history`、`set_history_status`），文字欄位的建議合成一次 `update_fields`（取代或接在後面在寫入時以當下的欄位內容計算）。回傳這次寫入了哪些項目與同步警告。
- 中途失敗時記下已寫入的項目，再按一次只補寫剩下的（同 TG 現在的行為）。TG 改成呼叫這個函式，行為不變。
- 卡片在生成之後又被改過：文字欄位以寫入當下的內容計算；劃掉某筆靠指紋找到同一筆（`resolve_index`），找不到時那一項失敗並說明；handler 寫入前照常驗證。

### D7 寫入的端點：`POST /drafts/{id}/apply`
- 網頁送出：草稿版本、勾選並編輯過的建議清單。後端用 `PersonProposalEdit`（依 `kind` 分開的 Pydantic 模型，每一種沿用 `schemas/person_profile` 既有的項目模型）驗證。
- 佔用：沿用草稿的 `ready → creating` 轉換（同建卡，server_drafts D6、D7），寫入期間不能丟棄或重複送出。
- 補舊卡：`apply_proposals(note_id, …)`。
- 建新卡：先以稱呼與勾選的文字欄位建立卡片（`NoteService.create`，建在選的牌組），**成功後草稿立刻改記這張新卡的 `note_id`**，再對新卡 `apply_proposals` 其他項目。之後任何一步失敗，重試只會補寫，不會再建第二張卡。
- 全部成功：清空槽位（草稿結束）。部分失敗：草稿退回 `ready`，記下已寫入的項目與失敗原因，對話框標「已存」與錯誤，使用者可以改了再送或丟棄。
- `CreateSession` 需要的小改：寫入結束時能更新草稿的 `note_id` 與已寫入項目（`finish_apply`）。其他狀態、版本、鎖的規則不變。

### D8 網頁：「AI 整理」對話框
- 入口：
  - 補舊卡：卡片頁查看人脈卡時，卡面標題列的「AI 整理」。
  - 建新卡：Card Generator 與卡片頁「＋」選人脈卡時，填表上方的「用一段話建立」（牌組用建卡畫面選的）。
- 對話框（手機上是全螢幕的 Sheet）：
  1. 輸入一段描述，按「整理」。生成在背景進行，關掉對話框不會中斷；生成中顯示進度與已花的時間（同三語卡）。
  2. 建議依「互動、待追蹤、目標、文字欄位、會變的欄位」分組。每一項可勾選、可展開編輯，附「依據：…」（只讀）。可編輯的內容：

     | 建議 | 可以改的內容 |
     |---|---|
     | 稱呼（建新卡） | 稱呼（必填，不能取消） |
     | 互動 | 日期、聊了什麼、對方近況、地點、記住的一句話 |
     | 待追蹤 | 內容、類型（要問他／我答應的／他答應的）、期限 |
     | 目標 | 類型、內容、目標時間 |
     | 文字欄位 | 寫入的內容、取代或接在後面（補舊卡時旁邊顯示目前的內容） |
     | 會變的欄位：新增一筆 | 內容；工作經歷另有公司、職稱、起訖時間、說明 |
     | 會變的欄位：劃掉某筆 | 只能選要不要劃掉，顯示要劃掉的是哪一筆 |

  3. 「寫入勾選的」：寫完關閉對話框、重新讀這張卡（建新卡時打開新卡）。部分失敗時留在對話框，已寫入的標「已存」、失敗的標原因。
  4. 「丟棄」：確認後丟棄草稿（同三語卡的丟棄）。
- 暫存：編輯與勾選自動存回草稿（沿用 `useDraftEditor`），關掉對話框、重新整理或換裝置都還在。
- 全站的草稿通知（`DraftWatcher`）：人脈卡草稿的「前往」，補舊卡時打開那張卡並開啟對話框，建新卡時到建卡畫面並開啟對話框。
- 驗證：前端只做基本格式提示（日期、必填），以後端的驗證為準。

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `backend/app/services/person_profile_ingest.py` | `ingest()`、`build_prompt()` 加 `mode` 與用途鍵；建新卡的可填欄位；`proposals_from()` 支援沒有互動與稱呼；新增 `apply_proposals()`（從 bot 搬來）；`FILL_ROUTE` |
| `backend/app/templates/prompts/anki/Person_Profile_Ingest.j2` | 依 `mode` 分支（D3）；補舊卡的內容不變 |
| `backend/app/schemas/llm/person_profile.py` | 新增 `LLMPersonCreateResult` |
| `backend/app/schemas/person_profile.py` 或新檔 | `PersonFillDraftParams`、`PersonProposalEdit`（寫入時的建議驗證） |
| `backend/app/services/task_handlers/person_profile_handler.py` | 宣告 `DRAFT_PARAMS`、`fill` 能力；`draft_context`、`execute_draft` |
| `backend/app/services/task_handlers/capabilities.py` | `HandlerCapabilities.fill` |
| `backend/app/services/create_session.py` | `finish_apply`（更新 `note_id` 與已寫入項目） |
| `backend/app/api/drafts.py` | `POST /drafts/{id}/apply` |
| `backend/app/bot/handlers/person_profile.py` | 改呼叫 `apply_proposals()`，行為不變 |
| `frontend/src/components/person/PersonFillDialog.tsx`（新） | 對話框：輸入、生成中、建議清單、寫入、丟棄 |
| `frontend/src/components/person/ProposalEditor.tsx`（新） | 各種建議的勾選與編輯 |
| `frontend/src/components/cards/CardViewPanel.tsx` | 人脈卡的「AI 整理」按鈕 |
| 建卡畫面（`CreatePanelBody` 等） | 人脈卡的「用一段話建立」入口 |
| `frontend/src/components/shared/DraftWatcher.tsx`、`lib/serverDraft.ts` | 人脈卡草稿的「前往」 |
| `frontend/src/api/client.ts`、`types/api.ts` | `applyDraft`、建議的型別 |
| 測試 | 後端：兩種模式的提示詞內容、`LLMPersonCreateResult`、`proposals_from` 的建新卡情況、`apply_proposals`（部分失敗只補寫、劃掉找不到）、`/drafts/{id}/apply`（建新卡後重試不重建、鎖與版本）、TG 寫入行為不變；前端：建議的編輯與暫存狀態 |

## 5. 驗收標準

- [ ] 補舊卡：輸入一段描述，建議放到正確的位置，內容只有描述裡有的事；每項都能改、能不勾；寫入後卡片正確
- [ ] 建新卡：從一段描述整理出稱呼、姓名、認識的資料與其他內容；建立的卡片與勾選的一致；沒提到見面時沒有互動
- [ ] 關掉對話框、重新整理頁面，草稿（含編輯與勾選）還在；丟棄後就沒了；生成失敗可重試
- [ ] 有人脈卡草稿時不能開始三語卡草稿（反之亦然），提示目前的草稿
- [ ] 工具設定裡 `person_profile.fill` 可以單獨選模型，網頁整理用的是它；TG 仍用 `person_profile`
- [ ] 部分寫入失敗時，再送一次只補寫剩下的；建新卡不會建出第二張
- [ ] TG「用一段話記錄」的行為不變（現有測試通過，實際操作一次）
- [ ] 手機上對話框可用（全螢幕、輸入框不放大頁面）

## 6. 待確認

1. **實作順序**：建議先做補舊卡（幾乎全部沿用 TG），再做建新卡（提示詞分支、`LLMPersonCreateResult`、建卡後補寫）；兩段都在這份計畫內，可以分兩個 PR。
