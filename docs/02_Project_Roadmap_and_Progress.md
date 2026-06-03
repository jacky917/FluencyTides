# Project Roadmap and Progress

這份文件用於追蹤 FluencyTides 的開發進度。我們將專案拆分為四個主要階段 (Phases)，每個階段都有明確的任務 (Tasks)。

## Phase 1: 基礎骨架、舊程式碼重構與 CI/CD
**目標：** 搭建穩固的前後端開發環境，將舊專案的核心 Anki 工具類遷移並現代化，確保 API 基礎可通。

- [x] 建立並定義核心架構文件與 ADR (Architecture Decision Records)。
- [x] 初始化 FastAPI 後端專案 (導入 Pydantic V2, 配置 `main.py` 與基礎資料夾)。
- [x] **[核心遷移]** 完整讀取並分析舊專案的 Anki 核心工具類與卡片生成邏輯還有語音生成。目錄: `old`
- [x] **[代碼重構]** 將舊工具類重構至新專案中。
  - **注意：請勿拘泥於舊代碼的文件切分方式。**
  - 請根據 FluencyTides 的新架構需求，重新進行函數職責拆分與合併。
  - 將純邏輯處理、API 呼叫與資料驗證嚴格解耦，放入對應的 `infrastructure/`、`utils/` 或 `services/` 目錄。
  - 確保符合非同步 (Async) 規範、補齊 Docstrings，並排除不適用的陳舊邏輯。
  - [x] **[基礎設施層補漏]** Phase 1 遺留的 Infrastructure 層完整遷移：
    - MinIO Client 重構：補齊 download/delete/list/set_policy + Pydantic 回傳值 + 自訂異常 `MinioStorageError`
    - VoicePeak Runner 重構：完整參數支援 (emotions/speed/pitch/volume) + 環境變數隔離 + Pydantic 入參
    - FFmpeg Merger 遷移：從 `old/` 遷移至 `infrastructure/ffmpeg/`（獨立子套件）+ 非同步化
    - 新增 `schemas/storage.py`（MinioUploadResult, MinioObjectInfo, MinioBucketPolicy）
    - 新增 `schemas/voice.py`（VoicepeakSynthesisRequest/Result, FfmpegMergeRequest/Result）
    - `core/config.py` 擴充：VOICEPEAK_EXECUTABLE_PATH / DEFAULT_NARRATOR / CHARACTERS_CONFIG_PATH / MINIO_DEFAULT_BUCKET
    - 新增 `backend/.env.example` 環境變數配置範例
- [x] 初始化 React + Vite 前端專案 (安裝 Tailwind CSS 與 shadcn/ui 基礎配置)。
- [x] 實作前後端 Health Check 介面，確保本地開發環境與跨域 (CORS) 設定無誤。
- [x] 建立基礎的 CI/CD Pipeline 腳本 (Linting & 基礎單元測試)。

## Phase 2: 核心 LLM 與 Anki 服務 (Backend Core)
**目標：** 完善後端的核心業務邏輯，確保 LLM 能產出穩定 JSON，並與重構後的 Anki 工具類無縫對接。

- [x] 實作 `infrastructure/llm_client`，套用 `llm-structured-output` 技巧，確保返回純淨的 JSON。
- [x] 擴充 `infrastructure/anki_client`，基於 Phase 1 遷移的基礎，完善 Anki Connect 的本地端呼叫 (例如 `addNote`, `guiBrowse`)。
- [x] 實作 `services/card_service.py` 核心邏輯 (串接 LLM 生成內容 -> 呼叫重構後的工具類 -> 送入 Anki)。
- [x] 實作 `api/` 下的 REST API 路由，透過 Swagger UI 完成卡片生成的完整流程測試。
- [x] 實作 MinIO 的基礎存取服務 (上傳與讀取媒體檔)。

## Phase 3: Telegram Bot 整合 (Double-end Controller)
**狀態：** 完成 ✅

- [x] 在 `bot/` 目錄下建立 Telegram Webhook / Polling 基礎設施。
- [x] 實作 Telegram 訊息解析器，將指令轉換為後端通用的 Pydantic Model。
- [x] 在 Bot Controller 中注入 `card_service.py`，實現透過 Telegram 發送字詞即刻生成卡片。
- [x] 實作 Telegram Deep Links 整合，設計流暢的端對端跳轉體驗。

## Phase 4: React 知識圖譜與 UI (Frontend Engineering)
**狀態：** 完成 ✅

- [x] 根據後端 Swagger UI，建立前端 `src/api` 的請求封裝，並嚴格定義 TypeScript 型別 (`src/types`)。
- [x] 實作非同步請求的 Loading / Error 狀態共用組件 (結合 shadcn/ui 的 Skeleton 或 Toast)。
- [x] 整合 `react-force-graph`，將使用者的學習資料視覺化為知識圖譜。
- [x] 實作卡片管理與預覽 UI，套用 Tailwind 的精美排版與設計規範。
- [x] 全端聯調與效能優化 (避免不必要的渲染與過長的等待時間)。

