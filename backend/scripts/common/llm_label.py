"""LLM 模型標籤（腳本側薄包裝）。

Thin script-side wrapper over the backend's single label rule.

規則本體在 ``app/infrastructure/llm/labels.py``（``build_model_label``），
provider 的差異（要不要去 ``claude-`` 前綴、effort 讀哪個設定）由各
client 類別的類別屬性宣告，本函式經工廠的 ``resolve_client_class`` 取得，
不再自己比對 provider 字串。

【定位（2026-08-31 降級）】本函式依**本機** settings 推導，而後端與腳本的
.env 是兩份檔案，可能不一致（2026-08-28 曾因此錯標 190 筆 DB 紀錄）。
因此成功生成的 DB 紀錄一律改取**生成 API 回應中的 ``llm_model``**
（後端單一事實來源）；本函式僅剩兩個正當用途：
①失敗紀錄（生成未發生，無回應可取）②回應缺 ``llm_model`` 欄時的 fallback。
Downgraded role: success records take ``llm_model`` from the generation
API response; this local derivation only serves failure records and the
missing-field fallback.
"""

from app.core.config import settings
from app.infrastructure.llm.factory import resolve_client_class
from app.infrastructure.llm.labels import build_model_label


def build_llm_model_label() -> str:
    """依目前 settings 組出 LLM 模型標籤。

    Build the LLM model label from the current settings.

    Returns:
        str: 例如 ``(claude-code)opus-5@medium``；google/openai 等
        provider 不加前綴與力度，維持裸模型名。E.g.
        ``(claude-code)opus-5@medium``; bare model name for
        google/openai-style providers.
    """
    client_cls = resolve_client_class(settings.LLM_PROVIDER)
    return build_model_label(
        settings.LLM_PROVIDER,
        settings.LLM_MODEL_NAME,
        client_cls.default_effort(),
        strip_prefix=client_cls.MODEL_LABEL_STRIP_PREFIX,
    )
