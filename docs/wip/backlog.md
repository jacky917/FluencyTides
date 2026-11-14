# Backlog — 已歸檔計畫書留下的未完成項目

| 欄位 | 內容 |
|---|---|
| **建立日期** | 2026-10-19 |
| **用途** | 計畫書做完歸檔時，把還沒做的後續項目集中到這裡，避免散落在 `docs/archive/` 裡被遺忘。進行中的計畫書（`docs/wip/` 下其他檔案）的待辦仍寫在各自的文件裡 |
| **維護方式** | 開始做某一項時，另開計畫書或直接在 PR 說明引用本檔的編號；做完後從本檔刪除該項（或標 ✅ 並註明 PR） |

優先級：**高**＝會壞資料或讓功能失效；**中**＝影響品質或常用流程；**低**＝整理與改善。

---

## 1. TG Bot（來源：`docs/archive/tg_bot_copy_review_2026-10-18.md`）

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| T3 | 中 | 以檔案形式傳送的音檔、圖片 | 只收 `F.voice`、`F.photo`，用「檔案」傳的音檔與圖片被靜默忽略；至少回覆「請改用語音訊息／照片傳送」（審查書 §6） |
| T4 | 低 | 回覆文字的精簡改寫 | 審查書 §4 的逐則改寫（指令與 Deep Link、語音與上傳、`/newcard` 三條建卡流程）；§4.4 的 `/setconfig`、`/models` 已隨能力路由改寫，不必再做 |
| T5 | 低 | 用語統一 | 審查書 §5：同一件事全 bot 用同一個說法（用語規則見審查書 §2，共用文字在 `app/bot/utils/texts.py`） |

（原 T1 `/sync` 防誤刪、T2 外語糾錯查詢跳脫已隨 PR #27 合併，2026-11-03 刪除；部署後的確認項見 §5 R1、R2。）

## 2. LLM 客戶端與生成腳本（來源：`docs/archive/llm_provider_hierarchy_FEAT_2026-09-06.md` §7、§8）

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| L1 | 中 | 生成腳本遇到設定錯誤時中止 | 現在所有 `LLMServiceError` 都回 502，腳本對 ≥500 逐句記失敗後跳過：金鑰無效、模型不存在時整批句子累加 failure_count 直到永久跳過。改成設定錯誤回 4xx 或專屬 `error_code`，腳本改讀 `ErrorResponse.error_code`（也解決以 `"429" in 回應文字` 判斷額度的誤判），之後移除訊息字串保證 |
| L2 | 中 | 未註冊的 `LLM_PROVIDER` 啟動即報錯 | 中轉站目前靠「未註冊值落到 OpenAI 相容 `LLMClient`」運作；改為明確登記一個 OpenAI 相容群組，未知值啟動就報錯 |
| L3 | 低 | gemini-native、antigravity 各跑一章 | 程式已合併，只差使用者實測拒答率與品質（antigravity 另與 claude-code 做 20 張人工評閱）；有要用這兩個 provider 時再做 |
| L4 | 低 | mypy 加進 CI | `requirements.txt` 已有 mypy；不進 CI 型別錯誤會再累積 |
| L5 | 低 | `LLMClient` 改用 `_retry_transient()` | 指數退避取代固定 2 秒；屬行為變更，需獨立驗證。語音的 `GeminiNativeAudioEvaluator` 同樣改用（F043 的另一半） |
| L6 | 低 | 移除過渡用的程式 | `_formatted_model_name` alias 與 `LLMClient` 上的兩個轉呼工具方法 |
| L7 | 低 | `GeminiNativeLLMClient` 的 `thinking_config` | `LLM_GEMINI_NATIVE_THINKING_BUDGET`；與 effort 是不同概念，先不混入 `SUPPORTS_EFFORT` |
| L8 | 低 | `AnthropicLLMClient` 本體 | 目前是佔位 stub |
| L9 | 低 | CLI 診斷 hook 化與 agy 認證 | 診斷區塊鍵仍叫 `claude_code`；antigravity 的帳號與認證探測、容器內 headless 認證（`JETSKI_OAUTH_TOKEN`、`AGY_ADC_AUTH`）、`LLM_ANTIGRAVITY_ISOLATE_HOME` 在 macOS / Linux 的行為都未驗證 |

