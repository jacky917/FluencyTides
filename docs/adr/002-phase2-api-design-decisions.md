# ADR 002: Phase 2 後端 API 與服務層架構設計決策

## 狀態
**已接受 (Accepted)** - 2026-06-02

## 背景
在 Phase 1 中，我們建立了基礎設施層的骨架 (AnkiClient, LLMClient, MinioClient)。進入 Phase 2 (Backend Core)，我們需要實作核心的業務邏輯層 (Service) 與介面層 (Controller/API)，將整個「生成結構化 JSON 並發送至 Anki」的流程串接起來，並為未來的 Telegram Bot 預留共用介面。

為確保符合 Clean Architecture 原則與 03_Acceptance_Criteria.md 中的規範，我們必須確立依賴注入、錯誤處理、系統提示 (System Prompt) 管理與 API 認證等架構標準。

## 決策內容

### 1. 依賴注入與生命週期 (Lifespan & Dependency Injection)
- **決策**：所有 Infrastructure Client（AnkiClient, LLMClient, MinioClient）作為 Singleton 實例，在 FastAPI 的 `lifespan` 啟動階段初始化並存入 `app.state`；在關閉時統一釋放資源（如 `httpx.AsyncClient` 連線池）。
- **決策**：Service 層（CardService, AnkiModelManager 等）透過 FastAPI 的 `Depends()` 依賴注入，每次請求建立新實例，並注入對應的 Infrastructure Singleton。
- **原因**：確保網路連線資源高效重用，同時保持 Service 層的無狀態特性，避免不同請求間的狀態污染。

### 2. 全域異常處理 (Global Exception Handling)
- **決策**：建立統一的業務例外繼承體系 `FluencyTidesError`。所有基礎設施的原生錯誤（如 `AnkiConnectError`）必須在 Service 層被捕捉，並重新拋出為具備明確業務語意（如 `DuplicateCardError`, `DeckNotFoundError`）的自訂例外。
- **決策**：在 `main.py` 中註冊 `@app.exception_handler(FluencyTidesError)`，將所有業務錯誤統一格式化為 `ErrorResponse` (包含 `error_code`, `message`, `details`) 回傳給客戶端。
- **原因**：嚴格遵守 Acceptance Criteria 中「不允許將原始 traceback 直接暴露給前端」的規定，並使 Controller 層代碼保持乾淨（無需到處寫 try-except）。

### 3. API Key 認證機制
- **決策**：使用基於 `X-API-Key` Header 的認證機制保護所有 `/api/v1/` 路由。
- **決策**：開發環境便利性：若 `.env` 未設定 `API_SECRET_KEY`，系統自動跳過認證。
- **原因**：目前的後端主要提供給第一方前端 SPA 與後端自己的 Telegram Bot 使用，不需實作複雜的 OAuth2 / JWT 流程。Header 認證足夠輕量且安全。

### 4. 系統提示詞管理 (System Prompt Management)
- **決策**：引入 Jinja2 作為模板渲染引擎，將 Prompt 從程式碼抽出為獨立的 `.j2` 檔案。
- **決策**：輸入與輸出約束分離：Prompt 的指令（輸入）由 Jinja2 處理；輸出的 JSON 結構約束，則繼續依賴 `anki_models/` 目錄下的 JSON Schema 定義檔。
- **原因**：使用者提出希望透過 Jinja2 管理 Prompt 的需求，這有利於處理更複雜的生成情境，並避免將長篇幅的字串硬編碼在 Python 中。將 Schema 獨立保留在 JSON 中，則避免了維護 Prompt 時誤改結構的風險。

## 影響
- **正面**：
  - API 層（Controller）達成了真正的零業務邏輯，有利於後續直接重用 `CardService` 在 Telegram Bot 開發上。
  - Pydantic V2 的廣泛使用消除了 Any，保證了邊界資料的安全。
  - Jinja2 讓 Prompt 的優化與 A/B 測試更為獨立且靈活。
- **負面**：
  - 引入 Jinja2 增加了些微的系統複雜度。
  - 開發者需要學習自訂例外的拋出與對應的 `error_code`，而不是簡單地 `raise Exception`。
