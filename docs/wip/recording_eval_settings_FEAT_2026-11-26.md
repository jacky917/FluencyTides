# FEAT — 模型設定統一三層（文字全域、語音全域）、錄音評分由 handler 宣告、每筆錄音記下評分方式

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-26 |
| **性質** | 機能調整（能力路由的層級、錄音評分的設定與紀錄） |
| **狀態** | 🛠️ 實作完成，待 PR（分支 `feat/routing-three-layers`；實作紀錄見 §8） |
| **範圍** | 能力路由的解析（`app/infrastructure/llm/routing.py`）與寫入服務（`app/services/capability_route_service.py`）；`/setconfig`（`app/bot/handlers/models_menu.py`）與網頁的模型設定頁（`app/api/capability_routes.py`、前端）；`app/bot/handlers/voice.py`（錄音評分流程）與各口說 handler 的錄音評分宣告；錄音紀錄的格式（`app/schemas/speaking.py` 的 `RecordingItem`）與卡面模板 |
| **不動** | 評分器本身（`listen`／`stt_llm`／`stt_diff` 的評分邏輯）；各用途用哪些能力（`route(...)` 宣告的 capabilities），唯一的例外是 D6 拿掉人脈卡 `person_profile` 的 `stt` |
| **PR / 進度** | D1–D6 都已實作在 `feat/routing-three-layers`（2026-11-28 多面向審查後修正），尚未開 PR |
| **關聯文件** | `docs/archive/capability_routing_FEAT_2026-10-19.md`（能力路由、用途鍵、流程與分支）、`docs/wip/speaking_monologue_FEAT_2026-11-26.md`（三語長文卡；它的錄音評分要靠這份先做完） |

---

## 1. 問題

### 1.1 模型設定的層數不一致，而且只有一個「全域」

能力路由（2026-10-19）的規則：用途鍵寫成「流程.分支」，查不到設定時沿著「.」往上找，再找全域 `*`，最後是 `.env`（`routing.route_layers`）。

所以層數依用途不同：

| 用途 | 查找順序 | 層數 |
|---|---|---|
| 大部分文字用途（自他動詞子卡、核心動詞子卡、讀音判斷、外語糾錯、三語卡生成說法） | 用途 → `*` → `.env` | 三層 |
| 人脈卡「網頁 AI 整理」（`person_profile.fill`）、「分析與想話題」（`person_profile.topics`） | 用途 → 「TG 用一段話記錄」（`person_profile`）→ `*` → `.env` | 四層 |
| 三語卡錄音評分（`recording_eval.speaking_trilingual`） | 用途 → 「錄音評分」（`recording_eval`）→ `*` → `.env` | 四層 |
| 對話卡錄音評分（沒有自己的分支） | 「錄音評分」→ `*` → `.env` | 三層 |

另外，`*` 同時是文字和語音的全域：

- 建卡等用途的文字模型存在 `*`，錄音評分的評分方式、聽音檔的模型、STT 也會往上找到 `*`。
- 錄音評分用來看逐字稿的文字模型則特別不讀 `*`（以免跟著改用建卡的模型），是一個例外。
- 使用者 2026-11-26 指出：全域的文字模型不一定能聽音檔，放在同一層看起來就像錄音評分會跟著它走。（程式裡聽音檔的模型其實是另一項設定，只能從可聽音檔的白名單選，`capabilities.py` 用 `find_audio_model` 檢查，所以不會真的用錯；但很容易誤會。）

### 1.2 錄音評分是看欄位名稱猜 handler

`app/bot/handlers/voice.py` 決定評分方式時，不是問卡片的 handler，而是看錄音欄位的名稱：

- 欄位有語言後綴（`Recordings_JA` 等）就當成三語卡，沒有就當成對話卡：`handler_name = "speaking_trilingual" if trilingual_lang else "speaking_coach"`。
- 評分提示詞（三語卡的 `Speaking_Trilingual_ZH／JA／EN.j2`、對話卡的 `audio_evaluator.j2`）、目標語言、參考答案從哪個欄位讀，也都依這個猜測決定。

**由來**：最早只有對話卡；三語卡接進錄音流程時還沒有 handler 註冊表，最直接的做法就是看欄位後綴分流。2026-10-19 的能力路由讓評分設定依 handler 分開，但 `voice.py` 只是把原本的分支對應成兩個 handler 名稱；那份計畫書寫了「階段 C 移到 `RecordingService`」，一直沒做。

