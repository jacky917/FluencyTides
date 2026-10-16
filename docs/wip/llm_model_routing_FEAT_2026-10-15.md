# FEAT — LLM 模型路由：每個用途各自指定模型，存在 DB、重啟後仍生效

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-15 |
| **性質** | 新增機能設計 + 實作工作項（含移除既有的兩種暫時性覆蓋） |
| **狀態** | 🚧 實作中（P1 完成） |
| **範圍** | `app/infrastructure/llm/`（端點設定、工廠依端點建立客戶端、新增路由模組）、`app/infrastructure/database/models.py`（新資料表）、`app/services/task_handlers/`（呼叫點宣告用途鍵並改走路由）、`app/services/jp_verb_reading_service.py`、`app/api/`（路由管理端點）、`app/bot/handlers/`（新增 `/models` 選單、`/setconfig` 移除 LLM 設定）、`app/services/runtime_config_service.py`（診斷）、`scripts/common/llm_label.py` 與 `scripts/fastapi_client/JP_Common/judge_verb_readings.py`、`.env.example` |
| **不動** | `generate_structured_data` 簽名與 `LLMGenerateResult`；各客戶端的生成邏輯（只改金鑰、網址的來源）；DB 沒有任何設定時的行為（全部照 `.env`，與現在逐位元一致）；語音評分的設定（`AUDIO_*`、`STT_LLM_MODEL_NAME`）與其 `/setconfig` |
| **PR / 進度** | P1 完成（分支 `feat/llm-model-routing`，PR 待開）；P2–P4 未開始 |
| **關聯文件** | `docs/archive/llm_provider_hierarchy_FEAT_2026-09-06.md`（工廠註冊表與 `BaseLLMClient` 的由來）、`docs/archive/runtime_config_service_FEAT_2026-08-29.md`（`/setconfig` 與診斷端點） |

---

## 1. 問題與動機

### 1.1 現況：模型是全域單一設定

所有文字生成共用 `.env` 的 `LLM_PROVIDER` / `LLM_MODEL_NAME`（CLI 類 provider 另有 `LLM_CLAUDE_CODE_EFFORT` / `LLM_ANTIGRAVITY_EFFORT`）。工廠 `create_llm_client()` 每次呼叫才讀設定，所以改設定後下一次生成即生效，但**一改就是全部一起改**。

目前呼叫 LLM 的地方（全部不帶參數，一律吃全域設定）：

| 呼叫點 | 用途 |
|---|---|
| `app/services/task_handlers/expression_handler.py:136` | 外語糾錯 |
| `app/services/task_handlers/jp_verb_pair_handler.py:183` | 自他動詞子卡 |
| `app/services/task_handlers/jp_core_verb_handler.py:242` | 核心動詞子卡 |
| `app/services/task_handlers/example/verb_pair_example_handler.py:101` | 範例 handler |
| `app/services/jp_verb_reading_service.py:136` | 讀音判斷（唯一帶覆寫的地方，見 1.2） |
| `app/main.py:118` | 啟動時建立的 `app.state.llm_client`，只給診斷端點與 CLI 探測用 |

不同任務其實需要不同模型：讀音判斷是簡單分類、要便宜；子卡生成要品質；之後的「情境實用句」追問要快、生成要準。全域單一設定做不到這種分工。

### 1.2 現有的兩種「暫時性覆蓋」都會在重啟後消失或只作用一次

1. **TG `/setconfig` 改 LLM 設定**：`callbacks_config.py` 以 `setattr(settings, key, value)` 寫入記憶體，重啟即回到 `.env`。主機目前開放的是 `MODIFY_LLM_MODEL_NAME`；`MODIFY_LLM_PROVIDER` 與兩個 effort 鍵在 `.env.example` 為註解狀態
2. **讀音判斷 API 的單次覆寫**：`POST /api/v1/jp/verb-readings/judge` 的請求可帶 `model` / `effort`（`app/schemas/llm/jp_verb_reading.py:33-34`），由 `judge_verb_readings.py --model / --effort` 使用；值須在 `MODIFY_LLM_MODEL_NAME` 白名單內，否則回 422（`InvalidOverrideError`）

兩者都不能「設定好就一直生效」，而且和本計畫的 DB 設定並存會產生三套來源，難以判斷某次生成實際用了什麼。

## 2. 目標與非目標

**目標**

- G1 每個 LLM 用途可在 DB 指定「端點 / 模型 / effort」，**重啟後仍生效**
- G2 解析順序固定為：**用途設定（DB）→ 全域設定（DB）→ `.env`**；DB 沒有任何設定時，行為與現在逐位元一致
- G3 用途鍵由寫程式的人在用到 LLM 的地方自由宣告（可含 `.`），一個 handler 可宣告多個、不用 LLM 的 handler 不必宣告；用途與 handler 脫鉤
- G4 新增用途鍵時不需要任何設定：沒有 DB 設定就走全域，並自動出現在管理選單
- G5 可在 `.env` 任意新增多家服務（多個中轉站、多把金鑰），每家一個唯一 ID；依用途選「哪一家的哪個模型」，標籤前綴即該 ID
- G6 TG 提供 `/models` 選單管理設定（限管理員）；後端提供對應的 API
- G7 移除兩種暫時性覆蓋：`/setconfig` 不再管 LLM 設定、讀音判斷 API 不再收 `model` / `effort`
- G8 診斷端點列出每個用途**實際生效**的設定與來源；腳本寫失敗紀錄時的模型標籤改向後端查詢

**非目標**

- 把端點的網址與金鑰存進 DB、由 TG 或 API 新增端點（理由見 D5「為什麼金鑰不放 DB」）
- 「情境實用句」功能本身（另立計畫；它會是本機制的第一個多用途使用者）
- 語音評分的設定與其 `/setconfig`
- 前端網頁的管理介面
- 多 worker 之間的快取同步（後端目前只跑一個 uvicorn worker）
- 本機直接建立 LLM 客戶端的 3 支腳本改讀伺服器 DB（見 D9）

