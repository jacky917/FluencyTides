# 網頁新增牌組

日期：2026-10-30　分支：`feat/deck-create`　狀態：🚧 實作完成（2026-11-13），待連真實 Anki 驗收；實作紀錄見 §9

## 1. 目的

在網頁的牌組樹直接新增牌組（含子牌組），不必回 Anki 電腦版操作。

起因：建卡時要選牌組，但牌組只能在 Anki 裡建。另外牌組樹會出現 Anki 裡不存在的「分類節點」（例如
`FluencyTides::Speaking_Trilingual`：Anki 只有 `FluencyTides::Speaking_Trilingual::buy_flowers`，上兩層不存在），
點到它時建卡頁顯示「牌組已不存在」，讓人看不懂，也沒有辦法在網頁上補建。

## 2. 現況

| 層 | 現有 | 缺 |
|---|---|---|
| `AnkiClient`（`app/infrastructure/anki/client.py`） | `create_deck`、`delete_decks`、`change_deck`、`get_decks`、`get_deck_config` 都已實作 | — |
| 服務層 | 只有建卡時的 `ensure_deck_exists`（自動補建不存在的牌組） | 單獨新增牌組的方法 |
| REST API | `GET /decks`（列出牌組） | 新增牌組的端點 |
| 網頁 | 牌組樹（`DeckTreePanel`，Cards、Card Generator、Knowledge Graph 共用） | 新增的入口 |

## 3. 決定

| 項目 | 決定 | 理由 |
|---|---|---|
| 範圍 | 只做**新增** | 改名、刪除、搬移卡片見 §8 |
| 入口 | 牌組樹：標題列的「＋」新增最上層牌組；每個節點滑過時出現「＋」新增子牌組 | 新牌組通常是某個牌組底下的分類，在樹上原地新增最直覺 |
| 輸入 | 節點下方出現一行輸入框，只輸入新的這一層名稱；也可輸入 `a::b` 一次建多層 | 不用重打上層的完整名稱 |
| 上層 | 建 `A::B::C` 時 Anki 會自動補上 `A`、`A::B` | 順便修好 §1 的「缺上層」 |
| 已存在 | 回 409「牌組已存在」，不當成成功 | 名稱打錯成既有牌組時要讓人知道 |
| 建立後 | 重新載入牌組清單、展開上層、自動選取新牌組 | 建完通常就是要在這個牌組建卡 |
| 同步 | 寫入後同步 AnkiWeb 並等它完成（同建卡）；失敗時牌組仍已建立，顯示提醒 | 手機上才看得到 |
| 分類節點 | Anki 裡不存在的中間節點在樹上以淡色斜體顯示，滑鼠移上去說明「Anki 裡沒有這個牌組，只是分類」；它的「＋」旁多一個「建立這一層」 | 讓人分得出哪些能建卡；能一鍵補建 |
| 頁面 | 三頁的牌組樹都有新增功能 | 元件共用；在哪一頁建都一樣 |

## 4. 名稱規則

後端檢查（前端同樣先擋，訊息一致）：

- 以 `::` 分層；每一層去掉前後空白後不能是空的（擋掉 `a::::b`、`::a`、`a::`）。
- 每一層前後的空白自動去掉（`日本語 :: 面接` → `日本語::面接`）。
- 全名最長 200 字。
- 「全部牌組」可以當名稱：網頁的「全部牌組」以 null 表示，不會衝突。
- 其他字元照 Anki 的規則（Anki 允許的都允許）；Anki 拒絕時把它的錯誤訊息轉給使用者。

## 5. 後端

### 5.1 服務：`app/services/deck_service.py`（新）

```python
class DeckService:
    async def create(self, name: str) -> dict[str, object]:
        """檢查名稱 → 已存在則拋 DeckExistsError → create_deck → 寫入後同步。

        Returns: {"deck_name", "deck_id", "sync_warning"}
        """
```

- 名稱正規化與檢查放在同一個模組的 `normalize_deck_name(name) -> str`（不合格拋 `DeckNameInvalidError`），
  建卡的 `ensure_deck_exists` 也可以之後改用它（本次不動）。
- 同步沿用 `note_service.sync_warning(anki_client)`，提醒文字與建卡相同。

### 5.2 例外：`app/core/exceptions/services.py`

| 例外 | HTTP | error_code |
|---|---|---|
| `DeckNameInvalidError` | 400 | `DECK_NAME_INVALID` |
| `DeckExistsError` | 409 | `DECK_EXISTS` |

AnkiConnect 連不上沿用既有的 502。

### 5.3 端點：`app/api/notes.py`（與 `GET /decks` 放在一起）

```
POST /decks
{"name": "日常會話::日本語面接::二次面接"}

201 {"deck_name": "日常會話::日本語面接::二次面接", "deck_id": 1793..., "sync_warning": null}
400 {"error_code": "DECK_NAME_INVALID", "message": "牌組名稱的第 2 層是空的"}
409 {"error_code": "DECK_EXISTS", "message": "牌組「…」已存在"}
```

回傳的 `deck_name` 是正規化後的名稱，前端用它選取新牌組。

