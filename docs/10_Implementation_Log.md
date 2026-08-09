# 10. 實作紀錄（Implementation Log）— 巨型模組拆分與設計偏離修正

產生日期：2026-07-08（由 Claude Code 依 [09_Action_Plan.md](09_Action_Plan.md) 執行，方案出自 [08_Refactor_Recommendations.md](08_Refactor_Recommendations.md)）

本文檔記錄第一輪修正的完整實作：處理範圍為**巨型模組拆分**與**設計偏離**兩大類問題，由 5 個並行修正代理按檔案所有權分區執行，完成後經交叉驗證（全量 `py_compile`、銜接點 grep 核對、方法清單前後 diff）。所有 finding id 對應 [06_Issues_and_Risks.md](06_Issues_and_Risks.md)。

## 1. 成果總覽

| 指標 | 數值 |
|------|------|
| 解決的發現 | **33 條**（1 critical + 9 medium + 23 low；另 F096 部分完成） |
| 修改檔案 | 27 個（後端 21 + 前端 6） |
| 新建檔案 | 17 個 |
| 差分 | **+1,087 / −2,378 行（淨 −1,291 行）** |
| 消除的技術債標記 | 25 處 `# type: ignore`（後端）+ 15 處 `any`（前端）全數歸零 |
| 刪除的死代碼 | ~120 行（`generate_and_add_card` / `check_and_generate`）+ 重複方法 + 重複 Prompt |

### 已解決 finding 一覽

| 主題 | finding | 章節 |
|------|---------|------|
| CardService 拆分 | **F001**(critical)、F030、F031、F046、F088、F091 | §2.1 |
| AnkiClient 拆分 | F134、F095、F096(部分)、F094 | §2.2 |
| AnkiModelManager 拆分 | F027、F028、F074、F087 | §2.3 |
| 後端設計偏離 | F021、F022、F023(部分)、F029、F041、F043、F076、F077、F078、F097、F104、F109、F113、F114 | §3 |
| 前端設計偏離 | F057、F117、F120、F123、F124 | §4 |
| 明確跳過 | F072、F075（API 破壞性變更需與前端契約同步）、F093（DB metadata 遷移風險）、F099、F101（涉及死模組/部署模式） | §6 |

---

## 2. 巨型模組拆分

### 2.1 `card_service.py`：763 → 568 行 + 兩個新模組

**差分**：`card_service.py` −195 行；新建 `speaking_service.py`（236 行）、`schema_composer.py`（61 行）。

**解決的問題**：

- **F001（critical）**：`process_voice_evaluation` 的 `except raise` 之後寄生著孤立 docstring 與 `return self._model_manager.list_available_models()`——`def list_available_models(self)` 簽名被誤刪，`GET /api/v1/cards/models` 必然 500。已恢復方法定義，端點恢復運作，前端 CardGenerator 模型下拉選單復活。
- **F030**：刪除 Phase 1 遺留、全 repo 零呼叫端的 `generate_and_add_card` / `check_and_generate`（~120 行）。
- **F031**：170 行的 `generate_card` 拆為編排骨架（~90 行含 docstring）+ 六個私有步驟方法（`_ensure_preconditions` / `_load_llm_schema` / `_resolve_system_prompt` / `_merge_and_extract` / `_submit` / `_create_relations_from_llm_data`）；30 行 Graph_Relations JSON Schema 字面量移出流程代碼，成為 [schema_composer.py](../backend/app/services/schema_composer.py) 的純函數 `compose_graph_relations`（深拷貝防止汙染快取 Schema）。
- **語音評估流程獨立**：`process_voice_evaluation`（127 行，與卡片生成零共用狀態）整體移入新建 [speaking_service.py](../backend/app/services/speaking_service.py) 的 `SpeakingService`，內部再拆 `_load_card_context` / `_persist_recording`，以 `_CardContext` dataclass 承載中間狀態；`bot/dependencies.py` 與 `bot/handlers/voice.py` 注入鏈同步改指向新服務。
- **F046**：`ServiceInjectionMiddleware` 原本在 LLM client 缺失時讓整個 Bot 以 RuntimeError 全滅——改為 `llm_client=None` 時仍注入全部服務（僅首次記 warning），需要 LLM 的流程在 `generate_card` 拋 `LLMServiceError`，由既有 `FluencyTidesError` handler 回覆友善錯誤。另將 `AnkiModelManager` / `PromptManager` / `SpeakingService` 以 `anki_client` 為鍵做中介層實例級快取，不再每個 Telegram Update 重建全套服務（`RelationService` 因綁定 DB session 維持逐 Update 建立）。
- **F088**：`update_card` 新增 `primary_field_name: str = "Expression"` 參數，消除硬編碼主欄位假設（預設值保持既有行為，呼叫端零改動）。
- **F091**：4 處函數內 import（`copy`、`AnkiStoreMediaParams`、`RecordingItem`、`CardRelationCreate`）上移至模組頂部。