## 3. 設計決策

### D1 解析順序：用途 → 全域 → `.env`

```text
CHILD_CARD_LLM.client()   # 鍵 "jp_verb_pair"
  ├─ DB 有 "jp_verb_pair"   → 用它（來源：route）
  ├─ DB 有 "*"（全域）       → 用它（來源：global）
  └─ 都沒有                  → .env 的 LLM_PROVIDER（端點）/ LLM_MODEL_NAME / 預設 effort（來源：env）
```

- 用途鍵**精確比對**，鍵名中的 `.` 不代表階層，`scenario.ask` 沒設定時不會去找 `scenario`，直接退回全域。理由：`/models` 選單會逐一列出每個用途與它目前生效的來源，規則越少越容易預期
- `.env` 仍是最底層的預設，只在部署時修改；「全域（DB）」讓你不登入主機也能全面換模型
- 每筆設定都是完整的一組（端點 + model + 可選的 effort），不做欄位層級的合併。避免「用途只設了 model、端點卻吃到全域」這種難以察覺的組合

### D2 用途鍵：在程式裡自由宣告，與 handler 脫鉤

用途鍵是**寫程式的人自己取的名字**，在用到 LLM 的模組裡宣告一次、呼叫時拿來取客戶端。它不綁 handler：

- 一個 handler 要幾個鍵就宣告幾個（例如追問與生成各一個）；不用 LLM 的 handler（例如目前的 `vocabulary_mining`）什麼都不宣告，完全不受影響
- 不是 handler 的服務（例如讀音判斷）用同一套方式
- 命名自由，可含 `.` 作分組（`scenario.ask`、`jp.verb_pair.child_card` 都行）；`.` 只是名字的一部分，**沒有**任何繼承或退回語意（見 D1 的精確比對）
- 限制只有：英數字與 `. _ -`、長度 ≤ 64、`*` 保留給全域。不合規或同名重複宣告，在 import 時直接拋錯，開發時就會發現

```python
# app/services/task_handlers/jp_verb_pair_handler.py
from app.infrastructure.llm.routing import llm_route

CHILD_CARD_LLM = llm_route("jp_verb_pair", "自他動詞子卡生成")

class JpVerbPairHandler(BaseHandler):
    async def execute_create(self, ...):
        llm = await CHILD_CARD_LLM.client()      # 取代 create_llm_client()
        ...
```

```python
# 未來的情境實用句：一個 handler 兩個鍵
ASK_LLM = llm_route("scenario.ask", "情境追問")
GENERATE_LLM = llm_route("scenario.generate", "實用句生成")
```

- `llm_route()` 在模組 import 時登記到路由模組的清單；`list_route_keys()` 就是這份清單。`/models` 選單與 API 的合法鍵都從這裡來，**新增一個鍵就自動出現**，不必另外維護
- 以模組層級的物件呼叫，而不是在呼叫點寫字串 `client_for("...")`：鍵名只出現一次，打錯字在 import 時（重複／不合規）或靜態檢查時（變數名不存在）就會發現
- 已登記的模組在後端啟動時都會被 import（handler 由註冊表載入、服務由路由載入），所以清單在啟動完成時是完整的
- 本計畫為現有呼叫點取的鍵：

| 鍵 | 位置 | 說明 |
|---|---|---|
| `expression_correction` | `expression_handler.py` | 外語糾錯 |
| `jp_verb_pair` | `jp_verb_pair_handler.py` | 自他動詞子卡 |
| `jp_core_verb` | `jp_core_verb_handler.py` | 核心動詞子卡 |
| `verb_pair_example` | `example/verb_pair_example_handler.py` | 範例 handler |
| `jp_verb_reading` | `jp_verb_reading_service.py` | 讀音判斷 |

### D3 資料表

#### 用途設定表 `llm_routes`

放在主資料庫（`app/infrastructure/database/models.py`），遵守該檔開頭的 MySQL 相容規則：單一自增主鍵、字串欄位一律指定 `max_length`、時間欄位用 `sa_column` + `DateTime(timezone=True)` + `server_default=func.now()`。啟動時的 `create_all` 會自動建出新表；另補一份 Alembic 遷移，保持與既有表一致。

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | 整數，自增主鍵 | 依 MySQL 相容規則；程式一律以 `route_key` 查詢 |
| `route_key` | 字串（64），**唯一**、建索引 | 見 D2；`*` 為全域 |
| `endpoint` | 字串（32），必填 | 端點 ID（D5） |
| `model` | 字串（128），必填 | 模型名（CLI 類為基底名） |
| `effort` | 字串（16），可空 | 只有該模型在白名單中**不帶** effort 時為空；帶 effort 的模型一律存明確的值，沒有「留空＝預設」 |
| `updated_by` | 字串（64），必填 | `tg:<user id>` 或 `api` |
| `created_at` | 時間 | 建立時間 |
| `updated_at` | 時間 | 最後修改時間（`onupdate=func.now()`） |

```python
class LLMRouteSetting(SQLModel, table=True):
    __tablename__ = "llm_routes"

    id: int | None = Field(default=None, primary_key=True)
    route_key: str = Field(max_length=64, unique=True, index=True)
    endpoint: str = Field(max_length=32)
    model: str = Field(max_length=128)
    effort: str | None = Field(default=None, max_length=16)
    updated_by: str = Field(max_length=64)
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now()),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    )
```

- 寫入用「有就更新、沒有就新增」（以 `route_key` 查詢後更新，不依賴資料庫特有的 upsert 語法）
- 刪除一筆＝「恢復預設」（回到下一層）
- **不留歷史紀錄表**：只有一位管理員、設定很少變動，`updated_by` / `updated_at` 加上每次寫入記一行 INFO log 就足夠追查；日後真的需要再加

#### 分工：`.env` 決定能選什麼，DB 決定目前選了什麼

