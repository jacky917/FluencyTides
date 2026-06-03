# Architecture and Structure

這份文件定義了 FluencyTides 的系統架構與初始目錄樹狀結構，嚴格遵守 Clean Architecture 與 Domain-Driven Design (DDD) 原則。

## 1. 系統上下文圖 (System Context Diagram)

此圖展示了 FluencyTides 與外部系統以及使用者的互動關係。

```mermaid
C4Context
  title System Context diagram for FluencyTides
  
  Person(user, "使用者 (Learner)", "透過網頁瀏覽器或 Telegram 使用本服務來學習與生成卡片。")
  
  System(fluencytides, "FluencyTides", "核心服務。利用 LLM 自動生成 Anki 學習卡片，管理知識圖譜，並提供雙端介面。")
  
  System_Ext(anki, "Anki Connect", "本地端 Anki 應用程式，用於同步與寫入學習卡片。")
  System_Ext(telegram, "Telegram API", "Telegram 官方平台，用於 Bot 互動與 Deep Links 跳轉。")
  System_Ext(minio, "MinIO", "相容 S3 的物件存儲服務，用於存放媒體檔案與靜態資源。")
  System_Ext(llm, "LLM API", "外部大型語言模型服務 (例如 OpenAI/Gemini API)，負責生成結構化卡片內容。")

  Rel(user, fluencytides, "互動與管理學習進度", "Web Browser / Telegram Client")
  Rel(fluencytides, anki, "寫入與同步卡片", "REST/JSON")
  Rel(fluencytides, telegram, "接收指令與發送推播", "Webhook/REST")
  Rel(fluencytides, minio, "存取檔案資源", "S3 API")
  Rel(fluencytides, llm, "請求結構化知識萃取", "REST/JSON")
```

## 2. 容器圖 (Container Diagram)

此圖展示了 FluencyTides 內部的子系統劃分，特別凸顯出前後端的職責分離，以及 Web 與 Telegram 如何對接同一套核心服務。

```mermaid
C4Container
  title Container diagram for FluencyTides
  
  Person(user, "使用者 (Learner)", "透過網頁瀏覽器或 Telegram 使用本服務。")
  
  System_Boundary(c1, "FluencyTides System") {
    Container(spa, "Web Application", "React, TypeScript, Vite", "提供知識圖譜與卡片管理的視覺化互動介面。")
    Container(backend, "API Application", "Python, FastAPI", "後端核心系統。提供 RESTful API 與 Telegram Webhook 路由，並封裝共用的業務邏輯層 (Services)。")
    ContainerDb(db, "Database", "PostgreSQL / SQLite", "儲存使用者狀態、設定與學習紀錄。")
  }

  System_Ext(anki, "Anki Connect", "本地端 Anki")
  System_Ext(telegram, "Telegram API", "Telegram 平台")
  System_Ext(minio, "MinIO", "物件存儲")
  System_Ext(llm, "LLM API", "語言模型服務")

  Rel(user, spa, "存取與互動", "HTTPS")
  Rel(user, telegram, "傳送訊息與指令", "Telegram Client")
  
  Rel(telegram, backend, "觸發 Webhook (接收指令)", "HTTPS/JSON")
  Rel(spa, backend, "呼叫 API (取得與修改資料)", "HTTPS/JSON")
  
  Rel(backend, db, "讀寫資料", "SQL")
  Rel(backend, anki, "操作 Anki 卡片", "HTTP/JSON")
  Rel(backend, minio, "上傳/下載媒體檔", "S3 API")
  Rel(backend, llm, "生成結構化 JSON", "HTTPS/JSON")
```

## 3. 卡片生成流程時序圖 (Card Generation Sequence Diagram)

此圖展示了使用者輸入單字到卡片成功寫入 Anki 的完整資料流向。

```mermaid
sequenceDiagram
    participant U as User (Web/Telegram)
    participant R as Router (api/cards.py)
    participant CS as CardService
    participant PM as PromptManager
    participant MM as AnkiModelManager
    participant LLM as LLMClient
    participant AC as AnkiClient
    participant Anki as Anki Desktop

    U->>R: POST /api/v1/cards/generate (CardGenerateRequest)
    R->>CS: generate_card(request)
    CS->>MM: ensure_deck_exists(deck_name)
    MM->>AC: get_deck_names()
    AC->>Anki: POST {action: "deckNames"}
    Anki-->>AC: ["Default", "TOEIC"]
    AC-->>MM: deck list
    MM-->>CS: ✅ 牌組存在

    CS->>MM: can_add_note(deck, model, fields)
    MM->>AC: can_add_notes(notes)
    AC->>Anki: POST {action: "canAddNotes"}
    Anki-->>AC: [true]
    AC-->>MM: can add
    MM-->>CS: ✅ 不重複

    CS->>MM: get_model_schema("TOEIC_Coach_Dark.json")
    MM-->>CS: JSON Schema

    CS->>PM: render(model_name)
    PM-->>CS: Jinja2 System Prompt

    CS->>LLM: generate_structured_data(prompt, schema)
    LLM-->>CS: LLMGenerateResult(parsed_data={Expression: "accelerate", ...})

    CS->>MM: create_note_from_llm_response(deck, model, merged_data)
    MM-->>CS: AnkiNote (Pydantic Model)

    CS->>MM: submit_note(note)
    MM->>AC: add_note(note)
    AC->>Anki: POST {action: "addNote", params: {note: {...}}}
    Anki-->>AC: 1496198395707
    AC-->>MM: note_id
    MM-->>CS: note_id
    CS-->>R: CardGenerateResponse(note_id)
    R-->>U: 200 OK ✅ 卡片已建立 (ID: 1496198395707)
```

