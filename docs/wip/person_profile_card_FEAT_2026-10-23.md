# FEAT — 人脈卡：記住每個人的名字、喜好與上次聊過的事（新筆記類型 `Person_Profile_Dark`）

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-23 |
| **性質** | 新增機能設計 + 實作工作項（新筆記類型、卡片模板、handler、TG 流程）；含三個管道（Anki app、網頁、TG）欄位 CRUD 的盤點 |
| **狀態** | 📝 未實作 |
| **範圍** | 新筆記類型 `Person_Profile_Dark` 的模型定義與模板（`backend/app/anki_models/`）；新 handler `person_profile`（建立、修改、刪除、具名動作）與註冊；TG 的人脈卡流程（找人、看摘要、建立、記一筆互動與待追蹤、改欄位、傳照片、LLM 整理）與深連結；handler 與 TG 的單元測試 |
| **不動** | 網頁前端（建卡表單、編輯彈窗、卡面顯示都走現有的通用流程）；網頁上的 JSON 欄位表單編輯（屬 `json_field_editor` 計畫，人脈卡是它的使用者之一）；網頁的媒體上傳（屬 `media_upload` 計畫）；其他筆記類型與現有 TG 流程的行為 |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/wip/handlers_api_redesign_FEAT_2026-10-20.md`（handler 能力、`JSON_FIELDS`、`MEDIA_FIELDS`、標準建立；在 `feat/handlers-api-redesign` 分支）；`docs/wip/json_field_editor_FEAT_2026-10-20.md`（JSON 欄位的表單編輯；尚未 commit，在 `feat/handlers-api-redesign` 的 worktree）；`docs/wip/media_upload_FEAT_2026-10-20.md`（媒體上傳；同上）；參考模板 `backend/app/anki_models/JP_CoreVerb_Cloze_Dark_*`、`Speaking_Coach_Dark_back.html` |

> **分支**：handler 架構（`CAPABILITIES`、`JSON_FIELDS`、`MEDIA_FIELDS`、標準建立表單）目前只在 `feat/handlers-api-redesign`（以及從它分出的 `feat/card-transitions`），`main` 還沒有。實作要從含有 handler 架構的分支開新分支。

---

## 1. 問題與動機

想用 Anki 記住認識的每個人：名字（中、英、日）、怎麼認識的、喜好、家人、上次聊了什麼、下次要問什麼。目標是見面前快速複習，做到《人性的弱點》說的幾件事：

- **記住名字**：對任何人來說，自己的名字都是最悅耳的聲音。
- **談對方感興趣的事**：記得興趣、近況。
- **讓對方覺得自己重要**：記得對方的成就、可以向對方請教的事、對方說過的話。
- **當個好聽眾**：記得上次聊的話題，下次接著問。

現有的筆記類型都是語言學習用的，沒有適合的；需要新的筆記類型與模板。

## 2. 目標與非目標

**目標**

- G1 新筆記類型 `Person_Profile_Dark`，欄位涵蓋姓名與稱呼、基本資料、認識脈絡、工作、興趣喜好、家庭、個性價值觀、溝通偏好、互動紀錄、待追蹤事項、禮物、重要日期、聯絡方式（§3.2）。
- G2 兩種卡片：「認人」（線索 → 姓名）與「見面前複習」（稱呼 → 該問的事與近況）。**多數人沒有照片**：「認人」以外貌、公司職稱、認識場合等線索出題，有照片時再加上照片；一條線索都沒有的人不產生「認人」卡（§3.4）。
- G3 背面一律顯示「見面前必看」（未完成的待追蹤事項、我答應的事、飲食限制、最新一筆互動），其餘區段可收合；沒有內容的區段整段不顯示（§3.6、§3.7）。
- G4 模板在 Anki 桌面版、AnkiDroid、AnkiMobile 與網頁版（FluencyTides 卡片瀏覽）都能正常顯示與操作；點收合區塊不會誤翻面（§3.7）。
- G5 新 handler `person_profile`：網頁能建立、修改（非 JSON、非媒體欄位）、刪除人脈卡；JSON 清單與照片經由具名動作寫入（§3.11）。
- G6 三個管道（Anki app、網頁、TG）對每一類欄位能做的新增、讀取、修改、刪除逐項盤點，標出現有、要實作與不做的（§3.12）。
- G7 TG 不開 Anki 也能維護人脈：用名字找人、看「見面前必看」摘要、建立新的人、記一筆互動與待追蹤、把待追蹤標成完成、改欄位、傳照片；最後再加上「打一段話，由 LLM 整理成互動紀錄」（§3.13）。

**非目標**

- 網頁上編輯 JSON 欄位：依 `json_field_editor` 計畫做成通用的表單編輯，這份不做；在那之前 JSON 欄位由 TG、Anki app 或腳本寫入，網頁唯讀。
- 網頁上傳照片：依 `media_upload` 計畫；在那之前照片從 TG 傳或在 Anki 編輯器貼上。
- TG 刪除整筆人脈卡：誤刪無法復原，刪除只在 Anki app 與網頁做（§3.12）。
- 從卡面直接修改資料（例如把待追蹤事項標為完成）：網頁版不支援 `updateNoteFields`，桌面版呼叫後會跳回正面（§3.8）。
- 「資訊 → 是誰」的反向卡：會變成考試，與「見面前複習」的用途不符。

## 3. 設計決策

### 3.1 欄位分三種，不整張做成一個 JSON

| 種類 | 什麼時候用 | 好處 | 例子 |
|---|---|---|---|
| **單值欄位** | 卡片正面要顯示、要在 Anki 搜尋或排序、只有一個值 | Anki 編輯器與網頁都能直接改；能搜尋，例如 `Company:*Geekly*` | 姓名、公司、生日 |
| **文字區段**（可含 HTML） | 一整段描述，主要手寫 | Anki 與網頁都能直接編輯，可以寫條列 | 興趣、個性、讚美素材 |
| **JSON 陣列** | 會一直追加、每筆有日期或狀態、需要排序或篩選的清單 | 能依日期排序、篩出未完成的項目 | 互動紀錄、待追蹤事項、禮物 |

**為什麼不整張做成一個 JSON**（已定位）：

- **手改容易壞**：模板以 `textContent` 讀欄位再 `JSON.parse`，失敗就整段不顯示（`JP_CoreVerb_Cloze_Dark_back.html:124-131`）。在 Anki 編輯器手改 JSON 很容易被插入 `<br>` 或 HTML 跳脫，經 TG 寫入的又是跳脫過的（`&quot;`，見 `json_field_editor` 計畫 §1）。
- **網頁唯讀**：handler 宣告的 JSON 欄位在網頁建卡與修改時都鎖住（`note_service.py:140`、`task_handlers/base.py:56`）。整張做成 JSON，網頁就什麼都不能改。
- **搜尋與排序**：Anki 的搜尋、排序欄位、重複檢查都以欄位為單位。

**原則**：手寫的放文字欄位；之後會由表單或 TG 逐筆追加的清單才用 JSON。

### 3.2 欄位清單

順序即 `inOrderFields` 的順序。第一個欄位是 Anki 的排序欄位，也是重複檢查的欄位。「顯示」欄：**頭**＝背面頭部；**必看**＝「見面前必看」；**區**＝可收合區段（括號內為區段名）；**正**＝卡片正面。

**姓名與稱呼**

| 欄位 | 種類 | 格式 | 例子 | 顯示 |
|---|---|---|---|---|
| `Display_Name` | 單值 | 平常怎麼稱呼 | 田中さん | 正（見面前複習）、頭 |
| `Name_ZH` | 單值 | 中文全名 | 田中悠希 | 頭 |
| `Name_EN` | 單值 | 英文名，照對方的拼法 | Yuki Tanaka | 頭 |
| `Name_JA` | 單值 | 日文漢字 | 田中 悠希 | 頭（加振假名） |
| `Name_JA_Kana` | 單值 | 讀音，與 `Name_JA` 用空白對齊分段 | たなか ゆうき | 頭（振假名） |
| `Name_Romaji` | 單值 | 羅馬拼音 | Tanaka Yūki | 頭 |
| `Honorific` | 單值 | 稱呼與敬語 | さん／敬語 | 頭 |
| `Name_Memo` | 文字 | 名字的記憶口訣、由來 | 「悠」閒的「希」望 | 頭 |
| `Photo` | 媒體（選填，多數人沒有） | `<img src="檔名">` | `<img src="person_tanaka.jpg">` | 正（認人）、頭（頭像） |
| `Appearance` | 文字 | 外貌特徵；沒有照片時是認人的主要線索 | 高個子、黑框眼鏡、關西腔 | 正（認人）、頭 |

**基本資料**

| 欄位 | 種類 | 格式 | 例子 | 顯示 |
|---|---|---|---|---|
| `Birthday` | 單值 | `YYYY-MM-DD`；不知年份寫 `--MM-DD` | `1994-03-15`、`--03-15` | 頭（倒數、年齡） |
| `Hometown` | 單值 | | 大阪 | 區（基本資料） |
| `Residence` | 單值 | | 東京・中野 | 區（基本資料） |
| `Languages` | 單值 | 逗號分隔 | 日語, 英語 | 區（基本資料） |
| `Timezone` | 單值 | IANA 名稱 | Asia/Tokyo | 區（基本資料） |

**認識脈絡**

| 欄位 | 種類 | 格式 | 例子 | 顯示 |
|---|---|---|---|---|
| `Met_Date` | 單值 | `YYYY-MM-DD` | `2026-06-07` | 區（認識脈絡） |
| `Met_Where` | 單值 | 場合與地點 | Geekly 面談 | 區（認識脈絡） |
| `Met_Via` | 單值 | 管道 | 朋友介紹 | 區（認識脈絡） |
| `Introducer_Note_ID` | 單值 | 介紹人的人脈卡筆記 ID | `1780912304027` | 區（認識脈絡）：「🔍 查看介紹人」按鈕 |
| `Relationship` | 單值 | 關係類型 | 客戶 | 頭（徽章） |
| `Closeness` | 單值 | 1–5 | `3` | 頭（圓點） |
| `First_Impression` | 文字 | | 說話很有條理 | 區（認識脈絡） |

**工作**

| 欄位 | 種類 | 格式 | 例子 | 顯示 |
|---|---|---|---|---|
| `Company` | 單值 | | Geekly | 頭、區（工作） |
| `Title` | 單值 | | 顧問 | 頭、區（工作） |
| `Work_Detail` | 文字 | 工作內容、目前的專案、職涯經歷 | | 區（工作） |
| `Education` | 單值 | 學校、主修 | 早稻田・經濟 | 區（工作） |

**興趣、個性與溝通**

| 欄位 | 種類 | 格式 | 例子 | 顯示 |
|---|---|---|---|---|
| `Interests` | 文字 | 短項目用逗號分隔（顯示成標籤），也可以寫條列 | 登山, 精釀啤酒, 推理小說 | 區（興趣喜好） |
| `Food_Drink` | 文字 | 喜歡吃喝的東西、愛去的店 | 拉麵、喜歡 IPA | 區（興趣喜好） |
| `Dietary` | 文字 | 過敏與飲食限制 | 不吃生魚 | 必看（紅框） |
| `Personality_Values` | 文字 | 個性、價值觀、夢想、煩惱 | | 區（個性與價值觀） |
| `Praise` | 文字 | 真誠讚美的素材（具體、真實） | 一年內考過簿記二級 | 區（個性與價值觀） |
| `Ask_Advice` | 文字 | 可以向他請教的事 | 日本轉職、履歷 | 區（個性與價值觀） |
| `Sensitive` | 文字 | 不要主動提的話題 | | 區（敏感話題，模糊顯示） |
| `Comm_Prefs` | 文字 | 偏好的聯絡管道、時段、溝通風格 | LINE，晚上 9 點後別打擾 | 區（聯絡方式） |
| `Notes` | 文字 | 其他 | | 區（備註） |

**JSON 清單**（格式見 §3.3）

| 欄位 | 內容 | 顯示 |
|---|---|---|
| `Interactions_JSON` | 每次見面或聊天一筆 | 必看（最新一筆）、區（互動紀錄，時間軸） |
| `Followups_JSON` | 下次要問的事、我答應的事、對方答應的事 | 必看（未完成）、區（互動紀錄內的已完成） |
| `Family_JSON` | 家人與寵物 | 區（家庭） |
| `Gifts_JSON` | 送出與收到的禮物 | 區（禮物） |
| `Key_Dates_JSON` | 考試、預產期、紀念日等 | 頭（近期日子提示）、區（重要日期） |
| `Contacts_JSON` | 聯絡方式 | 區（聯絡方式） |

**其他**

| 欄位 | 種類 | 格式 | 用途 | 顯示 |
|---|---|---|---|---|
| `Last_Contact` | 單值 | `YYYY-MM-DD` | 單獨一欄，才能在 Anki 搜「很久沒聯絡」 | 頭（「23 天前」） |
| `Contact_Cadence` | 單值 | 天數 | 目標聯絡間隔；超過時「N 天前」變紅 | 頭 |
| `Card_ID` | 單值 | 穩定 ID，`PP_YYYYMMDD_HHMMSS`；由網頁、TG 建立時自動產生 | TG 以它找到這個人（現有 TG 一律用 `Card_ID` 欄位找筆記，`app/bot/handlers/commands.py:125`）；名稱沿用其他類型，深連結的格式不用改 | 不顯示 |
| `TG_Bot` | 單值 | TG bot 的使用者名稱 | 背面的 TG 深連結按鈕用（同 `Speaking_Coach_Dark_back.html:158-161` 的做法）；沒填就不顯示按鈕 | 不顯示 |
| `Updated_At` | 單值 | `YYYY-MM-DD` | 資料最後更新日 | 區（備註） |

**圈子用 Anki 標籤**（例如 `人脈::客戶`、`人脈::日本`、`人脈::Geekly`），不另開欄位：標籤能多選、能階層、能直接在瀏覽器篩選。

### 3.3 JSON 欄位的格式

一律是陣列。日期 `YYYY-MM-DD`；沒有的鍵可以省略。模板只讀、不寫，順序不重要（顯示時自己排序）。

`Interactions_JSON`：

```json
[
  {
    "date": "2026-10-01",
    "place": "中野的咖啡廳",
    "topics": "轉職、京都旅行",
    "updates": "下個月要考 N1；媽媽住院中",
    "quote": "工作是為了生活，不是反過來"
  }
]
```

`Followups_JSON`：`type` 為 `ask`（下次要問）、`my_promise`（我答應的）、`their_promise`（對方答應的）；`status` 為 `open` 或 `done`。

```json
[
  { "added": "2026-10-01", "item": "N1 考得如何？", "type": "ask", "due": "2026-12-07", "status": "open" },
  { "added": "2026-10-01", "item": "介紹 Ken 給他", "type": "my_promise", "status": "open" }
]
```

`Family_JSON`：寵物也放這裡（`relation` 寫「貓」）。

```json
[
  { "relation": "妻子", "name": "美咲", "note": "在醫院工作" },
  { "relation": "兒子", "name": "陽太", "birth_year": 2021 },
  { "relation": "貓", "name": "Momo" }
]
```

`Gifts_JSON`：`direction` 為 `given` 或 `received`。

```json
[{ "date": "2026-09-20", "direction": "given", "item": "台灣鳳梨酥", "reaction": "很喜歡，說要帶回老家" }]
```

`Key_Dates_JSON`：`yearly` 為 `true` 表示每年的日子（顯示時換算成下一次）。

```json
[
  { "date": "2026-12-07", "label": "N1 考試" },
  { "date": "2023-05-20", "label": "結婚紀念日", "yearly": true }
]
```

`Contacts_JSON`：

```json
[
  { "channel": "LINE", "handle": "yuki.t", "preferred": true },
  { "channel": "Email", "handle": "yuki@example.com" }
]
```

日後依 `json_field_editor` 計畫，這六種格式要在 handler 以 Pydantic 模型宣告，網頁才能產生逐筆表單、後端才能驗證。這份計畫先在文件中固定格式。

### 3.4 兩種卡片

| 卡片（模板名） | 正面 | 背面 | 產生條件 |
|---|---|---|---|
| `認人` | 線索：照片（有才顯示）、外貌特徵、公司・職稱、認識場合與日期、介紹人；問「這是誰？」 | 共用背面（§3.6） | 正面引用的線索欄位（`Photo`、`Appearance`、`Company`、`Title`、`Met_Where`）任一有值就產生 |
| `見面前複習` | 稱呼（`Display_Name`）與小頭像 | 共用背面 | 每個人都有 |

- **多數人沒有照片**，所以「認人」不能只靠照片。沒有照片時，正面以文字線索出題，例如「Geekly 的顧問・2026-06 在面談認識・高個子、黑框眼鏡、關西腔」；有照片時照片放最上面，文字線索照樣列出。
- **產生條件靠 Anki 的規則**：卡片正面引用的欄位只要有一個有值，Anki 就產生這張卡（隱藏的 `<div>{{欄位}}</div>` 也算引用）。所以「認人」的正面**只能引用線索欄位**，不能引用姓名：
  - 引用姓名會讓每個人都產生這張卡。
  - 姓名也可能被看到，等於直接洩漏答案。
- **只有姓名、沒有任何線索的人**不會產生「認人」卡，只有「見面前複習」。之後補上外貌或公司，Anki 會自動補產生這張卡（在 Anki 檢查資料庫或編輯筆記時）。
- **沒有照片的頭像**：用 `Display_Name` 的第一個字，背景色由姓名雜湊出固定的顏色，不同人一眼就分得出來（§3.6）。
- **共用背面**：兩張卡用同一份背面模板，以 Anki 的特殊欄位 `{{Card}}`（卡片模板名稱）區分細節。例如「認人」卡的頭部先顯示姓名、讀音和口訣，「見面前複習」卡先顯示「見面前必看」。
- **檔案**：現有慣例是一組 `_front.html`、`_back.html`，只能放一種卡片。這個類型改成 `_front_recognize.html`、`_front_review.html` 兩個正面加一個共用的 `_back.html`（§4、§6 R1）。

### 3.5 照片是選填，有的話存成 Anki 媒體檔

- **多數人沒有照片**：版面、卡片產生條件、頭像都以「沒有照片」為常態設計，照片只是加分（§3.4、§3.6）。
- `Photo` 欄位存 `<img src="檔名">`，圖片在 Anki 的媒體資料夾，會隨 AnkiWeb 同步到手機，離線也能看。
- 網頁版會代為下載卡面要用的圖片（`frontend/src/lib/ankiFrame.ts:293` 攔截 `img`、`audio`、`source`），不需要另外處理。
- handler 把 `Photo` 宣告為 `MEDIA_FIELDS`：網頁建卡與修改時鎖住（`note_service.py:140`）。在 `media_upload` 計畫完成前，照片在 Anki 桌面版編輯器貼上。
- 不採用存網址（如 `Avatar` 欄位的做法）：外部網址會失效，離線看不到，也會把私人照片放到外部服務。

### 3.6 背面版面

```text
┌ 頭部（一律顯示）──────────────────────────────┐
│ (頭像) 田中 悠希                                  │
│        たなか ゆうき · Yuki Tanaka · 田中さん/敬語  │
│        Geekly・顧問                               │
│        [客戶] ●●●○○  🎂 還有 12 天  📅 23 天前    │
└──────────────────────────────────────────────┘
┌ ⭐ 見面前必看（一律展開）──────────────────────┐
│ ❓ 下次要問：N1 考得如何？（10/01 記）            │
│ 🤝 我答應的：介紹 Ken 給他                         │
│ ⚠ 飲食：不吃生魚                                  │
│ 💬 上次（10/01）：轉職、京都旅行                    │
│ 📅 近期：N1 考試（12/07，還有 45 天）               │
└──────────────────────────────────────────────┘
▸ 🙂 興趣與喜好          ▸ 🏢 工作
▸ 👨‍👩‍👧 家庭               ▸ 💡 個性與價值觀（含讚美素材、可請教的事）
▸ 🗓️ 互動紀錄（時間軸，最新一筆展開；已完成的待追蹤事項）
▸ 🎁 禮物               ▸ 📅 重要日期    ▸ 📇 聯絡方式
▸ 🤝 認識脈絡（「🔍 查看介紹人」）  ▸ 🗒️ 備註
▸ 🔒 敏感話題（模糊顯示，點了才看得到）
```

- **頭部**：
  - 頭像取 `Photo`；沒有照片（常態）時用 `Display_Name` 的第一個字（同現有 `avatar-icon` 的做法，`JP_CoreVerb_Cloze_Dark_back.html:56-65`），背景色由姓名雜湊出固定的顏色。
  - 日文姓名用 `Name_JA` 與 `Name_JA_Kana` 組成 `<ruby>`。
  - 「N 天前」依 `Contact_Cadence` 上色：未過半為綠、過半為黃、超過為紅。
  - 生日倒數與年齡由 `Birthday` 算出。
- **見面前必看**：
  - `Followups_JSON` 中 `status` 為 `open` 的項目，依 `type` 分組。
  - `Dietary`（紅框）。
  - `Interactions_JSON` 最新一筆的日期與話題。
  - `Key_Dates_JSON` 中 60 天內的日子。
  - 整塊沒有內容時顯示一行「還沒有要注意的事」。
- **可收合區段**：預設只展開「互動紀錄」的最新一筆；**沒有內容的區段整段不顯示**，連標題都不出現。
- **每個區段左邊框一種顏色**，沿用現有 `.section` 的做法（`JP_CoreVerb_Cloze_Dark_back.html:5`）。

### 3.7 可收合區段用 `<details>`／`<summary>`

選原生 `<details>`，不用 Speaking_Coach 的 JS 切換 `display`（`Speaking_Coach_Dark_back.html:40-49`）：

- **不靠 JS 狀態**：Anki 桌面版、AnkiDroid、AnkiMobile 的 WebView 都支援，也比較容易用鍵盤和螢幕閱讀器操作。
- **網頁版不會誤翻面**：網頁的橋接腳本把 `summary` 視為控制項，點它不會送出翻面（`frontend/src/lib/ankiFrame.ts:367`、`:380`）。
- **外觀沿用現有樣式**：用 `.accordion-item`、`.accordion-summary`、`.accordion-detail`（`JP_CoreVerb_Cloze_Dark_style.css:280-328`）。隱藏原生三角形（`summary::-webkit-details-marker { display: none }`、`list-style: none`），以 `details[open] > summary .accordion-arrow` 旋轉 ▸。
- **展開狀態**：Anki 每次翻卡都重建畫面，預設狀態見 §3.6。記住使用者展開過哪些區段可以存在 `localStorage`（鍵含區段名，不含個人資料），讀寫包 `try`；桌面版有效，其他平台不一定保留，不保證。
- **敏感話題**：內容加 `.blur-reveal`（`filter: blur(6px)`），點一下才清楚，方便在別人面前打開卡片。

### 3.8 JS

- **沿用現有寫法**：欄位放在 `<div id="…-data" style="display:none">{{欄位}}</div>`，用一個立即執行的函式讀取與畫出（`JP_CoreVerb_Cloze_Dark_back.html:14-21`、`:123-220`）。
- **JS 寫在模板裡，不拆成共用的 `_person.js` 媒體檔**：網頁版只代為下載圖片與音檔，不會載入 `<script src>`（`ankiFrame.ts:293`），拆出去網頁版就失效。兩張卡共用同一份背面，所以 JS 只有一份。
- **共用函式**：

  | 函式 | 用途 |
  |---|---|
  | `readField(id)` | 讀欄位文字（`textContent`，`&quot;` 會自動還原） |
  | `readHtml(id)` | 讀文字區段的 HTML（`innerHTML`），保留條列 |
  | `parseJsonArray(id)` | 解析 JSON 陣列；先比照後端 `AnkiJsonFieldManager.parse_field_string`（`app/infrastructure/anki/json_modifier.py:36`）清掉 Anki 編輯器插入的 `<div>`、`<br>`、不換行空白並還原跳脫；失敗時回傳 `null`，該區段顯示「資料格式錯誤」與原文，而不是默默消失 |
  | `section(key, title, icon, color, nodes, open)` | 產生 `<details>` 區段；`nodes` 為空時不產生 |
  | `renderFurigana(text)` | 沿用現有的振假名轉換（`JP_CoreVerb_Cloze_Dark_back.html:222-229`） |
  | `daysSince(date)`、`nextOccurrence(date)`、`age(birthday)` | 日期計算，支援 `--MM-DD` |
  | `chips(text)` | 逗號分隔的短項目變成標籤；含 HTML 或換行時照原樣顯示 |
  | `byDateDesc(a, b)` | 互動紀錄由新到舊 |
  | `browseNote(id)` | 以 AnkiConnect `guiBrowse` 打開另一張筆記（同 `openMasterCard`，`JP_CoreVerb_Cloze_Dark_back.html:231-238`）；網頁版會在網頁裡切換過去 |

- **TG 深連結**（2026-10-24 依使用者要求擴充）：`Card_ID` 與 `TG_Bot` 都有值時顯示，是 Anki app（尤其手機）唯一的「寫入」入口：卡面不寫，交給 TG 寫。格式同現有深連結 `{動作}-{欄位}-{序號}-{Card_ID}`（`app/bot/utils/deep_link_parser.py:67-90`），最長約 57 字元，在 TG 的 64 字元上限內。

  | 動作 | 卡面上的入口 | TG 做什麼 |
  |---|---|---|
  | `padd-{JSON 欄位}-none` | 區段標題的「+」、TG 連結列的「+ 家人」等、「見面前必看」的「+ 待追蹤」、「➕ 記一筆互動」 | 依欄位進入新增一筆的問答 |
  | `pdel-{JSON 欄位}-{序號}_{指紋}` | **長按項目 3 秒** | 顯示那一筆的內容，確認後刪除 |
  | `pdone-Followups_JSON-{序號}_{指紋}` | 未完成待追蹤旁的 ✅ | 標成完成 |
  | `pview-Display_Name-none` | 「✏️ 改資料」 | 摘要與改欄位選單 |
  | `pphoto-Photo-none` | 「📷 照片」 | 傳照片替換 |

  - **長按 3 秒刪除**：按住時項目下緣出現 3 秒的進度條，放開、移動超過 10px 或捲動就取消；滿 3 秒時震動（支援時）並開啟 TG。有些 App 不允許程式自動開連結，所以同時在項目旁留下「🗑 在 TG 刪除這筆」連結，點它也能開。觸發後的那一下點擊不會再展開、收合或翻面；長按的項目設 `role="button"`，網頁版不會把它當成點卡面翻面。
  - **序號附指紋**：卡片上的序號可能過時（別處新增或刪除、手機尚未同步）。序號後面附上那一筆的指紋（主要文字欄位 `date`、`item`、`topics`、`name`、`label`、`handle`、`relation`、`channel` 以 `|` 串起，djb2 雜湊、UTF-16 碼元、36 進位），TG 刪除或標成完成前比對；對不上時在清單中找同指紋的那一筆，找不到就不動，請使用者重新整理卡片。前後端用同一個算法（模板的 `itemFingerprint`、後端的 `item_fingerprint`）。
  - 排序顯示的清單（互動紀錄、禮物）用的是存放時的序號，不是畫面上的順序。
- **模板只顯示，不寫資料**：網頁版不支援 `updateNoteFields`（卡面會出現「網頁版不支援」提示），桌面版呼叫後會跳回正面（`Speaking_Coach_Dark_back.html:297`）。修改交給網頁表單（非 JSON 欄位）、TG 或腳本。
- **防 XSS**：JSON 與單值欄位一律用 `textContent` 放進畫面；只有文字區段用 `innerHTML`，它們本來就是自己在 Anki 編輯器寫的 HTML。

### 3.9 CSS

- **沿用 `JP_CoreVerb_Cloze_Dark_style.css` 的暗色基底**：`.card`、`.section`、`.section-title`、`.glass-container`、`.pane-badge`、`badge-*`、`.accordion-*`、`.avatar-icon`，以及手機版的 media query。複製需要的部分，不引用其他類型的檔案。
- **新增的樣式**：

  | class | 用途 |
  |---|---|
  | `.person-header` | 72px 圓形頭像，姓名、讀音、徽章 |
  | `.closeness-dots` | 親密度圓點 |
  | `.must-see` | 「見面前必看」區塊，金色左邊框 |
  | `.followup` | 待追蹤事項；`.is-done` 為已完成（灰、刪除線） |
  | `.warn-box` | 紅框，用在飲食限制 |
  | `.chip` | 興趣標籤 |
  | `.timeline` | 互動紀錄：直線加圓點，日期在左 |
  | `.blur-reveal` | 模糊，點擊後清楚 |
  | `.json-error` | JSON 解析失敗時的提示與原文 |
  | `details` 箭頭 | 隱藏原生三角形、旋轉 ▸ |

### 3.10 日期格式

- 一律 `YYYY-MM-DD` 文字：能在 Anki 搜尋（例如 `Last_Contact:2026-0*`），字典序就是時間序。
- 生日不知年份寫 `--MM-DD`（ISO 8601 的格式），模板只顯示倒數、不顯示年齡。
- 解析失敗的日期照原文顯示，不計算。

### 3.11 handler

新增 `backend/app/services/task_handlers/person_profile_handler.py`，比照 `vocabulary_mining_handler.py:40-70`：

| 項目 | 值 |
|---|---|
| `handler_name` | `person_profile` |
| `title` | 人脈卡 |
| `supported_models` | `["Person_Profile_Dark"]` |
| `CAPABILITIES` | `create`：`supported=True`、`channels=("web", "tg")`、`flow="form"`、`deck="selected"`、`note_type="Person_Profile_Dark"`；`update_fields`、`delete` 開放；`actions` 見下；圖譜不開放 |
| `JSON_FIELDS` | `Interactions_JSON`、`Followups_JSON`、`Family_JSON`、`Gifts_JSON`、`Key_Dates_JSON`、`Contacts_JSON` |
| `MEDIA_FIELDS` | `Photo` |
| `CREATE_TAGS` | `("PersonProfile", "HandlerGenerated")` |
| `RELATION_LABEL_FIELD` | 不設（不進關聯表） |
| `CREATE_PARAMS` | `PersonCreateParams`：`Display_Name` 必填，其餘非 JSON、非媒體欄位選填 |

**建立要覆寫**（不用標準建立）：建立時要自動產生 `Card_ID`、把六個 JSON 欄位填 `[]`、寫入 `Updated_At`。註冊表規定覆寫建立就要宣告 `CREATE_PARAMS`（`registry.py:182-269`），所以用一個 Pydantic 模型列出可建立的欄位；網頁表單由它產生，TG 建立也用同一個模型驗證。

**具名動作**（`CAPABILITIES.actions`，經 `NoteService.run_action` 執行，`app/services/note_service.py:263`；handler 覆寫 `execute_update` 依 `action` 分派，寫入一律經 `AnkiJsonFieldManager`，`json_modifier.py:104-160`）：

| 動作 | 參數 | 做什麼 |
|---|---|---|
| `add_interaction` | 一筆 `Interactions_JSON` 的內容 | 追加一筆；日期比 `Last_Contact` 新時一併更新 `Last_Contact` |
| `add_followup` | 一筆 `Followups_JSON` 的內容 | 追加一筆，`status` 預設 `open` |
| `set_followup_status` | `index`、`status` | 標成完成或重新打開 |
| `add_item` | `field`（`Family_JSON`、`Gifts_JSON`、`Key_Dates_JSON`、`Contacts_JSON` 之一）、`item` | 追加一筆 |
| `remove_item` | `field`（六個 JSON 欄位之一）、`index` | 刪除一筆 |
| `set_photo` | `filename`（已存進 Anki 媒體） | 把 `Photo` 設成 `<img src="…">` |

- 每個動作的參數以 Pydantic 模型驗證（與 §3.3 的格式相同）；`run_action` 本身不驗證參數（`note_service.py:263` 收的是任意 dict），所以驗證寫在 handler 裡。
- 每次寫入（含 `update_fields`）都更新 `Updated_At`。

### 3.12 三個管道的欄位 CRUD 盤點

**管道**：
- **Anki app**：桌面版、AnkiDroid、AnkiMobile 的卡片與內建編輯器。
- **網頁**：FluencyTides 卡片瀏覽與編輯彈窗。
- **TG**：FluencyTides 的 Telegram bot。

**圖例**：✅ 現有功能就能做；🔧 這份計畫要實作（括號內是階段，見 §5）；🔗 依其他計畫；⛔ 不做（附原因）。

**現況**（已定位，2026-10-23 於 `feat/card-transitions`）：
- **TG 沒有通用的「改某筆記的某欄位」流程**：每個流程都為特定卡片類型而寫；找既有筆記只靠深連結帶的 `Card_ID`（`commands.py:125`、`callbacks.py:89`、`voice.py:147`），沒有用名字搜尋的入口。
- **JSON 欄位只能經 handler 的具名動作追加**（例如口說卡的 `add_recording`，`voice.py:290-303`）；沒有通用的寫入方法或網頁端點。網頁的 `PATCH` 會拒絕 JSON 與媒體欄位（`FIELD_NOT_EDITABLE`）。
- **具名動作只有 TG 在用**：`NoteService.run_action` 沒有對應的網頁端點。
- **把照片寫進媒體欄位**：沒有現成流程。現有的 `storeMediaFile` 呼叫都是把檔名寫進 JSON 項目（`voice.py:233-243`、`media.py:30-99`）。
- **TG 收到一般文字時只回說明**（`messages.py:116-130`）；沒有「打一段話 → LLM 整理 → 追加到既有筆記」的流程。

**筆記本身**

| 操作 | Anki app | 網頁 | TG |
|---|---|---|---|
| 建立 | ✅ 新增筆記；`Card_ID` 留空（之後補，見 R7） | 🔧（P1）`POST /handlers/person_profile/notes`，表單由 `CREATE_PARAMS` 產生 | 🔧（P2）`/newcard` →「人脈卡」→ 輸入稱呼與一句描述 |
| 讀取 | ✅ 卡片 | ✅ 卡片瀏覽 | 🔧（P2）`/p 名字` 找人，回覆「見面前必看」摘要 |
| 刪除 | ✅ | 🔧（P1）`DELETE` | ⛔ 誤刪無法復原，只在 Anki app 與網頁刪 |
| 標籤（圈子） | ✅ | ✅（現有卡片瀏覽可看；不在本計畫改） | ⛔ 不需要 |

**單值與文字欄位**（姓名、公司、興趣、飲食限制、個性…）

| 操作 | Anki app | 網頁 | TG |
|---|---|---|---|
| 讀取 | ✅ | ✅ | 🔧（P2）摘要只列「見面前必看」；完整資料用「📇 全部資料」按鈕 |
| 新增或修改 | ✅ 編輯器 | 🔧（P1）`PATCH`（`update_fields`） | 🔧（P3）「✏️ 改資料」：按鈕選欄位 → 輸入新內容 → 選「取代」或「接在後面」→ `update_fields`；🔧（P5）一段話由 LLM 判斷要更新哪些欄位，預覽後確認 |
| 清空 | ✅ | 🔧（P1）`PATCH` 送空字串 | 🔧（P3）同上，輸入「清空」 |

**JSON 清單**（互動紀錄、待追蹤、家人、禮物、重要日期、聯絡方式）

| 操作 | Anki app | 網頁 | TG |
|---|---|---|---|
| 讀取 | ✅ 卡片（時間軸、清單） | ✅ 卡片；欄位彈窗唯讀 | 🔧（P2）摘要含未完成的待追蹤與最新一筆互動 |
| 新增一筆 | ⚠ 只能手改原始 JSON，不建議（R2）；或點卡面上的「+」開啟 TG 新增 | 🔗 `json_field_editor`（P4） | 🔧（P3）`add_interaction`、`add_followup`、`add_item`；從卡面「➕ 記一筆互動」深連結或 `/p` 摘要的按鈕進入；🔧（P5）一段話或語音由 LLM 整理成一筆互動加新的待追蹤 |
| 修改一筆 | ⚠ 同上 | 🔗 `json_field_editor`（P4） | 🔧（P3）只做最常用的「待追蹤標成完成」（`set_followup_status`）；其他修改用刪掉再新增 |
| 刪除一筆 | ⚠ 同上；或長按卡面上的項目 3 秒，開啟 TG 刪除 | 🔗 `json_field_editor`（P4） | 🔧（P3）`remove_item`：從卡面長按（`pdel` 深連結）或 TG 清單的「🗑」進入，顯示內容確認後刪除。不沿用現有 `del-` 深連結（它跳過能力檢查直接寫，且欄位白名單只有口說卡的欄位，`commands.py:272-279`） |

**照片**

| 操作 | Anki app | 網頁 | TG |
|---|---|---|---|
| 讀取 | ✅ | ✅ 網頁代為下載圖片 | ⛔ 不需要 |
| 新增或替換 | ✅ 編輯器貼上 | 🔗 `media_upload` | 🔧（P3）在某人的摘要按「📷 換照片」→ 傳一張照片 → `storeMediaFile` 存成 `person_{Card_ID}.jpg` → `set_photo`；`media.py` 目前只在 `wait_avatar_upload` 狀態收照片，要加一個狀態分支 |
| 移除 | ✅ | 🔗 `media_upload` | 🔧（P3）「📷 移除照片」→ `set_photo` 傳空值 |

**自動維護的欄位**（`Card_ID`、`Last_Contact`、`Updated_At`）

| 欄位 | 由誰寫 |
|---|---|
| `Card_ID` | 網頁、TG 建立時產生；Anki app 建的人留空，由 🔧（P2）補 ID 腳本或 TG 第一次找到他時補上（R7） |
| `Last_Contact` | `add_interaction` 自動更新；Anki app 與網頁也可以手改 |
| `Updated_At` | handler 每次寫入自動更新；Anki app 手改時不會更新（可接受） |

**整理成要實作的項目**：

| # | 項目 | 階段 |
|---|---|---|
| 1 | handler：覆寫建立（`CREATE_PARAMS`、產生 `Card_ID`、JSON 填 `[]`）、`update_fields`、`delete` | P1 |
| 2 | handler：具名動作 `add_interaction`、`add_followup`、`set_followup_status`、`add_item`、`remove_item`、`set_photo`，各自的參數模型 | P3 |
| 3 | TG：`/p 名字` 找人（搜 `Display_Name`、三種姓名、讀音、羅馬拼音），多筆時列按鈕選 | P2 |
| 4 | TG：摘要訊息（見面前必看）與「📇 全部資料」 | P2 |
| 5 | TG：`/newcard` 加「人脈卡」（`newcard_menu.py:50` 的靜態清單） | P2 |
| 6 | TG：深連結動作（記一筆互動、改資料）與解析（`deep_link_parser.py`） | P3 |
| 7 | TG：記一筆互動、新增待追蹤、標成完成、刪除一筆的對話流程 | P3 |
| 8 | TG：改欄位（選欄位 → 取代或接在後面） | P3 |
| 9 | TG：換照片、移除照片（`media.py` 新狀態） | P3 |
| 10 | 補 `Card_ID` 的腳本（Anki app 建的人） | P2 |
| 11 | 網頁 JSON 清單編輯 | P4（`json_field_editor`） |
| 12 | 網頁照片上傳 | `media_upload` |
| 13 | TG：一段話或語音 → LLM → 一筆互動、新的待追蹤、欄位更新建議 → 預覽確認 | P5 |

### 3.13 TG 流程

**找人與摘要（P2）**

```text
/p 田中
→ 只找到一位：直接回摘要
→ 找到多位：列出「田中さん（Geekly・顧問）」等按鈕，點選後回摘要
```

摘要訊息：

```text
👤 田中さん（田中 悠希・たなか ゆうき）
   Geekly・顧問｜客戶｜上次聯絡 23 天前