| 放哪裡 | 決定什麼 | 何時改、怎麼改 |
|---|---|---|
| `.env` | **能選什麼**：有哪些端點（網址、金鑰），每個端點有哪些模型、每個模型有哪些 effort（白名單，D5） | 部署時改，改完重建容器 |
| DB `llm_routes` | **目前選了什麼**：每個用途與全域 `*` 用白名單裡的哪一組「端點 + 模型 + effort」 | 隨時以 TG `/models` 改，下一次呼叫即生效，重啟後仍在 |
| 程式碼 | 有哪些用途（D2 的宣告）、各客戶端認得哪些 effort 值 | 改程式 |

白名單取決於部署環境（有哪幾家的金鑰、容器裡裝了哪些 CLI），和金鑰、CLI 路徑一樣屬於部署設定，所以跟著端點放 `.env`，不另建資料表。

**DB 的值必須在白名單內**：寫入時檢查（D6），讀取時也逐次比對。白名單改了、某筆設定的「端點 + 模型 + effort」已不在其中（端點被拿掉、模型被拿掉、或該模型不再提供那個 effort），該筆**不使用、退回下一層**（用途 → 全域 → `.env`），記 WARNING，`/models` 與診斷端點標示「已不在白名單」。端點、模型、effort 三者一律適用這一條規則；DB 裡存的值因此永遠是「看到的就是實際在用的」。

### D4 路由模組與快取

新增 `app/infrastructure/llm/routing.py`：

```python
def llm_route(key: str, description: str) -> LLMRoute: ...   # 宣告並登記；LLMRoute.client() 取客戶端
async def resolve(route_key: str) -> ResolvedRoute: ...   # endpoint / model / effort / label / source
async def list_effective() -> list[ResolvedRoute]: ...     # 診斷與 /models 用
```

- 設定讀出後放程序內快取；任何寫入或刪除都清空快取。只有一個 worker，不必處理跨程序同步
- 客戶端仍然每次呼叫才建立（與現在相同）。設定變更因此在下一次呼叫就生效，正在進行中的生成不受影響
- 讀不到 DB（例如資料庫異常）時退回 `.env` 並記 WARNING，不讓生成整個失敗

### D5 端點：每一家一個唯一 ID

**端點**＝「一家可呼叫的服務」：一個中轉站、一把 Google 金鑰、一個本機 CLI。每個端點有唯一 ID，DB 只記 ID；網址、金鑰、白名單（可選的模型與 effort）都在 `.env`。選模型＝選「端點 ID + 模型 + effort」，卡片標籤的 `(前綴)` 就是端點 ID，所以看標籤就知道是哪一家。

#### 端點從哪來

| 來源 | ID | 說明 |
|---|---|---|
| 自訂端點（任意多個） | 自取，例如 `yinli`、`or2`、`google` | `.env` 的 `LLM_ENDPOINTS__<ID>__*`，見下 |
| 內建 CLI 端點 | `claude-code`、`antigravity` | 固定 ID，各一個；設定沿用既有的 `LLM_CLAUDE_CODE_*` / `LLM_ANTIGRAVITY_*` 與本機登入 |
| 舊式單組設定（相容用） | `.env` 的 `LLM_PROVIDER` 值 | 見「相容」 |

`anthropic`（仍是佔位）不提供。

#### 自訂端點的寫法

