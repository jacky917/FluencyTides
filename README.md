# FluencyTides

FluencyTides 是一套**個人語言學習自動化系統**，把「LLM 內容生成 → Anki 間隔重複記憶 → 語音影子跟讀評測」串成一個閉環。系統以 **FastAPI** 為中樞，單一進程同時服務三類客戶端：

- **React Web 前端**：卡片生成、知識圖譜視覺化與卡片管理。
- **Telegram Bot**：文字即刻生卡、Deep Link 跳轉、語音影子跟讀 AI 評分。
- **CLI 維運腳本**：JSON 批次匯入、LLM 批次生成、Deep Link 批次更新。

> 本 README 描述的是**當前實際代碼狀態**（FastAPI 專案）。若在舊版文檔或歷史記錄中看到 Flask / Redis / PostgreSQL 等描述，均為早期規劃殘留、從未實作，請以本文與 `docs/` 下的審查文檔為準。

---

## 核心功能

| 功能 | 說明 | 主要入口 |
|---|---|---|
| **LLM 生成 Anki 卡片** | 依所選模型讀取 LLM JSON Schema 與 Jinja2 System Prompt，呼叫 OpenAI 相容 LLM（實務上為 Gemini 相容層）取得結構化 JSON，經 AnkiConnect 寫入本地 Anki | Web `POST /api/v1/cards/generate`、Telegram 任意文字訊息、CLI 批次腳本 |
| **知識圖譜** | LLM 生卡時同步輸出 `Graph_Relations` 關聯，寫入 SQLite；前端以 react-force-graph-2d 力導向圖呈現，支援連線建立/刪除關聯與卡片詳情編輯 | `GET /api/v1/relations/graph`、前端 `KnowledgeGraph.tsx` |
| **Telegram 語音影子跟讀評測** | 卡片內嵌 Deep Link（`rec_` 前綴），使用者錄音後由 AudioEvaluator（Gemini 原生或 OpenAI 相容，策略+工廠模式）AI 評分，結果寫回 Anki 卡片的 Recordings 欄位 | `bot/handlers/voice.py` → `SpeakingService.process_voice_evaluation` |

輔助能力：MinIO 媒體存儲（`/api/v1/storage`，尚未接入生卡流程）、VOICEPEAK 語音合成與 FFmpeg 音檔拼接（已實作但尚未接線）。

---

## 技術棧

### 後端（Python 3.11，`backend/requirements.txt`）

| 類別 | 套件 | 用途 |
|---|---|---|
| Web 框架 | fastapi、uvicorn[standard]、python-multipart | API 中樞、ASGI 伺服器、檔案上傳 |
| 驗證/設定 | pydantic v2、pydantic-settings、python-dotenv | Pydantic v2 合約、`.env` 集中設定 |
| 資料庫 | sqlmodel、aiosqlite、alembic、greenlet | SQLAlchemy 2.0 async ORM、SQLite 驅動、Schema 遷移 |
| AI 整合 | httpx、openai、google-genai、jinja2 | AnkiConnect 客戶端、LLM 生卡、Gemini 原生語音評測、Prompt 模板 |
| 外部服務 | minio、aiogram（3.4+） | 媒體存儲、Telegram Bot（純 aiogram 3） |

資料庫預設為 **SQLite**（`sqlite+aiosqlite`），並依 MySQL 相容準則（顯式約束命名、VARCHAR 長度、時區安全）設計，為未來遷移 MySQL 預留（見 `docs/adr/003-sqlmodel-orm-foundation.md`）。

### 前端（`frontend/package.json`）

| 類別 | 套件 | 用途 |
|---|---|---|
| 核心框架 | react / react-dom 18.3.1 | SPA（注意：實際為 React 18，非 19） |
| 建置 | vite 6、typescript 5.6（strict） | 開發伺服器 + 建置，`/api` 代理至 `127.0.0.1:8000` |
| 樣式 | tailwindcss v4 + @tailwindcss/vite | `index.css` 以 `@theme inline` 映射 shadcn 風格 HSL 變數 |
| 資料層 | @tanstack/react-query v5、axios | 伺服器狀態管理、單一 axios instance |
| 路由 | react-router-dom v6 | 三條路由（Dashboard / CardGenerator / KnowledgeGraph） |
| 視覺化 / UI | react-force-graph-2d、lucide-react、sonner、@radix-ui、cva、clsx、tailwind-merge | 知識圖譜力導向圖、手動拷貝的 shadcn/ui 元件體系 |

