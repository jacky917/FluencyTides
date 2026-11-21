"""Single assembly point for LLM model labels (plan D6) and the single provider-normalization rule.

LLM 模型標籤的唯一組裝點（計畫 D6），以及 provider 值的唯一正規化函式。

The label is written to Anki tags and to ``card_gen_log.llm_model`` (formerly ``generated_sentences_log``)
in the format ``(provider)model@effort``:

- when ``provider`` is ``google``, ``openai``, or empty, no prefix is added (keeping the bare model name of
  historical data)
- every other provider is prefixed with ``(provider)``
- when ``effort`` is not empty, ``@effort`` is appended (stripped and lowercased, matching the value the CLI
  clients accept after validation)
- ``strip_prefix`` can remove a redundant model-name prefix (for example ``claude-opus-5`` → ``opus-5``,
  because the provider part already says claude-code)

標籤寫入 Anki tag 與 ``card_gen_log.llm_model``（原 ``generated_sentences_log``），格式 ``(provider)model@effort``：

- ``provider`` 為 ``google`` / ``openai`` / 空 時不加前綴（沿用歷史資料的裸模型名）
- 其餘 provider 冠 ``(provider)``
- ``effort`` 非空時追加 ``@effort``（去空白、小寫——與 CLI 客戶端驗證後的值一致）
- ``strip_prefix`` 可去掉模型名的冗餘前綴（如 ``claude-opus-5`` → ``opus-5``，因
  provider 段已寫明 claude-code）

Before it was extracted, this rule was scattered across three places (``client.py`` /
``claude_code_client.py`` / ``scripts/common/llm_label.py``); on 2026-08-28 two inconsistent ``.env`` files
caused 190 DB records to be mislabelled.

抽出前這條規則散落三處（``client.py`` / ``claude_code_client.py`` /
``scripts/common/llm_label.py``），2026-08-28 曾因兩份 ``.env`` 不一致誤標
190 筆 DB 紀錄。

This module is a leaf of the ``llm`` package (it depends only on the standard library). Both ``base.py`` and
``factory.py`` take :func:`normalize_provider` from here, so the provider value used for routing and the one
used for labels are guaranteed to come from the same rule.

本模組是 ``llm`` 套件的葉節點（只依賴標準庫），``base.py`` 與 ``factory.py``
都從這裡取 ``normalize_provider``，路由用的 provider 值與標籤用的 provider
值因此保證出自同一條規則。
"""

# 不冠 provider 前綴的服務商：歷史資料以裸模型名記錄，保持相容
# Providers whose labels stay as the bare model name for compatibility.
NO_PREFIX_PROVIDERS: frozenset[str] = frozenset({"google", "openai", ""})


def normalize_provider(value: str | None) -> str:
    """Normalize a provider value by stripping whitespace and lowercasing it.

    正規化 provider 值（去空白、小寫）。

    Args:
        value: the raw provider value; ``None`` is treated as empty
            原始 provider 值；``None`` 視為空字串

    Returns:
        the normalized provider; an empty string for ``None`` or blank input
        正規化後的 provider；``None`` 或空白輸入時為空字串
    """
    return (value or "").strip().lower()


def build_model_label(
    provider: str | None,
    model: str,
    effort: str | None = None,
    *,
    strip_prefix: str = "",
) -> str:
    """Build the ``(provider)model@effort`` label.

    組出 ``(provider)model@effort`` 格式的標籤。

    Args:
        provider: the ``LLM_PROVIDER`` value; case- and surrounding-whitespace-insensitive
            ``LLM_PROVIDER`` 值；大小寫與前後空白不敏感
        model: the model name
            模型名
        effort: the effort level; ``None`` or empty means the provider has no such notion. Case- and
            whitespace-insensitive.
            推理力度；``None`` / 空表示該 provider 無此概念。大小寫與空白不敏感。
        strip_prefix: the prefix to strip from the model name (for example ``"claude-"``)
            要從模型名去掉的前綴（如 ``"claude-"``）

    Returns:
        the label string
        標籤字串
    """
    normalized = normalize_provider(provider)
    display_model = model.removeprefix(strip_prefix) if strip_prefix else model
    label = display_model if normalized in NO_PREFIX_PROVIDERS else f"({normalized}){display_model}"
    normalized_effort = (effort or "").strip().lower()
    if normalized_effort:
        label = f"{label}@{normalized_effort}"
    return label