（§8 原有的「`jp_verb_reading_service` 覆寫錯誤一律轉 422」與「`RuntimeConfigService` 寫入側」已不適用：讀音判斷的覆寫已於模型路由 PR #24 移除，執行期設定改由能力路由管理。）

## 3. 語料檢索與驗證（來源：`docs/archive/es_sudachi_normalizedform_FIX_2026-09-05.md` §6）

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| E1 | 中 | VerbPair 只抓 ES 前 100 筆 | 自他動詞每個關鍵字只抓前 100 筆、不分頁，高頻動詞永遠只看到遊戲前面的章節；比照 CoreVerb 改成全量游標分頁 |
| E2 | 中 | 同表層異讀的核心動詞 | `支える`（ささえる／つかえる）、`滑る`（すべる／ぬめる）：CoreVerb 不傳 `expected_reading`，驗證器分不開。與讀音判斷計畫 §7.3「多讀表層候選來源擴成 UniDic」是同一件事 |
| E3 | 低 | 旁白過濾的粒度 | 37 個動作描寫動詞的語料全在旁白，`--skip-narrator` 一開就全滅；改成個別放行或不用全域旗標 |
| E4 | 低 | 兩條管線的關鍵字設定檔統一 | `extra_search_keywords.json`（以 note id 為鍵）與 `verb_search_config.json`（以表記為鍵）是兩套實作；自他動詞這邊也沒做假名關鍵字分析 |
| E5 | 低 | 關鍵字的單一 token 啟動檢查 | 載入時以 `_analyze` 驗證每個關鍵字在 Sudachi 下是單一 token，多 token 記 WARNING 並忽略 |
| E6 | 低 | 驗證器的三個既有弱點 | `〜がって＋補助動詞`、`〜ないといけない／〜ちゃいけない`、UniDic 誤切（そかそか）；細節見原文件 §6 第 5 點 |
| E7 | 低 | `どく` 的 2 張損失 | 等 `judge_verb_readings.py` 跑過 退く 三讀母卡後，整組重審是否補回 |

## 4. Handlers 與網頁卡片編輯（來源：`docs/archive/handlers_api_redesign_FEAT_2026-10-20.md` §13、`docs/archive/json_field_editor_FEAT_2026-10-20.md` §6、§8）

2026-11-03 歸檔時逐項確認過，在 main 上都還存在。

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| H1 | 中 | `import_cards_with_llm.py` 必定失敗 | `backend/scripts/local_anki/import_cards_with_llm.py` 呼叫已不存在的 `CardService.generate_card`；修好或刪掉 |
| H2 | 中 | 沒有 handler 的筆記類型 | Notion_SRS（712 張）、Shadowing_Breakdown（206）、AI_QA（42）、Conversation_Coach（31）、Voice_Shadowing（17）、Contrast_Coach（1）目前只有 `scripts/local_anki/import_cards_from_json.py` 能建；要在網頁開放就先為它們建 handler（基底的標準 CRUD 與圖譜可直接用）。數量為 2026-10-20 的統計 |
| H3 | 中 | JSON 欄位表單編輯開放給更多筆記類型 | 目前只有人脈卡開放 `update_json`。依序：單字卡 `_v2` 的 `Word_Family_JSON`、`Synonyms_JSON`、`Collocations_JSON`（原設計的試點，資料平坦、沒有媒體；開放前先對全部 `_v2` 跑一次「讀出→驗證」）；對話卡 `Recordings`（含媒體，TG 會背景追加）；`References`（巢狀 `audios`）；三語卡 `_ZH/_JA/_EN` 各欄。另待決：改同義詞、搭配詞後要不要同步知識圖譜的關聯 |
| H4 | 低 | `verb-pair`、`core-verb`、`config`、`jp` 路由沒有 `X-API-Key` 驗證 | 若要補，本機腳本要同時改成送 `X-API-Key` |
| H5 | 低 | 舊版 `TOEIC_Coach_Dark` | Anki 裡 0 張筆記，已從 handler 清單拿掉；定義檔 `backend/app/anki_models/TOEIC_Coach_Dark.json`（repo 內沒有它的 HTML、CSS 模板）與提示詞模板 `backend/app/templates/prompts/anki/TOEIC_Coach_Dark.j2` 要不要刪由使用者決定（Anki 裡的要自己操作）。H7 的通用 LLM 建卡殘骸以它當註解範例，一起處理 |
| H6 | 低 | 沒有呼叫方的關聯 API | `GET /relations/graph`、前端 `getKnowledgeGraph`、`RelationService.get_graph_data` 與 handler 的標準圖譜重複；`DELETE /relations/by-note/{note_id}` 沒有呼叫方，`delete_relations_by_note_id` 與 `delete_relations_for_note` 是兩個一樣的方法 |
| H7 | 低 | 通用 LLM 建卡的殘骸 | `AnkiModelManager` 的 `get_model_schema`、`create_note_from_llm_response`、`list_available_models`、`get_model_detail`、`can_add_note`、`sync_to_ankiweb`；`schemas/card.py` 的 `CardGenerateRequest`。沒有呼叫方；網頁不做自動化，本機腳本可能重用，先保留 |
| H8 | 低 | `/storage/*`（MinIO） | 上傳、列出、預簽、刪除，repo 內沒有呼叫方；網頁的媒體上傳不走這裡（見 `docs/wip/media_upload_FEAT_2026-10-20.md`） |

