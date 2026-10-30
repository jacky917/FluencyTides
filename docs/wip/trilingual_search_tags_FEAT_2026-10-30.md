# 三語對話卡的查詢與建卡標籤（含標籤推薦）

日期：2026-10-30　分支：`feat/person-profile`　狀態：設計中（未實作）

## 1. 目的

1. **查詢**：在網頁卡片頁用欄位搜尋找三語對話卡（例如「說法包含『恐れ入りますが』」「標籤是 `Interview`」）。
2. **建卡加標籤**：網頁建卡時可以加自己的標籤（目前只會加固定的 `Speaking_Trilingual`、`HandlerGenerated`）。
3. **標籤推薦**：建卡時推薦合適的標籤，並讓同一個主題維持同一種寫法（避免 `Interview`、`面接`、`interview` 並存）。

## 2. 現況

- 欄位搜尋（`docs/wip/handler_field_search_FEAT_2026-10-25.md`）目前只開放人脈卡，只能比對欄位內容，不能用標籤當條件。
- 三語卡 34 張，有主題意義的標籤：`BusinessJapanese`（26）、`Interview`（26）、`最終面接`（14）、`逆質問`（10）、
  `FlowerShop`（6）、`志望動機`（5）、`実績`（4）等；排序用的 `Step1`～`Step4`、`つなぎ1`、`締め`；系統加的
  `TelegramBot`、`Sample`、`HandlerGenerated`、`Speaking_Trilingual`。
- 整個 Anki 有 112 個標籤，不少是系統用的（`LLM::(claude-code)opus@high`、`AI內文不遮名字`、`Auto_Migrated` 等）。
- `AnkiClient` 已有 `get_tags`、`add_tags`、`remove_tags`。

## 3. 決定

| 項目 | 決定 | 理由 |
|---|---|---|
| 查詢 | 三語卡開放欄位搜尋，宣告欄位按鈕（§4.1） | 沿用人脈卡的框架 |
| 標籤條件 | 欄位搜尋框架多一種「標籤」條件，**通用**（人脈卡也能用） | 標籤是 Anki 通用的，不該每種卡各做一次 |
| 建卡加標籤 | 建卡 API 多一個 `tags`；**通用**於所有能從網頁建卡的卡片（單字卡、人脈卡、三語卡） | 同上；成本幾乎一樣 |
| 寫入方式 | 建卡成功後由服務層呼叫 `addTags` 加上，再同步（§5.3） | 不用改任何卡片類型的建卡程式 |
| 推薦 | A＋B：既有標籤自動完成＋三語卡草稿由 LLM 順便建議（§6） | A 不花錢且維持寫法一致；B 不多花一次呼叫 |
| 不推薦的標籤 | 系統標籤與排序標籤（`Step1` 等）不推薦，系統標籤也不能手動加 | 系統標籤由程式管；排序標籤 LLM 判斷不了 |
| 做法 C（向量比對相似卡） | 不做 | 34 張卡用不到，成本高 |

## 4. 查詢

### 4.1 三語卡的欄位按鈕（`SEARCH_FIELDS`）

| 鍵 | 按鈕 | 比對的欄位 |
|---|---|---|
| `prompt` | 情境 | `Prompt` |
| `context` | 補充 | `Context` |
| `refs` | 說法（三語） | `References_ZH`、`References_JA`、`References_EN` 任一個 |
| `zh` | 中文 | `References_ZH` |
| `ja` | 日文 | `References_JA` |
| `en` | 英文 | `References_EN` |

- 參考答案欄位是 JSON，句子與說明都在裡面，「包含」比對會同時找到句子與說明，這是預期的。
- 既有規則照舊：關鍵字照字面比對、同時比對 HTML 轉義後的寫法（`&`、`<`）。
- 熟識度、是否錄過音這類 JSON 裡的值不做成條件（文字比對做不準）。

### 4.2 標籤條件（通用）

- 欄位搜尋多一個保留鍵 `#`（按鈕名「標籤」），與 `*`（任何欄位）並列；註冊表檢查 `SEARCH_FIELDS` 不能用 `#`。
- 查詢轉成 Anki 的 `tag:值`：比對這個標籤**和它的子標籤**（`tag:Interview` 也找到 `Interview::Final`），
  不做部分比對（打字時由自動完成挑既有標籤，§6.1）。值照既有的 `escape_search_value` 跳脫。
- 開放欄位搜尋的卡片（人脈卡、三語卡）都有這個按鈕。
- 前端：按鈕列多「標籤」；輸入框以 `<datalist>` 列出這種卡片用過的標籤（§6.1 的 API）。

## 5. 建卡加標籤

### 5.1 標籤規則：`app/services/tag_rules.py`（新）

```python
SYSTEM_TAGS: frozenset[str]   # HandlerGenerated、TelegramBot、Sample、ManualCreated、Auto_Generated、
                              # Auto_Migrated、Migrated、Notion_Sync、leech、__E2E_TEMP__、
                              # 各 handler 建卡固定加的標籤（Speaking_Trilingual、PersonProfile…）、
                              # 開關標籤（AI內文不遮名字）
SYSTEM_TAG_PREFIXES = ("LLM::",)

def is_system_tag(tag: str) -> bool
def normalize_tags(tags: list[str]) -> list[str]
    # 去前後空白、去重（保留順序）、略過空字串；
    # 含空白（Anki 標籤不能有空白）、系統標籤、超過 100 字 → TagInvalidError（訊息指出哪一個）
    # 最多 20 個
```