❓ 下次要問：N1 考得如何？
🤝 我答應的：介紹 Ken 給他
⚠ 飲食：不吃生魚
💬 上次（10/01）：轉職、京都旅行
[➕ 記一筆互動] [✅ 待追蹤] [✏️ 改資料] [📷 照片] [📇 全部資料]
```

- **搜尋**：限定 `note:Person_Profile_Dark`，在姓名相關欄位比對（AnkiConnect `findNotes`），沿用現有的查詢組字工具（`field_term`、`note_type_term`，`app/infrastructure/anki/search.py:43`、`:70`）處理跳脫。
- **摘要格式**：只用 TG 支援的 HTML 標籤；長度超過上限時截斷互動紀錄。

**記一筆互動（P3，手動）**

1. 從摘要按「➕ 記一筆互動」，或從卡面深連結進來。
2. bot 依序問：日期（預設今天）、聊了什麼、對方的近況、要記下的一句話（可略過）、下次要問的事（可略過，每行一筆）。
3. 預覽後確認：`add_interaction` 一次，`add_followup` 每筆待追蹤各一次。

**改欄位（P3）**

1. 按「✏️ 改資料」，列出欄位分組按鈕（姓名、工作、興趣、飲食、個性、溝通…）。
2. 選欄位後顯示目前內容，輸入新內容。
3. 選「取代」或「接在後面」（文字欄位常用接在後面），送出 `update_fields`。

**LLM 整理（P5）**

1. 在某人的摘要按「🧠 用一段話記錄」，再打一段話或傳一段語音。
2. 語音先轉文字（能力路由的 `stt`，用途鍵 `person_profile`），轉出的文字先回給使用者看。
3. LLM 以結構化輸出（`generate_structured_data`，同 `expression_handler.py:134-198`）回傳：一筆互動、新的待追蹤、建議更新的欄位（例如「興趣：加上 登山」）。
4. 預覽，逐項勾選後寫入（`add_interaction`、`add_followup`、`update_fields`）。
5. 提示詞放在 `app/templates/prompts/anki/`，模型路由沿用 capability routing（`route(...)`）。

**AI 分析與話題（P6，2026-10-24 追加）**

1. 入口：TG 摘要的「AI 分析與話題」，或卡片背面的同名連結（深連結 `ptopic-Display_Name-none-{Card_ID}`）。
2. LLM 扮演人際關係諮商師（卡內基原則），先分析這個人（`insight`：個性、在意的事、相處方式、最近的處境、這次要留意的地方），再依分析推薦 3～5 個話題（`topics`：話題、依據、可以直接說出口的開場白，用平常交談的語言）。分析放在話題前面，讓 LLM 先想清楚再推薦。
3. 隱私：送出前把本人與家人的名字換成代號（`〔本人〕`、`〔家人1〕`…），回來後換回稱呼（稱呼已含「さん」等敬稱時，去掉 LLM 在代號後多加的敬稱）。
   - 遮的範圍（最基本的字串比對）：姓名欄位的完整值（稱呼另加去掉敬稱的部分）、只寫名字的情況（「姓 名」分開寫的欄位取出的名字）、家人清單裡的名字；假名另加平假名、片假名兩種寫法。不另外拆出姓。
   - 不送的欄位：姓名欄位本身、名字記憶法、外貌、敏感話題（只告訴 LLM「有不想被提起的話題」）、介紹人 ID、聯絡方式。
   - 限制：其他文字裡提到的第三者名字（例如「介紹 Ken 給他」）沒有登記，照原文送出。
   - 開關：名字和一般詞同音（例如「ゆうき」與勇気）、遮了反而看不懂時，可以替這個人加標籤 `AI內文不遮名字`：內文照原文送出，姓名欄位照樣不送。預設沒有標籤＝遮。用標籤而不是新欄位：改筆記類型的欄位會讓 AnkiWeb 要求完整同步。切換方式：TG 分析結果下方的「內文名字：遮／不遮」按鈕，或在 Anki app 直接加減標籤；卡片背面有標籤時顯示「AI：內文不遮名字」。
4. 只回覆，不寫入卡片；提示詞 `Person_Profile_Topics.j2`，用途鍵 `person_profile.topics`（沒有另外設定時沿用 `person_profile`）。

## 4. 改動清單

### Anki 模型與模板

| 檔案 | 改動 |
|---|---|
| `backend/app/anki_models/Person_Profile_Dark.json` | 新增：`modelName`、`inOrderFields`（§3.2 的順序） |
| `backend/app/anki_models/Person_Profile_Dark_front_recognize.html` | 新增：「認人」正面，只引用線索欄位（§3.4） |
| `backend/app/anki_models/Person_Profile_Dark_front_review.html` | 新增：「見面前複習」正面 |
| `backend/app/anki_models/Person_Profile_Dark_back.html` | 新增：共用背面，含全部 JS |
| `backend/app/anki_models/Person_Profile_Dark_style.css` | 新增：§3.9 |

在 Anki 建立筆記類型（手動，與現有類型相同）：

1. 工具 → 管理筆記類型 → 新增 → 「新增：基本」，命名 `Person_Profile_Dark`。
2. 欄位依 `Person_Profile_Dark.json` 的 `inOrderFields` 建立，順序相同。
3. 卡片 → 模板「Card 1」改名 `認人`，貼上 `_front_recognize.html` 與 `_back.html`；選項 → 新增卡片類型 `見面前複習`，貼上 `_front_review.html` 與 `_back.html`；樣式貼上 `_style.css`。

### Backend

| 檔案 | 改動 |
|---|---|
| `app/services/task_handlers/person_profile_handler.py` | 新增：§3.11（覆寫建立、`update_fields`、`delete`、具名動作），類別加 `@register_handler` |
| `app/schemas/person_profile.py` | 新增：`PersonCreateParams` 與六種 JSON 項目、各動作參數的 Pydantic 模型 |
| `app/services/task_handlers/__init__.py` | 加入 `person_profile_handler` 的 import（註冊在 import 套件時執行，`__init__.py:10-21`） |
| `tests/test_handler_declarations.py` | 比照 `VocabularyMiningHandler` 的測試（`:139-189`）：能力宣告、JSON 與媒體欄位在建立／修改時被鎖住 |
| `tests/test_person_profile_handler.py` | 新增：建立時產生 `Card_ID` 與 `[]`、各具名動作（含 `Last_Contact` 更新、參數驗證失敗） |
| `app/bot/handlers/person_profile.py` | 新增（P2、P3）：`/p` 找人、摘要、記一筆互動、待追蹤、改欄位、照片的路由與狀態 |
| `app/bot/dispatcher.py` | 註冊新的 router（放在 `messages` 這個兜底路由之前，`dispatcher.py:51-63`） |
| `app/bot/handlers/newcard_menu.py` | `CARD_TYPES` 加「人脈卡」與對應分派（`:50-66`、`:215-230`） |
| `app/bot/utils/deep_link_parser.py`、`app/schemas/tg/deep_link.py` | 加入人脈卡的深連結動作 |
| `app/bot/handlers/media.py` | 加「換照片」狀態分支 |
| `scripts/local_anki/Person_Profile_Dark/backfill_card_ids.py` | 新增（P2）：替 `Card_ID` 為空的人補上 ID |
| `app/templates/prompts/anki/Person_Profile_Ingest.j2`、`app/services/person_profile_ingest.py`、`app/schemas/llm/person_profile.py`、相關 bot 檔案 | 新增（P5）：LLM 整理的提示詞與流程（同 `expression_correction.j2` 的位置） |

### Frontend

不動。建卡表單、編輯彈窗、卡面顯示都是依 handler 宣告產生的通用流程。

### 測試

- backend：handler 單元測試（見上）；註冊表的宣告檢查會在註冊時一併跑。
- 模板：沒有自動測試。用 §8 的範例資料，在 Anki 桌面版與網頁版各看一次「資料齊全」「只有姓名」「JSON 格式錯誤」三種筆記。

## 5. 實作順序

| 階段 | 內容 | 為何這個順序 |
|---|---|---|
| **P0** | 模型定義、兩個正面、共用背面、CSS；在 Anki 建立筆記類型，用 §8 範例資料檢查 | 卡片本身就能用了（Anki app 可完整 CRUD），先確認版面與資料格式 |
| **P1** | handler：覆寫建立、`update_fields`、`delete`；網頁建立、修改、刪除 | 網頁能建人、改單值與文字欄位 |
| **P2** | TG：`/p` 找人、摘要、`/newcard` 建立；補 `Card_ID` 腳本 | 手機上不開 Anki 也能查人；後面的 TG 寫入都從摘要出發 |
| **P3** | handler 具名動作；TG：記一筆互動、待追蹤、改欄位、照片、深連結 | 「見完面馬上記」的主要入口 |
| **P4** | 網頁 JSON 清單編輯 | 依 `json_field_editor` 計畫做通用版本，人脈卡的六種格式是其中一個使用者。2026-10-24 實作時與該計畫的差異：格式宣告用另一個類別屬性 `JSON_FIELD_SPECS`（`JSON_FIELDS` 維持名稱的 tuple，其他 handler 不用改）；`GET /handlers` 多一個鍵 `json_field_specs`（`json_fields` 照舊是名稱清單，前端唯讀判斷不受影響）；`after_json_update` 讓 handler 在整欄寫入後維護相關欄位（人脈卡：`Updated_At`、`Last_Contact`）。單字卡 `_v2` 的試點留待之後 |
| **P5** | TG：一段話或語音 → LLM 整理 → 預覽確認後寫入 | 最省力的記錄方式；依賴 P3 的動作，放最後。2026-10-24 實作時與 §3.13 的差異：入口只有摘要的「🧠 用一段話記錄」（要先知道是誰，不接受沒選人時直接打的話）；用途鍵 `person_profile` 同時宣告 `llm` 與 `stt`，`/setconfig` 可分別設定；LLM 的建議先過一次格式檢查（日期不對的互動改成今天、期限格式不對的待追蹤與不開放或格式不對的欄位建議丟掉、已經有的內容不再建議、單一值欄位一律取代）；同一個欄位的多項建議合成一次 `update_fields`；預覽一則訊息放不下的建議不列出也不寫入；儲存中途失敗時保留狀態、已存的標 💾，再按儲存只補存其他的 |
| **P6** | TG：AI 分析與話題（諮商師角度先分析再推薦話題；名字以代號送出） | 2026-10-24 追加，見 §3.13「AI 分析與話題」 |

## 6. 風險與未知

| # | 風險 | 應對 |
|---|---|---|
| R1 | 現有模型檔慣例只有一組 `_front.html`、`_back.html`；`AnkiClient.create_model`（`app/infrastructure/anki/client.py:1107`）若日後用來自動匯入，不認得兩個正面 | 本計畫手動在 Anki 建立筆記類型（與現有做法相同）；若要自動匯入，另行擴充，讓它讀 `_front_*.html` 產生多個模板 |
| R2 | 在 Anki 編輯器手改 JSON 欄位會壞 | 模板解析失敗時顯示「資料格式錯誤」與原文（§3.8），不會默默消失；JSON 欄位只由 TG、腳本、日後的表單寫入 |
| R3 | 同名的人：第一個欄位 `Display_Name` 重複時，Anki 編輯器會警告「重複」 | 只是警告，不會擋；必要時在稱呼後加區別，例如「田中さん（Geekly）」 |
| R4 | 個人資料會隨 AnkiWeb 同步，也會經過 FluencyTides 後端 | 敏感資訊只在對方主動告知、且真的有幫助時才記；`Sensitive` 區段預設模糊；不放健康、財務等細節 |
| R5 | AnkiDroid 舊版 WebView 對 `<details>` 的支援 | P0 在手機上實際打開確認；不支援時改回 JS 切換（`Speaking_Coach_Dark_back.html:40-49` 的做法） |
| R6 | `localStorage` 在部分平台不保留、或被停用 | 讀寫包 `try`；不保留時退回預設展開狀態，不影響使用 |
| R7 | Anki app 建立的人沒有 `Card_ID`，TG 的深連結與後續寫入找不到 | 背面沒有 `Card_ID` 時不顯示 TG 按鈕；TG 的 `/p` 用名字找到他時自動補上；另有補 ID 腳本 |
| R8 | 同時從兩處改同一個 JSON 欄位：`AnkiJsonFieldManager` 每次讀整欄、改、寫整欄，沒有鎖（`json_modifier.py:104-160`），後寫的會蓋掉先寫的 | 個人使用，同時編輯機率低；網頁端由 `json_field_editor` 的 G3（偵測衝突）處理；TG 每次動作前重讀 |
| R9 | TG 寫入後要等 AnkiWeb 同步，手機上的 Anki 才看得到；手機上同時在改同一張卡可能衝突 | 每次寫入後端都會強制同步（`note_service.py:350`）；在手機上用 TG 記錄時，先同步再打開卡片 |
| R10 | TG 用名字搜尋時，同名或讀音相近的人很多 | 結果超過一筆就列按鈕，附公司與職稱區分；超過 10 筆請使用者打更完整的名字 |

## 7. 驗收標準

- [ ] Anki 桌面版：有任一線索（照片、外貌、公司、職稱、認識場合）的人產生兩張卡；只有姓名的人只產生「見面前複習」；之後補上線索會補產生「認人」
- [ ] 「認人」正面不會出現姓名、讀音、稱呼
- [ ] 沒有照片時：「認人」正面以文字線索出題；背面頭像是姓名首字，不同人顏色不同
- [ ] 背面頭部：頭像、日文姓名振假名、徽章、親密度、生日倒數、「N 天前」顏色依 `Contact_Cadence` 正確
- [ ] 「見面前必看」只列未完成的待追蹤事項，並顯示飲食限制、最新一筆互動、60 天內的重要日子
- [ ] 沒有內容的區段整段不顯示；只填了姓名的人，卡片沒有空標題
- [ ] `<details>` 區段能展開收合；互動紀錄的最新一筆預設展開
- [ ] 敏感話題預設模糊，點擊後清楚
- [ ] JSON 欄位格式錯誤時，該區段顯示「資料格式錯誤」與原文，其他區段正常
- [ ] 「🔍 查看介紹人」在桌面版打開瀏覽器，在網頁版切換到那張卡
- [ ] 網頁版：點收合區塊不會翻面；有照片的人照片正常顯示
- [ ] 網頁：能建立人脈卡（表單不含 JSON 與媒體欄位）、修改單值與文字欄位、刪除；建立後 `Card_ID` 有值、JSON 欄位為 `[]`
- [ ] TG：`/p 名字` 找到人並回摘要；同名時列按鈕
- [ ] TG：`/newcard` 能建立人脈卡
- [ ] TG：記一筆互動後卡片的互動紀錄與 `Last_Contact` 更新；新增的待追蹤出現在「見面前必看」，標成完成後消失
- [ ] TG：改欄位的「取代」與「接在後面」都正確；能刪除 JSON 清單中的一筆
- [ ] TG：傳照片後卡片出現照片、「認人」正面顯示照片
- [ ] 卡面「➕ 記一筆互動」深連結在 Anki app 開啟 TG 並直接進入該人的記錄流程
- [ ] TG（P5）：一段話整理出的互動、待追蹤、欄位更新建議正確，預覽確認後才寫入
- [ ] TG（P6）：AI 分析與話題的內容具體、依據資料；送給 LLM 的提示裡沒有本人與家人的名字，回覆裡換回了稱呼
- [ ] AnkiDroid（或 AnkiMobile）實機：版面正常、`<details>` 可用

## 8. 範例資料（P0 檢查用，虛構人物）

資料齊全、**沒有照片**的一筆（多數人的情況；JSON 欄位直接貼原始 JSON）：

| 欄位 | 值 |
|---|---|
| `Display_Name` | 田中さん |
| `Name_ZH` | 田中悠希 |
| `Name_EN` | Yuki Tanaka |
| `Name_JA` | 田中 悠希 |
| `Name_JA_Kana` | たなか ゆうき |
| `Name_Romaji` | Tanaka Yūki |
| `Honorific` | さん／敬語 |
| `Birthday` | `--11-04` |
| `Relationship` | 客戶 |
| `Closeness` | 3 |
| `Appearance` | 高個子、黑框眼鏡、關西腔 |
| `Met_Date` / `Met_Where` | `2026-06-07`／Geekly 面談 |
| `Company` / `Title` | Geekly／顧問 |
| `Interests` | 登山, 精釀啤酒, 推理小說 |
| `Dietary` | 不吃生魚 |
| `Last_Contact` | `2026-10-01` |
| `Contact_Cadence` | 30 |
| `Interactions_JSON`、`Followups_JSON`、`Family_JSON`、`Key_Dates_JSON` | §3.3 的例子 |

另外三筆：

- **有照片**：同上，再在 `Photo` 貼一張圖，檢查「認人」正面照片在上、文字線索照樣列出，背面頭像換成照片。
- **只有姓名**：只填 `Display_Name` 與 `Name_JA`，檢查只產生「見面前複習」、空區段全部不顯示。
- **JSON 格式錯誤**：`Followups_JSON` 填 `[{"item": "少了右括號"`，檢查錯誤提示。