**優點**：CardService 回歸單一職責（卡片 CRUD + 生成編排）；語音評估可獨立測試（只依賴 AnkiClient + 呼叫時傳入的 evaluator）；Bot 在缺配置時優雅降級而非全滅。

### 2.2 `infrastructure/anki/`：933 行單檔 → 傳輸層 + 六個領域 Mixin

**差分**：`client.py` 933 → 60 行（純組合類）；新建 `transport.py`（228）、`notes.py`（278）、`cards.py`（127）、`decks.py`（121）、`misc.py`（96）、`media.py`（91）、`models.py`（86）。公開 API 完全不變（Mixin 組合），全 repo 10 個呼叫點的 `from app.infrastructure.anki.client import AnkiClient, AnkiConnectError` 均不受影響；方法清單前後 diff 完全一致。

**解決的問題**：

- **F134**：新增 `AnkiTransport._invoke_typed(action, result_type, **params)`（pydantic `TypeAdapter` 執行期驗證），全部 **25 處 `# type: ignore` 歸零**。AnkiConnect 回傳 `null` 時從「靜默 `TypeError`」變為明確的 `ValidationError`。`mypy` 於該套件 0 錯誤。
- **F095**：`_invoke` 的 DEBUG 日誌經 `_summarize_params()` 摘要——base64 的 `data` 欄位一律只輸出 `<N bytes>`，其他值超過 200 字元截斷，`storeMediaFile` 的整段 base64 不再進日誌。
- **F096（部分）**：`can_add_notes` 簽名改為 `Sequence[AnkiNote | dict[str, object]]`（唯一呼叫端傳裸 dict，採 Union 向後相容）；`get_cards_info` 的 `AnkiCardInfo` 回應模型**未新增**（呼叫端同輪次由他人重構，留待下一輪，見 §6）。
- **F094**：`SYNC_TIMEOUT` 從「定義後從未使用」改為真正生效——`_invoke` 支援 per-request timeout 覆寫，`sync()` 傳入 60 秒（同步操作確實常超過預設 30 秒，故選「使用」而非「刪除」）。

**驗證**：除 `py_compile` 外，該代理另以 Python 3.11 + respx 對 `_invoke` 錯誤分支、`null` 回應、`can_add_notes` 雙型別輸入、`sync()` timeout 做了冒煙測試，全數通過。

### 2.3 `anki_model_manager.py`：617 行三職責混合 → `anki_model/` 套件

**差分**：`anki_model_manager.py` 617 → 24 行（相容 re-export shim）；新建 `anki_model/repository.py`（272）、`manager.py`（420）、`note_builder.py`（63）、`__init__.py`（24）。全 repo 5 處既有 import 路徑全部不變。

**解決的問題**：

