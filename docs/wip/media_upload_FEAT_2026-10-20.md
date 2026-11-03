# FEAT — 網頁上傳媒體到 Anki：先上傳取得檔名，再填進建卡、JSON 欄位或動作

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-20 |
| **性質** | 設計書（實作排在 `handlers_api_redesign_FEAT_2026-10-20.md` 之後） |
| **狀態** | 🗒️ 討論中 |
| **範圍** | 新增 `POST /api/v1/media`（上傳單一檔案到 Anki 媒體資料夾）；檔名產生、格式轉換、類型與大小限制；handler 宣告哪些參數或欄位是媒體；前端的上傳與錄音元件 |
| **不動** | TG 上傳語音、頭像的流程；本機腳本經 AnkiConnect 上傳媒體的方式；`GET /anki-media/{filename}`（讀取） |
| **關聯文件** | `docs/archive/handlers_api_redesign_FEAT_2026-10-20.md` D9（本設計的方向最初記在那裡）、D3（媒體欄位的名稱宣告）；`docs/archive/json_field_editor_FEAT_2026-10-20.md`（JSON 欄位裡的媒體）；`docs/archive/web_ui_parity_FEAT_2026-10-18.md` 階段 D（上傳音檔與頭像） |

---

## 1. 現況

**誰在寫媒體到 Anki**

| 來源 | 做法 | 檔名 |
|---|---|---|
| TG 錄音評分 | 下載 TG 語音（`.ogg`），經 `AnkiClient.store_media_file` 存進 Anki，再以 `add_recording` 寫進 `Recordings` | `rec_{Card_ID}_{UTC 時間}.ogg`（`voice.py:134`） |
| TG 上傳音檔 | 同上存檔，再以 `add_audio` 寫進 `Prompt_Audios`／`References` | 同上 |
| TG 自訂頭像 | 照片存一份在後端 `app/assets/avatars/`，再上傳到 Anki；之後從那個資料夾列出供選 | `avatar_{chat_id}_{UTC 時間}.jpg`（`media.py:78`） |
| 本機批次腳本 | 從本機目錄讀檔，經 AnkiConnect `storeMediaFile` 上傳 | `{source_game}_{原檔名}` |

**網頁**

- 只能讀：`GET /anki-media/{filename}` 只回應音訊、影片、圖片類型，Cards 頁顯示卡面時用。
- 不能寫：沒有上傳到 Anki 的端點。
- 現有的 `/storage/*` 是存到 MinIO，不是 Anki 媒體資料夾，repo 內也沒有呼叫方，不能拿來用。

**handlers 計畫書的現況**：網頁建卡只收文字；handler 宣告的媒體欄位（`MEDIA_FIELDS`，例如 `_v2` 的 `Word_Audio`、`Target_Example_Audio`）在建卡表單不列、建出來留空，`PATCH` 拒絕直接修改，網頁上唯讀。

## 2. 目標與非目標

**目標**

- G1 網頁可以上傳音檔、圖片到 Anki 媒體資料夾，也可以在瀏覽器直接錄音。
- G2 **上傳和使用分開**：一個上傳端點，給建卡、JSON 欄位編輯、動作（例如對話卡加音檔）共用。
- G3 **不會蓋掉別人的檔案**：檔名一律由後端產生。
- G4 **要不要上傳由 handler 決定**：只有宣告了媒體參數或媒體欄位的地方，前端才顯示上傳元件。

**非目標**

- 改 TG 與本機腳本的上傳方式。
- 清理沒被引用的媒體檔（交給 Anki 的「檢查媒體」）。
- TTS 產生語音（卡片模板上的「透過 AI TTS 生成語音」按鈕，另案）。
- 列出、挑選 Anki 裡已有的媒體檔（例如頭像庫）：之後若需要再加查詢端點。

## 3. 設計

### D1 兩步驟：先上傳，再使用

```text
1. POST /api/v1/media                     上傳檔案 → 回傳後端產生的檔名
2. POST /handlers/{name}/notes            建卡，params 裡填那個檔名
   PUT  /handlers/{name}/notes/{id}/json/{field}     JSON 欄位，某筆資料的媒體鍵填那個檔名
   POST /handlers/{name}/notes/{id}/actions/{action} 動作，參數填那個檔名
```

前端可以把兩步包成一次「送出」，使用者感覺不到。

為什麼不在建卡請求裡直接夾帶檔案（multipart）：

- 建卡、JSON 編輯、動作的請求都維持 JSON：驗證、422 指到欄位、`create_schema` 產生表單都建立在 JSON 上，混入檔案就要另外處理。
- 一個上傳端點三處共用，不必各做一套。
- 建卡失敗（例如首欄位重複）時，檔案已經上傳好，修正後再送不必重傳。

代價：上傳後放棄或建卡失敗，媒體資料夾會留下沒被引用的檔案；Anki 的「工具 → 檢查媒體」可以清掉，影響很小。

### D2 `POST /api/v1/media`

請求：`multipart/form-data`，欄位 `file`（單一檔案）與 `kind`（`audio` 或 `image`）。

回應（201）：

```json
{ "filename": "web_audio_20261020_153012_3f9a1c.mp3", "kind": "audio", "size": 48213 }
```

處理順序：

1. **檢查類型**：依檔案內容判斷，不只看副檔名。
   - `audio`：接受 mp3、ogg、wav、m4a、webm（瀏覽器錄音）。
   - `image`：接受 jpg、png、webp。