## 5. 已合併計畫的未實測驗收與後續（2026-11-03 歸檔時整理）

來源：`docs/archive/` 下的 `person_profile_card_FEAT_2026-10-23.md`、`trilingual_familiarity_FEAT_2026-10-30.md`、`trilingual_web_create_FEAT_2026-10-30.md`、`trilingual_search_tags_FEAT_2026-10-30.md`、`handler_field_search_FEAT_2026-10-25.md`、`relation_sync_and_search_escape_FIX_2026-10-19.md`。程式都已合併（PR #27、#32），以下是沒有實機確認過的驗收，以及各計畫明確留到之後的事。實機驗收做完、沒問題就刪掉該項；有問題另開 FIX。

### 5.1 實機驗收

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| V1 | 中 | 人脈卡：Anki 桌面版與網頁的卡面 | 人脈卡計畫書 §7 前 11 項：兩種卡片的產生條件、「認人」正面不露姓名、無照片時的線索與首字頭像、背面頭部、「見面前必看」、空區段不顯示、`<details>` 收合、敏感話題模糊、JSON 格式錯誤提示、「查看介紹人」、網頁點收合不翻面。用計畫書 §8 的範例資料檢查 |
| V2 | 中 | 人脈卡：網頁 CRUD | 建立（表單不含 JSON 與媒體欄位）、修改、刪除；建立後 `Card_ID` 有值、JSON 欄位為 `[]` |
| V3 | 中 | 人脈卡：TG 流程 | `/p 名字`（同名列按鈕）、`/newcard`、記一筆互動與待追蹤、改欄位（取代、接在後面、刪除一筆）、傳照片、卡面「記一筆互動」深連結、P5 一段話整理、P6 AI 分析（提示裡沒有本人與家人的名字） |
| V4 | 低 | 人脈卡：手機實機 | AnkiDroid 或 AnkiMobile：版面正常、`<details>` 可用（計畫書 R5） |
| V5 | 低 | 三語卡熟識度：卡面與錄音評分 | 刻度條、顏色、依熟識度排序；排序後加音檔、切換狀態動到的仍是同一句；錄音評分（stt_llm、listen）的提示詞帶出熟識度，stt_diff 註明比對對象。匯入檢查已有單元測試 |
| V6 | 低 | 卡片瀏覽頁建卡：連真實後端與 Anki | 來源 `docs/archive/card_list_create_FEAT_2026-10-30.md` §7（2026-11-12 歸檔時加入）。當時只用假資料在瀏覽器驗過，以下沒有實機確認：建卡成功後清單重新整理、出現新卡；同步 AnkiWeb；預覽流程（三語卡）在卡片瀏覽頁的完整建卡；收合側邊欄觸發的版面切換後表單內容還在 |
| R1 | 低 | 外語糾錯的查詢跳脫（原 T2） | 部署後在 TG 走一次外語糾錯，原文含 `_` 或 `"`，確認重複檢查正確 |
| R2 | 低 | TG 實機錄音評分 | `relation_sync_and_search_escape_FIX` §5.2 的三步確認：對話卡、三語卡各錄一則，後端 log 出現「錄音評分器已建立」 |

