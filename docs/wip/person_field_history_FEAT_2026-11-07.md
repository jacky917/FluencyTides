# 人脈卡：資料附登錄日期與有效狀態、新增按鈕移到各區塊（FEAT）

- 狀態：**已實作，待使用者驗收**（分支 `claude/person-field-history-a92427`，未 push、未部署；實作紀錄見 §8）
- 日期：2026-11-07
- 相關：`docs/archive/person_profile_card_FEAT_2026-10-23.md`（人脈卡原計畫書）、`docs/archive/json_field_editor_FEAT_2026-10-20.md`（網頁編輯 JSON 欄位）

## 1. 背景與目標

人的興趣、職位、住處都會變，但過去的內容仍是聊天素材（「你以前不是在玩攝影嗎？」）。目前一般文字欄位
沒有日期、也不分有效失效：在 TG「改資料」選「取代」，舊內容就沒了；選「接在後面」只是接文字。

目標：

1. 會變的資料每一筆都有**登錄日期**與**是否有效**；失效的不刪，劃掉保留。
2. 卡片預設只顯示目前有效的；點眼睛圖示展開該欄位的所有紀錄（含登錄日、劃掉的過去內容與失效日）。
3. 卡片上的「+」放在每個區塊上，不再集中在一排。

## 2. 現況盤點（2026-11-07，main + server-drafts）

### 2.1 卡片背面跳 TG 的入口（`backend/app/anki_models/Person_Profile_Dark_back.html`）

| 入口 | 位置 | 動作 |
|---|---|---|
| 「+ 記一筆互動」「+ 家人」「+ 禮物」「+ 日期」「+ 聯絡」 | 「見面前必看」下方的一排（`#person-tg`） | 新增一筆 JSON 項目 |
| 區段標題的「+」 | 家庭、互動紀錄、禮物、重要日期、聯絡方式 | 同上；但**區段沒有內容時整個不顯示**，所以空的區段沒有「+」可按 |
| 「+ 待追蹤」 | 「見面前必看」標題 | 新增待追蹤 |
| 「完成」 | 每筆未完成的待追蹤 | 標成完成 |
| 長按 3 秒 | 互動、待追蹤、家人、禮物、重要日期、聯絡方式 | 刪除一筆 |
| 「改資料」 | 同一排 | 開 TG 摘要 → 改資料 → 分組 → 欄位（三層） |
| 「照片」「AI 分析與話題」、眼睛圖示 | 同一排 | 換照片、AI 分析、AI 內文遮名字開關 |

一般欄位（`PersonCreateParams` 的 35 個）都能經「改資料」改，但卡片上沒有直達某個欄位的連結。
六種 JSON 清單只能新增、刪除（待追蹤另可標完成），不能修改既有的一筆。

### 2.2 會讀寫這些欄位的地方

| 地方 | 檔案 | 現在怎麼用 |
|---|---|---|
| 卡片模板 | `Person_Profile_Dark_back.html` | `htmlRow`、`chips`（興趣、飲食顯示成標籤）、公司・職稱合成一行、標頭顯示關係與親密度、見面前必看讀飲食限制、敏感話題模糊 |
| 建立參數／網頁建卡表單 | `schemas/person_profile.py` `PersonCreateParams` | 文字欄位 |
| TG 改資料 | `bot/handlers/person_profile.py`、`bot/utils/person_profile_view.py`（`FIELD_GROUPS`、`EDITABLE_FIELDS`） | 取代／接在後面 |
| TG 摘要、全部資料 | `person_profile_view.py` | 公司・職稱、關係、飲食限制等 |
| 用一段話記錄（AI 整理） | `services/person_profile_ingest.py`（`UPDATABLE_FIELDS`、`append_field_html`） | 建議 append／replace 一般欄位 |
| AI 分析與話題 | `services/person_profile_topics.py` | 送出一般欄位（排除姓名、敏感話題等） |
| 網頁欄位搜尋 | `person_profile_handler.py` `SEARCH_FIELDS` | 依欄位搜尋 |
| 網頁 JSON 欄位編輯 | `JSON_FIELD_SPECS`（由 `PERSON_JSON_ITEMS` 產生）、`frontend/src/components/JsonFieldEditor.tsx` | 六種清單可在網頁以表單整欄編輯 |

