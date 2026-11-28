# FEAT — 收掉 Card Generator 頁：建卡只留在 Cards 頁，清掉只為它存在的程式碼與文檔

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-23 |
| **性質** | 前端頁面移除 + 程式碼／文檔清理 |
| **狀態** | 🚧 實作完成（2026-11-23，§3 全部照建議 A，使用者確認），待 PR 與上線驗收；實作紀錄見 §8 |
| **範圍** | 前端：`/generate` 頁、側欄項目、版面設定與測試、草稿通知的「前往」、只有 Generate 會走到的分支（成功提示的連結、不存的成功提示）、`components/generate/` 目錄名稱、`generate_*` 瀏覽器儲存鍵、`.env.example`；文檔：README 與仍描述現況的文件 |
| **不動** | 建卡本身（`CreatePanelBody`、填表／預覽流程、人脈卡「用一段話建立」、伺服器草稿）；後端 API 與 creator 的 `create.channels`（`GET /handlers` 經 handler 帶出）；牌組樹；Knowledge Graph 的 `VITE_DEFAULT_DECK`；已完成的歷史紀錄（`docs/archive/`、`docs/bk/` 的審查與實作紀錄） |
| **PR / 進度** | 分支 `feat/retire-generate`，4 個 commit 已 rebase 到 `origin/main`（2026-11-28，含 PR #62、#63）；審查修正見 §8；尚未 push、未開 PR |
| **關聯文件** | `docs/archive/card_list_create_FEAT_2026-10-30.md`（Cards 頁建卡，D1：選牌組會退出建卡畫面）、`docs/wip/server_drafts_FEAT_2026-11-04.md`（草稿通知「前往」）、`docs/wip/cards_page_revamp_FEAT_2026-11-23.md` |

---

## 1. 問題與動機

- 使用者問 Cards 頁是不是已經完全涵蓋 Generate 頁，決定收掉 Generate，而且要求盤點乾淨，不留廢棄程式碼或歷史債（包含文檔）。
- 兩頁的建卡功能是同一套元件：牌組樹（`components/shared/DeckTreePanel.tsx`）和建卡內容（`components/generate/CreatePanelBody.tsx`），所以卡片類型、填表、三語卡與外語糾錯的預覽草稿、人脈卡「用一段話建立」、新增牌組、同步 AnkiWeb，在 Cards 頁都已經有。
- 只有 Generate 才有、或指向 Generate 的東西（已定位）：
  - 全站草稿通知的「前往」導到 `/generate`：`components/shared/DraftWatcher.tsx:61-65`。
  - AI 整理對話框裡「前往那份草稿」也導到 `/generate`：`components/person/PersonFillDialog.tsx:136-145`。
  - 一進頁面就是建卡表單，Cards 要先按清單標題的「＋」。
  - 牌組另外記一份（`generate_selectedDeck`），預設是 `VITE_DEFAULT_DECK`。
  - 在 Generate 換牌組時表單不會關；在 Cards 選牌組會退出建卡畫面（`hooks/useCardNavigation.ts:274`，`card_list_create` 計畫書 D1）。
- 只為 Generate 存在的分支：
  - `CreatedNotice` 沒有 `onView` 時顯示「到 Cards 頁查看這個牌組」連結：`components/generate/formParts.tsx:475-500`。
  - `useCreatedNotice` 的 `persist=false`：`hooks/useCreatedNotice.ts:9-27`。
  - `onViewCreated` 是選填，Generate 不傳：`CreatePanelBody.tsx:82`、`PreviewCreate.tsx:154,169`。

## 2. 目標與非目標

