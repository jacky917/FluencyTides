# 11. 實作紀錄（第二輪）— 階段 0–2 修復 + 回歸驗證

產生日期：2026-07-09（由 Claude Code 依 [09_Action_Plan.md](09_Action_Plan.md) 執行）

本文檔記錄第二輪修正：處理 [09_Action_Plan.md](09_Action_Plan.md) 的**階段 0（止血）、階段 1（安全加固）、階段 2（穩定性）**，並針對「兩輪多代理修改是否互相產生新 bug」做了**三方對抗式回歸審查**與**真實環境 runtime 驗證**。與第一輪（[10_Implementation_Log.md](10_Implementation_Log.md)）合計，141 條發現中已修復 **72 條**（另 2 條部分修復、5 條暫緩）。

## 1. 成果總覽

| 指標 | 數值 |
|------|------|
| 本輪解決的原始發現 | **41 條**（階段 0/1/2 的 bug、安全、config、部分 design 類） |
| 回歸審查發現並修復的**新 bug** | **10 個**（2 critical/high 級真實回歸 + 8 中低，不在原始 141 之列） |
| 修改/新增檔案 | 後端 21 + 前端 4 + 部署 5 + 新增 baseline 遷移 1 |
| 兩輪累計已修復 | **72 / 141 條**（第一輪 31 + 第二輪 41）＋ 2 部分修復（F023、F096）＋ 5 暫緩（F072、F075、F093、F099、F101） |
| 尚未處理 | 62 條（主要為階段 2 殘留、階段 3 測試、階段 4/5 剩餘清理與重構） |

### 關鍵里程碑：這是第一次有真實環境驗證

前一輪受限於本機無依賴，只能靠 `py_compile`。本輪建立了 **Python 3.11 venv + 完整依賴**，得以做端到端驗證：

- ✅ `app.main` 完整啟動，`TestClient` 生命週期跑通（startup singleton 初始化 → shutdown 資源清理）
- ✅ `GET /api/health` → 200
- ✅ `GET /api/v1/cards/models` → **200**（回 9 個模型；這正是修復 F001 + Bug 1 的雙重確認——第一輪修好方法簽名、本輪修好 llm gate 誤擋）
- ✅ `GET /api/v1/relations/graph` → 200、OpenAPI schema 正常生成
- ✅ Alembic baseline 遷移在全新 DB 套用成功（`alembic upgrade head` → `card_relations` + `relation_types` + `alembic_version` 三表）
- ✅ fail-closed validator 行為驗證：生產模式空 `API_SECRET_KEY` 被拒絕啟動、開發模式放行
- ✅ Anki 查詢跳脫 `escape_anki_search_value` 行為驗證（引號/反斜線正確跳脫）
- ✅ 前端 `tsc -b` → 通過，且不再產出 `vite.config.js/.d.ts`

驗證過程本身還撈出一個真實缺漏：`greenlet`（SQLAlchemy async 執行期必要依賴）未列在 requirements.txt，已補上。

---

## 2. 階段 0：止血

| finding | 修法 | 驗證 |
|---------|------|------|
| **F002**(critical) | `sync_with_anki` 開頭加空列表防護：`valid_note_ids` 為空時記 warning 並 return 0，杜絕「一句 /sync 清空整個關聯表」 | 代碼審查確認防護在刪除邏輯之前 |
| **F009**(high) | 新增 baseline 遷移 `7f3d1a2b4c5e`（手寫 `create_table(card_relations)` + 索引），`9bbc72f7c470` 的 down_revision 改指向它，全新環境 `alembic upgrade head` 不再失敗 | ✅ 全新 DB 遷移實測通過 |
| **F003**(critical) | `.env.example` 的 DATABASE_URL 預設改 `sqlite+aiosqlite:////app/data/fluencytides.db`（掛載卷內）；compose 用 named volume | — |
| **F012**(high) | compose 資料改 named volume（繼承映像內 chown 過的 `/app/data` ownership），附繁中遷移註解（舊 bind mount 資料以 `docker cp` 搬移） | — |
| **F013**(high) | 共用網路改由後端建立（`name: fluencytides_net`，移除雙 external），前端保持 external | — |
| **F010**(high) | `git rm` vite.config.js/.d.ts；tsconfig.node.json 解決 composite/noEmit 衝突（產物導向 node_modules 快取）；.gitignore 封鎖 | ✅ tsc -b 通過且無殘留產物 |
| **F011**(high) | 第一輪已改二段式刪除；本輪確認 isDeleting 改用 `deleteMutation.isPending`，無殘留問題 | — |

