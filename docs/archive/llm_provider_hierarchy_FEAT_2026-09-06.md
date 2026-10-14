# FEAT — LLM 客戶端層級化:抽象基底 + CLI 中間層,接入 Gemini 原生 SDK 與 Antigravity CLI 兩個新 provider

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-09-06 |
| **性質** | 重構(既有三類掛上抽象基底)+ 新增機能設計(兩個新 provider)+ 實作工作項 |
| **狀態** | ✅ 實作完成並歸檔(2026-10-14)—— P0 實測、P1 基底重構、P2 `GeminiNativeLLMClient`、P3 `AntigravityLLMClient`、P4 文件收尾全數完成;全套 430 項測試綠、ruff / mypy 零錯誤,gemini-native 與 antigravity 皆以真實 API / CLI 經工廠跑通。**實跑驗收待使用者**:gemini-native 與 google 的拒答率比對、antigravity 跑一章、與 claude-code 的 20 張人工評閱(移入 §8 第一項,結果出來後回填本文件 §5 對應段落) |
| **範圍** | `app/infrastructure/llm/`(新增 `base.py`、`cli_base.py`、`schema_utils.py`、`labels.py`、`gemini_native_client.py`、`antigravity_client.py`;修改 `client.py`、`claude_code_client.py`、`anthropic_client.py`、`factory.py`)、`app/core/exceptions/infrastructure.py`(三個 `LLMServiceError` 子類)、`app/core/config.py`(`LLM_ANTIGRAVITY_*` 六欄位,含 P3 新增的 `_ISOLATE_HOME`)、`app/core/dependencies.py`、`app/services/runtime_config_service.py`、`scripts/common/llm_label.py`、`.env.example`、`.gitignore` |
| **不動** | `generate_structured_data` 簽名與 `LLMGenerateResult`;既有 provider 的標籤字串格式;既有錯誤訊息文字(生成腳本分級表的識別字串保留;例外:effort 不支援的訊息句型不變、支援清單隨註冊表擴充,見 §7);`scripts/fastapi_client/**` 生成腳本;各 handler 的 prompt 渲染與建卡邏輯;`audio_evaluator/` 語音軸線;API request/response schema |
| **PR / 進度** | P1:[PR #20](https://github.com/jacky917/FluencyTides/pull/20)(2026-10-14 合併,分支 `feat/llm-provider-hierarchy`);P2:[PR #21](https://github.com/jacky917/FluencyTides/pull/21)(2026-10-14 提交,分支 `feat/gemini-native-llm-client`);P2 已合併;P3:[PR #22](https://github.com/jacky917/FluencyTides/pull/22)(2026-10-14 提交,分支 `feat/antigravity-llm-client`,含 P4 文件收尾) |
| **關聯文件** | `docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md`(§D11 的 Anthropic 實作指引仍有效,本計畫只掛基底不實作本體)、`docs/bk/adr/004-audio-evaluator-strategy.md`(ABC 策略模式先例)、`docs/bk/08_Refactor_Recommendations.md` F043(fence 清理重複)、`app/infrastructure/audio_evaluator/base.py`(ABC 而非 Protocol 的原始論證)、本文件 HTML 版 `llm_provider_hierarchy_FEAT_2026-09-06.html`(含架構圖) |

---

## 1. 問題與動機

### 1.1 現況:三個裸類別,契約靠文字(已定位)

文字 LLM 軸線的三個客戶端沒有任何共同基底:

```
app/infrastructure/llm/client.py:57               class LLMClient:
app/infrastructure/llm/claude_code_client.py:84   class ClaudeCodeLLMClient:
app/infrastructure/llm/anthropic_client.py:134    class AnthropicLLMClient:
```

對照同一個 repo 語音軸線的 `class GeminiNativeAudioEvaluator(BaseAudioEvaluator):`,
差別一眼可見。三者的契約目前靠三樣東西維繫,沒有一樣是型別系統:

1. 工廠的 union 回傳型別 `"LLMClient | ClaudeCodeLLMClient | AnthropicLLMClient"`(`factory.py:52`)—— 只在型別檢查時有效,且每加一個 provider 要手動同步所有 union 出現點
2. 三份 docstring 反覆聲明同一組契約(`anthropic_client.py` 的實作指引第 5 點寫得最完整)
3. 錯誤訊息裡的魔術字串 `"LLM API 在所有重試後仍回傳空內容"` 與 `"Quota"`—— 生成腳本的錯誤分級表以子字串比對決定「跳句」或「暫停 60 秒」

代價已經在三處看得到:

- `app/core/dependencies.py:103` 的型別註記寫 `"LLMClient | ClaudeCodeLLMClient"`,漏了 `AnthropicLLMClient`——union 已經對不上
- `app/services/runtime_config_service.py:131-133, 191-193, 234-246` 只能用 `getattr(llm_client, "_formatted_model_name")`、`getattr(llm_client, "_cli_path")`、`hasattr(llm_client, "_build_env")` 甚至直接呼叫 `llm_client._build_env()` 來探測——沒有可宣告的型別,服務層只能對私有屬性做反射
- `docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md` §D9 用一張表格逐項人工核對兩個 client 的行為一致性——那張表做的事,`@abstractmethod` 加一個抽象 property 就能讓 mypy 自動做

程式碼共用的方式是跨類直呼 static/classmethod:`ClaudeCodeLLMClient` 直接叫
`LLMClient._resolve_json_schema()` 與 `LLMClient._strip_markdown_fences()`。效果等同繼承,
但把兩個純函式綁在一個具體實作類上,任何新 provider 都得「借用」`LLMClient` 的名字。

### 1.2 兩個新 provider 同時到來,而且落在不同的變化軸上

**Antigravity CLI(`agy -p`)**。`agy -h`(2026-09-06,Mac)顯示它與 `claude -p` 同型:
`-p`、`--model`、`--effort`、`--output-format json`、`--json-schema` 全部對得上。這代表
「subprocess CLI」現在有第二個成員,而重疊的不是模型名,是整套機械流程——
`asyncio.to_thread(subprocess.run)`、UTF-8 解碼、workdir / audit dir 解析、
fence 清理 → `json.loads` → dict 檢查 → `jsonschema.validate`、帶回饋的修復重試、
`_RetryableCliError`、審計落盤。這些佔 `ClaudeCodeLLMClient` 六成以上行數,逐字複製
到第二個類別說不過去。

**Gemini 原生 SDK(`google-genai`)**。目前文字軸線沒有原生 Gemini 實作,`LLMClient` 走的是
Google 的 OpenAI 相容端點。`client.py:114-117` 的註解寫明:該端點不認識 `safety_settings`,
傳了直接 400,只有第三方中轉站能靠 `extra_body` 塞進去。而 `.env` 的 `LLM_BASE_URL` 正是
那個 Google 相容端點。換言之 **`LLM_PROVIDER=google` 這條路根本無法把安全閾值調到 `BLOCK_NONE`**,
但 galgame 台詞正是最容易觸發 `PROHIBITED_CONTENT` 的內容(生成腳本分級表已為此字串留了跳句路徑)。
原生 SDK 的 `GenerateContentConfig` 直接收 `safety_settings` 與 `thinking_config`,這是
OpenAI 相容層永遠給不了的。語音軸線的 `GeminiNativeAudioEvaluator` 已用同一個 SDK,先例齊備。

### 1.3 已埋的坑(重構時順手拆,不另開計畫)

- `factory.py:94-98` 的 anthropic 分支 `return AnthropicLLMClient()`,**既不傳 `model` 也不傳 `effort`**。今天無害是因為 stub 一律拋錯;等到照 §D11 實作出 `LLM_ANTHROPIC_EFFORT`,兩個覆寫參數會靜默消失——不報錯,只是沒生效
- `factory.py:100` 硬編碼「effort 覆寫僅 claude-code provider 支援」。第二個支援 effort 的 provider(agy 有 `--effort`,Anthropic 將有 `output_config.effort`)一出現,這行就錯
- 模型標籤的推導有三份:`client.py:110-112`、`claude_code_client.py`(同段邏輯逐字重複)、`scripts/common/llm_label.py`。後者 docstring 記著 2026-08-28 因兩份 `.env` 不一致誤標 190 筆 DB 紀錄的前科
- 錯誤分級靠字串:`scripts/fastapi_client/JP_VerbPair/generate_child_cards.py:490-495`、`JP_CoreVerb/generate_child_cards.py:475-482`、`mark_skipped_es_data.py:293-299` 三處 `"LLM API 在所有重試後仍回傳空內容" in e.response.text`。而 `FluencyTidesError` 本來就有 `error_code` 欄位、`main.py:261` 的全域 handler 本來就把它放進 `ErrorResponse`——機制存在,只是 `LLMServiceError` 一律回 `LLM_SERVICE_ERROR`,沒有細分

## 2. 目標與非目標

**目標**

- G1 新增 `BaseLLMClient(abc.ABC)`,五個具體類別全部掛上;`generate_structured_data` 為 `@abstractmethod`,mypy 可驗證契約
- G2 能力旗標(`SUPPORTS_EFFORT` / `SUPPORTS_SAFETY_SETTINGS`)取代 provider 字串硬判;工廠不再認得任何 provider 名,只查註冊表與旗標
- G3 新增 `HeadlessCliLLMClient(BaseLLMClient)` 承載 subprocess 共用流程;`ClaudeCodeLLMClient` 瘦身為「命令列、環境、信封分流」三個 hook,`tests/test_claude_code_client.py` 既有 40 項測試**一行不改**全綠
- G4 新增 `GeminiNativeLLMClient`,`LLM_PROVIDER=gemini-native` 即生效,`safety_settings` 四類一律 `BLOCK_NONE`
- G5 新增 `AntigravityLLMClient`,`LLM_PROVIDER=antigravity` 即生效
- G6 新增 `LLMRetryExhaustedError` / `LLMQuotaError` / `LLMRefusalError` 三個 `LLMServiceError` 子類,各帶獨立 `error_code`,經既有 handler 進 `ErrorResponse`;訊息文字保留分級表識別字串
- G7 標籤推導與 schema 工具各只剩一份
- G8 既有行為零變更:`LLM_PROVIDER=google` / `claude-code` 兩條路的標籤字串、錯誤訊息、重試次數與間隔逐位元一致(例外:拒答訊息依 D5 追加 `PROHIBITED_CONTENT` 說明,見 §5 P1「2026-10-08 複查」;腳本側失敗紀錄的標籤改與後端一致,見 §5 P1「2026-10-09 code review 修正」)

**非目標**

- 不做母公司層級的中間抽象(D1 說明為什麼那是錯的分組軸)
- 原生 SDK 群(Gemini / Anthropic)不設中間層(D4)
- 不改 `LLMClient` 的重試時序——`_retry_transient()` helper 由新 client 首用,`LLMClient` 換用列 §8
- 不改生成腳本的分級表——`error_code` 遷移列 §8;本計畫靠保留訊息字串讓腳本零感知
- 不實作 `AnthropicLLMClient` 本體——只掛基底、修正建構子收 `model` / `effort`;§D11 指引原樣有效
- 不動語音軸線(`audio_evaluator/`)

## 3. 設計決策

### D1 分組軸線:傳輸協定,不是母公司

繼承樹按「怎麼把 prompt 送出去、怎麼把 JSON 收回來」分,不按「模型是誰家的」分。
兩個反例足以說明:

- `ClaudeCodeLLMClient` 與 `AnthropicLLMClient` 同屬 Anthropic,共用面積是零——一個是 subprocess + CLI 信封 + 落盤憑證/OAuth token 雙模式,另一個是 HTTPS + `anthropic` SDK + `output_config.format` + `stop_reason` 檢查
- Google、OpenAI、Yinli/DeepSeek 是三個母公司,卻共用同一個 `LLMClient`——它們共用 `AsyncOpenAI`、`chat.completions.create`、`response_format`、fence 清理、`tool_calls` 相容分支

母公司分組會把零共用的湊在一起、把全共用的拆開。設定層早已做過同樣的判斷:
`anthropic_client.py` 實作指引第 6 點寫明 `LLM_ANTHROPIC_EFFORT` 與 `LLM_CLAUDE_CODE_EFFORT` 是
兩個獨立 provider 的獨立設定,「這正是設定前綴採 provider 全名(而非籠統的 `LLM_CLAUDE_*`)的原因」。

按傳輸協定分成三群:

| 群 | 成員 | 中間層 |
|---|---|---|
| OpenAI 相容 HTTP | `LLMClient`(Google 相容端點、OpenAI、各中轉站) | 無(一個類別內以 provider 字串分流,分支只有兩處,不值得拆) |
| 原生 SDK | `GeminiNativeLLMClient`(新)、`AnthropicLLMClient`(stub) | 無(D4) |
| subprocess CLI | `ClaudeCodeLLMClient`、`AntigravityLLMClient`(新) | `HeadlessCliLLMClient`(D3) |

最終繼承樹:

```
BaseLLMClient (abc.ABC)                                   llm/base.py
├── LLMClient                                             llm/client.py
├── GeminiNativeLLMClient                                 llm/gemini_native_client.py   [新]
├── AnthropicLLMClient                                    llm/anthropic_client.py       [stub,掛基底]
└── HeadlessCliLLMClient (abc.ABC)                        llm/cli_base.py               [新]
    ├── ClaudeCodeLLMClient                               llm/claude_code_client.py     [瘦身]
    └── AntigravityLLMClient                              llm/antigravity_client.py     [新]
```

### D2 `BaseLLMClient`:ABC 而非 Protocol,帶能力旗標與共用 helper

選 ABC 的三個理由,與 `audio_evaluator/base.py` 的論證一致:

1. 要強制子類實作 `generate_structured_data`,漏實作在 import 時就炸,不是呼叫時
2. 基底要放**具體**共用邏輯(統一的 `model` 解析、effort 支援檢查、`_retry_transient`),Protocol 放不了
3. 工廠與 `RuntimeConfigService` 需要 `isinstance` 做能力分流(D11),Protocol 的 `runtime_checkable` 只檢查方法名存在,不檢查旗標

介面:

```python
class BaseLLMClient(abc.ABC):
    PROVIDER_NAME: ClassVar[str]                       # 工廠註冊鍵,也是標籤的 (provider) 段
    SUPPORTS_EFFORT: ClassVar[bool] = False
    SUPPORTS_SAFETY_SETTINGS: ClassVar[bool] = False

    def __init__(self, *, model: str | None = None, effort: str | None = None) -> None:
        # 統一:model 解析(覆寫 > LLM_MODEL_NAME,空即拋錯)
        # 統一:effort 給值但 SUPPORTS_EFFORT=False → LLMServiceError(取代 factory.py:100)
        # 子類 super().__init__() 後再做自己的驗證與資源建立

    @property
    def model_label(self) -> str: ...                  # 取代跨層讀 _formatted_model_name

    @abc.abstractmethod
    async def generate_structured_data(
        self, system_prompt: str, user_prompt: str, response_schema: dict[str, object],
    ) -> LLMGenerateResult: ...

    async def _retry_transient(self, op, *, is_transient, max_retries=3, base_delay=1.0):
        # 具體 helper:指數退避 + asyncio.wait_for 絕對超時
        # 藍本 audio_evaluator/gemini_client.py:150-175(503/429/UNAVAILABLE/high demand 判斷)
```

`_formatted_model_name` 屬性保留一個版本作為 `model_label` 的 alias(D10),既有讀取點
(`runtime_config_service.py:133`、`build_llm_index.py:165,227`)遷到 `model_label`。

### D3 `HeadlessCliLLMClient`:template method,固定流程在基底,CLI 差異做成 hook

上一輪(2026-09-05 討論)判斷「中間層等重複出現再抽」,觸發條件現在成立:兩個成員、
重疊的是機械流程而非模型名。切法:

| 放在基底(具體實作,子類不碰) | 子類必須提供(`@abstractmethod`) |
|---|---|
| `generate_structured_data()` —— 帶回饋的修復重試迴圈(template method 本體) | `_build_command(system_prompt, schema_text) -> list[str]` |
| `_invoke_cli()` —— `asyncio.to_thread(subprocess.run)`、UTF-8 解碼、`TimeoutExpired` / `OSError` → `LLMServiceError` | `_build_env() -> dict[str, str]` |
| `_validate_payload(raw, schema)` —— fence 清理 → `json.loads` → dict 檢查 → `jsonschema.validate`,失敗拋 `_RetryableCliError`(含回饋文字) | `_extract_result(stdout, stderr) -> str` —— 解析信封;致命 → `LLMServiceError` 子類,可重試 → `_RetryableCliError` |
| `_resolve_workdir()`、`_resolve_audit_dir()`、`_write_audit()` | `_build_stdin(system_prompt, user_prompt) -> str` —— 預設回傳 `user_prompt`(claude 用旗標帶 system prompt);agy 無 `--system-prompt`,必須覆寫成併入 |
| `_resolve_cli_path()` —— 泛化為「設定值 → `~/.local/bin/<BINARY_NAME>` → PATH → `_extra_cli_candidates()`」 | `_extra_cli_candidates() -> list[Path]`(預設空;claude 覆寫成 `%APPDATA%` glob) |
| `_RetryableCliError` 搬到此模組 | |

子類以類別屬性宣告差異,不寫邏輯:

| 屬性 | `ClaudeCodeLLMClient` | `AntigravityLLMClient` |
|---|---|---|
| `PROVIDER_NAME` | `"claude-code"` | `"antigravity"` |
| `BINARY_NAME` | `"claude"` | `"agy"` |
| `VALID_EFFORTS` | `{low, medium, high, xhigh, max}` | `{low, medium, high}`(依 `agy -h`) |
| `SCRUBBED_ENV_VARS` | `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | P0 實測後定(A6) |
| `MODEL_LABEL_STRIP_PREFIX` | `"claude-"` | `""` |
| `MAX_RETRIES` | `2`(CLI 內建 5 次) | P0 實測後定(A3:若 `--json-schema` 非 harness 強制,外層需更多) |
| `SUPPORTS_EFFORT` | `True`(標在 `HeadlessCliLLMClient` 上,兩者繼承) | 同左 |

provider 專屬設定不進基底:子類在 `__init__` 讀自己的 `LLM_<PROVIDER>_*` 組成
`CliRuntime(cli_path, workdir, timeout_seconds, audit_dir)` frozen dataclass 交給 `super().__init__()`。
基底對 `settings` 的依賴只剩 `LLM_MODEL_NAME` / `LLM_PROVIDER`。

不可改的實作約束(沿自 2026-08-27 計畫 D5):`_invoke_cli` 必須用 `asyncio.to_thread` +
`subprocess.run`,不可改 `create_subprocess_exec`(Windows selector event loop 不支援)。

### D4 原生 SDK 群不設中間層

兩個成員一個是 stub、一個尚未建;兩個 SDK 的形狀不同——anthropic 是 `messages.create`
+ `output_config.format` + 檢查 `stop_reason == "refusal"`,google-genai 是
`aio.models.generate_content` + `GenerateContentConfig` + 檢查 `candidates[0].finish_reason`。
信封解析沒得共用。真正共用的只有瞬時錯誤重試,而 `LLMClient` 也需要它——所以放
`BaseLLMClient._retry_transient()`,不另開一層。若日後兩者的 refusal / safety 映射出現
逐字重複,再抽;由重複驅動,不由分類法驅動。

### D5 例外子類:`error_code` 取代魔術字串,訊息文字保留過渡

```python
class LLMRetryExhaustedError(LLMServiceError):   error_code = "LLM_RETRY_EXHAUSTED"
class LLMQuotaError(LLMServiceError):            error_code = "LLM_QUOTA"
class LLMRefusalError(LLMServiceError):          error_code = "LLM_REFUSAL"
```

| 例外 | 拋出時機 | 訊息必含(過渡期,供既有分級表子字串比對) | 腳本分級表既有處置 |
|---|---|---|---|
| `LLMRetryExhaustedError` | 外層重試耗盡(空內容 / 壞 JSON / schema 不符) | `"LLM API 在所有重試後仍回傳空內容"` | 記錄失敗、跳此句 |
| `LLMQuotaError` | 429 / 額度耗盡 / claude-code 的 rate limit 信封 | `"Quota"` | 暫停 60 秒 |
| `LLMRefusalError` | Gemini `finish_reason` ∈ {SAFETY, PROHIBITED_CONTENT};Anthropic `stop_reason == "refusal"`;claude-code 拒答形態 | `"PROHIBITED_CONTENT"` **與** `"LLM API 在所有重試後仍回傳空內容"` 兩者皆含 | 跳此句(與現況相同) |

`error_code` 零 API 改動即可到達腳本:`FluencyTidesError.error_code` 經 `main.py:261`
的 `fluencytides_error_handler` 進 `ErrorResponse.error_code`,腳本已收到該 JSON,只是
現在沒讀那個欄位。腳本改讀 `error_code` 列 §8;本計畫靠訊息文字讓腳本零感知。

三個既有拋錯點改型別、訊息不動:`client.py:230,239,257,295`、`claude_code_client.py:355`
與 `_raise_for_error_envelope` 內的 quota 分支。

### D6 標籤與 schema 工具:各一份

```
app/infrastructure/llm/labels.py
    build_model_label(provider: str, model: str, effort: str | None, *, strip_prefix: str = "") -> str
    # 規則不變:google/openai/空 → 裸模型名;其餘 → (provider)model;effort 非 None → @effort
app/infrastructure/llm/schema_utils.py
    resolve_json_schema(schema) / strip_markdown_fences(content)
    # 從 LLMClient 搬出;LLMClient 保留同名 classmethod 轉呼一個版本(D10)
```

`scripts/common/llm_label.py` 改為薄包裝:讀本機 settings 後呼叫
`build_model_label(settings.LLM_PROVIDER, settings.LLM_MODEL_NAME, effort=<依 provider 取對應設定>, strip_prefix=...)`。
其「僅供失敗紀錄與 fallback」的定位不變。

### D7 `GeminiNativeLLMClient` 規格

| 項目 | 值 |
|---|---|
| `PROVIDER_NAME` | `"gemini-native"`(連字號,與 `claude-code` 一致;語音軸線的 `gemini_native` 是底線,該不一致既存,新 provider 跟 LLM 軸線走) |
| 金鑰 | 沿用 `LLM_API_KEY`——`AUDIO_API_KEY` 的註解已寫明 Google 原生與 OpenAI 相容共用同一把 key;不另開前綴 |
| `LLM_BASE_URL` | 對此 provider 無意義,忽略(不拋錯,記一行 debug) |
| `SUPPORTS_SAFETY_SETTINGS` | `True`;四類一律 `BLOCK_NONE`,不加開關(與現況對中轉站的行為一致) |
| `SUPPORTS_EFFORT` | `False`(thinking budget 是另一個概念,列 §8) |
| 呼叫 | `client.aio.models.generate_content(model, contents=user_prompt, config=GenerateContentConfig(system_instruction=system_prompt, temperature=0.0, response_mime_type="application/json", response_json_schema=resolve_json_schema(schema), safety_settings=[...]))`,以 `_retry_transient()` 包裝(網路層:5xx 與 aiohttp / httpx 連線類例外,退避 1 秒、2 秒),`asyncio.timeout` 120 秒整體上限(**P2 實作定案**,A9 見下) |
| system prompt | google-genai 有 `system_instruction`,用它,不併入 contents |
| 分流 | **P2 實作定案**:先看 `prompt_feedback.block_reason`(prompt 被擋),否則看 `candidates[0].finish_reason`。`SAFETY` / `PROHIBITED_CONTENT` / `BLOCKLIST` / `SPII` / `JAILBREAK` → 拒答(即使帶完整 JSON 也不當成功);空內容、截斷、壞 JSON → 重試耗盡;內容層與 `LLMClient` 同樣 3 次、固定 2 秒,最後一次的型態決定子類。429 → `LLMQuotaError`(不重試);5xx / 連線 → 瞬時重試;400 / 401 / 403 / 404 → `LLMServiceError` 附設定提示(不重試)。訊息一律帶 `finish_reason`,API 錯誤只取 `code status: message`、不嵌回應 JSON(避免誤觸腳本子字串分級) |
| 標籤 | `(gemini-native)gemini-3.5-pro`,走 D6 通用規則,自然與 OpenAI 相容那條(裸 `gemini-3.5-pro`)區分 |
| 白名單 | `MODIFY_LLM_MODEL_NAME` 現值即 gemini-3.x 名稱,**免改**——唯一不用動白名單的新 provider |
| 依賴 | `google-genai` 已在 `requirements.txt`,P2 把下限由 `>=0.2.0` 提到實測的 `>=2.10.0`(`response_json_schema` 是較新欄位);模組維持惰性 import |

**A9(P0 實測項)**:`generate_structured_data` 收的是 dict schema,語音軸線是直接丟 Pydantic class。
須確認目前安裝的 google-genai 版本收原始 dict 的方式(`response_schema` 直接吃 dict、或
`response_json_schema`),以及是否仍需 `resolve_json_schema` 展平——該函式當初就是為
Gemini 不吃 `$ref` 而寫,原生 SDK 可能已不需要。實測結果決定 D7 呼叫列的 `<見 A9>`。

**A9 實測結果**(2026-10-14,google-genai 2.10.0、`gemini-3.1-pro-preview`、真實 `ChildCardGenerationResult` schema):

| 傳法 | 原始 schema(含 `$defs` / `$ref`) | 展平後 |
|---|---|---|
| `response_json_schema` | ✅ 接受,輸出通過 Pydantic 驗證 | ✅ 同左 |
| `response_schema`(dict) | ✅ 同左 | ✅ 同左 |

四種組合全數可用,原生 SDK 已能吃 `$ref`。定案用 `response_json_schema` + 展平:前者是 SDK 收原始 JSON Schema 的欄位(`response_schema` 會先轉成 OpenAPI 子集),展平則讓各 provider 送出同一份 schema,排除 `$ref` 解析差異這個變因。另確認 SDK 的 tenacity 重試預設關閉(`retry_args(None)` 為只試一次),但環境裝了 aiohttp 時非同步請求一律走 aiohttp,其連線錯誤另有一次 SDK 內建重試(先等隨機 1–10 秒,無法關閉)。

### D8 `AntigravityLLMClient` 規格

從 `agy -h` 對照出的實質差異(決定 hook 內容):

| | `claude -p` | `agy -p` | 對 hook 的影響 |
|---|---|---|---|
| system prompt | `--system-prompt` | **無對應旗標** | 覆寫 `_build_stdin` 併入;現有 prompt 是照「system 定角色、user 給資料」寫的,併成一段後效果要重測(P3 放量比對) |
| 工具隔離 | `--safe-mode --tools ""` | 只有 `--sandbox`(限制終端)、`--mode plan` | 沒有「純文字生成」開關;空 workdir 擋專案脈絡,擋不住模型主動呼叫工具(A4) |
| 全域脈絡 | `--safe-mode` 隔離 CLAUDE.md | 無旗標;本機有 `~/.gemini/GEMINI.md` | 可能被自動載入污染生成(A5) |
| 會話殘留 | `--no-session-persistence` | 無;有 `--new-project` | 批次會累積 conversation;決定接受或每次 `--new-project`(A2 附帶) |
| 自身超時 | 無 | `--print-timeout` **預設 5 分鐘** | 必須把 `LLM_ANTIGRAVITY_TIMEOUT_SECONDS` 同步傳給 `--print-timeout`,否則它先砍 |
| slash 展開 | 無此問題 | `--disable-slash-commands` | 一定要開,台詞以 `/` 開頭會被當指令 |
| effort 值域 | 五檔 | `low\|medium\|high` | `VALID_EFFORTS` 三檔 |

暫定 `_build_command`(P0 後定案):

```
agy -p --output-format json --json-schema <schema> --model <m> --effort <e>
    --print-timeout <T>s --disable-slash-commands --sandbox [--new-project]
```

設定(前綴用 provider 全名,沿既有慣例):`LLM_ANTIGRAVITY_CLI_PATH`(預設 `""` 自動探測)、
`LLM_ANTIGRAVITY_EFFORT`(預設 `"high"`)、`LLM_ANTIGRAVITY_WORKDIR`(預設 `""` = `backend/.antigravity_workdir`)、
`LLM_ANTIGRAVITY_TIMEOUT_SECONDS`(預設 `900`)、`LLM_ANTIGRAVITY_AUDIT_DIR`(預設 `logs/antigravity_audit`)。
workdir 與 audit dir **必須**與 claude 分開——agy 有 `--project` 概念,會在 cwd 留東西。
標籤 `(antigravity)gemini-3.5-pro@high`。

**P0 實測清單**(`-h` 只看得到旗標;下列決定信封解析與穩定性,比照 2026-08-27 計畫的 A 組):

| # | 項目 | 為什麼非測不可 |
|---|---|---|
| A1 | `-p` 讀 stdin 還是吃參數? | 若只吃參數,18KB prompt 在 Windows 撞 argv 上限 |
| A2 | `--output-format json` 信封欄位名;錯誤怎麼標;是否有 `--new-project` 以外的無殘留方式 | `_extract_result` 的全部內容 |
| A3 | `--json-schema` 是 harness 層強制(帶重試)還是只轉給模型? | 決定 `MAX_RETRIES` |
| A4 | 無工具模式:`--sandbox` 夠不夠、`--mode plan` 會不會先寫計畫、有無可自訂的最小 `--agent` | 防止 print 模式卡在權限提示或工具呼叫 |
| A5 | `~/.gemini/GEMINI.md` 在 print 模式下讀不讀 | 脈絡污染 |
| A6 | 認證:憑證檔位置、要剔除哪些環境變數、容器的 headless token 等價物 | `SCRUBBED_ENV_VARS` 與 `_build_env` |
| A7 | 致命標記:模型名錯、未登入、額度耗盡各長什麼樣 | `_extract_result` 的分流 |
| A8 | Windows 二進位:`agy` 不在 PATH,Antigravity IDE 目錄無獨立 `agy.exe`;`agy install` 後路徑為何 | `_extra_cli_candidates` |

#### 實測紀錄(P0,2026-10-14,Windows 11,agy 1.1.27 → 1.3.5)

在 scratchpad 的空目錄跑,模型 `gemini-3.8-flash`、`--effort low`,一律帶 `--sandbox --disable-slash-commands`、不帶 `--dangerously-skip-permissions`。約 20 次呼叫。

**總表**

| # | 結論 | 對實作的影響 |
|---|---|---|
| A1 | `-p` **必須帶值**,`-p -` 的 `-` 被當成字面 prompt,**不讀 stdin**。stdin 只能走 `--input-format stream-json`(須搭配 `--output-format stream-json`、不可再給 `-p`),每行一則 `{"event":"user","message":{"role":"user","content":"…"}}` | 用 stream-json 收發,避開 Windows 命令列長度上限;`_build_stdin` 輸出一行 NDJSON |
| A2 | 信封見下方原始紀錄;stream-json 逐行輸出 `init` → `step_update`… → `result`,`result` 的結構與 `--output-format json` 相同。workdir 不留東西,但每次呼叫在 `~/.gemini/antigravity-cli/conversations/` 留一個會話檔 | `_extract_result` 取最後一行 `event=result`;會話殘留靠 A5 的專用 home 隔離 |
| A3 | `--json-schema` **由 CLI 強制**:模型多走一步呼叫 `finish` 工具,結果放在信封獨立的 `structured_output` 欄位;即使 prompt 叫它別輸出 JSON,`structured_output` 仍合 schema。`response` 文字混有散文與 `toolAction` 等額外欄位 | **讀 `structured_output`,不讀 `response`**;單看 A3,`MAX_RETRIES` 可比照 claude 維持 2——後因 A7 的 503 頻率改為總嘗試 3 次 |
| A4 | print 模式預設開放約 60 個工具(`run_command`、`write_to_file`、`search_web`、`call_mcp_tool`、瀏覽器控制…),`permission_mode=request-review`。**workspace 內寫檔不需許可**(實測真的建出檔案);執行命令被自動拒絕,信封多 `denied_actions`、`status` 仍是 `SUCCESS` 但 `response` 為空 | 有 `denied_actions` 或缺 `structured_output` 一律視為可重試失敗;workdir 必須是專用空目錄 |
| A5 | **會讀 `~/.gemini/GEMINI.md`**(模型逐字引出 FluencyTides 開發規範的第一個標題),input tokens 多約 1.8k。把 `HOME` / `USERPROFILE` 指到空目錄後回答 NONE、認證照常 | `_build_env` 把 `HOME` / `USERPROFILE` 指向專用 home(會話紀錄也一併隔離在那裡) |
| A6 | 憑證在 **OS keyring**(Windows Credential Manager,binary 內含 `wincred` / `keyring`),不在 `~/.gemini` 檔案裡——所以換 home 不影響認證。binary 內可見 `JETSKI_OAUTH_TOKEN`、`AGY_ADC_AUTH`(容器 headless 認證的候選,未實測),以及 IDE 設的 `ANTIGRAVITY_CONVERSATION_ID` / `ANTIGRAVITY_LS_ADDRESS` / `ANTIGRAVITY_CSRF_TOKEN` 等 | 剔除 `ANTIGRAVITY_*`(防止從 IDE 終端機啟動時黏到 IDE 會話);容器認證留待部署時測 |
| A7 | 失敗形態:模型名錯 → exit 1、`status=ERROR`、`error` 含 `invalid model selection`;服務不可用 → exit 1、`error` 含 `Eligibility check failed: UNAVAILABLE (code 503)`,**本日約四成呼叫遇到**,重試即過;用法錯誤 → exit 2(只有 stderr)。未登入、額度耗盡未能觸發(1.3.3 changelog:個人額度耗盡會立即回報、不重試) | 503 / UNAVAILABLE 可重試;invalid model 致命;exit 2 致命 |
| A8 | Windows:`%LOCALAPPDATA%\agy\bin\agy.exe`。現行安裝會把該目錄加入**使用者 PATH**(9 月的「不在 PATH」是舊安裝的狀況) | `_extra_cli_candidates` 仍加這個路徑,作為 PATH 未生效時(安裝前已啟動的程序)的兜底 |
| 新 | `--model` 給**基底名**(`gemini-3.8-flash`),思考深度另用 `--effort`;`agy models` 列的 `gemini-3.8-flash-low` 是組合後的顯示名,傳給 `--model` 會被拒。1.3.5 的 effort 值域為 `low\|medium\|high\|xhigh\|max` | `VALID_EFFORTS` 五檔(與 claude 相同);白名單要放基底名 |
| 新 | **會自動更新**:探測期間從 1.1.27 自己升到 1.3.5,期間 `-h` 輸出改變(`--print-timeout` 預設由 5 分鐘改為 0 = 不限)。binary 內有 `AGY_CLI_DISABLE_AUTO_UPDATE` | `_build_env` 設 `AGY_CLI_DISABLE_AUTO_UPDATE=1`;版本由使用者手動 `agy update` |
| 新 | 啟動開銷大且不穩:模型本身 2–4 秒,整個程序 8–163 秒(資格檢查與內部重試) | 逾時預設要寬(計畫的 900 秒足夠);批次吞吐量會明顯低於 claude-code |

**原始信封**

`--output-format json` 成功:

```json
{"conversation_id":"5c5152f0-d71e-49b3-9c04-607bbce2c278","status":"SUCCESS","response":"PONG\n","duration_seconds":117.094311,"num_turns":1,"usage":{"input_tokens":15046,"output_tokens":2,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":15048}}
```

帶 `--json-schema`(A3,prompt 要求只寫散文):

```json
{"conversation_id":"f5f43828-…","status":"SUCCESS","response":"The tide breathes against the shore, …\n{\"answer\":0,\"note\":\"Two-sentence poem about the sea completed.\",\"toolAction\":\"Finishing task\",\"toolSummary\":\"Task completion\"}\n","duration_seconds":80.25,"num_turns":2,"structured_output":{"answer":0,"note":"Two-sentence poem about the sea completed."},"json_schema":{…},"usage":{"input_tokens":30746,"output_tokens":174,"thinking_tokens":95,"cache_read_tokens":0,"total_tokens":30920}}
```

工具被拒(A4,stderr 另有說明):

```json
{"conversation_id":"0bc7f5bd-…","status":"SUCCESS","response":"","duration_seconds":7.44,"num_turns":1,"usage":{…},"denied_actions":[{"action":"command","display_name":"RunCommand"}]}
```

```text
jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied. Add an allow-rule under permissions.allow in settings.json (e.g. command(<target>)). Alternatively, re-run with --dangerously-skip-permissions to auto-approve all tools.
```

模型名錯 / 服務不可用(A7,exit 1):

```json
{"conversation_id":"","status":"ERROR","response":"","error":"invalid model selection (--model \"gemini-3.8-flash-low\" --effort \"\"): model gemini-3.8-flash-low is not recognized as a known model or custom model in settings","duration_seconds":0,"num_turns":0,"usage":{…}}
{"conversation_id":"","status":"ERROR","response":"","error":"Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable.","duration_seconds":0,"num_turns":0,"usage":{…}}
```

stream-json 模式下的同一個 503(stdout 只有這一行 `result`,沒有 `init`;stderr 另有 `error: Eligibility check failed: …`)——P3 依此確認 stream-json 的錯誤也會產生 `result` 事件:

```json
{"event":"result","result":{"conversation_id":"","status":"ERROR","response":"","error":"Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable.","duration_seconds":0,"num_turns":0,"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":0}}}
```

stream-json 輸出(A1 / A2):

```text
{"event":"init","conversation_id":"…","init":{"model":"gemini-3.8-flash","cwd":"…","tools":[…約 60 個…],"permission_mode":"request-review"}}
{"event":"step_update","step_update":{…,"state":"DONE","step_type":"user_input"}}
{"event":"step_update","step_update":{…,"state":"ACTIVE","step_type":"agent_response","text_delta":"PONG"}}
{"event":"step_update","step_update":{…,"state":"DONE","step_type":"agent_response","text_delta":"\n","duration_seconds":2.36,"usage":{…}}}
{"event":"result","result":{"conversation_id":"…","status":"SUCCESS","response":"PONG\n","duration_seconds":2.44,"num_turns":1,"usage":{…}}}
```

**端到端驗證**:專用 home + `AGY_CLI_DISABLE_AUTO_UPDATE=1` + stream-json stdin(system prompt 以 `<system>…</system>` 區塊併入)+ `--json-schema` 指向展平的 `ChildCardGenerationResult` schema 檔 → 事件序列 `init` → `user_input` → `agent_response` → `finish` → `result`,`structured_output` 通過 Pydantic 驗證,workdir 無殘留,37 秒(前一次遇 503)。

**對 §D8 暫定命令的修正**:

```text
agy --input-format stream-json --output-format stream-json --json-schema <展平後的 schema 字串>
    --model <基底名> --effort <e> --sandbox --disable-slash-commands [--print-timeout <T>s]
    (stdin:一行 {"event":"user","message":{"role":"user","content":"<system 區塊 + user prompt>"}})
    (env:HOME / USERPROFILE → 專用 home、AGY_CLI_DISABLE_AUTO_UPDATE=1、剔除 ANTIGRAVITY_*)
```

不再需要 `--new-project`(會話殘留由專用 home 隔離)。schema 以字串傳(與 claude 相同;展平後的卡片 schema 約 5KB,遠低於 Windows 命令列上限,且免去每次呼叫寫暫存檔的並行問題)——P3 實作定案,原先寫的「改傳檔案路徑」不採用。

### D9 工廠:註冊表 + 統一參數傳遞

```python
_LOADERS: dict[str, Callable[[], type[BaseLLMClient]]] = {
    "claude-code":   lambda: _import("claude_code_client", "ClaudeCodeLLMClient"),
    "antigravity":   lambda: _import("antigravity_client", "AntigravityLLMClient"),
    "gemini-native": lambda: _import("gemini_native_client", "GeminiNativeLLMClient"),
    "anthropic":     lambda: _import("anthropic_client", "AnthropicLLMClient"),
}

def create_llm_client(*, model=None, effort=None) -> BaseLLMClient:
    provider = (settings.LLM_PROVIDER or "").strip().lower()
    client_cls = _LOADERS.get(provider, lambda: LLMClient)()
    return client_cls(model=model, effort=effort)      # 所有 provider 一律收兩個參數
```

- **P1 review 後定案**:註冊列為 `_ProviderEntry(module, class_name, is_cli, supports_effort)`——兩個能力旗標**靜態**宣告,讓 `is_cli_provider()` / `providers_supporting_effort()` 是純查表(診斷端點與錯誤訊息在依賴缺失時仍能運作);`resolve_client_class()` 載入時核對 `PROVIDER_NAME == 註冊鍵`、`issubclass(HeadlessCliLLMClient) == is_cli`、`SUPPORTS_EFFORT == supports_effort`,不一致即 `RuntimeError`,三處宣告不會漂移
- 惰性 import 慣例不變:loader 在被選中時才 import,第三方依賴不成為其他 provider 的啟動前提
- **既有測試的攔截點必須保留**:`factory.py` 頂層維持 `from app.infrastructure.llm.client import LLMClient`(`test_claude_code_client.py:533,607` 以 `mock.patch("app.infrastructure.llm.factory.LLMClient")` 攔截);loader 以 `importlib.import_module` + `getattr` 在**呼叫時**解析類別,`mock.patch.object(claude_code_client, "ClaudeCodeLLMClient")`(`:532,542,550`)才仍有效
- effort 支援檢查搬進 `BaseLLMClient.__init__`(D2),工廠不再有 `factory.py:100` 那行
- 未註冊的 provider 值(google / openai / 中轉站名 / 空)一律回 `LLMClient`,行為與現況一致
- 回傳型別改 `BaseLLMClient`,`dependencies.py:103` 同步

### D10 相容性保證:兩條既有路徑逐位元一致

| 面向 | 保證 | 驗證方式 |
|---|---|---|
| 標籤字串 | `google` → `gemini-3.1-pro-preview`;`claude-code` → `(claude-code)opus-5@medium`;中轉站 → `(yinli)deepseek-chat` | 既有測試 `test_model_label_*` 不改;新增 `labels` 參數化測試涵蓋三例 |
| 錯誤訊息 | 每個既有拋錯點的訊息文字不變,只換例外型別(子類仍是 `LLMServiceError`) | 既有 `assertRaises(LLMServiceError)` 與 `assertIn("...", str(ctx.exception))` 測試不改 |
| 重試時序 | `LLMClient` 維持 `MAX_RETRIES=3` / 固定 2 秒;`ClaudeCodeLLMClient` 維持外層 2 次 | 不換 helper(非目標) |
| 私有屬性 | `_formatted_model_name`、`_cli_path`、`_effort`、`_build_env` 保留一個版本 | `runtime_config_service` 遷到 `model_label` / `isinstance` 後仍能 fallback |
| `LLMClient._resolve_json_schema` / `_strip_markdown_fences` | 保留為轉呼 `schema_utils` 的 classmethod/staticmethod | `test_schema_is_flattened_before_sending` 等不改 |

### D11 連帶改動

- `runtime_config_service.py`:**P1 實作結果**——診斷區塊的觸發改為 `factory.is_cli_provider(provider)`(查註冊表 + `issubclass`,不硬比 provider 字串;client 為 None 時仍能觸發,這是 `isinstance(client, …)` 做不到的);屬性讀取採「公開屬性優先(`model_label` / `cli_path` / `effort`),私有屬性 fallback」,因此 `test_runtime_config_read.py` 的 `SimpleNamespace` 替身**一行未改**即通過(優於原計畫「改 7 處替身」)。claude 專屬的子探測(token 格式 / `auth status --json` / haiku 實打)仍留在服務內、未 hook 化——agy 的認證模型要等 A6 實測,P3 再抽 `auth_diagnostics()`
- `MODIFY_LLM_MODEL_NAME`:`.env.example` 註解補「provider 為 claude-code / antigravity 時請加入對應模型名」;gemini-native 免改
- `.gitignore`:`+ .antigravity_workdir/`、`+ logs/antigravity_audit/`
- `scripts/common/llm_label.py` 的 `if provider == "claude-code"` 改為查 `effort` 設定是否存在(D6 包裝已涵蓋)

### D12 影響範圍審計:Telegram bot、config API、前端

2026-09-06 全 repo grep(類名、私有屬性、`settings.LLM_*` 讀取點)的結論:

| 面 | 現況 | 本計畫的影響 | 要改嗎 |
|---|---|---|---|
| TG bot `callbacks_config.py:142-175`(`/setconfig` 寫入) | `setattr(settings, key, value)` 純記憶體覆寫;**只有** `AUDIO_EVALUATOR_PROVIDER` 觸發 singleton 重建;完全不認得 LLM 類名、工廠、`_formatted_model_name` | 零。它只認 `MODIFY_*` 的鍵名,本計畫不改任何鍵名。`LLM_MODEL_NAME` 改完後**下一次生成即生效**——因為所有生成都走 L2 每次 `create_llm_client()`、client 在 `__init__` 讀 `settings`(§1.3 圖 3) | **不改** |
| TG bot `commands.py:433`(`/setconfig` 說明文) | 文案列 `AUDIO_MODEL_NAME / LLM_MODEL_NAME / STT_LLM_MODEL_NAME` | 零 | 不改 |
| `app/api/config.py`(`GET /api/v1/config`, `/{key}`) | 唯讀,經 `RuntimeConfigService` | 只透過 D11 的 `model_label` 間接受影響 | 不改 |
| `app/core/dynamic_config.py` | 解析 `MODIFY_*` | 零 | 不改 |
| 前端 `frontend/src` | grep `claude-code` / `llm_model` / `effort` / `LLM_PROVIDER` 零命中 | 零 | 不改 |
| `scripts/local_anki/test_llm.py` | 獨立診斷腳本,自建 `AsyncOpenAI` 讀 `LLM_API_KEY` / `LLM_BASE_URL` | 零(不經工廠) | 不改 |

**附帶結果**:重構後 TG 可以直接切 provider——`.env` 加一行
`MODIFY_LLM_PROVIDER=['claude-code','gemini-native','antigravity']` 即可,bot 零改動,
因為 D9 的工廠在每次呼叫時讀 `settings.LLM_PROVIDER`。這不是本計畫的目標,但是它的自然結果。

**既存的殘留問題(本計畫不修,記錄以免誤判為回歸)**:TG 改 `LLM_*` 後 `app.state.llm_client`
不重建,`RuntimeConfigService.get_runtime_info()` 回報的 `llm_label` 會是舊值——它讀的是啟動期
singleton 的標籤(§1.3 圖 3 的 L1)。`runtime_config_service.py:30-36` 的 `REQUIRES_REBUILD_KEYS`
已把 `LLM_PROVIDER` / `LLM_MODEL_NAME` / `LLM_CLAUDE_CODE_EFFORT` 列為需重建,寫入側計畫
(`runtime_config_service_FEAT_2026-08-29.md` P0)預留的 REBUILD_REGISTRY 綁定的就是本計畫的
`create_llm_client()`;實作寫入側時順手把 `LLM_ANTIGRAVITY_EFFORT` 加進該集合即可。

## 4. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `app/infrastructure/llm/base.py` | **新增** `BaseLLMClient(abc.ABC)`:能力旗標、統一 `__init__`(model 解析 + effort 支援檢查)、`model_label` property、抽象 `generate_structured_data`、`_retry_transient()` |
| `app/infrastructure/llm/cli_base.py` | **新增** `HeadlessCliLLMClient(BaseLLMClient)`:D3 表左欄全部 + 四個抽象 hook + `CliRuntime` dataclass + `_RetryableCliError`(從 `claude_code_client.py` 搬入) |
| `app/infrastructure/llm/schema_utils.py` | **新增** `resolve_json_schema` / `strip_markdown_fences`(從 `client.py` 搬出) |
| `app/infrastructure/llm/labels.py` | **新增** `build_model_label()`(三份合一) |
| `app/infrastructure/llm/gemini_native_client.py` | **新增** `GeminiNativeLLMClient(BaseLLMClient)`(D7) |
| `app/infrastructure/llm/antigravity_client.py` | **新增** `AntigravityLLMClient(HeadlessCliLLMClient)`(D8;P0 後定案 hook 內容) |
| `app/infrastructure/llm/client.py` | 掛 `BaseLLMClient`;`__init__` 改 `super().__init__()` 後建 `AsyncOpenAI`;標籤改呼 `labels`;兩個工具方法改轉呼 `schema_utils`;四個拋錯點換子類型別(訊息不動) |
| `app/infrastructure/llm/claude_code_client.py` | 掛 `HeadlessCliLLMClient`;刪除搬入基底的部分(預估 -250 行);保留 `_build_command` / `_build_env` / `_extract_result`(合併原 `_raise_for_fatal_markers` + `_extract_envelope` + `_raise_for_error_envelope`)/ `_extra_cli_candidates` / 類別屬性 / token 格式驗證 / `auth_diagnostics()` |
| `app/infrastructure/llm/anthropic_client.py` | 掛 `BaseLLMClient`;建構子改收 `model` / `effort` 後仍拋「尚未實作」;`SUPPORTS_EFFORT = True`(依 §D11 指引預留);docstring 實作指引第 5 點補「例外用 D5 子類」 |
| `app/infrastructure/llm/factory.py` | 改為註冊表(D9);刪 `factory.py:100`;回傳型別 `BaseLLMClient` |
| `app/core/exceptions/infrastructure.py` | **新增** `LLMRetryExhaustedError` / `LLMQuotaError` / `LLMRefusalError`;`app/core/exceptions/__init__.py` 匯出 |
| `app/core/config.py` | **新增** `LLM_ANTIGRAVITY_CLI_PATH` / `_EFFORT` / `_WORKDIR` / `_TIMEOUT_SECONDS` / `_AUDIT_DIR`;`LLM_PROVIDER` 的 description 補列 `gemini-native` / `antigravity` |
| `app/core/dependencies.py:103` | 型別註記改 `BaseLLMClient` |
| `app/services/runtime_config_service.py` | D11:`_probe_cli` + `isinstance`;`model_label` |
| `scripts/common/llm_label.py` | 改為 `labels.build_model_label` 的薄包裝 |
| `app/core/dependencies.py:45,51` | 頂層 `LLMClient` import 與 `TYPE_CHECKING` 的 `ClaudeCodeLLMClient` import 改為單一 `BaseLLMClient` |
| `app/services/task_handlers/base.py:22` | `TYPE_CHECKING` 的 `LLMClient` 型別註記改 `BaseLLMClient` |
| `scripts/database/MySQL/JP_VerbPair/build_llm_index.py:76,165,227` | 型別註記 `BaseLLMClient`;`_formatted_model_name` → `model_label` |
| `scripts/database/MySQL/JP_VerbPair/build_llm_index_no_context.py:181,314,377` | 同上(首次 grep 漏列) |
| `analyze_pairs.py:23` | `LLMClient()` → `create_llm_client()`(2026-08-27 計畫漏網的第 9 處實例化點) |
| `.env.example` | 新增 `LLM_ANTIGRAVITY_*` 六欄位與註解(P3 實作多一個 `_ISOLATE_HOME`);`LLM_PROVIDER` 可選值補列 |
| `.gitignore` | `+ .antigravity_workdir/`、`+ logs/antigravity_audit/` |

### Frontend

無。

### 測試

- `tests/test_claude_code_client.py`:**一行不改**,40 項必須全綠——這是 G3 的驗收線,也是 `HeadlessCliLLMClient` 切法正確與否的裁判
- `tests/test_llm_base.py`(新):
  - 未實作 `generate_structured_data` 的子類 → `TypeError` at instantiation
  - `SUPPORTS_EFFORT=False` 的子類收 `effort` → `LLMServiceError`;`True` 者不拋
  - `model` 空 / 覆寫優先序
  - `_retry_transient`:瞬時錯誤 → 退避重試 → 成功;非瞬時 → 立即拋;超時 → `LLMServiceError`
- `tests/test_llm_labels.py`(新):參數化涵蓋 google / claude-code / 中轉站 / gemini-native / antigravity 五例,與 D10 表逐字比對
- `tests/test_llm_exceptions.py`(新):三個子類的 `error_code`;經 `fluencytides_error_handler` 後 `ErrorResponse.error_code` 正確;訊息含分級表識別字串
- `tests/test_gemini_native_client.py`(新,mock `google.genai`):happy path、五種 `finish_reason` 分流、429 → `LLMQuotaError`、5xx 瞬時重試、`safety_settings` 確實送出、`system_instruction` 確實送出、標籤格式
- `tests/test_antigravity_client.py`(新,mock subprocess,P0 後寫):比照 claude 的 `CommandConstructionTests` / `FailureTriageTests` / `RepairRetryTests`;特別驗證 `_build_stdin` 併入 system prompt、`--print-timeout` 與設定同步、`--disable-slash-commands` 必在
- `tests/test_claude_code_client.py::FactoryTests` 追加:`gemini-native` / `antigravity` 路由;所有 provider 的 `model` / `effort` 皆傳到建構子(對 anthropic 尤其重要,§1.3 第一項)
- `tests/test_jp_verb_reading_judgments.py`:**一行不改**必須綠——它 `mock.patch.object(ClaudeCodeLLMClient, "_resolve_cli_path")`(`:235,244`)並讀 `_formatted_model_name`(`:238-239`);前者靠 MRO 解析仍命中基底方法,後者靠 D10 的 alias
- `tests/test_runtime_config_read.py`:原計畫「必須改 7 處替身」,**P1 實作後一行未改**——D11 改以註冊表判斷 provider、屬性讀取保留私有 fallback,`SimpleNamespace` 替身照常通過(見 D11)

## 5. 實作順序

- **P0 實測**(可與 P1 平行;結果決定 D7 的 A9 與 D8 的 A1–A8):
  - Mac 上跑 A1–A7,把原始信封(stdout / stderr,成功與各失敗形態)貼進本文件 §D8 下方的「實測紀錄」小節
  - Windows 上跑 A8(`agy install` 後的路徑)
  - 本機以現有 google-genai 版本驗證 A9(dict schema 接受形式、`$ref` 是否需展平)
- **P1 基底重構,零功能變更** ✅ 完成(2026-09-06):`base.py`(BaseLLMClient + `_retry_transient`)/ `cli_base.py`(HeadlessCliLLMClient + CliRuntime + `_RetryableCliError`)/ `schema_utils.py` / `labels.py` / 三個例外子類;三個既有類掛上;工廠改註冊表(`resolve_client_class` / `is_cli_provider` / `providers_supporting_effort`);`dependencies` / `task_handlers/base` / `runtime_config_service` / `llm_label` / `build_llm_index`×2 / `analyze_pairs` 接線。`claude_code_client.py` 由 ~720 行減為 ~330 行。驗收:全套 266 項綠(含新增 `test_llm_base` 17 項、`test_llm_labels` 11 項、`test_llm_exceptions` 5 項);三個既有測試檔一行未改;ruff 零警告;以真實 `.env`(`LLM_PROVIDER=claude-code`)實例化,標籤 `(claude-code)opus-5@medium`、CLI 路徑與重構前一致。mypy 型別驗收於 2026-10-08 補上,見下方「mypy 驗收」。
  **順手發現並修掉的既存 bug**:`build_llm_index.py:76` 與 `build_llm_index_no_context.py:181` 的 `llm_client: LLMClient` 註記沒有對應 import、也無 `from __future__ import annotations`——2026-08-27 換工廠時把 `LLMClient` import 拿掉後,這兩支腳本 import 期即 `NameError`,自那天起就跑不起來。已改 `BaseLLMClient` 並補 import。
  **兩個 P1 才確定的實作約束**(已回填 D9 / D11):工廠須保留模組層 `LLMClient` 名稱與呼叫時 `getattr` 解析供既有測試攔截;診斷觸發用註冊表而非 `isinstance`(client 為 None 時仍要能判斷)。
  **審計 meta.json 欄位集合刻意不變**:實作時曾加 `provider` 欄,為守住 D10「審計三檔逐位元一致」已移除,P3 若要加,連同計畫一起改。
  **P1 code review 後的修正**(2026-09-06,八個角度 → 10 項 findings,全數處理;全套 274 項綠):
  - 工廠註冊列改帶靜態 `is_cli` / `supports_effort` 旗標,載入時與類別宣告核對、不一致即 `RuntimeError`。`is_cli_provider()` 與 `providers_supporting_effort()` 因此是純查表——原版 `is_cli_provider` 會 import provider 模組(連帶 jsonschema),依賴缺失時診斷端點會 500,正是它該「帶病回報」的情境。
  - effort 不支援的錯誤訊息**逐字恢復**層級化前 `factory.py:100` 的文字;anthropic stub 的 `SUPPORTS_EFFORT` 改 False(不能把建構即拋錯的 stub 列進「支援的 provider」誤導使用者),實作本體時再改 True。
  - `_retry_transient` 改用 `asyncio.timeout()` + `scope.expired()` 判斷逾時:Python ≥ 3.11 的 `asyncio.TimeoutError` 就是內建 `TimeoutError`,原版會把 operation 自己拋的 `TimeoutError` 誤當逾時而繞過 `is_transient`。
  - CLI 探測順序恢復原樣:Windows 只找 `~/.local/bin/<name>.exe`(原版多插了一個無副檔名候選在 PATH 之前,會撿到殘留 shim)。
  - effort 單一來源:子類改提供 `_runtime()` classmethod,`__init__` 與 `default_effort()` 讀同一個 `CliRuntime`,不再有 `getattr(settings, f"{PREFIX}_EFFORT", None)` 那條會靜默回 None 的反射路徑;`build_model_label` 對 effort 去空白小寫,腳本側標籤與後端一致(`LLM_CLAUDE_CODE_EFFORT=High` 兩邊都是 `@high`)。
  - 建構子檢查順序:`LLMClient` effort → 金鑰 → 端點 → 模型名(與層級化前一致);CLI 客戶端 effort → 憑證(`_check_credentials` hook)→ 模型名 → CLI 路徑——**與層級化前不完全相同**(原為 CLI 路徑先於模型名),2026-10-09 code review 更正,見下方。
  - `_probe_claude_code` 收 `provider`:通用部分(路徑 / 版本 / effort)適用所有 CLI provider,token / `auth status` / haiku 三項 claude 專屬,其他 CLI provider 回 `unsupported`——原版觸發泛化了、探測本體沒泛化,agy 接上後會跑 `agy auth status --json` 並把結果放進 `claude_code`。
  - 廢棄面移除:`SUPPORTS_SAFETY_SETTINGS`(無人讀)、`registered_providers()`、`workdir` / `audit_dir` property、`LLMClient._strip_markdown_fences` shim、`anthropic_client.ANTHROPIC_PROVIDER` 重複常數、`dependencies.py` 與 `task_handlers/base.py` 兩個無用 import;`normalize_provider` 收斂到 `labels.py` 一份,路由與標籤用同一條規則。
  **mypy 驗收**(2026-10-08,mypy 2.4.0;`mypy app/infrastructure/llm --ignore-missing-imports` 零錯誤,全套 274 項綠,ruff 零警告):初跑 9 個錯誤,以同指令跑 `main` 快照對照分類——
  - **P1 引入 3 個,已修**:`schema_utils.resolve_json_schema` 回傳型別寫成 `object`(遞迴要處理 list / 純量),兩個呼叫端拿不到 dict——改為公開函式收發 `dict[str, object]`、遞迴本體移到私有 `_inline_refs`,展開邏輯不變;基底 `_effort: str | None` 在 CLI 子類未收窄——`HeadlessCliLLMClient` 補宣告 `_effort: str`(與 `main` 上 `ClaudeCodeLLMClient` 一致)。`main` 上舊 `_resolve_json_schema` 簽名的 3 個錯誤隨抽出一併消失。
  - **`main` 既存 6 個,一併修**(CI 只跑 ruff E9/F821/F823、從未跑 mypy,故累積):`config.py` InterceptHandler 補 `level: str | int`、`frame: FrameType | None`;`client.py` debug log 的 `properties` 只取一次再判斷;`structured_format` 標註為 SDK 的 `ResponseFormatJSONSchema`;`**({"extra_body": …} if … else {})` 展開改為直接傳 `extra_body=…或 None`(SDK 預設即 None,請求內容相同);tool_calls 改讀 `message.tool_calls` 並以 `isinstance(…, ChatCompletionMessageFunctionToolCall)` 判斷。
  - **唯一有執行期影響的一項**:新版 OpenAI SDK 的 `tool_calls` 元素為 function / custom 聯集,custom 無 `.function`。原寫法遇到 custom 會在 `try` 外拋裸 `AttributeError`,不轉 `LLMServiceError`、不重試;現改為視同空內容,走既有重試,最終拋 `LLMRetryExhaustedError`。實測 SDK 對缺 `type` 欄位的 tool_call 仍解析為 function 型,中轉站既有路徑不受影響。
  - 既有測試未覆蓋 `LLMClient` 的請求路徑,另以 `httpx.MockTransport` 攔截實際請求驗證:`safety_settings` 開/關分別在/不在請求 body;function tool_call 取值正確;custom tool_call 呼叫 3 次後拋 `LLMRetryExhaustedError`(訊息「LLM API 在所有重試後仍回傳空內容。」)。
  **2026-10-08 複查**(逐項對照 §4 / §7 與程式碼):
  - **google 路徑離線比對**:同一個 `httpx.MockTransport`,分支與 `main` 快照各跑 9 情境(正常 / fence 包裹 / function tool_call / 先空後成功 / 連續空內容 / `PROHIBITED_CONTENT` / 壞 JSON / 429 / 500)× safety_settings 開關,共 18 次——請求 body、請求次數、回傳結果、標籤全部一致;`error_code` 依 G6 改為子類代碼(500 維持 `LLM_SERVICE_ERROR`)
  - **D5 與 G8 / §7 條文衝突**:拒答情境訊息由「LLM API 在所有重試後仍回傳空內容。」變為「LLM API 在所有重試後仍回傳空內容（安全拒答 PROHIBITED_CONTENT，finish_reason=PROHIBITED_CONTENT）。」。這是 D5 表格的要求(兩個識別字串皆含),三支生成腳本以 `or` 子字串比對,分級結果不變;G8 與 §7 已加註此例外
  - **實跑與 mypy 修正的先後**:google / claude-code 實跑與 TG `/setconfig` 由使用者實測通過,時間早於上方 mypy 修正。修正後的回歸確認:`client.py` 由上述 main 比對覆蓋;`schema_utils` / `cli_base` 由 `test_claude_code_client` 43 項覆蓋;`config.py` 的 logging 攔截實跑輸出與 `main` 相同
  - **anthropic 覆寫值傳遞補上測試保護**(2026-10-08):新增 `tests/test_llm_factory.py`(10 項;放新檔是因 `test_claude_code_client.py` 依 §7 必須一行不改)——逐列展開註冊表,驗證每個註冊 provider 與未註冊落空路徑的建構子都收到原樣的 `model` / `effort`(日後新增 provider 自動納入);另以不 mock 的真實類別驗證 `LLMClient` 確實採用模型覆寫、anthropic stub 訊息回顯兩個覆寫值。突變驗證:工廠改成丟掉 `effort` → 8 項失敗;只讓 anthropic 不傳覆寫(重現 §1.3 第一項)→ 恰好 anthropic 的 3 項失敗。全套 284 項綠
  - **mypy 進 `requirements.txt`**(`mypy>=2.4.0`,放在 Test & Static Checks 區;CI 尚未跑,列 §8)
  - **未註冊 provider 靜默落到 OpenAI 相容路徑**:`LLM_PROVIDER=gemini-native` / `antigravity` 目前不報錯,走 `LLMClient` 打 `LLM_BASE_URL`,標籤成為 `(antigravity)<LLM_MODEL_NAME>` 這類誤導值。與 `main` 行為相同(非本次引入),P2 / P3 註冊後自然解除;在那之前改設定不易察覺,列 §8
  - **與 §2 / §4 原文的差異**(P1 review 後刻意調整,上方已記錄理由,原文保留不改):G2 仍列 `SUPPORTS_SAFETY_SETTINGS`,實作已移除;§4 寫 anthropic `SUPPORTS_EFFORT = True`,實作為 `False`;§4 寫 `runtime_config_service` 用 `_probe_cli` + `isinstance`,實作改用註冊表
  - **2026-10-09 code review 修正**(重新 diff + review 得 8 項 findings;處理 4 項、略過 4 項):
    - **腳本側標籤行為變更(只記錄,不改程式)**:`scripts/common/llm_label.py` 改為經 `resolve_client_class` 取類別屬性後,非 CLI provider 搭配 `claude-*` 模型時不再去掉 `claude-` 前綴,且 provider 一律小寫——例:`yinli` + `claude-opus-5` 由 `(yinli)opus-5` 變 `(yinli)claude-opus-5`;`LLM_PROVIDER=Yinli` 由 `(Yinli)…` 變 `(yinli)…`。新值與後端 `model_label` 一致(舊腳本反而與後端不一致),但會改變失敗紀錄(`dedup_manager.record_failure` 的 `llm_model`)寫入的標籤,與歷史紀錄不同。成功紀錄一律取後端回應的 `llm_model`,不受影響
    - **CLI 建構子檢查順序(只改註解與文件)**:現行為 effort → 憑證 → 模型名 → CLI 路徑;層級化前是 effort → token → CLI 路徑 → 模型名。只有「模型名為空且找不到 CLI」同時成立時,最先看到的錯誤訊息不同。`cli_base.py` 註解與上方 code review 修正段已更正(原寫「恢復」不實)
    - **RECITATION 不再算安全拒答(改程式)**:`client.py` 的 `_REFUSAL_FINISH_REASONS` 移除 `RECITATION`(版權內容重複輸出而停止,不是安全拒答),改走 `LLMRetryExhaustedError`,免得 P2 拒答率比對失真。`test_llm_exceptions.py` 補 6 項(SAFETY / PROHIBITED_CONTENT / content_filter → `LLM_REFUSAL` 且訊息含兩個識別字串;RECITATION / stop / length → `LLM_RETRY_EXHAUSTED`);突變驗證:放回 RECITATION → 恰好 RECITATION 那項失敗
    - **CLI 逾時改回每次呼叫讀設定(改程式)**:P1 曾在建構時快取 `_timeout_seconds`,而 `/setconfig` 只改 settings、不重建 singleton,執行期調整的逾時會到重啟才生效。`_invoke_cli` 改讀 `self._runtime().timeout_seconds`,恢復層級化前行為。`test_llm_base.py` 補 `CliTimeoutTests`(建構後改設定,下一次呼叫即用新值);突變驗證:改回固定值 → 該項失敗
    - **略過的 4 項**:`LLMQuotaError` docstring 寫「訊息須含 Quota」但 OpenAI 相容路徑只含「429」(腳本比對「429 或 Quota」,現況正確);`runtime_config_service` 對非 claude 的 CLI provider 特例處理(P3 抽 `auth_diagnostics()` hook 時解決);`_retry_transient` 尚無正式呼叫端(P2 首用);`LLMClient` 建構時兩次 effort 檢查(為維持錯誤順序,無害)
    - 驗證:全套 291 項綠、ruff 零警告、mypy 零錯誤;`test_claude_code_client.py` 與 `main` 相同
  - 保留未動:`import shutil  # noqa` 在 `claude_code_client.py`(既有測試以 `ccc.shutil` 拼寫 patch;要拆得改測試,留給 §8)、`_formatted_model_name` alias 與 `_resolve_json_schema` shim(§8 排程移除)、`LLMRefusalError` 訊息帶兩個識別字串(D5 過渡期,§8 腳本改讀 `error_code` 後拿掉)。
- **P2 `GeminiNativeLLMClient`**:未知數最少(SDK 先例在 repo、白名單免改、金鑰現成),且立刻拿到 `safety_settings` 的實際價值。驗收:`LLM_PROVIDER=gemini-native` 跑 JP_VerbPair 一章,與同章 `google`(OpenAI 相容)路徑比對拒答率
  **實作完成**(2026-10-14,分支 `feat/gemini-native-llm-client`):
  - 改動:新增 `gemini_native_client.py`、`tests/test_gemini_native_client.py`(64 項);`factory.py` 加註冊列 `gemini-native`(`is_cli=False`、`supports_effort=False`);`config.py` 的 `LLM_PROVIDER` / `LLM_API_KEY` 說明;`.env.example` 補 gemini-native 說明與 TG 切換註解;`requirements.txt` 的 `google-genai` 下限提到 `>=2.10.0`、aiohttp 註解補「gemini-native 直接依賴」;`base.py` 的 `_retry_transient` 加 `describe_error` 參數(見下方第二輪)
  - A9 實測與定案見 §D7;D7「呼叫」「分流」兩列已改寫為實作版本
  - 真實 API 煙霧測試:經工廠建出 `GeminiNativeLLMClient`,以真實卡片 schema 打 `gemini-3.1-pro-preview`,標籤 `(gemini-native)gemini-3.1-pro-preview`、輸出通過 Pydantic 驗證;攔截 SDK 實際送出的請求 body,含 `safetySettings`(四類 `BLOCK_NONE`)、`systemInstruction`、展平的 `responseJsonSchema`
  - **code review 修正**(code / errors / tests / comments 四面向,全數處理或縮小範圍處理):
    - 正式環境 SDK 走 aiohttp,連線錯誤是 `aiohttp.ClientConnectionError` 系列,初版 `_is_transient` 只認 httpx 與內建 `ConnectionError`,等於連線錯誤零重試(審查者以假位址實測確認)。改為兩種傳輸層都列,並收窄 httpx 為連線 / 讀寫 / 逾時類,代理設定錯、協定不支援等確定性錯誤不重試
    - 非預期例外改 `logger.exception` 並在訊息帶型別名與 repr(`TimeoutError()` 的 str 是空字串);`genai.Client` 建構失敗也包成 `LLMServiceError`
    - 移除 `.text` 外的 `except ValueError: return ""`:2.10.0 無候選時 `.text` 回 None,那段防護沒有依據,反而會把 SDK 錯誤偽裝成空內容跳句
    - 拒答集合加 `JAILBREAK`;所有最終訊息帶 `finish_reason`;截斷改用與 `LLMClient` 一致的「非有效 JSON 格式」,不再借用跳句字串(腳本結果同樣是跳句,只是不再誤記成安全攔截)
    - API 錯誤訊息只取 `code status: message`,不嵌回應 JSON;400 / 401 / 403 / 404 附「請檢查 LLM_API_KEY / LLM_MODEL_NAME」等提示(不另開例外子類,腳本層中止列 §8)
    - 補測試:模型覆寫真的送進請求、安全中斷但帶完整合法 JSON 仍判拒答、單次逾時接線、`BLOCKED_REASON_UNSPECIFIED` 防護、無候選、最後一次決定子類的混合序列、拒答後遇 429、退避間隔、HTTP 層請求 body(走 SDK 真實的 wire 轉換與回應解析)
  - **第二輪複審修正**(errors / tests 兩面向重審,確認第一輪各項已解決後的新發現):
    - 網路層重試耗盡的訊息由基底 `_retry_transient` 產生,第一輪的修正沒涵蓋到:5xx 耗盡仍嵌整包回應 JSON、`TimeoutError()` 耗盡訊息仍是空白。基底加 `describe_error` 參數(預設「型別名: repr」),gemini-native 傳入只取 `code status: message` 的版本。基底只有這一個呼叫端,其他 provider 不受影響
    - `aiohttp.ClientConnectionError` 底下的代理錯誤、SSL / 憑證錯誤、憑證指紋不符是確定性錯誤,改為不重試
    - Gemini 對無效 / 過期金鑰回 400(不是 401),改從 details 的 `ErrorInfo.reason` 認出 `API_KEY_INVALID` / `API_KEY_EXPIRED`,提示指向 `LLM_API_KEY`
    - API 錯誤說明截斷 500 字(代理回 HTML 錯誤頁時 message 是整頁)、缺欄位不印 None;429 改記 WARNING;不可用回應的 log 補 Google 附的人讀原因(`block_reason_message` / `finish_message`)
    - 補測試:OTHER 不算拒答、aiohttp / httpx 其餘瞬時例外、aiohttp 確定性例外不重試、瞬時錯誤耗盡訊息帶型別名、5xx 耗盡不含 details、金鑰 400 提示、訊息截斷;逾時測試在回歸時 5 秒內明確失敗而非卡死;caplog 斷言限定本模組
  - 突變驗證:拿掉 aiohttp 瞬時判斷、拒答只在空內容時判、忽略模型覆寫、拿掉 UNSPECIFIED 防護、訊息改回 `str(APIError)`、拿掉 `describe_error`、拿掉確定性例外排除、拿掉金鑰 reason 判斷、拿掉單次逾時、拿掉截斷——各自恰好被對應測試抓到
  - 驗收指令(在 `backend/` 下):

    ```bash
    python -m pytest tests -q -p no:cacheprovider
    ```

    全套 357 項綠;ruff 零警告;`mypy app/infrastructure/llm --ignore-missing-imports` 零錯誤
  - **待做**:使用者以 `LLM_PROVIDER=gemini-native` 跑 JP_VerbPair 一章,與同章 `google` 路徑比對拒答率。比對時注意兩邊的拒答認定不完全相同(原生多認 `SPII` / `JAILBREAK`、帶文字的安全中斷也算拒答)
- **P3 `AntigravityLLMClient`**:依 P0 結果寫 hook 與測試。驗收:`LLM_PROVIDER=antigravity` 跑同一章;因 system prompt 併入(D8),與 claude-code 路徑做 20 張人工評閱比對
  **實作完成**(2026-10-14,分支 `feat/antigravity-llm-client`):
  - 新增 `antigravity_client.py`(`AntigravityLLMClient(HeadlessCliLLMClient)`)。初版**基底一行未改**——P1 的 hook 切法足以容納第二個 CLI,G3 的設計目標成立;review 後為暫時性失敗在基底加了退避(見下方 code review 修正),那是新行為、不是切法問題。實作內容逐項對應 §D8 實測紀錄:
    - `_build_command`:`--input-format stream-json --output-format stream-json --sandbox --disable-slash-commands --model --effort --print-timeout <T>s --json-schema <schema>`;`--print-timeout` 每次呼叫讀 `LLM_ANTIGRAVITY_TIMEOUT_SECONDS`
    - `_build_stdin`:一行 `{"event":"user","message":{"role":"user","content":"<system>…</system>\n\n<user prompt>"}}`;修復重試的回饋同樣經此併入
    - `_build_env`:剔除 `ANTIGRAVITY_*`、設 `AGY_CLI_DISABLE_AUTO_UPDATE=1`、(預設)`HOME` / `USERPROFILE` 指向工作目錄旁的 `<workdir>_home`——刻意不放進 workdir,因為 workdir 是 agy 的 workspace,模型可列出與寫入其中檔案
    - `_extract_result`:由後往前取 `event=result`;`status != SUCCESS` 時模型名錯 → 致命(提示用基底名)、額度 → `LLMQuotaError`、未登入 → 致命、其餘(含 503 資格檢查)→ 可重試;成功但有 `denied_actions` 或缺 `structured_output` → 可重試並附「不要呼叫工具」回饋;回傳 `structured_output` 序列化後的 JSON,交給基底的 `jsonschema` 複核
    - `MAX_RETRIES = 3`(比 claude 多一次,因 503 約四成)、`VALID_EFFORTS` 五檔、`_extra_cli_candidates` 加 `%LOCALAPPDATA%\agy\bin\agy.exe`
  - 設定:`LLM_ANTIGRAVITY_CLI_PATH` / `_EFFORT`(預設 high)/ `_WORKDIR` / `_TIMEOUT_SECONDS`(900)/ `_AUDIT_DIR`(`logs/antigravity_audit`)加上計畫外的 **`_ISOLATE_HOME`**(預設 true):Windows 實測憑證在 keyring、隔離不影響認證,但 macOS / Linux 未驗證,留一個關閉開關
  - 接線:工廠註冊 `antigravity`(`is_cli=True`、`supports_effort=True`);`REQUIRES_REBUILD_KEYS` 加 `LLM_ANTIGRAVITY_EFFORT`(§D12);`.gitignore` 加 `.antigravity_workdir/` 與 `.antigravity_workdir_home/`(審計目錄在既有的 `backend/logs/` 規則內);`.env.example` 新增第 11-B 區
  - `test_llm_base` 一處斷言更新:effort 不支援的錯誤訊息由「僅 claude-code provider 支援」變為「僅 claude-code, antigravity provider 支援」——支援清單取自註冊表,這正是 §1.3 第二項要的行為
  - 測試:`tests/test_antigravity_client.py` 初版 34 項、review 後 55 項、複審後 68 項(信封樣本依 P0 原始輸出結構簡化),涵蓋命令列旗標、stdin 格式與 system 區塊、`--print-timeout` 同步、schema 展平、HOME 隔離與開關、IDE 變數剔除、各失敗形態分流、工具被拒的修復回饋、schema 不符的修復重試、Windows 安裝路徑兜底、審計三檔、工廠路由、runtime info 的 CLI 區塊。突變驗證(改讀 `response`、拿掉 HOME 隔離 / IDE 剔除 / 自動更新關閉 / `--disable-slash-commands` / system 區塊、忽略 `denied_actions`、503 改致命)各自被對應測試抓到
  - 真實 agy 煙霧測試:經工廠建出客戶端、`gemini-3.8-flash` + `low`、真實 `ChildCardGenerationResult` schema → 第 1 次即通過 Pydantic 驗證,138 秒;workdir 無殘留、會話紀錄落在專用 home、審計已寫入
  - 驗收指令(在 `backend/` 下):

    ```bash
    python -m pytest tests -q -p no:cacheprovider
    ```

    全套 430 項綠(複審修正後);ruff 零警告;`mypy app/infrastructure/llm --ignore-missing-imports` 零錯誤;三個受保護測試檔與 `main` 相同
  - **code review 修正**(2026-10-14,code / errors / tests / comments 四面向;code-reviewer 無 ≥80 信心的問題,其餘全數處理):
    - **503 耗盡不再偽裝成「空內容」**:初版 503 三次全中時拋 `LLMRetryExhaustedError`(含跳句識別字串),腳本會記成「安全審查攔截」並永久跳句——基礎設施故障被算在內容頭上。基底 `_RetryableCliError` 加 `transient` 旗標與 `TRANSIENT_BACKOFF_SECONDS = (10, 30)`:暫時性失敗在下一次嘗試前退避,耗盡時若最後一次仍是暫時性,改拋「服務在 N 次嘗試後仍不可用」(複審後為新子類 `LLMServiceUnavailableError`,`error_code="LLM_SERVICE_UNAVAILABLE"`,不含跳句字串)。claude 目前沒有標為暫時性的失敗,行為不變(受保護測試照過)
    - **只以 `\n` 分行**:`str.splitlines()` 還會在 U+2028 / U+2029 / U+0085 切行,生成內容含這些字元時 result 行被切斷、誤判為致命錯誤而中斷批次
    - **錯誤分流改看 gRPC 狀態名**:先解析 `error` 欄位的 `<STATUS_NAME> (code N)`(`RESOURCE_EXHAUSTED` / code 429 → 額度;`UNAUTHENTICATED` / `PERMISSION_DENIED` → 致命),措辭標記只看 `error` 欄位,`error` 為空才退回 stderr;不再把裸「429」子字串當額度。模型選擇被拒的訊息附 CLI 原文與當下 effort(可能是模型與 effort 的組合不被接受);認證失敗提示 `LLM_ANTIGRAVITY_ISOLATE_HOME=false`;CLI 自身逾時(措辭未實測,只認明確提到 print timeout 的)→ 致命
    - **status 缺失或不在 {SUCCESS, ERROR} → 致命**(信封格式漂移時重試只會每句燒三次慢速呼叫);**有 `structured_output` 就採用**,`denied_actions` 只記警告(初版會連有效結果一起丟掉)
    - **沒有 result 事件分兩種**:一個事件都沒有(用法錯誤)→ 致命;有事件但沒有 result(CLI 中途結束)→ 暫時性重試;訊息附無法解析的行數與 stderr 尾端
    - `--print-timeout` 設為逾時設定減 30 秒(下限 1 秒,agy 把 0 解讀為不限),讓 CLI 先以結構化錯誤結束,不和 subprocess 逾時賽跑
    - 專用 home 改在建構時建立,建立失敗以專屬訊息拋出(不再被基底包成「無法啟動 CLI」);每次呼叫後檢查 workdir,有殘留檔案記警告
    - 診斷端點的 `--version` 探測改用 client 的 `_build_env()`,不會成為意外觸發 agy 自動升版的入口
    - 測試:新增 U+2028 內容、兩次 503 後第三次成功(鎖住總嘗試 3 次)、退避間隔、狀態名分流、stderr 不參與判斷、status 漂移、中途結束、denied 但有結果、非 dict 結果、workdir 殘留警告、隔離設定每次呼叫才讀等;`os.name` 不再全域竄改(原寫法在 Linux 上必壞),改替換模組參照;`test_llm_factory` 加由註冊表推導的檢查:支援 effort 的 CLI provider 其 effort 鍵必須在 `REQUIRES_REBUILD_KEYS`
    - 突變驗證:改回 `splitlines()`、503 不標暫時性、拿掉 status 檢查、denied 時丟棄結果、拿掉逾時餘裕、stderr 參與判斷、版本探測不帶環境、暫時性耗盡改回空內容、拿掉退避——各自被對應測試抓到
    - **複審修正**(errors / tests 兩面向重審,確認上列皆已解決後的新發現):
      - CLI 自身逾時改以 result 的 `duration_seconds` 判斷(已到 `--print-timeout` 即致命),不再只靠未實測的措辭——否則以 `DEADLINE_EXCEEDED` 回報時會被當暫時性錯誤重試三輪、約 44 分鐘
      - `--print-timeout` 餘裕改為「180 秒與逾時 20% 取小」:180 秒涵蓋實測最壞啟動開銷(agy 的計時起點未驗證),比例上限讓小設定值不會算出一兩秒就中止
      - 解析得到 gRPC 狀態名時只依狀態名與 code 分流,措辭只在沒有狀態名時才用(避免 `UNAVAILABLE … quota service` 被判成額度)
      - 新增 `LLMServiceUnavailableError`,讓 §8 的腳本修正能以 `error_code` 區分「服務不可用」與真正的致命錯誤,不必比對中文訊息
      - **還原 claude 路徑的 effort 錯誤訊息**:初次修正時把基底 `_validate_effort` 的訊息改了字,違反 D10「既有拋錯點訊息不變」;已還原原文,agy 的差異改寫在 docstring
      - 只有被截斷的 JSON 行(沒有完整事件)視為中途結束(暫時性),不再判成用法錯誤;退避表為空時不會 IndexError;基底的重試 log 區分「暫時性失敗」與「未通過驗證」並帶 provider 名
      - 測試:退避改以基底新增的 `_sleep()` 局部替換(不再全域竄改 `asyncio.sleep`);補暫時性 / 內容問題混合序列(最後一次決定例外、只在暫時性失敗後退避)、耗盡後不再等待、執行期建立 home 失敗、stderr 退回判斷的正向案例、狀態名優先、逾時依實耗判斷、CRLF 行尾、預設 home 路徑與 `.gitignore` 一致;print-timeout 測試改鎖不變式(0 < print-timeout < 逾時,且不低於逾時的 80%)
      - 突變驗證:措辭蓋過狀態名、拿掉實耗判斷、截斷行改致命、最後一次後仍退避、改回一般 `LLMServiceError`、改回固定餘裕——各自被對應測試抓到;修正後再以真實 agy 跑通一次(113 秒,第 1 次即通過)
    - 註解與文件:`cli_base` 模組說明改正(agy 不用 `-p`);`base._reject_unsupported_effort` 與 §7 不再宣稱 effort 訊息「逐字相同」;OpenAPI 欄位說明、`judge_verb_readings.py` 的 `--effort` 說明、`main.py` 註解、`jp_verb_reading_service` 的白名單提示改為涵蓋 antigravity;額度標記同樣未實測的事實寫進註解;`.env.example` 的模型清單改為「以 `agy models` 為準」、耗時改稱「整體耗時」、補 TG 白名單與 home 會累積的說明
  - **待做**:使用者以 `LLM_PROVIDER=antigravity` 跑一章;與 claude-code 做 20 張人工評閱比對(system prompt 併入 stdin 的品質影響)
- **P4 收尾**:文件回填(本文件狀態、`docs/bk/02_Backend_Architecture.md` 的 LLM 段、`.env.example`);F043 標記「fence 清理已收斂到 `schema_utils`」;移入 `archive/`
  **完成**(2026-10-14,併入 PR #22):
  - `docs/bk/02_Backend_Architecture.md`:目錄樹與總覽圖的 LLM 節點改為層級化後的結構;§5.3 依現行程式碼改寫(繼承樹、provider 對照表、共同契約、註冊表、錯誤子類、各 provider 的重試方式、建立時機)
  - `docs/bk/08_Refactor_Recommendations.md` F041 / F043:補「2026-10-14 對照現行程式碼」註記——**文字 LLM 的圍欄清理已收斂到 `schema_utils`**;但收尾時對照程式碼發現,2026-07 記為「已實施」的語音側收斂(`BaseAudioEvaluator` Template Method、四個評分器共用圍欄清理)與 F041 的 2/4/8 秒指數退避,**現行程式碼都不成立**(`BaseAudioEvaluator` 只有抽象方法、四個評分器各自內嵌圍欄清理;`LLMClient` 為固定 2 秒)。只如實標註、不回溯改寫歷史紀錄;語音側收斂與 `LLMClient` 換退避本來就在 §8
  - `.env.example` 已於 P2 / P3 各自補齊(第 10、11-B 區)
  - 本文件與 HTML 版移入 `docs/archive/`;實跑驗收三項移入 §8 第一項

## 6. 風險與未知

| 風險 | 應對 |
|---|---|
| `HeadlessCliLLMClient` 切法讓既有 claude 測試某項紅 | 那是切法錯,不是測試錯——調整 hook 邊界直到 40 項不改全綠;測試檔是 G3 的裁判,不得為了過而改 |
| agy 無 `--system-prompt`,併入後生成品質下降 | P3 以 20 張人工評閱比對;若明顯劣化,`_build_stdin` 改用結構化分隔(如 `<system>…</system>` 區塊)再測;仍不行則 agy 只用於 effort 敏感度低的任務,文件明寫 |
| agy 的 `--json-schema` 若非 harness 強制(A3),壞 JSON 率高 | `MAX_RETRIES` 提高 + 修復重試已是既有機制;P3 統計壞 JSON 率,超過 5% 則 agy 列為實驗性 provider |
| `~/.gemini/GEMINI.md` 被 print 模式讀入(A5) | 若無旗標可關:`_build_env` 覆寫 `HOME` / `USERPROFILE` 指向 workdir 內的空 home(claude 已有空 cwd 先例,同一思路);記為 agy 專屬 hook |
| google-genai 對 dict schema 的接受形式與預期不符(A9;2026-10-14 實測已排除,見 §D7) | 三條路依序:`response_schema` 吃 dict → `response_json_schema` → 轉 `types.Schema`;都不行則退回「schema 塞 system_instruction + `response_mime_type=json` + 本地 `jsonschema` 複核」(與 claude 三道防線同構) |
| 例外換型別後某個未知讀取點靠 `type(e) is LLMServiceError` | 子類仍是 `LLMServiceError`;grep 全 repo 無 `type(...) is` 比對;風險極低 |
| `_formatted_model_name` alias 移除時漏了讀取點 | 本計畫不移除,只加 `model_label`;移除列 §8 並附 grep 清單 |
| 兩個新 provider 同時開發造成 P1 基底介面反覆改動 | P1 完成並合併後再開 P2 / P3;P2 與 P3 各開獨立 PR |

## 7. 驗收標準

- [x] `grep -n '^class ' app/infrastructure/llm/*.py` 顯示五個具體類別皆有基底,無裸 `class X:`(2026-09-06 P1)
- [x] `tests/test_claude_code_client.py` 與 `tests/test_jp_verb_reading_judgments.py` 一行未改,全綠(P1)
- [x] `tests/test_runtime_config_read.py` **一行未改**,全綠(P1;優於原訂「只改替身」)
- [x] TG `/setconfig` 改 `LLM_MODEL_NAME` 後,下一次生成的 `llm_model` 回應即為新值(bot 程式碼未改)(使用者實測)
- [x] `mypy app/infrastructure/llm` 零錯誤(2026-10-08,見 §5 P1「mypy 驗收」);`create_llm_client()` 回傳型別為 `BaseLLMClient` ✅;ruff 零警告 ✅
- [x] `grep -rn '_formatted_model_name' app/ scripts/` 只剩 `base.py` 內的 alias 定義與 `runtime_config_service` 的 fallback 讀取(P1)
- [x] `grep -rn 'provider == "claude-code"' app/` 為零(P1;分流走註冊表 + 旗標)
- [x] `LLM_PROVIDER=google` 跑一張卡:標籤 `gemini-3.1-pro-preview`、錯誤訊息、審計行為與 main 分支逐位元一致(使用者實測;另以 MockTransport 對 `main` 離線比對 18 次一致;拒答訊息依 D5 例外)
- [x] `LLM_PROVIDER=claude-code` 跑一張卡:標籤 `(claude-code)opus-5@medium`、審計三檔與 main 分支逐位元一致(使用者實測)
- [ ] (移入 §8)`LLM_PROVIDER=gemini-native` 跑一章:`safety_settings` 在請求中(mock 或 SDK debug log 證明);標籤 `(gemini-native)<model>`;`MODIFY_LLM_MODEL_NAME` 未改即可用(2026-10-14:`safety_settings` 已由 HTTP 層測試與真實 API 請求攔截證明、標籤已驗;跑一章待使用者)
- [ ] (移入 §8)`LLM_PROVIDER=antigravity` 跑一章:標籤 `(antigravity)<model>@<effort>`;審計三檔落盤於 `logs/antigravity_audit/`;`--print-timeout` 值等於 `LLM_ANTIGRAVITY_TIMEOUT_SECONDS`(2026-10-14:標籤、審計三檔、`--print-timeout` 同步已由測試與真實煙霧測試證明;跑一章待使用者)
- [x] `create_llm_client(model="x", effort="high")` 在 `LLM_PROVIDER=anthropic` 下拋「尚未實作」且訊息可見兩個覆寫值(證明參數已傳入,不再靜默丟棄)(2026-10-08 實測;`tests/test_llm_factory.py` 守護)
- [x] `create_llm_client(effort="high")` 在 `LLM_PROVIDER=google` 下拋 `LLMServiceError`,訊息句型與層級化前相同(P1 時逐字相同:`effort 覆寫僅 claude-code provider 支援（目前 LLM_PROVIDER='google'）。`;P3 加入 antigravity 後支援清單隨註冊表變為 `claude-code, antigravity`,這正是 §1.3 第二項要的行為);gemini-native 同樣拒絕(P2 測試覆蓋)
- [x] 三個例外子類經 API 回傳的 `error_code` 分別為 `LLM_RETRY_EXHAUSTED` / `LLM_QUOTA` / `LLM_REFUSAL`,且 `message` 含既有識別字串;`generate_child_cards.py` 未改仍正確分級(2026-10-08:離線比對各情境拋出正確子類;`test_llm_exceptions` 驗全域 handler;三支生成腳本未改,比對字串仍在訊息中)
- [x] `RuntimeConfigService.get_runtime_info()` 在 claude-code 與 antigravity 下皆回 CLI 診斷區塊,在 gemini-native 下為 `None`(claude-code 部分已由 `test_runtime_config_read` 覆蓋;gemini-native 為 `None` 已由 P2 測試覆蓋;antigravity 回 CLI 區塊已由 P3 測試覆蓋)
- [x] 全套測試綠(含新增五個測試檔)(P1 的三個新測試檔 ✅,另加 `test_llm_factory`;全套 291 項綠;P2 加 `test_gemini_native_client` 64 項;P3 加 `test_antigravity_client` 68 項,全套 430 項綠)

## 8. 後續事項(不屬本計畫範圍)

- **實跑驗收(使用者)**:①`LLM_PROVIDER=gemini-native` 跑 JP_VerbPair 一章,與同章 `google` 比對拒答率(兩邊拒答認定不同:原生多認 `SPII` / `JAILBREAK`、帶文字的安全中斷也算拒答);②`LLM_PROVIDER=antigravity` 跑同一章(每次呼叫 8–163 秒);③ antigravity 與 claude-code 各 20 張人工評閱,判斷 system prompt 併入 stdin 的品質影響,若明顯劣化依 §6 風險表的對策處理。跑之前先手動打一次端點確認金鑰與模型名(見下一項)。結果回填本文件 §5 P2 / P3 的「待做」段落與 §7
- 未註冊的 `LLM_PROVIDER` 值不再靜默落到 OpenAI 相容 `LLMClient`:中轉站 provider 值目前靠這條落空路徑運作,需改為明確登記(例如註冊表加一列「OpenAI 相容」群組),再讓未知值啟動即報錯
- mypy 加入 CI(`requirements.txt` 已於 2026-10-08 加入;不進 CI 的話型別錯誤會再次累積)
- 生成腳本三處分級表改讀 `ErrorResponse.error_code`,之後移除訊息字串保證(D5 過渡期結束)
- `LLMClient` 換用 `_retry_transient()`(指數退避取代固定 2 秒);屬行為變更,需獨立驗證
- 移除 `_formatted_model_name` alias 與 `LLMClient` 上的兩個轉呼工具方法
- 生成腳本遇到確定性設定錯誤(金鑰無效、模型不存在、schema 被拒)或服務持續不可用(P3 的 `LLMServiceUnavailableError` / `LLM_SERVICE_UNAVAILABLE`)應中止或暫停,而不是逐句 `record_failure`;另外腳本以 `"429" in 回應文字` 判斷額度,錯誤訊息裡的 CLI 原文或 conversation_id 碰巧含 429 時會誤判,改讀 `error_code` 可一併解除:目前所有 `LLMServiceError` 都回 502,腳本對 ≥500 一律記失敗後跳過,設定錯誤會把整批句子累加 failure_count 直到永久跳過。P2 只在 gemini-native 的訊息裡加設定提示;`LLMClient` 有同樣問題,修法(例如設定錯誤改用 4xx 狀態碼或專屬 `error_code`)兩個客戶端與腳本一起改。在那之前,用 gemini-native 跑大批次前先手動打一次端點確認金鑰與模型名
- `jp_verb_reading_service` 帶覆寫時,把 `create_llm_client` 拋出的**任何** `LLMServiceError` 都轉成 `InvalidOverrideError`(422);伺服器環境問題(金鑰未設、SDK Client 建立失敗)因此被歸咎成使用者的覆寫不合法。應讓覆寫驗證失敗(effort 不支援、模型名為空)拋專屬子類,service 只轉那個
- `GeminiNativeLLMClient` 的 `thinking_config`(`LLM_GEMINI_NATIVE_THINKING_BUDGET`)——與 effort 是不同概念,先不混入 `SUPPORTS_EFFORT`
- `AnthropicLLMClient` 本體(§D11 指引 + D5 例外)
- CLI 診斷的 hook 化:`get_runtime_info()` 的區塊鍵仍叫 `claude_code`,antigravity 只回通用部分(路徑 / 版本 / effort),帳號與認證探測標 `unsupported`。agy 的認證在 keyring、無 `auth status` 類子命令,要做需另找探測方式;屆時把鍵改為中性名稱並抽 `auth_diagnostics()`
- agy 的容器 / headless 認證:binary 內可見 `JETSKI_OAUTH_TOKEN`、`AGY_ADC_AUTH`,未實測;目前只驗證過 Windows 桌機
- `LLM_ANTIGRAVITY_ISOLATE_HOME` 在 macOS / Linux 的行為未驗證(憑證若不在 keyring,隔離後會無法認證)
- 語音軸線的 `GeminiNativeAudioEvaluator` 改用 `BaseLLMClient._retry_transient()`(F043 的另一半)
- `RuntimeConfigService` 寫入側(`set_config`)——`REQUIRES_REBUILD_KEYS` 已含 `LLM_PROVIDER`,重建工廠即為本計畫的 `create_llm_client()`