```text
# 中轉站（OpenAI 相容）
LLM_ENDPOINTS__YINLI__TYPE=openai
LLM_ENDPOINTS__YINLI__BASE_URL=https://api.yinli.example/v1
LLM_ENDPOINTS__YINLI__API_KEY=sk-...
LLM_ENDPOINTS__YINLI__MODELS=["gemini-3.5-flash", "gpt-..."]

# Google 原生 SDK（不需網址）
LLM_ENDPOINTS__GEMINI__TYPE=gemini-native
LLM_ENDPOINTS__GEMINI__API_KEY=AIza...
LLM_ENDPOINTS__GEMINI__MODELS=["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"]
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| `TYPE` | 否，預設 `openai` | `openai`（OpenAI 相容，走 `LLMClient`）或 `gemini-native`（走 `GeminiNativeLLMClient`） |
| `BASE_URL` | `openai` 類必填 | `gemini-native` 類不使用 |
| `API_KEY` | 是 | |
| `MODELS` | 是 | 白名單，格式見下；**JSON 格式（雙引號）**；空清單＝`/models` 不列出此端點 |
| `SAFETY_SETTINGS` | 否 | 是否送 `safety_settings`。預設沿用現行規則：ID 為 `google` / `openai` 時不送，其餘送 |

- 以 pydantic-settings 的巢狀分隔符（`env_nested_delimiter="__"`）解析成 `LLM_ENDPOINTS: dict[str, LLMEndpointSettings]`（`app/core/config.py`）。已在本機以 pydantic-settings 2.14.2 驗證：`.env` 檔與環境變數都讀得到、兩者會合併、ID 自動轉小寫
- **原樣收下、載入時才解析**：`LLMEndpointSettings` 的 `MODELS` / `SAFETY_SETTINGS` 型別為 `Any`、`extra="allow"`，從 `.env` 來的都是原始字串，`Settings()` 不會因為某個端點寫錯而拋錯（實作時發現：若宣告成 `tuple[str, ...]` / `bool`，`MODELS` 不是 JSON 或 `SAFETY_SETTINGS=maybe` 會讓整個後端起不來）。解析、欄位名拼錯的提醒都在 `endpoints.py` 載入時進行；`LLM_CLAUDE_CODE_MODELS` / `LLM_ANTIGRAVITY_MODELS` 同理
- **ID 規則**：小寫英數字與 `_`（環境變數名稱不能含 `-`）、不可含連續的 `__`（巢狀分隔符會把 ID 切開）、長度 ≤ 32、不得與內建 ID 相同。不合規的端點跳過並記 ERROR（不讓整個後端起不來）
- **何時檢查**：後端啟動時（lifespan）載入一次並記一行摘要「LLM 端點已載入：…」，設定錯誤因此出現在啟動 log；之後每次建立客戶端都會重新載入，同一則錯誤只記一次
- **ID 唯一**：字典鍵本身唯一；同一 ID 在 `.env` 檔與環境變數各寫一份時，環境變數優先（pydantic-settings 的既定順序）
- **標籤前綴**：沿用現行規則，ID 為 `google` / `openai` 時不加前綴（與現在的標籤一致），其餘加 `(ID)`
- 一家服務想用兩把金鑰（例如分帳號計費）就定義兩個 ID，彼此獨立

CLI 端點沒有網址與金鑰，只另設白名單：

```text
LLM_CLAUDE_CODE_MODELS=["<模型>@low|medium|high|xhigh|max", "<模型>"]
LLM_ANTIGRAVITY_MODELS=["<基底名，見 .env.example 第 11-B 區>@low|high"]
```

（實作時依各 CLI 實測可用的名稱與 effort 填入。）

#### 白名單格式：`模型@effort`

白名單的每一項就是卡片標籤的 `model@effort` 寫法：

| 寫法 | 意思 |
|---|---|
| `"deepseek/deepseek-v4"` | 此模型不使用 effort |
| `"opus@high"` | 此模型只允許 `high` |
| `"opus@low\|medium\|high"` | 此模型允許三個 effort，展開為 `opus@low`、`opus@medium`、`opus@high` |

- 以**最後一個** `@` 切開模型名與 effort（OpenRouter 有 `deepseek-r1:free` 這類帶 `:` 的模型名，所以不用 `:`）
- 同一模型寫兩項時合併其 effort
- 一行就能看出某端點允許的所有組合，比巢狀 JSON 物件好讀好改

載入時逐項檢查，不合格的**那一項**跳過並記 ERROR（指出端點 ID 與該項），其餘照常載入：

- effort 值必須是該客戶端認得的值（目前 CLI 類為 `low` / `medium` / `high` / `xhigh` / `max`）
- 客戶端不支援 effort 的端點類型（目前為 `openai`、`gemini-native`）不可帶 `@effort`

#### 相容：舊式單組設定自動變成一個端點

現有部署只有 `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`。啟動時若 `LLM_ENDPOINTS` 裡**沒有**與 `LLM_PROVIDER` 同 ID 的端點，就用這組舊值合成一個：

| `LLM_PROVIDER` | 合成的端點 |
|---|---|
| `google` / `openai` / 中轉站名 | `openai` 類，ID＝該值，用 `LLM_BASE_URL` / `LLM_API_KEY` |
| `gemini-native` | `gemini-native` 類，ID＝`gemini-native`，用 `LLM_API_KEY` |
| `claude-code` / `antigravity` | 直接對應內建端點 |
| 留空 | 同 `google`，但只供 `.env` 那一層使用，不在 `/models` 列出 |

所以**現有 `.env` 一行都不用改**；想加第二家時才開始寫 `LLM_ENDPOINTS__*`。`LLM_ENDPOINTS` 有同 ID 的端點時以它為準，舊的 `LLM_BASE_URL` / `LLM_API_KEY` 不再使用並記 WARNING——工廠在**不指定端點**時也會走它（`create_llm_client()` 偵測到 `LLM_PROVIDER` 指向 `LLM_ENDPOINTS` 的端點即改走端點路徑），所以 .env 那一層與 DB 選它時用的是同一組網址與金鑰。該端點寫錯被跳過時不以舊設定頂替，工廠明確報「端點不存在」。

「.env 那一層」的預設組合併進白名單時，若白名單把同一模型寫成**不帶** effort，則尊重白名單、不改寫（只記 WARNING），以免 DB 中 effort 為空的設定被判為不合格。

`.env` 那一層（D1 最底層）＝ 端點 `LLM_PROVIDER` + 模型 `LLM_MODEL_NAME` + 該端點的 `.env` 預設 effort（CLI 類為 `LLM_CLAUDE_CODE_EFFORT` / `LLM_ANTIGRAVITY_EFFORT`，其餘無）。這一組**自動加進白名單**，所以現有 `.env` 不必另寫白名單，最底層也永遠合法，不會出現「最後一層也失效」的情況。

#### 工廠與客戶端

- 新增 `get_endpoint(id) -> Endpoint`（類型、網址、金鑰、白名單、標籤前綴、是否送 safety_settings），**所有讀端點設定的地方都經過它**。日後若改成 DB 管理端點，只改這個函式
- `create_llm_client(*, endpoint=None, model=None, effort=None)`：`endpoint` 為 None 時用 `LLM_PROVIDER`（現行行為）；依端點類型選客戶端類別
- `BaseLLMClient` 不再讀 `settings.LLM_PROVIDER`（`base.py:76`、`base.py:272`），改由工廠傳入端點；標籤前綴取自端點
- `LLMClient` 不再讀 `settings.LLM_API_KEY` / `LLM_BASE_URL`（`client.py:122-130`），改用端點的值；`safety_settings` 改看端點的 `SAFETY_SETTINGS`（`client.py:133`）
- `GeminiNativeLLMClient` 不再讀 `settings.LLM_API_KEY`（`gemini_native_client.py:238-246`），改用端點的金鑰
- `effort` 的來源一律是解析結果：DB 那兩層存的就是白名單內的明確值；`.env` 那一層用 `LLM_*_EFFORT`。為 None 時（白名單中不帶 effort 的模型）客戶端**不傳** effort。「不傳」以 `OMIT_EFFORT`（空字串）表示，與 None（沿用 `.env` 預設）區分；CLI 客戶端收到時命令列省略 `--effort`，不支援 effort 的客戶端視同 None。2026-10-15 實測：claude-code（2.1.292，haiku）與 antigravity（gemini-3.8-flash）省略 `--effort` 都正常完成結構化輸出（agy 回報有思考 token，即走 CLI 自己的預設）

#### 端點從 `.env` 被拿掉時

即 D3「DB 的值必須在白名單內」的一種情況：指向它的設定退回下一層，記 WARNING，`/models` 與診斷端點標示出來。不讓生成整個失敗；實際用的模型仍會正確寫進標籤。

#### 為什麼金鑰不放 DB

- SQLite 是明文，會跟著資料卷與備份複製；加密的話主金鑰仍得放 `.env`
- 用 TG 輸入金鑰會留在 Telegram 聊天紀錄
- 後端 API 目前未啟用認證（`API_SECRET_KEY` 留空，只靠 Cloudflare Access 擋外網），區網內任何人都能呼叫；若端點可經 API 新增或修改，就能把生成請求導到自己的網址
- 新增一家中轉站很少發生，改 `.env` 加重建容器約一分鐘（見 D11），不值得多做一套端點管理介面

以後若常換中轉站、要給不能登入主機的人管理、或做網頁管理介面，再把端點改存 DB（屆時先補 API 認證）；`get_endpoint()` 是唯一要改的地方。

### D6 寫入與驗證

兩個寫入口共用同一個服務函式：

- **API**：`GET /api/v1/llm-routes`（列出所有用途與生效設定）、`PUT /api/v1/llm-routes/{route_key}`、`DELETE /api/v1/llm-routes/{route_key}`。掛在既有的 `verify_api_key` 之下（目前金鑰留空即放行）；對外由 Cloudflare Access 保護
- **TG**：`/models`（限 `TG_ADMIN_CHAT_ID`），見 D7

寫入前的檢查：

1. `route_key` 在 `list_route_keys()` 或為 `*`
2. `(endpoint, model, effort)` 在白名單內（D5）；模型不帶 effort 時 `effort` 必須為空，帶 effort 時必須給值
3. **試建一次客戶端**：CLI 是否找得到、金鑰是否設定，都在存檔當下擋下，不拖到生成時才失敗。模型名本身是否真的存在仍要實際呼叫才知道，這裡不打 API

白名單取代原本混在一起的 `MODIFY_LLM_MODEL_NAME`（以及未啟用的 `MODIFY_LLM_PROVIDER` / `MODIFY_LLM_*_EFFORT`），改為每個端點各自一份（D5 的 `MODELS` 與 CLI 的 `LLM_<CLI>_MODELS`）。

### D7 TG `/models` 選單

```text
/models
  └─ 用途清單（每列顯示「用途：模型@來源」，可翻頁；第一列是「* 全域」）
       └─ 選一個用途
            ├─ [設定] → 選端點 → 選模型 → 選 effort → 試建 → 儲存
            └─ [恢復預設] → 刪除該筆，顯示退回後生效的設定