- **職責分離**：本地模型檔案 IO → `ModelFileRepository`（不依賴 AnkiClient）；AnkiNote 組裝 → `build_note_from_llm_response` 純函數；AnkiConnect 前置檢查與提交 → 瘦身後的 `AnkiModelManager`。
- **F028 + F074**：`can_add_note` 從「每次請求同步掃描整個模型目錄」改為「單模型 `asyncio.to_thread` 讀取 + 實例級快取（首次之後零 IO）」；`import_model_from_files` 四處同步 `open()` 全部 async 化。API 層兩個 async 端點的事件迴圈阻塞隨之消除。
- **Singleton**：`main.py` lifespan 建立 `app.state.model_repo`，`core/dependencies.py` 的工廠優先取用（拿不到時退回自建，Bot 端與 scripts 相容）。
- **F027**：`ensure_deck_exists(deck_name, *, sync_on_missing=False)`——預設不再於牌組缺失時隱式觸發完整 AnkiWeb 同步，改為快速失敗；`import_cards_from_json.py` 腳本場景顯式傳 `sync_on_missing=True` 保留原行為。
- **F087**：兩處 `raise ... from e` 補上異常鏈。

**行為變化（需注意）**：模型 JSON/HTML/CSS 檔首次讀取後快取，**執行期修改模型檔需重啟服務**（repository.py 模組 docstring 已標注）。

---

## 3. 後端設計偏離修正

- **F021** `core/config.py`：新增 `@lru_cache get_settings()`（標準 FastAPI 模式，測試可 `cache_clear()`），模組層 `settings = get_settings()` 保持全部既有 import 不變；docstring 改為如實描述。
- **F022 + F023（下沉部分）** `api/relations.py` → `relation_service.py`：`get_graph_data` 的 Anki 查詢與卡片狀態提取整段下沉至 `RelationService.get_graph_data(anki_client, deck_name)`（AnkiClient 以方法參數傳入，DI 佈線零改動），Controller 只留參數傳遞；`AnkiConnectError` 統一包裝為 `AnkiServiceError`（502），不再裸 500。**快取優化未做**（留待下一輪，見 §6）。
- **F029** 刪除與 `delete_relations_by_note_id` 完全重複的 `delete_relations_for_note`，`card_service.py` 呼叫端同步改名（跨代理銜接，grep 確認零殘留）。
- **F076/F077** Schema 驗證強化：`CardRelationCreate` 的 `relation_type`/`target_label` 加 `min_length=1` + `model_validator` 要求 source 至少一者有值；`CardUpdateRequest.fields` 拒絕空字典。**行為變化**：原本靜默接受的空值請求現在回 422。
- **F078** `ErrorResponse` 移至新建 [schemas/common.py](../backend/app/schemas/common.py)，`card.py` re-export 平滑遷移。
- **F043 + F097** audio_evaluator 去重與強化：兩個 evaluator 逐字重複的評分 Prompt 抽至新建 `audio_evaluator/prompts.py`；圍欄清理統一為 `llm/client.py` 的模組級 `strip_markdown_fences`（原三處重複）；`BaseAudioEvaluator` 改為 Template Method——`evaluate_audio` 提供統一指數退避重試（基底類原本承諾但從未實作），子類只實作 `_evaluate_audio_once`；Gemini 改用 `response_schema=AudioEvaluationResult` + `response.parsed`（保留文字解析 fallback，因環境無法安裝 SDK 驗證，docstring 已註明）。
- **F041** `llm/client.py` 重試策略：僅對 `RateLimitError` / `APIConnectionError` / `APITimeoutError` / 5xx 重試且改指數退避（2/4/8s）；401/400 立即包裝 `LLMServiceError` 拋出（原本盲目重試 3 次）。
- **F104** `/newcard` 硬編碼模型名改為 `TG_SPEAKING_MODEL_NAME` 設定項。
- **F109/F113/F114** 腳本整併：兩支匯入腳本重複的 `--db-url` 引導抽至新建 `scripts/_bootstrap.py` 的 `build_session_factory()`；`update_tg_bot_links.py` 移除 `os.chdir + sys.path` hack（改 `python -m scripts.update_tg_bot_links` 執行）；被覆蓋的模組層 `basicConfig` 刪除。

---

## 4. 前端設計偏離修正

