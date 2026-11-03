# FEAT — JSON 欄位的表單編輯：前端用表單、送出時才是 JSON，後端宣告格式並再次檢查

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-20 |
| **性質** | 設計書；已實作（實作紀錄見 §9） |
| **狀態** | ✅ 完成（2026-10-24 以人脈卡為第一個使用者實作，2026-10-31 隨 PR #32 合併；2026-11-03 歸檔）。單字卡 `_v2` 的試點（D6）沒有做，與其他筆記類型的開放一起移到 `docs/wip/backlog.md` §4 |
| **範圍** | handler 的 JSON 欄位宣告（每筆資料的格式）；`GET /handlers` 回傳格式；handler 讀取回傳解析後的 JSON 欄位；新增整欄寫入端點；前端的清單編輯元件；試點 `TOEIC_Coach_Dark_v2` 的 3 個 JSON 欄位 |
| **不動** | TG 與本機腳本寫入 JSON 欄位的方式；`AnkiJsonFieldManager` 的讀寫邏輯；卡片模板 |
| **PR / 進度** | [#32](https://github.com/jacky917/FluencyTides/pull/32)（`feat/person-profile`，2026-10-31 合併，merge `a1f6ea0`）；實作 commit `107ef75`（人脈卡計畫書 P4） |
| **關聯文件** | `docs/archive/handlers_api_redesign_FEAT_2026-10-20.md`（服務層、能力、`/handlers/{name}/notes/...`；先在那份宣告 JSON 欄位的**名稱**）；`docs/wip/media_upload_FEAT_2026-10-20.md`（媒體上傳；實作後 `media` 改為可上傳的 `audio`、`image`） |

---

## 1. 問題

- 不少筆記類型把一組資料存成 JSON 陣列放在一個欄位裡：
  - `TOEIC_Coach_Dark_v2`：`Word_Family_JSON`、`Synonyms_JSON`、`Collocations_JSON`
  - 對話卡、三語卡：`Recordings`、`References`、`Prompt_Audios`（三語卡分語言）
  - 外語糾錯、jp 類：各自的 `*_JSON`、`*_Audios` 欄位
- 網頁現在只能把整欄當成一串文字看。前端用 `isStructuredField` 依內容猜哪些是 JSON 欄位並設成唯讀；後端完全不擋，直接改字串很容易寫壞。
- 寫壞的後果：卡片模板解析失敗（`_v2` 的模板包在 `try` 裡，那一段會整段不顯示），TG 之後讀這個欄位追加資料也可能出錯。
- 同一種欄位的儲存格式並不一致：經 TG 語音流程寫入的是 HTML 跳脫過的（`&quot;`），匯入腳本直寫的是原始 JSON。只能經 `AnkiJsonFieldManager` 讀寫（S065）。

## 2. 目標與非目標

**目標**

- G1 **前端不直接編輯 JSON**：每個 JSON 欄位顯示成一個清單，每一筆是一張小表單；新增、修改、刪除、調整順序都在表單上做，按「儲存」時才組成 JSON 陣列送出。
- G2 **格式由後端宣告**：handler 宣告每個 JSON 欄位每一筆資料的格式（Pydantic 模型），`GET /handlers` 回傳給前端產生表單；送出時後端用同一份模型再驗證一次。
- G3 **不會蓋掉別處的修改**：網頁編輯期間，TG 或腳本若改了同一欄，網頁儲存時要能發現並拒絕，而不是默默覆蓋。
- G4 試點：`TOEIC_Coach_Dark_v2` 的 3 個 JSON 欄位。

**非目標**

- 資料裡的媒體（音檔、頭像檔名）的上傳：見 `media_upload_FEAT_2026-10-20.md`，這次在表單上唯讀顯示。
- 巢狀清單的編輯（例如 `References` 每一筆裡的 `audios` 陣列）：這次唯讀顯示摘要。
- 改同義詞、搭配詞時同步知識圖譜的關聯。
- TG、本機腳本改用這套寫入。

## 3. 設計

### D1 宣告：handler 的 `JSON_FIELDS`

```python
class WordEntry(BaseModel):                 # Word_Family_JSON、Synonyms_JSON 的一筆
    model_config = ConfigDict(extra="allow")   # 保留模型沒列出的鍵，不丟資料
    word: str = Field(title="單字")
    phonetic: str = Field("", title="音標")
    pos: str = Field("", title="詞性")
    translation: str = Field("", title="翻譯")
    nuance: str = Field("", title="語感說明", json_schema_extra={"x-ui": "textarea"})
    example: str = Field("", title="例句", json_schema_extra={"x-ui": "textarea"})
    example_translation: str = Field("", title="例句翻譯", json_schema_extra={"x-ui": "textarea"})

class CollocationEntry(BaseModel):          # Collocations_JSON 的一筆
    model_config = ConfigDict(extra="allow")
    en: str = Field(title="英文")
    tw: str = Field(title="中文")

class VocabularyMiningHandler(BaseHandler):
    JSON_FIELDS = {
        "Word_Family_JSON":  JsonFieldSpec(item=WordEntry, title="字族"),
        "Synonyms_JSON":     JsonFieldSpec(item=WordEntry, title="同義詞"),
        "Collocations_JSON": JsonFieldSpec(item=CollocationEntry, title="搭配詞"),
    }
```

- 格式是照實機 Anki 裡 `_v2` 的資料寫的（例：`anyway` 那張的 `Synonyms_JSON` 每筆有 `word`、`phonetic`、`pos`、`translation`、`nuance`、`example`、`example_translation`；`Collocations_JSON` 每筆有 `en`、`tw`）。
- `extra="allow"`：既有資料若有模型沒列出的鍵（例如舊版 LLM 多產生的欄位），讀進來、存回去都原樣保留，不會因為網頁編輯而遺失。
- 欄位的 `x-ui` 沿用 handlers 計畫書的約定：`textarea`；本設計新增 `readonly`（只顯示）與 `media`（媒體檔名；`media_upload_FEAT_2026-10-20.md` 實作前唯讀）。巢狀的清單、物件自動視為唯讀。
- 在 handlers 計畫書裡，handler 先宣告 JSON 欄位的**名稱**（不含格式），讓建卡表單不列這些欄位、`PATCH` 拒絕直接改它們、前端不再用猜的；本設計把名稱擴充成完整的 `JsonFieldSpec`。

### D2 能力

- 新增 handler 層級的能力 `update_json`：`{"supported": true}`。與 `update_fields`（一般文字欄位）分開，因為 JSON 編輯是另外實作、另外驗收的（照 handlers 計畫書 G3 逐一開放）。
- `GET /handlers` 多回傳 `json_fields`：

```json
"json_fields": {
  "Synonyms_JSON": {
    "title": "同義詞",
    "item_schema": { "type": "object", "properties": { "word": { "type": "string", "title": "單字" } }, "required": ["word"] }
  }
}
```

- 註冊檢查：`update_json.supported` 為 true 時，`JSON_FIELDS` 不可為空，而且每個欄位名都存在於 handler 負責的筆記類型（啟動時不連 Anki，以 `app/anki_models/*.json` 的 `inOrderFields` 比對）。

### D3 讀取

handler 讀取（`GET /handlers/{name}/notes/{note_id}`）多回傳解析後的 JSON 欄位：

```json
"json_fields": {
  "Synonyms_JSON": {
    "items": [ { "word": "regardless", "phonetic": "/rɪˈɡɑːrdləs/", "pos": "adv.", "translation": "不管怎樣" } ],
    "version": "9f2c…",
    "error": null
  }
}
```

- `items` 用 `AnkiJsonFieldManager.parse_field_string` 解析，兩種儲存格式（跳脫與否）都能讀。
- `version`：這一欄**原始字串**的雜湊（SHA-256 前 16 碼），寫入時用來偵測衝突（D4）。
- 解析失敗（欄位本來就壞了）時 `items` 為空、`error` 說明原因，前端顯示錯誤並停用這一欄的編輯，避免存檔把壞資料整個覆蓋掉。
- 一般欄位的 `fields` 照舊回傳原始字串（顯示卡面、除錯用）。

### D4 寫入：整欄送出，附版本檢查

```text
PUT /api/v1/handlers/{name}/notes/{note_id}/json/{field}
```

```json
{ "items": [ { "word": "regardless", "translation": "不管怎樣" } ], "version": "9f2c…" }
```

服務層的處理順序：

1. 筆記歸這個 handler、`update_json` 開放、`{field}` 在 `JSON_FIELDS` 裡，否則 400。
2. 讀出目前的欄位字串算雜湊，和請求的 `version` 不同就回 **409 `JSON_FIELD_CONFLICT`**（代表編輯期間 TG、腳本或另一個分頁改過），不寫入。
3. 以 `TypeAdapter(list[Item])` 驗證整個陣列；失敗回 422，`loc` 為 `["body", "items", 第幾筆, 鍵名]`，前端可以把錯誤標在那一筆的那個欄位旁。
4. 經 `AnkiJsonFieldManager.update_field` 寫回（統一跳脫，與 TG 語音流程的格式一致，S065）。
5. 寫入後同步，回傳更新後的這一欄（新的 `items`、`version`）與 `sync_warning`。

為什麼整欄送出，而不是逐筆增刪改：

- 表單編輯常是一次動好幾筆（改兩筆、刪一筆、調整順序），一次存檔最直覺；逐筆送出時，前一筆成功、後一筆失敗會留下半套結果。
- 逐筆操作要用位置指定是哪一筆，位置會因別處的增刪而位移（TG 刪錄音就遇過刪到下一筆的問題）。整欄送出加上版本檢查，任何位移都會被 409 擋下。
- 代價是兩邊同時編輯時，後存檔的一方要重新載入再改；對個人使用的工具可以接受。

格式變化：`_v2` 這三欄現在是匯入時寫入的原始 JSON，經網頁儲存一次後會變成跳脫過的格式。卡片模板以 `textContent` 讀取（`TOEIC_Coach_Dark_v2_back.html:88`），兩種格式都能正確顯示；repo 內也沒有其他程式直接 `json.loads` 這三欄（只有匯入腳本寫入它們）。

### D5 前端：清單編輯元件

- Cards 頁的筆記詳情裡，每個 JSON 欄位顯示成一個區塊：標題用 `JsonFieldSpec.title`，下面是一筆一筆的卡片。
- 每一筆依 `item_schema` 產生表單：`string` → 單行輸入；`x-ui: textarea` → 多行；`number`、`integer` → 數字輸入；`enum` → 下拉；`readonly`、`media`、巢狀資料 → 只顯示；`required` 的欄位標示必填。
- 操作：新增一筆（加在最後）、刪除、上移、下移；有未儲存的變更時，區塊標示「未儲存」，離開時提醒。
- 「儲存」把整個清單組成陣列，連同讀取時拿到的 `version` 送出。
  - 422：錯誤標在對應那一筆的欄位旁。
  - 409：提示「這一欄在別處被改過」，提供「重新載入」（放棄本地修改）。
  - 成功：用回應更新畫面；有 `sync_warning` 時一併顯示。
- `update_json` 不開放、或欄位解析失敗時，區塊唯讀並顯示原因。

### D6 試點與之後的類型

- 試點：`TOEIC_Coach_Dark_v2` 的 `Word_Family_JSON`、`Synonyms_JSON`、`Collocations_JSON`（資料平坦，沒有媒體）。
- 之後依序開放時要先處理的：
  - 對話卡 `Recordings`：每筆含 `audio`（媒體）、`score`（數字）、`comment`（含 HTML 標記）；TG 會在背景追加，衝突機會較高。
  - `References`：每筆含巢狀的 `audios` 陣列。
  - 三語卡：`Recordings_ZH/JA/EN` 等同一格式的多個欄位，`JsonFieldSpec` 可共用模型。

## 4. 測試

- 註冊檢查：宣告開放但 `JSON_FIELDS` 為空、欄位名不在筆記類型裡 → 啟動失敗。
- 讀取：跳脫與未跳脫兩種格式都解析成相同的 `items`；解析失敗時回 `error`、`items` 為空；`version` 對同一字串穩定。
- 寫入：
  - `version` 不符 → 409，Anki 未被寫入。
  - 缺必填、型別錯 → 422，`loc` 指到第幾筆、哪個鍵。
  - 模型沒列出的鍵原樣保留。
  - 寫入後欄位為跳脫格式，`parse_field_string` 讀回與送出的內容相同。
  - 欄位不在 `JSON_FIELDS`、能力不開放、筆記不歸這個 handler → 400。
- 前端：`item_schema` 轉表單欄位的純函式；清單的新增、刪除、上下移與「未儲存」狀態；422 錯誤對應到那一筆的欄位。

## 5. 實作順序

1. （handlers 計畫書內）handler 宣告 JSON 欄位名稱；建卡表單不列、`PATCH` 拒絕、前端改看宣告。
2. `JsonFieldSpec` 與註冊檢查；`_v2` 的兩個資料模型。
3. handler 讀取回傳 `json_fields`；`PUT .../json/{field}` 與服務層的版本檢查、驗證、寫入。
4. 前端清單編輯元件，先接 `_v2`。

每一步結束都要通過 handlers 計畫書 §9 的檢查命令。

## 6. 驗收

- [ ] Cards 頁打開一張 `_v2` 單字卡，三個 JSON 欄位顯示成清單表單，不出現原始 JSON
- [ ] 改一筆同義詞的翻譯、刪一筆搭配詞、新增一筆字族、調整順序後儲存；卡片在 Anki 與 AnkiWeb 上正確顯示
- [ ] 必填欄位留空時無法儲存，錯誤標在那一筆旁
- [ ] 開兩個分頁編輯同一欄，後存檔的那個收到「在別處被改過」，選重新載入後看到前一個分頁的修改
- [ ] 既有資料裡模型沒列出的鍵，經網頁儲存後仍在

## 7. 風險

| # | 風險 | 應對 |
|---|---|---|
| R1 | 資料模型和實際資料不符，儲存時把既有卡驗證成失敗 | 模型照實機資料寫，非必要的鍵都給預設值；`extra="allow"` 保留未知鍵；試點前對 800 張 `_v2` 跑一次「讀出→驗證」的檢查，列出不通過的筆記 |
| R2 | 儲存後格式從原始 JSON 變成跳脫格式 | 模板以 `textContent` 讀取，兩種都正確；repo 內沒有其他程式直接解析這三欄 |
| R3 | 兩邊同時編輯，後存檔的一方要重來 | 409 加重新載入；個人工具可接受 |

## 8. 待決事項

1. **`version` 用欄位雜湊，還是整張筆記的修改時間？** 雜湊只看這一欄，別的欄位被改不會誤判衝突；Anki 筆記的修改時間（`mod`）目前 `AnkiNoteInfo` 沒有收進來。建議雜湊。
2. **儲存是「一欄一存」還是「整張筆記一次存」？** 本設計是一欄一存，衝突範圍小；若希望一般欄位和 JSON 欄位一起按一次儲存，前端要依序呼叫 `PATCH` 與各欄的 `PUT`，中途失敗會留下部分已存。建議一欄一存。
3. 同義詞、搭配詞改動後要不要同步知識圖譜的關聯（本設計的非目標，之後再決定）。

## 9. 實作紀錄（2026-11-03 歸檔時補）

> **已合併**：[PR #32](https://github.com/jacky917/FluencyTides/pull/32) · merge `a1f6ea0` · 2026-10-31；實作 commit `107ef75`

實作是在人脈卡計畫書（`docs/archive/person_profile_card_FEAT_2026-10-23.md` P4）裡做的，與本設計的差異：

| 項目 | 本設計 | 實際 |
|---|---|---|
| 格式宣告 | `JSON_FIELDS` 從名稱改成 `{名稱: JsonFieldSpec}` | 另開類別屬性 `JSON_FIELD_SPECS`；`JSON_FIELDS` 維持名稱的 tuple，其他 handler 不必改。註冊檢查要求 `JSON_FIELD_SPECS` 的鍵都在 `JSON_FIELDS` 內（`task_handlers/registry.py`） |
| `GET /handlers` | `json_fields` 改成含格式的物件 | 多一個鍵 `json_field_specs`；`json_fields` 照舊是名稱清單，前端的唯讀判斷不受影響 |
| 寫入後處理 | 無 | 新增 `after_json_update`，讓 handler 在整欄寫入後維護相關欄位（人脈卡更新 `Updated_At`、`Last_Contact`） |
| 第一個使用者 | 單字卡 `TOEIC_Coach_Dark_v2` 的 3 個欄位 | 人脈卡 `Person_Profile_Dark` 的 6 種 JSON 欄位；`_v2` 沒有開放 `update_json` |

照設計實作的部分：`PUT /handlers/{name}/notes/{note_id}/json/{field}` 整欄寫入（`api/handlers.py`）；`version` 為欄位原始字串 SHA-256 的前 16 碼，不符時回 409 `JSON_FIELD_CONFLICT`（`NoteService.update_json`、`JsonFieldConflictError`）；讀取時回傳解析後的 `json_fields`；前端清單編輯元件 `frontend/src/components/JsonFieldEditor.tsx`。

§6 的驗收是以 `_v2` 寫的，沒有照做；人脈卡的驗收見人脈卡計畫書。
