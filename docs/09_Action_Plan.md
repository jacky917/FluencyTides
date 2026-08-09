# FluencyTides 修正行動計劃（Action Plan）

產生日期：2026-07-07（由 Claude Code 全項目審查產生）
最後更新：2026-07-11（第四輪收尾遺留項後同步——CI 接入 pytest/vitest、F023/F096 完全修復、webhook 原子性補償，見 [12_Implementation_Log.md](12_Implementation_Log.md) §9）

本文檔是本次全項目審查（141 條發現，詳見 [06_Issues_and_Risks.md](06_Issues_and_Risks.md)）的執行路線圖：把所有推薦修正點按「先止血、再加固、後還債」的原則排成六個階段，每項標註對應 finding id、嚴重度與工作量估計（S ≤ 半天、M ≤ 2 天、L > 2 天）。各階段內部按建議執行順序排列。

## 階段總覽

| 階段 | 主題 | 目標 | 條目數 | 建議時程 | 狀態（最後更新 2026-07-11） |
|------|------|------|-------:|----------|--------------------|
| 0 | 止血 | 修復必然損壞的功能與資料遺失路徑 | 6 | 立即（1–2 天） | ✅ **完成**（第二輪，F001 隨第一輪重構修復；見 [11_Implementation_Log.md](11_Implementation_Log.md)） |
| 1 | 安全加固 | 消除 fail-open 認證與注入面 | 9 | 本週 | ✅ **完成**（第二輪，見 [11_Implementation_Log.md](11_Implementation_Log.md)） |
| 2 | 穩定性 | 錯誤邊界、超時、競態、啟動韌性 | 15 | 1–2 週 | ✅ **完成**（第二輪主體 + 第三輪收尾 F052；第二輪遺留的 webhook `_persist_recording` 半套原子性已於**第四輪**以 best-effort 補償完成；見 [11_Implementation_Log.md](11_Implementation_Log.md)、[12_Implementation_Log.md](12_Implementation_Log.md) §9） |
| 3 | 測試與 CI/CD | 建立回歸防線，堵住「lint 即上生產」 | 8 | 與階段 2 並行 | ✅ **完成**（第三輪建立 **後端 48 + 前端 11** 測試套件、F054/F058/F059/F060/F062/F130 收斂；**第四輪將 pytest/vitest 接入 CI 作為 docker 部署前置**，最後一哩完成；見 [12_Implementation_Log.md](12_Implementation_Log.md) §9） |
| 4 | 死代碼清理 | 移除空殼與未接線模組 | 7 組 | 順手處理 | ✅ **大部分完成**（第三輪清除空殼 scaffold／死 schema／死方法；殘留 F042 未接線模組；F105 改判為活代碼；見 [12_Implementation_Log.md](12_Implementation_Log.md)） |
| 5 | 重構 | 拆分巨型模組、下沉業務邏輯 | 8 | 防線建立後 | 大部分完成（8 項中 6 項完成、1 項部分完成，F083/F084 第三輪完成，見 [10_Implementation_Log.md](10_Implementation_Log.md)、[12_Implementation_Log.md](12_Implementation_Log.md)） |
| 6 | 文檔同步 | README 重寫與舊文檔修正 | 5 組 | 隨時可做 | ✅ **完成**（第三輪；README 重寫 + docs/01/02/ADR 全對齊，見 [12_Implementation_Log.md](12_Implementation_Log.md)） |

> 依賴關係：階段 5 的重構**必須**在階段 3 的測試防線建立後進行；階段 0–2 的修正每一項都應附帶對應測試（作為階段 3 的起點）。

---

## 階段 0：止血（立即執行）

> ✅ **完成（2026-07-09，第二輪）**：全六項已解決——F001（第一輪隨階段 5 重構）、✅ F002、✅ F003+F012、✅ F013、✅ F010、✅ F011（F010/F011 第一輪部分處理、本輪完成/確認）。全新 DB `alembic upgrade head` 與 `GET /cards/models=200` 已實測，見 [11_Implementation_Log.md](11_Implementation_Log.md)。

這六項要麼是「功能必然損壞」、要麼是「資料必然遺失」，全部有明確修法：

