"""
應用程式配置管理模組。

使用 Pydantic V2 BaseSettings 集中管理所有環境變數，
涵蓋 AnkiConnect、Cloudflare Access、MinIO、LLM、VOICEPEAK
等外部服務的連線資訊。

重構自 old/Anki/utils/config_manager.py，改進：
- 移除全域實例化（避免 import 時拋出 ValidationError）。
- 使用 Pydantic V2 語法（SettingsConfigDict）。
- 集中管理所有服務的環境變數（舊版散落在各模組中）。
- 新增 VOICEPEAK 語音合成引擎的設定欄位。

設計決策：
- 使用 Singleton 模式提供全域 settings 實例，但延遲初始化，
  讓測試環境可以注入 mock 值。
- extra="ignore" 允許 .env 中有未定義的環境變數，
  避免新增環境變數時因未同步更新 Settings 而崩潰。
"""

import logging
import sys

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """FluencyTides 應用程式配置管理類別。

    透過 pydantic-settings 從 .env 檔案與系統環境變數讀取設定，
    並強制進行型別與驗證檢查。

    Attributes:
        PROJECT_NAME: 專案名稱，用於 FastAPI 的 title。
        LOG_LEVEL: 系統日誌層級。

        ANKI_CONNECT_URL: AnkiConnect 本地端點完整 URL。
        ANKI_CONNECT_API_KEY: AnkiConnect API 金鑰（可選）。

        CF_ACCESS_CLIENT_ID: Cloudflare Access Client ID（可選）。
        CF_ACCESS_CLIENT_SECRET: Cloudflare Access Client Secret（可選）。

        MINIO_HOST: MinIO 伺服器主機位址。
        MINIO_PORT: MinIO 伺服器埠號。
        MINIO_ACCESS_KEY: MinIO 存取金鑰。
        MINIO_SECRET_KEY: MinIO 秘密金鑰。
        MINIO_SECURE: 是否使用 HTTPS 連線 MinIO。
        MINIO_DEFAULT_BUCKET: MinIO 預設儲存桶名稱。

        LLM_API_KEY: OpenAI 相容 API 金鑰。
        LLM_BASE_URL: OpenAI 相容 API 端點 URL。
        LLM_MODEL_NAME: LLM 模型名稱。

        VOICEPEAK_EXECUTABLE_PATH: VOICEPEAK CLI 執行檔路徑。
        VOICEPEAK_DEFAULT_NARRATOR: VOICEPEAK 預設旁白角色。
        VOICEPEAK_CHARACTERS_CONFIG_PATH: 角色設定 JSON 檔案路徑。
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
            "資料庫連線 URL。預設使用 SQLite（零配置、適合開發）。"
            "未來遷移 MySQL 範例：mysql+aiomysql://user:pass@host:3306/dbname"
        ),
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def resolve_sqlite_path(cls, v: str) -> str:
        """優雅處理 SQLite 的相對路徑問題。
        
        若使用 `sqlite+aiosqlite:///./...` 這種寫法，
        將會自動相對於 backend 根目錄轉換為絕對路徑，避免在不同目錄執行腳本時
        產生多個不同的 db 檔案。
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
    TG_DEFAULT_DECK: str = Field(
        default="Default",
        description="Telegram 預設生成使用的牌組",
    )
    TG_DEFAULT_MODEL_NAME: str = Field(
        default="TOEIC_Coach_Dark",
        description="Telegram 預設生成使用的模型名稱",
    )
    TG_DEFAULT_MODEL_FILE: str = Field(
        default="TOEIC_Coach_Dark.json",
        description="Telegram 預設生成使用的模型 JSON 檔名",
    )
    TG_STATE_EXPIRE_MINUTES: int = Field(
        default=5,
        description="Telegram 兩階段互動狀態過期時間 (分鐘)",
    )

    @property
    def tg_webhook_url(self) -> str | None:
        """根據 DOMAIN 與 PATH 組裝完整的 Webhook URL。"""
        if not self.TG_WEBHOOK_DOMAIN:
            return None
        domain = self.TG_WEBHOOK_DOMAIN.rstrip("/")
        path = self.TG_WEBHOOK_PATH.lstrip("/")
        return f"{domain}/{path}"

    @property
    def tg_allowed_users(self) -> set[int]:
        """解析 TG_ALLOWED_USER_IDS 字串為整數 Set。

        Returns:
            包含允許 User ID 的集合。若為空字串，則回傳空集合。
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

    # ====================================================================
    # Audio Evaluator 語音評分設定 (Strategy Pattern)
    # ====================================================================
    AUDIO_EVALUATOR_PROVIDER: str = Field(
        default="gemini_native",
        description=(
            "語音評分器的供應商選擇。"
            "可選值: 'openai' (使用 OpenAI 相容 API) 或 "
            "'gemini_native' (使用 Google 原生 SDK)。"
            "策略模式允許在不修改業務邏輯的前提下切換供應商。"
        ),
    )
    GEMINI_NATIVE_API_KEY: str | None = Field(
        default=None,
        description=(
            "Google Gemini 原生 SDK 的 API Key。"
            "僅在 AUDIO_EVALUATOR_PROVIDER='gemini_native' 時需要。"
        ),
    )
    GEMINI_NATIVE_MODEL: str = Field(
        default="gemini-2.5-flash",
        description="Gemini 原生 SDK 使用的模型名稱。",
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

    def setup_logging(self) -> None:
        """設定全域日誌 (Global Logging)。

        設定標準輸出格式與指定層級。
        若已配置過 root logger，將覆蓋舊的 handler，確保一致性。
        """
        level: int = getattr(logging, self.LOG_LEVEL.upper(), logging.INFO)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        # 移除已存在的 handlers 避免重複輸出
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        handler_sh = logging.StreamHandler(sys.stdout)
        handler_sh.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(name)-15s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler_sh.setFormatter(formatter)
        root_logger.addHandler(handler_sh)

        config_logger = logging.getLogger(__name__)
        config_logger.info("系統日誌初始化完成，層級: %s", self.LOG_LEVEL)


# 預設提供一個全域實例
settings = Settings()
