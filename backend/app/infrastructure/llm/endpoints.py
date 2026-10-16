"""
LLM 端點與白名單（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D5）。

**端點**＝一家可呼叫的服務（一個中轉站、一把 Google 金鑰、一個本機 CLI），以唯一
ID 識別；卡片標籤的 ``(前綴)`` 就是端點 ID。每個端點帶一份白名單，列出可選的
「模型 + effort」組合。端點與白名單全部來自 ``.env``——DB 只記「目前選了哪一組」
（見 :mod:`app.infrastructure.llm.routing`）。

端點的三個來源：

- **自訂端點**：``LLM_ENDPOINTS__<ID>__TYPE / BASE_URL / API_KEY / MODELS /
  SAFETY_SETTINGS``，任意多個
- **內建 CLI 端點**：``claude-code`` / ``antigravity``，白名單為
  ``LLM_CLAUDE_CODE_MODELS`` / ``LLM_ANTIGRAVITY_MODELS``
- **舊式單組設定**：``LLM_PROVIDER`` / ``LLM_BASE_URL`` / ``LLM_API_KEY`` /
  ``LLM_MODEL_NAME``，自動合成為 ID＝``LLM_PROVIDER`` 的端點，即「.env 那一層」。
  這一組（含 CLI 的 ``LLM_*_EFFORT``）自動加進白名單，所以現有 ``.env`` 不必改。
  ``LLM_ENDPOINTS`` 定義了與 ``LLM_PROVIDER`` 同 ID 的端點時改用它（工廠在不指定
  端點時也會走它，見 :func:`env_endpoint_is_custom`）

白名單每項寫成卡片標籤的 ``模型@effort`` 形式：``"opus@low|high"`` 允許兩個
effort，不帶 ``@`` 表示此模型不使用 effort。以**最後一個** ``@`` 切開，讓模型名本身
可以含 ``@``；模型名也可含 ``/``、``:``（如 OpenRouter 的 ``deepseek/deepseek-r1:free``）。
代價：不支援 effort 的端點上，名字含 ``@`` 的模型會被當成帶 effort 而跳過。

設定寫錯（ID 不合規、缺金鑰、``MODELS`` 不是 JSON、欄位名拼錯、effort 拼錯…）只
跳過那個端點或那一項並記 ERROR，不讓整個後端起不來；同一則錯誤只記一次。後端啟動
時以 :func:`log_endpoint_summary` 載入一次，錯誤因此出現在啟動 log。

指定端點的路徑都經過 :func:`get_endpoint` / :func:`list_endpoints`；不指定端點的
舊路徑（``LLM_PROVIDER`` 沒有對應的 ``LLM_ENDPOINTS``）仍由工廠與客戶端直接讀
settings。本模組不 import 任何客戶端類別（避免把 openai / jsonschema / google-genai
拉進來）；各 CLI 的 effort 值域以 :data:`EFFORT_VALUES` 為唯一來源，客戶端的
``VALID_EFFORTS`` 直接取自這裡。

English summary:
    LLM endpoints and their whitelists, all sourced from ``.env``: custom
    ``LLM_ENDPOINTS__<ID>__*`` endpoints, the built-in CLI endpoints, and the
    legacy single-endpoint settings synthesized into an endpoint whose ID is
    ``LLM_PROVIDER`` (auto-whitelisted, so existing ``.env`` files keep
    working). Whitelist entries are ``model`` or ``model@e1|e2``. Bad
    settings are skipped with a one-time ERROR instead of failing startup.
"""

import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import SecretStr, ValidationError

from app.core.config import LLMEndpointSettings, settings
from app.infrastructure.llm.labels import normalize_provider

logger = logging.getLogger(__name__)

# 端點類型。openai / gemini-native 可在 .env 自訂多個；CLI 類各有一個內建端點；
# anthropic 只在 LLM_PROVIDER=anthropic 時以不可選的佔位端點出現
# Endpoint types; only openai / gemini-native can be defined freely in .env.
TYPE_OPENAI = "openai"
TYPE_GEMINI_NATIVE = "gemini-native"
TYPE_CLAUDE_CODE = "claude-code"
TYPE_ANTIGRAVITY = "antigravity"
TYPE_ANTHROPIC = "anthropic"

