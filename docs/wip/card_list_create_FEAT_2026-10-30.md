# FEAT — 卡片瀏覽頁直接建卡：清單標題的「＋」打開右欄的建卡表單

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-30（2026-11-03 依 `main` `863c495` 重寫） |
| **性質** | 新增機能設計 + 實作工作項 |
| **狀態** | 🚧 實作完成，待在有 Anki 的環境驗收（2026-11-03） |
| **範圍** | `frontend/src/pages/Cards.tsx`、`frontend/src/components/cards/`（清單標題列的「＋」、新的建卡面板）、`frontend/src/hooks/useCardNavigation.ts`、`frontend/src/lib/cardBrowse.ts`、`frontend/src/pages/CardGenerator.tsx`（把建卡表單與卡片類型選單抽成共用元件）、`frontend/src/components/generate/`（表單與預覽流程加「建立後」的回呼） |
| **不動** | 後端（handler、端點、`create_schema` 都不改）；Card Generator 頁的行為與畫面；各卡片類型的建卡能力；卡片的刪除（見 §8） |
| **PR / 進度** | 分支 `feat/card-list-create`；P1～P4 完成，瀏覽器實測以攔截 API 的假資料進行（見 §7） |
| **關聯文件** | `docs/archive/handlers_api_redesign_FEAT_2026-10-20.md`（`create_schema`、建卡能力宣告、Card Generator 頁 §6）；`docs/archive/trilingual_web_create_FEAT_2026-10-30.md`（預覽流程）；`docs/wip/deck_create_FEAT_2026-10-30.md`（牌組樹新增牌組，與本計畫各自獨立） |

> **重寫說明（2026-11-03）**：第一版寫在 2026-10-20 的舊基底（`f00fc09`）上，當時建卡表單一律送 `{ word }`、有筆記類型下拉。之後 PR #32 改成依 handler 宣告產生表單（`create_schema`）、建卡走 `POST /handlers/{name}/notes`、牌組不能是「全部牌組」，還多了三語卡的預覽流程。舊分支 `feat/cards-create-panel` 照搬舊表單，與現在的 `main` 衝突；本計畫改為重用 Card Generator 現有的表單元件，不再另寫一份。

---

## 1. 問題與動機

建卡（`/generate`）和看卡（`/cards`）是兩個分開的頁面：在卡片瀏覽頁看到某個牌組想補一張卡，要切到 Card Generator、在那裡的牌組樹重選一次牌組，建好後再切回來找新卡。希望在卡片瀏覽頁直接建：清單標題（牌組名）右邊放「＋」，按下後右欄（原本顯示卡面的位置）換成建卡表單，建好後右欄顯示新卡片。

現況（已對照 `main` `863c495`）：

- 建卡表單在 `pages/CardGenerator.tsx`：卡片類型選單 `HandlerPicker`（`:182`）與填表流程 `CreateForm`（`:235`）是頁面內的私有元件；預覽流程是 `components/generate/PreviewCreate.tsx`。兩種流程都吃 `handler`、`selectedDeck`、`deckStatus`，牌組規則（固定牌組、`useDeckStatus` 確認牌組存在、選「全部牌組」不能建）已寫在裡面。
- 填到一半的內容存在 sessionStorage（`generate_form:{handler}`、`generate_tags:{handler}`、`generate_draft_v2:{handler}`）；預覽流程的 LLM 生成用 `mutationKey` 追蹤，離開頁面再回來仍看得到結果。
- 建卡成功只顯示 toast 與 `CreatedNotice`（附「到 Cards 頁查看這個牌組」），回應有 `note_ids`、`deck`、`sync_warning`。
- 卡片瀏覽頁依寬度切換三欄／兩欄／一欄（`SplitLayout`、`lib/pageLayouts.ts` 的 `CARD_LAYOUT`），切換時面板重新掛載，面板狀態放在 `Cards.tsx`。頁面狀態都在網址（`lib/cardBrowse.ts`），導覽在 `hooks/useCardNavigation.ts`；`openNotes`（`:132`）已有「依筆記 ID 找卡片並切換過去」的做法（`browseCards({ q: 'nid:…' })` + `pickNoteCard`）。