**目標**
- G1 移除 `/generate` 頁、側欄的「Card Generator」、`GENERATE_LAYOUT` 與相關測試。
- G2 草稿通知的「前往」、「前往那份草稿」改成打開 Cards 頁的建卡畫面，並回到上次在 Cards 看的牌組。
- G3 只為 Generate 存在的分支刪掉：成功提示一律有「查看新卡片」，一律存在 sessionStorage，`onViewCreated` 改成必填。
- G4 不留帶著 Generate 名字的東西：目錄、儲存鍵、註解、測試說明、`.env.example`。
- G5 仍描述現況的文檔不再提到 Generate 頁；歷史紀錄照舊（見 D6）。

**非目標**
- 不改建卡流程、後端 API、creator 宣告（`create.channels` 的 `web` 指的是「從網頁建」，不是 Generate 頁；PR #63 之後由 creator 宣告，`GET /handlers` 經 handler 帶出）。
- 不整理 `docs/bk/` 整體過時的內容（例如早已不存在的 `POST /cards/generate`）：那是 2026-07 的審查快照，見 D6。

## 3. 設計決策（需要使用者確認的打 ❓）

### D1 移除的程式碼
- 刪除 `pages/CardGenerator.tsx`；`App.tsx` 拿掉 import、側欄項目、`<Route path="/generate">`。
- `lib/pageLayouts.ts` 拿掉 `GENERATE_LAYOUT`，Graph 的 `SIDE_LAYOUT` 保留；註解改成「Graph 兩欄」。
- `lib/pageLayouts.test.ts` 拿掉 Generate 的案例：`it.each([GENERATE_LAYOUT, GRAPH_LAYOUT])` 改成只測 Graph，`panelWidthsKey` 的唯一性測試少一個。

### D2 ❓ 舊網址 `/generate`
現在 App 沒有萬用路由：打不存在的網址，內容區是空白的。iPhone 主畫面 App 的 `start_url` 是 `/`，但 App 停在 `/generate` 時被系統重新載入，就會停在空白頁。
- **A（建議）**：加一條通用的 `<Route path="*" element={<Navigate to="/" replace />} />`。這不是為 Generate 留的轉址，任何打錯或廢掉的網址都回 Dashboard。
- B：專門加 `/generate` → `/cards?new=1` 的轉址（像現有的 `/llm-models` → `/tools`）。這會留下一條 Generate 的歷史路由。
- C：什麼都不加。

### D3 ❓ 草稿通知「前往」去哪裡
草稿只存在伺服器，沒有記牌組（`ServerDraft` 沒有 `deck`）。Generate 靠自己記的 `generate_selectedDeck` 回到原本的牌組；Cards 的牌組在網址裡。
- **A（建議）**：「前往」打開「上次在 Cards 的位置＋建卡畫面」。Cards 已經把位置記在 localStorage（`cards_last_search`，`hooks/useCardNavigation.ts:18`），從側欄回 Cards 時會恢復。新增一個純函式（例如 `lib/cardBrowse.ts` 的 `createFormSearch(remembered)`），在記住的參數加上 `new=1`。`DraftWatcher` 與 `PersonFillDialog` 都改用它。已經在 Cards 頁時，就在目前的網址加 `new=1`。
- B：一律導到 `/cards?new=1`，牌組是「全部牌組」。草稿看得到，但建立前要先選牌組，而且選牌組會關掉建卡畫面（見 D4），體驗差。
- 兩種做法都保留現在的行為：先寫入 `create_handler`（原 `generate_handler`，見 D5），選好那種卡片。

### D4 ❓ 在建卡畫面選牌組要不要關掉表單
`card_list_create` 計畫書 D1 的決定是「選牌組代表要看那個牌組的卡片；要在別的牌組建，選好後再按一次「＋」」。當時還有 Generate 可以「先填表、再換牌組」，現在 Cards 是唯一建卡的地方。
- **A（建議）**：建卡畫面開著時，選牌組只換牌組、表單保持開著。`selectDeck` 在 `creating` 時保留 `new`，搜尋與選卡片仍會退出建卡畫面。手機上的流程會變成：按「＋」→ 發現牌組不對 → 打開牌組選擇換一個 → 表單還在，直接建立。
- B：維持現狀（選牌組會退出建卡畫面）。