1. **F001（critical/S）** 恢復 `list_available_models` 的方法定義 — [card_service.py:488](../backend/app/services/card_service.py:488) 的方法簽名遺失，docstring 與 return 成為 `process_voice_evaluation` 尾端的死代碼，`GET /api/v1/cards/models` 必然 500，前端 CardGenerator 的模型下拉選單因此失效。補回 `def list_available_models(self) -> list[AnkiModelInfo]:` 並加 smoke test。
2. **F002（critical/S）** `sync_with_anki` 空列表防護 — [relation_service.py:143](../backend/app/services/relation_service.py:143)：Anki 回傳空集合時會清空整個關聯資料表，而關聯資料**只存在 SQLite、無法從 Anki 重建**。開頭加防護：空列表記 warning 並 return 0。
3. **F003（critical/S）+ F012（high/M）** SQLite 落點與權限 — 預設資料庫路徑不在掛載卷內，每次自動部署即清空資料庫；即使改到 `/app/data`，非 root 使用者對 bind mount 目錄又可能無寫入權限。一起修：預設 URL 改 `sqlite+aiosqlite:////app/data/fluencytides.db`，部署文件註明 `chown` 或改用 named volume。
4. **F013（high/S）** compose 網路兩邊都宣告 `external: true` — 沒有任何一方建立網路，`compose up` 必然失敗。改由後端 compose 建立（`name: fluencytides_net`），前端保持 external。
5. **F010（high/S）** 刪除被 commit 的 `vite.config.js` / `vite.config.d.ts` — Vite 會優先載入 `.js` 而遮蔽 `vite.config.ts`，所有後續 vite 設定修改都會靜默無效。`git rm` 後在 `.gitignore` 封鎖，`tsconfig.node.json` 加 `noEmit`。
6. **F011（high/S）** CardDetailModal 的 `isDeleting` 永不重置 — 刪除一張卡片後，之後開啟的所有 Modal 按鈕全部停用。改用 `deleteMutation.isPending`。

**驗證方式**：`GET /cards/models` 回 200；空 Anki 集合下 `/sync` 不清表；重新部署後資料庫仍在；兩個 compose 可從零 `up`；修改 `vite.config.ts` 能生效；連續刪兩張卡片。

---

## 階段 1：安全加固（本週）

> ✅ **完成（2026-07-09，第二輪）**：全九項已解決——✅ F004、✅ F005、✅ F049+F068、✅ F061、✅ F020、✅ F024、✅ F007、✅ Anki 查詢注入群組（F071/F085/F086/F112）、✅ F032。核心設計為「生產模式（`ENVIRONMENT=production`）fail-closed、開發模式行為不變」，fail-closed validator 行為已實測，見 [11_Implementation_Log.md](11_Implementation_Log.md)。

共同模式是「**缺配置 → 靜默放行**」，方向統一改為 fail-closed：

1. **F004（high/M）** `API_SECRET_KEY` 未設時所有受保護端點完全開放（[auth.py:51](../backend/app/core/auth.py:51)）— 新增 `ENVIRONMENT` 設定，生產模式下密鑰為空直接拒絕啟動。
2. **F005（high/S）** `TG_WEBHOOK_SECRET` 未設時 Webhook 無認證，可偽造 Update 冒充白名單使用者（[webhook.py:30](../backend/app/api/webhook.py:30)）— webhook 模式下無密鑰即拒絕啟動；handler 無密鑰設定時回 403 而非放行。
3. **F049（medium/S）** Webhook secret 比對非常數時間、日誌洩漏密鑰片段 — 改 `hmac.compare_digest`，移除日誌中的密鑰內容；F068（low）API Key 比對同樣處理。
4. **F061（medium/S）** 後端 8000 埠直接映射主機、繞過 nginx — 移除埠映射，僅經 nginx 反代出口。
5. **F020（medium/S）** MinIO 憑證預設 `minioadmin/minioadmin` — 預設改 `None`，缺值時明確報錯。
6. **F024（medium/M)** 上傳端點無大小/類型限制且整檔讀入記憶體 — 加大小上限（超限 413）、Content-Type 白名單、prefix 正則。
7. **F007（high/M）** 全 Bot 未做 HTML escaping — 使用者輸入/LLM 輸出插入 HTML 訊息前一律 `html.quote()`，含特殊字元即發送失敗的問題一併解決。
8. **Anki 查詢注入群組（F071、F085、F086、F112，low–medium/M）** — deck_name、card_id、牌組名稱等未跳脫即拼接 Anki 搜尋語法。在 AnkiClient 層加統一的查詢跳脫工具函數，四處呼叫點一起收斂。
9. **F032（medium/S）** 客戶端檔名未消毒即用於暫存檔與 MinIO 物件名 — 檔名走白名單字元過濾。

---

## 階段 2：穩定性（1–2 週）

