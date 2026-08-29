# FEAT — 執行期設定服務:模型讀取/設置 API 與 TG 共用核心

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-29 |
| **性質** | 新增機能設計 + 實作工作項 |
| **範圍** | 新增 `app/services/runtime_config_service.py`、新增 `app/api/config.py`(REST router)、`app/bot/handlers/callbacks_config.py`(改薄)、`app/main.py`(掛 router)、對應單元測試 |
| **不動** | `app/core/dynamic_config.py`(白名單解析,原樣共用)、`MODIFY_*` 的 .env 慣例與語意、TG 的 UI/權限模型(`TG_ADMIN_CHAT_ID`)、設定的「記憶體級、重啟還原」語意 |
| **狀態** | 📝 未實作 |
| **PR / 進度** | [#14](https://github.com/jacky917/FluencyTides/pull/14)(與容器 claude-code 支援同 PR,計畫書分立) |
| **關聯文件** | `docs/wip/claude_cli_in_container_FEAT_2026-08-29.md`(同 PR 的姊妹計畫)、`docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md` |

---

## 1. 問題與動機

需求:後端要能**讀取**與**設置**目前使用的 LLM 模型(以及既有白名單內的其他設定),
且不只 TG 一個入口——REST API 也要能操作(例如給腳本或未來前端用)。

2026-08-29 調查現有 TG 邏輯的共用性,結果:

1. **白名單層可直接共用**:`app/core/dynamic_config.get_modifiable_configs()` 讀
   `MODIFY_*` 前綴環境變數,決定可改鍵與選項,純函式、零框架依賴。
2. **套用層與 TG UI 耦合**:「驗證 → `setattr(settings, ...)` → 特定 key 重建
   singleton → 失敗回滾」這段真正的業務邏輯內嵌在
   `app/bot/handlers/callbacks_config.py:115-194` 的 callback 裡,與權限檢查、
   `message.edit_text` 混雜,無法被 REST API 重用。
3. **挖到現有 bug(S011 同族缺口,callbacks_config.py:158 註解自己預言過)**:
   `LLMClient` 與 `ClaudeCodeLLMClient` 都在 `__init__` 快取
   `settings.LLM_MODEL_NAME`(`client.py:102`、`claude_code_client.py:138`),
   而 LLM client 是 lifespan 建立的 singleton(`app.state.llm_client`)。
   rebuild 特例目前**只有** `AUDIO_EVALUATOR_PROVIDER` 一條——TG 把
   `LLM_MODEL_NAME` 改掉後介面回報「✅ 成功」,但 singleton 不重建,
   **下一張卡照舊模型生成**。本案順帶根治。

## 2. 目標與非目標

**目標**
- G1 抽出框架無關的 `RuntimeConfigService`:列出可改設定(含**當前值**)、
  驗證並套用新值、按註冊表重建受影響的 singleton、失敗回滾。
- G2 新 REST API(獨立 router,與 TG 分開):
  - `GET /api/v1/config` — 白名單鍵 + 可選值 + 當前值(「讀取模型」的載體)
  - `PUT /api/v1/config/{key}` — 設值(body: `{"value": "..."}`)
- G3 TG callback 改為呼叫同一個 service(行為不變、程式碼變薄),
  `AUDIO_EVALUATOR_PROVIDER` 的 rebuild+回滾邏輯搬進 service 註冊表。
- G4 修復 S011 缺口:`LLM_MODEL_NAME` / `LLM_PROVIDER` / `LLM_CLAUDE_CODE_EFFORT`
  變更時重建 `app.state.llm_client`,失敗回滾(與 evaluator 同語意)。

**非目標**
- 不做設定持久化(維持記憶體級、重啟退回 .env;持久化是獨立議題)。
- 不擴充 `MODIFY_*` 白名單內容(那是 .env 的事;但 `.env.example` 會補
  `MODIFY_LLM_PROVIDER` 的示例註解供啟用)。
- 不做 API 端的細粒度權限(沿用現有整站防護,見 D4)。
- 不動 TG 的鍵盤互動流程與文案。

## 3. 設計決策

- **D1 Service 形態**:`app/services/runtime_config_service.py`,方法
  `list_configs() -> list[ConfigEntry]`(key/options/current_value)與
  `async set_config(key, value, app) -> SetResult`。回傳結構化結果
  (成功/未在白名單/選項非法/rebuild 失敗已回滾),由呼叫端(TG/API)
  自行轉成 UI 文案或 HTTP 狀態碼——service 不碰 aiogram 也不碰 FastAPI 例外。
- **D2 rebuild 註冊表**:`{設定鍵: async rebuild(app)}` 字典:
  - `AUDIO_EVALUATOR_PROVIDER` → 重建 `app.state.audio_evaluator`(搬現有邏輯)
  - `LLM_MODEL_NAME`、`LLM_PROVIDER`、`LLM_CLAUDE_CODE_EFFORT` →
    `app.state.llm_client = create_llm_client()`
  失敗時 `setattr` 回舊值再拋結構化錯誤(先改值→再 rebuild→失敗回滾,
  與現有 evaluator 語意一致)。多鍵共用同一 rebuild 函式,天然去重。
- **D3 當前值讀取**:`getattr(settings, key)`;白名單鍵目前全是模型名/力度類,
  無機密值。防禦性起見,service 對值做 `str()` 並在白名單以外一律拒讀
  (API 不暴露任意 settings)。
- **D4 API 權限**:沿用整站現行防護(Cloudflare Access header + 內網部署),
  不另設 admin token——與其他破壞性端點(生成/刪除)同一信任邊界。
  路由掛在 `/api/v1/config`,與 TG 完全分離。
- **D5 併發語意**:`set_config` 全程在事件圈內、rebuild 為 await 單點;
  與進行中請求的競態(舊 client 正在生成、中途被換掉)不在本案處理——
  singleton 替換是原子賦值,舊請求持有舊實例跑完,新請求拿新實例,可接受。

## 3.5 接口定義

### REST API(`app/api/config.py`,prefix `/api/v1/config`)

#### `GET /api/v1/config` — 列出可改設定與當前值

回應 `200`:

```json
{
  "configs": [
    {
      "key": "LLM_MODEL_NAME",
      "current_value": "claude-opus-5",
      "options": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"],
      "requires_rebuild": true
    },
    {
      "key": "AUDIO_EVALUATOR_PROVIDER",
      "current_value": "stt_llm",
      "options": null,
      "requires_rebuild": true
    }
  ]
}
```

- `options: null` = 白名單允許但不限選項(`MODIFY_X=` 留空的情形)。
- `requires_rebuild` = 該鍵在 rebuild 註冊表內(改值會重建 singleton),
  純資訊性欄位,方便呼叫端提示「切換需數秒」。
- 白名單為空時回 `{"configs": []}`,不視為錯誤。
- 回應另含頂層唯讀欄位 `"llm_label"`(如 `"(claude-code)opus-5@medium"`;
  `app.state.llm_client` 為 None 時為 `null`):取自活的 client 實例算好的
  `_formatted_model_name`,供呼叫端**直接顯示**,不必自行以
  provider/model/effort 重新推導。

> **⚠️ current_value 不是 DB 標籤**:`current_value` 是 settings 原值
> (`claude-opus-5`),與寫入 `generated_sentences_log.llm_model` 的標籤
> (`(claude-code)opus-5@medium`)差了三道加工。**禁止**呼叫端拿
> `current_value` 自行加工後寫 DB——那是 2026-08-28 錯標 190 筆的同型錯誤
> (呼叫端自行推導標籤,規則分岔即錯標)。寫 DB 的標籤一律取
> **生成 API 回應中的 `llm_model`**(後端 client 的單一事實來源);
> 需要顯示用標籤則取本 API 的 `llm_label`。

#### `GET /api/v1/config/{key}` — 讀取單一設定

回應 `200`:

```json
{
  "key": "LLM_MODEL_NAME",
  "current_value": "claude-opus-5",
  "options": ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"],
  "requires_rebuild": true
}
```

錯誤:`404`(key 不在白名單——**白名單外一律 404,不區分「存在但不可讀」**,
避免探測 settings 鍵名)。

#### `PUT /api/v1/config/{key}` — 設置新值

請求 body:

```json
{ "value": "gemini-3.5-flash" }
```

回應 `200`(成功,含新舊值與是否觸發了重建):

```json
{
  "key": "LLM_MODEL_NAME",
  "old_value": "claude-opus-5",
  "new_value": "gemini-3.5-flash",
  "rebuilt": ["llm_client"]
}
```

錯誤(統一 `{"detail": "..."}` 形狀,沿用全站 ErrorResponse 慣例):

| 狀態碼 | 情境 | detail 範例 |
|---|---|---|
| `404` | key 不在白名單 | `設定 'FOO' 不允許透過此介面存取` |
| `422` | value 不在 options 內 / body 缺 value / value 非字串 | `無效的選項 'x'，可用：[...]` |
| `500` | rebuild 失敗(settings 已回滾) | `LLM 客戶端重建失敗，設定已回滾為 'claude-opus-5'：<原因>` |

語意備註:
- 冪等:PUT 相同值直接回 200(`old_value == new_value`,`rebuilt: []`,
  **跳過 rebuild**——避免無意義的 singleton 重建)。
- 一次一鍵:不提供批次 PUT(TG 也是逐鍵操作;需要時另案)。
- 所有變更為記憶體級,重啟退回 `.env` 值(回應不另標註,文件與
  `.env.example` 說明)。

### Service 層(`app/services/runtime_config_service.py`)

```python
@dataclass(frozen=True)
class ConfigEntry:
    key: str
    current_value: str
    options: list[str] | None       # None = 不限選項
    requires_rebuild: bool

@dataclass(frozen=True)
class SetOutcome:
    status: Literal["ok", "not_allowed", "invalid_option", "rebuild_failed"]
    key: str
    old_value: str | None = None    # ok / rebuild_failed 時有值
    new_value: str | None = None    # ok 時有值
    rebuilt: list[str] = ()         # 本次重建的 singleton 名（如 "llm_client"）
    error: str | None = None        # rebuild_failed 時的原因摘要

class RuntimeConfigService:
    def list_configs(self) -> list[ConfigEntry]: ...
    def get_config(self, key: str) -> ConfigEntry | None:   # None = 白名單外
        ...
    async def set_config(self, key: str, value: str, app: FastAPI) -> SetOutcome: ...
```

- 狀態碼對映由 API 層負責:`not_allowed→404`、`invalid_option→422`、
  `rebuild_failed→500`;TG 層則映射為對應的 alert/edit_text 文案。
- rebuild 註冊表(service 內部常數):

```python
REBUILD_REGISTRY: dict[str, tuple[str, Callable[[], Any]]] = {
    # 設定鍵: (app.state 屬性名, factory)
    "AUDIO_EVALUATOR_PROVIDER": ("audio_evaluator", create_audio_evaluator),
    "LLM_MODEL_NAME":           ("llm_client", create_llm_client),
    "LLM_PROVIDER":             ("llm_client", create_llm_client),
    "LLM_CLAUDE_CODE_EFFORT":   ("llm_client", create_llm_client),
}
```

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `app/services/runtime_config_service.py` | 新增:service + rebuild 註冊表 + 結構化結果型別 |
| `app/api/config.py` | 新增:GET 列表/PUT 設值,轉換 service 結果 ↔ HTTP 語意(404/422/500) |
| `app/main.py` | 掛新 router |
| `app/bot/handlers/callbacks_config.py` | value_selection 改呼叫 service;evaluator rebuild 段移除(進 service);UI/權限不動 |
| `backend/.env.example` | 補 `MODIFY_LLM_PROVIDER` 示例註解(說明啟用後 API/TG 皆可切 provider) |
| `backend/tests/test_runtime_config_service.py` | 新增:白名單拒絕/選項驗證/setattr 生效/rebuild 成功/rebuild 失敗回滾(fake app + fake factory)/當前值讀取 |
| `scripts/fastapi_client/*/generate_child_cards.py` | 寫入 DB 的 `llm_model` 改取**生成 API 回應**的 `llm_model`(後端單一事實來源),不再以本機 settings 推導——2026-08-28 錯標 190 筆的根治;`scripts/common/llm_label.py` 降級為回應缺欄時的 fallback 並註記 |

## 5. 實作順序

- **P0 Service + 單元測試**:純邏輯先行,fake app.state 覆蓋 rebuild/回滾。
- **P1 API router**:GET/PUT + HTTP 語意測試(FastAPI TestClient)。
- **P2 TG 改薄**:callback 換呼叫 service,手動以 TG 實測一次選單流程回歸。
- **P3 實機驗證**:`PUT LLM_MODEL_NAME` 後生成一張卡,Anki tag/DB 標籤反映新模型
  (S011 修復的驗收點)。

## 6. 風險與未知

- **rebuild 失敗的中間態**:`create_llm_client()` 失敗時回滾 settings,
  但 `app.state.llm_client` 保持舊實例——舊值舊實例一致,無中間態。
  若舊實例本來就是 None(啟動時就失敗),回滾後仍 None,PUT 回 500 並附因。
- **TG 回歸面**:callback 重構後文案與按鈕流程須逐一比對;P2 以實測把關。
- **白名單即權限**:API 能改的範圍完全由 NAS/本機 .env 的 `MODIFY_*` 決定,
  部署者不加 `MODIFY_LLM_PROVIDER` 就改不了 provider——與 TG 同一治理模型,
  文件寫明即可。

## 7. 驗收標準

- [ ] `GET /api/v1/config` 回傳白名單鍵、選項與當前值;白名單外的鍵不可讀不可寫。
- [ ] `PUT /api/v1/config/LLM_MODEL_NAME`(值在選項內)後,`app.state.llm_client` 為新實例,後續生成的 Anki tag/DB 標籤反映新模型(S011 修復驗收)。
- [ ] 選項外的值回 422;白名單外的鍵回 404;rebuild 失敗回 500 且 settings 已回滾(單元測試覆蓋)。
- [ ] TG `/setconfig` 流程行為與現狀一致(含 evaluator 切換的回滾文案),callback 內不再含 rebuild 業務邏輯。
- [ ] 生成腳本寫入 DB 的 `llm_model` 來自生成 API 回應;後端與腳本 env 刻意不一致時(重演 08-28 情境)DB 標籤仍正確。
- [ ] `GET /config` 的 `llm_label` 與後續生成寫入的 DB 標籤一致。
- [ ] 全套既有測試回歸綠燈。
