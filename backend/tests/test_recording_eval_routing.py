"""錄音評分的能力路由測試（能力路由計畫 capability_routing_FEAT_2026-10-19.md R2）。

Tests for capability routing R2: the ``audio_llm`` / ``stt`` / ``audio_eval``
capabilities, their whitelists and ``.env`` defaults, the built-in ``audio``
endpoint, building the recording evaluator from the routes of a route key
(branch → flow → global → ``.env``), the evaluator cache, and validation of
writes. Acceptance (plan §9): the three strategies can be switched through
settings and persist across a restart; trilingual and dialog cards can use
different strategies.
"""

import asyncio
import json
from contextlib import ExitStack
from unittest import mock

import pytest
from pydantic import SecretStr

from app.core.config import LLMEndpointSettings, settings
from app.core.exceptions import AudioEvaluatorUnavailableError, InvalidRouteError
from app.infrastructure.audio_evaluator import factory as audio_factory
from app.infrastructure.audio_evaluator.gemini_client import GeminiNativeAudioEvaluator
from app.infrastructure.audio_evaluator.openai_client import OpenAIAudioEvaluator
from app.infrastructure.audio_evaluator.proxy_client import ProxyAudioEvaluator
from app.infrastructure.audio_evaluator.stt_diff_evaluator import STTDiffEvaluator
from app.infrastructure.audio_evaluator.stt_llm_evaluator import STTLLMEvaluator
from app.infrastructure.llm import routing
from app.infrastructure.llm.capabilities import RouteChoice, check_choice, list_providers
from app.infrastructure.llm.endpoints import get_endpoint, load_endpoints
from app.infrastructure.llm.gemini_native_client import GeminiNativeLLMClient
from app.infrastructure.llm.routing import Route
from app.schemas.llm.speaking import AudioEvaluationResult
from app.services import capability_route_service, recording_eval

STT_URL = "http://stt.test/v1"


def _run(coro):
    return asyncio.run(coro)


def _patched(**values):
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(mock.patch.object(settings, name, value))
    return stack


@pytest.fixture
def audio_env():
    """生產環境相似的設定：Google 金鑰（gemini_native）、Whisper 伺服器、三個可選的音訊模型。"""
    with _patched(
        AUDIO_EVALUATOR_PROVIDER="gemini_native",
        AUDIO_API_KEY="google-key",
        AUDIO_BASE_URL=None,
        AUDIO_MODEL_NAME="gemini-3.5-flash",
        AUDIO_MODELS=json.dumps(["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"]),
        STT_LLM_MODEL_NAME="gemini-2.5-flash",
        STT_SERVER_URL=STT_URL,
        STT_MODEL_NAME="Systran/faster-whisper-large-v3",
        STT_MODELS=json.dumps(["Systran/faster-whisper-small"]),
        STT_API_KEY="speaches",
    ), mock.patch("app.infrastructure.audio_evaluator.gemini_client.genai.Client"), \
         mock.patch("app.infrastructure.llm.gemini_native_client.genai.Client"):
        audio_factory.clear_cache()
        yield
        audio_factory.clear_cache()


def _rows(**rows):
    """``_fetch_choices`` 回傳的設定：``rows`` 為 ``"用途鍵|能力" → 選擇``（``*`` 寫成 GLOBAL）。"""
    snapshot: dict[str, dict[str, RouteChoice]] = {}
    for spec, choice in rows.items():
        key, capability = spec.split("|")
        snapshot.setdefault(capability, {})["*" if key == "GLOBAL" else key] = choice
    routing.invalidate_cache()
    return mock.patch.object(routing, "_fetch_choices", mock.AsyncMock(return_value=snapshot))


# ----------------------------------------------------------------------
# 內建 audio 端點與白名單（§6.3、§6.4）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "base_url", "endpoint_type", "transport"),
    [
        ("gemini_native", "https://ignored.example/v1", "gemini-native", None),
        ("openai", None, "openai", "input_audio"),
        ("proxy", "https://relay.example/v1", "openai", "image_url"),
        ("stt_llm", None, "openai", "input_audio"),
        ("stt_diff", "https://relay.example/v1", "openai", "input_audio"),
    ],
)
def test_audio_endpoint_type_follows_the_old_provider(audio_env, provider, base_url, endpoint_type, transport):
    """audio 端點的類型與送音檔方式沿用 AUDIO_EVALUATOR_PROVIDER 原本的意思。"""
    with _patched(AUDIO_EVALUATOR_PROVIDER=provider, AUDIO_BASE_URL=base_url):
        endpoint = get_endpoint("audio")
    assert endpoint is not None
    assert (endpoint.type, endpoint.audio_transport) == (endpoint_type, transport)
    if endpoint_type == "openai":
        assert endpoint.base_url == (base_url or "https://api.openai.com/v1")


