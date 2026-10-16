"""端點與白名單的載入測試（模型路由計畫 D5）。

Tests for loading LLM endpoints and their whitelists from settings: the
nested ``LLM_ENDPOINTS__<ID>__*`` variables (kept raw so a bad value never
breaks ``Settings()``), the ``模型@effort`` whitelist format, ID rules, the
legacy single-endpoint synthesis (so existing ``.env`` files keep working),
type invariants, and the one-time error reporting for bad settings.
"""

import logging
from contextlib import ExitStack
from unittest import mock

import pytest
from pydantic import SecretStr

from app.core.config import LLMEndpointSettings, Settings, settings
from app.infrastructure.llm import endpoints
from app.infrastructure.llm.endpoints import (
    Endpoint,
    ModelOption,
    env_endpoint_is_custom,
    get_endpoint,
    list_endpoints,
    load_endpoints,
    log_endpoint_summary,
    parse_whitelist,
    skip_reason,
)

# 「.env 那一層」的基準設定：每個測試從這裡出發，再各自覆寫
_BASE = {
    "LLM_PROVIDER": "google",
    "LLM_MODEL_NAME": "gemini-3.5-flash",
    "LLM_API_KEY": "legacy-key",
    "LLM_BASE_URL": "https://legacy.example/v1",
    "LLM_ENDPOINTS": {},
    "LLM_CLAUDE_CODE_MODELS": (),
    "LLM_ANTIGRAVITY_MODELS": (),
    "LLM_CLAUDE_CODE_EFFORT": "high",
    "LLM_ANTIGRAVITY_EFFORT": "high",
}


@pytest.fixture(autouse=True)
def _fresh_reports():
    """每個測試都能重新觀察到「只記一次」的錯誤。Reset one-time reports."""
    endpoints._reported_problems.clear()
    yield
    endpoints._reported_problems.clear()


def _patched(**overrides):
    """以基準設定加上覆寫值 patch settings。Patch settings with base + overrides."""
    stack = ExitStack()
    for name, value in {**_BASE, **overrides}.items():
        stack.enter_context(mock.patch.object(settings, name, value))
    return stack


def _relay(**fields) -> LLMEndpointSettings:
    """一組合格的 openai 類端點設定，可覆寫欄位。A valid openai-type endpoint."""
    values = {
        "base_url": "https://relay.example/v1",
        "api_key": SecretStr("relay-key"),
        "models": ("gemini-3.5-flash",),
        **fields,
    }
    return LLMEndpointSettings(**values)


def _load_env_file(tmp_path, text: str) -> dict[str, Endpoint]:
    """寫一份 .env、以它建 Settings，再用其中的端點設定載入端點。"""
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    parsed = Settings(_env_file=env_file)  # 不論內容怎麼寫錯都不該拋錯
    with _patched(
        LLM_ENDPOINTS=parsed.LLM_ENDPOINTS,
        LLM_CLAUDE_CODE_MODELS=parsed.LLM_CLAUDE_CODE_MODELS,
    ):
        return load_endpoints()


# ----------------------------------------------------------------------
# .env 解析：原樣收下，壞值不讓 Settings() 拋錯（Raw parsing from .env）
# ----------------------------------------------------------------------