ALL_TYPES: frozenset[str] = frozenset(
    {TYPE_OPENAI, TYPE_GEMINI_NATIVE, TYPE_CLAUDE_CODE, TYPE_ANTIGRAVITY, TYPE_ANTHROPIC}
)
CUSTOM_TYPES: frozenset[str] = frozenset({TYPE_OPENAI, TYPE_GEMINI_NATIVE})

# 自訂端點不可使用的 ID：對應非 OpenAI 相容客戶端的 provider 值。google / openai 不在
# 內——它們本來就是 OpenAI 相容類的合法 ID，可用 LLM_ENDPOINTS 重新定義
# IDs naming non-OpenAI-compatible clients; google / openai stay definable.
RESERVED_IDS: frozenset[str] = frozenset(
    {TYPE_CLAUDE_CODE, TYPE_ANTIGRAVITY, TYPE_GEMINI_NATIVE, TYPE_ANTHROPIC}
)

# 各端點類型接受的 effort 值（唯一來源，CLI 客戶端的 VALID_EFFORTS 取自這裡）；
# 不在表內的類型不支援 effort
# Accepted effort values per endpoint type — the single source of truth.
EFFORT_VALUES: dict[str, frozenset[str]] = {
    TYPE_CLAUDE_CODE: frozenset({"low", "medium", "high", "xhigh", "max"}),
    TYPE_ANTIGRAVITY: frozenset({"low", "medium", "high", "xhigh", "max"}),
}

# 不送 safety_settings 的端點 ID：Google 的 OpenAI 相容端點與 OpenAI 官方嚴格遵守
# OpenAI Schema，不認識該欄位會 400；只有第三方中轉站支援經 extra_body 傳遞。
# 與 labels.NO_PREFIX_PROVIDERS 值相同但語意不同（那個決定標籤前綴），刻意不共用
# Endpoint IDs that must not receive safety_settings by default.
NO_SAFETY_SETTINGS_IDS: frozenset[str] = frozenset({"google", "openai", ""})

# 自訂端點 ID：環境變數名稱不能含 "-"；不可含 "__"（巢狀分隔符會把 ID 切開）；
# pydantic-settings 會把鍵轉成小寫
_ID_PATTERN = re.compile(r"(?!.*__)[a-z0-9_]{1,32}")

# 與 llm_routes.model 欄位長度一致
MODEL_NAME_MAX_LENGTH = 128

_KNOWN_FIELDS = "TYPE / BASE_URL / API_KEY / MODELS / SAFETY_SETTINGS"
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})

# 同一則設定問題只記一次（每次建立客戶端都會重新載入端點）
_reported_problems: set[str] = set()


@dataclass(frozen=True)
class ModelOption:
    """白名單中的一個模型。One whitelisted model.

    Attributes:
        name: 模型名（CLI 類為基底名），非空。Model name, non-empty.
        efforts: 允許的 effort（不重複）；空＝此模型不使用 effort。Allowed
            efforts, unique; empty means the model takes no effort.
    """

    name: str
    efforts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("ModelOption.name 不可為空")
        if not all(self.efforts) or len(set(self.efforts)) != len(self.efforts):
            raise ValueError(f"ModelOption.efforts 不可有空值或重複：{self.efforts!r}")

    def allows(self, effort: str | None) -> bool:
        """``effort`` 是否合法：帶 effort 的模型必須指定其一，不帶的必須為空。

        Whether ``effort`` is valid for this model: one of ``efforts`` when
        the model takes effort, otherwise empty.
        """
        if self.efforts:
            return effort in self.efforts
        return not effort


