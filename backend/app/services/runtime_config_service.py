"""執行期設定服務(唯讀切片):白名單設定列表與後端 runtime 對帳資訊。

Runtime configuration service (read-only slice): whitelisted settings
listing and backend runtime reconciliation info.

對應計畫 docs/wip/runtime_config_service_FEAT_2026-08-29.md §3.5。
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
import subprocess
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.dynamic_config import get_modifiable_configs

# 改值後需要重建 singleton 的設定鍵(計畫 §3.5 REBUILD_REGISTRY 的鍵集合;
# 對應的 factory 綁定在寫入側實作時補上)。
# Keys whose changes require a singleton rebuild.
REQUIRES_REBUILD_KEYS: frozenset[str] = frozenset({
    "AUDIO_EVALUATOR_PROVIDER",
    "LLM_MODEL_NAME",
    "LLM_PROVIDER",
    "LLM_CLAUDE_CODE_EFFORT",
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

    async def get_runtime_info(self, app_state: Any) -> dict[str, Any]:
        """取得後端 runtime 對帳資訊(計畫 §3.5 的 ``runtime`` 區塊)。

        Build the backend runtime reconciliation block.

        Args:
            app_state: FastAPI 的 ``app.state``(取活的 llm_client 用)。
                The FastAPI ``app.state`` carrying the live llm_client.

        Returns:
            dict: ``llm_label``(活 client 算好的顯示標籤,client 為 None
            時為 None)、``llm_provider``、``anki_connect_url``(後端實際
            連線的 Anki 端點,供腳本啟動對帳),以及 provider 為
            claude-code 時的 ``claude_code`` 環境診斷區塊(其他 provider
            為 None)。
        """
        llm_client = getattr(app_state, "llm_client", None)
        # _formatted_model_name 是三種 client 共同的標籤欄位;
        # 取不到則退回 None(不自行推導,見計畫警告)
        llm_label = getattr(llm_client, "_formatted_model_name", None)
        provider = (settings.LLM_PROVIDER or "").strip().lower() or None

        claude_code: dict[str, Any] | None = None
        if provider == "claude-code":
            claude_code = await self._probe_claude_code(llm_client)

        return {
            "llm_label": llm_label,
            "llm_provider": provider,
            "anki_connect_url": settings.ANKI_CONNECT_URL,
            "claude_code": claude_code,
        }

    @staticmethod
    async def _probe_claude_code(llm_client: Any) -> dict[str, Any]:
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
            llm_client: 活的 LLM client(取已解析的 CLI 路徑);None 表示
                後端啟動時初始化失敗。The live client, or None when
                startup initialization failed.

        Returns:
            dict: ``client_initialized`` / ``cli_path`` / ``cli_version``
            (或 ``cli_version_error``)/ ``effort`` /
            ``oauth_token_configured``(bool,不洩漏 token 值)。
        """
        info: dict[str, Any] = {
            "client_initialized": llm_client is not None,
            "cli_path": getattr(llm_client, "_cli_path", None),
            "cli_version": None,
            "effort": getattr(llm_client, "_effort", None),
            # 只回報是否設定,絕不回傳 token 內容
            "oauth_token_configured": bool(
                (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip()
            ),
        }

        cli_path = info["cli_path"]
        if not cli_path:
            info["cli_version_error"] = (
                "LLM client 未初始化,無 CLI 路徑可探測(檢查後端啟動 log)"
            )
            return info

        def _run_version() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [cli_path, "--version"], capture_output=True, timeout=15
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
        return info
