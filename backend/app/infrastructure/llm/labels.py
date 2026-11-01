"""
LLM 模型標籤的唯一組裝點（計畫 D6），以及 provider 值的唯一正規化函式。

標籤寫入 Anki tag 與 ``card_gen_log.llm_model``（原 ``generated_sentences_log``），格式 ``(provider)model@effort``：

- ``provider`` 為 ``google`` / ``openai`` / 空 時不加前綴（沿用歷史資料的裸模型名）
- 其餘 provider 冠 ``(provider)``
- ``effort`` 非空時追加 ``@effort``（去空白、小寫——與 CLI 客戶端驗證後的值一致）
- ``strip_prefix`` 可去掉模型名的冗餘前綴（如 ``claude-opus-5`` → ``opus-5``，因
  provider 段已寫明 claude-code）

抽出前這條規則散落三處（``client.py`` / ``claude_code_client.py`` /
``scripts/common/llm_label.py``），2026-08-28 曾因兩份 ``.env`` 不一致誤標
190 筆 DB 紀錄。

本模組是 ``llm`` 套件的葉節點（只依賴標準庫），``base.py`` 與 ``factory.py``
都從這裡取 :func:`normalize_provider`，路由用的 provider 值與標籤用的 provider
值因此保證出自同一條規則。

English summary:
    Single source of truth for the model label written to Anki tags and
    the generation log, plus the single provider-normalization rule shared
    by routing (factory) and labelling (base). ``google``/``openai``/empty
    providers keep the bare model name for backward compatibility; every
    other provider is prefixed; effort is normalized and appended when
    present.
"""

# 不冠 provider 前綴的服務商：歷史資料以裸模型名記錄，保持相容
# Providers whose labels stay as the bare model name for compatibility.
NO_PREFIX_PROVIDERS: frozenset[str] = frozenset({"google", "openai", ""})


def normalize_provider(value: str | None) -> str:
    """正規化 provider 值（去空白、小寫）。Normalize a provider value."""
    return (value or "").strip().lower()


def build_model_label(
    provider: str | None,
    model: str,
    effort: str | None = None,
    *,
    strip_prefix: str = "",
) -> str:
    """組出 ``(provider)model@effort`` 格式的標籤。

    Build the ``(provider)model@effort`` label.

    Args:
        provider: ``LLM_PROVIDER`` 值；大小寫與前後空白不敏感。The provider
            value; case- and whitespace-insensitive.
        model: 模型名。The model name.
        effort: 推理力度；None / 空表示該 provider 無此概念。大小寫與空白
            不敏感。Effort level, or None/empty when the provider has no
            such notion; case- and whitespace-insensitive.
        strip_prefix: 要從模型名去掉的前綴（如 ``"claude-"``）。Prefix to
            strip from the model name.

    Returns:
        標籤字串。The label.
    """
    normalized = normalize_provider(provider)
    display_model = model.removeprefix(strip_prefix) if strip_prefix else model
    label = display_model if normalized in NO_PREFIX_PROVIDERS else f"({normalized}){display_model}"
    normalized_effort = (effort or "").strip().lower()
    if normalized_effort:
        label = f"{label}@{normalized_effort}"
    return label