def test_audio_endpoint_whitelists(audio_env):
    """音訊白名單＝AUDIO_MODELS＋AUDIO_MODEL_NAME；文字白名單另加 STT_LLM_MODEL_NAME。"""
    endpoint = get_endpoint("audio")
    assert [o.name for o in endpoint.audio_models] == ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"]
    assert [o.name for o in endpoint.models] == [*[o.name for o in endpoint.audio_models], "gemini-2.5-flash"]
    assert endpoint.listed


def test_audio_endpoint_without_key_is_not_selectable(audio_env):
    """沒有 AUDIO_API_KEY：audio 端點還在（.env 那一層用到時報金鑰未設定），但不可選。"""
    with _patched(AUDIO_API_KEY=None):
        assert get_endpoint("audio").listed is False
        assert list_providers("audio_llm") == []
        problem = check_choice("audio_llm", RouteChoice("audio", "gemini-3.5-flash", None))
    assert problem is not None and "AUDIO_API_KEY" in problem


def test_custom_endpoint_audio_models_and_transport():
    """自訂端點以 AUDIO_MODELS 標明能聽音檔的模型，AUDIO_TRANSPORT 決定送音檔的方式。"""
    endpoints = {
        "relay": LLMEndpointSettings(
            base_url="https://relay.example/v1", api_key=SecretStr("k"), models=("m1",),
            audio_models=json.dumps(["audio-1"]), audio_transport="image_url",
        ),
        "plain": LLMEndpointSettings(base_url="https://plain.example/v1", api_key=SecretStr("k"), models=("m1",)),
    }
    with _patched(LLM_ENDPOINTS=endpoints):
        loaded = load_endpoints()
        options = {option.id: option for option in list_providers("audio_llm")}
    assert [o.name for o in loaded["relay"].audio_models] == ["audio-1"]
    assert loaded["relay"].audio_transport == "image_url"
    assert loaded["plain"].audio_models == () and "plain" not in options
    assert [o.name for o in options["relay"].models] == ["audio-1"]


def test_custom_endpoint_cannot_take_the_audio_id():
    """audio 是保留 ID：自訂端點不能用它（避免頂替內建 audio 端點）。"""
    endpoints = {"audio": LLMEndpointSettings(base_url="https://x.example/v1", api_key=SecretStr("k"), models=("m",))}
    with _patched(LLM_ENDPOINTS=endpoints, AUDIO_API_KEY="google-key"):
        assert get_endpoint("audio").type == "gemini-native"


def test_stt_whitelist_and_strategies(audio_env):
    """stt 的白名單＝STT_MODELS＋STT_MODEL_NAME；audio_eval 為固定的三個策略、不選模型。"""
    [whisper] = list_providers("stt")
    assert whisper.id == "whisper"
    assert [o.name for o in whisper.models] == ["Systran/faster-whisper-small", "Systran/faster-whisper-large-v3"]
    assert [option.id for option in list_providers("audio_eval")] == ["listen", "stt_diff", "stt_llm"]
    assert check_choice("audio_eval", RouteChoice("listen", "gemini-3.5-flash", None)) is not None
    with _patched(STT_SERVER_URL=None):
        assert list_providers("stt") == []


# ----------------------------------------------------------------------
# 用途鍵宣告（§4.1、§8.1）
# ----------------------------------------------------------------------


def test_recording_eval_routes_are_declared():
    """recording_eval 與三語卡分支已宣告，宣告了錄音評分的四個能力；分支在流程鍵之後。"""
    keys = [route.key for route in routing.list_route_keys()]
    assert keys.index("recording_eval") + 1 == keys.index("recording_eval.speaking_trilingual")
    assert recording_eval.RECORDING_EVAL.capabilities == {"audio_eval", "audio_llm", "stt", "llm"}
    assert recording_eval.route_key_for_handler("speaking_trilingual") == "recording_eval.speaking_trilingual"
    assert recording_eval.route_key_for_handler("speaking_coach") == "recording_eval"


