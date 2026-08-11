# 12. 實作紀錄（第三／四輪）— 測試基線、CI/CD、死代碼清理、文檔對齊與遺留項收尾

產生日期：2026-07-09（第三輪）／2026-07-11（第四輪收尾，見 §9）（由 Claude Code 依 [09_Action_Plan.md](09_Action_Plan.md) 執行）

本文檔記錄第三輪修正：處理 [09_Action_Plan.md](09_Action_Plan.md) 的**階段 3（測試與 CI/CD）、階段 4（死代碼清理）、階段 6（文檔同步）**，以及階段 1/2 剩餘的 bug/config/performance 項。由 6 個並行代理按嚴格檔案所有權分區執行，完成後做全套 pytest + 前端 tsc + 全量編譯的交叉驗證。**§9（第四輪，2026-07-11）**另記錄先前技術遺留項的收尾。與前三輪合計，141 條發現中已修復 **134 條**，僅餘 1 條（F042）未處理。

## 1. 成果總覽

| 指標 | 數值 |
|------|------|
| 本輪解決的原始發現 | **56 條**（含 4 條前輪完成本輪確認：F062/F111/F118/F125） |
| 里程碑 | **建立後端 48 個 + 前端 11 個自動化測試（F063 零測試解除）** |
| 差分 | **63 檔案，+3,037 / −492 行** |
| 新增檔案 | 18（測試 11 + 設定 5 + favicon + 2 個 `__init__.py`） |
| 刪除檔案 | 6 個空殼 scaffold 套件 |
| **四輪累計** | **134 / 141 已修復**（第一輪 31 + 第二輪 41 + 第三輪 60 + 第四輪將 F023/F096 由部分升級為完全修復）＋ F105 保留為活代碼 ＋ **0 部分修復** ＋ 5 暫緩（F072、F075、F093、F099、F101）＋ **1 未處理（F042）**。核對：134 + 1（F105）+ 5 + 1 = 141 |

### 最大成果：F063 零測試風險解除

前兩輪雖做了 runtime 驗證，但那是臨時 venv 裡的一次性檢查。本輪把它**沉澱為 repo 內的自動化測試**：

- **後端 `backend/tests/`（48 個測試，全綠）**：`test_api_smoke`（全端點 + F001/Bug1 的 `/cards/models=200`）、`test_config`（F004/F005 fail-closed validator）、`test_anki_escape`（F071/F085/F086/F112 查詢跳脫）、`test_relation_sync`（F002 空列表防護）、`test_llm_client`（圍欄清理 + F041 重試分類）、`test_schema_composer`、`test_alembic`（F009 baseline 遷移）。
- **前端 `frontend/tests/`（11 個測試）+ vitest 基線**：`cn`、`ApiError`、`useLocalStorage`（含 F055 stale closure 驗證）。
- 這些測試對應 [11_Implementation_Log.md](11_Implementation_Log.md) §1 的 runtime 驗證清單——現在每次 CI 都能自動攔截同類回歸（F001 那種「方法定義損壞仍過 CI」不會再發生）。

---

## 2. 階段 3：測試與 CI/CD

| finding | 修法 |
|---------|------|
| **F063**(test-gap) | 建立 `backend/tests/` pytest 套件（48 測試）+ `pytest.ini`（asyncio_mode=auto）+ `requirements-dev.txt`。conftest 用 TestClient + `dependency_overrides` + mock AnkiClient，不需真實外部服務 |
| **F126**(test-gap) | 前端 vitest 基線 + `vitest.config.ts` + `package.json` test script + 3 個範例測試 |
| **F054**(config) | 新建 `frontend/eslint.config.js`（ESLint 9 flat config，`npm run lint` 恢復可用） |
| **F058**(config) | requirements.txt 全部 `>=` 改為相容區間 `>=X,<Y`（0.x 取下一 minor、semver 取下一 major），建置可重現 |
| **F059**(bug) | CI 部署 webhook 的 curl 加 `--fail`（`-sf`），HTTP 錯誤使 step 失敗 |
| **F060**(bug) | Dockerfile CMD 加 `--proxy-headers --forwarded-allow-ips=*`，nginx 傳的真實 IP 生效 |
| **F062**(config) | HEALTHCHECK（前輪已完成，本輪確認） |
| **F128**(perf) | Dockerfile 改 `COPY --chown=` 一步到位，移除 `chown -R`，映像層不再翻倍 |
| **F130**(config) | CI lint/build job 加 `needs: detect-changes` + `if` 條件（PR 一律全跑、push 僅相關路徑變更才跑） |
| **F131**(docs) | CI 註解 Docker Hub → GHCR；image 名改 `${{ github.repository_owner }}` 動態取得 |
| **F053**(config) | frontend/Dockerfile 加 `ARG VITE_* → ENV`，VITE_ 變數可在 build time 注入（原只用 hardcoded fallback） |

