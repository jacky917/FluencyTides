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

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `app/services/runtime_config_service.py` | 新增:service + rebuild 註冊表 + 結構化結果型別 |
| `app/api/config.py` | 新增:GET 列表/PUT 設值,轉換 service 結果 ↔ HTTP 語意(404/422/500) |
| `app/main.py` | 掛新 router |
| `app/bot/handlers/callbacks_config.py` | value_selection 改呼叫 service;evaluator rebuild 段移除(進 service);UI/權限不動 |
| `backend/.env.example` | 補 `MODIFY_LLM_PROVIDER` 示例註解(說明啟用後 API/TG 皆可切 provider) |
| `backend/tests/test_runtime_config_service.py` | 新增:白名單拒絕/選項驗證/setattr 生效/rebuild 成功/rebuild 失敗回滾(fake app + fake factory)/當前值讀取 |

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
- [ ] 全套既有測試回歸綠燈。
