"""LLM endpoints and their allowlists.

LLM 端點與白名單（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D5）。

See the model-routing plan llm_model_routing_FEAT_2026-10-15.md D5. An **endpoint** is one callable service (a
relay, a Google key, a local CLI), identified by a unique ID; the ``(prefix)`` of a card label is the endpoint
ID. Each endpoint carries an allowlist of selectable "model + effort" combinations. Endpoints and allowlists all
come from ``.env``; the DB only records "which combination is currently selected" (see
:mod:`app.infrastructure.llm.routing`).

Endpoints have four sources:

- **custom endpoints**: ``LLM_ENDPOINTS__<ID>__TYPE / BASE_URL / API_KEY / MODELS / SAFETY_SETTINGS /
  AUDIO_MODELS / AUDIO_TRANSPORT``, any number of them
- **built-in CLI endpoints**: ``claude-code`` / ``antigravity``, with allowlists ``LLM_CLAUDE_CODE_MODELS`` /
  ``LLM_ANTIGRAVITY_MODELS``
- **legacy single-set settings**: ``LLM_PROVIDER`` / ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL_NAME``,
  automatically synthesized into an endpoint whose ID is ``LLM_PROVIDER``, that is, "the ``.env`` layer". This
  set (including the CLI ``LLM_*_EFFORT``) is added to the allowlist automatically, so existing ``.env`` files
  need no change. When ``LLM_ENDPOINTS`` defines an endpoint with the same ID as ``LLM_PROVIDER``, that one is
  used instead (the factory also takes it when no endpoint is given, see :func:`env_endpoint_is_custom`)
- **built-in audio endpoint** (capability-routing plan capability_routing_FEAT_2026-10-19.md §6.3): synthesized
  from ``AUDIO_API_KEY`` / ``AUDIO_BASE_URL`` / ``AUDIO_EVALUATOR_PROVIDER``, with the fixed ID ``audio``. Its
  audio-model allowlist is ``AUDIO_MODELS`` plus ``AUDIO_MODEL_NAME``; its text-model allowlist is the same list
  plus ``STT_LLM_MODEL_NAME`` (for the ``stt_llm`` recording-evaluation strategy). Without ``AUDIO_API_KEY`` it
  is not selectable (when the ``.env`` layer uses it, the client still reports the key as unset)

**Audio models** (the ``audio_llm`` of capability routing) have a separate allowlist ``audio_models``: only
models marked as able to listen to audio may be used to evaluate recordings. ``audio_transport`` decides how
openai-type endpoints send audio: ``input_audio`` (OpenAI's audio input) or ``image_url`` (disguised as an
image, for relays that accept only images; this is the former ``proxy`` evaluator).

Each allowlist entry is written in the card-label form ``model@effort``: ``"opus@low|high"`` allows two
efforts, and no ``@`` means the model takes no effort. The entry is split at the **last** ``@``, so the model
name itself may contain ``@``; model names may also contain ``/`` and ``:`` (for example OpenRouter's
``deepseek/deepseek-r1:free``). The cost: on endpoints that do not support effort, a model whose name contains
``@`` is treated as carrying effort and skipped.

A wrong setting (an invalid ID, a missing key, ``MODELS`` that is not JSON, a misspelled field name, a
misspelled effort, and so on) only skips that endpoint or that entry and logs an ERROR, instead of keeping the
whole backend from starting; the same error is logged only once. The backend loads endpoints once at startup
through :func:`log_endpoint_summary`, so the errors appear in the startup log.

Paths that name an endpoint all go through :func:`get_endpoint` / :func:`list_endpoints`; the legacy path
without an endpoint (``LLM_PROVIDER`` with no matching ``LLM_ENDPOINTS``) still has the factory and clients read
settings directly. This module imports no client class (to avoid pulling in openai / jsonschema /
google-genai); :data:`EFFORT_VALUES` is the single source of each CLI's effort values, and the clients'
``VALID_EFFORTS`` are taken directly from here.

**端點**＝一家可呼叫的服務（一個中轉站、一把 Google 金鑰、一個本機 CLI），以唯一
ID 識別；卡片標籤的 ``(前綴)`` 就是端點 ID。每個端點帶一份白名單，列出可選的
「模型 + effort」組合。端點與白名單全部來自 ``.env``——DB 只記「目前選了哪一組」
（見 ``app.infrastructure.llm.routing``）。

端點的四個來源：

- **自訂端點**：``LLM_ENDPOINTS__<ID>__TYPE / BASE_URL / API_KEY / MODELS /
  SAFETY_SETTINGS / AUDIO_MODELS / AUDIO_TRANSPORT``，任意多個
- **內建 CLI 端點**：``claude-code`` / ``antigravity``，白名單為
  ``LLM_CLAUDE_CODE_MODELS`` / ``LLM_ANTIGRAVITY_MODELS``
- **舊式單組設定**：``LLM_PROVIDER`` / ``LLM_BASE_URL`` / ``LLM_API_KEY`` /
  ``LLM_MODEL_NAME``，自動合成為 ID＝``LLM_PROVIDER`` 的端點，即「.env 那一層」。
  這一組（含 CLI 的 ``LLM_*_EFFORT``）自動加進白名單，所以現有 ``.env`` 不必改。
  ``LLM_ENDPOINTS`` 定義了與 ``LLM_PROVIDER`` 同 ID 的端點時改用它（工廠在不指定
  端點時也會走它，見 ``env_endpoint_is_custom``）
- **內建 audio 端點**（能力路由計畫 capability_routing_FEAT_2026-10-19.md §6.3）：由
  ``AUDIO_API_KEY`` / ``AUDIO_BASE_URL`` / ``AUDIO_EVALUATOR_PROVIDER`` 合成，ID 固定為
  ``audio``。音訊模型白名單為 ``AUDIO_MODELS`` 加上 ``AUDIO_MODEL_NAME``；文字模型白名單
  為同一份清單加上 ``STT_LLM_MODEL_NAME``（錄音評分 ``stt_llm`` 策略用）。沒有
  ``AUDIO_API_KEY`` 時不可選（``.env`` 那一層用到它時客戶端照舊報金鑰未設定）

**音訊模型**（能力路由的 ``audio_llm``）另有一份白名單 ``audio_models``：只有標明能聽
音檔的模型才能拿去評分錄音。``audio_transport`` 決定 openai 類端點怎麼送音檔：
``input_audio``（OpenAI 的音訊輸入）或 ``image_url``（偽裝成圖片，給只收圖片的中轉站，
即原本的 ``proxy`` 評分器）。

白名單每項寫成卡片標籤的 ``模型@effort`` 形式：``"opus@low|high"`` 允許兩個
effort，不帶 ``@`` 表示此模型不使用 effort。以**最後一個** ``@`` 切開，讓模型名本身
可以含 ``@``；模型名也可含 ``/``、``:``（如 OpenRouter 的 ``deepseek/deepseek-r1:free``）。
代價：不支援 effort 的端點上，名字含 ``@`` 的模型會被當成帶 effort 而跳過。

設定寫錯（ID 不合規、缺金鑰、``MODELS`` 不是 JSON、欄位名拼錯、effort 拼錯…）只
跳過那個端點或那一項並記 ERROR，不讓整個後端起不來；同一則錯誤只記一次。後端啟動
時以 ``log_endpoint_summary`` 載入一次，錯誤因此出現在啟動 log。

指定端點的路徑都經過 ``get_endpoint`` / ``list_endpoints``；不指定端點的
舊路徑（``LLM_PROVIDER`` 沒有對應的 ``LLM_ENDPOINTS``）仍由工廠與客戶端直接讀
settings。本模組不 import 任何客戶端類別（避免把 openai / jsonschema / google-genai
拉進來）；各 CLI 的 effort 值域以 ``EFFORT_VALUES`` 為唯一來源，客戶端的
``VALID_EFFORTS`` 直接取自這裡。
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
# 能聽音檔的端點類型（有對應的語音評分器）
AUDIO_TYPES: frozenset[str] = frozenset({TYPE_OPENAI, TYPE_GEMINI_NATIVE})

# 自訂端點不可使用的 ID：對應非 OpenAI 相容客戶端的 provider 值。google / openai 不在
# 內——它們本來就是 OpenAI 相容類的合法 ID，可用 LLM_ENDPOINTS 重新定義
# IDs naming non-OpenAI-compatible clients; google / openai stay definable.
# 內建 audio 端點的 ID（由 AUDIO_* 合成），自訂端點不可使用
AUDIO_ENDPOINT_ID = "audio"

RESERVED_IDS: frozenset[str] = frozenset(
    {TYPE_CLAUDE_CODE, TYPE_ANTIGRAVITY, TYPE_GEMINI_NATIVE, TYPE_ANTHROPIC, AUDIO_ENDPOINT_ID}
)

# openai 類端點送音檔的方式：OpenAI 的 input_audio，或偽裝成 image_url（只收圖片的中轉站）
# How an openai-type endpoint receives audio.
AUDIO_TRANSPORT_INPUT_AUDIO = "input_audio"
AUDIO_TRANSPORT_IMAGE_URL = "image_url"
AUDIO_TRANSPORTS: frozenset[str] = frozenset({AUDIO_TRANSPORT_INPUT_AUDIO, AUDIO_TRANSPORT_IMAGE_URL})

# 沒有 AUDIO_BASE_URL 時 openai 類 audio 端點的網址（與能力路由之前 AsyncOpenAI 的預設相同）
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"

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
ENDPOINT_ID_MAX_LENGTH = 32
_ID_PATTERN = re.compile(rf"(?!.*__)[a-z0-9_]{{1,{ENDPOINT_ID_MAX_LENGTH}}}")

# 與 cfg_capability_routes.provider / model / effort 欄位長度上限相容（ENDPOINT_ID_MAX_LENGTH 見上）
MODEL_NAME_MAX_LENGTH = 128
EFFORT_MAX_LENGTH = 16

_KNOWN_FIELDS = "TYPE / BASE_URL / API_KEY / MODELS / SAFETY_SETTINGS / AUDIO_MODELS / AUDIO_TRANSPORT"
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})

# 同一則設定問題只記一次（每次建立客戶端都會重新載入端點）
_reported_problems: set[str] = set()


@dataclass(frozen=True)
class ModelOption:
    """One model in an allowlist.

    白名單中的一個模型。

    Attributes:
        name: the model name (the base name for CLI types); not empty
            模型名（CLI 類為基底名），非空
        efforts: the allowed efforts (no duplicates); empty means this model takes no effort
            允許的 effort（不重複）；空＝此模型不使用 effort
    """

    name: str
    efforts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the name and the efforts.

        檢查模型名與 effort。

        Raises:
            ValueError: if ``name`` is blank, or ``efforts`` contains an empty value or a duplicate
                若 ``name`` 為空白，或 ``efforts`` 含空值或重複
        """
        if not self.name.strip():
            raise ValueError("ModelOption.name 不可為空")
        if not all(self.efforts) or len(set(self.efforts)) != len(self.efforts):
            raise ValueError(f"ModelOption.efforts 不可有空值或重複：{self.efforts!r}")

    def allows(self, effort: str | None) -> bool:
        """Return whether ``effort`` is valid for this model.

        回傳 ``effort`` 是否合法：帶 effort 的模型必須指定其一，不帶的必須為空。

        A model that takes effort must be given one of ``efforts``; a model that does not must be given ``None``
        or an empty string.

        帶 effort 的模型必須指定 ``efforts`` 之一；不帶的必須為 None 或空字串。

        Args:
            effort: the effort to check, or ``None``
                要檢查的 effort，或 None

        Returns:
            ``True`` if the effort is valid
            effort 合法時為 ``True``
        """
        if self.efforts:
            return effort in self.efforts
        return not effort


