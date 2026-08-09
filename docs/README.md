# FluencyTides 項目文檔索引（審查與實作系列，2026-07-07 起）

本目錄由 Claude Code 多代理審查工作流產生：11 個審查代理分工掃描全部子系統（後端核心 / API 層 / 服務層 / 資料與 AI 基礎設施 / Telegram Bot / 腳本與遷移 / 前端 / DevOps / 廢棄 API 掃描 / 文檔一致性），合併去重出 **141 條發現**（Critical 3、High 12、Medium 50、Low 76），其中 critical/high 逐條經對抗式驗證（懷疑論者立場覆核代碼），0 條被駁回。

**修復進度（四輪累計 134/141 已修復，另 F105 保留為活代碼、0 部分、5 暫緩、僅 1 條未處理）**：第一輪（2026-07-08）重構解決 31 條（巨型模組拆分 + 設計偏離），詳見 [10_Implementation_Log.md](10_Implementation_Log.md)；第二輪（2026-07-09）完成階段 0–2 修復 41 條 + 三方回歸審查發現並修復 10 個回歸 bug（不在原始 141 之列）+ 首次 runtime 驗證，詳見 [11_Implementation_Log.md](11_Implementation_Log.md)；第三輪（2026-07-09）建立測試基線（後端 48 + 前端 11 個測試，F063 解除）+ CI/CD + 死代碼清理 + 文檔對齊，修復 60 條，詳見 [12_Implementation_Log.md](12_Implementation_Log.md)；第四輪（2026-07-11）收尾遺留項——CI 接入 pytest/vitest（F063 最後一哩）、F023（graph 快取）與 F096（AnkiCardInfo）由部分升級為完全修復、webhook 原子性補償，詳見 [12_Implementation_Log.md](12_Implementation_Log.md) §9。僅餘 F042（零呼叫未接線模組）未處理。

## 文檔索引

| 文檔 | 內容 |
|------|------|
| [01_Project_Overview.md](01_Project_Overview.md) | 項目定位、技術棧、系統架構圖、目錄結構、健康度總評 |
| [02_Backend_Architecture.md](02_Backend_Architecture.md) | 後端分層架構、請求生命週期、資料模型、外部整合封裝 |
| [03_Frontend_Architecture.md](03_Frontend_Architecture.md) | 前端技術棧、頁面與路由、API 客戶端資料流、已知前端問題 |
| [04_API_Reference.md](04_API_Reference.md) | 全部 API 端點參考（以代碼為準）、認證方式、已知 API 缺陷 |
| [05_DevOps_and_Deployment.md](05_DevOps_and_Deployment.md) | 本地開發、Docker 部署、CI/CD 現狀、環境變數完整清單 |
| [06_Issues_and_Risks.md](06_Issues_and_Risks.md) | **核心成果**：141 條問題全清單（F001–F141），按嚴重度分節 |
| [07_Deprecated_and_Dead_Code.md](07_Deprecated_and_Dead_Code.md) | 廢棄用法與死代碼清單，每項標注可否安全刪除 |
| [08_Refactor_Recommendations.md](08_Refactor_Recommendations.md) | 巨型模組拆分方案、設計重構、測試策略（含範例代碼） |
| [09_Action_Plan.md](09_Action_Plan.md) | **執行路線圖**：六階段修正計劃（止血 → 安全 → 穩定 → 測試 → 清理 → 重構） |
| [10_Implementation_Log.md](10_Implementation_Log.md) | 第一輪重構實作紀錄：33 條發現的修正、差分與行為變化 |
| [11_Implementation_Log.md](11_Implementation_Log.md) | 第二輪：階段 0-2 修復 + 三方回歸審查 + runtime 驗證 |
| [12_Implementation_Log.md](12_Implementation_Log.md) | 第三輪：測試基線（48+11 測試）+ CI/CD + 死代碼清理 + 文檔對齊；§9 第四輪遺留項收尾（CI 接入測試、F023/F096 完全修復、webhook 原子性） |
| [13_Implementation_Log.md](13_Implementation_Log.md) | 多語言口說教練支援：Target_Language 欄位、FSM 語言選取、Evaluator 簽名重構 |
| [14_STT_Dual_Mode_Evaluator_Plan.md](14_STT_Dual_Mode_Evaluator_Plan.md) | STT 雙模式語音評分整合計畫（stt_diff / stt_llm）：可行性調查結論與設計方案 |
| [15_Bug_Scan_Report.md](15_Bug_Scan_Report.md) | 全項目 Bug 掃描報告（S001–S062：High 16 / Med 21 / Low 25），修復狀態追蹤 |

### 早期專案文檔（與上表共存，已於第三輪修正對齊實況）

| 文檔 | 內容 |
|------|------|
| [01_Architecture_and_Structure.md](01_Architecture_and_Structure.md) | 早期架構文件：C4 圖、時序圖、目錄樹（已對齊實際結構） |
| [02_Project_Roadmap_and_Progress.md](02_Project_Roadmap_and_Progress.md) | 開發階段 Roadmap 與進度追蹤 |
| [03_Acceptance_Criteria.md](03_Acceptance_Criteria.md) | 驗收標準 |
| [04_Telegram_Integration_Guide.md](04_Telegram_Integration_Guide.md) | Telegram 整合指南 |
| [adr/](adr/) | 架構決策紀錄 ADR 001–004（002/003 已補「實作現狀」說明） |

> 命名說明：審查系列（上表 01–12）與早期文檔（本表 01–04）編號各自獨立，以檔名區分。

## 最關鍵的風險（先看這裡）

1. ~~**F001** `GET /cards/models` 必然 500 —— `list_available_models` 方法定義遭破壞（[card_service.py:488](../backend/app/services/card_service.py)）~~ ✅ **已修復（2026-07-08）**，見 [10_Implementation_Log.md](10_Implementation_Log.md)
2. ~~**F002** `/sync` 在 Anki 空集合時會**清空整個關聯資料表**，且該資料無法重建~~ ✅ **已修復（2026-07-09，第二輪）**，見 [11_Implementation_Log.md](11_Implementation_Log.md)
3. ~~**F003 + F012** SQLite 預設路徑不在掛載卷內，**每次自動部署都會清空資料庫**~~ ✅ **已修復（2026-07-09，第二輪）**，見 [11_Implementation_Log.md](11_Implementation_Log.md)
4. ~~**F004 + F005** 認證全面 fail-open：密鑰未設定時 API 與 Telegram Webhook 完全開放~~ ✅ **已修復（2026-07-09，第二輪）**，見 [11_Implementation_Log.md](11_Implementation_Log.md)
5. ~~**F063** 後端零測試，CI 通過 lint 即自動部署生產 —— 以上所有問題的共同放大器~~ ✅ **已完全修復**：第三輪（2026-07-09）建立後端 48 + 前端 11 個自動化測試，第四輪（2026-07-11）將 pytest/vitest 接入 CI 作為 docker 部署前置（測試失敗即擋下部署），見 [12_Implementation_Log.md](12_Implementation_Log.md) §9

上述最關鍵風險已全部修復。四輪後僅餘 F042（零呼叫未接線模組，需產品決策）未處理 + 5 暫緩（部分修復已於第四輪清零），詳見 [06_Issues_and_Risks.md](06_Issues_and_Risks.md) 與各實作紀錄。第四輪已完成 CI 接入 pytest/vitest、F023/F096 完全修復、webhook 原子性補償（見 [12_Implementation_Log.md](12_Implementation_Log.md) §9）。
