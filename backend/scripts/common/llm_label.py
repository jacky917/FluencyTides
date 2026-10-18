"""LLM 模型標籤（腳本側）。

Script-side LLM model label.

【定位】成功生成的 DB 紀錄一律取**生成 API 回應中的 ``llm_model``**（後端單一事實
來源）；本模組只剩兩個用途：①失敗紀錄（生成未發生，無回應可取）②回應缺
``llm_model`` 欄時的 fallback。
Success records take ``llm_model`` from the generation API response; this
module only serves failure records and the missing-field fallback.

【來源】後端每個用途的模型由 ``/models``（DB）決定，本機 ``.env`` 推不出來。所以先問後端
``GET /api/v1/llm-routes``，取該用途鍵生效的標籤（模型路由計畫
llm_model_routing_FEAT_2026-10-15.md D9）；查不到時（後端未啟動、版本太舊、認證失敗）
才退回以**本機** settings 推算，並印出警告——兩份 ``.env`` 可能不一致（2026-08-28 曾因此
錯標 190 筆 DB 紀錄）。同一個用途鍵在一次執行中只查一次。
The label comes from the backend's route setting; the local derivation is
only a warned fallback. Looked up once per route key per run.
"""

import logging
from functools import cache

import httpx

from app.core.config import settings
from app.infrastructure.llm.factory import resolve_client_class
from app.infrastructure.llm.labels import build_model_label

logger = logging.getLogger(__name__)

# 查詢後端的逾時（秒）：只在啟動時查一次，查不到就退回本機推算
_TIMEOUT_SECONDS = 10


def backend_headers() -> dict[str, str]:
    """呼叫後端 API 用的認證標頭（Cloudflare Access、``API_SECRET_KEY``）。

    Auth headers for calling the backend: Cloudflare Access and the API key.
    """
    headers: dict[str, str] = {}
    if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
        headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
        headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET
    if settings.API_SECRET_KEY:
        headers["X-API-Key"] = settings.API_SECRET_KEY
    return headers


def local_llm_model_label() -> str:
    """依**本機** settings 推算 ``.env`` 那一層的標籤（不知道後端 ``/models`` 的設定）。

    Derive the ``.env``-layer label from the local settings.

    Returns:
        str: 例如 ``(claude-code)opus-5@medium``；google/openai 等 provider 維持裸模型名。
    """
    client_cls = resolve_client_class(settings.LLM_PROVIDER)
    return build_model_label(
        settings.LLM_PROVIDER,
        settings.LLM_MODEL_NAME,
        client_cls.default_effort(),
        strip_prefix=client_cls.MODEL_LABEL_STRIP_PREFIX,
    )


def _fetch_backend_label(route_key: str) -> str:
    """向後端查詢用途鍵生效的標籤。Ask the backend for a route's label.

    Raises:
        httpx.HTTPError: 連不上或回應不是 2xx。
        LookupError: 後端清單裡沒有這個用途鍵（後端版本不含這個用途）。
        ValueError: 回應格式不符。
    """
    base_url = getattr(settings, "SCRIPTS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    response = httpx.get(f"{base_url}/api/v1/llm-routes", headers=backend_headers(), timeout=_TIMEOUT_SECONDS)
    response.raise_for_status()
    for route in response.json()["routes"]:
        if route["route_key"] == route_key:
            label = route["label"]
            if not isinstance(label, str) or not label:
                raise ValueError(f"用途 '{route_key}' 的標籤格式不符：{label!r}")
            if route.get("problem"):
                logger.warning("⚠️ 後端用途 '%s' 目前無法使用：%s", route_key, route["problem"])
            return label
    raise LookupError(f"後端沒有用途鍵 '{route_key}'")


@cache
def build_llm_model_label(route_key: str) -> str:
    """失敗紀錄用的 LLM 模型標籤：後端該用途生效的標籤，查不到時退回本機推算並警告。

    The label for failure records: the backend's effective label for the
    route, or a warned local derivation when the backend cannot be asked.

    Args:
        route_key: 這支腳本呼叫的生成端點所用的用途鍵（例如 ``jp_verb_pair``）。
            The route key behind the generation endpoint the script calls.

    Returns:
        str: 模型標籤。The label.
    """
    try:
        label = _fetch_backend_label(route_key)
    except (httpx.HTTPError, LookupError, ValueError, KeyError, TypeError) as error:
        fallback = local_llm_model_label()
        logger.warning(
            "⚠️ 無法向後端查詢用途 '%s' 的模型標籤（%s: %s），失敗紀錄改用本機 .env 推算的 '%s'——"
            "若後端在 /models 改過這個用途，標籤會不一致。",
            route_key, type(error).__name__, error, fallback,
        )
        return fallback
    logger.info("🏷️ 用途 '%s' 的模型標籤（後端）：%s", route_key, label)
    return label