@dataclass(frozen=True)
class Endpoint:
    """一個可呼叫的 LLM 端點。One callable LLM endpoint.

    建構時檢查欄位之間的一致性（類型、safety_settings、白名單）；端點是否「可呼叫」
    （金鑰、網址是否齊全）由客戶端建構時檢查——舊式合成端點可能缺金鑰，客戶端照舊
    拋「LLM_API_KEY 未設定」。

    Attributes:
        id: 唯一 ID，標籤前綴的來源（google / openai / 空字串不加前綴）。
            Unique ID and source of the label prefix.
        type: 端點類型（:data:`ALL_TYPES`），決定客戶端類別。Endpoint type.
        base_url: OpenAI 相容端點網址。Base URL (OpenAI-compatible only).
        api_key: API 金鑰。API key.
        models: 白名單，模型名不重複。The whitelist.
        send_safety_settings: 是否送 safety_settings（只有 openai 類可為 True）。
            Whether to send safety_settings (openai type only).
        listed: 是否可在管理介面選擇；``LLM_PROVIDER`` 留空時的合成端點與 anthropic
            佔位端點為 False。Whether selectable in the admin menu.
        api_key_setting: 金鑰的設定名（錯誤訊息用）。Setting name of the key.
        base_url_setting: 網址的設定名（錯誤訊息用）。Setting name of the URL.
    """

    id: str
    type: str
    base_url: str | None = None
    api_key: SecretStr | None = field(default=None, repr=False)
    models: tuple[ModelOption, ...] = ()
    send_safety_settings: bool = False
    listed: bool = True
    api_key_setting: str = "LLM_API_KEY"
    base_url_setting: str = "LLM_BASE_URL"

    def __post_init__(self) -> None:
        if self.type not in ALL_TYPES:
            raise ValueError(f"未知的端點類型 {self.type!r}")
        if self.send_safety_settings and self.type != TYPE_OPENAI:
            raise ValueError("只有 openai 類端點可以送 safety_settings")
        names = [option.name for option in self.models]
        if len(set(names)) != len(names):
            raise ValueError(f"端點 {self.id!r} 的白名單有重複模型：{names}")
        allowed = EFFORT_VALUES.get(self.type, frozenset())
        for option in self.models:
            if not set(option.efforts) <= allowed:
                raise ValueError(f"端點 {self.id!r} 的模型 {option.name!r} 含此類型不支援的 effort")

    def find_model(self, name: str) -> ModelOption | None:
        """白名單中名為 ``name`` 的模型。The whitelisted model named ``name``."""
        return next((option for option in self.models if option.name == name), None)

    def allows(self, model: str, effort: str | None) -> bool:
        """``(model, effort)`` 是否在白名單內。Whether ``(model, effort)`` is whitelisted."""
        option = self.find_model(model)
        return option is not None and option.allows(effort)


def _report(message: str, level: int = logging.ERROR) -> None:
    """同一則設定問題只記一次。Log a configuration problem once per message."""
    if message in _reported_problems:
        return
    _reported_problems.add(message)
    logger.log(level, message)


def coerce_entries(raw: Any, *, setting_name: str) -> tuple[str, ...] | None:
    """把白名單設定值（``.env`` 的 JSON 字串，或程式給的序列）轉成字串 tuple。

    Coerce a raw whitelist setting (a JSON string from ``.env``, or a
    sequence given programmatically) into a tuple of strings.

    Returns:
        字串 tuple；格式錯誤時記 ERROR 並回 None。The entries, or None after
        reporting a malformed value.
    """
    value = raw
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            _report(
                f"{setting_name} 不是 JSON 陣列（應為 [\"模型\", \"模型@low|high\"]，用雙引號與中括號）：{text[:80]}"
            )
            return None
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        _report(f"{setting_name} 必須是字串陣列：{str(raw)[:80]}")
        return None
    return tuple(value)


def _parse_bool(raw: Any) -> tuple[bool | None, bool]:
    """把 ``true`` / ``false`` 之類的設定值轉成布林。Parse a boolean-ish setting.

    Returns:
        ``(值, 是否可辨識)``；未設為 ``(None, True)``，無法辨識為 ``(None, False)``。
        ``(value, recognized)``.
    """
    if raw is None or isinstance(raw, bool):
        return raw, True
    word = str(raw).strip().lower()
    if not word:
        return None, True
    if word in _TRUE_WORDS:
        return True, True
    if word in _FALSE_WORDS:
        return False, True
    return None, False