**後果**：新的口說卡（例如三語長文卡，錄音欄位也叫 `Recordings_*`）會被當成三語卡，用錯評分設定、錯的提示詞，參考答案也讀錯欄位。

### 1.3 錄音紀錄沒有記下是怎麼評的

每筆錄音寫回卡片時（`voice.py` 的 `new_recording`）只有：

```json
{"date": "2026-11-26", "audio": "…", "transcript": "…", "comment": "…", "score": 85}
```

用哪種評分方式、哪個模型評的，只出現在 Telegram 的回覆裡，沒有存下來；時間也只有日期。設定切換之後，同一張卡的分數可能來自不同的評分方式，事後分不出來。

### 1.4 使用者要的效果（2026-11-26）

- 文字和語音的模型設定都是三層：`.env` < 全局（資料庫） < 用途／handler（資料庫）。資料庫有設定就用，資料庫沒有才用 `.env` 保底。
- 文字、語音各有自己的全局。
- 兩個全局都有「全局生效」開關：開的時候那一邊的所有用途都用它，關的時候各用途用自己的設定。
- 不生效的設定照樣存著，不刪除，避免紀錄丟失。
- 每筆錄音記下評分的模型、時間等資訊。

## 2. 目標與非目標

**目標**

- G1 所有用途的模型設定都是三層：用途自己的設定 → 全局 → `.env`；拿掉「沿著『.』往上找」的中間層。
- G2 文字全域、語音全域分開，各有「全局生效」開關；不生效的設定照樣保存，而且在 Telegram 與網頁上看得出來。
- G3 錄音評分的設定（目標語言、評分提示詞、參考答案欄位、用途鍵）由 handler 宣告；`voice.py` 從卡片的筆記類型查到 handler，再讀它的宣告。
- G4 每筆錄音記下評分時間（含時分）、評分方式與用到的模型，卡面上顯示出來。

**非目標**

- 不改評分器本身的評分邏輯。
- 不改各用途用哪些能力。
- 不回填舊的錄音紀錄（不知道當時是怎麼評的）。

## 3. 設計決策

### D1 統一三層，拿掉中間層

| | 第 1 層（最優先） | 第 2 層 | 第 3 層（保底） |
|---|---|---|---|
| 文字 | 用途自己的設定 | 文字全域（有開關，D3） | `.env` |
| 語音 | handler／用途自己的設定 | 語音全域（有開關，D3） | `.env` |

- 查不到設定時**不再沿著「.」往上找**。用途鍵的名稱照舊（例如 `person_profile.fill`），「.」只是命名，不再代表上下層。
- 拿掉之後的差別：
  - 人脈卡「網頁 AI 整理」「分析與想話題」沒有自己的設定時，直接用文字全域，不再先借「TG 用一段話記錄」的設定。
  - 錄音評分的中間層「錄音評分」（`recording_eval`）拿掉，由語音全域取代；對話卡改成和三語卡一樣有自己的用途鍵（例如 `recording_eval.speaking_coach`）。
- 原本放在流程鍵上、讓分支沿用的宣告要搬到各自的位置：
  - 評分方式的限縮（`allowed_strategies` 目前「鍵本身沒有就看流程鍵」）改成每個用途自己宣告。
  - 錄音評分文字模型的預設（`recording_eval` 的 `env_defaults`：audio 端點＋`STT_LLM_MODEL_NAME`）改成語音那一層的 `.env` 預設。
- 來源標示（`models_menu.py`、`factory.py` 的「用途／流程／全域／.env」）拿掉「流程」，改成「用途／文字全域／語音全域／.env」；`/setconfig` 清單的縮排（`parent_key` 判斷子項）也拿掉。
- 2026-11-26 查正式的 `cfg_capability_routes`：一列設定都沒有，現在全部用 `.env`，所以拿掉中間層不會改變任何現有的行為，也不用搬資料。

### D2 文字全域、語音全域分開

**哪些能力歸哪個全域**：

| 能力 | 全域 |
|---|---|
| `llm`（文字模型） | 文字全域；**錄音評分用來看逐字稿的文字模型例外，歸語音全域**（它是錄音設定的一部分，不跟建卡的模型走） |
| `audio_eval`（評分方式）、`audio_llm`（聽音檔的模型）、`stt`（語音轉文字） | 語音全域 |