## 2. 目標與非目標

**目標**

- G1 清單標題右邊有「＋」；按下後右欄顯示建卡表單，牌組就是左欄目前選的牌組（與 Card Generator 同一套規則：選「全部牌組」或不存在的牌組時說明原因、不能送出）。
- G2 卡片類型、表單欄位、預覽流程與 Card Generator 完全相同：由後端宣告產生，兩頁共用同一份元件。
- G3 建卡成功後清單、牌組樹重新整理，右欄留在建卡表單（清空回預設值）可以接著建；成功提示上可以打開新卡片。
- G4 切換版面、重新整理、到別頁再回來，表單草稿都還在；瀏覽器上一頁可以退出建卡畫面。

**非目標**

- 修改任何卡片類型的建卡行為或後端：表單由宣告產生，本計畫只多一個入口。
- 移除 Card Generator 頁：兩個入口並存。
- 清單每一列的刪除按鈕（§8）。
- 在網頁新增牌組：另見 `deck_create`。

## 3. 設計決策

### D1 建卡畫面記在網址：`new=1`

- `BrowseParams` 加 `creating: boolean`，只有 `new=1` 時為 true。
- `useCardNavigation` 加：
  - `openCreate()`：設 `new=1`，**新增**一筆瀏覽器歷史，按上一頁就回到原本的卡片。
  - `closeCreate()`：移除 `new`（取代目前這筆歷史）。
  - `showCreated(noteIds)`：成功提示上按「查看新卡片」時，查出新筆記的卡片後選取它、移除 `new`、清掉跳轉紀錄，**新增**一筆歷史（上一頁回到建卡畫面），見 D5。
- `card`、`trail` 在建卡畫面中保留，退出時回到原本看的卡片。
- 選牌組、送出搜尋、從清單選卡時一併移除 `new`；換頁、翻面不移除。
- **不寫進記住的位置**：`searchToRemember` 只記 `deck`、`q`、`page`、`card`、`side`。重新整理會留在建卡畫面（網址還在），從側邊欄回來則回到上次看的卡片。
- 放棄的做法：只用元件狀態——重新整理、切換版面會掉，也不能用上一頁退出。

### D2 右欄依 `nav.creating` 換元件

- `Cards.tsx` 的 `viewer` 欄依 `nav.creating` 渲染新的 `CardCreatePanel` 或原本的 `CardViewPanel`，位置、寬度、拖曳記憶都沿用 viewer 這一欄。
- 兩欄、一欄版面時右欄在清單下方：打開建卡畫面時捲到面板；版面模式改變而重新掛載時不再捲（比照 `CardViewPanel` 的 `scrolledCardRef`）。

### D3 共用 Card Generator 的元件，不另寫表單

- 把 `CardGenerator.tsx` 的 `HandlerPicker`、`CreateForm` 與「卡片類型選單 + 依 `flow` 切換表單」那一段抽成 `components/generate/CreatePanelBody.tsx`，兩頁都用：
  - props：`selectedDeck`、`deckStatus`、`onViewCreated?`；卡片類型與選單收合狀態照舊記在 localStorage 的 `generate_handler`、`generate_picker_open`。
  - 兩頁共用同一組鍵：在卡片瀏覽頁選的類型、填到一半的內容，到 Card Generator 也看得到，反之亦然。同一件事只有一份草稿。
- `CreateForm`、`PreviewCreate` 加選填的 `onViewCreated(result)`：有傳時成功提示顯示「查看新卡片」按鈕、按下時呼叫，沒傳時照舊是到 Cards 頁的連結。Card Generator 不傳，行為不變。
- 放棄的做法：卡片瀏覽頁自己寫一份表單（舊分支的做法）——PR #32 之後表單規則很多（schema 欄位、標籤、預覽、422 對回欄位），兩份一定會分歧。

### D4 牌組