### 2.3 「今天」目前怎麼算

| 地方 | 算法 |
|---|---|
| 人脈卡（`person_profile_handler.today_iso`、`bot/handlers/person_profile._today`） | `date.today()`，依伺服器時區；正式機靠 `backend/docker-compose.yml` 的 `TZ=Asia/Tokyo` |
| 口說卡的日期（`speaking_fsm.py`、`voice.py` 錄音紀錄的 `date`） | `datetime.now(timezone.utc)`，UTC：東京 0–9 點記成前一天 |
| 檔名的時間戳 | 有的 UTC、有的本地時間 |

本機開發（Windows、不經 docker）時用的是電腦的時區，與正式機不一定相同。

## 3. 決策

| # | 決策 | 理由 |
|---|---|---|
| D1 | **方案 B**：會變的欄位本身改成 JSON 清單（欄位名不變），不另加欄位 | 不改筆記類型的欄位，AnkiWeb 不必完整同步；資料只有一份來源 |
| D2 | **原有資料損壞沒關係**：不必保證轉換無損 | 使用者確認 |
| D3 | **每個區塊上一個「+」**，取代集中在一排的新增連結 | 使用者要求 |
| D4 | 改成清單的欄位照 §4.2 | 使用者確認建議 |
| D5 | 「單值」欄位（現居地、時區、關係、親密度）新增一筆時，自動把原本有效的那筆劃掉（失效日＝今天）；其他欄位（含職位）不自動 | 使用者確認建議；職位可能同時有兩份 |
| D6 | **新增時不必填日期**：登錄日、失效日一律由系統填今天（TG、網頁建卡、用一段話記錄都一樣） | 使用者要求 |
| D7 | 資料改成清單與「+」移到各區塊**同一個 PR** | 使用者要求 |
| D8 | 時區用 `.env` 的 `APP_TIMEZONE`（預設 `Asia/Tokyo`），人脈卡的日期都經共用的「今天」函數（§4.7） | 使用者確認建議 |
| D9 | 口說卡用 UTC 記日期的問題這個 PR 不改，列進 backlog | 使用者確認建議 |

## 4. 設計草案

### 4.1 項目格式

每個改成清單的欄位，一筆項目：

```json
{"value": "攝影", "added": "2026-11-07", "ended": ""}
```

- `value`：內容（多行欄位一筆一段）。
- `added`：登錄日期，新增時系統填今天（D6）。
- `ended`：空字串表示有效；有日期表示從那天起失效（劃掉時系統填今天）。

「職位」例外：放在 `Company` 欄位，一筆是 `{company, title, added, ended}`；`Title` 欄位不再使用（刪欄位會要求完整同步，所以留著不動）。

日期只在網頁的 JSON 欄位編輯器裡能改（補登過去的事、修正錯誤），TG 不問。

### 4.2 改成清單的欄位（D4）

- 興趣與飲食：`Interests`、`Food_Drink`、`Dietary`
- 工作：`Company`（＋`Title`）、`Work_Detail`、`Education`
- 基本資料：`Residence`、`Languages`、`Timezone`
- 關係：`Relationship`、`Closeness`
- 個性與價值觀：`Personality_Values`、`Praise`、`Ask_Advice`、`Sensitive`
- 其他：`Comm_Prefs`

維持文字欄位：姓名類、`Appearance`、`Birthday`、`Hometown`、認識類（`Met_*`、`Introducer_Note_ID`、`First_Impression`）、`Notes`、`Last_Contact`、`Contact_Cadence`。

單值欄位（D5）：`Residence`、`Timezone`、`Relationship`、`Closeness`。