- 拿掉人脈卡的 TG 語音輸入之後（D6），語音只剩錄音評分在用，文字用途都只用文字模型，不會有一個用途同時用到兩邊。
- 錄音評分的用途要宣告自己屬於語音（`route(..., voice=True)`），它的 `llm` 才會走語音全域；這個宣告取代現在「錄音評分的文字模型不讀 `*`」的特例。
- **存法**：文字全域就是現在的 `*`；語音全域用一個新的保留鍵存，不和 `*` 共用，否則錄音評分的文字模型會和 `*` 的文字模型存在同一列。保留鍵定為 `*voice`：用途鍵規則只允許英數字與 `. _ -`，所以它不可能和宣告的用途鍵撞名；`Route` 建構時也直接拒絕 `*` 與 `*voice`。
- `/setconfig` 和網頁都分成「全域（文字）」和「全域（語音）」兩項。

一組錄音評分設定 = 評分方式 ＋ 這種方式用到的模型：

| 評分方式 | 用到的模型 |
|---|---|
| `listen` | 聽音檔的模型（直接聽錄音評分） |
| `stt_llm` | STT 模型（轉成文字）＋ 文字模型（看逐字稿評分） |
| `stt_diff` | STT 模型（轉成文字後逐字比對，不用大模型） |

### D3 兩個全域各有「全局生效」開關

使用者 2026-11-26 提出（先提語音，後來決定文字也要）。兩個開關各自獨立，規則相同：

| 開關 | 那一邊的用途用什麼 |
|---|---|
| 開 | 一律整組用全域的設定；全域沒設到的項目用 `.env`，不會去拿用途自己的設定 |
| 關 | 用途自己的設定 → 全域存著的設定 → `.env` |

- 原則：資料庫有設定就用，資料庫沒有才用 `.env` 保底。開關關掉時，全域存著的設定照樣是沒有自己設定的用途的預設。
- 開的時候整組用全域，不會出現「評分方式來自 A、模型來自 B」的混搭。
- 開關的狀態存在資料庫（重啟後保留）：實作成同一張表裡的一列假能力 `global_switch`（`provider` 為 `on`），不用改資料表；關掉就刪掉那一列。它不在可設定的能力裡，一般的設定與恢復預設碰不到它，清單也不會把它當成孤兒能力。值無法辨識時視為關閉並記警告。
- 切換開關（`set_global_switch`）和 `set_route` 一樣，在寫入之前就組好回傳的狀態：寫入提交後不再讀 DB，不會因為讀回失敗被說成切換失敗。
- **文字全域的開關**：涵蓋所有文字用途的 `llm`（建卡、糾錯、讀音判斷、人脈卡整理……），不包括錄音評分看逐字稿的文字模型（它歸語音，D2）。
- **語音全域的開關**：涵蓋所有語音用途（2026-11-26 使用者決定：要包含所有的語音）。拿掉人脈卡的 TG 語音輸入之後（D6），就是各卡片類型的錄音評分（評分方式、聽音檔的模型、STT、評分用文字模型）。

例子：

| 動作 | 結果 |
|---|---|
| 三語卡設成 `listen`，語音全域設成 `stt_diff`，開關關 | 三語卡用 `listen`；沒有自己設定的卡片類型用 `stt_diff` |
| 開關打開 | 所有語音用途都用 `stt_diff`；三語卡的 `listen` 照樣存著，不生效 |
| 開關再關掉 | 三語卡回到 `listen`，不用重新設定 |

**不生效的設定要看得出來**：

- 開關打開時，各用途的設定不刪除。
- Telegram `/setconfig` 和網頁的模型設定頁：
  - 「全域（文字）」「全域（語音）」旁邊各顯示開關，可以直接切換。
  - 開關打開時，那一邊各用途的設定旁標示「（全局生效開著，目前不生效）」，用途的畫面也說明是哪個全域蓋過了它。
  - 開關打開時去改用途的設定，存檔後提醒「這組設定已儲存，但目前不會生效」，旁邊附「🔓 關閉全局生效」的按鈕。
- 來源標示多兩種「全域（文字，全局生效）」「全域（語音，全局生效）」；全域沒設到、退到 `.env` 時是「.env（全局生效）」。網頁和 TG 依同一套規則標示，措辭不同（網頁是「自己設定」「沿用全域（語音）」「.env 預設」）。

### D4 錄音評分的設定由 handler 宣告