> ✅ **完成（第二輪主體 + 2026-07-09 第三輪收尾 + 2026-07-11 第四輪清尾）**：✅ F018、✅ F016、✅ F017、✅ F006、✅ F008、✅ F025、✅ F033/F034/F035、✅ F098、F041（第一輪）、✅ F048、✅ F026、✅ F044、✅ F039/F040、✅ F009、✅ F036、✅ F051、✅ F050、✅ F067 於第二輪解決，**✅ F052（遷移改用方言中立 `sa.func.now()`）已於第三輪收尾**。**第四輪已補齊最後殘留**：webhook `speaking_service._persist_recording` 的「存 media + 寫回欄位」兩步改為 best-effort 原子——先算後存 + 失敗補償刪除孤兒 media + 明確錯誤 + 重試冪等。詳見 [11_Implementation_Log.md](11_Implementation_Log.md)、[12_Implementation_Log.md](12_Implementation_Log.md) §9。

按子系統分組：

**啟動與生命週期**
1. **F018（medium/S）** Bot 啟動未包 try/except，Telegram 暫時故障會拖垮整個後端啟動 — 失敗時降級為 `bot=None`。
2. **F016（medium/S）** polling task 異常無人觀察、shutdown re-raise 跳過清理 — `add_done_callback` + `try/finally`。
3. **F017（medium/S）** webhook secret 輪換不會觸發重綁 — 啟動時無條件 `set_webhook`。
4. **F006（high/S）** `get_llm_client` / `get_minio_client` 回傳 None 造成下游 500 — 改拋 503 `SERVICE_NOT_CONFIGURED`。

**錯誤契約**
5. **F008（high/M）** OpenAI `input_audio` 傳入非法 `'ogg'`，OpenAI 供應商在語音評測主場景完全無法運作 — 先經 FFmpeg 轉碼 wav/mp3。
6. **F025（medium/S）** `get_card` 的「找不到筆記」判斷是死路徑，實際以 ValidationError 500 收場 — 先 `find_notes` 確認存在，回真正的 404。
7. **F033、F034、F035（medium/M）** MinIO/Anki client 例外捕捉不完整，部分錯誤繞過自訂錯誤契約 — 補齊 except 範圍。
8. **F098（low/S）** `response.choices[0]` 在 try 區塊外 — 移入錯誤邊界。
9. **F041（medium/S）** LLM 重試對 401/400 也盲重試 — 按狀態碼分類，不可重試錯誤直接拋出。

**併發與競態**
10. **F048（medium/M）** 錄音狀態非原子消費，並發語音造成重複評分與 lost update。
11. **F026（medium/S）** relation_type 正規化不一致 + check-then-insert 競態 — 寫入前統一正規化，捕獲 IntegrityError 回退。
12. **F044（medium/M）** Webhook 同步等待長任務，Telegram 重送造成重複處理 — 改背景任務 + 立即 ACK。

**超時**
13. **F039、F040（medium/S）** VOICEPEAK 與 FFmpeg 子程序皆無 timeout，掛死即協程永久阻塞 — `asyncio.wait_for` 包裝。

**資料庫**
14. **F009（high/M）** Alembic 初始遷移 ALTER 一張沒人建立的表，全新環境無法執行 — 補 baseline 遷移，部署改為先 `alembic upgrade head`。
15. **F036（medium/S）** 每次啟動無條件 `create_all` 與 Alembic 雙軌漂移 — `create_all` 僅限開發模式（與 F009 一起修）。
16. **F051（medium/S）** 關聯寫入失敗後未 rollback 共用 session，後續寫入連鎖失敗。

---

## 階段 3：測試與 CI/CD（與階段 2 並行）

> ✅ **完成（2026-07-09 第三輪建立基線、2026-07-11 第四輪 CI 接入）**：第二輪的 runtime 驗證已**沉澱為 repo 內自動化測試**——`backend/tests/`（48 個 pytest，全端點 smoke + F002 空列表防護 + F004/F005 fail-closed + Anki 跳脫 + Alembic baseline）+ `pytest.ini` + `requirements-dev.txt`，前端 `frontend/tests/` vitest 基線（11 個）。F054（eslint flat config）、F058（依賴相容區間）、F059（curl --fail）、F060（forwarded-allow-ips）、F062（HEALTHCHECK）、F130（detect-changes 過濾）一併收斂。**第四輪完成最後一哩**：`.github/workflows/main.yml` 的 backend-lint-test job 加 pytest 步驟（安裝 requirements-dev.txt，測試失敗即擋下 backend-docker 部署），frontend-build 加 vitest（npm test）+ eslint，pytest/vitest 已成為 docker 部署前置。詳見 [12_Implementation_Log.md](12_Implementation_Log.md) §9。

**F063 是全清單的放大器**：目前 CI 只有 ruff，映像通過 lint 即推 GHCR 並自動部署生產——F001 那種「方法定義整段損壞」都攔不住。