### D5 ❓ 目錄與儲存鍵改名
- 目錄 `components/generate/` → `components/create/`。9 個檔案都是建卡用的，`components/person/` 有 3 個檔案從這裡 import。
- 瀏覽器儲存鍵 `generate_*` → `create_*`：
  - localStorage：`generate_handler`、`generate_picker_open`、`generate_form:{handler}`、`generate_tags:{handler}`
  - sessionStorage：`generate_created:{handler}`
- **代價**：上線後，瀏覽器裡填到一半的建卡表單、選過的卡片類型、標籤草稿會遺失一次。伺服器上的草稿（三語卡、外語糾錯、AI 整理）不受影響。
- **A（建議）**：改名，不寫轉移程式碼（不留相容層）。舊鍵與 Generate 自己的 `generate_selectedDeck`、`generate_deck_toggled`、`generate_panel_widths` 一起留在瀏覽器當孤兒資料，幾 KB，不寫清除程式碼（寫了就是要永久留著的舊債）。上線前先把手機上填到一半的表單建完或記下來。
- B：只改目錄，儲存鍵維持 `generate_*`（不遺失資料，但名字留著 Generate）。

### D6 ❓ 文檔的處理範圍
| 類別 | 檔案 | 建議處理 |
|---|---|---|
| 現況說明 | `README.md:48,111`（「三條路由（Dashboard / CardGenerator / KnowledgeGraph）」，本來就過時） | 改成現在的頁面清單 |
| 現況說明 | `frontend/.env.example`：`VITE_DEFAULT_DECK` 的說明「知識圖譜與卡片生成」 | 改成「知識圖譜」 |
| 死設定 | `VITE_DEFAULT_MODEL_FILE`：程式碼沒有任何地方讀它，只剩 `frontend/.env.example`、`src/vite-env.d.ts:13`、本機 `frontend/.env`（「卡片生成預設選中的模型」，舊 Generate 留下的） | 刪除；本機 `frontend/.env` 先備份再刪那一行；正式機是建置期變數，Dockerfile 沒有傳它，不用動 |
| 進行中的計畫書 | `docs/wip/server_drafts_FEAT_2026-11-04.md` 尚未勾選的驗收項目（:392、:406 寫「Card Generator 與卡片瀏覽頁」） | 改成 Cards 頁；已完成的實作紀錄不改 |
| 進行中的計畫書 | `docs/wip/deck_create_FEAT_2026-10-30.md:20,183`、`person_llm_fill_FEAT_2026-11-23.md:98,192`、`cards_page_revamp_FEAT_2026-11-23.md:86`、`server_drafts` 其他段落 | 都是當時做了什麼、測了什麼的紀錄（多數已勾選），不改 |
| 歷史紀錄 | `docs/archive/*`（5 份）、`docs/bk/06～15`（審查發現、實作紀錄） | 不改：寫的是當時的事實，改了反而失真 |
| 審查快照 | `docs/bk/01_Project_Overview.md`、`01_Architecture_and_Structure.md`、`03_Frontend_Architecture.md`、`05_DevOps_and_Deployment.md` | 不改：整份是 2026-07 的快照（還在寫早已不存在的 `POST /cards/generate`、下拉選模型），只改 Generate 的幾行會變成半新半舊。要更新就整份另開計畫 |
| 程式碼註解 | `DeckTreePanel.tsx:78`（三頁共用）、`handlerCapabilities.ts:18,25`（「Generate 頁的呈現」）、`CardCreatePanel.tsx:33,39`、`CreatePanelBody.tsx:2,18`、`PreviewCreate.tsx:2,169`、`formParts.tsx:2,476`、`useCreatedNotice.ts:9-11`、`expressionDraft.ts:3`、`trilingualDraft.ts:3`、`Cards.tsx:6`、`CardGenerator` 的 log 標籤 `[CardGenerator]`（`CreatePanelBody.tsx:402`）、後端 `tests/test_handler_declarations.py:136` 的 docstring | 全部改成現況 |