def parse_whitelist(
    entries: tuple[str, ...] | list[str], *, endpoint_id: str, endpoint_type: str
) -> tuple[ModelOption, ...]:
    """把白名單字串解析成 :class:`ModelOption`，不合格的項目跳過並記 ERROR。

    Parse whitelist strings into model options, skipping (and reporting)
    malformed entries.

    同一模型寫多項時合併 effort（依出現順序、去重）。同一模型同時出現「不帶
    effort」與「帶 effort」的寫法時，視為必須帶 effort，並記 ERROR 提醒。

    Args:
        entries: 白名單字串。Whitelist entries.
        endpoint_id: 端點 ID（錯誤訊息用）。Endpoint ID, for messages.
        endpoint_type: 端點類型，決定可用的 effort 值。Endpoint type.

    Returns:
        依首次出現順序排列的模型。Models in first-seen order.
    """
    allowed = EFFORT_VALUES.get(endpoint_type)
    merged: dict[str, list[str]] = {}
    plain: set[str] = set()
    for raw in entries:
        item = str(raw).strip()
        if not item:
            continue
        name, separator, effort_part = item.rpartition("@")
        if not separator:
            name, efforts = item, []
        else:
            name = name.strip()
            efforts = [part.strip().lower() for part in effort_part.split("|")]
            if not name or not all(efforts):
                _report(
                    f"LLM 端點 '{endpoint_id}' 的白名單項目 {item!r} 格式錯誤"
                    "（應為 模型 或 模型@effort1|effort2），已跳過。"
                )
                continue
            if allowed is None:
                _report(f"LLM 端點 '{endpoint_id}'（{endpoint_type}）不支援 effort，白名單項目 {item!r} 已跳過。")
                continue
            invalid = [effort for effort in efforts if effort not in allowed]
            if invalid:
                _report(
                    f"LLM 端點 '{endpoint_id}' 的白名單項目 {item!r} 含無效 effort {invalid}"
                    f"（可用：{', '.join(sorted(allowed))}），已跳過。"
                )
                continue
        if len(name) > MODEL_NAME_MAX_LENGTH:
            _report(f"LLM 端點 '{endpoint_id}' 的模型名超過 {MODEL_NAME_MAX_LENGTH} 字元，已跳過：{name[:40]}…")
            continue
        slot = merged.setdefault(name, [])
        if not efforts:
            plain.add(name)
        for effort in efforts:
            if effort not in slot:
                slot.append(effort)

    for name in sorted(plain):
        if merged[name]:
            _report(
                f"LLM 端點 '{endpoint_id}' 的模型 '{name}' 同時寫了帶與不帶 effort 的項目，"
                f"視為必須帶 effort（{'|'.join(merged[name])}）。"
            )
    return tuple(ModelOption(name, tuple(efforts)) for name, efforts in merged.items())


def _with_env_option(endpoint: Endpoint, extra: ModelOption) -> Endpoint:
    """把「.env 那一層」的組合併進端點的白名單。

    Merge the ``.env`` layer's choice into the endpoint's whitelist.

    - 白名單沒有這個模型：加進去
    - 白名單已有、且兩者都帶 effort：補上 ``.env`` 的預設 effort
    - 白名單把它寫成**不帶** effort、``.env`` 卻帶 effort：尊重白名單，不改寫（否則
      DB 中 effort 為空的設定會被判為不合格），記 WARNING 說明 ``.env`` 預設未列入
    """
    current = endpoint.find_model(extra.name)
    if current is None:
        return replace(endpoint, models=endpoint.models + (extra,))
    if not current.efforts and extra.efforts:
        _report(
            f"LLM 端點 '{endpoint.id}' 的白名單把 '{extra.name}' 寫成不帶 effort，"
            f".env 預設的 {extra.name}@{'|'.join(extra.efforts)} 不列入白名單（.env 那一層照常使用它）。",
            logging.WARNING,
        )
        return endpoint
    efforts = current.efforts + tuple(e for e in extra.efforts if e not in current.efforts)
    models = tuple(ModelOption(o.name, efforts) if o is current else o for o in endpoint.models)
    return replace(endpoint, models=models)