## 3. 階段 1：安全加固（fail-closed）

核心設計：**所有「缺配置 → 靜默放行」改為 fail-closed，但只在生產模式（`ENVIRONMENT=production`）生效，開發模式行為完全不變**。新增設定 `ENVIRONMENT`（預設 development）與 `is_production` property。

| finding | 修法 |
|---------|------|
| **F004**(high) | config validator：生產模式且 `API_SECRET_KEY` 為空 → 拒絕啟動；auth.py 無密鑰時生產一律 403 | 
| **F005**(high) | config validator：生產模式 webhook 已設但 secret 為空 → 拒絕啟動；webhook.py 無密鑰即 403（原放行） |
| **F007**(high) | Bot 全部動態內容（使用者輸入、LLM 輸出、卡片欄位、Anki Prompt 原文）插入 HTML 前 `html.quote()`（messages/commands 25 處 + voice 進度訊息） |
| **F049+F068** | webhook secret 與 API Key 比對改 `hmac.compare_digest`（常數時間），移除日誌密鑰片段 |
| **F044**(medium) | webhook 改背景 ACK（`asyncio.create_task` + 立即回 200），長任務不再觸發 Telegram 重送重複處理 |
| **F020**(medium) | MinIO 憑證預設 minioadmin → `None`，minio_client 初始化加明確 None 防護 |
| **F024**(medium) | 上傳端點加大小上限（`STORAGE_MAX_UPLOAD_MB=50`，分塊累計超限 413）、副檔名/Content-Type 白名單（415）、prefix 正則（422） |
| **F032**(medium) | 客戶端檔名 `sanitize_filename()` 白名單過濾後才用於暫存檔與物件名 |
| **F061**(medium) | compose 移除 `8000:8000` 埠映射，後端只經 nginx 反代出口 |
| **F071/F085/F086/F112** | anki 套件新增 `escape_anki_search_value()`，四處查詢拼接點（graph/語音/查重/腳本）全部收斂 |

## 4. 階段 2：穩定性

| finding | 修法 |
|---------|------|
| **F018**(medium) | Bot 啟動包 try/except，失敗降級 `bot=None` 不阻其餘 API |
| **F016**(medium) | polling task `add_done_callback` 記錄異常；shutdown try/finally 確保資源清理 |
| **F017**(medium) | 啟動時無條件 `set_webhook`（冪等），secret 輪換必重綁 |
| **F006**(high) | `get_llm_client`/`get_minio_client` 為 None 時 raise `ServiceUnavailableError`(503) 統一契約 |
| **F008**(high) | OpenAI 音訊格式：wav/mp3 直傳，ogg/opus 等經 ffmpeg 轉碼 wav（超時 kill + 明確錯誤），不再傳非法 format |
| **F036**(medium) | `create_db_and_tables` 僅非生產模式執行（生產走 Alembic），消除 schema 雙軌漂移 |
| **F039+F040** | VOICEPEAK(120s)/ffmpeg(60s) 子程序 `asyncio.wait_for` 超時保護 |
| **F048**(medium) | 錄音狀態原子消費（`pop_state`），並發語音不再重複評分/lost update |
| **F098**(low) | `response.choices[0]` 移入錯誤邊界 |
| **F025**(medium) | `get_card` 先 `find_notes` 確認存在，回真正的 404（非 500） |
| **F026**(medium) | relation_type 統一正規化 + get_or_create 捕獲 IntegrityError 回退 |
| **F051**(medium) | 關聯寫入失敗補 `session.rollback()`，不再連鎖失敗 |
| **F033+F034+F035** | MinIO 與 Anki transport 錯誤契約補齊（擴大 except 範圍、file_size 修正） |
| **F050**(medium) | alembic env.py 的 DATABASE_URL `%` 轉義為 `%%`，避免 ConfigParser 插值錯誤 |
| **F067** | startup 中途失敗時 AnkiClient 連線池與 DB engine 確保關閉（實測失敗路徑觸發了清理） |

