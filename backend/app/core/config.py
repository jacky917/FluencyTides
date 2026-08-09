"""
應用程式配置管理模組。

Application configuration management module.

使用 Pydantic V2 BaseSettings 集中管理所有環境變數，
涵蓋 AnkiConnect、Cloudflare Access、MinIO、LLM、VOICEPEAK
等外部服務的連線資訊。

Centralizes all environment variables via Pydantic V2 BaseSettings, covering
connection info for AnkiConnect, Cloudflare Access, MinIO, LLM, VOICEPEAK and
other external services.

重構自 old/Anki/utils/config_manager.py，改進：
- 移除全域實例化（避免 import 時拋出 ValidationError）。
- 使用 Pydantic V2 語法（SettingsConfigDict）。
- 集中管理所有服務的環境變數（舊版散落在各模組中）。
- 新增 VOICEPEAK 語音合成引擎的設定欄位。

Refactored from old/Anki/utils/config_manager.py with these improvements:
- Removed module-level instantiation side effects at import time.
- Adopted Pydantic V2 syntax (SettingsConfigDict).
- Centralized env vars for all services (previously scattered per module).
- Added VOICEPEAK speech-synthesis settings fields.

設計決策：
- 使用 Singleton 模式提供全域 settings 實例，但延遲初始化，
  讓測試環境可以注入 mock 值。
- extra="ignore" 允許 .env 中有未定義的環境變數，
  避免新增環境變數時因未同步更新 Settings 而崩潰。

Design decisions:
- A singleton `settings` instance is provided globally, with lazy-ish
  initialization so tests can inject mock values.
- extra="ignore" tolerates undefined variables in .env, preventing crashes
  when new env vars are added before Settings is updated.
"""

