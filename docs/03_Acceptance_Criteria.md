# Acceptance Criteria & Definition of Done (DoD)

為了確保 FluencyTides 的程式碼品質與架構一致性，所有模組在開發完成 (Done) 之前，必須符合以下驗收標準。

## 1. 雙端共用標準 (Clean Architecture Rules)
- **Controller 解耦：** `backend/app/api/` (Web 路由) 與 `backend/app/bot/` (Telegram 處理器) **絕對不允許**包含任何業務邏輯 (Business Logic)。
- **職責限制：** Controller 只能負責：
  1. 接收 Request。
  2. 交由 Pydantic Schema 進行型別驗證。
  3. 呼叫 `services/` 中的函數。
  4. 格式化 Response 並回傳。
- **違反此規則的 Pull Request 或是 Commit 將被拒絕。**

## 2. API 與後端驗收標準 (FastAPI & Documentation)
- **資料驗證：** 所有外部輸入 (Web 請求或 Telegram 訊息) 必須透過 Pydantic `BaseModel` 驗證。
- **文檔生成：** 新增的 REST API 必須具備清晰的 Docstrings，確保 FastAPI 能自動生成高品質的 OpenAPI 文檔 (Swagger/Redoc)。
- **統一錯誤處理 (Error Handling)：** 必須實作全域的 Exception Handler，回傳統一格式的 JSON (包含 `error_code`, `message`, `details`)，不允許將原始 traceback 直接暴露給前端。
- **依賴注入：** 對資料庫或外部服務的呼叫必須採用 FastAPI 的 `Depends()` 進行依賴注入，以利未來的單元測試與 Mock。

## 3. LLM 穩定性驗收標準 (LLM Ops)
- **結構化輸出 (Structured Output)：** 所有要求 LLM 返回資料的請求，必須明確限制輸出格式。利用 Prompt Engineering 或 Function Calling 強制約束。
- **防污染機制：** 接收到的 LLM 回應必須具備清除 Markdown 標籤 (例如 ````json ... ````) 的防護機制，確保能被 `json.loads()` 或 Pydantic 成功解析。
- **Timeouts & Fallbacks：** 呼叫外部 LLM API 時必須設定合理的 Timeout 時間，並在失敗時給予使用者明確的錯誤提示。

## 4. 前端工程化驗收標準 (React + TypeScript + UI)
- **型別對齊 (Type Safety)：** 前端 TypeScript 的 `Interfaces` 或 `Types` 必須與後端的 Pydantic Models 100% 對齊。若 API 欄位有更動，前端必須同步更新型別定義。
- **狀態管理：** 所有的非同步 API 請求都必須包含合理的 Loading 狀態提示 (Spinners / Skeletons) 以及 Error 處理 (Toast Notifications)。
- **UI 規範：** 介面開發需優先使用 `shadcn/ui` 元件庫與 Tailwind CSS 工具類別。禁止在 React 檔案中編寫大量行內樣式 (Inline Styles)。
- **效能考量：** `react-force-graph` 組件必須使用 `React.memo` 或適當的 Hooks (`useMemo`, `useCallback`) 來防止不必要的重複渲染，維持視覺流暢。