@dataclass(frozen=True)
class Endpoint:
    """One callable LLM endpoint.

    一個可呼叫的 LLM 端點。

    Construction checks the consistency between fields (type, safety_settings, allowlists); whether the endpoint
    is "callable" (whether the key and URL are complete) is checked when a client is constructed. A legacy
    synthesized endpoint may lack a key, and the client still raises "LLM_API_KEY 未設定".

    建構時檢查欄位之間的一致性（類型、safety_settings、白名單）；端點是否「可呼叫」
    （金鑰、網址是否齊全）由客戶端建構時檢查——舊式合成端點可能缺金鑰，客戶端照舊
    拋「LLM_API_KEY 未設定」。

    Attributes:
        id: the unique ID, source of the label prefix (google / openai / empty string add no prefix)
            唯一 ID，標籤前綴的來源（google / openai / 空字串不加前綴）
        type: the endpoint type (:data:`ALL_TYPES`), which decides the client class
            端點類型（``ALL_TYPES``），決定客戶端類別
        base_url: the URL of an OpenAI-compatible endpoint
            OpenAI 相容端點網址
        api_key: the API key
            API 金鑰
        models: the allowlist; model names are unique
            白名單，模型名不重複
        send_safety_settings: whether to send safety_settings (only openai types may be ``True``)
            是否送 safety_settings（只有 openai 類可為 True）
        listed: whether selectable in the admin UI; ``False`` for the synthesized endpoint when ``LLM_PROVIDER``
            is blank, the anthropic placeholder endpoint, and the audio endpoint without a key
            是否可在管理介面選擇；``LLM_PROVIDER`` 留空時的合成端點、anthropic
            佔位端點、沒有金鑰的 audio 端點為 False
        api_key_setting: the setting name of the key (for error messages)
            金鑰的設定名（錯誤訊息用）
        base_url_setting: the setting name of the URL (for error messages)
            網址的設定名（錯誤訊息用）
        audio_models: the models that can listen to audio (the ``audio_llm`` of capability routing), without
            effort
            能聽音檔的模型（能力路由的 ``audio_llm``），不帶 effort
        audio_transport: how an openai type sends audio (:data:`AUDIO_TRANSPORTS`); ``None`` for other types
            openai 類送音檔的方式（``AUDIO_TRANSPORTS``）；其他類型為 None
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
    audio_models: tuple[ModelOption, ...] = ()
    audio_transport: str | None = None

    def __post_init__(self) -> None:
        """Validate the consistency between fields.

        檢查欄位之間的一致性。

        Raises:
            ValueError: if the type is unknown, safety_settings is set on a non-openai type, the allowlist has
                duplicate models or efforts the type does not support, the audio allowlist has duplicates or
                carries effort, a non-audio type has audio models, or ``audio_transport`` is set where it does
                not apply or is invalid
                若類型未知、非 openai 類設了 safety_settings、白名單有重複模型或此類型不支援的 effort、
                音訊白名單有重複或帶 effort、不能聽音檔的類型有音訊模型，或 ``audio_transport`` 設在不適用處或無效
        """
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
        audio_names = [option.name for option in self.audio_models]
        if len(set(audio_names)) != len(audio_names) or any(option.efforts for option in self.audio_models):
            raise ValueError(f"端點 {self.id!r} 的音訊白名單有重複模型或帶 effort：{audio_names}")
        if self.audio_models and self.type not in AUDIO_TYPES:
            raise ValueError(f"端點 {self.id!r}（{self.type}）不能聽音檔")
        if (self.audio_transport is not None) != (self.type == TYPE_OPENAI and bool(self.audio_models)):
            raise ValueError(f"端點 {self.id!r} 的 audio_transport 只在有音訊模型的 openai 類端點設定")
        if self.audio_transport is not None and self.audio_transport not in AUDIO_TRANSPORTS:
            raise ValueError(f"端點 {self.id!r} 的 audio_transport 無效：{self.audio_transport!r}")

    def find_model(self, name: str) -> ModelOption | None:
        """Return the model named ``name`` in the allowlist.

        回傳白名單中名為 ``name`` 的模型。

        Args:
            name: the model name to look up
                要查找的模型名

        Returns:
            the matching model, or ``None`` if it is not in the allowlist
            符合的模型；不在白名單時為 None
        """
        return next((option for option in self.models if option.name == name), None)

    def allows(self, model: str, effort: str | None) -> bool:
        """Return whether ``(model, effort)`` is in the allowlist.

        回傳 ``(model, effort)`` 是否在白名單內。

        Args:
            model: the model name
                模型名
            effort: the effort, or ``None``
                effort，或 None

        Returns:
            ``True`` if the model is listed and accepts the effort
            模型在白名單內且接受該 effort 時為 ``True``
        """
        option = self.find_model(model)
        return option is not None and option.allows(effort)

    def find_audio_model(self, name: str) -> ModelOption | None:
        """Return the model named ``name`` in the audio allowlist.

        回傳音訊白名單中名為 ``name`` 的模型。

        Args:
            name: the model name to look up
                要查找的模型名

        Returns:
            the matching model, or ``None`` if it is not in the audio allowlist
            符合的模型；不在音訊白名單時為 None
        """
        return next((option for option in self.audio_models if option.name == name), None)


def _report(message: str, level: int = logging.ERROR) -> None:
    """Log a configuration problem only once per message.

    同一則設定問題只記一次。
    """
    if message in _reported_problems:
        return
    _reported_problems.add(message)
    logger.log(level, message)


def coerce_entries(raw: Any, *, setting_name: str) -> tuple[str, ...] | None:
    """Coerce an allowlist setting value into a tuple of strings.

    把白名單設定值（``.env`` 的 JSON 字串，或程式給的序列）轉成字串 tuple。

    The value may be a JSON string from ``.env`` or a sequence given programmatically. ``None``, a blank string,
    and JSON ``null`` all yield an empty tuple.

    設定值可以是 ``.env`` 的 JSON 字串，或程式給的序列。None、空白字串、JSON ``null`` 都得到空 tuple。

    Args:
        raw: the raw setting value
            原始設定值
        setting_name: the setting name, for error messages
            設定名（錯誤訊息用）

    Returns:
        the string tuple; ``None`` after logging an ERROR when the format is wrong
        字串 tuple；格式錯誤時記 ERROR 並回 None
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
    """Parse a setting value such as ``true`` / ``false`` into a bool.

    把 ``true`` / ``false`` 之類的設定值轉成布林。

    Returns:
        ``(value, recognized)``; ``(None, True)`` when unset, ``(None, False)`` when unrecognized
        ``(值, 是否可辨識)``；未設為 ``(None, True)``，無法辨識為 ``(None, False)``
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
    """Parse allowlist strings into :class:`ModelOption` objects, skipping invalid entries with an ERROR.

    把白名單字串解析成 ``ModelOption``，不合格的項目跳過並記 ERROR。

    When the same model is written in several entries, their efforts are merged (in order of appearance,
    deduplicated). When the same model appears both "without effort" and "with effort", it is treated as
    requiring effort, and an ERROR is logged as a reminder. Blank entries are ignored.

    同一模型寫多項時合併 effort（依出現順序、去重）。同一模型同時出現「不帶
    effort」與「帶 effort」的寫法時，視為必須帶 effort，並記 ERROR 提醒。空白項目略過。

    Args:
        entries: the allowlist strings
            白名單字串
        endpoint_id: the endpoint ID (for error messages)
            端點 ID（錯誤訊息用）
        endpoint_type: the endpoint type, which decides the available effort values
            端點類型，決定可用的 effort 值

    Returns:
        the models in order of first appearance
        依首次出現順序排列的模型
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