### 4.3 卡片顯示

- 預設只顯示有效的項目；興趣、飲食照樣顯示成標籤。
- 欄位旁的眼睛圖示展開全部：每筆附登錄日，失效的劃線並附失效日。展開與否在卡片上切換，不開 TG。
- 底部那排已有一個眼睛圖示（AI 內文遮名字開關），歷史的眼睛放在各欄位旁邊，樣式要能區分。

### 4.4 每個區塊一個「+」（D3）

區塊標題列放一個「+」，收合時也能按。區塊只對應一個欄位時直接開 TG 新增；對應多個欄位時，TG 先問要新增哪一項。

| 區塊 | 「+」新增到 |
|---|---|
| 見面前必看 | 待追蹤、飲食限制 |
| 興趣與喜好 | 興趣、飲食喜好 |
| 工作 | 職位、工作內容、學歷 |
| 家庭 | 家人 |
| 個性與價值觀 | 個性與價值觀、讚美素材、可以請教的事 |
| 互動紀錄 | 互動 |
| 禮物 | 禮物 |
| 重要日期 | 重要日期 |
| 聯絡方式 | 聯絡方式、溝通偏好 |
| 認識脈絡與基本資料 | 現居地、語言、時區、關係、親密度 |
| 敏感話題 | 敏感話題 |

- 有開 TG 時，這些區塊**沒有內容也顯示**（標題＋「+」＋「還沒有…」），否則空的區塊沒地方按。
- 「見面前必看」下方那排拿掉所有新增連結，只留「改資料」「照片」「AI 分析與話題」、遮名字開關與長按提示。
- 關係、親密度顯示在標頭，但「+」放在「認識脈絡與基本資料」。
- 文字欄位（認識日期、備註等）仍從「改資料」改。

### 4.5 TG

- 改成清單的欄位從「改資料」的選單移除，改走清單操作（沿用六種清單現有的流程）：新增一筆（只問內容，日期自動）、劃掉、取消劃掉、刪除（打錯字時）。
- 卡片上：長按 3 秒沿用為刪除；劃掉用每筆旁的小連結（類似待追蹤的「完成」）。
- 摘要、全部資料只顯示有效的。

### 4.6 其他

- **網頁**：這些欄位加進 `JSON_FIELD_SPECS`，網頁的 JSON 欄位編輯器、欄位搜尋自動跟著支援。
- **網頁建卡表單**：這些欄位維持文字輸入，建立時轉成清單、登錄日填今天（D6）。興趣、飲食喜好、語言用逗號、頓號切成多筆，其他整段一筆。
- **用一段話記錄**：AI 的欄位建議改成「新增一筆」或「劃掉某筆」，日期由系統填。
- **AI 分析與話題**：有效的照常送；失效的也送，標明是過去的事（聊天素材）。
- **既有資料**（D2）：一次性腳本轉換，切法同網頁建卡表單；`added` 留空（顯示「日期不明」），`ended` 空。轉不好的手動修。

### 4.7 時區設定（推薦）

在 `.env` 加 `APP_TIMEZONE`（預設 `Asia/Tokyo`，IANA 名稱），由 `app/core/config.py` 讀入；新增一個共用的「今天」函數
（`ZoneInfo(settings.APP_TIMEZONE)` 下的日期），人脈卡的登錄日、失效日、`Updated_At`、互動日期、AI 整理換算「昨天」等全部改用它。

為什麼不只靠容器的 `TZ`：

- 本機開發不經 docker，`date.today()` 用的是電腦的時區；設定寫在 `.env` 兩邊一致，測試也能固定時區。
- 程式裡已經混著 UTC 與本地時間（§2.3），明確的設定比依賴環境變數清楚。

連帶要做：

- `requirements.txt` 加 `tzdata`（Windows 沒有系統的時區資料，`ZoneInfo` 會失敗）。
- 同步本機 `backend/.env`（先備份）與 `.env.example`；正式機 `.env` 要加一行（給 sudo 指令）。
- `docker-compose.yml` 的 `TZ=Asia/Tokyo` 保留（影響 log 的時間），與 `APP_TIMEZONE` 無關。