def _custom_endpoint(raw_id: str, raw: Any) -> Endpoint | str:
    """把一組 ``LLM_ENDPOINTS__<ID>__*`` 轉成端點；不合格時記 ERROR 並回傳跳過原因。

    Build an endpoint from one ``LLM_ENDPOINTS`` entry, or report it and
    return the reason it was skipped.
    """
    endpoint_id = raw_id.strip().lower()
    prefix = f"LLM_ENDPOINTS__{raw_id.strip().upper()}"

    def skip(reason: str) -> str:
        _report(f"{prefix}：{reason}，已跳過此端點。")
        return reason

    if not _ID_PATTERN.fullmatch(endpoint_id) or endpoint_id in RESERVED_IDS:
        return skip(
            f"端點 ID '{raw_id}' 不合規（只能用小寫英數字與單一 _、長度 ≤ 32，"
            f"且不可為 {', '.join(sorted(RESERVED_IDS))}）"
        )
    try:
        config = raw if isinstance(raw, LLMEndpointSettings) else LLMEndpointSettings.model_validate(raw)
    except ValidationError as error:
        return skip(
            "結構錯誤（應為 LLM_ENDPOINTS__<ID>__<欄位>=值，欄位為 "
            f"{_KNOWN_FIELDS}）：{error.errors()[0]['msg']}"
        )

    for unknown in sorted(config.model_extra or {}):
        _report(f"{prefix}__{unknown.upper()} 不是已知欄位（可用：{_KNOWN_FIELDS}），已忽略。", logging.WARNING)

    endpoint_type = normalize_provider(config.type)
    if endpoint_type not in CUSTOM_TYPES:
        return skip(f"TYPE='{config.type}' 無效（可用：{', '.join(sorted(CUSTOM_TYPES))}）")

    api_key = config.api_key.get_secret_value().strip() if config.api_key else ""
    if not api_key:
        return skip("API_KEY 未設定")

    base_url = (config.base_url or "").strip()
    if endpoint_type == TYPE_OPENAI and not base_url:
        return skip("BASE_URL 未設定（openai 類必填）")

    entries = coerce_entries(config.models, setting_name=f"{prefix}__MODELS")
    if entries is None:
        return skip("MODELS 格式錯誤")

    safety_override, recognized = _parse_bool(config.safety_settings)
    if endpoint_type == TYPE_GEMINI_NATIVE:
        for name, value in (("BASE_URL", base_url), ("SAFETY_SETTINGS", config.safety_settings)):
            if value not in (None, ""):
                _report(f"{prefix}__{name} 對 gemini-native 類無效，已忽略。", logging.WARNING)
        send_safety = False
    elif safety_override is not None:
        send_safety = safety_override
    else:
        send_safety = endpoint_id not in NO_SAFETY_SETTINGS_IDS
        if not recognized:
            _report(
                f"{prefix}__SAFETY_SETTINGS={config.safety_settings!r} 無法辨識（請用 true / false），"
                f"改用預設規則：{'送' if send_safety else '不送'} safety_settings。"
            )

    models = parse_whitelist(entries, endpoint_id=endpoint_id, endpoint_type=endpoint_type)
    if not models:
        _report(f"{prefix}__MODELS 為空，此端點不會出現在管理介面。", logging.WARNING)

    return Endpoint(
        id=endpoint_id,
        type=endpoint_type,
        base_url=base_url if endpoint_type == TYPE_OPENAI else None,
        api_key=SecretStr(api_key),
        models=models,
        send_safety_settings=send_safety,
        api_key_setting=f"{prefix}__API_KEY",
        base_url_setting=f"{prefix}__BASE_URL",
    )


def _raw_endpoints() -> dict[str, Any]:
    """``LLM_ENDPOINTS`` 的原始字典；頂層被直接設值（非字典）時記 WARNING 並視為空。

    The raw ``LLM_ENDPOINTS`` mapping; a value set directly on the top-level
    variable is reported and treated as empty.
    """
    raw = settings.LLM_ENDPOINTS
    if isinstance(raw, dict):
        return raw
    _report(
        f"LLM_ENDPOINTS 不應直接設值（目前為 {str(raw)[:40]!r}），請改用 LLM_ENDPOINTS__<ID>__<欄位>；"
        "所有自訂端點都被忽略（同時寫了巢狀變數也會被這一行蓋掉）。",
        logging.WARNING,
    )
    return {}