def parse_audio_whitelist(entries: tuple[str, ...] | list[str], *, setting_name: str) -> tuple[ModelOption, ...]:
    """Parse an audio-model allowlist into :class:`ModelOption` objects (without effort, duplicates kept once).

    把音訊模型白名單解析成 ``ModelOption``（不帶 effort，重複的只留一個）。

    An entry containing ``@`` (treated as carrying effort) or a name that is too long is skipped and reported.

    含 ``@`` 的項目（視為帶 effort）或過長的名稱會被跳過並記錄。

    Args:
        entries: the allowlist strings
            白名單字串
        setting_name: the setting name, for error messages
            設定名（錯誤訊息用）

    Returns:
        the models in order of first appearance
        依首次出現順序排列的模型
    """
    names: list[str] = []
    for raw in entries:
        name = str(raw).strip()
        if not name or name in names:
            continue
        if "@" in name:
            _report(f"{setting_name} 的項目 {name!r} 帶了 effort（音訊模型不使用 effort），已跳過。")
            continue
        if len(name) > MODEL_NAME_MAX_LENGTH:
            _report(f"{setting_name} 的模型名超過 {MODEL_NAME_MAX_LENGTH} 字元，已跳過：{name[:40]}…")
            continue
        names.append(name)
    return tuple(ModelOption(name) for name in names)