## 6. 前端

- `api/client.ts`：`createDeck(name)`；`types/api.ts`：`CreateDeckResponse`。
- `lib/deckTree.ts`：`DeckNode` 多一個 `exists: boolean`（名稱在 `GET /decks` 清單裡才是 true），
  `buildDeckTree` 補上的中間節點為 false。另加 `validateDeckName(name)`（規則同 §4，回傳錯誤訊息或 null）。
- `components/shared/DeckTreePanel.tsx`：
  - 標題列「重新整理」旁加「＋」（新增最上層）。
  - 節點滑過或鍵盤聚焦時出現「＋」；按下後在該節點下方插入輸入列（自動展開該節點），Enter 建立、Esc 取消、
    失焦且空白時取消。建立中輸入框停用並顯示轉圈。
  - `exists=false` 的節點：淡色斜體、`title` 說明；它的輸入列預設填好它自己的名稱，按 Enter 就是補建這一層。
  - 成功：`invalidateQueries(['decks'])`、展開上層、`onSelect(新牌組)`、toast（有同步提醒時另顯示）。
  - 失敗：錯誤訊息顯示在輸入列下方（400、409），其他錯誤用 toast。
- 手機寬度：「＋」一直顯示（沒有滑過這回事），按鈕大小不小於 24px。
- 建卡頁的提示改寫：選到分類節點時顯示「「X」在 Anki 裡不是牌組（只是分類），請選它底下的牌組，或在牌組樹按
  ＋ 建立它」，而不是「已不存在」（真的被刪掉的牌組仍用「已不存在」）。

## 7. 測試

- 後端：
  - `normalize_deck_name`：去空白、空層、`::` 開頭或結尾、長度上限。
  - `DeckService.create`：已存在回 `DeckExistsError`；成功時呼叫 `create_deck` 並同步；同步失敗時回傳提醒、
    不拋錯；AnkiConnect 錯誤往上拋。
  - API：201 的回應形狀、400、409、需要 API 金鑰。
- 前端：
  - `validateDeckName` 與後端同一組案例。
  - `buildDeckTree` 的 `exists`：清單裡沒有的中間節點為 false。
- 瀏覽器：在三頁各新增一次（最上層、子牌組、一次多層、補建分類節點），確認選取、展開、動畫、錯誤訊息、
  手機寬度。實際建立的測試牌組事後要刪除（在 Anki 裡刪，或請使用者決定）。

## 8. 範圍外

| 功能 | 為什麼不做 |
|---|---|
| 改名 | AnkiConnect 沒有改名的操作，只能「建新牌組 → 搬卡片 → 刪舊牌組」，子牌組與牌組設定要另外處理，風險高 |
| 刪除牌組 | AnkiConnect 刪牌組一定連卡片一起刪，無法復原；在 Anki 裡操作比較安全。若之後要做，只開放刪除空牌組並要確認 |
| 搬移卡片 | 需要在卡片列表多選卡片，另開需求 |
| 牌組設定（每日新卡數等） | 在 Anki 裡調整 |

## 9. 實作紀錄（2026-11-12～13）

### 9.1 與設計的差異與補充

| 項目 | 設計（§3～§6） | 實作 |
|---|---|---|
| 已存在的判斷 | 名稱已存在回 409 | 分兩層。建立前：名稱不分大小寫、不分 Unicode 的組合寫法（`é` 與 `e`＋重音符號）視為同名。建立後：AnkiConnect 的 `createDeck` 遇到已存在的牌組不報錯、直接回傳舊 id，所以再看回傳的 id 是不是原本就有的，是的話也回 409。第二層擋住「我們的比對和 Anki 的規則有出入」與「檢查和建立之間別處建了同名牌組」 |
| 回傳的 `deck_name` | 正規化後的名稱 | Anki 裡實際的名稱（建立後依 id 查回來）。查詢失敗或查不到時改用送出的名稱並記 warning，不當成建立失敗：牌組此時已經建好 |
| 建卡頁的牌組狀態 | 只說提示要改寫 | `useDeckStatus` 多一個 `category` 狀態（Anki 裡沒有、但底下有牌組），和 `missing`（真的不存在）分開；Knowledge Graph 頁對分類節點不再顯示「已不存在」 |
| 輸入列的按鈕 | Enter 建立、Esc 取消 | 另有「✓」「×」兩個按鈕（手機沒有 Esc）；補建分類節點時預填的名稱會全選 |
| 建立中 | 輸入框停用並顯示轉圈 | 另外鎖住所有「＋」與「建立這一層」：換到別的輸入列會把建立中的那一列卸載，結果就沒地方顯示 |
| 失敗訊息 | 400、409 在輸入列下方，其他用 toast | 其他錯誤的標題照 `failureTitle` 的規則：只有 4xx 能確定沒寫入，其餘寫「未完成或結果未知，請重新整理查看」，並重抓牌組清單（牌組可能其實已建好）。失敗後焦點回到輸入框 |
| 牌組清單重抓失敗 | 未提 | 牌組已建好但清單重抓失敗時，另外提示「請按牌組旁的重新整理」，不回報成建立失敗 |
| 長度上限 | 全名最長 200 字 | 前後端都以 Unicode 字元計數（表情符號算一個字），訊息相同 |