- 牌組就是左欄選的 `nav.deck`，狀態用同一個 `useDeckStatus`。選「全部牌組」時表單照樣顯示，`DeckSummary` 說明要先選牌組、送出按鈕停用（與 Card Generator 相同）。
- 不另外記一份牌組：在建卡畫面改選左欄的牌組會退出建卡畫面（D1）。選牌組代表要看那個牌組的卡片；要在別的牌組建，選好後再按一次「＋」。

### D5 建卡成功後留在表單，可以接著建

- 建卡成功後**留在建卡畫面**：表單清空、恢復預設值（schema 的 `default`，標籤清空；預覽流程回到輸入畫面），下方顯示成功提示，可以直接建下一張。這是表單元件原本的行為，卡片瀏覽頁不另外處理。
- 成功提示（`CreatedNotice`）在卡片瀏覽頁把「到 Cards 頁查看這個牌組」的連結換成「查看新卡片」按鈕（`onViewCreated`）；Card Generator 不傳，照舊是連結。
- 按「查看新卡片」才切換：`showCreated(noteIds)` 以 `browseCards({ q: 'nid:…' })` 查卡片、`pickNoteCard` 選第一則筆記 ord 最小的那張（一次建多則筆記時按鈕寫「查看新卡片（第一張）」），退出建卡畫面並**新增**一筆歷史，上一頁回到建卡畫面（做法同 `openNotes`）。查詢期間網址若變了，結果作廢。
- 新卡片不在目前的清單裡（固定牌組的卡片、或有搜尋條件時）也照樣在右欄顯示；查不到卡片時以 toast 說明可搜尋 `nid:…` 找到它。
- 重新整理查詢沿用表單元件既有的 `['graph']`、`['cards']`、`['decks']`、`['tags']`：新卡片出現在左邊的清單裡，也可以直接從清單點。
- 修訂紀錄（2026-11-03）：原本是建卡成功後自動切到新卡片（使用者還停在這一輪建卡畫面時）。連續建好幾張時每次都要再按「＋」，改為留在表單；看新卡片改成使用者自己按。

### D6 「＋」的位置與狀態

- `CardListPanel` 的標題列改成 `flex items-start justify-between gap-2`：標題維持 `break-all` 並加 `min-w-0`，「＋」`shrink-0` 固定在右上角。
- `aria-pressed` 表示是否在建卡畫面，再按一次退出；`aria-label`：「在這個牌組建立卡片」。

## 4. 改動清單

### Frontend

| 檔案 | 改動 |
|---|---|
| `src/lib/cardBrowse.ts` | `BrowseParams` 加 `creating`；`parseBrowseParams` 解析 `new=1`；`searchToRemember` 不變 |
| `src/hooks/useCardNavigation.ts` | 加 `openCreate`、`closeCreate`、`showCreated`；`selectDeck`、`submitSearch`、`selectCard` 一併移除 `new` |
| `src/components/generate/CreatePanelBody.tsx`（新） | 自 `CardGenerator.tsx` 搬來的卡片類型選單、填表流程與流程切換 |
| `src/components/generate/PreviewCreate.tsx` | 加選填的 `onViewCreated` |
| `src/components/generate/formParts.tsx` | `CreatedNotice` 加選填的 `onView`：有傳時顯示「查看新卡片」按鈕，否則照舊是連結 |
| `src/pages/CardGenerator.tsx` | 改用 `CreatePanelBody`，行為不變 |
| `src/components/cards/CardCreatePanel.tsx`（新） | 右欄的建卡面板：外框、關閉按鈕、捲動；內容是 `CreatePanelBody`；成功提示的「查看新卡片」接 `showCreated`（D5） |
| `src/components/cards/CardListPanel.tsx` | 標題列加「＋」（D6）；新增 `creating`、`onToggleCreate` props |
| `src/pages/Cards.tsx` | 依 `nav.creating` 切換右欄元件；接上導覽函式 |

### 測試

- `lib/cardBrowse.test.ts`：`new=1` 解析為 true，其他值（`new=0`、`new=true`、沒有）為 false；`searchToRemember` 不含 `new`。
- 其他是畫面與導覽行為，依 §7 在瀏覽器實測。

## 5. 實作順序