---

## 5. 回歸審查：兩輪修改交互產生的新 bug

因為多個檔案被兩輪、多個代理先後修改（`main.py`、`config.py`、`voice.py`、`transport.py`、`card_service.py` 等），我派了**三個對抗式回歸審查代理**（Bot 流程 / 啟動與資料層 / 前端與部署），專門找「兩輪修改互相衝突或產生的新 bug」。結果找到 10 個真實問題，全部已修復。

### 真實回歸（修改交互直接造成）

| # | 嚴重度 | 問題 | 修法 |
|---|--------|------|------|
| **Bug 1** | 🔴 critical | 第二輪 `get_llm_client` 的 503 raise（F006）與第一輪「CardService 容忍 llm=None」設計**直接衝突**——LLM 未設定時所有 `/cards` 唯讀端點誤回 503 | 新增 `get_llm_client_optional`（回 None 不 raise）供 card_service；嚴格版留給真正需 LLM 的鏈。**實測 `/cards/models` 由 503 恢復為 200** |
| **Bug 3** | 🟠 high | 第二輪重排 lifespan 後，`create_bot()` 落在 F018 降級 try **之外**，token 格式錯就讓整個 API 拒絕啟動 | 移進內層 F018 try，失敗走既有降級 |
| **Bug A** | 🟠 medium | F044 背景 ACK 後，背景任務在 shutdown 時無人 await，重啟會砍半正在跑的評分（已回 200，update 永久遺失） | webhook 匯出 `wait_for_background_tasks`，main.py shutdown 於關閉資源前呼叫 |

### 修一半的漏網（宣稱範圍內但掃描遺漏）

| # | 嚴重度 | 問題 | 修法 |
|---|--------|------|------|
| **Bug 2** | 🟠 medium | `_invoke_typed` 的 `TypeAdapter.validate_python` 在 try 外，回應異形時拋裸 ValidationError 逃出 F035 錯誤邊界 → 未處理 500 | 包 try/except 轉 AnkiConnectError |
| **Bug 4** | 🟡 medium | F025 只修 get_card；update/delete 對不存在 note 仍回 502/靜默 200，與端點宣告的 404 不符 | update/delete 補存在性檢查回 404 |
| **Bug D** | 🟡 low | F007 掃描漏掉 voice 進度訊息：Anki `Prompt` 欄位原文（含 `<br>`）直插 HTML 會拋 TelegramBadRequest 中止評分 | update_progress 對 msg 整串 `html.quote` |
| **Bug E** | 🟡 low | F085 只修 speaking_service；commands.py 的 `Card_ID:` 查詢未跳脫，行為分裂 | 兩處補 `escape_anki_search_value` |

### 新引入的邊角問題

| # | 嚴重度 | 問題 | 修法 |
|---|--------|------|------|
| **Bug B** | 🟡 low | voice.py 狀態歸還可能用倒數中的舊卡狀態覆蓋使用者新建立的狀態（pop 與歸還間有 await 窗口） | 歸還前 `has_state` 檢查，僅無狀態時才歸還 |
| **Bug C** | 🟡 low | `status_msg = reply()` 在狀態保護 try 之外，reply 失敗則狀態已消費且零回饋 | reply 納入 try + 歸還路徑 |
| **前端 A** | 🟡 medium | CardDetailModal 的 `useEffect([cardDetail])` 在視窗重聚焦 refetch 時無條件覆寫表單，**清空未儲存編輯** | 依賴改 `[cardDetail?.note_id]`，只在切換卡片時同步 |
| **前端 B** | 🟡 low | `['card', noteId]` 快取從不失效——刪除留殭屍、更新閃舊資料 | update invalidate / delete removeQueries |