def env_endpoint_id() -> str:
    """「.env 那一層」的端點 ID：正規化後的 ``LLM_PROVIDER``。

    The endpoint ID of the ``.env`` layer: the normalized ``LLM_PROVIDER``.
    """
    return normalize_provider(settings.LLM_PROVIDER)


def env_endpoint_is_custom() -> bool:
    """``LLM_PROVIDER`` 是否指向 ``LLM_ENDPOINTS`` 中定義的端點（不論設定是否合格）。

    Whether ``LLM_PROVIDER`` names an endpoint defined in ``LLM_ENDPOINTS``,
    valid or not. 工廠據此決定「不指定端點」時是否改走端點路徑：定義了就用它的網址
    與金鑰；定義了卻寫錯，則明確報「端點不存在」，不默默改用舊的 ``LLM_API_KEY``。
    """
    provider = env_endpoint_id()
    return any(raw_id.strip().lower() == provider for raw_id in _raw_endpoints())


def _env_option(provider: str) -> ModelOption | None:
    """「.env 那一層」的模型組合（自動加進白名單）。

    The ``.env`` layer's model choice, auto-added to the whitelist. A CLI
    default effort outside the accepted values is left out: the client
    rejects it at construction, exactly as before.
    """
    model = (settings.LLM_MODEL_NAME or "").strip()
    if not model:
        return None
    if len(model) > MODEL_NAME_MAX_LENGTH:
        _report(
            f"LLM_MODEL_NAME 超過 {MODEL_NAME_MAX_LENGTH} 字元，不列入白名單"
            "（.env 那一層照常使用，但管理介面選不到它）。",
            logging.WARNING,
        )
        return None
    default_effort = {
        TYPE_CLAUDE_CODE: settings.LLM_CLAUDE_CODE_EFFORT,
        TYPE_ANTIGRAVITY: settings.LLM_ANTIGRAVITY_EFFORT,
    }.get(provider)
    if default_effort is None:
        return ModelOption(model)
    effort = default_effort.strip().lower()
    return ModelOption(model, (effort,)) if effort in EFFORT_VALUES[provider] else None


def _load() -> tuple[dict[str, Endpoint], dict[str, str]]:
    """依目前設定組出所有端點，連同被跳過的自訂端點與原因（每次呼叫重新讀設定）。

    Build every endpoint from the current settings, plus the custom
    endpoints skipped and why. Re-reads settings on each call.
    """
    endpoints: dict[str, Endpoint] = {}
    skipped: dict[str, str] = {}
    for raw_id, raw in _raw_endpoints().items():
        result = _custom_endpoint(raw_id, raw)
        if isinstance(result, str):
            skipped[raw_id.strip().lower()] = result
        else:
            endpoints[result.id] = result

    for cli_type, raw, setting_name in (
        (TYPE_CLAUDE_CODE, settings.LLM_CLAUDE_CODE_MODELS, "LLM_CLAUDE_CODE_MODELS"),
        (TYPE_ANTIGRAVITY, settings.LLM_ANTIGRAVITY_MODELS, "LLM_ANTIGRAVITY_MODELS"),
    ):
        entries = coerce_entries(raw, setting_name=setting_name)
        if entries is None:
            _report(f"{setting_name} 格式錯誤，{cli_type} 端點的白名單視為空（只剩 .env 那一層的模型）。")
            entries = ()
        endpoints[cli_type] = Endpoint(
            id=cli_type,
            type=cli_type,
            models=parse_whitelist(entries, endpoint_id=cli_type, endpoint_type=cli_type),
        )

    provider = env_endpoint_id()
    option = _env_option(provider)
    existing = endpoints.get(provider)
    if provider in skipped:
        # LLM_ENDPOINTS 定義了它卻寫錯：不以舊的 LLM_BASE_URL / LLM_API_KEY 頂替，
        # 讓「.env 那一層」明確失敗（工廠報端點不存在），而不是默默換一組憑證
        pass
    elif existing is not None:
        if existing.type not in (TYPE_CLAUDE_CODE, TYPE_ANTIGRAVITY) and (
            settings.LLM_BASE_URL or settings.LLM_API_KEY
        ):
            _report(
                f"LLM_ENDPOINTS 已定義端點 '{provider}'，LLM_BASE_URL / LLM_API_KEY 對它不再生效。",
                logging.WARNING,
            )
        if option is not None:
            endpoints[provider] = _with_env_option(existing, option)
    elif provider == TYPE_ANTHROPIC:
        # 佔位：只為不把 anthropic 合成成 openai 類端點。listed=False 讓 DB 選不到它；
        # 客戶端照舊由不指定端點的工廠路徑建立並拋「尚未實作」
        endpoints[provider] = Endpoint(id=provider, type=TYPE_ANTHROPIC, listed=False)
    else:
        endpoint_type = TYPE_GEMINI_NATIVE if provider == TYPE_GEMINI_NATIVE else TYPE_OPENAI
        endpoints[provider] = Endpoint(
            id=provider,
            type=endpoint_type,
            base_url=(settings.LLM_BASE_URL or None) if endpoint_type == TYPE_OPENAI else None,
            api_key=SecretStr(settings.LLM_API_KEY) if settings.LLM_API_KEY else None,
            models=(option,) if option is not None else (),
            send_safety_settings=endpoint_type == TYPE_OPENAI and provider not in NO_SAFETY_SETTINGS_IDS,
            listed=bool(provider),
        )
    return endpoints, skipped