口說卡用 UTC 記日期（§2.3）是另一個問題，這個 PR 不改；列進 backlog（D9、§7），之後改用同一個函數。

## 5. 影響

- 不新增、刪除筆記類型的欄位；模板要重新匯入（`backend/scripts/local_anki/Person_Profile_Dark/import_model.py`）。
- 在 Anki 裡直接手改這些欄位要寫 JSON，不再適合手改。
- 新的 `.env` 設定：`APP_TIMEZONE`。

## 6. 待確認

（無。Q1 已決：時區照 §4.7，見 D8、D9。）

## 7. 實作時一併做

- `docs/wip/backlog.md` 加一項（D9）：口說卡（`speaking_fsm.py`、`voice.py`）的日期用 UTC 記，東京 0–9 點記成前一天；改用 §4.7 的共用「今天」函數。

## 8. 實作紀錄（2026-11-07）

### 8.1 與設計的差異與補充

| 項目 | 設計 | 實作 |
|---|---|---|
| 空清單 | 未提 | 會變的欄位清空時寫**空字串**而不是 `[]`：「認人」卡只要正面引用的欄位有值就產生，`Company` 寫 `[]` 會讓每個人都多一張空的認人卡。建卡時沒填的也是空字串 |
| 卡片正面 | 未提 | 「認人」「見面前複習」正面原本直接印 `{{Company}}・{{Title}}`，改成以 JS 解析清單、只顯示有效的職位；「認人」正面在職位都劃掉時顯示最近一筆並標「（以前）」（認人仍是線索）。正面不再引用 `Title` |
| 「劃掉」連結的位置 | 每筆旁 | 一般文字的項目在預設畫面就有「劃掉」；顯示成標籤的（興趣、飲食喜好、語言）在點眼睛展開後才有，避免標籤列太擠。長按 3 秒刪除兩種畫面都有 |
| TG「清單」選單 | 未提 | 改資料拿掉會變的欄位後，它們從「清單」選單進入（22 個清單兩個一排，括號是有效筆數）。刪除確認時提示「不再適用用劃掉，刪除是給打錯的」 |
| TG 新增可切的欄位 | 只問內容 | 興趣、飲食喜好、語言輸入「登山、攝影」會存成兩筆（與建卡表單同一切法）；與目前有效的某筆相同的略過 |
| 取消劃掉 | 有 | TG 清單的「取消劃掉 n」按鈕，以及劃掉後回覆的「復原」按鈕；卡片上不放取消劃掉的連結 |
| 網頁 JSON 編輯器新增一筆 | 未提 | 登錄日欄位在 schema 標 `x-default: today`，前端 `newItem` 預填瀏覽器時區的今天（可改） |
| `Title` 欄位 | 不再使用但保留 | 經 `update_fields` 寫入非空的 `Title` 會被擋下並說明改到職位（清空可以）；轉換腳本把 `Title` 併進職位後清空 |
| 指紋 | 未提 | 會變的欄位的項目另外把 `value`、`company`、`title` 接進指紋（項目有這些鍵時才接，原有六種清單的指紋不變）；`ended` 不在指紋裡，劃掉後的連結仍對得上 |
| 用一段話記錄 | 新增或劃掉 | LLM 輸出新增 `list_updates`（`op: add / end`），劃掉以提示中有效項目的編號 `#n` 指定；文字欄位的建議 `field_updates` 只剩文字欄位。單值欄位新增時系統自動劃掉原本的，提示詞也這樣告訴 LLM |
| AI 話題 | 失效的也送 | 一行裡先列有效的，再接「以前：X（到 YYYY-MM-DD 為止）」；提示詞加一句說明「以前：」是過去的事。劃掉的敏感話題照樣不送內容，但仍觸發「不要碰敏感話題」的提醒 |
| 格式壞掉的清單 | 未提 | TG 讀取時當成空的（摘要照常顯示），但摘要的飲食、全部資料、清單頁、清單選單會標「資料格式壞了」，不顯示成「還沒有」；寫入時拒絕並說是哪一欄（會變的欄位提示先跑轉換腳本）。卡片背面的格式不對的單筆（不是物件）另列錯誤，不算有效也不算劃掉；AI 提示不送這些項目 |
| `requirements.lock` | 重產 | 本機沒有 `uv`，照鎖定檔的格式手動加了 `tzdata==2026.6`（`-r requirements.txt`）；之後有 `uv` 時重產一次確認 |