### 順帶處理的既有發現

回歸審查重新發現了原始清單中尚未修的 **F014**（high，nginx 靜態容器名 DNS：後端不存在則 nginx 啟動失敗、重建後 502）——因埠移除後 nginx 成為唯一入口而風險放大，本輪一併修復：改用 Docker 內建 DNS（`resolver 127.0.0.11` + 變數 proxy_pass），並順帶 F132（`/api/` 加 `proxy_read_timeout 300s` 防 LLM 長請求 504）。另修 CasaOS WebUI 死連結（8000 埠移除後改指向前端）。

---

## 6. 已知遺留與設計權衡（下一輪）

1. **生產 validator 與 Alembic 的張力**（回歸審查 Issue 5）：`settings = get_settings()` 於 import 時實例化，`alembic/env.py` import 它——生產模式下跑遷移的環境若未帶 `API_SECRET_KEY` 會在 import 期 ValidationError 中止，而 F036 又規定生產 schema 只能靠 Alembic。**緩解**：部署管線的遷移步驟需提供應用密鑰，或以 `ENVIRONMENT=development` 單獨執行 schema 遷移。未改 validator 語意（改動風險大於效益），列為已知限制。
2. **webhook 半套狀態**：`speaking_service._persist_recording` 的「存 media + 寫回 Recordings 欄位」兩步仍非原子——極端情況（背景任務逾 30s 被 shutdown 等待逾時後仍砍）可能留下 media 已存但欄位未寫。
3. **階段 3（測試與 CI/CD）尚未開始**：F063 零測試仍是最大結構性風險。本輪雖建立了臨時 venv 做 runtime 驗證，但這些驗證**未沉澱為 repo 內的自動化測試**——強烈建議下一輪把本文檔 §1 的驗證清單固化為 `backend/tests/` 的 pytest smoke test。（✅ **第三輪已處理**：驗證清單已固化為 `backend/tests/` 48 個 pytest + 前端 vitest 11 個，F063 零測試風險解除；CI job 接入為唯一待辦，詳見 [12_Implementation_Log.md](12_Implementation_Log.md)。）
4. **9bbc 遷移的 SQLite 方言 server_default**（F052）、**其餘階段 2 未列項**、**階段 4/5 剩餘清理與重構**仍待處理。（✅ **第三輪已處理大部分**：F052 改用 `sa.func.now()`、階段 4 死代碼清理與 F083/F084 批次寫入已收尾，詳見 [12_Implementation_Log.md](12_Implementation_Log.md)；殘留僅本節第 2 點的 webhook 半套原子性。）

## 7. 驗證方法與限制

- **runtime 驗證**：Python 3.11 venv + requirements.txt 完整安裝（+ greenlet），透過 `TestClient` 驗證啟動、健康、關鍵端點、遷移、validator、跳脫函數；前端 `npm install` + `tsc -b`。
- **靜態驗證**：全後端 `py_compile` 通過；三方回歸審查逐檔細讀 + git diff 對照 + 呼叫鏈 grep。
- **限制**：無真實 AnkiConnect / MinIO / Telegram / LLM 服務，故涉及外部 IO 的路徑（實際卡片生成、語音評分端到端、webhook 真實 update）僅驗證到「錯誤契約正確、不崩潰」的層次，未做真服務串接。Bot 錄音流程與 Docker 部署拓撲（named volume 遷移、nginx 動態解析）建議在具備服務的環境再人工走一次。