def test_endpoints_load_from_env_file(tmp_path):
    """``LLM_ENDPOINTS__<ID>__*`` 與 CLI 白名單從 .env 檔載入，ID 轉小寫。"""
    loaded = _load_env_file(
        tmp_path,
        "LLM_ENDPOINTS__OPENROUTER__BASE_URL=https://openrouter.ai/api/v1\n"
        "LLM_ENDPOINTS__OPENROUTER__API_KEY=sk-or\n"
        'LLM_ENDPOINTS__OPENROUTER__MODELS=["deepseek/deepseek-r1:free", "gpt-x"]\n'
        "LLM_ENDPOINTS__GEMINI__TYPE=gemini-native\n"
        "LLM_ENDPOINTS__GEMINI__API_KEY=AIza\n"
        'LLM_ENDPOINTS__GEMINI__MODELS=["gemini-3.5-flash"]\n'
        "LLM_ENDPOINTS__RELAY__BASE_URL=https://relay.example/v1\n"
        "LLM_ENDPOINTS__RELAY__API_KEY=k\n"
        'LLM_ENDPOINTS__RELAY__MODELS=["m"]\n'
        "LLM_ENDPOINTS__RELAY__SAFETY_SETTINGS=false\n"
        'LLM_CLAUDE_CODE_MODELS=["opus@low|max", "haiku"]\n',
    )

    relay = loaded["openrouter"]
    assert relay.type == "openai"
    assert relay.base_url == "https://openrouter.ai/api/v1"
    assert relay.api_key is not None and relay.api_key.get_secret_value() == "sk-or"
    assert relay.models == (ModelOption("deepseek/deepseek-r1:free"), ModelOption("gpt-x"))
    assert loaded["gemini"].type == "gemini-native"
    assert loaded["relay"].send_safety_settings is False
    assert loaded["claude-code"].models == (ModelOption("opus", ("low", "max")), ModelOption("haiku"))


@pytest.mark.parametrize(
    ("models_line", "fragment"),
    [
        ("LLM_ENDPOINTS__BAD__MODELS=opus", "不是 JSON 陣列"),
        ("LLM_ENDPOINTS__BAD__MODELS=['opus']", "不是 JSON 陣列"),
        ("LLM_ENDPOINTS__BAD__MODELS=[1, 2]", "必須是字串陣列"),
        ('LLM_ENDPOINTS__BAD__MODELS={"a": 1}', "必須是字串陣列"),
    ],
)
def test_malformed_models_skip_only_that_endpoint(tmp_path, models_line, fragment, caplog):
    """``MODELS`` 不是 JSON 字串陣列：Settings() 不拋錯，跳過該端點並記 ERROR，其他端點照常。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        loaded = _load_env_file(
            tmp_path,
            "LLM_ENDPOINTS__BAD__BASE_URL=https://bad.example/v1\n"
            "LLM_ENDPOINTS__BAD__API_KEY=k\n"
            f"{models_line}\n"
            "LLM_ENDPOINTS__GOOD__BASE_URL=https://good.example/v1\n"
            "LLM_ENDPOINTS__GOOD__API_KEY=k\n"
            'LLM_ENDPOINTS__GOOD__MODELS=["m"]\n',
        )

    assert "bad" not in loaded
    assert "good" in loaded
    assert fragment in caplog.text
    assert "LLM_ENDPOINTS__BAD__MODELS" in caplog.text


def test_unrecognized_safety_settings_falls_back_to_default(tmp_path, caplog):
    """``SAFETY_SETTINGS`` 無法辨識：記 ERROR，端點照常載入並用預設規則。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        loaded = _load_env_file(
            tmp_path,
            "LLM_ENDPOINTS__RELAY__BASE_URL=https://relay.example/v1\n"
            "LLM_ENDPOINTS__RELAY__API_KEY=k\n"
            'LLM_ENDPOINTS__RELAY__MODELS=["m"]\n'
            "LLM_ENDPOINTS__RELAY__SAFETY_SETTINGS=maybe\n",
        )

    assert loaded["relay"].send_safety_settings is True
    assert "無法辨識" in caplog.text


def test_misspelled_field_is_reported(tmp_path, caplog):
    """欄位名拼錯（``MODEL`` 少了 S）：記 WARNING 點名該變數，且白名單為空也提醒。"""
    with caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        loaded = _load_env_file(
            tmp_path,
            "LLM_ENDPOINTS__RELAY__BASE_URL=https://relay.example/v1\n"
            "LLM_ENDPOINTS__RELAY__API_KEY=k\n"
            'LLM_ENDPOINTS__RELAY__MODEL=["m"]\n',
        )

    assert loaded["relay"].models == ()
    assert "LLM_ENDPOINTS__RELAY__MODEL 不是已知欄位" in caplog.text
    assert "LLM_ENDPOINTS__RELAY__MODELS 為空" in caplog.text