### 基礎設施

- **卡片後端**：AnkiConnect v6 JSON-RPC（httpx 連線池，支援 Cloudflare Access header）。
- **對象存儲**：MinIO。
- **CI/CD**：GitHub Actions → GHCR（amd64 + arm64 多架構）→ Cloudflare Access → Portainer webhook 自動重新部署。
- **容器**：後端 `python:3.11-slim`（非 root apiuser）；前端 `node:20-alpine` 建置 + `nginx:alpine`（SPA try_files + `/api/` 反代後端）。

---

## 系統架構

整體為 **Controller → Service → Infrastructure** 三層架構，Web API 與 Telegram Bot **共用同一層 Service**（依賴注入分兩套：Web 端 `core/dependencies.py` 的 `Depends()` 鏈，Bot 端 `bot/dependencies.py` 的 middleware）。

```
React SPA ─┐                      ┌─ AnkiConnect (本地 Anki)
           ├─ nginx ─┐            ├─ Gemini / OpenAI 相容 LLM
Telegram ──┤         ├─ FastAPI ──┤─ MinIO
CLI 腳本 ──┘  (寄生 Bot 於同進程)  ├─ SQLite (card_relations / relation_types)
                                  └─ Telegram Bot API
```

單一 FastAPI 進程承載 Web API 與 Telegram Bot（「寄生」架構）：`backend/app/main.py` 的 lifespan 依 `TG_WEBHOOK_DOMAIN` 是否設定，選擇 Webhook 模式或 Long Polling 模式。詳細分層、請求生命週期與外部整合見 [`docs/02_Backend_Architecture.md`](docs/02_Backend_Architecture.md)。

---

## 目錄結構

```
FluencyTides/
├── README.md                       # 本檔
├── .github/workflows/main.yml      # CI/CD：paths-filter → lint/build → GHCR → Portainer webhook
├── docs/                           # 專案設計文檔與 ADR（見下方文檔索引）
│   ├── 01_Architecture_and_Structure.md
│   ├── 02_Project_Roadmap_and_Progress.md
│   ├── 03_Acceptance_Criteria.md
│   ├── 04_Telegram_Integration_Guide.md
│   ├── adr/                        # ADR 001-004
│   └── new/                        # 全項目審查產出（權威現狀參考）
├── backend/                        # Python FastAPI 後端
│   ├── Dockerfile / docker-compose.yml / alembic.ini / requirements.txt
│   ├── .env.example                # 環境變數範例
│   ├── alembic/                    # async 遷移環境 + versions/
│   ├── scripts/                    # 三支 CLI 維運腳本 + _bootstrap.py + samples/
│   └── app/
│       ├── main.py                 # 進入點：lifespan 管理全部 Singleton 與 Bot 啟停
│       ├── core/                   # config / auth / dependencies / exceptions
│       ├── api/                    # cards / relations / storage / health / webhook 五個 Router
│       ├── schemas/                # Pydantic v2 DTO（card/anki/relation/storage/speaking/voice/llm/deep_link/common）
│       ├── services/               # CardService / SpeakingService / RelationService / StorageService /
│       │   │                       #   PromptManager / schema_composer；anki_model/ 子套件；prompts/ Jinja2 模板
│       │   ├── anki_model/          # repository / manager / note_builder
│       │   └── prompts/             # 5 個 Jinja2 System Prompt 模板（.j2）
│       ├── anki_models/            # 9 種 Anki 模型定義（.json + front/back HTML + CSS）
│       ├── bot/                    # dispatcher / dependencies（雙 middleware）/ state / handlers / utils
│       └── infrastructure/         # anki / database / llm / audio_evaluator / storage / voice / ffmpeg
└── frontend/                       # React + Vite 前端
    ├── Dockerfile / docker-compose.yml / nginx.conf / vite.config.ts
    └── src/
        ├── main.tsx / App.tsx / index.css
        ├── api/client.ts           # 單一 axios instance，集中所有後端呼叫
        ├── types/api.ts            # 手寫對齊後端 Pydantic 的介面
        ├── pages/                  # Dashboard / CardGenerator / KnowledgeGraph
        ├── components/             # CardDetailModal + ui/（shadcn 手動拷貝）
        ├── hooks/useLocalStorage.ts
        └── lib/utils.ts
```