- **F057**：**15 處 `any` 全數清零**（grep 確認 `: any` / `as any` 零殘留）。`GraphNode` 補上實際存在的 `status` 欄位；新增 `RuntimeGraphNode` / `RuntimeGraphLink` 型別承載 force-graph 執行期欄位（x/y/color 等）；`fgRef` 改用庫匯出的 `ForceGraphMethods`；抽出 `endpointId` / `endpointLabel` helper 統一處理「字串 id 或節點物件」聯集。
- **F117**：`alert()` 全部改為 sonner toast；`confirm()` 改為非阻塞確認——CardDetailModal 刪除按鈕二段式（點擊變「確認刪除？」，5 秒自動復原）、KnowledgeGraph 刪關聯改為圖譜底部內嵌確認列（8 秒自動消失），timer 均有 unmount 清理。
- **F123**：新建 [components/ui/select.tsx](../frontend/src/components/ui/select.tsx)（cva + cn + forwardRef，遵循既有 shadcn 慣例），取代四處複製貼上的長樣式字串。
- **F124**：新增 `class ApiError extends Error`（errorCode / status / details），interceptor 統一 reject 真正的 Error 物件；`checkHealth` 改走共用 `apiClient`（`baseURL: '/api'`，已核對 vite proxy 規則），保留 interceptor 一致性。
- **F120**：行動版 header 加漢堡選單（lucide `Menu`/`X`，含 aria-label / aria-expanded，點連結自動收合），行動裝置終於能到達 Card Generator 與 Knowledge Graph。無新增 npm 依賴。

---

## 5. 差分紀錄

<details>
<summary>git diff --stat（27 個修改檔案，點開展開）</summary>

```
backend/app/api/relations.py                       |  18 +-
backend/app/bot/dependencies.py                    |  73 +-
backend/app/bot/handlers/commands.py               |   5 +-
backend/app/bot/handlers/voice.py                  |  14 +-
backend/app/core/config.py                         |  31 +-
backend/app/core/dependencies.py                   |  27 +-
backend/app/infrastructure/anki/__init__.py        |  10 +
backend/app/infrastructure/anki/client.py          | 943 +--------------------
backend/app/infrastructure/audio_evaluator/base.py | 109 ++-
backend/app/infrastructure/audio_evaluator/gemini_client.py |  88 +-
backend/app/infrastructure/audio_evaluator/openai_client.py |  80 +-
backend/app/infrastructure/llm/client.py           | 110 ++-
backend/app/main.py                                |   8 +
backend/app/schemas/card.py                        |  47 +-
backend/app/schemas/relation.py                    |  26 +-
backend/app/services/anki_model_manager.py         | 627 +-------------
backend/app/services/card_service.py               | 573 +++++--------
backend/app/services/relation_service.py           |  66 +-
backend/scripts/import_cards_from_json.py          |  37 +-
backend/scripts/import_cards_with_llm.py           |  33 +-
backend/scripts/update_tg_bot_links.py             |  20 +-
frontend/src/App.tsx                               | 110 ++-
frontend/src/api/client.ts                         |  47 +-
frontend/src/components/CardDetailModal.tsx        |  80 +-
frontend/src/pages/CardGenerator.tsx               |  13 +-
frontend/src/pages/KnowledgeGraph.tsx              | 234 +++--
frontend/src/types/api.ts                          |  36 +
27 files changed, 1087 insertions(+), 2378 deletions(-)
```

</details>

### 新建檔案（17 個）

