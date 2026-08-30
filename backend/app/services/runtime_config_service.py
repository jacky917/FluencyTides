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

    def get_runtime_info(self, app_state: Any) -> dict[str, str | None]:
        """取得後端 runtime 對帳資訊(計畫 §3.5 的 ``runtime`` 區塊)。

        Build the backend runtime reconciliation block.

        Args:
            app_state: FastAPI 的 ``app.state``(取活的 llm_client 用)。
                The FastAPI ``app.state`` carrying the live llm_client.

        Returns:
            dict: ``llm_label``(活 client 算好的顯示標籤,client 為 None
            時為 None)、``llm_provider``、``anki_connect_url``(後端實際
            連線的 Anki 端點,供腳本啟動對帳)。
        """
        llm_client = getattr(app_state, "llm_client", None)
        # _formatted_model_name 是兩種 client 共同的標籤欄位;
        # 通用 LLMClient 若無此欄位則退回 None(不自行推導,見計畫警告)
        llm_label = getattr(llm_client, "_formatted_model_name", None)
        return {
            "llm_label": llm_label,
            "llm_provider": (settings.LLM_PROVIDER or "").strip().lower() or None,
            "anki_connect_url": settings.ANKI_CONNECT_URL,
        }