- **P1 網址狀態**：`creating` 與測試；導覽函式。
- **P2 抽共用元件**：`CreatePanelBody`、`onViewCreated`；Card Generator 改用後行為不變（先做這步，確認沒有改壞既有頁面）。
- **P3 右欄與「＋」**：`CardCreatePanel`、`Cards.tsx` 切換、清單標題列。
- **P4 驗證**：測試、lint、build；三種版面在瀏覽器實際操作（§7）。

## 6. 風險與未知

- **兩頁共用草稿**：在卡片瀏覽頁填一半，到 Card Generator 會看到同一份。這是刻意的（D3）；兩頁同時開著同一種卡片的表單時，送出後兩邊都會被清空，個人工具可接受。
- **建卡期間離開**：mutation 在元件卸載後仍會完成（TanStack Query v5），toast 與清草稿照常；建卡成功不會自動切換（D5），所以不會把使用者拉走。驗證：送出後立刻點清單其他卡片，完成時右欄不被拉走、toast 出現。
- **固定牌組的卡片**：新卡片多半不在目前的清單裡，但按「查看新卡片」右欄照樣顯示（D5），屬預期。
- **沒有後端環境**：本機連不到後端與 Anki 時，瀏覽器實測只看得到錯誤狀態；完整建卡流程要在有 Anki 的環境驗收。

## 7. 驗收標準

> 2026-11-03 驗收方式：本機沒有後端與 Anki，以 Playwright 開 Vite 開發伺服器、攔截 `/api/*` 回傳假資料（一個填表流程的卡片類型、兩個牌組含超長牌組名、建卡回應可延遲），在 1600／1000／500 px 寬（三欄／兩欄／一欄）實際操作。勾選的是這樣驗證過的項目；連真實後端、預覽流程（三語卡）的完整建卡尚未驗證。

- [x] 三欄、兩欄、一欄版面下，「＋」都在清單標題右側；長牌組名換行時不被擠掉
- [x] 按「＋」後右欄顯示建卡表單；卡片類型、欄位與 Card Generator 相同；牌組是左欄選的牌組，選「全部牌組」時說明原因、不能送出（假資料只有填表流程，預覽流程未實測）
- [x] 再按一次「＋」、按面板的關閉、或按瀏覽器上一頁，退出建卡畫面並回到原本看的卡片
- [x] 建卡畫面中切換版面（收合側邊欄、縮放視窗），表單內容還在（以縮放視窗驗證，收合側邊欄未個別測）
- [ ] 建卡成功後清單重新整理，右欄留在建卡表單（清空回預設值）；成功提示的「查看新卡片」切到新卡片；送出後改點別張卡，完成時右欄不被拉走
  - 已驗證（假資料）：建卡後留在建卡畫面、欄位清空且預設值恢復、可連續建第二張；「查看新卡片」切到新卡片，上一頁回到建卡畫面、再上一頁回到原本的卡片；送出後改點別張卡，完成時不被拉走、toast 出現；Card Generator 的提示仍是到 Cards 頁的連結
  - 未驗證：清單重新整理後出現新卡（假資料的清單不會變；重新整理沿用表單元件既有的查詢失效）、真實後端與同步 AnkiWeb
- [x] 重新整理後仍在建卡畫面；從側邊欄回到卡片瀏覽頁，回到上次看的卡片而不是建卡畫面
- [x] Card Generator 頁照常可用，選的卡片類型與草稿和卡片瀏覽頁共用
- [x] 前端測試、lint、build 通過：

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

## 8. 後續：清單每一列的刪除（暫緩）

第一版原本也想在清單每一列放刪除按鈕，2026-10-30 查證後暫緩，2026-11-03 重寫時維持暫緩。刪除已經有 handler 宣告的能力（`capabilities.delete`）與 `DELETE /handlers/{name}/notes/{id}`，卡面的「筆記欄位」彈窗就能刪；但 `jp_verb_pair`、`jp_core_verb` 的刪除只開放本機腳本（會一併更新母卡 JSON 與查重紀錄），清單直接放刪除按鈕容易誤刪，要另外設計確認流程。