### 9.2 檔案

- 後端
  - `app/services/deck_service.py`（新）：`normalize_deck_name`、`DeckService.create`
  - `app/core/exceptions/services.py`、`__init__.py`：`DeckNameInvalidError`（400）、`DeckExistsError`（409）
  - `app/schemas/notes.py`：`CreateDeckRequest`、`CreateDeckResponse`
  - `app/api/notes.py`：`POST /decks`
  - `tests/test_deck_service.py`（新）、`tests/test_handlers_api.py`
- 前端
  - `lib/deckTree.ts`：`DeckNode.exists`、`isCategoryDeck`、`joinDeckName`、`validateDeckName`、`deckCreateErrorPlacement`
  - `hooks/useDeckStatus.ts`：`category` 狀態
  - `lib/createForm.ts`、`components/generate/formParts.tsx`：分類節點的提示（`categoryDeckNotice`）
  - `components/shared/DeckTreePanel.tsx`：「＋」、輸入列（`DeckNameInput`）、分類節點的顯示與「建立這一層」
  - `api/client.ts`、`types/api.ts`：`createDeck`、`CreateDeckResponse`
  - `lib/deckTree.test.ts`、`lib/createForm.test.ts`

### 9.3 驗收

自動檢查（2026-11-13，全部通過）：

```bash
cd backend && python -m ruff check app scripts tests && python -m pytest tests -q
```

```bash
cd frontend && npm run lint && npm test && npm run build
```

- 後端：ruff 通過；pytest 1,920 通過、7 略過。
- 前端：lint 0 錯誤（15 個既有警告）、`tsc --noEmit` 通過、370 個測試通過、build 成功。

瀏覽器實測（2026-11-12～13）：正式的後端程式，只把 Anki 換成假的（`tests/test_handlers_api.py` 的 `ApiAnki`），沒有連真的 Anki、沒有建立任何真的牌組。

- [x] 標題列「＋」新增最上層牌組；建好後選取新牌組、出現提示
- [x] 節點「＋」新增子牌組；輸入 `面接 :: 二次` 一次建兩層，上層自動展開，名稱的空白被整理掉
- [x] 分類節點淡色斜體、滑鼠移上去有說明；「建立這一層」預填並全選它的名稱，Enter 補建後變成一般牌組
- [x] 選到分類節點時，建卡頁顯示「在 Anki 裡不是牌組（只是分類）…」
- [x] 名稱不合格（`a::`）與已存在（`English::TOEIC`）的訊息在輸入列下方；失敗後焦點回到輸入框
- [x] Anki 回傳既有牌組的 id 時回 409「牌組「…」已存在」
- [x] Anki 拒絕（502）時 toast「新增牌組未完成或結果未知，請重新整理查看」
- [x] 同步失敗：牌組已建立，另有同步提醒
- [x] 牌組清單重抓失敗：提示「請按牌組旁的重新整理」
- [x] 建立中轉圈、輸入框停用、其他「＋」都被鎖住
- [x] Esc 取消；空白時失焦取消；有內容時失焦不取消
- [x] 手機寬度（375px）：「＋」一直顯示，24×24px
- [x] Cards、Card Generator、Knowledge Graph 三頁的牌組樹都有新增入口（完整流程只在 Card Generator 走過）
- [ ] **連真實 Anki**：實際建立牌組、Anki 自動補上層、同步 AnkiWeb 後手機看得到。測試建的牌組事後要刪除
- [ ] Anki 對特殊字元（例如 `"`）的處理：會拒絕還是改名，沒有實測
- [ ] Knowledge Graph 頁選到分類節點時，圖譜有沒有包含它底下子牌組的卡片，沒有確認（原本顯示「已不存在」，現在當成一般牌組）

### 9.4 審查（`/multi-review`，2026-11-12）

五個面向（一般、測試、註解、錯誤處理、型別）都沒有 Critical。六項 Important 已修（就是 §9.1 表格裡「已存在的判斷」第二層、`deck_name` 查詢失敗、失敗訊息、建立中鎖定、清單重抓失敗這幾列，以及把輸入列的狀態改成三種情況的聯集型別、拿掉兩個沒被讀取的欄位）。

沒做的建議：

- `deckBlocksForm` 改白名單、`deckBlockedReason` 加窮舉檢查：下次再加牌組狀態時漏補會編譯失敗。
- 後端 `CreateDeckResponse.sync_warning` 拿掉預設值，和同檔其他回應一致。
- `buildDeckTree` 的說明補上 `exists`；`useDeckStatus` 的英文說明同步；409 的 API 說明補上「比對不分大小寫」。
- 「建好後要展開哪些上層」抽成純函式並補測試。
- 前端的 `trim()` 與後端的 `strip()` 認定的空白字元有極少數不同（例如 U+FEFF）；前端先擋、後端為準，只會讓少數字元的訊息不同。