def test_strategy_restriction_requires_audio_eval():
    """策略限縮只能用在宣告了 audio_eval 的鍵，且必須是已知策略的非空子集。"""
    with pytest.raises(ValueError, match="策略限縮"):
        Route("x", "測試", frozenset({"llm"}), frozenset({"stt_diff"}))
    with pytest.raises(ValueError, match="策略限縮"):
        Route("x", "測試", frozenset({"audio_eval"}), frozenset({"bogus"}))


def test_branch_inherits_strategy_restriction_and_env_defaults(declare_route_key):
    """分支沒有自己的限縮時沿用流程鍵的；流程鍵宣告的 .env 預設，分支也用得到。"""
    routing.route(
        "shadow_test", "跟讀", {"audio_eval", "llm"}, strategies={"stt_diff"},
        env_defaults={"llm": lambda: RouteChoice("audio", "x", None)},
    )
    routing.route("shadow_test.branch", "分支", {"audio_eval", "llm"})
    try:
        assert routing.allowed_strategies("shadow_test.branch") == {"stt_diff"}
        assert routing.check_choice(RouteChoice("listen", "", None), "audio_eval", route_key="shadow_test.branch")
        assert routing.env_choice_for("shadow_test.branch", "llm") == RouteChoice("audio", "x", None)
    finally:
        routing._DECLARED.pop("shadow_test", None)
        routing._DECLARED.pop("shadow_test.branch", None)


# ----------------------------------------------------------------------
# 依路由建立評分器（§3.2、§8.3）
# ----------------------------------------------------------------------


def test_env_defaults_keep_the_old_behaviour(audio_env):
    """沒有任何設定：gemini_native＋AUDIO_MODEL_NAME 直接聽音檔（與能力路由之前相同）。"""
    evaluator, plan = _run(recording_eval.evaluator_for_handler("speaking_coach"))
    assert isinstance(evaluator, GeminiNativeAudioEvaluator)
    assert evaluator._model_name == "gemini-3.5-flash"
    assert plan.strategy_name == "listen" and plan.strategy.source == "env"


@pytest.mark.parametrize(
    ("provider", "base_url", "cls"),
    [("openai", None, OpenAIAudioEvaluator), ("proxy", "https://relay.example/v1", ProxyAudioEvaluator)],
)
def test_env_openai_and_proxy(audio_env, provider, base_url, cls):
    with _patched(AUDIO_EVALUATOR_PROVIDER=provider, AUDIO_BASE_URL=base_url):
        evaluator, _ = _run(recording_eval.evaluator_for_handler("speaking_coach"))
    assert type(evaluator) is cls


def test_global_llm_row_does_not_override_recording_eval(audio_env):
    """全域 * 的 llm（建卡用，例如 Claude Code）不會連帶改掉錄音評分宣告的預設；設定錄音評分本身才會。"""
    with _rows(**{"GLOBAL|llm": RouteChoice("audio", "gemini-3.1-pro-preview", None)}):
        resolved = _run(routing.resolve("recording_eval.speaking_trilingual", "llm"))
        card = _run(routing.resolve("jp_verb_pair", "llm"))
    assert (resolved.source, resolved.model) == ("env", "gemini-2.5-flash")
    assert (card.source, card.model) == ("global", "gemini-3.1-pro-preview")

    with _rows(**{
        "GLOBAL|llm": RouteChoice("audio", "gemini-3.1-pro-preview", None),
        "recording_eval|llm": RouteChoice("audio", "gemini-3.1-flash-lite", None),
    }):
        resolved = _run(routing.resolve("recording_eval.speaking_trilingual", "llm"))
    assert (resolved.source, resolved.model) == ("parent", "gemini-3.1-flash-lite")


def test_stt_llm_text_model_defaults_to_the_audio_endpoint(audio_env):
    """以設定切到 stt_llm（.env 仍是 gemini_native）：文字 LLM 在 .env 那一層用 audio 端點（gemini-native）
    ＋STT_LLM_MODEL_NAME，不跟著建卡的 LLM_PROVIDER。"""
    with _patched(LLM_PROVIDER="claude-code", LLM_MODEL_NAME="claude-opus-5"), \
         _rows(**{"recording_eval|audio_eval": RouteChoice("stt_llm", "", None)}):
        evaluator, plan = _run(recording_eval.evaluator_for_handler("speaking_trilingual"))
    assert isinstance(evaluator, STTLLMEvaluator)
    assert isinstance(evaluator._llm, GeminiNativeLLMClient)
    assert (evaluator._llm.provider, evaluator._llm.model_name) == ("audio", "gemini-2.5-flash")
    assert evaluator._whisper.model_name == "Systran/faster-whisper-large-v3"
    assert plan.routes["llm"].source == "env"