## 4. Telegram 語音評分流程時序圖 (Audio Evaluation Sequence Diagram)

此圖展示了 `Speaking_Coach_Dark` 卡片中 Workflow B 的完整語音處理與評分流程。

```mermaid
sequenceDiagram
    participant U as User (Telegram)
    participant DP as Dispatcher (voice.py)
    participant SM as UserStateManager
    participant AC as AnkiClient
    participant AE as AudioEvaluator (Strategy)
    participant LLM as Gemini / OpenAI
    participant Anki as Anki Desktop

    U->>DP: 發送語音訊息 (.ogg)
    DP->>SM: get_state(chat_id)
    SM-->>DP: {action: "recording", card_id: "12345"}
    
    DP->>U: "正在下載語音..."
    DP->>DP: 透過 Bot API 下載二進位音檔
    
    DP->>AC: find_notes("Card_ID:12345") & get_notes_info()
    AC->>Anki: POST {action: "notesInfo"}
    Anki-->>AC: note_info (含 Prompt 與 References)
    AC-->>DP: 解析出 prompt_text 與 reference_answers
    
    DP->>AE: evaluate_audio(audio_data, prompt, references)
    AE->>LLM: 傳送 Base64 音訊或原生 File API + JSON Schema Prompt
    LLM-->>AE: 結構化 JSON (score, status_code, feedback, transcript)
    AE-->>DP: AudioEvaluationResult
    
    DP->>AC: store_media_file(rec_12345.ogg, base64_data)
    AC->>Anki: POST {action: "storeMediaFile"}
    Anki-->>AC: "rec_12345.ogg"
    
    DP->>AC: update_note_fields({Recordings: [...]})
    AC->>Anki: POST {action: "updateNoteFields"}
    
    DP->>SM: clear_state(chat_id)
    DP->>U: "評分完成！分數：85/100..."
```

## 5. 目錄結構與解耦設計 (Folder Structure)

為了實踐「Web 與 Telegram 雙端共用核心邏輯」，我們將 Controller 層（負責接收與回應）與 Service 層（負責業務邏輯）徹底分開。

