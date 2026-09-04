# FEAT — 新增 claude-code LLM Provider:以本機 `claude -p`(headless CLI)取代計費 API 完成結構化生成

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-27 |
| **性質** | 新增機能設計 + 實作工作項 |
| **狀態** | ✅ 完成(2026-08-27):P0~P4 全數完成,128 項測試全綠,真實管線產出 86 張卡並全量品質驗證,定案 `opus` + `medium` |
| **範圍** | `app/infrastructure/llm/`(新增 provider + 工廠)、`app/core/config.py`(新增設定)、8 處 `LLMClient()` 實例化點改為工廠呼叫、`requirements.txt`(+jsonschema) |
| **不動** | `scripts/fastapi_client/**` 生成腳本(僅 D7 標籤行一行例外)、API request/response schema、各 handler 的 prompt 渲染與建卡邏輯、現有 `LLMClient` 類本體、Anki/ES/MySQL 基礎設施 |
| **PR / 進度** | [#9](https://github.com/jacky917/FluencyTides/pull/9)(2026-08-27 開啟,commit `911bdc7`) |
| **關聯文件** | `docs/archive/claude_cli_env_setup_FEAT_2026-08-27.md`(環境配置 + 全參數值域實測;本文件所有旗標與失敗形態均以該文件 §3/§5 的實測為依據)、`app/infrastructure/llm/client.py`、`app/services/task_handlers/jp_verb_pair_handler.py` |

---

## 1. 問題與動機

批量生成子卡片管線(JP_VerbPair / JP_CoreVerb)的唯一外部 LLM 依賴是
`LLMClient.generate_structured_data()`(`app/infrastructure/llm/client.py:115`),
走 OpenAI 相容計費 API。每張卡的 prompt 約 18KB(世界觀 + 120 行規則 + 上下文),
量產數百張卡時 API 成本可觀,且第三方中轉站另有速率限制(429)與安全審查攔截
(`PROHIBITED_CONTENT`)兩類額外失敗模式。

使用者已有 Claude Max 訂閱,本機 Claude CLI(2.1.247)已完成環境標準化與全參數
實測(關聯文件)。`claude -p` 是官方文件明載、專為 scripting 設計的 headless
功能,可在本機以訂閱額度完成同等的「prompt 進、JSON 出」生成,且自帶
`--json-schema` 結構化輸出強制(harness 層驗證 + 內建最多 5 次重試)。

**合規邊界(設計約束)**:此 provider 僅服務「使用者手動發起、有始有終的自有
批次任務」。禁止形態:常駐 daemon、對外網路 endpoint、供第三方使用、接入多用戶
生產路徑。

接縫證據:

- `app/infrastructure/llm/client.py:102` — `LLM_MODEL_NAME` 僅作為 API `model` 參數與標籤,無行為分流
- `LLMClient()` 實例化點共 8 處(見 §4),全部改為工廠呼叫後,`.env` 一行切換全域生效
- 下游雙防線已存在:handler 的 `model_validate` + `position_cloze`(422 跳句),壞輸出結構上不可能入庫

## 2. 目標與非目標

**目標**

- G1 `.env` 設 `LLM_PROVIDER=claude-code` 後,`generate_structured_data()` 改經本機 `claude -p` 完成,回傳與現有 `LLMGenerateResult` 完全相容,上下游零感知
- G2 模型與力度由 `.env` 顯式控制:`LLM_MODEL_NAME` 直通 `--model`,`LLM_CLAUDE_CODE_EFFORT` 直通 `--effort`,兩者均明寫、不依賴 CLI 隱式預設
- G3 Python 端 JSON 校驗:信封分流 → `json.loads` → `jsonschema` 深度複核,失敗觸發帶錯誤回饋的修復重試
- G4 失敗語義與現有管線對齊:拋 `LLMServiceError`,訊息帶生成腳本錯誤分級表可識別的字串
- G5 每次呼叫的 prompt 與 answer 落盤存檔(審計目錄)

**非目標**

- 常駐服務或任何網路 endpoint 形態 —— 合規紅線,結構上不允許做成
- 併發呼叫優化 —— 生成腳本序列執行(併發已驗證可行,留作未來選項)
- `stream-json` 串流輸出 —— 單發 `json` 信封已滿足需求

## 3. 設計決策

### D1 接縫位置:infrastructure 層新類 + 工廠函數

新增 `ClaudeCodeLLMClient`(與 `LLMClient` 同介面),以 `create_llm_client()`
工廠依 `settings.LLM_PROVIDER == "claude-code"` 分流。放棄的替代方案:腳本層
切換(要動腳本 + API schema + handler 三處)、`LLM_MODEL_NAME` 當開關(佔用
模型紀錄欄位)、本地 OpenAI 相容 shim server(正是「包裝成 API」的形狀,主動避開)。

介面契約:同簽名 `generate_structured_data(system_prompt, user_prompt, response_schema) -> LLMGenerateResult`、
失敗拋 `LLMServiceError`。**已實證(grep)**:`generate_structured_data` 是
`LLMClient` 唯一的公開方法,全部使用點(JP_VerbPair / JP_CoreVerb / expression /
example 四個 handler + 三支 scripts + main.py 單例)只呼叫它 —— 單方法契約即
覆蓋所有卡片類型,無隱藏介面缺口。

### D2 CLI 路徑解析:設定優先,自動探測兜底

1. `settings.LLM_CLAUDE_CODE_CLI_PATH` 有值 → 直接用
2. 無值 → 依序探測:`%USERPROFILE%\.local\bin\claude.exe`(原生安裝標準路徑)
   → `shutil.which("claude")` → glob `%APPDATA%\Claude\claude-code\*/claude.exe`
   取版本最大者(桌面版兜底)
3. 都找不到 → 拋 `LLMServiceError`,訊息附安裝教學

### D3 認證與環境衛生

前提:使用者已以 `claude auth login` 登入(憑證落盤 `~/.claude/.credentials.json`),
subprocess 零配置直接可用。provider 的責任:

1. **subprocess env 剔除清單**:
   - `CLAUDE_CODE_OAUTH_TOKEN` —— **必須**。優先級高於落盤憑證,殘留壞值會蓋掉
     有效登入(實測 401)
   - `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` —— 衛生性。實測登入態優先、
     假 key 不影響,剔除是防未來版本改變優先級
2. auth 失效偵測:`result` 含 `Not logged in` 或 `401 OAuth access token is invalid`
   → 拋 `LLMServiceError`,訊息附 `claude auth login` 教學
3. 可選前置檢查:`claude auth status`(已登入 exit 0)
4. cwd 指向專用空目錄(`settings.LLM_CLAUDE_CODE_WORKDIR`),與 `--safe-mode` 疊加
   構成雙保險

### D4 呼叫規格(每個參數的值域均經實測,詳見環境計劃 §3 值域表)

```
claude -p --safe-mode --tools "" --no-session-persistence \
  --model <LLM_MODEL_NAME> --effort <LLM_CLAUDE_CODE_EFFORT> \
  --output-format json \
  --system-prompt <system_prompt> --json-schema <展平 schema> \
  < user_prompt(stdin,UTF-8 bytes)
```

| 參數 | 值 / 來源 | 實測依據與注意 |
|---|---|---|
| `--model` | `LLM_MODEL_NAME` 直通。可用:別名 `opus`(預設,≈21s/卡)/ `sonnet`(≈36s)/ `haiku`(≈116s)/ `fable`,或完整模型名 | 錯名**硬失敗**可識別(stderr `[claude-code:unrecognized_model]` + `is_error:true`),可信任 CLI 報錯 |
| `--effort` | `LLM_CLAUDE_CODE_EFFORT` 直通。可用:`low`/`medium`/`high`/`xhigh`/`max`(三模型全接受)。預設 `high`(實測與 CLI 隱式預設行為一致)。降力度警示:sonnet+`low` 實測品質下降(挖空腦補原文不存在的助詞);頂級模型+`low` 未評估,調低前先以實戰 prompt 驗證品質 | 非法值**靜默回退**(僅 stderr Warning,`is_error:false`)→ **provider 必須 Python 端白名單驗證,拒絕啟動而非無聲跑錯力度** |
| `--safe-mode` | 固定 | 隔離 CLAUDE.md / skills / hooks / MCP(canary 對照實測),auth 正常 |
| `--tools ""` | 固定 | 全工具禁用,純文本生成 |
| `--no-session-persistence` | 固定 | 批量呼叫不留會話殘骸(對照實測) |
| `--output-format json` | 固定 | 單一 JSON 信封;成功時 `result` 為**純 JSON 字串、無 Markdown 圍欄** |
| `--system-prompt` | handler 的 system_prompt | 整替預設系統提示(canary 實測生效),並省下預設提示的 token |
| `--json-schema` | `response_schema` 經 `LLMClient._resolve_json_schema()`(classmethod,免實例化)**展平後**的 JSON | **必須展平 `$defs`/`$ref`**:未展平會使 CLI 內建 5 次結構化重試全數耗盡(haiku 實測 189s 白燒);非法 JSON 快速失敗不燒額度;enum/巢狀/陣列均被遵守 |

| user_prompt | stdin | UTF-8 bytes 寫入;避免命令列長度限制與轉義問題 |

**輸出串流歸屬(實作期實測補充)**:`[claude-code:unrecognized_model]` 與
`Error: --json-schema is not valid JSON` **均為 stderr 專屬**,stdout 只承載
JSON 信封。因此致命標記的偵測只掃 stderr —— 若連 stdout 一起掃,生成內容
剛好含這些字串時會被錯殺(已加測試防護)。

### D5 進程執行與校驗管線

- **必須用 `asyncio.to_thread(subprocess.run, ..., timeout=...)`,不可用
  `asyncio.create_subprocess_exec`** —— 實測:Windows 的
  `WindowsSelectorEventLoopPolicy` 下 `create_subprocess_exec` 直接拋
  `NotImplementedError`,而本專案 20+ 支腳本都設了該 policy(目前三個 script
  呼叫點雖未設,但同目錄兄弟腳本普遍設定,未來極易踩到)。`to_thread` +
  同步 `subprocess.run` 在任何 event loop 下都可用,且本管線為序列呼叫,
  無併發吞吐考量
- 超時交由 `subprocess.run(timeout=...)` 處理(自動 kill 進程)→ 計一次失敗。
  單次超時 ≥ 300s(CLI 內建 5 次重試最壞情況 ≈190s)
- stdout/stderr 一律以 bytes 取得再 `decode("utf-8", errors="replace")`,
  不依賴系統編碼(cp950 實測會產生假亂碼)

三道防線(CLI 原生強制 → Python 複核 → handler 業務驗證):

```
stdout(bytes)→ decode("utf-8") → 解析信封 → is_error / terminal_reason 分流(見 D6)
       → 取 result(防禦性剝圍欄)→ json.loads
       → jsonschema.validate(parsed, response_schema)      # Python 複核
       → 通過:回傳 LLMGenerateResult
       → 失敗:重試 prompt 附上驗證錯誤要求修正(外層 MAX_RETRIES=2,CLI 內部已重試 5 次)
```

引入 `jsonschema` 套件(新依賴)做複核,因 provider 拿到的是 JSON Schema dict
(非 Pydantic class)。handler 端既有的 `model_validate` 保留不動,作為第三道防線
(provider 保證「符合 schema」、handler 保證「符合業務模型」)。

### D6 失敗分流(形態均經實測,對齊生成腳本錯誤分級表)

| 實測形態 | provider 行為 | 腳本端既有路徑 |
|---|---|---|
| `terminal_reason: structured_output_retry_exhausted` + `errors` 陣列 | 計一次失敗,進外層重試;全敗拋 `LLMServiceError("LLM API 在所有重試後仍回傳空內容 ...")` | `record_failure` + 跳句 |
| `result` 含 `Not logged in` | 拋 `LLMServiceError`(附 `claude auth login` 教學),不重試 | 5xx → 記失敗跳句 |
| `401 OAuth access token is invalid` | 同上 | 同上 |
| stderr `[claude-code:unrecognized_model]` | 拋 `LLMServiceError`(配置錯誤,不重試) | 中止批次(配置問題應立即修) |
| 額度耗盡(形態未知,量產首遇時補記) | 映射為含 `Quota` 字串的 `LLMServiceError` | 暫停 60s 跳句 |
| subprocess 超時 | kill + 計失敗,進外層重試 | 全敗後同第一列 |

### D7 模型標籤:模型名 + effort

effort 影響生成品質(環境計劃 B1:sonnet+`low` 品質下降),事後稽核「哪批卡
是什麼力度生成的」必須靠標籤。格式**沿用 API 模式的既有慣例再附加 effort**:
`LLMClient` 實際回填的是 `_formatted_model_name = (provider)model`
(`client.py:277`),claude-code 模式對齊此慣例,定為
**`({LLM_PROVIDER}){LLM_MODEL_NAME}@{LLM_CLAUDE_CODE_EFFORT}`**,如 `(claude-code)opus@high`:

- **provider 端**:`LLMGenerateResult.model_name` 回填上述格式 → handler 既有
  邏輯自動使 Anki tag 變 `LLM::(claude-code)opus@high`(與 API 模式
  `LLM::(yinli)gemini-...` 同構),`position_cloze` 錯誤日誌同步帶 effort
- **腳本端(SQL 去重表)**:`generate_child_cards.py` 的標籤行是腳本自行從
  settings 拼裝(不走 API 回應),需追加一行:claude-code 模式下
  `llm_model_name += f"@{settings.LLM_CLAUDE_CODE_EFFORT}"` → 去重表與失敗紀錄的
  `llm_model` 欄位為 `(claude-code)opus@high`,**與 Anki tag 完全同串**。
  此為本計劃唯一的腳本改動(§4)
- 事後可用 `llm_model LIKE '%@low'` / `tag:LLM::*@low` 精確圈出低力度批次重生

### D8 審計落盤

每次呼叫寫 `{audit_dir}/{timestamp}_{短hash}/`:`prompt.md`、`answer.json`、
`meta.json`(model、effort、attempt、耗時、exit code、CLI 版本)。
`settings.LLM_CLAUDE_CODE_AUDIT_DIR` 留空則關閉。目錄 gitignore。

### D9 與 API 模式的行為一致性對照

目標是「切換 provider 後上下游零感知、效果一致」。逐項對照現有
`LLMClient.generate_structured_data` 的行為,一致者列驗證方式,無法完全一致者
明寫差異與緩解:

| 行為 | API 模式(現況) | claude-code 模式 | 一致性 |
|---|---|---|---|
| 介面簽名 / 回傳型別 / 例外 | `generate_structured_data → LLMGenerateResult`,失敗拋 `LLMServiceError` | 完全相同 | ✅ 等同 |
| schema 傳遞 | schema 文字塞 system prompt + `response_format` strict | `--json-schema`(harness 層強制 + 內建 5 次重試) | ✅ 等效且更強;不再需要把 schema 文字拼進 system prompt |
| schema 預處理 | `_resolve_json_schema` 展平(為 Gemini) | 同一函數展平(為 CLI,A5 實測必須) | ✅ 複用同一代碼 |
| `model_name` 標籤格式 | `(provider)model` | `(claude-code)model@effort`(D7,同構加後綴) | ✅ 同慣例 |
| 空回應/壞 JSON 重試 | MAX_RETRIES=3 盲重發 | CLI 內建 5 次 + 外層 2 次帶錯誤回饋 | ✅ 更強 |
| `temperature=0.0` | 顯式設定,追求格式穩定 | **CLI 無 temperature 旗標,不可控** | ⚠️ 已知差異。緩解:`--json-schema` 強制 + `--effort high` + 三道驗證;輸出穩定性由 P4 放量統計驗證 |
| `safety_settings` BLOCK_NONE | 對第三方中轉站傳遞,壓低 Gemini 安全審查誤攔 | **無對應機制**,Claude 模型自身內容政策生效 | ⚠️ 已知差異。galgame 台詞若遭拒答會表現為結構化輸出失敗 → 走既有 `record_failure` 跳句路徑(與現在 `PROHIBITED_CONTENT` 的處置相同);P4 統計拒答率 |
| 請求日誌 | `logger.info`(model、prompt 長度) | 同格式輸出 | ✅ 對齊 |

### D10 反向保證:API 模式(原實作)零影響審計

每項改動對 `LLM_PROVIDER != claude-code` 時的影響逐一過檢:

| 改動 | API 模式影響 | 依據 |
|---|---|---|
| 新增 `claude_code_client.py` / `factory.py` | 無,且**連 import 都不發生**:工廠對 `claude_code_client` 採**惰性 import**(只在 claude-code 分支內),`dependencies.py` 僅以 `TYPE_CHECKING` 引用 → API 模式不載入該模組、也不需要安裝 `jsonschema`,既有部署零影響。已加自動化測試以 AST 檢查模組層級 import,防日後被改回 | 實測:API 模式啟動後 `sys.modules` 不含 `claude_code_client` 與 `jsonschema` |
| 8 處 `LLMClient()` → `create_llm_client()` | 無:工廠在非 claude-code 時回傳 `LLMClient()`,物件與行為與現狀逐字節相同 | D1 |
| config 新增 5 欄位 | 無:全部有預設值,API 模式不讀取 | — |
| **effort 白名單驗證** | 無 —— **驗證位置定為 `ClaudeCodeLLMClient.__init__` 內,絕不做成全域 pydantic validator**;否則 API 模式會被一個與它無關的非法 `LLM_CLAUDE_CODE_EFFORT` 擋住啟動 | D4 |
| 複用 `_resolve_json_schema` | 無:它是 `classmethod`(`client.py:327`),以 `LLMClient._resolve_json_schema(...)` 呼叫、**不實例化** `LLMClient`,類本體零改動。(附帶:`LLMClient.__init__` 在 `LLM_API_KEY`/`LLM_BASE_URL` 未設時拋錯 —— claude-code 模式下這兩個變數可留空,正因 provider 從不實例化它) | 實測 grep |
| 腳本標籤行 | 無:追加 effort 的一行以 `if LLM_PROVIDER == "claude-code"` 為條件,API 模式標籤字串與現狀完全相同 | D7 |
| `requirements.txt` +jsonschema | 無:純新增依賴 | — |

最終防線:驗收標準含「`.env` 改回原 provider 後,API 模式行為與 main 分支完全
一致」的回歸驗證(§7)。

### D11 擴充點:Anthropic 官方 API provider(佔位 STUB)

未來若要直接呼叫 Anthropic 官方計費 API(需要更高併發、Batch API 半價、
或不想依賴本機 CLI 時),已預留完整的命名與路由:

| 面向 | 值 |
|---|---|
| `LLM_PROVIDER` | `anthropic` |
| 模組 | `app/infrastructure/llm/anthropic_client.py`(**不可命名為 `anthropic.py`**,會遮蔽套件) |
| 類別 | `AnthropicLLMClient` |
| 設定前綴 | `LLM_ANTHROPIC_*`(與 `LLM_CLAUDE_CODE_*` 完全分離) |

**目前狀態**:建構子與 `generate_structured_data` 皆拋 `LLMServiceError`,
訊息明確說明未實作並指引改用 `claude-code`。**刻意不靜默降級** —— 誤設
provider 必須在啟動當下暴露,而非批次跑到一半才失敗。

**設定前綴分離的必要性**(呼應本計劃的命名決策):Anthropic 官方 API 本身
也有 `output_config.effort`(`low`/`medium`/`high`/`xhigh`/`max`),與
claude-code CLI 的 `--effort` 是**兩個不同 provider 的獨立設定**。若沿用
籠統的 `LLM_CLAUDE_*` 前綴,屆時 `LLM_CLAUDE_EFFORT` 將無法從名稱判斷歸屬。

**實作指引**:完整 checklist 寫在該模組的 docstring 內(依賴與客戶端、
`output_config.format` 結構化輸出、schema 前處理的兩個坑、refusal 處理、
thinking/effort、介面契約、待新增設定、Batch API 與 prompt caching 的
成本優化空間)。三個要點摘要:

1. **schema 前處理**:Pydantic 的 `model_json_schema()` 需先以
   `LLMClient._resolve_json_schema()` 展平 `$defs`/`$ref`,且 Pydantic
   **不會**自動加 `additionalProperties: false`,而 json_schema 格式要求它
   與 `required` 皆齊備 —— 與 claude-code provider 同源的教訓
2. **必須處理 refusal**:`stop_reason == "refusal"` 時 HTTP 仍為 200,
   讀 `content` 前必須先檢查,否則會拿到空內容誤判。本專案的 galgame 台詞
   正是容易觸發拒答的內容類型,應映射為含分級表識別字串的 `LLMServiceError`
3. **介面契約不可偏離**:同簽名、同回傳型別;重試耗盡訊息含
   `LLM API 在所有重試後仍回傳空內容`、速率限制訊息含 `Quota`,
   生成腳本的錯誤分級表依賴這兩個字串

**設定刻意未加**:`LLM_ANTHROPIC_*` 三個欄位待實作時再加,避免產生無人讀取
的死設定(死設定會暗示不存在的功能)。docstring 已列出完整清單。

## 4. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `app/infrastructure/llm/claude_code_client.py` | **新增** `ClaudeCodeLLMClient`:CLI 探測、effort 白名單驗證、subprocess 呼叫、信封分流、三段校驗、修復重試、審計落盤(~200 行) |
| `app/infrastructure/llm/factory.py` | **新增** `create_llm_client()` 工廠:三向路由,provider 模組一律惰性 import |
| `app/infrastructure/llm/anthropic_client.py` | **新增** `AnthropicLLMClient` 佔位 STUB(D11):保留命名與路由,實例化即拋錯;docstring 內含完整實作 checklist |
| `app/core/config.py` | 新增 `LLM_CLAUDE_CODE_CLI_PATH`(預設 `""`)、`LLM_CLAUDE_CODE_EFFORT`(預設 `"high"`,白名單 `low`/`medium`/`high`/`xhigh`/`max`)、`LLM_CLAUDE_CODE_WORKDIR`(預設 `""` = `backend/.claude_code_workdir`)、`LLM_CLAUDE_CODE_TIMEOUT_SECONDS`(預設 `900`)、`LLM_CLAUDE_CODE_AUDIT_DIR`(預設 `logs/claude_code_audit`) |
| `app/main.py:115` | `LLMClient()` → `create_llm_client()`(lifespan 單例) |
| `app/services/task_handlers/jp_verb_pair_handler.py:183` | 同上 |
| `app/services/task_handlers/jp_core_verb_handler.py:242` | 同上 |
| `app/services/task_handlers/expression_handler.py:136` | 同上 |
| `app/services/task_handlers/example/verb_pair_example_handler.py:101` | 同上 |
| `scripts/database/MySQL/JP_VerbPair/build_llm_index.py:220` | 同上 |
| `scripts/database/MySQL/JP_VerbPair/build_llm_index_no_context.py:370` | 同上 |
| `scripts/local_anki/import_cards_with_llm.py:114` | 同上 |
| `scripts/fastapi_client/JP_VerbPair/generate_child_cards.py` | 標籤行追加 effort(D7):claude-code 模式下 `llm_model_name += f"@{settings.LLM_CLAUDE_CODE_EFFORT}"` |
| `requirements.txt` | 新增 `jsonschema>=4.0.0` |
| `.gitignore` | 新增 `.claude_code_workdir/`、`logs/claude_code_audit/` |

### Frontend

無。

### 測試

- backend 單元測試(`tests/infrastructure/test_claude_code_client.py`,mock subprocess):
  - happy path:合法信封 + 合法 JSON → 正確組裝 `LLMGenerateResult`
  - 信封分流:D6 表格每一列各一案(`structured_output_retry_exhausted` / `Not logged in` / 401 / 錯誤模型名)
  - 修復重試:第一次 schema 不符 → 第二次 prompt 含錯誤回饋 → 成功
  - 全敗:重試耗盡 → `LLMServiceError` 且訊息含分級表識別字串
  - 超時:`wait_for` 逾時 → 進程被 kill → 計失敗
  - CLI 探測:設定優先 / 依序兜底 / 全空報錯
  - effort 白名單:非法值(如 `ultra`、打錯字)→ 啟動即拋錯,不送 CLI
  - env 剔除:subprocess env 不含 `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`(即使父進程有)
  - schema 展平:傳入含 `$defs` 的 schema → 送 CLI 前已展平
  - 標籤格式:`LLMGenerateResult.model_name == f"({provider}){model}@{effort}"`(D7)
  - anthropic 佔位:`LLM_PROVIDER=anthropic` 時明確拋錯(訊息含「尚未實作」與替代方案),且繞過建構子直呼 `generate_structured_data` 仍被擋下(D11)
- 整合驗證(手動,見 §5 P3)

## 5. 實作順序

- **P0 環境與參數實測** ✅ 完成(2026-08-27):A 組 17 項零 ❌,定案呼叫規格、
  值域表、失敗形態、三模型基準均已產出 —— 全部紀錄於環境計劃 §3/§5。
- **P1 provider 本體** ✅ 完成(2026-08-27):`claude_code_client.py`(~430 行)
  + `factory.py` + config 五欄位 + 31 項單元測試(全綠)。另以**真實 CLI**
  端到端驗證:sonnet 36.7s、一次過、pydantic 驗證通過、挖空為原文子字串、
  12 句翻譯保留、標籤 `(claude-code)sonnet@high`、審計三檔落盤。
  實作期發現並修正一項文檔未預見的問題(見 D5):`create_subprocess_exec`
  在 Windows selector event loop 下拋 `NotImplementedError`,改用
  `asyncio.to_thread` + `subprocess.run`。
- **P2 接線** ✅ 完成(2026-08-27):8 處實例化點換工廠、兩支 generate_child_cards
  的 effort 標籤、`get_llm_client` 型別註記、`.gitignore`、`requirements.txt`
  (+jsonschema 4.26.0 已安裝)。回歸驗證:全套 122 項測試綠;以真實 `.env`
  (`LLM_PROVIDER=google`)確認工廠回傳 `LLMClient`、標籤 `gemini-3.1-pro-preview`,
  與 main 分支行為一致。
- **P3 端到端驗證** ✅ 完成(2026-08-27):以環境變數覆寫在 8001 埠起獨立 backend(不動 `.env`),全鏈路實跑。修掉一個既存環境漂移:系統 `elasticsearch` 套件為 9.4.1 但 requirements 釘 `<9.0.0`、伺服器為 8.19.15,v9 相容標頭被 ES 8 拒收(HTTP 400);降回 8.19.3 後正常。
- **P4 放量與收尾** ✅ 完成(2026-08-27):模型×力度掃描 7 組共 66 張卡 + 決賽 20 張,逐張人工評閱 + 程式化全檢。定案 `opus` + `medium`(38 秒/張)。D9 兩項已知差異的實測影響:輸出穩定性未見問題(86 張零 JSON 格式失敗);拒答率 opus@high 1/21、opus@medium 0/20。

## 6. 風險與未知

| 風險 | 應對 |
|---|---|
| 訂閱滾動窗口額度耗盡導致批次中斷 | D6 映射為 `Quota` 錯誤 → 腳本既有「暫停 60s 跳句」路徑;放量時分批跑(每批 ≤50) |
| 額度耗盡的實際輸出形態未知 | 量產首遇時記錄,補進 D6 與單元測試 |
| 登入憑證過期 | D3 失效偵測 + 報錯附 `claude auth login` 教學 |
| CLI 自動更新後行為漂移(信封欄位、重試次數、effort 預設值) | 關鍵值全部明寫(D4),不依賴 CLI 預設;provider 日誌記錄 `--version`;異常時與環境計劃 §3 基準對照 |
| 單卡耗時高於 API | 可接受:管線序列且 `BackendAPIClient` 本就 `timeout=None`;P4 記錄實測數據 |
| 合規形態滑移(被日後改成常駐/對外) | §1 合規邊界 + provider docstring 註明僅供使用者自發批次任務 |

## 7. 驗收標準(全數驗證完畢 2026-08-27)

- [x] `.env` 僅改 `LLM_PROVIDER` + `LLM_MODEL_NAME` 即可切換,全鏈路成功建卡(Context + Cloze + 母卡 JSON 回寫)。掃描期間為避免污染 `.env`,改以等效的環境變數覆寫達成
- [x] 標籤帶 effort 且兩處同串:Anki tag `LLM::(claude-code)opus@medium`(實查 40 張)、MySQL 去重表 `llm_model` 為 `(claude-code)opus@medium`(實查 20 筆),成功與失敗紀錄皆同
- [x] 改 `.env` 即換模型/力度、不動代碼:掃描期間實跑 7 種組合(sonnet×4 + opus×3)全部生效
- [x] `LLM_CLAUDE_CODE_EFFORT` 非法值啟動即報錯(單元測試涵蓋 `ultra` 等值)
- [x] 單元測試全綠:128 項(既有 94 項零破壞)
- [x] `.env` 改回原 provider 後行為與 main 一致:工廠回傳 `LLMClient`、標籤 `gemini-3.1-pro-preview`
- [x] 生成失敗走優雅路徑:opus@high 對一句露骨候選內容拒答 → `position_cloze` 判定失敗 → 422 → 腳本 `record_failure` 跳句 → 批次未中斷,10 張仍如期完成(生產環境實證,非僅單元測試)
- [x] 審計目錄留存 prompt/answer/meta,抽驗一筆與 Anki 卡片逐欄位一致(translation 全等、Cloze_Sentence 挖空位置相符、tag 相符)

## 8. 後續事項(不屬本計劃範圍)

- **JP_VerbPair 缺少目標動詞 token 級驗證**:掃描發現短動詞(如「よる」)的 ES 候選有大量假陽性(終助詞「〜よっ」、招呼語「よっす」),LLM 無拒答機制只能將錯就錯。JP_CoreVerb 的 `funnel.py` 已有 fugashi 驗證可擋,應移植過去。對品質的影響大於任何模型參數調整。
- **測試卡與去重紀錄清理**:掃描產生 86 張卡(43 次生成 × Context+Cloze),Anki 依 tag 清理;MySQL `generated_sentences_log` 亦有對應紀錄(含一筆 `(claude-code)claude-opus-5@high` 來自中途的單次驗證),未清除會阻擋這些 script_id+動詞組合日後重新生成。