1. ✅ **F063（medium→實質 critical/L）** 建立 `backend/tests/` + pytest + pytest-asyncio：優先覆蓋 ① 全 API 端點 smoke test（TestClient + mock AnkiClient）② `sync_with_anki` 空列表防護 ③ `generate_card` 主路徑 ④ 純函數（`FfmpegMerger._build_command`、`_strip_markdown_fences`）。（**已完全完成**：第三輪 48 測試全綠，第四輪 pytest 已作為 backend-docker 部署前置接入 CI、測試失敗即擋下部署）
2. ✅ **F054（medium/S）** `npm run lint` 根本無法執行（ESLint 9 需要 flat config 但前端沒有任何 eslint 設定檔）— 補 `eslint.config.js`。（**已完成**）
3. ✅ **F126（low/M）** 前端零測試 — 建 vitest 基線。（**已完成 11 測試**）
4. ✅ **F058（medium/S）** 後端依賴全部 `>=` 無鎖版本，建置不可重現 — 改相容區間 `>=X,<Y`。（**已完成**）
5. ✅ **F059（medium/S）** 部署 webhook 的 curl 未加 `--fail`，部署失敗 CI 仍綠 — 加 `--fail`。（**已完成**）
6. ✅ **F062（medium/S）** 無 HEALTHCHECK — curl 與 `/api/health` 都已就緒，補 Dockerfile `HEALTHCHECK`。（**已完成／確認**）
7. ✅ **F060（medium/S）** uvicorn 缺 `--forwarded-allow-ips`，真實 IP 被忽略。（**已完成**）
8. ✅ **F130（low/S）** lint/build job 未用 detect-changes 過濾結果，任何 push 都全量執行。（**已完成**）

---

## 階段 4：死代碼清理（順手處理，全部 S）

> ✅ **大部分完成（第一輪 + 2026-07-09 第三輪）**：第一輪已完成 F030（Phase 1 遺留方法刪除）、F094（`SYNC_TIMEOUT` 改為真正使用）、F029（重複實作刪除）。**第三輪清除餘下死代碼**：✅ F115（6 個空殼 scaffold 套件 + `app/domain/`）、✅ F079–F082（死 schema）、✅ F090（死方法）、✅ F089（clean_html 死操作）、✅ F069（未用 logger）、✅ F135（未用 import os）、✅ F129（.vscode 設定填入）；F111/F125 確認前輪已清。**特例 F105**：`has_state` 已被上輪 F048/BugB 啟用，改判為活代碼。**殘留** F042（VoicepeakRunner/FfmpegMerger 仍零呼叫者，未接線）。詳見 [10_Implementation_Log.md](10_Implementation_Log.md)、[12_Implementation_Log.md](12_Implementation_Log.md)。

詳見 [07_Deprecated_and_Dead_Code.md](07_Deprecated_and_Dead_Code.md)。可安全刪除的分組：

| 組 | 內容 | 相關 id |
|----|------|---------|
| ✅ 空殼 scaffold | `backend/api|core|models|services|utils/`（各只有 `__init__.py`）與 `app/domain/`（**第三輪已刪除 6 個**） | F115 |
| ✅ Phase 1 遺留方法 | `generate_and_add_card`、`check_and_generate` 零呼叫端（**已刪除**） | F030 |
| 未接線模組 | `VoicepeakRunner`、`FfmpegMerger` 零呼叫者（**殘留**：仍無呼叫者，未接線；F008 走的是 audio_evaluator 端轉碼、非此模組） | F042 |
| ✅ 死 schema | `AnkiCardTemplate` 群、`RelationDef`、`CardRelationBatchDelete`、`MinioPresignedUrlRequest`、`PromptAudioItem`（**第三輪已刪除**） | F079–F082 |
| ✅ 死方法/常數 | `has_template`、`list_templates`（**已刪除**）、~~`SYNC_TIMEOUT`~~（✅ F094 已改為真正使用）、`has_state`（**改判為活代碼**）、`clean_html` 死操作（**已修**） | F090、F094、F105、F089 |
| ✅ 殘留物 | scaffold 註解、空 `.vscode/settings.json`（**已填入設定**）、`env.py` 未用 import、重複 import（**第三輪全數清理／確認**） | F125、F129、F135、F111 |
| ✅ 重複實作 | `delete_relations_by_note_id` 與 `delete_relations_for_note` 完全重複（**已刪除後者**） | F029 |

---

## 階段 5：重構（測試防線建立後）