1. **找 handler**：`notesInfo` 回傳的 `modelName` → `handler_registry.handler_for_note_type`。筆記類型只用來找到 handler，`voice.py` 裡不寫任何依筆記類型或欄位名稱的判斷。
2. **handler 宣告**：每個可錄音的欄位對應到：
   - 目標語言（三語卡由欄位後綴決定，對話卡讀 `Target_Language` 欄位）
   - 評分提示詞
   - 參考答案從哪個欄位讀
3. **用途鍵**：`recording_eval.<handler_name>`，handler 名稱改成查到的，不再猜；每個能錄音的 handler 都宣告自己的用途鍵（D1）。
4. **既有的卡**：三語卡、對話卡改成同樣的宣告方式，評分結果不變；之後的長文卡宣告自己的提示詞與參考答案欄位。
5. 不能評分時不要退回猜測，而且三種原因分開說明（都在上傳音檔之前，並清掉錄音狀態）：
   - 筆記類型找不到 handler（筆記類型改了名、handler 沒註冊）：記 ERROR，回覆「後端不認得這種卡片（筆記類型 …）」。
   - handler 沒有宣告 `RECORDING_EVAL`：記 ERROR，回覆「這種卡片不支援錄音評分」。
   - 欄位不能錄音（`target_for` 回 `None`）：記 WARNING，回覆「欄位 … 不支援錄音評分」。
6. handler 宣告的參考答案欄位不在筆記上，是宣告錯誤：記 ERROR 並拒絕評分，不當成「沒有參考答案」照樣評。`target_for` 拋例外時也回報並清掉狀態，不讓狀態訊息停在「讀取卡片中…」。

### D5 每筆錄音記下評分時間、評分方式與模型

錄音紀錄加上這些欄位（舊的 `date` 保留，舊紀錄沒有新欄位時照舊顯示）：

```json
{
  "date": "2026-11-26",
  "evaluated_at": "2026-11-26T06:03:12Z",
  "strategy": "stt_llm",
  "models": {"stt": "(whisper)Systran/faster-whisper-large-v3", "llm": "(audio)gemini-3.5-flash"},
  "settings": {"audio_eval": "recording_eval.speaking_trilingual", "stt": "*voice", "llm": ".env"},
  "audio": "…", "transcript": "…", "comment": "…", "score": 85
}
```

| 欄位 | 內容 |
|---|---|
| `evaluated_at` | 評分完成的時間，UTC、ISO 8601；卡面用裝置的時區顯示 |
| `strategy` | `listen`／`stt_llm`／`stt_diff` |
| `models` | 這次實際用到的能力與模型，寫成 `(端點)模型`；取自 `EvaluatorPlan` 的解析結果 |
| `settings` | 策略本身（`audio_eval`）與策略用到的各能力各自生效的設定層：用途鍵、`*voice`，或 `.env`（開關關著時各能力可能來自不同層，所以逐項記）；語音全域的開關開著時另加「（全局生效）」，例如 `*voice（全局生效）`，事後才分得出是開關蓋過了卡片自己的設定 |

- 寫入點：`voice.py` 的 `evaluation_metadata`。
- `RecordingItem`（`app/schemas/speaking.py`）加上這些選填欄位，`voice.py` 一律經它產生新錄音（`model_validate` → `model_dump`），卡片上的錄音格式只由這個模型定義；`strategy` 限定為三種策略。格式維持扁平（2026-11-28 使用者決定，不改成巢狀的 `evaluation`）：四個評分欄位在新錄音上一起寫入，舊錄音全都沒有。
- 卡面：三語卡、對話卡（以及之後的長文卡）的錄音列表，展開某筆錄音後的第一行顯示評分資訊，例如「⚙️ listen · gemini-3.5-flash · 11/26 14:03」（實作時放在詳情裡，不塞進收合時的那一行，避免手機上擠成兩行）。兩份卡面的 `recEvalMeta` 是同一份，測試會比對。
- 評分資訊在取得評分器之後、上傳音檔之前就組好（`evaluation_metadata`），存檔時只補上時間（`utc_timestamp`）：組資訊出錯時還沒上傳音檔，也不會在評分完成後被說成「寫回 Anki 失敗」。它和建評分器分開接：建評分器失敗多半是設定問題，保留錄音狀態讓使用者改好設定後重傳；組資訊失敗是程式錯誤，清掉狀態並說明。
- Telegram 的評分回覆照舊顯示模式。