### 8.2 檔案

| 檔案 | 內容 |
|---|---|
| `backend/app/core/config.py`、`backend/app/core/clock.py`（新） | `APP_TIMEZONE`（啟動時檢查是 IANA 名稱）、共用的 `now()`、`today()`、`today_iso()` |
| `backend/requirements.txt`、`requirements.lock`、`.env.example` | `tzdata`、`APP_TIMEZONE` 說明 |
| `backend/app/schemas/person_profile.py` | `HistoryEntry`、`HistoryNote`、`ClosenessEntry`、`Position`、`PERSON_HISTORY_FIELDS`（單值、可切）、`history_from_text`、`html_to_text`、`entry_text`、`active_texts`、`legacy_to_history`；指紋加鍵；`append_field_html` 拿掉標籤欄位的特例 |
| `backend/app/services/task_handlers/person_profile_handler.py` | 會變的欄位併入 `JSON_FIELDS`、`JSON_FIELD_SPECS`；建卡轉清單；動作 `add_history`、`set_history_status`；空清單寫空字串；欄位搜尋按鈕調整 |
| `backend/app/bot/utils/person_profile_view.py`、`deep_link_parser.py`、`schemas/tg/deep_link.py` | 清單代號、`SECTION_FIELDS`、改資料只剩文字欄位、摘要與全部資料只顯示有效的、清單每筆附日期、格式壞掉的清單標「資料格式壞了」；深連結 `pend`（劃掉）、`psec`（區塊的「+」） |
| `backend/app/bot/handlers/person_profile.py` | 清單選單、區塊選單、劃掉／取消劃掉、新增走 `add_history`、用一段話記錄的新建議種類；「今天」改用 `clock`（建立新的人那段程式沒改，但經 `_today()` 一樣改用 `APP_TIMEZONE`） |
| `backend/app/services/person_profile_ingest.py`、`person_profile_topics.py`、`schemas/llm/person_profile.py`、兩份 `.j2` | §4.6 的 AI 部分 |
| `backend/app/anki_models/Person_Profile_Dark_*` | 背面：`historyRow`（有效的、眼睛展開全部、劃掉、長按刪除、敏感話題模糊）、每個區塊一個「+」、空區塊在有 TG 時顯示、拿掉集中的新增連結；正面：職位改讀清單；CSS |
| `backend/scripts/local_anki/Person_Profile_Dark/migrate_history_fields.py`（新） | 一次性轉換（預設試跑，`--apply` 寫入並強制同步；開始前同步失敗就中止；可重複執行；不合格式的內容、看起來是壞掉的 JSON、殘留的 Title 列在「要手動處理」） |
| `frontend/src/lib/jsonItemForm.ts` | `x-default: today` 預填今天 |
| `docs/wip/backlog.md` | §7 Z1：口說卡日期用 UTC（D9） |
| 測試 | `tests/test_person_field_history.py`（新）、`tests/test_person_profile_template.py`（新：模板常數與後端一致、用 node 跑模板的指紋與 `entryText` 比對後端）、人脈卡四份測試、`test_note_service.py`；`jsonItemForm.test.ts` |

### 8.3 驗收清單

自動測試（已過）：後端 `pytest tests`、`ruff check .`；前端 `vitest run`、`eslint .`（沒有新增警告）、`npm run build`。