- **A（建議）**：照上表。
- B：連 `docs/bk/` 的審查快照、`docs/archive/` 也改掉 Generate 的字樣。

### D7 相鄰的舊債（不是 Generate 頁，列出來給使用者決定要不要順手做）
- `README.md:17` 寫「Web `POST /api/v1/cards/generate`」，但這個端點早已不存在。`backend/app/schemas/card.py` 的模組說明也這樣寫；`CardGenerateRequest` 仍被本機腳本 `scripts/local_anki/import_cards_with_llm.py` 使用，不是死碼。
- `backend/app/schemas/card.py` 的 `CardUpdateRequest` 沒有任何地方用到。
- 建議：README 與 `card.py` 的說明改成現況，刪掉 `CardUpdateRequest`。如果不想擴大這次的範圍，就另開一個 FIX。

## 4. 改動清單（以 §3 都選 A 為準）

### Frontend
| 檔案 | 改動 |
|---|---|
| `src/pages/CardGenerator.tsx` | 刪除 |
| `src/App.tsx` | 拿掉 Card Generator 的 import、側欄、路由；加萬用路由（D2） |
| `src/lib/pageLayouts.ts`、`pageLayouts.test.ts` | 拿掉 `GENERATE_LAYOUT` 與它的測試，註解改成只剩 Graph |
| `src/components/generate/` → `src/components/create/` | 用 `git mv` 搬移；`CardCreatePanel.tsx`、`person/OriginalContent.tsx`、`person/PersonFillDialog.tsx`、`person/ProposalEditor.tsx`、`lib/expressionDraft.ts`、`lib/trilingualDraft.ts` 跟著改路徑 |
| `src/components/create/CreatePanelBody.tsx`、`PreviewCreate.tsx` | 儲存鍵改成 `create_*`；`onViewCreated` 改成必填；註解與 log 標籤改成現況 |
| `src/components/create/formParts.tsx` | `CreatedNotice` 的 `onView` 改成必填，拿掉到 Cards 頁的 `Link` 分支（和沒用到的 `Link` import） |
| `src/hooks/useCreatedNotice.ts` | 拿掉 `persist` 參數，一律存在 sessionStorage；鍵改成 `create_created:{handler}` |
| `src/lib/cardBrowse.ts`（＋測試） | 新增 `createFormSearch`：記住的位置加上 `new=1`（D3） |
| `src/hooks/useCardNavigation.ts` | 讀取記住位置的函式要給 `DraftWatcher` 用（匯出或搬到 `lib`）；`selectDeck` 在建卡畫面開著時保留 `new`（D4） |
| `src/components/shared/DraftWatcher.tsx`、`person/PersonFillDialog.tsx` | 「前往」改成打開 Cards 的建卡畫面，寫入 `create_handler` |
| `src/lib/handlerCapabilities.ts`、`DeckTreePanel.tsx`、`Cards.tsx`、`CardCreatePanel.tsx` | 註解改成現況 |
| `src/vite-env.d.ts`、`frontend/.env.example`、本機 `frontend/.env` | 刪 `VITE_DEFAULT_MODEL_FILE`、改 `VITE_DEFAULT_DECK` 的說明（本機 `.env` 先備份） |

### Backend
| 檔案 | 改動 |
|---|---|
| `tests/test_handler_declarations.py` 的 `test_web_creatable_handlers` | docstring「Generate 頁可選」改成「網頁建卡可選」 |
| （D7 選做）`app/schemas/card.py`、`README.md:17` | 說明改成現況，刪掉 `CardUpdateRequest` |

### 文檔
- `README.md`、`docs/wip/server_drafts_FEAT_2026-11-04.md`（未勾選的驗收項目），見 D6。
- 這份計畫書完成後移到 `docs/archive/`。