```text
FluencyTides/
├── backend/                          # Python FastAPI 後端
│   ├── app/
│   │   ├── api/                      # [Controller] Web RESTful API 路由 (Routers)
│   │   │   ├── cards.py              # 卡片生成與模型/牌組列表端點
│   │   │   ├── storage.py            # MinIO 媒體存取端點
│   │   │   └── health.py             # Health Check 端點
│   │   ├── bot/                      # [Controller] Telegram Webhook/Polling 處理器
│   │   │   ├── handlers/             # Bot 指令與訊息接收
│   │   │   │   ├── commands.py       # /start, /help 與 Deep Link 處理
│   │   │   │   ├── messages.py       # 單字輸入與卡片生成處理
│   │   │   │   └── voice.py          # 語音接收與評分流程
│   │   │   ├── dependencies.py       # aiogram 白名單與 Service 注入中介層
│   │   │   ├── dispatcher.py         # aiogram Dispatcher 與 Bot 初始化
│   │   │   └── state.py              # In-Memory 使用者狀態機
│   │   ├── core/
│   │   │   ├── auth.py               # API 金鑰認證機制
│   │   │   ├── config.py             # Pydantic V2 Settings (全環境變數集中管理)
│   │   │   ├── dependencies.py       # 依賴注入工廠 (DI Container)
│   │   │   └── exceptions.py         # 全域異常類別階層 (FluencyTidesError)
│   │   ├── domain/                   # [DDD] 領域模型 (Entities, Value Objects)
│   │   ├── services/                 # [Use Case] 核心業務邏輯
│   │   │   ├── prompts/              # Jinja2 Prompt 模板目錄 (*.j2)
│   │   │   ├── anki_model_manager.py # 模型管理、Schema 讀取、防重複檢查
│   │   │   ├── card_service.py       # 卡片生成流程 (LLM → 組裝 → 提交)
│   │   │   ├── prompt_manager.py     # Jinja2 Prompt 模板管理器
│   │   │   └── storage_service.py    # MinIO 物件存儲業務邏輯
│   │   ├── infrastructure/           # 基礎設施實作 (外部服務客戶端)
│   │   │   ├── anki/
│   │   │   │   └── client.py         # 非同步 AnkiConnect v6 完整 CRUD 客戶端
│   │   │   ├── audio_evaluator/      # 語音評分器 (策略模式)
│   │   │   │   ├── base.py           # 抽象基底類別
│   │   │   │   ├── factory.py        # 根據環境變數建立對應的 Evaluator
│   │   │   │   ├── gemini_client.py  # Google 原生 SDK 實作
│   │   │   │   └── openai_client.py  # OpenAI 相容層實作
│   │   │   ├── database/             # 非同步 ORM 資料庫持久化
│   │   │   │   ├── conventions.py    # MetaData 顯式約束命名規範 (MySQL 相容)
│   │   │   │   ├── database.py       # AsyncEngine、Session 工廠、建表/釋放
│   │   │   │   └── models.py         # SQLModel Table Models (CardRelation)
│   │   │   ├── ffmpeg/               # FFmpeg 音訊/影片處理 (獨立子套件)
│   │   │   │   └── ffmpeg_merger.py  # 非同步音訊拼接 (filter_complex concat)
│   │   │   ├── llm/
│   │   │   │   └── client.py         # LLM 結構化輸出客戶端 (OpenAI 相容)
│   │   │   ├── storage/
│   │   │   │   └── minio_client.py   # MinIO 非同步物件存儲客戶端 (完整 CRUD)
│   │   │   └── voice/
│   │   │       └── voicepeak_runner.py # VOICEPEAK 非同步語音合成 (含環境隔離)
│   │   ├── schemas/
│   │   │   ├── anki.py               # Pydantic V2 驗證模型 (Note, Model, Media)
│   │   │   ├── card.py               # 卡片生成 API 請求/回應模型
│   │   │   ├── llm.py                # LLM 內部資料傳遞模型
│   │   │   ├── relation.py           # 卡片關聯 API 請求/回應模型 (DTO)
│   │   │   ├── speaking.py           # Speaking_Coach_Dark 專屬結構 (RecordingItem 等)
│   │   │   ├── storage.py            # MinIO 內部資料傳遞模型
│   │   │   ├── storage_api.py        # 媒體存取 API 請求/回應模型
│   │   │   └── voice.py              # Pydantic V2 驗證模型 (Voicepeak/FFmpeg)

│   │   ├── anki_models/              # Anki 筆記類型模板 (9 套完整模板)
│   │   │   ├── TOEIC_Coach_Dark.json / _front.html / _back.html / _style.css
│   │   │   ├── Conversation_Coach_Dark.json / ...
│   │   │   ├── Contrast_Coach_Dark.json / ...
│   │   │   ├── Voice_Shadowing_Dark.json / ...
│   │   │   ├── Shadowing_Breakdown_Dark.json / ...
│   │   │   ├── Speaking_Coach_Dark.json / ...
│   │   │   ├── AI_QA_Dark.json / ...
│   │   │   ├── Notion_SRS_Dark.json / ...
│   │   │   └── TOEIC_Coach_Dark_v2.json / ...
│   │   └── main.py                   # FastAPI 進入點
│   ├── .env.example                  # 環境變數配置範例
│   ├── tests/                        # 測試案例
│   └── requirements.txt              # Python 依賴管理
│
├── frontend/                         # React + Vite 前端 (Tailwind CSS v4)
│   ├── src/
│   │   ├── lib/
│   │   │   └── utils.ts              # cn() 工具函數 (clsx + tailwind-merge)
│   │   ├── components/               # UI 組件庫 (shadcn/ui 及共用組件)
│   │   ├── index.css                 # Tailwind v4 + shadcn/ui CSS Variables
│   │   ├── main.tsx                  # React 進入點
│   │   └── App.tsx                   # 主頁面 (含 Health Check 狀態)
│   ├── components.json               # shadcn/ui 配置
│   ├── package.json                  # NPM 依賴
│   └── vite.config.ts                # Vite 配置 (含 API Proxy)
│
├── .github/workflows/
│   └── main.yml                      # CI/CD (Ruff Lint + TS Build)
│
└── docs/                             # 專案架構與開發文件
    └── adr/                          # 架構決策記錄 (Architecture Decision Records)
```

**解耦設計說明：**
- `backend/app/api/` (Web) 與 `backend/app/bot/` (Telegram) 內部的程式碼**絕對不能**直接寫入資料庫或呼叫外部 API。
- 它們的職責僅限於：接收請求 -> 透過 `schemas/` 驗證資料 -> 呼叫 `services/` -> 回傳格式化的回應。
- 所有真正的卡片生成、知識萃取、Anki 同步邏輯，全部實作於 `backend/app/services/` 中。