> **CI 接入（第四輪已完成，見 §9）**：第三輪時 pytest 與 vitest 已就緒但尚未加入 CI job（frontend-build 當時只做 build）。第四輪已在 CI 加 `pytest`（backend-lint-test）與 `npm test` + eslint（frontend-build）步驟，作為 docker 部署 job 的前置——測試失敗即擋下部署，測試防線正式生效。

---

## 3. 階段 4：死代碼清理

每項刪除前都以 grep 全 repo 證明零引用：

| finding | 處置 |
|---------|------|
| **F115**(dead-code) | 刪除 6 個空殼 scaffold：`backend/{api,core,models,services,utils}/`（各僅 0 byte `__init__.py`）+ `backend/app/domain/`。真實模組 `backend/app/core`、`backend/app/services` 不受影響 |
| **F079**(dead-code) | 刪除 `schemas/anki.py` 的 `AnkiCardTemplate`/`AnkiModelPayload`/`AnkiCreateModelRequest`（互相依賴、對外零引用） |
| **F080** | 刪除 `schemas/relation.py` 的 `RelationDef`/`CardRelationBatchDelete` + 連帶未用的 `Literal` import |
| **F081** | 刪除 `schemas/storage.py` 的 `MinioPresignedUrlRequest` + 未用的 `timedelta` import |
| **F082** | 刪除 `schemas/speaking.py` 的 `PromptAudioItem`（與 `ReferenceAudioItem` 重複） |
| **F090** | 刪除 `prompt_manager.py` 的 `has_template`/`list_templates`（零呼叫端） |
| **F105** | **保留 `state.py` 的 `has_state`**——grep 確認已被上輪 F048/BugB 修復啟用（voice.py:55），改列為活代碼 |
| **F106**(config) | 新建 `bot/handlers/__init__.py`、`bot/utils/__init__.py`（原依賴 implicit namespace package） |
| **F111**(dead-code) | 前輪 F109 抽取 `_bootstrap.py` 時已消除，本輪確認無殘留 |
| **F135**(dead-code) | 刪除 `alembic/env.py` 未用的 `import os` |
| **F129**(dead-code) | `.vscode/settings.json` 從空物件填入專案級設定（Python interpreter、pytest、ruler、files.exclude 等） |

---

## 4. 階段 1/2 剩餘 + 其他 bug/config/perf