import logging
import sys

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """FluencyTides 應用程式配置管理類別。

    FluencyTides application settings class.

    透過 pydantic-settings 從 .env 檔案與系統環境變數讀取設定，
    並強制進行型別與驗證檢查。

    Reads settings from the .env file and system environment variables via
    pydantic-settings, enforcing type and validation checks.

    Attributes:
        PROJECT_NAME: 專案名稱，用於 FastAPI 的 title。Project name used as
            the FastAPI title.
        LOG_LEVEL: 系統日誌層級。System logging level.

        ANKI_CONNECT_URL: AnkiConnect 本地端點完整 URL。Full local
            AnkiConnect endpoint URL.
        ANKI_CONNECT_API_KEY: AnkiConnect API 金鑰（可選）。Optional
            AnkiConnect API key.

        CF_ACCESS_CLIENT_ID: Cloudflare Access Client ID（可選）。Optional
            Cloudflare Access client ID.
        CF_ACCESS_CLIENT_SECRET: Cloudflare Access Client Secret（可選）。
            Optional Cloudflare Access client secret.

        MINIO_HOST: MinIO 伺服器主機位址。MinIO server host address.
        MINIO_PORT: MinIO 伺服器埠號。MinIO server port.
        MINIO_ACCESS_KEY: MinIO 存取金鑰。MinIO access key.
        MINIO_SECRET_KEY: MinIO 秘密金鑰。MinIO secret key.
        MINIO_SECURE: 是否使用 HTTPS 連線 MinIO。Whether to use HTTPS for
            MinIO connections.
        MINIO_DEFAULT_BUCKET: MinIO 預設儲存桶名稱。Default MinIO bucket
            name.

        LLM_API_KEY: OpenAI 相容 API 金鑰。OpenAI-compatible API key.
        LLM_BASE_URL: OpenAI 相容 API 端點 URL。OpenAI-compatible API base
            URL.
        LLM_MODEL_NAME: LLM 模型名稱。LLM model name.

        VOICEPEAK_EXECUTABLE_PATH: VOICEPEAK CLI 執行檔路徑。Path to the
            VOICEPEAK CLI executable.
        VOICEPEAK_DEFAULT_NARRATOR: VOICEPEAK 預設旁白角色。Default
            VOICEPEAK narrator character.
        VOICEPEAK_CHARACTERS_CONFIG_PATH: 角色設定 JSON 檔案路徑。Path to
            the character configuration JSON file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ====================================================================
    # 應用程式基礎設定
    # ====================================================================
    PROJECT_NAME: str = "FluencyTides"
    LOG_LEVEL: str = Field(
        default="INFO",
        description="系統日誌層級：DEBUG, INFO, WARNING, ERROR, CRITICAL",
    )
    LOG_FILE_PATH: str = Field(
        default="logs/fluencytides.log",
        description="日誌儲存路徑，預設在 backend/logs 目錄下",
    )
    API_SECRET_KEY: str | None = Field(
        default=None,
        description="API 認證金鑰。若未設定或為空字串，則跳過認證（用於開發環境）。",
    )

    # ====================================================================
    # 資料庫設定 (SQLModel + SQLAlchemy Async)
    # ====================================================================
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./fluencytides.db",
        description=(
            "主要的資料庫連線 URL。預設使用 SQLite（零配置、適合開發）。"
        ),
    )

    # ====================================================================
    # 語料庫專用 MySQL 設定 (用於 NLP 檢索與例句擷取)
    # ====================================================================
    MYSQL_HOST: str = Field(
        default="127.0.0.1",
        description="MySQL 伺服器位址",
    )
    MYSQL_PORT: int = Field(
        default=3306,
        description="MySQL 伺服器埠號",
    )
    MYSQL_USER: str = Field(
        default="root",
        description="MySQL 使用者名稱",
    )
    MYSQL_PASSWORD: str = Field(
        default="",
        description="MySQL 密碼",
    )
    MYSQL_DATABASE: str = Field(
        default="fluencytides_corpus",
        description="MySQL 語料庫資料庫名稱",
    )

    # ====================================================================
    # Elasticsearch 設定 (NLP 檢索與語料庫)
    # ====================================================================
    ELASTICSEARCH_HOSTS: str = Field(
        default="http://localhost:9200",
        description="Elasticsearch 內部或主要連線位址",
    )
    ELASTICSEARCH_PUBLIC_URL: str | None = Field(
        default=None,
        description="Elasticsearch 外部公開位址 (優先用於本機測試腳本)",
    )
    ELASTICSEARCH_USERNAME: str = Field(
        default="elastic",
        description="Elasticsearch 帳號",
    )
    ELASTICSEARCH_PASSWORD: str = Field(
        default="",
        description="Elasticsearch 密碼",
    )

    # ====================================================================
    # JP_VerbPair 卡片專屬設定
    # ====================================================================
    JP_VERB_PAIR_CONTEXT_PREV: int = Field(
        default=20,
        description="JP_VerbPair: 目標句往前抓取的對話句數",
    )
    JP_VERB_PAIR_CONTEXT_NEXT: int = Field(
        default=10,
        description="JP_VerbPair: 目標句往後抓取的對話句數",
    )
    JP_VERB_PAIR_MAX_CARDS_PER_VERB: int = Field(
        default=20,
        description="單一動詞（自動/他動分開計算）最多生成的子卡片數量",
    )
    JP_VERB_PAIR_VOICE_DIR: str = Field(
        default=r"C:\Users\forip\Desktop\WorkSpace\material\voice\yuzusoft\SabbatOfTheWitch",
        description="音檔目錄絕對路徑",
    )
    JP_VERB_PAIR_AVATAR_DIR: str = Field(
        default=r"C:\Users\forip\Desktop\WorkSpace\material\avatar\yuzusoft\SabbatOfTheWitch",
        description="頭像目錄絕對路徑",
    )
    JP_VERB_PAIR_SOURCE_GAME: str = Field(
        default="SabbatOfTheWitch",
        description="遊戲來源前綴（用於產生 Anki 音檔標籤與尋找媒體）",
    )
    JP_VERB_PAIR_GAME_NAME_JP: str = Field(
        default="サノバウィッチ",
        description="遊戲日文原名，用於 LLM 提示詞與卡片標籤",
    )

    # ====================================================================
    # JP_CoreVerb 卡片專屬設定
    # ====================================================================
    JP_CORE_VERB_CONTEXT_PREV: int = Field(
        default=20,
        description="JP_CoreVerb: 目標句往前抓取的對話句數",
    )
    JP_CORE_VERB_CONTEXT_NEXT: int = Field(
        default=10,
        description="JP_CoreVerb: 目標句往後抓取的對話句數",
    )
    JP_CORE_VERB_MAX_CARDS_PER_VERB: int = Field(
        default=15,
        description="單一核心動詞最多生成的子卡片數量（verb_search_config.json 可 per-verb 覆寫）",
    )
    JP_CORE_VERB_MAX_PER_CHAPTER: int = Field(
        default=2,
        description="同一章節最多取句數（避免語料集中於單一劇情段落）",
    )
    JP_CORE_VERB_MIN_SENTENCE_LENGTH: int = Field(
        default=8,
        description="目標句最短長度（字元數），過短的句子缺乏文脈價值",
    )
    JP_CORE_VERB_VOICE_DIR: str = Field(
        default=r"C:\Users\forip\Desktop\WorkSpace\material\voice\yuzusoft\SabbatOfTheWitch",
        description="音檔目錄絕對路徑",
    )
    JP_CORE_VERB_AVATAR_DIR: str = Field(
        default=r"C:\Users\forip\Desktop\WorkSpace\material\avatar\yuzusoft\SabbatOfTheWitch",
        description="頭像目錄絕對路徑",
    )
    JP_CORE_VERB_SOURCE_GAME: str = Field(
        default="SabbatOfTheWitch",
        description="遊戲來源前綴（用於產生 Anki 音檔標籤與尋找媒體）",
    )
    JP_CORE_VERB_GAME_NAME_JP: str = Field(
        default="サノバウィッチ",
        description="遊戲日文原名，用於 LLM 提示詞與卡片標籤",
    )
    JP_CORE_VERB_MASTER_DECK: str = Field(
        default="日本語::核心動詞::Master",
        description="核心動詞母卡片所在的 Anki 牌組名稱",
    )

    @property
    def mysql_async_url(self) -> str:
        """組裝非同步 MySQL 連線字串 (使用 aiomysql)。

        Build the async MySQL connection URL (using aiomysql).
        """
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"
        )

    @field_validator("DATABASE_URL")
    @classmethod
    def resolve_sqlite_path(cls, v: str) -> str:
        """優雅處理 SQLite 的相對路徑問題。

        Gracefully resolve relative SQLite paths.

        若使用 `sqlite+aiosqlite:///./...` 這種寫法，
        將會自動相對於 backend 根目錄轉換為絕對路徑，避免在不同目錄執行腳本時
        產生多個不同的 db 檔案。

        URLs of the form `sqlite+aiosqlite:///./...` are converted to
        absolute paths relative to the backend root, so scripts run from
        different directories do not create multiple db files.

        Args:
            v: 原始 DATABASE_URL 字串。The raw DATABASE_URL string.

        Returns:
            解析後的 DATABASE_URL。The resolved DATABASE_URL.
        """
        if v.startswith("sqlite+aiosqlite:///./"):
            from pathlib import Path
            # 此檔案位於 backend/app/core/config.py
            # 往上三層即為 backend/ 根目錄
            base_dir = Path(__file__).resolve().parent.parent.parent
            db_name = v.replace("sqlite+aiosqlite:///./", "")
            abs_path = base_dir / db_name
            # URL 需要正斜線
            return f"sqlite+aiosqlite:///{abs_path.as_posix()}"
        return v

    # ====================================================================
    # AnkiConnect 設定
    # ====================================================================
    ANKI_MODELS_DIR: str = Field(
        default="app/anki_models",
        description="存放 Anki 模型 JSON 定義檔的相對路徑 (相對於 backend)",
    )
    ANKI_CONNECT_URL: str = Field(
        default="http://127.0.0.1:8765",
        description="AnkiConnect 本地端點完整 URL",
    )
    ANKI_CONNECT_API_KEY: str | None = Field(
        default=None,
        description="AnkiConnect API 金鑰（可選）",
    )

    # ====================================================================
    # Cloudflare Access 設定（用於遠端 AnkiConnect 穿透）
    # ====================================================================
    CF_ACCESS_CLIENT_ID: str | None = Field(
        default=None,
        description="Cloudflare Access Client ID",
    )
    CF_ACCESS_CLIENT_SECRET: str | None = Field(
        default=None,
        description="Cloudflare Access Client Secret",
    )

    # ====================================================================
    # MinIO 物件存儲設定
    # ====================================================================
    MINIO_HOST: str = Field(
        default="127.0.0.1",
        description="MinIO 伺服器主機位址",
    )
    MINIO_PORT: str = Field(
        default="9000",
        description="MinIO 伺服器埠號",
    )
    MINIO_ACCESS_KEY: str = Field(
        default="minioadmin",
        description="MinIO 存取金鑰",
    )
    MINIO_SECRET_KEY: str = Field(
        default="minioadmin",
        description="MinIO 秘密金鑰",
    )
    MINIO_SECURE: bool = Field(
        default=False,
        description="是否使用 HTTPS 連線 MinIO",
    )
    MINIO_DEFAULT_BUCKET: str = Field(
        default="fluencytides-media",
        description="MinIO 預設儲存桶名稱，用於存放媒體檔案",
    )

    # ====================================================================
    # 自訂筆記來源標籤設定
    # ====================================================================
    NOTE_SOURCE_TAGS: str = Field(
        default="",
        description="自訂的筆記來源標籤（逗號分隔），例如 '仕事,GRAVITY,HelloTalk'",
    )

    @property
    def note_source_tags_list(self) -> list[str]:
        """解析 NOTE_SOURCE_TAGS 為字串列表。

        Parse NOTE_SOURCE_TAGS into a list of strings.
        """
        if not self.NOTE_SOURCE_TAGS:
            return []
        return [tag.strip() for tag in self.NOTE_SOURCE_TAGS.split(",") if tag.strip()]

    # ====================================================================
    # Telegram Bot 設定 (Phase 3)
    # ====================================================================
    TG_BOT_TOKEN: str | None = Field(
        default=None,
        description="Telegram Bot Token (向 @BotFather 取得)",
    )
    TG_BOT_USERNAME: str = Field(
        default="",
        description="Telegram Bot 使用者名稱 (例如: Jacky917_bot)，用於生成 Deep Link",
    )
    TG_ADMIN_CHAT_ID: int | None = Field(
        default=None,
        description="管理員的 Telegram Chat ID，用於接收系統未預期崩潰通知",
    )
    TG_ALLOWED_USER_IDS: str = Field(
        default="",
        description="允許使用的 User ID 列表 (逗號分隔)，例如 '12345,67890'",
    )
    TG_WEBHOOK_DOMAIN: str | None = Field(
        default=None,
        description="生產環境 Webhook 網域名稱 (例如: https://your-domain.com)。若留空則預設使用 Long Polling",
    )
    TG_WEBHOOK_PATH: str = Field(
        default="/api/webhook",
        description="Webhook 接收路徑",
    )
    TG_WEBHOOK_SECRET: str | None = Field(
        default=None,
        description="Webhook 密鑰，用於驗證來自 Telegram 的請求。若設定，將會在 setWebhook 時自動帶上。",
    )
    TG_STATE_EXPIRE_MINUTES: int = Field(
        default=10,
        description="Telegram 使用者狀態過期時間（分鐘）",
    )

    @property
    def tg_webhook_url(self) -> str | None:
        """根據 DOMAIN 與 PATH 組裝完整的 Webhook URL。

        Build the full webhook URL from DOMAIN and PATH; None if unset.
        """
        if not self.TG_WEBHOOK_DOMAIN:
            return None
        domain = self.TG_WEBHOOK_DOMAIN.rstrip("/")
        path = self.TG_WEBHOOK_PATH.lstrip("/")
        return f"{domain}/{path}"

    @property
    def tg_allowed_users(self) -> set[int]:
        """解析 TG_ALLOWED_USER_IDS 字串為整數 Set。

        Parse TG_ALLOWED_USER_IDS into a set of integers.

        Returns:
            包含允許 User ID 的集合。若為空字串，則回傳空集合。
            Set of allowed user IDs; empty set for an empty string.
        """
        if not self.TG_ALLOWED_USER_IDS:
            return set()
        try:
            return {
                int(uid.strip())
                for uid in self.TG_ALLOWED_USER_IDS.split(",")
                if uid.strip()
            }
        except ValueError:
            logging.getLogger(__name__).error(
                "解析 TG_ALLOWED_USER_IDS 失敗，請確保為逗號分隔的整數。"
            )
            return set()

    # ====================================================================
    # LLM (OpenAI 相容) 設定
    # ====================================================================
    LLM_API_KEY: str | None = Field(
        default=None,
        description="OpenAI 相容 API 金鑰（例如 Gemini API Key）",
    )
    LLM_BASE_URL: str | None = Field(
        default=None,
        description="OpenAI 相容 API 端點 URL",
    )
    LLM_MODEL_NAME: str = Field(
        default="gemini-2.0-flash",
        description="LLM 預設模型名稱",
    )
    LLM_PROVIDER: str = Field(
        default="google",
        description="LLM 服務商名稱（若非 google 或 openai 則會自動在產出結果冠上服務商前綴）",
    )
    LLM_PARSE_BATCH_SIZE: int = Field(
        default=20,
        description="LLM 進行劇本批次解析時，一次抓取的台詞數量 (預設 20 句)",
    )

    AUDIO_API_KEY: str | None = Field(
        default=None,
        description="語音處理專用的 API 金鑰（Gemini Native 或 OpenAI 相容皆共用此金鑰）",
    )
    AUDIO_BASE_URL: str | None = Field(
        default=None,
        description="語音處理專用的 API 端點 URL (僅 OpenAI 相容 API 或代理需要)",
    )
    AUDIO_MODEL_NAME: str = Field(
        default="gemini-2.5-flash",
        description="語音評估模型名稱 (Gemini 或 OpenAI 共用)",
    )

    # ====================================================================
    # Audio Evaluator 語音評分設定 (Strategy Pattern)
    # ====================================================================
    AUDIO_EVALUATOR_PROVIDER: str = Field(
        default="gemini_native",
        description=(
            "語音評分器的供應商選擇。"
            "可選值: 'gemini_native' (Google 原生 SDK)、'openai' (OpenAI 相容 API)、"
            "'proxy' (第三方中轉)、'stt_diff' (本地 Whisper + difflib 零成本比對)、"
            "'stt_llm' (本地 Whisper + 純文字 LLM 低成本評分)。"
            "策略模式允許在不修改業務邏輯的前提下切換供應商。"
        ),
    )

    # ====================================================================
    # STT (自架 Whisper / Speaches) 設定
    # ====================================================================
    STT_SERVER_URL: str | None = Field(
        default=None,
        description="自架 Speaches/Whisper 的 OpenAI 相容端點 (含 /v1)",
    )
    STT_MODEL_NAME: str = Field(
        default="Systran/faster-whisper-large-v3",
        description="faster-whisper 模型 ID",
    )
    STT_API_KEY: str = Field(
        default="speaches",
        description="自架 STT 服務金鑰 (OpenAI SDK 要求非空即可)",
    )
    STT_LLM_MODEL_NAME: str = Field(
        default="gemini-2.5-flash",
        description="stt_llm 模式專用的純文字評分模型 (與多模態 AUDIO_MODEL_NAME 脫鉤)",
    )

    # ====================================================================
    # VOICEPEAK 語音合成設定
    # ====================================================================
    VOICEPEAK_EXECUTABLE_PATH: str = Field(
        default="voicepeak",
        description=(
            "VOICEPEAK CLI 執行檔的完整路徑或命令名稱。"
            "若已加入 PATH 環境變數，可直接使用 'voicepeak'。"
        ),
    )
    VOICEPEAK_DEFAULT_NARRATOR: str = Field(
        default="Japanese Male Child",
        description="VOICEPEAK 預設旁白角色名稱（CLI 英文 ID）",
    )
    VOICEPEAK_CHARACTERS_CONFIG_PATH: str = Field(
        default="characters.json",
        description=(
            "角色設定 JSON 檔案路徑（相對或絕對路徑）。"
            "此檔案定義了角色顯示名稱、CLI ID 與情緒映射。"
        ),
    )

    # ====================================================================
    # Scripts 內部與遠端測試腳本設定
    # ====================================================================
    SCRIPTS_API_BASE_URL: str = Field(
        default="http://127.0.0.1:8000",
        description="Scripts 呼叫 FastAPI 的基礎 URL。若是遠端測試，請填寫含 Cloudflare Access 的完整網域",
    )

    def setup_logging(self) -> None:
        """設定全域日誌 (Global Logging)。

        Configure global logging.

        使用 Loguru InterceptHandler 接管標準 logging。

        Uses a Loguru InterceptHandler to take over the standard logging
        module.
        """
        import logging
        import sys
        from loguru import logger

        class InterceptHandler(logging.Handler):
            """攔截標準 logging 訊息並導向 Loguru。

            Intercept standard logging records and route them to Loguru.
            """
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    level = logger.level(record.levelname).name
                except ValueError:
                    level = record.levelno

                frame, depth = logging.currentframe(), 2
                while frame and frame.f_code.co_filename == logging.__file__:
                    frame = frame.f_back
                    depth += 1

                logger.opt(depth=depth, exception=record.exc_info).log(
                    level, record.getMessage()
                )

        # 1. 移除 loguru 預設的 handler (預設是 stderr)
        logger.remove()

        # 2. 依照設定層級添加新的 stdout handler (輸出到終端機)
        level: str = self.LOG_LEVEL.upper()
        log_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
        logger.add(sys.stdout, level=level, format=log_format)

        # 3. 新增 File Handler (輸出到檔案，自動輪轉與壓縮)
        logger.add(
            self.LOG_FILE_PATH,
            level=level,           # 檔案記錄的最低層級
            format=log_format,     # 檔案內的格式
            rotation="10 MB",      # 當檔案達到 10MB 時，自動建立新檔案 (也支援 "1 day" 每天輪轉)
            retention="30 days",   # 最多保留 30 天的日誌
            compression="zip",     # 舊的日誌自動壓縮成 .zip 節省空間
            enqueue=True,          # 在異步環境中保證執行緒安全 (重要!)
            encoding="utf-8"
        )

        # 4. 將標準 logging 導向 Loguru
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

        # 4. 對於一些雜訊較多的套件 (例如 uvicorn, fastapi, httpx)，替換它們的 handlers
        for logger_name in logging.root.manager.loggerDict.keys():
            mod_logger = logging.getLogger(logger_name)
            # 保持它們自己的 level 控制，但修改 handler
            mod_logger.handlers = [InterceptHandler(level=0)]
            mod_logger.propagate = False

        logger.info("系統日誌 (Loguru Intercept) 初始化完成，層級: {}", self.LOG_LEVEL)


# 預設提供一個全域實例
settings = Settings()