> ✅ **完成度（2026-07-08 第一輪主體 + 2026-07-11 第四輪清尾）**：本階段已於第一輪重構大部分完成（下列 1、2、4、5、6、7 ✅ 完成，8 第三輪完成）；第 3 項（F022+F023）的殘餘快取已於**第四輪**補齊（TTL 快取 + 主動失效），至此本階段全數完成。注意：第一輪執行時階段 3 的測試防線尚未建立，改以「純搬移＋公開 API 不變＋py_compile / mypy / grep 交叉驗證」控制風險，詳見 [10_Implementation_Log.md](10_Implementation_Log.md)。

具體拆分方案見 [08_Refactor_Recommendations.md](08_Refactor_Recommendations.md)。優先序：

1. ✅ **拆分三大模組（L）**：`anki/client.py`（933 行）、`card_service.py`（763 行，F031 上帝類別趨勢）、`anki_model_manager.py`（617 行）。（**已完成**：anki/ 傳輸層＋六 Mixin、card_service 拆出 SpeakingService / schema_composer、anki_model/ 套件三職責分離）
2. ✅ **F021（medium/S）** `settings = Settings()` 模組級副作用 — 改 `@lru_cache get_settings()` 標準模式。（**已完成**）
3. ✅ **F022 + F023（medium/M）** `get_graph_data` Controller 內含業務邏輯且每請求全量掃描 Anki — 下沉 RelationService + 快取。（**已完成**：第一輪完成下沉，第四輪補 TTL(30s) 快取 + 寫入路徑主動失效）
4. ✅ **F046（medium/M）** ServiceInjectionMiddleware 每個 update 建立全套服務與 DB session — 改共享單例 + 按需 session。（**已完成**：實例級服務快取＋LLM 缺失時優雅降級；RelationService 因綁定 DB session 維持逐 Update 建立）
5. ✅ **F043（medium/S）** 評分 Prompt 在兩個 evaluator 間逐字重複 — 上提基底類。（**已完成**：prompts.py 共用＋Template Method 統一重試）
6. ✅ **F109 + F113 + F114（low/S）** 匯入腳本的複製貼上與載入 hack 統一。（**已完成**：scripts/_bootstrap.py）
7. ✅ **F134（low/M）** AnkiClient 25 處 `# type: ignore` — 隨拆分一併補型別。（**已完成**：`_invoke_typed` + TypeAdapter，25 處歸零）
8. ✅ **F083 + F084（low/S）** relation 批次寫入 N+1 與巨量 IN 參數。（**第三輪已完成**：F083 改批次註冊類型 + 單次 flush + 單一交易；F084 改 Python 端比對孤兒 + 每批 ≤900 的 IN 刪除，見 [12_Implementation_Log.md](12_Implementation_Log.md) §4）

---

## 階段 6：文檔同步（隨時可做）

> ✅ **完成（2026-07-09，第三輪）**：五組全數處理，詳見 [12_Implementation_Log.md](12_Implementation_Log.md)。

1. ✅ **F015（high/S）** 重寫 README — 原本整份描述不存在的 Flask/Redis/PostgreSQL 架構，已整份重寫為實際 FastAPI + aiogram 3 + SQLModel/Alembic + MinIO + React/Vite 架構。（**已完成**）
2. ✅ **F136–F140（low/S）** `docs/01` 目錄樹與容器圖過時 — 已依實際結構更新（前後端目錄樹、domain/ 空殼、DB SQLite→MySQL、CI 描述補全）。（**已完成**）
3. ✅ **F064、F065（medium/S）** Roadmap 標記完成的「基礎單元測試」不存在、目錄樹列出不存在的 `tests/` — 測試落地後如實更新為「已建立」並補上 `backend/tests/` 樹。（**已完成**）
4. ✅ **F141（low/S）** ADR 002 的資源釋放承諾與實際不符（LLMClient 從未關閉）— ADR 補「實作現狀」說明。（**已完成**）
5. ✅ **F070、F100、F131（low/S）** OpenAPI tags 補全、schema 語意修正、CI 註解修正。（**已完成**）

---

## 執行原則

1. **每修一項，補一個測試**：階段 0–2 的每個修正都是階段 3 測試清單的天然種子，防止同類回歸。
2. **fail-open 一律改 fail-closed**：本項目反覆出現「缺配置 → 靜默降級/放行」模式（F004、F005、F045、F053），統一改為「生產模式缺必要配置即拒絕啟動」。
3. **重構不與修 bug 混在同一個 commit**：階段 5 動大檔案前，先確認階段 3 的 smoke test 全綠。
4. **部署前後驗證資料庫**：階段 0 的 F003/F012 修完後，做一次「部署 → 寫資料 → 再部署 → 資料還在」的完整演練。