| finding | 修法 |
|---------|------|
| **F019**(config) | CORS 設定化：config.py 新增 `CORS_ORIGINS`（支援逗號分隔/JSON 陣列/list 三種輸入，validator 解析），main.py 從 settings 讀取 |
| **F037**(bug) | `models.py` 的 `created_at` 改 `default_factory=lambda: datetime.now(timezone.utc)`（應用層 UTC），消除 SQLite/MySQL 時區語義不一致 |
| **F038**(config) | `openai_client.py` 比照 LLMClient 檢查 `LLM_BASE_URL`，避免非 OpenAI 金鑰誤送 api.openai.com |
| **F045**(bug) | voice.py：audio_evaluator 未注入時給友善錯誤並 return（不消費使用者狀態），不再 TypeError 崩潰 |
| **F047**(bug) | messages.py：`F.text` handler 攔截 `/` 開頭的未知指令，回提示而非送 LLM 生成垃圾卡片 |
| **F052**(config) | 9bbc 遷移的 `server_default` 由硬編碼 SQLite `text('(CURRENT_TIMESTAMP)')` 改方言中立的 `sa.func.now()`（僅影響全新環境，既有 DB 不重跑） |
| **F055**(bug) | `useLocalStorage`：functional update 消除 stale closure、storage 事件 JSON.parse 加 try/catch |
| **F056**(bug) | CardGenerator：`primary_field_name` 由選定 model 的 `fields[0]` 推導、model_name 用 modelInfo（不再硬編碼 Expression/TOEIC_Coach_Dark） |
| **F066**(config) | config.py 的 `env_file` 改用絕對路徑（`Path(__file__).parents[2]/.env`），換 CWD 啟動仍讀得到 |
| **F069**(dead-code) | dependencies.py 刪除未用的 module-level logger |
| **F070**(docs) | main.py openapi_tags 補 Relations 與 Telegram Webhook 兩組 |
| **F073**(bug) | storage.py presigned_url 為 None 時拋 502 而非靜默空字串 |
| **F083**(perf) | `batch_create_relations` 消除 N+1：批次註冊類型 + 單次 flush 取 id + 單一交易 commit |
| **F084**(perf) | `sync_with_anki` 改 Python 端比對孤兒 + 每批 ≤900 的 IN 刪除，避免超 SQLite 變數上限 |
| **F089**(dead-code) | `clean_html` 修正為 regex 去標籤 + `html.unescape` 完整還原實體 |
| **F092**(bug) | `ensure_bucket_exists` 捕捉 BucketAlreadyExists 使並發冪等 |
| **F100**(docs) | ffmpeg_merger 的 `segment_count` 改為與實際 concat 一致（含靜音段） |
| **F102**(bug) | `/newcard` 改用 `CommandObject.args`，正確處理 `/newcard@BotName` |
| **F103**(bug) | Card_ID 加 `secrets.token_hex(3)` 後綴，同秒不再撞 ID |
| **F116**(perf) | KnowledgeGraph 反向連線檢查 O(L²)→O(L)（Map/Set 索引） |
| **F118**(bug) | card 快取失效（前輪回歸修復已完成，本輪確認） |
| **F119**(bug) | 記住的 deck 不在清單時 fallback 到 All Decks + 提示 |
| **F121**(bug) | App.tsx 加 `<Route path="*">` 404 頁，未知路徑不再空白 |
| **F122**(config) | 新建 `public/favicon.svg`（潮汐主題），index.html 引用改正，消除 404 |
| **F125**(dead-code) | App.tsx scaffold 註解（前輪已清，本輪確認） |
| **F127**(config) | x-casaos 移除 armv7 宣告，與 CI 的 amd64/arm64 對齊 |
| **F133**(config) | compose env_file 硬編碼 CasaOS 絕對路徑改 `./.env` + 註解 |

---

## 5. 腳本修復（階段 4 附帶）

| finding | 修法 |
|---------|------|
| **F107**(bug) | `import_cards_from_json.py`：關聯建構納入 try/except，label 超長截斷，卡片成功後的關聯錯誤不再使同卡重複計入 fail |
| **F108**(bug) | JSON 陣列元素驗證為 dict，混入字串跳過 + warning，不再整批崩潰 |
| **F110**(bug) | `--db-url` 自建 engine 於 finally `dispose()`（預設共享 engine 由旗標守衛不誤 dispose） |

---

## 6. 文檔對齊（階段 6）

| finding | 修法 |
|---------|------|
| **F015**(high) | **根 README 整份重寫**——原本描述不存在的 Flask/Redis/PostgreSQL，改為實際的 FastAPI + aiogram 3 + SQLModel/Alembic + MinIO + React/Vite，含正確目錄結構、啟動方式、docs 索引 |
| **F065/F136/F137/F138/F140**(docs) | docs/01 目錄樹對齊實際結構（補齊實際模組、標 domain/ 為空殼、DB 改 SQLite→MySQL、CI 描述補全）；本輪測試建立後另補上 `backend/tests/` 樹 |
| **F064/F139**(docs) | docs/02 移除不存在的 `old/` 引用；測試條目本輪測試落地後更新為「已建立」 |
| **F141**(docs) | ADR 002 補「實作現狀」說明——關閉階段只釋放 AnkiClient + DB engine，LLMClient 從未關閉 |

---

## 7. 驗證