| 檔案 | 行數 | 職責 |
|------|-----:|------|
| `backend/app/infrastructure/anki/transport.py` | 228 | AnkiTransport：`_invoke` / `_invoke_typed` / 日誌摘要 + AnkiConnectError |
| `backend/app/infrastructure/anki/notes.py` | 278 | NoteActionsMixin（14 方法） |
| `backend/app/infrastructure/anki/cards.py` | 127 | CardActionsMixin（7 方法） |
| `backend/app/infrastructure/anki/decks.py` | 121 | DeckActionsMixin（7 方法） |
| `backend/app/infrastructure/anki/misc.py` | 96 | MiscActionsMixin（6 方法，含 sync） |
| `backend/app/infrastructure/anki/media.py` | 91 | MediaActionsMixin（5 方法） |
| `backend/app/infrastructure/anki/models.py` | 86 | ModelActionsMixin（4 方法） |
| `backend/app/services/anki_model/repository.py` | 272 | ModelFileRepository：檔案 IO + 快取 + async |
| `backend/app/services/anki_model/manager.py` | 420 | 瘦身後的 AnkiModelManager（AnkiConnect 職責） |
| `backend/app/services/anki_model/note_builder.py` | 63 | AnkiNote 組裝純函數 |
| `backend/app/services/anki_model/__init__.py` | 24 | 套件 re-export |
| `backend/app/services/speaking_service.py` | 236 | SpeakingService：語音評估全流程 |
| `backend/app/services/schema_composer.py` | 61 | Graph_Relations Schema 注入純函數 |
| `backend/app/infrastructure/audio_evaluator/prompts.py` | 68 | 兩個 evaluator 共用的評分 Prompt |
| `backend/app/schemas/common.py` | 41 | 全系統共用的 ErrorResponse |
| `backend/scripts/_bootstrap.py` | 64 | 腳本共用的 --db-url session factory |
| `frontend/src/components/ui/select.tsx` | 38 | 共用 Select 元件（cva/cn） |

所有新建與修改的模組均附**符合規範的繁體中文 docstring**（後端 Google style 的 Args/Returns/Raises；前端 TSDoc 的 @param/@returns），模組層 docstring 標注職責與拆分來源。

## 6. 行為變化與遺留事項

### 有意的行為變化（部署前需知悉）

1. 模型檔快取：執行期修改 `anki_models/` 下的檔案需重啟服務。
2. `ensure_deck_exists` 預設不再自動觸發 AnkiWeb 同步（腳本已顯式保留舊行為）。
3. 空值請求（空 fields、空 relation_type）從靜默接受變為 422。
4. LLM 401/400 立即失敗（原本重試 3 次共 ~6 秒）；語音評分反而**新增**了自動重試。
5. AnkiConnect 回傳異常結構從靜默 `TypeError` 變為明確 `ValidationError`；`/relations/graph` 遇 Anki 故障回 502 統一錯誤格式。
6. Bot 在 LLM 未設定時不再整體崩潰，改為生成功能單獨回覆錯誤。
7. `sync()` 超時 30 → 60 秒；`update_tg_bot_links.py` 改用 `python -m` 執行。
8. 前端刪除操作從阻塞式 confirm 改為二段式確認。

### 遺留事項（下一輪）

> 📌 **更新（2026-07-09）**：本節提及的階段 0–2（止血 / 安全加固 / 穩定性）已於第二輪處理，並補做真實環境 runtime 驗證，見 [11_Implementation_Log.md](11_Implementation_Log.md)。其餘遺留（F023 快取殘留仍在；階段 3 測試與 CI/CD、階段 4 死代碼、階段 6 文檔同步、F083/F084 批次寫入）多已於第三輪收尾，詳見 [12_Implementation_Log.md](12_Implementation_Log.md)。

- **F096 殘餘**：`get_cards_info` 的 `AnkiCardInfo` Pydantic 回應模型（本輪呼叫端與 client 並行重構，避免衝突而延後）。
- **F023 殘餘**：`/relations/graph` 的快取/增量優化（本輪只完成邏輯下沉）。
- **F072、F075**（API 回應風格統一）：屬破壞性變更，需與前端 `types/api.ts` 契約同步規劃。
- **F093**（metadata monkeypatch）、**F099/F101**（死模組錯誤契約、單 worker 限制）：依風險評估暫緩。
- **執行期驗證**：本機環境無專案依賴（Python 3.9、無 venv/node_modules），驗證手段為全量 `py_compile`（全部通過）、anki 套件的 mypy + respx 冒煙測試、grep 銜接點核對。**強烈建議**：在 Docker 或裝好依賴的環境跑一次 `uvicorn app.main:app` 啟動 + `/api/health`、前端 `tsc -b && vite build`，並人工走一次 Bot 錄音評分流程（SpeakingService 遷移無自動測試覆蓋）。這正是 [09_Action_Plan.md](09_Action_Plan.md) 階段 3 測試防線的意義——本輪已按方案文件要求將風險降到最低（純搬移 + 公開 API 不變），但零測試的結構性風險（F063）仍未解除。
