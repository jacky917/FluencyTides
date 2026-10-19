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
| T1 | 高 | `/sync` 在 Anki 回 0 張卡時拒絕清理 | 現在 Anki 連線異常、回傳 0 張時，`/sync` 會把資料庫裡所有卡片關聯（知識圖譜）全部刪除。應在 0 張時拒絕清理並提示（審查書 §6） |
| T2 | 高 | 外語糾錯重複檢查的查詢跳脫 | `expression_fsm.py` 組 Anki 查詢時沒跳脫原文，含 `"` 時查詢語法壞掉，重複檢查失效（審查書 §6） |
| T3 | 中 | 以檔案形式傳送的音檔、圖片 | 只收 `F.voice`、`F.photo`，用「檔案」傳的音檔與圖片被靜默忽略；至少回覆「請改用語音訊息／照片傳送」（審查書 §6） |
| T4 | 低 | 回覆文字的精簡改寫 | 審查書 §4 的逐則改寫（指令與 Deep Link、語音與上傳、`/newcard` 三條建卡流程）；§4.4 的 `/setconfig`、`/models` 已隨能力路由改寫，不必再做 |
| T5 | 低 | 用語統一 | 審查書 §5：同一件事全 bot 用同一個說法（用語規則見審查書 §2，共用文字在 `app/bot/utils/texts.py`） |

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