### 測試
- frontend：
  - `pageLayouts.test.ts` 拿掉 Generate 的案例。
  - `cardBrowse.test.ts` 加 `createFormSearch`：沒有記錄、有牌組與卡片、已經有 `new` 時都要對。
  - D4 的 `selectDeck` 若抽成純函式就加測試，否則在瀏覽器實測。
- backend：只改 docstring，跑 `test_handler_declarations.py` 確認沒有壞。
- 收尾檢查：全專案搜尋 `generate_`、`CardGenerator`、`Card Generator`、`/generate`、`components/generate`、`GENERATE_LAYOUT`，除了 D6 決定保留的歷史文件以外都要是 0 筆（`generate-child-cards` 端點、`generate_child_cards.py` 腳本與 LLM 的 `generate_*` 函式不算）。

## 5. 實作順序
- P1 程式碼：路由、頁面、版面，加上 D3 的「前往」和 D4 的換牌組。先做這部分，Cards 才真的能取代 Generate。
- P2 改名：目錄搬移、儲存鍵、刪掉 Generate 專用的分支。只是機械式的改動，另外一個 commit，review 時好看。
- P3 文檔、註解、`.env.example` 與收尾的全專案搜尋。
- P4 驗證：`npx tsc -b`、eslint、vitest、後端相關 pytest；用假後端在瀏覽器實測手機 375px 與 PC。

## 6. 風險與未知
- **儲存鍵改名會遺失瀏覽器裡填到一半的表單（D5-A）**：上線前請使用者先把手機上填到一半的表單建完。伺服器草稿不受影響。
- **D4 改變了 Cards 已經定下的行為**：在建卡畫面換牌組不再退出。上一頁的行為要實測：換牌組是新增一筆歷史，按上一頁會回到前一個牌組、表單仍開著。
- **「前往」恢復的位置可能帶著卡片（`card=`）**：建卡畫面在右欄，卡片仍會在網址裡，和現在從卡片按「＋」的狀態一樣，沒有問題。
- **iPhone 主畫面 App 停在 `/generate` 時被重新載入**：D2-A 會回到 Dashboard。

## 7. 驗收標準
- [ ] 側欄沒有 Card Generator；打 `/generate` 會回到 Dashboard。
- [ ] 在 Cards 的建卡畫面生成三語卡草稿，切到別頁。生成完成時通知的「前往」會回到原本的牌組、打開建卡畫面，並選好三語對話卡。
- [ ] AI 整理對話框的「前往那份草稿」同上。
- [ ] 在建卡畫面換牌組，表單保持開著，填到一半的內容還在。
- [ ] 建卡成功的提示有「查看新卡片」，切換版面後提示還在。
- [ ] 手機 375px 與 PC 沒有橫向捲動。
- [ ] `npx tsc -b`、eslint、vitest、後端 `test_handler_declarations.py` 都通過。
- [ ] 收尾搜尋（§4 測試的最後一點）除了保留的歷史文件以外都是 0 筆。

## 8. 實作紀錄（2026-11-23）

分支 `feat/retire-generate`（從 `origin/main` 開），計畫書之外三個 commit：

- **P1**：頁面、路由、萬用路由、`GENERATE_LAYOUT`；D3 的「前往」改用 `hooks/useOpenCreateForm.ts` 與 `lib/cardBrowse.ts` 的 `createFormPath`。記住位置的讀寫（`readRememberedSearch`、`writeRememberedSearch`）從 `useCardNavigation` 搬到 `lib/cardBrowse.ts`，讓通知也讀得到。D4 的 `selectDeck` 在建卡畫面開著時保留 `new`。
- **P2**：`git mv components/generate components/create`；儲存鍵集中到 `lib/createStorage.ts`；`onViewCreated` 改成必填，`CreatedNotice` 拿掉連結分支，`useCreatedNotice` 拿掉 `persist`。
- **P3**：註解、README、`frontend/.env.example`、`vite-env.d.ts`，以及本機 `frontend/.env`（先備份成 `frontend/.env.bak-20261123233033`）；`server_drafts` 計畫書未勾選的驗收項目；後端 `test_handler_declarations.py` 的 docstring。
- **D7**：`app/schemas/card.py` 的說明改成現況，刪掉沒有任何地方用到的 `CardUpdateRequest` 與 `CardGenerateResponse`（盤點時發現後者也沒有人用）。README 第 17 行改成實際的建卡端點。