def _with_extra_models(models: tuple[ModelOption, ...], *names: str) -> tuple[ModelOption, ...]:
    """Append model names without effort to the allowlist.

    把不帶 effort 的模型名補進白名單。

    Names already listed are not added again; empty and over-long names are ignored.

    已有的不重複加，空的、太長的略過。
    """
    result = list(models)
    for raw in names:
        name = (raw or "").strip()
        if name and len(name) <= MODEL_NAME_MAX_LENGTH and all(option.name != name for option in result):
            result.append(ModelOption(name))
    return tuple(result)


def _audio_endpoint() -> Endpoint:
    """Synthesize the built-in audio endpoint from ``AUDIO_*`` (capability-routing plan §6.3).

    由 ``AUDIO_*`` 合成內建 audio 端點（能力路由計畫 §6.3）。

    Type: gemini-native when ``AUDIO_EVALUATOR_PROVIDER`` is ``gemini_native``; otherwise (``openai`` /
    ``proxy`` / ``stt_diff`` / ``stt_llm``) openai, using the official OpenAI URL when there is no
    ``AUDIO_BASE_URL``, the same as the evaluators before capability routing (``stt_llm`` already built an
    OpenAI-compatible client from ``AUDIO_*``). ``proxy`` sends audio as ``image_url``, the rest as
    ``input_audio``. Without ``AUDIO_API_KEY`` it is not selectable. At startup one line records the chosen type
    (:func:`log_endpoint_summary`).

    類型：``AUDIO_EVALUATOR_PROVIDER`` 為 ``gemini_native`` 時為 gemini-native；其他（``openai`` /
    ``proxy`` / ``stt_diff`` / ``stt_llm``）為 openai，沒有 ``AUDIO_BASE_URL`` 時用 OpenAI 官方網址——
    與能力路由之前的評分器相同（``stt_llm`` 原本就以 ``AUDIO_*`` 建 OpenAI 相容客戶端）。``proxy``
    以 ``image_url`` 送音檔，其餘以 ``input_audio``。沒有 ``AUDIO_API_KEY`` 時不可選。啟動時記一行
    選定的類型（``log_endpoint_summary``）。
    """
    provider = (settings.AUDIO_EVALUATOR_PROVIDER or "").strip().lower()
    base_url = (settings.AUDIO_BASE_URL or "").strip()
    endpoint_type = TYPE_GEMINI_NATIVE if provider == "gemini_native" else TYPE_OPENAI

    entries = coerce_entries(settings.AUDIO_MODELS, setting_name="AUDIO_MODELS")
    if entries is None:
        _report("AUDIO_MODELS 格式錯誤，audio 端點的音訊白名單只剩 AUDIO_MODEL_NAME。")
        entries = ()
    audio_models = _with_extra_models(
        parse_audio_whitelist(entries, setting_name="AUDIO_MODELS"), settings.AUDIO_MODEL_NAME
    )
    api_key = (settings.AUDIO_API_KEY or "").strip()
    is_openai = endpoint_type == TYPE_OPENAI
    return Endpoint(
        id=AUDIO_ENDPOINT_ID,
        type=endpoint_type,
        base_url=(base_url or OPENAI_DEFAULT_BASE_URL) if is_openai else None,
        api_key=SecretStr(api_key) if api_key else None,
        models=_with_extra_models(audio_models, settings.STT_LLM_MODEL_NAME),
        listed=bool(api_key),
        api_key_setting="AUDIO_API_KEY",
        base_url_setting="AUDIO_BASE_URL",
        audio_models=audio_models,
        audio_transport=(
            (AUDIO_TRANSPORT_IMAGE_URL if provider == "proxy" else AUDIO_TRANSPORT_INPUT_AUDIO)
            if is_openai and audio_models
            else None
        ),
    )


