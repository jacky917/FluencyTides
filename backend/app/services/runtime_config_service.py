"""執行期設定服務(唯讀切片):白名單設定列表與後端 runtime 對帳資訊。

Runtime configuration service (read-only slice): whitelisted settings
listing and backend runtime reconciliation info.

對應計畫 docs/archive/runtime_config_service_FEAT_2026-08-29.md §3.5。
本檔目前只實作讀取側(list/get/runtime);寫入側 `set_config`(驗證 →
setattr → rebuild 註冊表 → 失敗回滾)依計畫 P0 後續補上,介面已預留。
Only the read side is implemented for now; the write side follows the
plan's P0 and its interface is reserved below.

框架無關:不 import fastapi/aiogram,呼叫端(REST router / TG callback)
自行把結果轉成 HTTP 語意或訊息文案。
Framework-free: callers map results to HTTP or chat semantics themselves.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.dynamic_config import get_modifiable_configs
from app.infrastructure.llm.endpoints import get_endpoint, load_endpoints, skipped_endpoints
from app.infrastructure.llm.factory import CLAUDE_CODE_PROVIDER, is_cli_provider
from app.infrastructure.llm.labels import normalize_provider
from app.infrastructure.llm.routing import GLOBAL_ROUTE_KEY
from app.services import llm_route_service

logger = logging.getLogger(__name__)

# 改值後需要重建 singleton 的設定鍵(計畫 §3.5 REBUILD_REGISTRY 的鍵集合;
# 對應的 factory 綁定在寫入側實作時補上)。LLM 設定已不經這裡修改(改由 /models,
# 模型路由計畫 D8),所以不在其中。
# Keys whose changes require a singleton rebuild.
REQUIRES_REBUILD_KEYS: frozenset[str] = frozenset({
    "AUDIO_EVALUATOR_PROVIDER",
})


@dataclass(frozen=True)
class ConfigEntry:
    """單一可動態修改設定的描述。

    Description of one dynamically modifiable setting.

    Attributes:
        key: 設定鍵名(settings 屬性名)。Setting key.
        current_value: 目前生效值(settings 原值的字串形式)。Current
            value as string. **注意**:這是 settings 原值,不是 DB 標籤——
            寫 DB 的 llm_model 一律取生成 API 回應,詳見計畫 §3.5 警告。
        options: 允許的選項;None = 不限。Allowed options; None means
            unrestricted.
        requires_rebuild: 改值是否會觸發 singleton 重建。Whether a change
            triggers a singleton rebuild.
    """
    key: str
    current_value: str
    options: list[str] | None
    requires_rebuild: bool


class RuntimeConfigService:
    """白名單設定的讀取(與未來的套用)服務。

    Read (and, later, apply) service for whitelisted settings.
    """

    def list_configs(self) -> list[ConfigEntry]:
        """列出全部白名單設定與當前值。

        List every whitelisted setting with its current value.

        Returns:
            list[ConfigEntry]: 依鍵名排序;白名單為空時為空列表。Entries
            sorted by key; empty when the whitelist is empty.
        """
        modifiable = get_modifiable_configs()
        entries: list[ConfigEntry] = []
        for key in sorted(modifiable):
            entry = self.get_config(key)
            if entry is not None:
                entries.append(entry)
        return entries

    def get_config(self, key: str) -> ConfigEntry | None:
        """讀取單一白名單設定。

        Read one whitelisted setting.

        Args:
            key: 設定鍵名。Setting key.

        Returns:
            ConfigEntry | None: 白名單外(或 settings 無此屬性)回 None
            ——呼叫端一律以「不存在」處理,不區分原因,避免鍵名探測。
            None for non-whitelisted or unknown keys; callers treat both
            uniformly to avoid key probing.
        """
        modifiable = get_modifiable_configs()
        if key not in modifiable or not hasattr(settings, key):
            return None
        return ConfigEntry(
            key=key,
            current_value=str(getattr(settings, key)),
            options=modifiable[key],
            requires_rebuild=key in REQUIRES_REBUILD_KEYS,
        )

    async def get_runtime_info(
        self, app_state: Any, check_auth: bool = False
    ) -> dict[str, Any]:
        """取得後端 runtime 對帳資訊(計畫 §3.5 的 ``runtime`` 區塊)。

        Build the backend runtime reconciliation block.

        Args:
            app_state: FastAPI 的 ``app.state``(取活的 llm_client 用)。
                The FastAPI ``app.state`` carrying the live llm_client.
            check_auth: True 時實際打一次最小 model request(haiku)驗證
                token 認證——會消耗一次極小的訂閱請求,故由呼叫端明確
                要求才做。When True, fire one minimal haiku request to
                genuinely verify authentication (costs one tiny request).

        Returns:
            dict: ``llm_label``(全域 client 算好的顯示標籤,client 為 None
            時為 None)、``llm_client_matches_global``(該 client 是否與全域 * 目前的設定
            一致;無法比對時為 None)、``llm_provider``(全域設定生效的端點 ID;組不出
            清單時退回 .env 的 ``LLM_PROVIDER``)、
            ``anki_connect_url``(後端實際連線的 Anki 端點,供腳本啟動對帳)、
            全域端點為 CLI 型時的 ``claude_code`` 環境診斷區塊(其他為 None),
            以及模型路由計畫 D9 的 ``llm_routes``(每個用途生效的設定與來源)
            與 ``llm_endpoints``(端點與白名單,不含金鑰)。
        """
        llm_client = getattr(app_state, "llm_client", None)
        # model_label 是 BaseLLMClient 的公開標籤屬性;過渡期仍容忍只有
        # _formatted_model_name 的物件。取不到則退回 None(不自行推導,見計畫警告)
        llm_label = (
            getattr(llm_client, "model_label", None)
            or getattr(llm_client, "_formatted_model_name", None)
        )
        llm_routes, global_route = await self._llm_routes()
        global_endpoint_id = global_route["effective"]["endpoint"] if global_route is not None else None
        # 全域客戶端是啟動時或最後一次改 * 時建立的；之後全域設定的解析結果可能變了（例如啟動時
        # 讀不到 DB 而用了 .env、之後 DB 恢復）。比對兩者的標籤，不一致就明說，不讓標籤與端點互相矛盾
        matches_global = llm_label == global_route["label"] if global_route is not None else None
        if matches_global is False:
            logger.warning(
                "診斷：全域 LLM 客戶端（%s）與全域 * 目前的設定（%s）不一致；寫入一次 * 或重啟後端即可重建",
                llm_label, global_route["label"] if global_route is not None else None,
            )
        # 組不出清單時退回 .env 的 LLM_PROVIDER（llm_client_matches_global 為 None 表示無法比對）
        provider = global_endpoint_id or normalize_provider(settings.LLM_PROVIDER) or None
        endpoint = get_endpoint(provider) if provider else None
        provider_type = endpoint.type if endpoint is not None else provider

        # CLI 型端點才有環境診斷區塊;判斷交給工廠註冊表的靜態旗標——
        # 純查表、不 import provider 模組,環境壞掉(依賴缺失)時端點仍能帶病回報
        claude_code: dict[str, Any] | None = None
        if provider_type and is_cli_provider(provider_type):
            claude_code = await self._probe_claude_code(llm_client, check_auth, provider_type)

        return {
            "llm_label": llm_label,
            "llm_client_matches_global": matches_global,
            "llm_provider": provider,
            "anki_connect_url": settings.ANKI_CONNECT_URL,
            "claude_code": claude_code,
            "llm_routes": llm_routes,
            "llm_endpoints": self._llm_endpoints(),
        }

    @staticmethod
    async def _llm_routes() -> tuple[dict[str, Any], dict[str, Any] | None]:
        """各用途生效的設定(與 ``GET /api/v1/llm-routes`` 的 ``routes`` 同格式)。

        Every route's effective setting, plus the global row itself.

        Returns:
            ``({"routes": [...], "db_unavailable": bool}, 全域 * 那一列)``。組不出清單時
            (非預期錯誤,含清單裡找不到全域 *)回 ``({"error": "<型別>: <訊息>"}, None)`` 並記下
            traceback——診斷端點要能帶病回報,不因這一塊整個失敗。
        """
        try:
            views, read_ok = await llm_route_service.list_routes()
            routes = [llm_route_service.to_read(view).model_dump() for view in views]
            # 依鍵找全域那一列，不依賴清單順序
            global_route = next(route for route in routes if route["route_key"] == GLOBAL_ROUTE_KEY)
        except Exception as error:  # noqa: BLE001 - 診斷端點：其餘區塊照常回報，錯誤寫進回應並記錄
            logger.exception("診斷端點組 llm_routes 失敗")
            return {"error": f"{type(error).__name__}: {error}"}, None
        return {"routes": routes, "db_unavailable": not read_ok}, global_route

    @staticmethod
    def _llm_endpoints() -> dict[str, Any]:
        """端點與白名單(不含金鑰),以及設定有誤被跳過的端點與原因。

        Every endpoint and its whitelist (never keys), plus skipped ones.
        """
        return {
            "endpoints": [
                {
                    "id": endpoint.id,
                    "type": endpoint.type,
                    "base_url": endpoint.base_url,
                    "listed": endpoint.listed,
                    "models": [{"name": o.name, "efforts": list(o.efforts)} for o in endpoint.models],
                }
                for endpoint in sorted(load_endpoints().values(), key=lambda endpoint: endpoint.id)
            ],
            "skipped": skipped_endpoints(),
        }

    @staticmethod
    async def _probe_claude_code(
        llm_client: Any, check_auth: bool = False, provider: str = CLAUDE_CODE_PROVIDER
    ) -> dict[str, Any]:
        """探測 claude-code 執行環境(CLI 路徑/版本/認證模式)。

        Probe the claude-code runtime environment (CLI path, version,
        credential mode).

        版本探測實際執行 ``claude --version``(部署驗證的核心:證明容器內
        binary 存在且可執行);失敗時把錯誤摘要放進 ``cli_version_error``
        而不拋例外——診斷端點必須在環境壞掉時仍能回報「壞在哪」。
        The version probe actually executes ``claude --version``; failures
        are reported in-band so the endpoint stays useful when the
        environment is broken.

        Args:
            llm_client: 診斷用的全域 LLM client(取已解析的 CLI 路徑);None 表示
                啟動時或最後一次改全域 * 後建立失敗。The global client, or None
                when building it failed.
            provider: 全域端點的類型(claude-code / antigravity),決定是否做
                claude 專屬的探測。The global endpoint's type.

        Returns:
            dict: ``client_initialized`` / ``cli_path`` / ``cli_version``
            (或 ``cli_version_error``)/ ``effort`` /
            ``oauth_token_configured``(bool,不洩漏 token 值)/
            ``account``(訂閱方案,見 :meth:`_probe_account`)。
        """
        # 通用部分(CLI 路徑 / 版本 / effort)適用任何 CLI provider;token 格式、
        # `auth status --json`、haiku 實打三項是 claude 專屬,其他 CLI provider
        # 回 unsupported,等各自的認證模型實測後再做成 hook(計畫 P3)。
        is_claude = provider == CLAUDE_CODE_PROVIDER
        token = (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip() if is_claude else ""
        # token 格式靜態檢查(恆開,零成本):setup-token 產出應為連續字串,
        # 內含空白 = 複製斷行事故(2026-08-31 實際造成容器整晚 401)
        token_format_ok: bool | None = None
        token_format_error: str | None = None
        if token:
            if any(ch.isspace() for ch in token):
                token_format_ok = False
                token_format_error = "token 內含空白字元(疑為複製 setup-token 輸出時被斷行切開)"
            elif not token.startswith("sk-ant-"):
                token_format_ok = False
                token_format_error = "token 前綴非 sk-ant-(疑為貼錯內容)"
            else:
                token_format_ok = True

        info: dict[str, Any] = {
            "client_initialized": llm_client is not None,
            # 公開屬性優先,過渡期仍容忍只有私有屬性的物件
            "cli_path": getattr(llm_client, "cli_path", None) or getattr(llm_client, "_cli_path", None),
            "cli_version": None,
            "effort": getattr(llm_client, "effort", None) or getattr(llm_client, "_effort", None),
            # 只回報是否設定與格式判定,絕不回傳 token 內容
            "oauth_token_configured": bool(token),
            "oauth_token_format_ok": token_format_ok,
            "oauth_token_format_error": token_format_error,
            "auth_check": {"status": "skipped", "detail": "未要求(check_auth=false)"},
            "account": {"status": "unknown", "detail": "無 CLI 路徑可探測"},
        }

        cli_path = info["cli_path"]
        if not cli_path:
            info["cli_version_error"] = (
                "全域 LLM client 不存在或不是 CLI 型,無 CLI 路徑可探測(見後端 log 的「全域 LLM 客戶端」;寫入一次 * 會重建)"
            )
            return info

        def _run_version() -> subprocess.CompletedProcess[bytes]:
            # 用 client 自己的 subprocess 環境（有的話）：antigravity 的環境帶
            # AGY_CLI_DISABLE_AUTO_UPDATE，診斷端點不該成為意外觸發 agy 自動
            # 升版的入口；client 未初始化時退回繼承環境
            build_env = getattr(llm_client, "_build_env", None)
            env = build_env() if callable(build_env) else None
            return subprocess.run(
                [cli_path, "--version"], capture_output=True, timeout=15, env=env
            )

        try:
            completed = await asyncio.to_thread(_run_version)
            output = completed.stdout.decode("utf-8", errors="replace").strip()
            if completed.returncode == 0 and output:
                info["cli_version"] = output
            else:
                stderr = completed.stderr.decode("utf-8", errors="replace").strip()
                info["cli_version_error"] = (
                    f"exit={completed.returncode}: {stderr or output or '(無輸出)'}"[:300]
                )
        except Exception as e:  # noqa: BLE001 - 診斷端點必須帶病回報
            info["cli_version_error"] = f"{type(e).__name__}: {e}"[:300]

        if not is_claude:
            unsupported = {"status": "unsupported", "detail": f"{provider} 尚未實作帳號 / 認證探測"}
            info["account"] = dict(unsupported)
            if check_auth:
                info["auth_check"] = dict(unsupported)
            return info

        info["account"] = await RuntimeConfigService._probe_account(cli_path, llm_client)

        # ── 真實認證探測(check_auth=true 時):打一次最小 haiku 請求 ──
        # 版本探測只能證明 binary 可執行;token 是否真的能通過認證,
        # 只有實際打一次 model request 才知道(2026-08-31 教訓:診斷全綠
        # 但 token 內含空格,生成時才爆 401)。用 haiku 把成本壓到最低。
        if check_auth:
            if not hasattr(llm_client, "_build_env"):
                info["auth_check"] = {
                    "status": "failed",
                    "detail": "client 未初始化,無法組認證環境",
                }
                return info

            def _run_auth() -> subprocess.CompletedProcess[bytes]:
                return subprocess.run(
                    [cli_path, "-p", "reply with exactly: ok", "--model", "haiku"],
                    capture_output=True,
                    timeout=120,
                    env=llm_client._build_env(),
                )

            try:
                completed = await asyncio.to_thread(_run_auth)
                if completed.returncode == 0:
                    info["auth_check"] = {
                        "status": "ok",
                        "detail": "最小 haiku 請求成功,token 認證有效",
                    }
                else:
                    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
                    stdout = completed.stdout.decode("utf-8", errors="replace").strip()
                    info["auth_check"] = {
                        "status": "failed",
                        "detail": f"exit={completed.returncode}: {(stderr or stdout or '(無輸出)')[:300]}",
                    }
            except Exception as e:  # noqa: BLE001 - 帶病回報
                info["auth_check"] = {
                    "status": "failed",
                    "detail": f"{type(e).__name__}: {e}"[:300],
                }
        return info

    @staticmethod
    async def _probe_account(cli_path: str, llm_client: Any) -> dict[str, Any]:
        """探測登入帳號的訂閱方案(``claude auth status --json``)。

        Probe the signed-in account's subscription plan.

        只取**非識別性**欄位:是否登入、訂閱型別、認證方式、API 供應方。
        CLI 同時會回傳 email / orgId / orgName,那些一律丟棄——本區塊由
        無認證的診斷端點對外回傳,方案是運維資訊,帳號識別不是。
        Only non-identifying fields are kept; the email / org identifiers
        the CLI also returns are dropped, because this block is exposed by
        an unauthenticated diagnostic endpoint.

        兩層限制(2026-09-04 實測):
        1. 粒度只到 ``max`` / ``pro``,**不區分 Max 5x 與 20x**——倍率不在
           任何非互動輸出裡。
        2. **注入 token 認證時整個帳號 profile 都不在輸出裡**——實測欄位
           集合:落盤憑證模式回 8 個欄位(含 email / orgId / orgName /
           subscriptionType),注入 token 模式只回 4 個
           (``loggedIn`` / ``authMethod`` / ``apiProvider`` /
           ``analyticsDisabled``),profile 那組**鍵直接不存在**。合理的解釋
           是 profile 隨桌機登入流程落盤保存,而裸 token 沒有這份紀錄、
           ``auth status`` 也不為此發網路請求(此為由欄位集合推得,未讀 CLI
           原始碼)。容器部署正是後者,故方案為 None——CLI 的行為,不是探測
           失敗。另注意該模式的 ``loggedIn: true`` 只代表「token 已設定」,
           要證明 token 真能通過認證仍得靠 ``auth_check`` 的實打請求。
        Neither the Max multiplier nor, under injected-token auth, the
        subscription type itself is exposed by the CLI.

        不做的事:token 模式拿不到方案時**不**改用預設環境重探。落盤憑證
        可能屬於另一個帳號,回報那個帳號的方案等於報錯資訊。
        Deliberately does not re-probe without the injected credentials:
        on-disk credentials may belong to a different account.

        帶著 client 的認證環境跑:容器以注入 token 認證、沒有落盤憑證,
        用預設環境會誤報未登入。
        Runs with the client's credential environment so headless
        token-based setups are not misreported as signed out.

        Args:
            cli_path: 已解析的 CLI 路徑。Resolved CLI path.
            llm_client: 活的 LLM client(取認證環境)。The live client.

        Returns:
            dict: ``status`` 為 ``ok`` 時帶 ``logged_in`` /
            ``subscription_type`` / ``auth_method`` / ``api_provider``;
            探測不到時為 ``unknown`` 加 ``detail``。
        """
        env = llm_client._build_env() if hasattr(llm_client, "_build_env") else None

        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [cli_path, "auth", "status", "--json"],
                capture_output=True, timeout=20, env=env,
            )

        try:
            completed = await asyncio.to_thread(_run)
        except Exception as e:  # noqa: BLE001 - 帶病回報
            return {"status": "unknown", "detail": f"{type(e).__name__}: {e}"[:300]}

        stdout = completed.stdout.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            return {
                "status": "unknown",
                "detail": f"exit={completed.returncode}: {(stderr or stdout or '(無輸出)')[:300]}",
            }
        try:
            payload = json.loads(stdout)
        except (json.JSONDecodeError, TypeError):
            return {
                "status": "unknown",
                "detail": f"輸出非 JSON(CLI 版本可能不支援 auth status --json):{stdout[:120]}",
            }
        if not isinstance(payload, dict):
            return {"status": "unknown", "detail": "輸出 JSON 不是物件"}
        return {
            "status": "ok",
            "logged_in": bool(payload.get("loggedIn")),
            "subscription_type": payload.get("subscriptionType"),
            "auth_method": payload.get("authMethod"),
            "api_provider": payload.get("apiProvider"),
        }