### 盤點時發現、還沒處理的
- `backend/scripts/local_anki/import_cards_with_llm.py` 呼叫已不存在的 `CardService.generate_card`，執行一定失敗（`docs/archive/handlers_api_redesign_FEAT_2026-10-20.md:1044` 已記錄）。它是 `CardGenerateRequest` 唯一的使用者，屬於舊 Card Generator（`POST /cards/generate`）留下的東西。是否連同 `CardGenerateRequest` 刪除，等使用者決定。

### 驗證
- `npx tsc -b`、`npx eslint .`（0 個錯誤；18 個警告都在其他檔案，原本就有）、`npx vitest run`（440 個測試通過，含新增的 `createFormPath` 測試）。
- 後端 `python -m pytest tests -q`：2616 passed。2 個失敗（`test_llm_endpoints.py::test_list_endpoints_only_selectable_ones`、`test_models_menu.py::test_malformed_buttons_are_stale_and_do_nothing[effort-token-mismatch]`）是本機 `backend/.env` 多了一個 `audio` LLM 端點造成的，與這次的改動無關。
- 瀏覽器（假後端，不碰正式 Anki）：
  - 手機 375px：
    - `/generate` 回到首頁。
    - 建卡畫面換牌組，表單保持開著、內容還在。
    - 建立後提示有「查看新卡片」，按了會打開新卡片。
    - 在 Dashboard 等草稿生成，按「前往」會回到上次在卡片頁的位置（`deck=People&card=…&new=1`），並選好對話卡。
    - 在卡片頁的另一個牌組按「前往」，會留在目前的牌組。
    - 沒有橫向捲動。
  - PC 1280px：側欄沒有 Card Generator，沒有橫向捲動。
  - AI 整理對話框的「前往那份草稿」和通知共用 `useOpenCreateForm`，沒有另外在瀏覽器測。

### Rebase 與審查修正（2026-11-28）

rebase 到 `origin/main`（PR #62 模型設定三層、#63 handler 拆出 creator）沒有衝突；main 新加的程式沒有引用 `components/generate/` 或 `generate_*` 儲存鍵。多面向審查（code、errors、tests、comments）修了：

- **「前往」蓋掉建卡畫面的歷史狀態**：用「＋」打開建卡畫面後再按草稿通知的「前往」，原本 `navigate(path, { replace: true })` 會換掉「＋」留下的 `createOpened`，之後按 ✕ 退不回原本的卡片。改成建卡畫面已經開著時不導頁（卡片類型已寫入，建卡畫面經 storage 通知切過去）；判斷抽成 `lib/cardBrowse.ts` 的 `isCreateFormOpen`（看參數內容，不比網址字串），加了測試。
- **`docs/wip/handler_note_type_split_FEAT_2026-11-28.md`**：D6 的 localStorage 鍵改成 `create_*`、第二階段的前端清單改成 `components/create/` 並加上 `useOpenCreateForm.ts`。
- **README「建立 Anki 卡片」那一列**與 **`schemas/card.py` 的模組說明**：寫成現況（填表直接寫入、三語對話卡與外語糾錯走預覽流程、TG 是 `/newcard`），註明 `import_cards_with_llm.py` 目前無法執行（backlog H1）；錯誤格式改說是 `FluencyTidesError` 回傳的格式。backlog H7 一併更新。
- 本計畫書的 `create.channels` 用語改成 creator 宣告、測試改用函式名稱引用、進度欄。