def _with_env_option(endpoint: Endpoint, extra: ModelOption) -> Endpoint:
    """Merge the combination of "the ``.env`` layer" into the endpoint's allowlist.

    把「.env 那一層」的組合併進端點的白名單。

    - the allowlist lacks this model: add it
    - the allowlist already has it and both carry effort: add the ``.env`` default effort
    - the allowlist writes it **without** effort but ``.env`` carries effort: respect the allowlist and do not
      rewrite it (otherwise DB settings with an empty effort would be judged invalid), and log a WARNING saying the
      ``.env`` default is not listed

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
    """Turn one set of ``LLM_ENDPOINTS__<ID>__*`` into an endpoint.

    把一組 ``LLM_ENDPOINTS__<ID>__*`` 轉成端點。

    Returns:
        the endpoint; when invalid, the reason it was skipped, after logging an ERROR
        端點；不合格時記 ERROR 並回傳跳過原因
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
    audio_entries = coerce_entries(config.audio_models, setting_name=f"{prefix}__AUDIO_MODELS")
    if audio_entries is None:
        return skip("AUDIO_MODELS 格式錯誤")
    audio_models = parse_audio_whitelist(audio_entries, setting_name=f"{prefix}__AUDIO_MODELS")
    if not models and not audio_models:
        _report(f"{prefix}__MODELS 與 AUDIO_MODELS 都是空的，此端點不會出現在管理介面。", logging.WARNING)

    raw_transport = (config.audio_transport or "").strip().lower()
    audio_transport: str | None = None
    if endpoint_type == TYPE_OPENAI and audio_models:
        audio_transport = raw_transport or AUDIO_TRANSPORT_INPUT_AUDIO
        if audio_transport not in AUDIO_TRANSPORTS:
            return skip(f"AUDIO_TRANSPORT='{config.audio_transport}' 無效（可用：{', '.join(sorted(AUDIO_TRANSPORTS))}）")
    elif raw_transport:
        _report(f"{prefix}__AUDIO_TRANSPORT 只對有 AUDIO_MODELS 的 openai 類端點有效，已忽略。", logging.WARNING)

    return Endpoint(
        id=endpoint_id,
        type=endpoint_type,
        base_url=base_url if endpoint_type == TYPE_OPENAI else None,
        api_key=SecretStr(api_key),
        models=models,
        send_safety_settings=send_safety,
        api_key_setting=f"{prefix}__API_KEY",
        base_url_setting=f"{prefix}__BASE_URL",
        audio_models=audio_models,
        audio_transport=audio_transport,
    )