def test_trilingual_and_dialog_cards_use_different_strategies(audio_env):
    """驗收：三語卡分支設 stt_llm、流程鍵設 listen——兩種卡片各用各的；分支沒設的能力往上找。"""
    with _rows(**{
        "recording_eval|audio_eval": RouteChoice("listen", "", None),
        "recording_eval|audio_llm": RouteChoice("audio", "gemini-3.1-pro-preview", None),
        "recording_eval.speaking_trilingual|audio_eval": RouteChoice("stt_llm", "", None),
        "recording_eval.speaking_trilingual|llm": RouteChoice("audio", "gemini-3.1-flash-lite", None),
        "GLOBAL|stt": RouteChoice("whisper", "Systran/faster-whisper-small", None),
    }):
        coach, coach_plan = _run(recording_eval.evaluator_for_handler("speaking_coach"))
        trilingual, trilingual_plan = _run(recording_eval.evaluator_for_handler("speaking_trilingual"))

    assert isinstance(coach, GeminiNativeAudioEvaluator) and coach._model_name == "gemini-3.1-pro-preview"
    assert coach_plan.strategy.source == "route"
    assert isinstance(trilingual, STTLLMEvaluator)
    assert trilingual._llm.model_name == "gemini-3.1-flash-lite"
    assert trilingual._whisper.model_name == "Systran/faster-whisper-small"
    assert trilingual_plan.routes["stt"].source == "global"


@pytest.mark.parametrize("strategy", ["listen", "stt_diff", "stt_llm"])
def test_each_strategy_switches_through_settings(audio_env, strategy):
    """驗收：三種策略都能經設定切換。"""
    expected = {"listen": GeminiNativeAudioEvaluator, "stt_diff": STTDiffEvaluator, "stt_llm": STTLLMEvaluator}
    with _rows(**{"recording_eval|audio_eval": RouteChoice(strategy, "", None)}):
        evaluator, _ = _run(recording_eval.evaluator_for_handler("speaking_coach"))
    assert type(evaluator) is expected[strategy]


def test_evaluator_is_cached_until_a_setting_changes(audio_env):
    """同一組設定重用同一個評分器；寫入或刪除設定（快取世代改變）後重建。"""
    first, _ = _run(recording_eval.evaluator_for_handler("speaking_coach"))
    again, _ = _run(recording_eval.evaluator_for_handler("speaking_coach"))
    routing.invalidate_cache()
    rebuilt, _ = _run(recording_eval.evaluator_for_handler("speaking_coach"))
    assert first is again
    assert rebuilt is not first


@pytest.mark.parametrize(
    ("values", "fragment"),
    [
        ({"AUDIO_EVALUATOR_PROVIDER": "bogus"}, "策略 'bogus' 無效"),
        ({"AUDIO_API_KEY": None}, "AUDIO_API_KEY 未設定"),
        ({"AUDIO_EVALUATOR_PROVIDER": "stt_diff", "STT_SERVER_URL": None}, "STT_SERVER_URL 未設定"),
    ],
    ids=["unknown-strategy", "no-audio-key", "no-stt-server"],
)
def test_unbuildable_evaluator_says_which_setting(audio_env, values, fragment):
    """建不出評分器：AudioEvaluatorUnavailableError 說明是哪個能力、哪一層、哪個設定。"""
    with _patched(**values), pytest.raises(AudioEvaluatorUnavailableError) as caught:
        _run(recording_eval.evaluator_for_handler("speaking_coach"))
    assert fragment in caught.value.message
    assert "/setconfig" in caught.value.message


def test_stt_llm_scores_with_the_routed_llm(audio_env):
    """stt_llm：Whisper 轉文字後交給路由選的 LLM 客戶端，逐字稿以 STT 結果為準。"""
    whisper = mock.MagicMock(model_name="w")
    whisper.transcribe = mock.AsyncMock(return_value="こんにちは")
    llm = mock.MagicMock(model_name="gemini-2.5-flash", model_label="(audio)gemini-2.5-flash")
    llm.generate_structured_data = mock.AsyncMock(return_value=mock.MagicMock(
        parsed_data={"score": 88, "feedback": "ok", "transcript": "echo"}, raw_content="{}",
    ))
    with mock.patch("app.infrastructure.audio_evaluator.stt_llm_evaluator.get_template_engine") as engine:
        engine.return_value.render.return_value = "prompt"
        result = _run(STTLLMEvaluator(whisper, llm).evaluate_audio(b"x", "a.ogg", "p", "c", ["r"]))
    assert isinstance(result, AudioEvaluationResult)
    assert (result.score, result.transcript, result.evaluator_label) == (88, "こんにちは", "stt+gemini-2.5-flash")
    assert "こんにちは" in llm.generate_structured_data.await_args.kwargs["user_prompt"]


