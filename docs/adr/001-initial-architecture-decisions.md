# ADR 001: Initial Architecture and Technical Constraints

## Status
Accepted

## Date
2026-06-02

## Context
FluencyTides 是一個全端應用服務，旨在利用 LLM 自動生成 Anki 學習卡片。
本專案面臨兩個主要挑戰與需求：
1. **雙端介面：** 系統必須同時支援 Web 前端與 Telegram Bot 作為操作入口，且兩端應具備完全相同的核心功能（生成卡片、查詢進度等）。
2. **快速交付與敏捷開發：** 為了盡快驗證核心價值（LLM 結合 Anki），我們需要在初期保持架構簡單，避免過度工程 (Over-engineering)。

## Decision
經過技術負責人與架構師的討論，我們做出以下重大架構決策：

1. **採用 Controller-Service 分層架構 (雙端共用邏輯)**
   我們將強制實施 Clean Architecture 的邊界。Web API (FastAPI Router) 與 Telegram Handler 皆被視為純粹的「介面層 (Delivery Mechanism)」。它們的職責僅限於接收請求與資料驗證 (透過 Pydantic)，並統一呼叫內部的 Service 層。**業務邏輯只允許存在於 Service 層中**。

2. **暫不引入 Message Queue (MQ)**
   考量到 LLM 請求可能耗時較長，傳統上會引入 MQ (如 Celery, RabbitMQ) 來進行非同步任務處理。但為了保持架構輕量化，我們決定在初期**直接採用 FastAPI 的非同步 (async/await) 處理**，搭配前端的 Loading 狀態與 Telegram 的長時間等待提示，來處理 LLM 請求。

3. **暫不實作 RBAC (Role-Based Access Control) 權限管理**
   專案初期為單一使用者或信任小群體 (Personal/Small Team usage) 設計，無需複雜的角色權限。我們僅實作基礎的認證 (Authentication) 或是環境變數 Token 驗證，省略完整的 RBAC 模組。

## Consequences

**Positive (優勢):**
- **高內聚低耦合：** Controller 與 Service 的強制分離，使得未來若要新增第三個介面 (例如 CLI 或 Discord Bot) 將變得極為容易，且程式碼不會重複。
- **降低維運成本：** 不使用 MQ 與複雜的 RBAC，減少了需要部署與監控的基礎設施 (不用架設 Redis/RabbitMQ Worker)，加快了開發迭代速度。

**Negative / Risks (風險與影響):**
- **Timeout 風險：** 由於缺乏 MQ，當面臨大量 LLM 並發請求或 LLM 服務延遲時，HTTP Request 可能會觸發 Timeout。
  - *Mitigation (緩解方案):* 我們會在前端實施合理的 Request Timeout 攔截與重試機制；並在必要時透過 Telegram 告知使用者「正在處理中」。
- **未來升級成本：** 當系統擴展為多人 SaaS 服務，或需要批次生成數千張卡片時，同步的 async 架構將遇到瓶頸。
  - *Future Plan (未來計畫):* 若卡片生成量達到系統瓶頸，我們將輕易地把 Service 層的方法改為發布至 MQ 的 Task，因為目前的架構已保證業務邏輯被完整封裝，重構成本極低。