def _raw_endpoints() -> dict[str, Any]:
    """Return the raw ``LLM_ENDPOINTS`` dict.

    回傳 ``LLM_ENDPOINTS`` 的原始字典。

    When the top level is set directly (not a dict), log a WARNING and treat it as empty.

    頂層被直接設值（非字典）時記 WARNING 並視為空。
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
    """Return the endpoint ID of "the ``.env`` layer": the normalized ``LLM_PROVIDER``.

    回傳「.env 那一層」的端點 ID：正規化後的 ``LLM_PROVIDER``。

    Returns:
        the normalized ``LLM_PROVIDER``; may be an empty string
        正規化後的 ``LLM_PROVIDER``；可能為空字串
    """
    return normalize_provider(settings.LLM_PROVIDER)


def env_endpoint_is_custom() -> bool:
    """Return whether ``LLM_PROVIDER`` names an endpoint defined in ``LLM_ENDPOINTS``, valid or not.

    回傳 ``LLM_PROVIDER`` 是否指向 ``LLM_ENDPOINTS`` 中定義的端點（不論設定是否合格）。

    The factory uses this to decide whether "no endpoint given" switches to the endpoint path: if defined, its URL
    and key are used; if defined but wrong, "endpoint does not exist" is reported explicitly instead of silently
    falling back to the old ``LLM_API_KEY``.

    工廠據此決定「不指定端點」時是否改走端點路徑：定義了就用它的網址
    與金鑰；定義了卻寫錯，則明確報「端點不存在」，不默默改用舊的 ``LLM_API_KEY``。

    Returns:
        ``True`` if ``LLM_ENDPOINTS`` defines an endpoint with that ID
        ``LLM_ENDPOINTS`` 定義了該 ID 的端點時為 ``True``
    """
    provider = env_endpoint_id()
    return any(raw_id.strip().lower() == provider for raw_id in _raw_endpoints())


def _env_option(provider: str) -> ModelOption | None:
    """Return the model combination of "the ``.env`` layer" (added to the allowlist automatically).

    回傳「.env 那一層」的模型組合（自動加進白名單）。

    A CLI default effort outside the accepted values yields ``None``: the client rejects it at construction,
    exactly as before. A blank or over-long ``LLM_MODEL_NAME`` also yields ``None``.

    CLI 預設 effort 不在可接受值內時回 None：客戶端建構時照舊拒絕。``LLM_MODEL_NAME`` 為空或過長時也回 None。
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
    """Build every endpoint from the current settings, plus the skipped custom endpoints and their reasons.

    依目前設定組出所有端點，連同被跳過的自訂端點與原因。

    Settings are re-read on every call.

    每次呼叫重新讀設定。
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

    endpoints[AUDIO_ENDPOINT_ID] = _audio_endpoint()

    provider = env_endpoint_id()
    option = _env_option(provider)
    existing = endpoints.get(provider)
    if provider in skipped:
        # LLM_ENDPOINTS 定義了它卻寫錯：不以舊的 LLM_BASE_URL / LLM_API_KEY 頂替，
        # 讓「.env 那一層」明確失敗（工廠報端點不存在），而不是默默換一組憑證
        pass
    elif existing is not None:
        if existing.type not in (TYPE_CLAUDE_CODE, TYPE_ANTIGRAVITY) and existing.id != AUDIO_ENDPOINT_ID and (
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
    """Build every endpoint from the current settings.

    依目前設定組出所有端點。

    Settings are re-read on every call, so tests and runtime changes take effect immediately.

    每次呼叫重新讀設定，測試與執行期的變更因此立即生效。

    Returns:
        endpoint ID → endpoint
        端點 ID → 端點
    """
    return _load()[0]


def skip_reason(endpoint_id: str) -> str | None:
    """Return why a custom endpoint was skipped because of a wrong setting.

    回傳自訂端點因設定有誤被跳過的原因。

    This lets callers put the cause in the error itself instead of pointing at a log line that may have rotated
    away.

    讓呼叫者把原因直接放進錯誤，而不是指向可能已輪替掉的 log。

    Args:
        endpoint_id: the endpoint ID; case and surrounding whitespace are ignored
            端點 ID；忽略大小寫與前後空白

    Returns:
        the reason; ``None`` when it was not skipped (or was never defined)
        原因；沒被跳過（或根本沒定義）時為 None
    """
    return _load()[1].get(endpoint_id.strip().lower())


def skipped_endpoints() -> dict[str, str]:
    """Return every custom endpoint skipped because of a wrong setting, with its reason (for diagnostics).

    回傳所有因設定有誤被跳過的自訂端點與原因（診斷端點用）。

    Returns:
        endpoint ID → skip reason
        端點 ID → 跳過原因
    """
    return dict(_load()[1])


def get_endpoint(endpoint_id: str | None = None) -> Endpoint | None:
    """Get an endpoint; ``None`` gets the endpoint of "the ``.env`` layer".

    取得端點；None 取「.env 那一層」的端點。

    Args:
        endpoint_id: the endpoint ID (case and surrounding whitespace are ignored), or ``None`` for the
            ``.env`` layer
            端點 ID（忽略大小寫與前後空白），或 None 取「.env 那一層」

    Returns:
        the endpoint; ``None`` when it does not exist (undefined, or skipped because of a wrong setting)
        端點；不存在（未定義或設定有誤被跳過）時為 None
    """
    key = env_endpoint_id() if endpoint_id is None else endpoint_id.strip().lower()
    return load_endpoints().get(key)


def list_endpoints() -> list[Endpoint]:
    """Return the endpoints selectable in the admin UI (listed, non-empty allowlist), sorted by ID.

    回傳可在管理介面選擇的端點（白名單非空），依 ID 排序。

    Returns:
        the selectable endpoints; may be empty
        可選擇的端點；可能為空
    """
    return sorted(
        (endpoint for endpoint in load_endpoints().values() if endpoint.listed and endpoint.models),
        key=lambda endpoint: endpoint.id,
    )


def list_audio_endpoints() -> list[Endpoint]:
    """Return the endpoints selectable for listening to audio (non-empty audio allowlist), sorted by ID.

    回傳可選來聽音檔的端點（音訊白名單非空），依 ID 排序。

    Returns:
        the selectable endpoints; may be empty
        可選擇的端點；可能為空
    """
    return sorted(
        (endpoint for endpoint in load_endpoints().values() if endpoint.listed and endpoint.audio_models),
        key=lambda endpoint: endpoint.id,
    )


def log_endpoint_summary() -> None:
    """Load the endpoints once and log a one-line summary (called at backend startup).

    載入一次端點並記一行摘要（後端啟動時呼叫）。

    Configuration errors are first logged as ERROR here, so they surface in the startup log. Keys are never
    logged.

    設定錯誤在此首次記 ERROR，因此出現在啟動 log。不會記錄金鑰。
    """
    loaded, skipped = _load()
    summary = ", ".join(
        f"{endpoint.id or '（空）'}[{endpoint.type}, {len(endpoint.models)} 個模型"
        f"{f'、{len(endpoint.audio_models)} 個音訊模型' if endpoint.audio_models else ''}"
        f"{'' if endpoint.listed else '，不可選'}]"
        for endpoint in sorted(loaded.values(), key=lambda endpoint: endpoint.id)
    )
    logger.info("LLM 端點已載入（.env 那一層：'%s'）：%s", env_endpoint_id(), summary)
    audio = loaded.get(AUDIO_ENDPOINT_ID)
    if audio is not None:
        logger.info(
            "內建 audio 端點：%s%s（依 AUDIO_EVALUATOR_PROVIDER=%r 決定；gemini_native 為 gemini-native，其餘為 openai）",
            audio.type,
            f"，{audio.base_url}，送音檔方式 {audio.audio_transport}" if audio.base_url else "",
            settings.AUDIO_EVALUATOR_PROVIDER,
        )
    if skipped:
        logger.warning(
            "LLM 端點設定有誤已跳過：%s",
            "；".join(f"{endpoint_id}（{reason}）" for endpoint_id, reason in sorted(skipped.items())),
        )
