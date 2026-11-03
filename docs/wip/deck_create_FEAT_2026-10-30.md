# 網頁新增牌組

日期：2026-10-30　分支：未定（原寫於 `feat/person-profile`，該分支已合併）　狀態：設計中（未實作）

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
