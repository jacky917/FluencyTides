FluencyTides/
├── .cursorrules               # AI Agent 的開發規範與驗收標準
├── docker-compose.yml         # 包含 Flask, Redis(MQ), MinIO, PostgreSQL
├── docs/                      # 系統架構、資料庫與 API 規格文件
├── frontend/                  # Web 前端專案 (Angular / Vue / React)
│   └── ...                    # (前端代碼獨立打包，不與 Flask 混雜)
│
└── backend/                   # Flask 後端核心
    ├── run.py                 # 應用程式啟動入口 (Entry Point)
    ├── requirements.txt
    ├── .env                   # 環境變數 (API Keys, DB URL)
    │
    ├── assets/                # 🌟 縫合怪的核心：靜態資源與模板
    │   ├── prompts/           # LLM 提示詞 (Markdown/TXT 格式)
    │   │   ├── system_interviewer.md  # 面試官人設 Prompt
    │   │   └── grammar_correction.md  # 文法糾錯 Prompt
    │   └── anki_templates/    # Anki 卡片版型
    │       ├── front.html     # 卡片正面 HTML
    │       ├── back.html      # 卡片背面 HTML
    │       └── style.css      # 卡片 CSS 樣式
    │
    ├── app/                   # Flask Application Factory (應用程式主體)
    │   ├── __init__.py        # 初始化 Flask app, 註冊 Blueprints
    │   ├── core/              # 核心配置
    │   │   ├── config.py      # 讀取 .env 並轉為 Python Config
    │   │   └── security.py    # JWT 驗證、RBAC 權限裝飾器
    │   │
    │   ├── api/               # 🌐 進入點 A：Web RESTful API (Controllers)
    │   │   ├── routes.py      # 定義 /api/v1/... 等路由
    │   │   ├── auth_api.py    # 處理 Web 登入與 Token 發發
    │   │   └── audio_api.py   # 處理 Web 端的音訊上傳
    │   │
    │   ├── bot/               # 🤖 進入點 B：Telegram Bot (Controllers)
    │   │   ├── webhook.py     # 接收 TG Webhook 的路由
    │   │   ├── handlers.py    # 處理 TG 指令 (如 /start, /record)
    │   │   └── bot_sender.py  # 封裝 TG API，用來回傳訊息給用戶
    │   │
    │   ├── services/          # 🧠 業務邏輯層 (Service Layer) - 最重要！
    │   │   ├── llm_service.py # 讀取 assets/prompts，呼叫 Gemini/Claude API
    │   │   ├── anki_service.py# 操作 Anki 資料庫 (或呼叫 AnkiConnect API)
    │   │   ├── audio_task.py  # 處理音訊轉檔、丟入 Message Queue 的邏輯
    │   │   └── user_service.py# 處理使用者 CRUD 與權限邏輯
    │   │
    │   ├── models/            # 💾 資料庫模型 (ORM - SQLAlchemy)
    │   │   ├── user.py        # 使用者與 TG ID 綁定
    │   │   ├── attempt.py     # 錄音嘗試紀錄
    │   │   └── question.py    # 面試題庫
    │   │
    │   └── utils/             # 🛠️ 工具類 (Utilities) - 純函數，無狀態
    │       ├── prompt_loader.py # 讀取並解析 Markdown Prompt 的工具
    │       ├── ffmpeg_util.py   # 封裝 FFmpeg 轉檔指令
    │       ├── file_helper.py   # 處理 MinIO 檔案上傳/下載
    │       └── anki_packer.py   # 將 HTML/CSS 打包成 .apkg 檔案的工具
    │
    └── tests/                 # 單元測試與整合測試
        ├── test_api/
        ├── test_bot/
        └── test_services/