### 5.2 後續功能

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| F1 | 低 | 欄位搜尋 S2 | 短值欄位（關係、公司）列出出現過的值，點一下就選（`handler_field_search` §4） |
| F2 | 低 | 欄位搜尋 S3 | 後端讀資料再篩選：指定互動的地點、很久沒聯絡等 Anki 語法做不到的條件 |
| F3 | 低 | 欄位搜尋開放給更多卡片 | 目前開放人脈卡、三語卡；其他 handler 逐一確認欄位按鈕後開放 |
| F4 | 低 | 網頁修改既有卡片的標籤；標籤改名、合併；TG 建卡加標籤 | `trilingual_search_tags` §8 的範圍外項目 |
| F5 | 低 | 三語卡網頁建卡的延伸 | TG 入口；預覽中重新生成或修改單句；建卡前檢查重複場景；把 `note` 交給錄音評分的提示詞（`trilingual_web_create` §8） |
| F6 | 低 | 人脈卡建卡表單加照片 | 修改既有卡片的照片已可在筆記欄位彈窗上傳（`docs/wip/media_upload_FEAT_2026-10-20.md` §6）；建卡時就放照片要在 `PersonCreateParams` 加媒體參數（同文件 §6.4） |

### 5.3 待決策

| # | 項目 | 內容 |
|---|---|---|
| D1 | `docs/bk/` 修復紀錄全面核對 | 抽查發現多項「已修復」實際不存在；2026-11-03 再查：F004（`API_SECRET_KEY` 為空時直接放行，`core/auth.py`）、F010（`frontend/vite.config.js`、`.d.ts` 仍在版本控制）、F019（CORS 仍寫死 `localhost:5173`，`main.py`）都還是這樣。建議逐條核對 `docs/bk/06` 的 141 條（`relation_sync_and_search_escape_FIX` §6.1、§9 第 3 點） |
| D2 | 正式機要不要設定 `API_SECRET_KEY` | 後端 8000 埠綁在 NAS 的 `0.0.0.0`，區網內可以直接呼叫刪除端點；目前是刻意交給外層（Cloudflare Access）控管的部署決定（同上 §6.2、§9 第 4 點） |
| D3 | `/relations/sync` 強制清理的上限 | 帶 `force=true` 重送時服務層會重算、不再比對比例；可改為重送時帶 `expected=N`，超過就再拒絕。風險低（同上 §9 第 5 點） |

## 6. 網頁補齊 TG 能力（來源：`docs/archive/web_ui_parity_FEAT_2026-10-18.md` §8）

（原 W1 對話卡、W2 外語糾錯的網頁建卡已併入 `docs/wip/server_drafts_FEAT_2026-11-04.md` D10 並實作，2026-11-07 刪除。）

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| W3 | 低 | 網頁開放具名動作前，動作參數錯誤改回 4xx | handler 的 `execute_update` 參數錯誤拋 `ValueError`，會變成 500 並發 TG 當機警報；目前動作只從 TG 呼叫。網頁要開放動作（例如 media_upload 的 `add_audio`）時一併改成 `FluencyTidesError` 子類 |
| W4 | 低 | 能力路由的 503 拆成兩個錯誤代碼 | 寫入前讀不到 DB（確定沒寫入）與寫入時異常（結果未知）共用 `ROUTE_STORE_UNAVAILABLE`，網頁只能用不斷言的措辭 |


## 7. 日期與時區（來源：`docs/wip/person_field_history_FEAT_2026-11-07.md` §2.3、D9）

| # | 優先 | 項目 | 內容 |
|---|---|---|---|
| Z1 | 中 | 口說卡的日期改用 `APP_TIMEZONE` | `bot/handlers/fsm/speaking_fsm.py`（建卡時的參考範本 `date`）與 `bot/handlers/voice.py`（錄音紀錄的 `date`）用 `datetime.now(timezone.utc)` 記日期：東京 0–9 點記成前一天。三語口說卡的 `services/task_handlers/speaking_trilingual_handler.py`（參考範本的 `date`）用 `date.today()`，依容器的 `TZ`，本機不經 docker 時跟著電腦時區。都改用 `app/core/clock.py` 的 `today()`（人脈卡已改用）。檔名、`Card_ID` 裡的 UTC 時間戳不影響顯示，可以一併改成 `clock.now()` 或維持 |
