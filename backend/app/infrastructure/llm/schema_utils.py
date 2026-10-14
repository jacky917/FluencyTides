"""
LLM 結構化輸出的 JSON Schema 工具函式。

從 :class:`~app.infrastructure.llm.client.LLMClient` 抽出的兩個純函式，
供所有 provider 共用（計畫 D6）。抽出前 ``ClaudeCodeLLMClient`` 必須跨類直呼
``LLMClient._resolve_json_schema``，等於把兩個無狀態工具綁在一個具體實作上。

English summary:
    Pure JSON Schema helpers shared by every LLM provider: ``$ref``
    flattening for backends that reject ``$defs`` (Gemini, the Claude Code
    CLI) and Markdown code-fence stripping for models that wrap JSON output
    in fences even under structured-output mode.
"""


from typing import cast


def resolve_json_schema(
    schema: dict[str, object], defs: dict[str, object] | None = None
) -> dict[str, object]:
    """遞迴展開 JSON Schema 中的 ``$ref``，並移除 ``$defs``。

    Recursively inline ``$ref`` entries in a JSON Schema and drop ``$defs``.

    因部分 LLM（如 Gemini 的 Structured Outputs 實作、Claude Code CLI 的
    ``--json-schema``）不支援 ``$defs`` 與 ``$ref``，必須在發送前攤平。

    Args:
        schema: Pydantic 產生的 JSON Schema。The Pydantic-generated JSON
            Schema.
        defs: 最上層提取出來的 ``$defs`` 字典。The top-level ``$defs`` dict.

    Returns:
        展開所有 ``$ref`` 並移除 ``$defs`` 後的 schema。The schema with all
        ``$ref`` inlined and ``$defs`` removed.
    """
    if defs is None:
        defs = cast("dict[str, object]", schema.get("$defs", {}))
    # dict 進必為 dict 出（_inline_refs 對 dict 只回 merged 或 dict 推導式）
    return cast("dict[str, object]", _inline_refs(schema, defs))


def _inline_refs(node: object, defs: dict[str, object]) -> object:
    """``resolve_json_schema`` 的遞迴本體；子節點可能是 dict / list / 純量。

    Recursive worker for ``resolve_json_schema``; sub-nodes may be dicts,
    lists, or scalars.
    """
    if isinstance(node, dict):
        if "$ref" in node:
            ref_name = str(node["$ref"]).split("/")[-1]
            resolved = _inline_refs(defs.get(ref_name, {}), defs)
            # 保留其他非 $ref 屬性並合併展開後的內容
            merged: dict[str, object] = {k: v for k, v in node.items() if k != "$ref"}
            if isinstance(resolved, dict):
                merged.update(resolved)
            return merged
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


def strip_markdown_fences(content: str) -> str:
    """清理 LLM 回傳中可能包含的 Markdown 程式碼區塊標記。

    Strip Markdown code-fence markers that an LLM response may contain.

    部分 LLM 即使在 response_format 模式下，仍可能在回傳內容前後
    加上 ```json ... ``` 標記，需要清除後才能正確解析 JSON。

    Args:
        content: LLM 的原始回傳字串。The raw LLM response string.

    Returns:
        清理後的純 JSON 字串。The cleaned pure-JSON string.
    """
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    return cleaned.strip()