def test_malformed_cli_whitelist_does_not_break_settings(tmp_path, caplog):
    """CLI 白名單不是 JSON：Settings() 不拋錯，該白名單視為空並記 ERROR。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        loaded = _load_env_file(tmp_path, "LLM_CLAUDE_CODE_MODELS=opus@high\n")

    assert loaded["claude-code"].models == ()
    assert "LLM_CLAUDE_CODE_MODELS 不是 JSON 陣列" in caplog.text


# ----------------------------------------------------------------------
# 白名單格式（Whitelist format）
# ----------------------------------------------------------------------


def test_whitelist_formats():
    """不帶 @、單一 effort、| 展開、以最後一個 @ 切開（模型名可含 : 與 @）。"""
    options = parse_whitelist(
        ["haiku", "opus@high", "sonnet@low|medium|high", "vendor/model:free", "a@b@max", "  "],
        endpoint_id="claude-code",
        endpoint_type="claude-code",
    )

    assert options == (
        ModelOption("haiku"),
        ModelOption("opus", ("high",)),
        ModelOption("sonnet", ("low", "medium", "high")),
        ModelOption("vendor/model:free"),
        ModelOption("a@b", ("max",)),
    )


def test_whitelist_merges_same_model_in_order():
    """同一模型寫多項時依出現順序合併 effort、去重；effort 不分大小寫。"""
    options = parse_whitelist(
        ["opus@high", "opus@LOW|high"], endpoint_id="claude-code", endpoint_type="claude-code"
    )
    assert options == (ModelOption("opus", ("high", "low")),)


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("opus@ultra", "無效 effort"),
        ("@high", "格式錯誤"),
        ("opus@", "格式錯誤"),
        ("opus@low||high", "格式錯誤"),
        ("x" * 129, "超過 128 字元"),
    ],
)
def test_malformed_entries_are_skipped_and_reported(entry, fragment, caplog):
    """不合格的項目跳過並記 ERROR，其他項目照常載入。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        options = parse_whitelist(
            [entry, "haiku"], endpoint_id="claude-code", endpoint_type="claude-code"
        )

    assert options == (ModelOption("haiku"),)
    assert fragment in caplog.text


def test_effort_on_type_without_effort_is_rejected(caplog):
    """不支援 effort 的端點類型帶 @effort：該項跳過。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        options = parse_whitelist(
            ["gpt-x@high", "gpt-y"], endpoint_id="relay", endpoint_type="openai"
        )

    assert options == (ModelOption("gpt-y"),)
    assert "不支援 effort" in caplog.text


def test_plain_and_effort_entries_for_same_model_require_effort(caplog):
    """同一模型同時寫了帶與不帶 effort：視為必須帶 effort 並提醒。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        options = parse_whitelist(
            ["opus", "opus@high"], endpoint_id="claude-code", endpoint_type="claude-code"
        )

    assert options == (ModelOption("opus", ("high",)),)
    assert "同時寫了帶與不帶" in caplog.text