```

分層選擇，每一層只列白名單允許的選項：

| 層 | 列出 |
|---|---|
| 端點 | 白名單非空的端點 |
| 模型 | 該端點白名單裡的模型 |
| effort | 該模型白名單裡的 effort；只有一個時自動選上，模型不帶 effort 時跳過這一層 |

- **不使用狀態機**：每個按鈕的 callback data 自帶目前選到哪一步，例如 `lr:m:<用途序號>:<端點序號>:<模型序號>`，以序號代替名稱，確保不超過 Telegram 的 64 位元組上限。這樣沒有「狀態殘留」「舊按鈕拉回舊流程」的問題（TG 狀態機盤點的已知坑），也不會和其他流程搶輸入
- 每一步都有「返回」與「取消」
- 儲存成功後回覆新生效的設定與標籤，例如 `jp_verb_reading：(yinli)gemini-3.5-flash（來源：route）`

### D8 移除暫時性覆蓋

| 項目 | 改動 |
|---|---|
| `/setconfig` 的 LLM 設定 | `dynamic_config` 與 `/setconfig` 一律不處理 `LLM_` 開頭的鍵（即使 `.env` 還留著 `MODIFY_LLM_*` 也忽略並記 WARNING）；`REQUIRES_REBUILD_KEYS` 拿掉 LLM 相關鍵；`.env.example` 移除 `MODIFY_LLM_*` 並指向 `/models` |
| 讀音判斷 API | 請求 schema 拿掉 `model` / `effort`；`jp_verb_reading_service` 改用宣告的 `jp_verb_reading` 鍵；移除 `InvalidOverrideError` 與 422 分支；回應的 `llm_model` 保留 |
| `judge_verb_readings.py` | 拿掉 `--model` / `--effort`；說明改為「要換模型請用 `/models` 設定 `jp_verb_reading`」 |
| 測試 | `test_jp_verb_reading_judgments.py` 與覆寫相關的案例（約 16 處）改為驗證路由；`test_runtime_config_read.py` 的白名單案例依新規則調整 |

**代價**：讀音判斷「這次用強一點的模型重判一下」這類一次性實驗，改為先在 `/models` 改 `jp_verb_reading`、跑完再恢復預設。

### D9 診斷、標籤與腳本

- **診斷端點**：`GET /api/v1/config` 的 `runtime` 新增 `llm_routes`：每個用途的端點 / model / effort / 標籤 / 來源（route / global / env，含「已不在白名單、已退回」）；另列 `llm_endpoints`：每個端點的 ID、類型、網址、白名單（**不含金鑰**）
- **啟動時的全域客戶端**：`app.state.llm_client` 改為依「全域（DB）→ `.env`」建立，寫入 `*` 時重建（比照 `/setconfig` 重建語音評分器的做法），讓診斷端點的 CLI 探測與實際的全域設定一致
- **腳本的失敗紀錄標籤**：`scripts/common/llm_label.py` 目前以本機設定推算標籤，有了 DB 設定後會與伺服器實際使用的不一致。改為啟動時向後端查詢該用途的標籤（`GET /api/v1/llm-routes`，`query_backend_model.py` 已有查詢後端的骨架）；查不到時退回本機推算並印出警告
- **本機直接建立客戶端的 3 支腳本**（`build_llm_index.py`、`build_llm_index_no_context.py`、`import_cards_with_llm.py`）：在本機執行、讀本機 `.env` 與本機 DB，伺服器的設定對它們無效。本計畫維持現狀並在腳本說明中寫明；改為向後端查詢列 §8

### D10 相容性

| 面向 | 保證 |
|---|---|
| DB 沒有任何設定 | 所有生成照 `.env`，標籤、請求內容、錯誤訊息與現在逐位元一致 |
| 現有 `.env` | 不必修改：舊式單組設定自動合成為一個端點（D5「相容」） |
| 端點覆寫 | 端點等於 `.env` 的 `LLM_PROVIDER` 時，客戶端行為與標籤與現在相同 |
| 白名單縮減（端點、模型或 effort 被拿掉） | 指向它的設定退回下一層並標示，不中斷生成 |
| 新增用途鍵 | 不需任何設定，宣告後自動出現在 `/models`，生成走全域 |
| 不用 LLM 的 handler | 不宣告任何鍵，完全不受影響 |
| 用途鍵改名或移除 | 舊鍵的設定變成孤兒：`/models` 與 API 標示「已無對應用途」，可刪除，不影響生成 |

### D11 操作：新增一家中轉站

1. 在主機的 `/DATA/AppData/FluencyTides/backend/.env` 加一組端點（ID 自取，例如 `newrelay`）：

```text
LLM_ENDPOINTS__NEWRELAY__BASE_URL=https://api.newrelay.example/v1
LLM_ENDPOINTS__NEWRELAY__API_KEY=sk-...
LLM_ENDPOINTS__NEWRELAY__MODELS=["gemini-3.5-flash", "gpt-..."]
```

2. 重建後端容器（`env_file` 只在建立容器時讀取，`restart` 讀不到新值；只改 `.env` 不需重新 build 映像）：

```bash
cd ~/FluencyTides && docker compose -f backend/docker-compose.yml up -d --force-recreate
```

3. TG `/models` → 選用途 → 設定 → 端點清單出現 `newrelay` → 選模型 →（有 effort 時）選 effort → 儲存。之後該用途產生的卡片標籤為 `(newrelay)<模型>`

原有的端點與 DB 設定都不受影響。拿掉一家則反過來：刪 `.env` 那一組並重建容器，指向它的設定自動退回下一層（D3），再到 `/models` 改設或恢復預設。新增或拿掉某家的單一模型、effort 也是同樣流程：改那一行 `MODELS` 並重建容器。

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `app/infrastructure/llm/endpoints.py` | **新增**：`Endpoint` / `ModelOption`、`get_endpoint()` / `list_endpoints()` / `log_endpoint_summary()`、ID 檢查、原始設定解析、舊式單組設定的合成 |
| `app/infrastructure/llm/factory.py` | `create_llm_client` 加 `endpoint` 參數，依端點類型選客戶端 |
| `app/infrastructure/llm/claude_code_client.py`、`antigravity_client.py` | 收到 `OMIT_EFFORT` 時省略 `--effort`；`VALID_EFFORTS` 改取自 `endpoints.EFFORT_VALUES` |
| `app/infrastructure/llm/base.py` | 接收端點，不再讀 `settings.LLM_PROVIDER` |
| `app/infrastructure/llm/client.py` | 網址、金鑰、`safety_settings` 改取自端點 |
| `app/infrastructure/llm/gemini_native_client.py` | 金鑰改取自端點 |
| `app/infrastructure/llm/routing.py` | **新增**：`llm_route()` 宣告與登記、`resolve` / `list_effective` / `list_route_keys`、快取 |
| `app/infrastructure/database/models.py` | **新增** `LLMRouteSetting` 資料表（`llm_routes`）；`alembic/versions/` 補遷移 |
| 4 個 handler 與 `jp_verb_reading_service.py` | 模組層級宣告用途鍵（D2 表），呼叫點改 `await XXX_LLM.client()` |
| `app/services/llm_route_service.py` | **新增**：寫入驗證與試建（API 與 TG 共用） |
| `app/api/llm_routes.py` | **新增**：GET / PUT / DELETE |
| `app/bot/handlers/models_menu.py` | **新增**：`/models` 選單；`dispatcher.py` 註冊 |
| `app/bot/handlers/callbacks_config.py`、`app/core/dynamic_config.py` | 不再處理 `LLM_` 鍵 |
| `app/services/runtime_config_service.py` | `llm_routes` 診斷；`REQUIRES_REBUILD_KEYS` 調整；全域客戶端重建 |
| `app/main.py` | 啟動時依「全域（DB）→ `.env`」建立 `app.state.llm_client` |
| `app/schemas/llm/jp_verb_reading.py` | 拿掉 `model` / `effort` |
| `app/core/config.py`、`.env.example` | `LLM_ENDPOINTS`（`env_nested_delimiter="__"`）、`LLM_CLAUDE_CODE_MODELS` / `LLM_ANTIGRAVITY_MODELS`、白名單解析；`.env.example` 加端點範例與 D11 步驟；移除 `MODIFY_LLM_*` 說明 |
| `scripts/common/llm_label.py` | 改向後端查詢標籤 |
| `scripts/fastapi_client/JP_Common/judge_verb_readings.py` | 拿掉 `--model` / `--effort` |
| `app/bot/handlers/commands.py` | `/help` 補 `/models` 說明 |

測試：

- `tests/test_llm_routing.py`（新）：三層解析順序與來源、精確比對、快取在寫入後失效、DB 異常時退回 `.env`
- `tests/test_llm_endpoints.py`（新）：巢狀變數解析、ID 規則與內建 ID 衝突、舊式單組設定的四種合成、同 ID 時 `LLM_ENDPOINTS` 優先、`SAFETY_SETTINGS` 預設值、標籤前綴；白名單 `模型@effort` 解析（以最後一個 `@` 切、`|` 展開、同模型合併、模型名含 `:`）、不合格項目逐項跳過、不支援 effort 的端點類型帶 `@` 被拒、`.env` 那一層自動加入白名單
- `tests/test_llm_factory.py`：端點傳到建構子；端點等於 `.env` 的 `LLM_PROVIDER` 時標籤與請求 body 不變；兩個 `openai` 類端點各自用自己的網址與金鑰
- `tests/test_llm_route_service.py`（新）：寫入檢查、孤兒鍵；白名單縮減（端點／模型／effort 各一）時該筆退回下一層並標示
- `tests/test_models_menu.py`（新）：callback data 不超過 64 位元組、非管理員被擋、恢復預設、每層只列白名單選項、單一 effort 自動選上、無 effort 跳過
- 讀音判斷與 runtime 診斷的既有測試依 D8 / D9 調整

## 5. 實作順序

- **P1 路由骨架（DB 為空時零行為變更）**：端點設定與相容合成、工廠依端點建立客戶端、資料表與遷移、`routing.py`、呼叫點宣告用途鍵並改走路由；**先實測** claude-code / antigravity 不傳 effort 時的行為。驗收：DB 為空、`.env` 不改時全套測試照舊通過，四種 `LLM_PROVIDER` 的啟動與標籤與 main 相同
- **P2 管理介面**：寫入服務與檢查、API、TG `/models`。驗收：從 TG 設定某用途後重啟後端，設定仍生效
- **P3 移除暫時性覆蓋與診斷**：D8、D9。驗收：`/setconfig` 不再出現 LLM 設定；讀音判斷請求帶 `model` 會被忽略或拒絕（依實作時的 schema 嚴格度決定）；診斷端點列出每個用途的來源
- **P4 文件**：架構文件的 LLM 段、`.env.example`、本計畫歸檔

## 6. 風險與未知

| 風險 | 應對 |
|---|---|
| 端點改到基底類別與兩個客戶端，標籤、`safety_settings` 或金鑰來源意外改變 | 端點等於 `.env` 的 `LLM_PROVIDER` 時逐位元比對標籤與請求 body（沿用 LLM 層級化計畫的 MockTransport 比對法） |
| `.env` 寫錯（ID 含 `-`、`MODELS` 用單引號、缺 `BASE_URL`、effort 拼錯、欄位名拼錯） | 設定欄位原樣收下，`Settings()` 不會拋錯；啟動時載入一次，跳過有問題的端點或白名單項目並記 ERROR 指出是哪一個，拼錯的欄位名記 WARNING；啟動摘要與診斷端點列出實際載入的端點 |
| 白名單寫了模型實際不支援的 effort | 存檔時的試建擋不到（不打 API），要到生成才報錯；與模型名打錯相同，靠填寫時對照各家文件 |
| antigravity 沒有「不傳 effort」的模式 | ✅ 已排除：P1 實測省略 `--effort` 可正常完成（見 D5「工廠與客戶端」） |
| 金鑰外洩到診斷端點或 log | 設定層 `LLMEndpointSettings.api_key` 與載入後的 `Endpoint.api_key` 都是 `SecretStr`（後者另設 `repr=False`）；診斷與 log 只列 ID、類型、網址 |
| `/models` 選單的 callback data 超過 64 位元組 | 一律用序號；測試鎖住上限 |
| 改了設定卻以為沒生效（其實還在快取或是本機腳本） | 寫入後清快取；回覆新生效設定；本機腳本的限制寫進說明 |
| 讀音判斷失去一次性覆寫，實驗變麻煩 | D8 已列代價；若常用，日後可為腳本加「暫時指定用途鍵」的參數（例如在同一用途下另設 `jp_verb_reading.experiment`） |
| API 寫入端點在區網內不驗證 | 與現有 API 相同的取捨（見 README「API 認證」）；對外由 Cloudflare Access 保護 |
| `gemini-native` 每次建立客戶端約 0.9 秒 | 與現在相同（本計畫不改變建立頻率）；客戶端快取列 §8 |

## 7. 驗收標準

- [ ] DB 沒有任何設定時，全套測試通過，四種 `LLM_PROVIDER` 的啟動、標籤與 main 相同
- [ ] 解析順序：用途有設定用用途、否則用全域、否則用 `.env`，且診斷端點顯示的來源正確
- [ ] 一個模組宣告兩個用途鍵時，兩個鍵可分別指定不同端點與模型
- [ ] `.env` 定義兩家 `openai` 類端點與一個 `gemini-native` 端點，三者可同時被不同用途使用，各自用自己的網址與金鑰，標籤前綴為各自的 ID
- [ ] 現有 `.env`（只有舊式單組設定）不改即可運作
- [ ] 依 D11 步驟新增一家中轉站，不改程式、不重新 build 映像即可在 `/models` 選到
- [ ] 新宣告一個鍵（測試用）不做任何設定，自動出現在 `/models`、生成走全域；不合規或重複的鍵在 import 時拋錯
- [ ] 從 TG `/models` 設定後重啟後端，設定仍生效
- [ ] 寫入時各項檢查擋得住對應的錯誤（不在白名單、該帶 effort 卻沒帶、不該帶卻帶）；試建失敗不寫入
- [ ] 白名單拿掉某個端點、模型或 effort 並重建容器後，指向它的設定退回下一層，`/models` 與診斷端點標示出來
- [ ] `/models` 每一層只列白名單允許的選項
- [ ] `/setconfig` 不再出現任何 `LLM_` 設定
- [ ] 讀音判斷改走 `jp_verb_reading` 用途；腳本不再有 `--model` / `--effort`
- [ ] 生成腳本的失敗紀錄標籤與後端實際使用的一致
- [ ] ruff、mypy（`app/infrastructure/llm`）零錯誤

## 8. 後續事項（不屬本計畫範圍）

- 端點改存 DB、由 TG 或網頁新增（前提：先補 API 認證；只需改 `get_endpoint()`）
- 本機直接建立客戶端的 3 支腳本改向後端查詢用途設定
- 客戶端快取（避免 `gemini-native` 每次建立的約 0.9 秒）
- 前端網頁的路由管理介面
- 「情境實用句」功能（第一個宣告多個用途鍵的 handler：`scenario.ask` / `scenario.generate`）
- **既有問題（盤點時發現）**：`vocabulary_mining_handler.execute_create` 目前沒有呼叫 LLM（程式註解寫明「尚未包含呼叫 LLM 的邏輯」），TG 的單字卡流程實際只建立空卡；TG 狀態機文件（`docs/bk/16_TG_Bot_State_Machine.html`）§4.3 的描述需一併更正

## 9. 進度紀錄

### P1 路由骨架（2026-10-15 完成）

- **端點**：`app/infrastructure/llm/endpoints.py`（`Endpoint` / `ModelOption`、`load_endpoints` / `get_endpoint` / `list_endpoints`、白名單解析、舊式單組設定合成）；`Settings` 新增 `LLM_ENDPOINTS`（`env_nested_delimiter="__"`）、`LLM_CLAUDE_CODE_MODELS`、`LLM_ANTIGRAVITY_MODELS`
- **客戶端**：`BaseLLMClient` 與四個客戶端的建構子加 `endpoint` 參數，並以 `ENDPOINT_TYPES` 檢查端點類型（避免例如 Gemini 客戶端拿中轉站金鑰）；未指定端點時完全照舊讀 `LLM_PROVIDER` / `LLM_API_KEY` / `LLM_BASE_URL`。新增 `OMIT_EFFORT`（Enum 成員，任何字串包括 `""` 都不會等於它），CLI 客戶端收到時省略 `--effort`
- **工廠**：`create_llm_client(endpoint=...)` 依端點類型選類別並傳入端點；不指定端點且 `LLM_PROVIDER` 指向 `LLM_ENDPOINTS` 的端點時也走端點路徑；其餘情況（含現有 `.env`）路徑未變
- **資料表**：`LLMRouteSetting`（`llm_routes`）＋ Alembic `a01287171364`（暫存 SQLite 上驗證 upgrade / downgrade / `alembic check` 無差異）
- **路由**：`app/infrastructure/llm/routing.py`（`llm_route()` 宣告、`resolve` / `client_for`、白名單逐次比對、程序內快取與 `invalidate_cache()`）。只有資料庫錯誤（`SQLAlchemyError` / `OSError`）退回 `.env`，故障開始記 WARNING 附 traceback、恢復記 INFO；程式錯誤照常拋出。DB 層建立客戶端失敗時不退回下一層，記 ERROR 補路由脈絡後原樣拋出
- **啟動**：lifespan 呼叫 `log_endpoint_summary()`，端點設定錯誤出現在啟動 log
- **呼叫點**：D2 表的五個鍵已宣告並改走路由。讀音判斷帶 `model` / `effort` 覆寫時暫時沿用舊行為（P3 移除）
- **測試**：新增 `test_llm_endpoints.py`（71）、`test_llm_routing.py`（53，含記憶體 SQLite 讀表與缺表、DB 選擇到真實客戶端的端到端）、`test_llm_factory.py` 追加 23 個端點案例（含傳輸層驗證 safety_settings、註冊表逐列收得到端點）、`test_jp_verb_reading_judgments.py` 追加 5 個服務層案例；`conftest.py` 預設不讓路由讀真實 DB、忽略開發機 .env 的自訂端點；全套 582 passed
- **審查後修正（2026-10-15）**：多面向審查找出的問題已修——`OMIT_EFFORT` 由空字串改為 Enum（原本 API 的 `effort: ""` 會從 422 變成被接受）、端點設定改為原樣收下（原本 `MODELS` 寫錯會讓後端起不來）、.env 那一層與 DB 層對同一端點 ID 用同一組憑證、DB 故障只攔資料庫錯誤並依狀態轉換記 log、啟動時載入端點、拼錯欄位與被忽略欄位會提醒、客戶端檢查端點類型、型別加上建構時檢查（`Endpoint` / `ModelOption` / `RouteChoice` / `LLMRoute`）、`Endpoint.api_key` 改 `SecretStr`。複審後再修：`LLM_ENDPOINTS` 的值與頂層也原樣收下（`LLM_ENDPOINTS__FOO=bar`、`LLM_ENDPOINTS=` 留空這類寫錯都不再讓後端起不來）、只攔 `DBAPIError` / 連線池逾時 / `OSError`（ORM 設定錯誤照常拋出）、解析結果標 `db_unavailable`、「端點不存在」的錯誤直接附上跳過原因、啟動摘要列出被跳過的端點、DB 層建立失敗以 `add_note` 把路由脈絡附在例外上
- **驗收比對**：以 main 的 worktree 與本分支跑同一支腳本，六種 `LLM_PROVIDER`（google、中轉站名、留空、gemini-native、claude-code、antigravity）由 `create_llm_client()` 建出的客戶端，類別、標籤、網址、金鑰、safety 判斷、CLI 命令列逐項相同；經路由（DB 為空）建出的標籤亦相同
- **部署修正（2026-10-16 發現）**：主機 `.env` 的 `DATABASE_URL=./fluencytides.db` 解析成容器內的 `/app/fluencytides.db`，不在掛載的 `/app/data` 底下，每次 `deploy_local.sh`（`--force-recreate`）都會連同容器可寫層一起被清空——既有的卡片關聯資料一直如此，DB 存的用途設定也會一部署就消失。已在 `backend/docker-compose.yml` 的 `environment` 固定 `DATABASE_URL=sqlite+aiosqlite:////app/data/fluencytides.db`（優先於 `env_file`，本機不受影響），README 與 `.env.example` 補上說明
- **尚未做**：`.env.example` 的端點範例與說明（P4）；寫入端與管理介面（P2）；移除暫時性覆蓋與診斷（P3）

### 部署注意：第一次部署本分支前先搬資料

改了資料庫位置後，新容器會讀主機 `data/` 底下的資料庫（目前是空的）。要保留現有的卡片關聯資料，**在執行 `deploy_local.sh` 之前**先把舊容器裡的資料庫複製出來：

```bash
docker cp fluencytides-backend:/app/fluencytides.db /DATA/AppData/FluencyTides/backend/data/fluencytides.db
```

確認檔案在 `data/` 後再部署；新表 `llm_routes` 會在啟動時由 `create_all` 自動補建。之後的部署不必再做這一步。