def load_endpoints() -> dict[str, Endpoint]:
    """依目前設定組出所有端點（每次呼叫重新讀設定）。

    Build every endpoint from the current settings. Re-reads settings on
    each call, so tests and runtime changes take effect immediately.

    Returns:
        端點 ID → 端點。Endpoint ID to endpoint.
    """
    return _load()[0]


def skip_reason(endpoint_id: str) -> str | None:
    """自訂端點因設定有誤被跳過的原因；沒被跳過（或根本沒定義）時為 None。

    Why a custom endpoint was skipped, or None. Lets callers put the cause
    in the error itself instead of pointing at a log line that may have
    rotated away.
    """
    return _load()[1].get(endpoint_id.strip().lower())


def get_endpoint(endpoint_id: str | None = None) -> Endpoint | None:
    """取得端點；None 取「.env 那一層」的端點。

    Get an endpoint by ID, or the ``.env`` layer's endpoint for None.

    Returns:
        端點；不存在（未定義或設定有誤被跳過）時為 None。The endpoint, or
        None when it is undefined or was skipped as invalid.
    """
    key = env_endpoint_id() if endpoint_id is None else endpoint_id.strip().lower()
    return load_endpoints().get(key)


def list_endpoints() -> list[Endpoint]:
    """可在管理介面選擇的端點（白名單非空），依 ID 排序。

    Endpoints selectable in the admin menu (non-empty whitelist), by ID.
    """
    return sorted(
        (endpoint for endpoint in load_endpoints().values() if endpoint.listed and endpoint.models),
        key=lambda endpoint: endpoint.id,
    )


def log_endpoint_summary() -> None:
    """載入一次端點並記一行摘要（後端啟動時呼叫）；設定錯誤在此首次記 ERROR。

    Load the endpoints once and log a one-line summary (called at startup),
    so configuration errors surface in the startup log. Never logs keys.
    """
    loaded, skipped = _load()
    summary = ", ".join(
        f"{endpoint.id or '（空）'}[{endpoint.type}, {len(endpoint.models)} 個模型"
        f"{'' if endpoint.listed else '，不可選'}]"
        for endpoint in sorted(loaded.values(), key=lambda endpoint: endpoint.id)
    )
    logger.info("LLM 端點已載入（.env 那一層：'%s'）：%s", env_endpoint_id(), summary)
    if skipped:
        logger.warning(
            "LLM 端點設定有誤已跳過：%s",
            "；".join(f"{endpoint_id}（{reason}）" for endpoint_id, reason in sorted(skipped.items())),
        )