- **後端 pytest**：`48 passed`（Python 3.11 venv + 完整依賴）——全套在 D1/D2 的大量改動後仍全綠。
- **前端**：`tsc -b` 通過（0 error）；`eslint` 0 error；`vitest 11/11`（本機 Node 16 靠 polyfill 跑通，CI Node 20 原生執行）。
- **全量編譯**：全後端 `py_compile` 通過；6 個空殼目錄確認刪除；死符號全 repo grep 零殘留。
- **行為驗證**：TestClient 啟動正常、CORS 三種輸入解析正確、UTC 時戳、批次寫入 id/正規化正確。
- **限制**：無真實 Anki/MinIO/Telegram/LLM 服務，測試以 mock 驗證介面契約與錯誤分類，非真服務整合；前端測試框架需 Node 18+（CI 滿足）。

## 8. 已知遺留（第四輪後更新）

原始 141 條發現中，**第四輪收尾後**的最終狀態：**134 已修復、F105 保留為活代碼、0 部分修復、5 暫緩（F072/F075/F093/F099/F101，破壞性契約變更或死模組）、1 未處理（F042）**。第三輪時列為遺留的「CI 尚未接上測試」「F023 部分」「F096 部分」「webhook 半套原子性」四項均已於第四輪完成（見 §9），移出本清單。

尚待處理的事項（第四輪後）：

- **F042（唯一未處理的原始發現，需產品決策）**：`VoicepeakRunner`/`FfmpegMerger` 零呼叫的未接線模組。注意第二輪 F008 的 OpenAI 音訊轉碼已使用系統 ffmpeg（非 `FfmpegMerger` 類），故該類是否真為死代碼需再確認接線意圖（VOICEPEAK TTS 是否納入產品）後才刪，保守未動。
- **前端 X-API-Key 生產認證（需架構決策）**：前端目前的 API 認證方式在生產環境的安全模型仍待架構層決定，暫未動。
- **5 暫緩**：F072、F075、F093、F099、F101（破壞性 API 契約變更或涉及死模組，需與前端契約同步規劃）。
- 少數 low 項（如 F092 更嚴謹並發、F083 更大批次優化）為保守處理。

---

## 9. 第四輪收尾（2026-07-11）— 技術遺留項清理

第三輪後仍留有四項技術遺留（見 §8 舊版），第四輪逐項收尾並驗證。**驗證：48 個 pytest 全綠 + e2e smoke 通過。**

| 遺留項 | 收尾內容 |
|--------|----------|
| **CI 接入測試**（F063 最後一哩） | `.github/workflows/main.yml` 的 **backend-lint-test** job 加 pytest 步驟（安裝 `requirements-dev.txt` 後執行 pytest，測試失敗即擋下 **backend-docker** 部署）；**frontend-build** job 加 vitest（`npm test`）+ eslint。pytest/vitest 正式成為 docker 部署前置，測試防線生效。 |
| **F023**（graph 快取） | 由「🔶 部分修復（僅邏輯下沉）」升級為 **✅ 完成**——`RelationService` 加**類別層級 TTL(30s) 圖譜快取**，寫入路徑（新增/刪除關聯）**主動失效**快取，冷熱路徑一致。第一輪「快取/增量優化未做」殘餘清零。 |
| **F096**（AnkiCardInfo） | 由「🔶 部分修復（僅 `can_add_notes`）」升級為 **✅ 完成**——新增 **`AnkiCardInfo` 模型**、`get_cards_info` 改型別化回傳，消費端改型別化屬性存取（不再裸字典）；連帶修復 `anki_model/manager.py` 的 `isinstance(card, dict)` 回歸。 |
| **webhook `_persist_recording` 半套原子性**（第二輪遺留） | **✅ best-effort 完成**——改為「**先算後存**」：先完成計算再寫入，寫入失敗時**補償刪除孤兒 media**（避免半套狀態殘留 MinIO），回**明確錯誤**，並確保**重試冪等**。 |

### 驗證

- **pytest**：`48 passed`（含既有全套 + 涉及 F023/F096 的介面契約測試）。
- **e2e smoke**：端到端冒煙測試通過。
- **CI**：測試步驟已接入，本地確認 pytest/vitest 為 docker job 前置。