各 handler 建卡固定加的標籤由 `SYSTEM_TAGS` 從註冊表收集（`CREATE_TAGS`，三語卡改用 `CREATE_TAGS` 宣告，
不再寫死在 `execute_create` 裡），新增卡片類型時不用手動維護清單。

### 5.2 API

- `POST /handlers/{name}/notes` 的請求多 `tags: list[str]`（預設空）。
- 不合格回 400 `TAG_INVALID`。

### 5.3 寫入

`NoteService.create`：`normalize_tags` → `execute_create` → 有標籤時 `add_tags(note_ids, tags)` → 同步。

- 不把標籤傳進 `execute_create`：各卡片類型的建卡程式都不用改（考慮過改 `execute_create` 的簽名讓標籤跟筆記
  一起寫入，要動 6 個 handler，換來的只是「加標籤失敗」這個極少見情況的原子性，不划算）。
- `addTags` 失敗時（卡片已建立）：照樣同步，回應的 `sync_warning` 寫明「卡片已建立，但標籤沒加上：…」，
  不回錯誤（避免使用者以為沒建成而重建一張）。

## 6. 標籤推薦

### 6.1 A：既有標籤自動完成（通用）

- `GET /handlers/{name}/tags` → `[{"tag": "Interview", "count": 26}, …]`：這個 handler 的筆記類型用過的標籤，
  依次數排序，排除系統標籤（`is_system_tag`）。
  - 實作：`findNotes`（筆記類型查詢）→ `notesInfo` → 統計。三語卡 34 張、人脈卡幾十張，不需要快取。
- 用在三個地方：建卡表單的標籤輸入、三語預覽的標籤輸入、欄位搜尋的標籤條件。

### 6.2 B：三語卡草稿由 LLM 建議（只有預覽流程）

- 生成草稿前先取 §6.1 的清單（前 50 個），放進提示詞。
- LLM 輸出多一個 `tags`（0–3 個），規則寫在提示詞：
  - 優先從既有標籤挑，**照原本的寫法**；都不合適才提新的。
  - 主題層級的標籤（場景、領域，例如 `FlowerShop`、`Interview`）；不要排序用的（`Step1`、`つなぎ1`、`締め`）、
    不要系統標籤、不要熟識度或語言這類卡片本身已有的資訊。
  - 新標籤的寫法跟既有標籤的風格一致（英文單字用 PascalCase、日文照原文）；不能有空白。
- 後端整理：不合格式的（含空白等）丟掉；比對清單標出哪些是既有的。
- 草稿回應多 `suggested_tags: [{"tag": "Interview", "existing": true}, {"tag": "HairSalon", "existing": false}]`
  （不放進建立參數，因為標籤是建卡請求層級的欄位）。
  - `execute_draft` 回傳的 dict 可帶 `tags` 鍵，`NoteService.draft` 取出來整理成 `suggested_tags`。

### 6.3 前端：標籤輸入元件 `components/generate/TagInput.tsx`（新）

- 已選的標籤以小方塊顯示，點 × 移除；輸入框 Enter 或逗號加入，含空白時提示「標籤不能有空白」。
- 自動完成：§6.1 的清單（顯示次數）。
- 三語預覽：草稿的建議標籤預設勾選；既有的實線，新的虛線並標「新」，提醒這會新增一個標籤。
- 填表流程（單字卡、人脈卡）：只有輸入與自動完成。
- 保存：與表單、草稿一樣存在分頁的 sessionStorage，「清除」一起清掉。草稿的保存格式因此改變，鍵名改成
  `generate_draft_v2:`（依先前的約定，改格式就換鍵名，舊資料不再讀取）。

## 7. 測試

- 後端
  - `normalize_tags`：去空白、去重、空白字元、系統標籤、`LLM::` 前綴、長度與數量上限。
  - `SYSTEM_TAGS` 包含各 handler 的 `CREATE_TAGS`。
  - 欄位搜尋：三語卡各按鈕組出的查詢；標籤條件 `tag:` 的跳脫；`SEARCH_FIELDS` 用 `#` 時註冊失敗。
  - 建卡：帶標籤時呼叫 `add_tags` 再同步；`add_tags` 失敗時仍回 201 並帶提醒；不合格標籤回 400 且沒有建卡。
  - `GET /handlers/{name}/tags`：統計、排序、排除系統標籤。
  - 草稿：LLM 的 `tags` 整理成 `suggested_tags`（既有／新、丟掉不合格的）；提示詞在有／沒有既有標籤時都能渲染。
- 前端：`TagInput` 的加入、移除、去重、空白提示；欄位搜尋的「標籤」按鈕與條件。
- 實測：用真實 LLM 跑幾個場景，看建議的標籤是否沿用既有寫法；在三語卡用標籤與說法搜尋。

## 8. 範圍外

- 在網頁修改或刪除既有卡片的標籤（之後可以接在卡片詳情）。
- 標籤改名、合併（例如把 `interview` 併進 `Interview`）。
- TG 建卡加標籤。
