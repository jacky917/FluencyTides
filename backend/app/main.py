"""
FluencyTides API 應用程式進入點。

FluencyTides API application entry point.

本模組是 FastAPI 後端的根進入點，負責：
1. 透過 lifespan 事件管理 Infrastructure Client 的生命週期（Singleton）。
2. 註冊全域 Exception Handler，統一回傳 ErrorResponse JSON。
3. 掛載所有 API 路由（Health、Cards、Storage）。
4. 配置 CORS 中介層與 Swagger UI 元資料。

This module is the root entry point of the FastAPI backend. It:
1. Manages infrastructure client singletons via the lifespan events.
2. Registers global exception handlers returning ErrorResponse JSON.
3. Mounts all API routers (Health, Cards, Storage).
4. Configures the CORS middleware and Swagger UI metadata.

Phase 2 改進：
- 新增 lifespan 上下文管理器，啟動時初始化 AnkiClient、LLMClient、MinioClient。
- 新增 FluencyTidesError 全域異常處理器。
- 掛載 /api/v1/ 前綴下的 Cards 與 Storage 路由。
- 增強 Swagger UI 元資料（title、description、version、tags）。

Phase 2 improvements:
- Added the lifespan context manager initializing AnkiClient, LLMClient
  and MinioClient at startup.
- Added the global FluencyTidesError exception handler.
- Mounted the Cards and Storage routers under the /api/v1/ prefix.
- Enhanced Swagger UI metadata (title, description, version, tags).
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import handlers
from app.api.handlers import router as handlers_router
from app.api.health import router as health_router
from app.api.relations import router as relations_router
from app.api.storage import router as storage_router
from app.api.verb_pair import router as verb_pair_router
from app.api.core_verb import router as core_verb_router
from app.api.example.verb_pair import router as example_verb_pair_router
from app.api.webhook import router as webhook_router
from app.api.config import router as config_router
from app.api.jp_verb_readings import router as jp_verb_readings_router
from app.bot.dispatcher import create_bot, setup_dispatcher
from app.core.config import settings
from app.core.exceptions import FluencyTidesError
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.database import (
    create_db_and_tables,
    dispose_engine,
)
from app.infrastructure.database.corpus_database import dispose_corpus_engine
from app.infrastructure.llm.factory import create_llm_client
from app.infrastructure.storage.minio_client import MinioClient
from app.schemas.card import ErrorResponse

logger = logging.getLogger(__name__)


# ============================================================================
# Lifespan 事件管理器（Singleton 初始化與銷毀）
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """管理 FastAPI 應用程式的生命週期事件。

    Manage the FastAPI application lifespan events.

    Startup（yield 之前）：
        - 初始化全域日誌系統。
        - 建立 AnkiClient Singleton（httpx.AsyncClient 連線池）。
        - 建立 LLMClient Singleton（AsyncOpenAI 客戶端）。
        - 建立 MinioClient Singleton（MinIO SDK 客戶端）。
        - 啟動 Telegram Bot Polling（若有設定 TOKEN）。
        - 將所有 Singleton 存入 app.state。

    Shutdown（yield 之後）：
        - 釋放 AnkiClient 的 httpx 連線池。
        - 停止 Telegram Bot Polling。

    Startup (before yield): initialize global logging, create the
    AnkiClient/LLMClient/MinioClient singletons, start Telegram Bot
    polling or webhook binding if a token is configured, and store all
    singletons in app.state.

    Shutdown (after yield): release the AnkiClient httpx connection pool
    and stop the Telegram Bot polling/webhook.

    Yields:
        None: lifespan 上下文。The lifespan context.
    """
    # === Startup ===
    settings.setup_logging()
    logger.info("🚀 FluencyTides API 正在啟動...")

    # 初始化 Infrastructure Clients 為 Singleton
    app.state.anki_client = AnkiClient()
    logger.info("✅ AnkiClient Singleton 已初始化。")

    # 初始化資料庫（建立資料表，若已存在則跳過）
    await create_db_and_tables()
    logger.info("✅ 資料庫初始化完成。")

    # LLM 客戶端由 create_llm_client() 依 LLM_PROVIDER 選型，初始化可能因
    # 缺少 API Key（API 模式）或找不到 CLI／effort 設定錯誤（claude-code 模式）
    # 而失敗，但不應阻止整個應用啟動（其他端點仍可使用）。
    try:
        app.state.llm_client = create_llm_client()
        logger.info(
            "✅ LLM 客戶端 Singleton 已初始化 (%s)。",
            type(app.state.llm_client).__name__,
        )
    except Exception as e:
        logger.warning(
            "⚠️ LLM 客戶端初始化失敗（LLM 相關端點將不可用）: %s", e
        )
        app.state.llm_client = None

    # MinioClient 初始化同理，不應阻止應用啟動。
    try:
        app.state.minio_client = MinioClient()
        logger.info("✅ MinioClient Singleton 已初始化。")
    except Exception as e:
        logger.warning(
            "⚠️ MinioClient 初始化失敗（Storage 相關端點將不可用）: %s", e
        )
        app.state.minio_client = None

    # 初始化 AudioEvaluator Singleton (Strategy Pattern)
    try:
        from app.infrastructure.audio_evaluator.factory import create_audio_evaluator
        app.state.audio_evaluator = create_audio_evaluator()
        logger.info("✅ AudioEvaluator Singleton 已初始化。")
    except Exception as e:
        logger.warning(
            "⚠️ AudioEvaluator 初始化失敗（Workflow B 語音評分將不可用）: %s", e
        )
        app.state.audio_evaluator = None

    # 初始化並啟動 Telegram Bot (Polling)
    bot = create_bot()
    dp = None
    polling_task = None
    if bot:
        dp = setup_dispatcher()
        # 將 FastAPI app 注入 Dispatcher，供 Middleware 使用
        dp["app"] = app
        
        if settings.tg_webhook_url:
            # 檢查目前 Webhook 狀態
            webhook_info = await bot.get_webhook_info()
            if webhook_info.url != settings.tg_webhook_url:
                logger.info("🔧 準備設定 Telegram Webhook: %s", settings.tg_webhook_url)
                
                secret_token = settings.TG_WEBHOOK_SECRET
                if secret_token:
                    masked = f"{secret_token[:3]}***{secret_token[-3:]}" if len(secret_token) > 6 else "***"
                    logger.info("🔒 帶上 Webhook Secret Token 進行綁定: %s", masked)
                
                await bot.set_webhook(
                    url=settings.tg_webhook_url,
                    allowed_updates=dp.resolve_used_update_types(),
                    drop_pending_updates=True,
                    secret_token=secret_token,
                )
            
            # 驗證綁定結果
            current_webhook = await bot.get_webhook_info()
            if current_webhook.url == settings.tg_webhook_url:
                logger.info("✅ Telegram Webhook 綁定成功！")
            else:
                logger.error("❌ Telegram Webhook 綁定失敗！(目前: %s)", current_webhook.url)
                
            # 將 bot 與 dp 存入 app.state，以便外部的 webhook endpoint 可呼叫
            app.state.bot = bot
            app.state.dp = dp
        else:
            # 啟動前先清除 Webhook，避免衝突
            await bot.delete_webhook(drop_pending_updates=True)
            # 在背景啟動 Polling
            polling_task = asyncio.create_task(dp.start_polling(bot))
            logger.info("🤖 Telegram Bot Polling 已在背景啟動。")

    logger.info("🎉 FluencyTides API 啟動完成！")

    yield

    # === Shutdown ===
    logger.info("🛑 FluencyTides API 正在關閉...")
    
    # 停止 Telegram Bot
    if bot:
        if settings.tg_webhook_url:
            logger.info("刪除 Telegram Webhook...")
            await bot.delete_webhook(drop_pending_updates=True)
            await bot.session.close()
            logger.info("🤖 Telegram Webhook 已移除，連線已關閉。")
        elif polling_task and dp:
            logger.info("停止 Telegram Bot Polling...")
            await dp.stop_polling()
            await polling_task
            await bot.session.close()
            logger.info("🤖 Telegram Bot Polling 已停止。")

    await app.state.anki_client.close()
    logger.info("✅ AnkiClient 連線池已釋放。")
    
    await dispose_engine()
    logger.info("✅ 資料庫引擎已釋放。")
    
    await dispose_corpus_engine()
    
    logger.info("🏁 FluencyTides API 已關閉。")


# ============================================================================
# FastAPI 應用實例
# ============================================================================

app = FastAPI(
    title="FluencyTides API",
    description=(
        "Anki 卡片自動生成與管理系統 API。\n\n"
        "透過 LLM 結構化輸出自動生成 Anki 學習卡片，"
        "提供雙端介面（Web + Telegram）共用的核心服務。"
    ),
    version="0.2.0",
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Health",
            "description": "應用程式健康檢查端點。",
        },
        {
            "name": "Cards",
            "description": "Anki 卡片生成、模型查詢、牌組列表等核心功能。",
        },
        {
            "name": "Storage",
            "description": "MinIO 媒體檔案上傳、下載、列表、刪除功能。",
        },
    ],
)


# ============================================================================
# 全域異常處理器
# ============================================================================


@app.exception_handler(FluencyTidesError)
async def fluencytides_error_handler(
    request: Request,
    exc: FluencyTidesError,
) -> JSONResponse:
    """統一處理所有 FluencyTidesError 子類別的異常。

    Uniformly handle all FluencyTidesError subclasses.

    將業務異常轉換為統一的 ErrorResponse JSON 格式回傳，
    符合 03_Acceptance_Criteria.md §2 的要求。

    Converts business exceptions into the unified ErrorResponse JSON
    format, per 03_Acceptance_Criteria.md §2.

    Args:
        request: FastAPI Request 物件。The FastAPI Request object.
        exc: FluencyTidesError 異常實例。The FluencyTidesError instance.

    Returns:
        JSONResponse 包含 ErrorResponse 結構。A JSONResponse carrying the
        ErrorResponse structure.
    """
    logger.warning(
        "業務異常 [%s] -> %s (HTTP %d)",
        exc.error_code,
        exc.message,
        exc.status_code,
    )

    error_response = ErrorResponse(
        error_code=exc.error_code,
        message=exc.message,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_response.model_dump(),
    )


# ============================================================================
# 全域未知崩潰攔截 (Global Crash Middleware)
# ============================================================================

from fastapi import Request, Response
from fastapi.responses import JSONResponse
import asyncio

@app.middleware("http")
async def global_crash_middleware(request: Request, call_next) -> Response:
    """作為終極安全網攔截所有未捕捉的 Exception (500 Error)。

    Ultimate safety net intercepting every uncaught exception (HTTP 500).

    1. 記錄 ERROR 級別日誌並輸出 Traceback。
    2. 非同步發送 Telegram 警報。
    3. 回傳標準 500 JSON (遮蔽系統真實錯誤)。

    1. Logs at ERROR level with the traceback.
    2. Sends a Telegram alert asynchronously.
    3. Returns a standard 500 JSON that masks the real system error.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception(f"系統發生未預期崩潰: {request.url.path}")
        
        # 觸發 TG 警報 (非同步背景執行，不阻塞當前回應)
        from app.bot.alerts import send_crash_alert
        asyncio.create_task(
            send_crash_alert(
                url=str(request.url),
                method=request.method,
                error_msg=repr(exc)
            )
        )
        
        # 回傳給前端，絕對不可暴露 Traceback
        error_response = ErrorResponse(
            error_code="INTERNAL_SERVER_ERROR",
            message="系統發生未預期錯誤，已通知工程團隊處理"
        )
        return JSONResponse(
            status_code=500,
            content=error_response.model_dump()
        )

# ============================================================================
# CORS 中介層
# ============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# 路由註冊
# ============================================================================

# Health Check（不受 API Key 認證保護，確保監控系統可正常運作）
app.include_router(health_router, prefix="/api", tags=["Health"])

# Phase 2 核心路由（受 API Key 認證保護）
app.include_router(handlers.router, prefix="/api/v1")
app.include_router(storage_router, prefix="/api/v1")
app.include_router(relations_router, prefix="/api/v1")
app.include_router(verb_pair_router, prefix="/api/v1")
app.include_router(core_verb_router, prefix="/api/v1")
app.include_router(example_verb_pair_router, prefix="/api/v1/example")
app.include_router(config_router, prefix="/api/v1")
app.include_router(jp_verb_readings_router, prefix="/api/v1")

# 註冊 Webhook Router路由 (不受 prefix 限制，完全依照 TG_WEBHOOK_PATH 設定)
app.include_router(webhook_router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    """根路徑歡迎訊息。

    Root-path welcome message.

    Returns:
        包含歡迎訊息的字典。A dict containing the welcome message.
    """
    return {"message": "Welcome to FluencyTides API v0.2.0"}