### D6 拿掉人脈卡的 TG 語音輸入

使用者 2026-11-26 決定：語音都是為了練語言用的（評發音、口說），不當成輸入方式。網頁上的人脈卡本來就只有文字輸入，TG 也改成一樣。

現況：TG 的「用一段話記錄」可以傳語音訊息，Bot 先轉成文字再交給 LLM 整理。

要拿掉的地方：

| 位置 | 改動 |
|---|---|
| `app/bot/handlers/person_profile.py` | 刪掉收語音的 handler（`@router.message(PersonStates.ingest_text, F.voice)`，下載語音、轉文字、轉交整理）；按鈕「用一段話記錄（文字或語音）」改成「用一段話記錄」；提示文字「打一段話或傳語音」「請輸入一段話，或傳語音。」改成只說打字；模組開頭的說明拿掉語音 |
| `app/services/person_profile_ingest.py` | 刪掉 `transcribe`；用途鍵 `person_profile` 的能力只留 `llm`（拿掉 `stt`），名稱改成「人脈卡：TG 用一段話記錄」；模組說明拿掉「語音轉成的文字」 |
| `tests/test_person_profile_ingest.py` 等 | 刪掉語音輸入的測試，補上「在記錄狀態傳語音時不處理」（或回覆請改用打字）的測試 |

- 在「用一段話記錄」的狀態下傳了語音，回覆「請用打字輸入」，不要沒有反應。
- 拿掉之後，`stt` 只剩錄音評分在用，語音全域就只管錄音評分。

## 4. 影響範圍

| 檔案 | 改動 |
|---|---|
| `app/infrastructure/llm/routing.py` | 三層解析（拿掉 `parent_key` 往上找）；能力依文字／語音分到兩個全域；兩個全域的開關；用途宣告屬於語音；來源標示 |
| `app/services/capability_route_service.py`、`app/api/capability_routes.py` | 語音全域的保留鍵、兩個開關的讀寫；評分方式限縮改成每個用途自己宣告 |
| `app/bot/handlers/models_menu.py`、網頁的模型設定頁 | 「全域（文字）」「全域（語音）」兩項與各自的開關、不生效的標示、存檔時的提醒；拿掉「流程」來源與子項縮排 |
| `app/services/recording_eval.py` | 拿掉流程鍵 `recording_eval` 的設定角色；各 handler 的錄音評分用途鍵（含對話卡）宣告為語音用途 |
| `app/services/person_profile_*.py`、`app/bot/handlers/person_profile.py` | 用途鍵照舊；`fill`、`topics` 不再沿用 `person_profile` 的設定；拿掉 TG 語音輸入（D6） |
| `app/bot/handlers/voice.py` | 依筆記類型查 handler、讀它的錄音評分宣告；錄音紀錄加上 D5 的欄位 |
| `app/services/task_handlers/`（`base.py`、三語卡、對話卡） | 錄音評分的宣告 |
| `app/schemas/speaking.py` | `RecordingItem` 加欄位 |
| `app/anki_models/Speaking_Coach_Dark_back.html`、`Speaking_Trilingual_Dark_back.html` | 錄音列表顯示評分方式、模型與時間 |
| `app/infrastructure/audio_evaluator/factory.py` | 來源標示；評分器依新的解析結果建立 |
| 測試 | 三層解析（文字、語音）、能力分到兩個全域、開關開關時的解析與保存、handler 查找與宣告、`RecordingItem` 保留新欄位、選單顯示 |

卡面模板只改 HTML，上線時要更新 Anki 裡的模板（要不要完整同步見 §7 Q4）。

## 5. 順序

這份要在三語長文卡做錄音評分（長文卡計畫書的 P3）之前完成：長文卡的錄音評分直接用 D4 的宣告方式，不用再碰 `voice.py` 的猜測。

原本建議分兩個 PR（D1–D3＋D6、D4＋D5）。實作時兩部分都動到 `voice.py` 與 `recording_eval.py`（拿掉流程鍵的同時，
評分改由 handler 宣告用途鍵），分開反而要寫一份過渡用的猜測邏輯，所以合在同一個分支、同一個 PR。

## 6. 驗收