## Phase 5: 知識圖譜資料庫地基與關聯服務 (Database & Relations)
**狀態：** 完成 ✅
**目標：** 引入專屬資料庫維護卡片關聯（Card Relations），解決 Anki 無法支援多對多複雜圖譜與孤兒節點的問題。

- [x] **[ORM 地基建設]** 引入 `SQLModel` 與 `SQLAlchemy 2.0 AsyncEngine`。
  - [x] 嚴格實踐 MySQL 相容性準則（顯式約束命名、VARCHAR 長度、時區安全）。
  - [x] 設計 `CardRelation` 資料表模型，支援 `source_label` 等冗餘欄位。
  - [x] 整合 FastAPI Lifespan 管理連線池與依賴注入 (`get_db_session`)。
  - [x] 配置 `Alembic` 用於非同步 Schema 遷移與版本控管。
- [x] **[CRUD Service 與 API]**
  - [x] 在 `services/` 下實作 `RelationService`（包含關聯建立、連動刪除、批次查詢）。
- [x] 在 `api/` 下新增 `relations.py` Router。
  - [x] 修改 `CardService`，在透過 LLM 生成卡片後，解析 `Synonyms_JSON` 等欄位並同步寫入資料庫關聯表。
- [x] 整合前端與 TG 的關聯增刪改查邏輯，達成前後端圖譜資料雙向同步。

## Phase 6: 前端知識圖譜交互與 RUD 整合
**狀態：** 完成 ✅
**目標：** 完善知識圖譜的互動性，支援直接在圖譜上點擊節點進行卡片資料的讀取、更新與刪除。

- [x] **[Backend]** 於 `CardService` 中實作 `get_card`, `update_card`, `delete_card`，並支援更新時自動同步修改 `RelationService` 中的冗餘資料 (`source_label`)。
- [x] **[Backend]** 新增 `/api/v1/cards/{note_id}` 的 GET/PUT/DELETE 端點，確保 REST API 接口完整。
- [x] **[Frontend]** 實作 `CardDetailModal` 彈出視窗組件，讓使用者可直觀地編輯 Anki 欄位。
- [x] **[Frontend]** 整合圖譜點擊事件，串接 CRUD API，並於變更後觸發畫面更新。

## Phase 7: 圖譜交互增強與手動連線 (Manual Relations)
**狀態：** 完成 ✅
**目標：** 在知識圖譜介面中，允許使用者透過最直觀的「點擊連線 (Select & Connect)」方式，手動建立任意兩張卡片之間的同義詞或搭配詞關聯。

- [x] **[API]** 前端 `client.ts` 實作 `createRelation` 端點綁定。
- [x] **[UI]** 實作連線模式狀態機 (Link Mode)，引導使用者依序選擇 Source 與 Target 節點。
- [x] **[Canvas]** 在連線模式中，對已選取的 Source 節點繪製藍色光圈特效以提示狀態。
- [x] **[Modal]** 實作極簡的 Relation Type 選擇視窗，選擇後立即呼叫 API 寫入資料庫，並自動重繪圖譜。

## Phase 8: Speaking_Coach_Dark 專屬工作流與語音評分
**狀態：** 完成 ✅
**目標：** 為 `Speaking_Coach_Dark` 卡片類型量身打造專屬的 Telegram 互動工作流，包含生卡、語音評分與資料刪除。

- [x] **[架構]** 引入策略模式 (Strategy Pattern) 與工廠模式 (Factory Pattern) 重構 Audio Evaluator，支援 `openai` 與 `gemini_native` 兩種供應商，實現完美的依賴反轉。
- [x] **[Schema]** 定義 `Speaking_Coach_Dark` 的專屬 Pydantic 模型 (RecordingItem, ReferenceItem)，確保與前端 HTML 解析的 JSON 完全一致。
- [x] **[State Machine]** 實作 Telegram In-Memory 狀態機，追蹤使用者的多步驟操作 (例如 Workflow B 的錄音流程)。
- [x] **[Workflow A]** 實作 `/newcard` 指令，接收 JSON Payload 並無狀態建立新卡片。
- [x] **[Workflow B]** 整合 Anki 卡片上的 Deep Link，攔截 `/start rec_`，接收語音訊息，並交由 Audio Evaluator 進行 AI 評估，最後將 Base64 音檔與評分寫回 Anki。
- [x] **[Workflow C]** 攔截 `/start del_`，解析索引並自動刪除 Anki 卡片中對應的 References 或 Recordings 條目。