> 註：`backend/` 根目錄下另有 `api/ core/ models/ services/ utils/ scripts/` 等頂層目錄與 `app/domain/`，其中 `api/ core/ models/ services/ utils/` 與 `app/domain/` 為早期 scaffold 殘留（僅含空 `__init__.py`，無任何引用），實際代碼一律位於 `backend/app/` 下。

---

## 本地啟動

前置需求：Python 3.11、Node.js 20、可存取的 AnkiConnect（本地 Anki + AnkiConnect 外掛）、以及 LLM API 金鑰。MinIO 為選用。

### 後端

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # 依實際環境填入 LLM_API_KEY、ANKI_CONNECT_URL、TG_BOT_TOKEN 等
# 本地開發建議把 DATABASE_URL 改為相對路徑，例如：
#   DATABASE_URL=sqlite+aiosqlite:///./fluencytides.db

uvicorn app.main:app --reload --port 8000
```

- API 位於 `http://127.0.0.1:8000`，Swagger UI 在 `/docs`，健康檢查在 `/api/health`。
- **開發模式**（`ENVIRONMENT=development`，預設）下 `API_SECRET_KEY` 為空會放行所有 API 以方便本地測試；**生產模式**（`ENVIRONMENT=production`）下密鑰為空會在啟動時被拒絕（fail-closed）。
- 資料庫 schema：開發模式啟動時自動 `create_all`；生產模式跳過並改由 Alembic 管理，部署前應執行 `alembic upgrade head`。

### 前端

```bash
cd frontend
npm install
npm run dev               # http://127.0.0.1:5173，/api 自動代理至 127.0.0.1:8000
```

### Telegram Bot（選用）

在 `.env` 設定 `TG_BOT_TOKEN` 與 `TG_ALLOWED_USER_IDS` 後隨後端一併啟動：未設 `TG_WEBHOOK_DOMAIN` 走 Long Polling（適合本地），設定則走 Webhook。整合細節見 [`docs/04_Telegram_Integration_Guide.md`](docs/04_Telegram_Integration_Guide.md)。

---

## 文檔索引

### 專案設計文檔（`docs/`）

| 文檔 | 內容 |
|---|---|
| [`01_Architecture_and_Structure.md`](docs/01_Architecture_and_Structure.md) | 系統上下文/容器圖、時序圖、目錄結構與解耦設計 |
| [`02_Project_Roadmap_and_Progress.md`](docs/02_Project_Roadmap_and_Progress.md) | Phase 1-8 開發進度追蹤 |
| [`03_Acceptance_Criteria.md`](docs/03_Acceptance_Criteria.md) | 驗收標準與錯誤處理規範 |
| [`04_Telegram_Integration_Guide.md`](docs/04_Telegram_Integration_Guide.md) | Telegram Bot 整合指南 |
| [`adr/`](docs/adr/) | 架構決策記錄（ADR 001-004） |

### 全項目審查文檔（`docs/`，權威現狀參考）

由 Claude Code 全項目代碼審查產出，所有論斷以實際代碼為準。若設計文檔與審查文檔衝突，以審查文檔為準：

> **修復進度（三輪累計）**：141 條發現中已修復 **132 條**（第一輪 31 + 第二輪 41 + 第三輪 60），另 1 條改判為活代碼、2 條部分修復、5 條暫緩、1 條未處理。其中最關鍵的放大器 **F063（後端零測試）已於第三輪修復**——測試基線已建立（後端 48 + 前端 11 個自動化測試），CI job 接入為最後待辦。完整狀態見 [`06_Issues_and_Risks.md`](docs/06_Issues_and_Risks.md) 第 2 節。

| 文檔 | 內容 |
|---|---|
| [`01_Project_Overview.md`](docs/01_Project_Overview.md) | 項目定位、實際技術棧、系統架構圖、健康度總評 |
| [`02_Backend_Architecture.md`](docs/02_Backend_Architecture.md) | 後端分層、請求生命週期、資料模型與遷移、外部整合 |
| [`06_Issues_and_Risks.md`](docs/06_Issues_and_Risks.md) | 141 條問題完整清單 |
| [`09_Action_Plan.md`](docs/09_Action_Plan.md) | 分階段修復計畫 |
| [`10_Implementation_Log.md`](docs/10_Implementation_Log.md)、[`11_Implementation_Log.md`](docs/11_Implementation_Log.md) | 第一／二輪重構與修復實作紀錄 |
| [`12_Implementation_Log.md`](docs/12_Implementation_Log.md) | 第三輪：測試基線 + CI/CD + 死代碼清理 + 文檔對齊 |
