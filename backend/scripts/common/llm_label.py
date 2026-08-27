"""LLM 模型標籤的統一組裝（寫入 Anki tag 與 generated_sentences_log 用）。

Single source of truth for the LLM model label written to Anki tags and
generated_sentences_log.

格式：``(provider)model@effort``，其中 model 去掉 ``claude-`` 前綴
（``claude-opus-5`` → ``opus-5``）——provider 段已寫明 claude-code，
模型名再帶 claude- 是冗餘。與後端
``app/infrastructure/llm/claude_code_client.py`` 的 ``_formatted_model_name``
同一規則；改格式時兩處要一起動。
Format: ``(provider)model@effort`` with the ``claude-`` prefix stripped
from the model part, mirroring the backend client's label rule.
"""

from app.core.config import settings


def build_llm_model_label() -> str:
    """依目前 settings 組出 LLM 模型標籤。

    Build the LLM model label from the current settings.

    Returns:
        str: 例如 ``(claude-code)opus-5@medium``；google/openai 等
        provider 不加前綴與力度，維持裸模型名。E.g.
        ``(claude-code)opus-5@medium``; bare model name for
        google/openai-style providers.
    """
    label = settings.LLM_MODEL_NAME.removeprefix("claude-")
    provider = (settings.LLM_PROVIDER or "").lower()
    if provider and provider not in ("google", "openai", ""):
        label = f"({settings.LLM_PROVIDER}){label}"
    # claude-code provider 的推理力度會影響生成品質，
    # 需一併記錄才能事後區分同模型不同力度產出的卡片。
    if provider == "claude-code":
        label = f"{label}@{settings.LLM_CLAUDE_CODE_EFFORT}"
    return label