2. **檢查大小**：超過上限回 413（上限見 §5 待決 2）。整個檔案要以 base64 經 AnkiConnect 傳送，太大會拖慢甚至失敗。
3. **轉檔**：Anki 不一定能播 webm、m4a，統一用後端既有的 `FfmpegClient.convert_to_mp3`（錄音評分已在用）轉成 mp3；mp3、ogg、wav 原樣保留（ogg 是 TG 語音的格式，Anki 能播）。圖片不轉。
4. **產生檔名**：`web_{kind}_{UTC 時間}_{隨機 6 碼}.{副檔名}`。前綴 `web_` 和 TG 的 `rec_`、`avatar_`、腳本的 `{source_game}_` 區分開，看檔名就知道從哪裡上傳的。
5. 經 `AnkiClient.store_media_file` 存進 Anki。AnkiConnect 的 `storeMediaFile` 遇到同名檔會**直接覆蓋**，所以第 4 步一定由後端產生、不採用使用者的檔名。
6. 不做寫入後同步：媒體檔會隨下一次寫入筆記時的同步一起上傳到 AnkiWeb。

錯誤：

| 情況 | 狀態碼 | `error_code` |
|---|---|---|
| 類型不符或無法辨識 | 415 | `MEDIA_TYPE_UNSUPPORTED` |
| 超過大小上限 | 413 | `MEDIA_TOO_LARGE` |
| 轉檔失敗 | 422 | `MEDIA_CONVERT_FAILED` |
| Anki 寫入失敗 | 502 | `ANKI_SERVICE_ERROR`（既有） |

掛 `verify_api_key`，與其他 `/api/v1` 端點相同。

### D3 handler 宣告「這裡要上傳」

三個使用處各有宣告方式，前端看到宣告才顯示上傳元件：

| 使用處 | 宣告 | 前端顯示 |
|---|---|---|
| 建卡（標準的手動填表） | handler 的 `MEDIA_FIELDS` 由「名稱清單」擴充成「欄位名 → 種類」，例如 `{"Word_Audio": "audio"}` | 該欄位顯示上傳／錄音元件；送出時填 `[sound:檔名]`（音檔）或 `<img src="檔名">`（圖片） |
| 建卡（覆寫的 `CreateParams`） | 參數欄位標 `json_schema_extra={"x-ui": "audio"}` 或 `"image"` | 同上；送出時填純檔名，由 handler 自己組欄位 |
| JSON 欄位的某個鍵 | 資料模型的欄位標 `x-ui: audio`／`image`（JSON 欄位設計書裡原本先標成唯讀的 `media`） | 那一筆的那個鍵顯示上傳元件 |
| 動作參數 | 具名動作的參數模型標 `x-ui`（動作參數模型化之後） | 同上 |

- 一般欄位存 `[sound:檔名]` 或 `<img src="檔名">` 是 Anki 的慣例，卡片模板直接顯示；JSON 欄位裡存純檔名，由模板自己組（例如對話卡 `Recordings` 的 `audio` 鍵）。這個差異由宣告決定，前端不必猜。
- **服務層再檢查一次**：媒體欄位或參數填入的檔名必須符合 D2 的命名格式（`web_…`）或欄位原本就有的值，否則 422。避免使用者手填一個不存在或別人的檔名。
- 媒體欄位在 handlers 計畫書裡是「`PATCH` 拒絕直接修改」；本設計實作後，改成「只能填 `/media` 產生的檔名」。

### D4 前端元件

- **上傳**：選檔或拖放；顯示檔名、大小、上傳進度；音檔上傳後可以試聽，圖片顯示縮圖。
- **錄音**（`audio`）：瀏覽器 `MediaRecorder` 錄製（產出 webm），停止後可試聽、重錄，確定後上傳（後端轉 mp3）。
- 上傳成功後，元件把回傳的檔名填進對應的參數或欄位；整張表單送出前，若還有上傳中的檔案，送出按鈕停用。
- 錯誤（413、415、422）顯示在元件旁。

### D5 第一個接上的地方

建議從單字卡 `_v2` 的建卡開始（handlers 計畫書的試點）：`Word_Audio`、`Target_Example_Audio` 兩個音檔欄位。資料平坦、沒有 JSON，最容易驗收。

之後依序：

1. 對話卡、三語卡的 `add_audio` 動作（web_ui_parity 階段 D）：要先完成動作參數模型化與 `ValueError` 改 4xx（階段 B）。
2. JSON 欄位裡的媒體鍵（`Recordings` 的 `audio` 等）：接在 JSON 欄位設計書之後。
3. 網頁建 jp 子卡（handlers 計畫書 D7「之後網頁要做時」）。

## 4. 測試

- `POST /media`：各種允許的格式成功；副檔名偽裝（例如文字檔改名 `.mp3`）回 415；超過上限回 413；webm 轉成 mp3；回傳的檔名符合格式、兩次上傳不會撞名；Anki 寫入的檔名就是回傳的檔名。
- 服務層：媒體欄位填了不符格式的檔名回 422；填 `/media` 產生的檔名成功，`_v2` 的欄位存成 `[sound:檔名]`。
- 前端：錄音、試聽、重錄的狀態；上傳中停用送出；錯誤顯示。

## 5. 待決事項

1. **檔名格式**：`web_{kind}_{UTC 時間}_{隨機 6 碼}`，還是帶上筆記資訊（例如牌組或 handler 名稱）方便日後辨認？建卡時還沒有筆記 ID，帶不了。建議前者。
2. **大小上限**：建議音檔 10 MB、圖片 5 MB。
3. **轉檔目標**：統一轉 mp3，還是保留 ogg？ogg 檔較小、TG 已在用；mp3 相容性最好（舊版 Anki、某些手機）。建議只把 Anki 可能播不了的格式（webm、m4a）轉 mp3，其餘原樣保留。
4. **頭像**：TG 的頭像會存一份在後端 `app/assets/avatars/` 供下次挑選；網頁上傳的頭像要不要也存進那裡，讓 TG 也能選到？建議要，但屬於階段 D 的範圍。