# ----------------------------------------------------------------------
# 寫入（§8.2）與重啟後仍在
# ----------------------------------------------------------------------


def test_strategy_persists_across_restart(llm_route_db, llm_whitelist, audio_env):
    """驗收：設定存在資料庫；清掉程序內的快取（等同重啟）後仍生效。"""
    _run(capability_route_service.set_route(
        "recording_eval", "stt_diff", None, None, updated_by="api", capability="audio_eval",
    ))
    routing.invalidate_cache()
    audio_factory.clear_cache()
    evaluator, plan = _run(recording_eval.evaluator_for_handler("speaking_trilingual"))
    assert isinstance(evaluator, STTDiffEvaluator)
    assert (plan.strategy.source, plan.strategy.route_key) == ("parent", "recording_eval.speaking_trilingual")


@pytest.mark.parametrize(
    ("key", "capability", "provider", "model", "fragment"),
    [
        ("recording_eval", "audio_eval", "gemini", None, "評分策略只能是"),
        ("recording_eval", "audio_eval", "listen", "gemini-3.5-flash", "不選模型"),
        ("jp_verb_pair", "audio_eval", "listen", None, "沒有宣告能力"),
        ("recording_eval", "audio_llm", "audio", "gemini-2.5-flash", "不在"),
        ("recording_eval", "stt", "whisper", "unknown-model", "不在"),
        ("recording_eval", "stt", "speaches", "Systran/faster-whisper-small", "只能是 'whisper'"),
    ],
)
def test_invalid_writes_are_rejected(llm_route_db, llm_whitelist, audio_env, key, capability, provider, model, fragment):
    """寫入依能力檢查白名單；用途鍵沒有宣告的能力不能寫。"""
    import app.services.task_handlers.jp_verb_pair_handler  # noqa: F401 - 宣告 jp_verb_pair

    with pytest.raises(InvalidRouteError) as caught:
        _run(capability_route_service.set_route(key, provider, model, None, updated_by="api", capability=capability))
    assert fragment in caught.value.message


def test_audio_llm_write_is_trial_built(llm_route_db, llm_whitelist, audio_env):
    """audio_llm 寫入前試建評分器：建不起來（例如套件版本不符）就拒絕，不寫入。"""
    error = ImportError("cannot import name 'Client' from 'google.genai'", name="google.genai")
    with mock.patch.object(audio_factory, "build_listen_evaluator", side_effect=error), \
         pytest.raises(InvalidRouteError, match="試建工具失敗"):
        _run(capability_route_service.set_route(
            "recording_eval", "audio", "gemini-3.1-flash-lite", None, updated_by="api", capability="audio_llm",
        ))
    view = _run(capability_route_service.set_route(
        "recording_eval", "audio", "gemini-3.1-flash-lite", None, updated_by="api", capability="audio_llm",
    ))
    assert view.label == "(audio)gemini-3.1-flash-lite"


def test_listing_shows_every_capability_with_its_source(llm_route_db, llm_whitelist, audio_env):
    """清單：錄音評分的鍵列出四個能力，.env 那一層的標籤與來源；分支沒設的能力標「流程」。"""
    _run(capability_route_service.set_route(
        "recording_eval", "stt_llm", None, None, updated_by="api", capability="audio_eval",
    ))
    key_views, _ = _run(capability_route_service.list_routes())
    branch = next(view for view in key_views if view.route_key == "recording_eval.speaking_trilingual")
    labels = {view.capability: (view.label, view.resolved.source) for view in branch.views}
    assert labels == {
        "llm": ("(audio)gemini-2.5-flash", "env"),
        "audio_eval": ("stt_llm", "parent"),
        "audio_llm": ("(audio)gemini-3.5-flash", "env"),
        "stt": ("(whisper)Systran/faster-whisper-large-v3", "env"),
    }
    assert all(view.problem is None for view in branch.views)