def test_problem_reported_only_once(caplog):
    """每次建立客戶端都會重新載入；同一則錯誤只記一次。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        for _ in range(3):
            parse_whitelist(["opus@ultra"], endpoint_id="claude-code", endpoint_type="claude-code")

    assert caplog.text.count("無效 effort") == 1


def test_allows():
    """帶 effort 的模型必須指定白名單內的值；不帶的模型 effort 必須為空。"""
    endpoint = Endpoint(
        id="claude-code",
        type="claude-code",
        models=(ModelOption("opus", ("low", "high")), ModelOption("haiku")),
    )
    assert endpoint.allows("opus", "high")
    assert not endpoint.allows("opus", "max")
    assert not endpoint.allows("opus", None)
    assert endpoint.allows("haiku", None)
    assert endpoint.allows("haiku", "")
    assert not endpoint.allows("haiku", "high")
    assert not endpoint.allows("sonnet", None)


# ----------------------------------------------------------------------
# 型別不變條件（Type invariants）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": " "},
        {"name": "opus", "efforts": ("high", "high")},
        {"name": "opus", "efforts": ("",)},
    ],
)
def test_model_option_rejects_invalid(kwargs):
    """ModelOption 不接受空名稱、重複或空的 effort。"""
    with pytest.raises(ValueError):
        ModelOption(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": "x", "type": "whatever"},
        {"id": "x", "type": "claude-code", "send_safety_settings": True},
        {"id": "x", "type": "openai", "models": (ModelOption("m"), ModelOption("m"))},
        {"id": "x", "type": "openai", "models": (ModelOption("m", ("high",)),)},
        {"id": "x", "type": "claude-code", "models": (ModelOption("m", ("ultra",)),)},
    ],
)
def test_endpoint_rejects_inconsistent_fields(kwargs):
    """Endpoint 不接受未知類型、非 openai 類送 safety、重複模型、不支援的 effort。"""
    with pytest.raises(ValueError):
        Endpoint(**kwargs)


# ----------------------------------------------------------------------
# 自訂端點（Custom endpoints）
# ----------------------------------------------------------------------


def test_custom_endpoints_load_with_own_credentials():
    """多個自訂端點各自帶自己的網址、金鑰、白名單與設定名。"""
    with _patched(
        LLM_ENDPOINTS={
            "yinli": _relay(base_url="https://yinli.example/v1", api_key=SecretStr("k1")),
            "or2": _relay(base_url="https://or2.example/v1", api_key=SecretStr("k2")),
            "gemini": LLMEndpointSettings(
                type="gemini-native", api_key=SecretStr("k3"), models=("gemini-3.5-flash",)
            ),
        }
    ):
        loaded = load_endpoints()

    assert loaded["yinli"].base_url == "https://yinli.example/v1"
    assert loaded["yinli"].api_key.get_secret_value() == "k1"
    assert loaded["or2"].api_key.get_secret_value() == "k2"
    assert loaded["gemini"].type == "gemini-native"
    assert loaded["gemini"].base_url is None
    assert loaded["yinli"].api_key_setting == "LLM_ENDPOINTS__YINLI__API_KEY"
    assert loaded["yinli"].base_url_setting == "LLM_ENDPOINTS__YINLI__BASE_URL"


def test_api_key_not_exposed():
    """金鑰以 SecretStr 保存，不出現在 repr / str（避免進 log 或診斷輸出）。"""
    with _patched(LLM_ENDPOINTS={"yinli": _relay(api_key=SecretStr("sk-secret"))}):
        endpoint = get_endpoint("yinli")

    assert endpoint is not None
    assert isinstance(endpoint.api_key, SecretStr)
    assert "sk-secret" not in repr(endpoint)
    assert "sk-secret" not in str(endpoint.api_key)


@pytest.mark.parametrize(
    ("raw_id", "config", "fragment"),
    [
        ("my-relay", _relay(), "不合規"),
        ("a__b", _relay(), "不合規"),
        ("x" * 33, _relay(), "不合規"),
        ("anthropic", _relay(), "不合規"),
        ("relay", _relay(type="claude-code"), "TYPE"),
        ("relay", _relay(api_key=None), "API_KEY 未設定"),
        ("relay", _relay(api_key=SecretStr("  ")), "API_KEY 未設定"),
        ("relay", _relay(base_url=None), "BASE_URL 未設定"),
    ],
)
def test_invalid_custom_endpoint_is_skipped(raw_id, config, fragment, caplog):
    """ID 不合規、類型無效、缺金鑰或網址：跳過該端點並記 ERROR，不影響其他端點。"""
    with _patched(LLM_ENDPOINTS={raw_id: config, "good": _relay()}), \
         caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        loaded = load_endpoints()

    assert raw_id.lower() not in loaded
    assert "good" in loaded
    assert fragment in caplog.text


def test_gemini_native_ignores_url_and_safety_with_warning(caplog):
    """gemini-native 類不需網址；設了 BASE_URL / SAFETY_SETTINGS 會被忽略並提醒。"""
    config = LLMEndpointSettings(
        type="gemini-native", api_key=SecretStr("k"), base_url="https://proxy", safety_settings="true"
    )
    with _patched(LLM_ENDPOINTS={"gem": config}), \
         caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        endpoint = get_endpoint("gem")

    assert endpoint is not None
    assert endpoint.base_url is None
    assert endpoint.send_safety_settings is False
    assert "__BASE_URL 對 gemini-native 類無效" in caplog.text
    assert "__SAFETY_SETTINGS 對 gemini-native 類無效" in caplog.text


@pytest.mark.parametrize(
    ("endpoint_id", "override", "expected"),
    [
        ("google", None, False),
        ("openai", None, False),
        ("yinli", None, True),
        ("yinli", False, False),
        ("yinli", "off", False),
        ("google", True, True),
        ("google", "TRUE", True),
    ],
)
def test_safety_settings_default_and_override(endpoint_id, override, expected):
    """預設依 ID（google / openai 不送），可逐端點以布林或 true/false 字串覆寫。"""
    with _patched(LLM_ENDPOINTS={endpoint_id: _relay(safety_settings=override)}):
        endpoint = get_endpoint(endpoint_id)

    assert endpoint is not None
    assert endpoint.send_safety_settings is expected


# ----------------------------------------------------------------------
# 舊式單組設定的合成（Legacy synthesis）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "endpoint_type", "safety", "listed"),
    [
        ("google", "openai", False, True),
        ("openai", "openai", False, True),
        ("yinli", "openai", True, True),
        ("my-relay", "openai", True, True),
        ("", "openai", False, False),
        ("gemini-native", "gemini-native", False, True),
    ],
)
def test_legacy_settings_become_env_endpoint(provider, endpoint_type, safety, listed):
    """現有 .env 不改：LLM_PROVIDER 那組合成為 ID＝LLM_PROVIDER 的端點，模型自動進白名單。"""
    with _patched(LLM_PROVIDER=provider):
        endpoint = get_endpoint()

    assert endpoint is not None
    assert endpoint.id == provider
    assert endpoint.type == endpoint_type
    assert endpoint.send_safety_settings is safety
    assert endpoint.listed is listed
    assert endpoint.models == (ModelOption("gemini-3.5-flash"),)
    if endpoint_type == "openai":
        assert endpoint.base_url == "https://legacy.example/v1"
    assert endpoint.api_key is not None and endpoint.api_key.get_secret_value() == "legacy-key"
    assert endpoint.api_key_setting == "LLM_API_KEY"


def test_legacy_cli_model_and_effort_join_cli_whitelist():
    """LLM_PROVIDER 為 CLI 時，LLM_MODEL_NAME@預設 effort 併進該 CLI 的白名單。"""
    with _patched(
        LLM_PROVIDER="claude-code",
        LLM_MODEL_NAME="opus",
        LLM_CLAUDE_CODE_EFFORT="Medium",
        LLM_CLAUDE_CODE_MODELS=("opus@high", "haiku"),
    ):
        endpoint = get_endpoint()

    assert endpoint is not None and endpoint.id == "claude-code"
    assert endpoint.models == (ModelOption("opus", ("high", "medium")), ModelOption("haiku"))


def test_env_default_does_not_rewrite_plain_whitelist_entry(caplog):
    """白名單把模型寫成不帶 effort：.env 預設帶 effort 時不改寫它，只提醒。"""
    with _patched(
        LLM_PROVIDER="claude-code",
        LLM_MODEL_NAME="haiku",
        LLM_CLAUDE_CODE_MODELS=("haiku",),
    ), caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        endpoint = get_endpoint()

    assert endpoint is not None
    assert endpoint.models == (ModelOption("haiku"),)
    assert endpoint.allows("haiku", None)
    assert "不列入白名單" in caplog.text


def test_legacy_cli_invalid_default_effort_is_not_whitelisted():
    """預設 effort 不合法時不加進白名單（客戶端建立時照舊拋錯）。"""
    with _patched(LLM_PROVIDER="antigravity", LLM_MODEL_NAME="gemini-x", LLM_ANTIGRAVITY_EFFORT="ultra"):
        endpoint = get_endpoint()

    assert endpoint is not None
    assert endpoint.models == ()


def test_explicit_endpoint_with_provider_id_wins(caplog):
    """LLM_ENDPOINTS 有與 LLM_PROVIDER 同 ID 的端點時以它為準，舊網址與金鑰不再使用。"""
    with _patched(
        LLM_PROVIDER="yinli",
        LLM_ENDPOINTS={"yinli": _relay(base_url="https://new.example/v1", models=("other",))},
    ), caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        endpoint = get_endpoint()
        is_custom = env_endpoint_is_custom()

    assert is_custom is True
    assert endpoint is not None
    assert endpoint.base_url == "https://new.example/v1"
    assert endpoint.api_key.get_secret_value() == "relay-key"
    assert endpoint.models == (ModelOption("other"), ModelOption("gemini-3.5-flash"))
    assert "不再生效" in caplog.text


def test_skipped_custom_endpoint_is_not_replaced_by_legacy():
    """LLM_PROVIDER 指向的自訂端點寫錯被跳過時，不以舊的 LLM_API_KEY 合成替身。"""
    with _patched(LLM_PROVIDER="yinli", LLM_ENDPOINTS={"yinli": _relay(type="opneai")}):
        assert env_endpoint_is_custom() is True
        assert get_endpoint() is None


def test_anthropic_legacy_endpoint_is_unlisted_placeholder():
    """anthropic 不會被合成成 openai 類端點，且不可選。"""
    with _patched(LLM_PROVIDER="anthropic"):
        endpoint = get_endpoint()

    assert endpoint is not None
    assert endpoint.type == "anthropic"
    assert endpoint.listed is False


def test_list_endpoints_only_selectable_ones():
    """管理介面只列可選且白名單非空的端點。"""
    with _patched(
        LLM_PROVIDER="",
        LLM_ENDPOINTS={"empty": _relay(models=()), "relay": _relay()},
        LLM_CLAUDE_CODE_MODELS=("opus@high",),
    ):
        ids = [endpoint.id for endpoint in list_endpoints()]

    assert ids == ["claude-code", "relay"]


def test_get_endpoint_normalizes_id():
    """查詢時 ID 不分大小寫與前後空白。"""
    with _patched(LLM_ENDPOINTS={"relay": _relay()}):
        assert get_endpoint(" Relay ") is not None
        assert get_endpoint("missing") is None


def test_startup_summary_reports_errors_and_hides_keys(caplog):
    """啟動摘要：設定錯誤在此首次記 ERROR，摘要列出端點但不含金鑰。"""
    with _patched(
        LLM_ENDPOINTS={"relay": _relay(api_key=SecretStr("sk-secret")), "bad-id": _relay()}
    ), caplog.at_level(logging.INFO, logger=endpoints.__name__):
        log_endpoint_summary()

    assert "LLM 端點已載入" in caplog.text
    assert "relay[openai" in caplog.text
    assert "'bad-id' 不合規" in caplog.text
    assert "sk-secret" not in caplog.text


@pytest.mark.parametrize(
    "bad_line",
    ["LLM_ENDPOINTS__FOO=bar", "LLM_ENDPOINTS__FOO__API_KEY__X=k", "LLM_ENDPOINTS__FOO__TYPE__X=openai"],
    ids=["missing-field", "extra-level-key", "extra-level-type"],
)
def test_structural_mistake_skips_endpoint_without_breaking_settings(tmp_path, bad_line, caplog):
    """結構寫錯（漏欄位名、多一層）：Settings() 不拋錯，該端點跳過並記 ERROR，其他端點照常。"""
    with caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        loaded = _load_env_file(
            tmp_path,
            f"{bad_line}\n"
            "LLM_ENDPOINTS__GOOD__BASE_URL=https://good.example/v1\n"
            "LLM_ENDPOINTS__GOOD__API_KEY=k\n"
            'LLM_ENDPOINTS__GOOD__MODELS=["m"]\n',
        )

    assert "foo" not in loaded
    assert "good" in loaded
    assert "LLM_ENDPOINTS__FOO：結構錯誤" in caplog.text


def test_skip_reason_is_available_to_callers():
    """被跳過的端點可查到原因（工廠據此放進錯誤訊息）；正常或未定義的端點為 None。"""
    with _patched(LLM_ENDPOINTS={"bad": _relay(api_key=None), "good": _relay()}):
        assert skip_reason("BAD") == "API_KEY 未設定"
        assert skip_reason("good") is None
        assert skip_reason("missing") is None


def test_summary_lists_skipped_endpoints(caplog):
    """啟動摘要另記一行 WARNING，列出被跳過的端點與原因。"""
    with _patched(LLM_ENDPOINTS={"bad": _relay(base_url=None)}), \
         caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        log_endpoint_summary()

    assert "LLM 端點設定有誤已跳過：bad（BASE_URL 未設定（openai 類必填））" in caplog.text


def test_unrecognized_safety_message_states_effective_value(caplog):
    """SAFETY_SETTINGS 無法辨識時，訊息寫出實際採用的值。"""
    with _patched(LLM_ENDPOINTS={"google": _relay(safety_settings="maybe")}), \
         caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        endpoint = get_endpoint("google")

    assert endpoint is not None and endpoint.send_safety_settings is False
    assert "改用預設規則：不送 safety_settings" in caplog.text


def test_malformed_cli_whitelist_states_consequence(caplog):
    """CLI 白名單格式錯誤：說明後果（只剩 .env 那一層的模型）。"""
    with _patched(LLM_ANTIGRAVITY_MODELS="gemini-x"), \
         caplog.at_level(logging.ERROR, logger=endpoints.__name__):
        load_endpoints()

    assert "antigravity 端點的白名單視為空" in caplog.text


def test_overlong_env_model_is_reported(caplog):
    """LLM_MODEL_NAME 超過欄位長度：不列入白名單並提醒（.env 那一層照常使用）。"""
    with _patched(LLM_MODEL_NAME="x" * 129), \
         caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        endpoint = get_endpoint()

    assert endpoint is not None and endpoint.models == ()
    assert "LLM_MODEL_NAME 超過 128 字元" in caplog.text


@pytest.mark.parametrize("top_line", ["LLM_ENDPOINTS=", "LLM_ENDPOINTS=foo", "LLM_ENDPOINTS=[]"])
def test_top_level_value_does_not_break_settings(tmp_path, top_line, caplog):
    """頂層直接設值（留空、亂寫、陣列）：Settings() 不拋錯，自訂端點全部忽略並提醒。"""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{top_line}\n", encoding="utf-8")
    parsed = Settings(_env_file=env_file)

    with _patched(LLM_ENDPOINTS=parsed.LLM_ENDPOINTS), \
         caplog.at_level(logging.WARNING, logger=endpoints.__name__):
        loaded = load_endpoints()
        is_custom = env_endpoint_is_custom()

    assert set(loaded) == {"google", "claude-code", "antigravity"}
    assert is_custom is False
    assert "LLM_ENDPOINTS 不應直接設值" in caplog.text