部署前要做（本機 Anki）：

- [ ] 重新匯入模板（`import_model.py`），Anki 同步
- [ ] 轉換腳本試跑，看輸出合理後 `--apply`
- [ ] 卡片背面：興趣顯示成標籤、眼睛展開後每筆有登錄日（舊資料「日期不明」）、劃掉的有刪除線與失效日；眼睛與 TG 按鈕列裡的 AI 遮名字開關看得出不同
- [ ] 每個區塊標題有「+」，空的區塊也在；「見面前必看」的「+」問待追蹤或飲食限制；工作、興趣、個性、聯絡方式、認識脈絡先問新增哪一項
- [ ] 點「劃掉」→ TG 回覆已劃掉並有「復原」；同步後卡片那筆移到展開畫面、劃線
- [ ] TG 新增現居地：原本的自動劃掉；新增興趣「a、b」存成兩筆
- [ ] TG「清單」選單看得到會變的欄位，可劃掉、取消劃掉、刪除；「改資料」只剩文字欄位
- [ ] 網頁建卡填興趣「登山、攝影」、公司＋職稱：卡片顯示兩個標籤、一筆職位；網頁 JSON 編輯器能改登錄日、失效日
- [ ] 用一段話記錄說「他離職了，現在在 Sony 當 PM」：建議劃掉舊職位、新增新職位
- [ ] AI 分析與話題：劃掉的興趣以「以前」出現在話題素材

部署時（正式機）：`.env` 加 `APP_TIMEZONE=Asia/Tokyo`（不加也用預設值 Asia/Tokyo）；重建後端映像（新增 `tzdata`）。

### 8.4 審查（`/multi-review`）

五個面向（一般、錯誤處理、測試、註解、型別）都沒有 Critical；一般審查沒有信心 ≥ 80 的問題。已處理：

- 轉換腳本：開始前同步失敗就中止、最後一次同步強制（寫入前的同步有 10 秒防抖，原本可能被跳過卻顯示「已同步」）；看起來是壞掉的 JSON 不包成文字；不合欄位模型的內容（例如親密度「很熟」）不轉並列出；`Company` 已是清單但 `Title` 有字時列出。
- TG 不再把格式壞掉的清單默默顯示成空的（見 §8.1）；寫入失敗的訊息帶欄位名稱。
- 同一次輸入裡重複的值（「登山、登山」）只存一筆。
- 卡片正面的 `try` 只包 `JSON.parse`；背面格式不對的項目另列。
- `pp:hx` 按鈕的模式只接受 `e`／`o`；`HistoryField` 不允許同時單值又可切；`PERSON_HISTORY_FIELDS` 打錯欄位名時 import 就失敗；`PERSON_VERBS` 與 `PersonLinkAction.verb` 一致的測試；深連結 start 參數長度的測試。
- 註解：`APP_TIMEZONE` 與容器 `TZ` 的關係（TZ 仍影響三語口說卡的 `date.today()`，已加進 backlog Z1）、`Title` 的鎖定、指紋用途與 `added`／`ended` 不進指紋的理由、遮名字開關的位置等。

沒處理（留給之後）：

- 轉換前，每次讀到舊文字欄位 `AnkiJsonFieldManager.parse_field_string` 都會記一筆 ERROR log（共用的基礎設施，跑完轉換腳本就沒了）。
- 新增的內容和現有有效的完全相同時不會寫入，但 TG 仍回「已儲存」。
- 部署後聊天紀錄裡舊的「改資料」按鈕（以序號指欄位）會指到別的欄位；編輯畫面會先顯示欄位名稱。
- 型別審查的結構性建議：把「`Company` 是職位、其他是 `value`」的分派收進 `HistoryField`（目前 `field == "Company"` 散在幾個函式）；`ended >= added` 的檢查。
- 轉換腳本的 `main()` 沒有自動測試（轉換邏輯 `legacy_to_history` 有）。