- [x] 所有用途查設定都是三層；人脈卡「網頁 AI 整理」「分析與想話題」沒有自己的設定時用文字全域。
- [x] 文字全域改了不影響錄音評分；語音全域改了不影響文字用途。
- [x] 人脈卡在 TG「用一段話記錄」時傳語音，會回覆請改用打字；按鈕與提示文字不再提到語音。
- [ ] 文字、語音全域的開關照 D3 的例子生效；開關打開時，用途自己的設定還在資料表裡；Telegram 和網頁看得出哪些設定不生效，可以切換開關。
- [x] 三語卡、對話卡的錄音評分結果和改之前相同（評分提示詞、目標語言、參考答案）。
- [x] 沒有宣告錄音評分的卡片，錄音時回覆明確的錯誤。
- [ ] 新的錄音紀錄有 `evaluated_at`、`strategy`、`models`、`settings`，卡面顯示出來；舊紀錄照常顯示。
- [ ] 後端、前端測試與 lint 通過。

## 7. 待決事項

| # | 事項 | 狀態／建議 |
|---|---|---|
| Q1 | 文字全域要不要也有「全局生效」開關 | ✅ 2026-11-26 使用者決定：要（D3） |
| Q2 | 語音全域的開關要不要也涵蓋人脈卡 TG 語音訊息的語音轉文字 | ✅ 2026-11-26 使用者決定：要包含所有的語音；而且人脈卡的 TG 語音輸入整個拿掉（D6），語音只用來練語言 |
| Q3 | 舊的錄音紀錄要不要補上評分方式 | ✅ 2026-11-26 使用者決定：不補 |
| Q4 | 只改卡面模板的 HTML，Anki 需不需要完整同步 | ✅ 2026-11-26 照建議定案：照人脈卡加欄位的順序做（所有裝置先同步 → 更新模板 → 同步，要求完整同步時從桌面版上傳），並和長文卡的筆記類型匯入排在同一次，最多只做一次完整同步 |
| — | 「全局」和用途之間怎麼決定生效 | ✅ 2026-11-26 用使用者提出的「全局生效」開關（D3），不比修改時間 |
| — | 開關關掉時，沒有自己設定的用途用什麼 | ✅ 2026-11-26 使用者決定：資料庫有就用（語音全域存著的設定），資料庫沒有才用 `.env` |
| — | 文字、語音是否都統一三層 | ✅ 2026-11-26 使用者決定：都統一三層（D1） |

## 8. 實作紀錄

2026-11-26 起在 `feat/routing-three-layers` 實作，2026-11-28 經多面向審查後修正。最終的命名：

| 項目 | 名稱 |
|---|---|
| 語音全域的保留鍵 | `*voice`（文字全域仍是 `*`）；`routing.GLOBAL_ROUTE_KEYS`、`GLOBAL_TITLES` |
| 宣告語音用途 | `route(..., voice=True)`；宣告了 `audio_eval`／`audio_llm`／`stt` 卻沒標 `voice=True` 時建構就失敗 |
| 開關 | `cfg_capability_routes` 裡 capability 為 `routing.SWITCH_CAPABILITY`（`global_switch`）的一列，`provider` 為 `routing.SWITCH_ON`（`on`）；寫入只經 `capability_route_service.set_global_switch`，API 是 `PUT /api/v1/capability-routes/{全域鍵}/switch` |
| 解析結果 | `ResolvedRoute.global_key`、`ResolvedRoute.forced`；`routing.source_label` 產生 TG 與錯誤訊息的來源標示 |
| 錄音評分的宣告 | `task_handlers/shared/recording_spec.py` 的 `RecordingEvaluation`（用途鍵＋`target_for` 回調）與 `RecordingTarget`（模板、參考答案欄位、目標語言、`add_recording` 的額外參數）；handler 的類別屬性 `RECORDING_EVAL` |
| 用途鍵 | `recording_eval.speaking_coach`、`recording_eval.speaking_trilingual`（`recording_eval.py`）；流程鍵 `recording_eval` 拿掉 |
| 錄音紀錄 | `voice.evaluation_metadata`、`voice.utc_timestamp`，經 `RecordingItem` 寫入 |
| 人脈卡 | `person_profile.reject_ingest_voice`；`person_profile_ingest.transcribe` 拿掉 |

驗收還沒做到的：

- [ ] 卡面模板推到 Anki（照 Q4 的順序，和長文卡的筆記類型匯入排在同一次），新的錄音紀錄在卡面上顯示評分資訊。
- [ ] 開關在 Telegram 與網頁上實際操作過（目前有測試，正式機尚未實際切換）。
- [ ] lint 通過。
