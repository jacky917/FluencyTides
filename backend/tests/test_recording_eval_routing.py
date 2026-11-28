"""錄音評分的能力路由測試（能力路由計畫 capability_routing_FEAT_2026-10-19.md R2）。

Tests for capability routing R2: the ``audio_llm`` / ``stt`` / ``audio_eval``
capabilities, their whitelists and ``.env`` defaults, the built-in ``audio``
endpoint, building the recording evaluator from the routes of a route key
(the key → the voice global ``*voice`` → ``.env``, and the global switch), the evaluator cache, and validation of
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
from app.core.exceptions import AudioEvaluatorUnavailableError, InvalidRouteError, RouteNotFoundError
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
# 用途鍵宣告（§4.1、§8.1；三層與語音全域見 recording_eval_settings_FEAT_2026-11-26.md）
# ----------------------------------------------------------------------

COACH = recording_eval.RECORDING_EVAL_COACH.key
TRILINGUAL = recording_eval.RECORDING_EVAL_TRILINGUAL.key
VOICE = routing.VOICE_GLOBAL_ROUTE_KEY


def _evaluator(route_key):
    return _run(audio_factory.evaluator_for(route_key))


def test_recording_eval_routes_are_declared():
    """每種能錄音的卡片各宣告自己的語音用途鍵（四個能力）；沒有共用的流程鍵 recording_eval。"""
    from app.services.task_handlers.speaking_coach_handler import SpeakingCoachHandler
    from app.services.task_handlers.speaking_trilingual_handler import SpeakingTrilingualHandler

    for declared in (recording_eval.RECORDING_EVAL_COACH, recording_eval.RECORDING_EVAL_TRILINGUAL):
        assert declared.voice and declared.capabilities == {"audio_eval", "audio_llm", "stt", "llm"}
    assert routing.get_route("recording_eval") is None
    assert SpeakingCoachHandler.RECORDING_EVAL.route.key == COACH == "recording_eval.speaking_coach"
    assert SpeakingTrilingualHandler.RECORDING_EVAL.route.key == TRILINGUAL == "recording_eval.speaking_trilingual"


def test_strategy_restriction_requires_audio_eval():
    """策略限縮只能用在宣告了 audio_eval 的鍵，且必須是已知策略的非空子集。"""
    with pytest.raises(ValueError, match="策略限縮"):
        Route("x", "測試", frozenset({"llm"}), frozenset({"stt_diff"}))
    with pytest.raises(ValueError, match="策略限縮"):
        Route("x", "測試", frozenset({"audio_eval"}), frozenset({"bogus"}), voice=True)


def test_global_keys_are_reserved():
    """* 與 *voice 保留給兩個全域，不能宣告成用途鍵。"""
    for key in ("*", "*voice"):
        with pytest.raises(ValueError, match="保留給全域"):
            Route(key, "測試", frozenset({"llm"}))


def test_dotted_keys_do_not_inherit(declare_route_key):
    """「.」只是命名：a.b 不沿用 a 的策略限縮與 .env 預設；語音用途的 llm 預設是 audio 端點。"""
    routing.route(
        "shadow_test", "跟讀", {"audio_eval", "llm"}, strategies={"stt_diff"},
        env_defaults={"llm": lambda: RouteChoice("audio", "x", None)}, voice=True,
    )
    routing.route("shadow_test.branch", "分支", {"audio_eval", "llm"}, voice=True)
    try:
        assert routing.allowed_strategies("shadow_test") == {"stt_diff"}
        assert routing.allowed_strategies("shadow_test.branch") is None
        assert routing.declared_default_for("shadow_test", "llm") == RouteChoice("audio", "x", None)
        with _patched(AUDIO_API_KEY="google-key", STT_LLM_MODEL_NAME="gemini-2.5-flash"):
            assert routing.declared_default_for("shadow_test.branch", "llm") == RouteChoice(
                "audio", "gemini-2.5-flash", None
            )
    finally:
        routing._DECLARED.pop("shadow_test", None)
        routing._DECLARED.pop("shadow_test.branch", None)


def test_each_capability_belongs_to_one_global(declare_route_key):
    """語音能力與語音用途的 llm 歸 *voice；其他文字用途的 llm 歸 *；全域鍵屬於自己。"""
    declare_route_key("text_use")
    assert routing.global_key_for("text_use", "llm") == "*"
    assert routing.global_key_for(TRILINGUAL, "llm") == VOICE
    assert routing.global_key_for(TRILINGUAL, "stt") == VOICE
    assert routing.global_key_for("orphan.key", "audio_eval") == VOICE
    assert routing.global_key_for("*", "llm") == "*"
    assert routing.global_capabilities("*") == {"llm"}
    assert routing.global_capabilities(VOICE) == {"llm", "audio_eval", "audio_llm", "stt"}


# ----------------------------------------------------------------------
# 依路由建立評分器（§3.2、§8.3）
# ----------------------------------------------------------------------


def test_env_defaults_keep_the_old_behaviour(audio_env):
    """沒有任何設定：gemini_native＋AUDIO_MODEL_NAME 直接聽音檔（與能力路由之前相同）。"""
    evaluator, plan = _evaluator(COACH)
    assert isinstance(evaluator, GeminiNativeAudioEvaluator)
    assert evaluator._model_name == "gemini-3.5-flash"
    assert plan.strategy_name == "listen" and plan.strategy.source == "env"


@pytest.mark.parametrize(
    ("provider", "base_url", "cls"),
    [("openai", None, OpenAIAudioEvaluator), ("proxy", "https://relay.example/v1", ProxyAudioEvaluator)],
)
def test_env_openai_and_proxy(audio_env, provider, base_url, cls):
    with _patched(AUDIO_EVALUATOR_PROVIDER=provider, AUDIO_BASE_URL=base_url):
        evaluator, _ = _evaluator(COACH)
    assert type(evaluator) is cls


def test_text_global_does_not_reach_recording_eval(audio_env):
    """文字全域 * 的 llm（建卡用）不影響錄音評分；語音全域 *voice 的 llm 才會。"""
    with _rows(**{"GLOBAL|llm": RouteChoice("audio", "gemini-3.1-pro-preview", None)}):
        resolved = _run(routing.resolve(TRILINGUAL, "llm"))
        card = _run(routing.resolve("jp_verb_pair", "llm"))
    assert (resolved.source, resolved.model) == ("env", "gemini-2.5-flash")
    assert (card.source, card.global_key, card.model) == ("global", "*", "gemini-3.1-pro-preview")

    with _rows(**{
        "GLOBAL|llm": RouteChoice("audio", "gemini-3.1-pro-preview", None),
        f"{VOICE}|llm": RouteChoice("audio", "gemini-3.1-flash-lite", None),
    }):
        resolved = _run(routing.resolve(TRILINGUAL, "llm"))
    assert (resolved.source, resolved.global_key, resolved.model) == ("global", VOICE, "gemini-3.1-flash-lite")


def test_stt_llm_text_model_defaults_to_the_audio_endpoint(audio_env):
    """語音全域切到 stt_llm（.env 仍是 gemini_native）：文字 LLM 在 .env 那一層用 audio 端點（gemini-native）
    ＋STT_LLM_MODEL_NAME，不跟著建卡的 LLM_PROVIDER。"""
    with _patched(LLM_PROVIDER="claude-code", LLM_MODEL_NAME="claude-opus-5"), \
         _rows(**{f"{VOICE}|audio_eval": RouteChoice("stt_llm", "", None)}):
        evaluator, plan = _evaluator(TRILINGUAL)
    assert isinstance(evaluator, STTLLMEvaluator)
    assert isinstance(evaluator._llm, GeminiNativeLLMClient)
    assert (evaluator._llm.provider, evaluator._llm.model_name) == ("audio", "gemini-2.5-flash")
    assert evaluator._whisper.model_name == "Systran/faster-whisper-large-v3"
    assert plan.routes["llm"].source == "env"


def test_trilingual_and_dialog_cards_use_different_strategies(audio_env):
    """驗收：三語卡設 stt_llm、對話卡設 listen——兩種卡片各用各的；自己沒設的能力用語音全域。"""
    with _rows(**{
        f"{COACH}|audio_eval": RouteChoice("listen", "", None),
        f"{COACH}|audio_llm": RouteChoice("audio", "gemini-3.1-pro-preview", None),
        f"{TRILINGUAL}|audio_eval": RouteChoice("stt_llm", "", None),
        f"{TRILINGUAL}|llm": RouteChoice("audio", "gemini-3.1-flash-lite", None),
        f"{VOICE}|stt": RouteChoice("whisper", "Systran/faster-whisper-small", None),
    }):
        coach, coach_plan = _evaluator(COACH)
        trilingual, trilingual_plan = _evaluator(TRILINGUAL)

    assert isinstance(coach, GeminiNativeAudioEvaluator) and coach._model_name == "gemini-3.1-pro-preview"
    assert coach_plan.strategy.source == "route"
    assert isinstance(trilingual, STTLLMEvaluator)
    assert trilingual._llm.model_name == "gemini-3.1-flash-lite"
    assert trilingual._whisper.model_name == "Systran/faster-whisper-small"
    assert (trilingual_plan.routes["stt"].source, trilingual_plan.routes["stt"].global_key) == ("global", VOICE)


def test_voice_switch_makes_every_card_use_the_voice_global(audio_env):
    """語音全域的全局生效開著：各卡片整組用語音全域（全域沒設的用 .env），自己的設定保留但不生效。"""
    rows = {
        f"{TRILINGUAL}|audio_eval": RouteChoice("listen", "", None),
        f"{TRILINGUAL}|audio_llm": RouteChoice("audio", "gemini-3.1-pro-preview", None),
        f"{VOICE}|audio_eval": RouteChoice("stt_diff", "", None),
        f"{routing.VOICE_GLOBAL_ROUTE_KEY}|{routing.SWITCH_CAPABILITY}": RouteChoice(routing.SWITCH_ON, "", None),
    }
    with _rows(**rows):
        evaluator, plan = _evaluator(TRILINGUAL)
        audio_llm = _run(routing.resolve(TRILINGUAL, "audio_llm"))
    assert isinstance(evaluator, STTDiffEvaluator)
    assert (plan.strategy.source, plan.strategy.forced) == ("global", True)
    # 全域沒設 audio_llm：用 .env，不回去拿三語卡自己的
    assert (audio_llm.source, audio_llm.forced, audio_llm.model) == ("env", True, "gemini-3.5-flash")

    del rows[f"{routing.VOICE_GLOBAL_ROUTE_KEY}|{routing.SWITCH_CAPABILITY}"]
    with _rows(**rows):
        evaluator, plan = _evaluator(TRILINGUAL)
    assert isinstance(evaluator, GeminiNativeAudioEvaluator) and evaluator._model_name == "gemini-3.1-pro-preview"
    assert (plan.strategy.source, plan.strategy.forced) == ("route", False)


def test_text_switch_overrides_text_uses_only(audio_env, declare_route_key):
    """文字全域的全局生效開著：文字用途改用 *；錄音評分不受影響。"""
    declare_route_key("text_use")
    rows = {
        "text_use|llm": RouteChoice("audio", "gemini-3.1-flash-lite", None),
        "GLOBAL|llm": RouteChoice("audio", "gemini-3.1-pro-preview", None),
        f"GLOBAL|{routing.SWITCH_CAPABILITY}": RouteChoice(routing.SWITCH_ON, "", None),
        f"{TRILINGUAL}|llm": RouteChoice("audio", "gemini-3.5-flash", None),
    }
    with _rows(**rows):
        text = _run(routing.resolve("text_use", "llm"))
        voice = _run(routing.resolve(TRILINGUAL, "llm"))
    assert (text.source, text.forced, text.model) == ("global", True, "gemini-3.1-pro-preview")
    assert (voice.source, voice.forced, voice.model) == ("route", False, "gemini-3.5-flash")


@pytest.mark.parametrize("strategy", ["listen", "stt_diff", "stt_llm"])
def test_each_strategy_switches_through_settings(audio_env, strategy):
    """驗收：三種策略都能經設定切換。"""
    expected = {"listen": GeminiNativeAudioEvaluator, "stt_diff": STTDiffEvaluator, "stt_llm": STTLLMEvaluator}
    with _rows(**{f"{COACH}|audio_eval": RouteChoice(strategy, "", None)}):
        evaluator, _ = _evaluator(COACH)
    assert type(evaluator) is expected[strategy]


def test_evaluator_is_cached_until_a_setting_changes(audio_env):
    """同一組設定重用同一個評分器；寫入或刪除設定（快取世代改變）後重建。"""
    first, _ = _evaluator(COACH)
    again, _ = _evaluator(COACH)
    routing.invalidate_cache()
    rebuilt, _ = _evaluator(COACH)
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
        _evaluator(COACH)
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
    _run(capability_route_service.set_route(VOICE, "stt_diff", None, None, updated_by="api", capability="audio_eval"))
    routing.invalidate_cache()
    audio_factory.clear_cache()
    evaluator, plan = _evaluator(TRILINGUAL)
    assert isinstance(evaluator, STTDiffEvaluator)
    assert (plan.strategy.source, plan.strategy.global_key, plan.strategy.route_key) == ("global", VOICE, TRILINGUAL)


def test_switch_is_stored_and_keeps_route_settings(llm_route_db, llm_whitelist, audio_env):
    """開關存在資料庫（重啟後仍在）；打開時存的值不動、寫入時回報「目前不生效」，關掉後回到自己的設定。"""
    _run(capability_route_service.set_route(VOICE, "stt_diff", None, None, updated_by="api", capability="audio_eval"))
    _run(capability_route_service.set_global_switch(VOICE, True, updated_by="api"))
    view = _run(capability_route_service.set_route(
        TRILINGUAL, "listen", None, None, updated_by="api", capability="audio_eval",
    ))
    assert (view.resolved.source, view.resolved.forced, view.stored.endpoint) == ("global", True, "listen")
    routing.invalidate_cache()
    key_views, _ = _run(capability_route_service.list_routes())
    voice = next(item for item in key_views if item.route_key == VOICE)
    assert voice.switch is True
    assert next(item for item in key_views if item.route_key == TRILINGUAL).switch is None

    _run(capability_route_service.set_global_switch(VOICE, False, updated_by="api"))
    audio_factory.clear_cache()
    evaluator, plan = _evaluator(TRILINGUAL)
    assert isinstance(evaluator, GeminiNativeAudioEvaluator) and plan.strategy.source == "route"


def test_switch_only_exists_on_globals(llm_route_db, llm_whitelist):
    with pytest.raises(RouteNotFoundError):
        _run(capability_route_service.set_global_switch(TRILINGUAL, True, updated_by="api"))


@pytest.mark.parametrize(
    ("key", "capability", "provider", "model", "fragment"),
    [
        (VOICE, "audio_eval", "gemini", None, "評分策略只能是"),
        (VOICE, "audio_eval", "listen", "gemini-3.5-flash", "不選模型"),
        ("jp_verb_pair", "audio_eval", "listen", None, "沒有宣告能力"),
        ("*", "audio_eval", "listen", None, "沒有宣告能力"),
        (VOICE, "audio_llm", "audio", "gemini-2.5-flash", "不在"),
        (VOICE, "stt", "whisper", "unknown-model", "不在"),
        (TRILINGUAL, "stt", "speaches", "Systran/faster-whisper-small", "只能是 'whisper'"),
    ],
)
def test_invalid_writes_are_rejected(llm_route_db, llm_whitelist, audio_env, key, capability, provider, model, fragment):
    """寫入依能力檢查白名單；用途鍵（或全域）沒有宣告的能力不能寫——文字全域 * 只管 llm。"""
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
            VOICE, "audio", "gemini-3.1-flash-lite", None, updated_by="api", capability="audio_llm",
        ))
    view = _run(capability_route_service.set_route(
        VOICE, "audio", "gemini-3.1-flash-lite", None, updated_by="api", capability="audio_llm",
    ))
    assert view.label == "(audio)gemini-3.1-flash-lite"


def test_listing_shows_every_capability_with_its_source(llm_route_db, llm_whitelist, audio_env):
    """清單：錄音評分的鍵列出四個能力，.env 那一層的標籤與來源；自己沒設的能力標語音全域。"""
    _run(capability_route_service.set_route(VOICE, "stt_llm", None, None, updated_by="api", capability="audio_eval"))
    key_views, _ = _run(capability_route_service.list_routes())
    branch = next(view for view in key_views if view.route_key == TRILINGUAL)
    labels = {view.capability: (view.label, view.resolved.source) for view in branch.views}
    assert labels == {
        "llm": ("(audio)gemini-2.5-flash", "env"),
        "audio_eval": ("stt_llm", "global"),
        "audio_llm": ("(audio)gemini-3.5-flash", "env"),
        "stt": ("(whisper)Systran/faster-whisper-large-v3", "env"),
    }
    assert all(view.problem is None for view in branch.views)
    assert [view.route_key for view in key_views[:2]] == ["*", VOICE]


def test_evaluation_metadata_records_models_and_layers(audio_env):
    """存進錄音紀錄的評分資訊（D5）：策略、各能力的 (端點)模型，以及各能力生效的是哪一層（用途、*voice、.env）。"""
    from datetime import datetime, timedelta, timezone

    from app.bot.handlers.voice import evaluation_metadata, utc_timestamp

    with _rows(**{
        f"{TRILINGUAL}|audio_eval": RouteChoice("stt_llm", "", None),
        f"{VOICE}|stt": RouteChoice("whisper", "Systran/faster-whisper-small", None),
    }):
        plan = _run(audio_factory.plan_for(TRILINGUAL))
    assert evaluation_metadata(plan) == {
        "strategy": "stt_llm",
        "models": {"stt": "(whisper)Systran/faster-whisper-small", "llm": "(audio)gemini-2.5-flash"},
        "settings": {"audio_eval": TRILINGUAL, "stt": VOICE, "llm": ".env"},
    }
    taipei = datetime(2026, 11, 26, 14, 3, 12, tzinfo=timezone(timedelta(hours=8)))
    assert utc_timestamp(taipei) == "2026-11-26T06:03:12Z"


def test_evaluation_metadata_marks_layers_forced_by_the_switch(audio_env):
    """語音全域開關開著時，錄音紀錄的 settings 標出「（全局生效）」：分得出是開關蓋過了卡片自己的設定。"""
    from app.bot.handlers.voice import evaluation_metadata
    from app.schemas.speaking import RecordingItem

    with _rows(**{
        f"{TRILINGUAL}|audio_eval": RouteChoice("listen", "", None),
        f"{VOICE}|audio_eval": RouteChoice("stt_diff", "", None),
        f"{VOICE}|{routing.SWITCH_CAPABILITY}": RouteChoice(routing.SWITCH_ON, "", None),
    }):
        plan = _run(audio_factory.plan_for(TRILINGUAL))
    meta = evaluation_metadata(plan)
    assert meta["strategy"] == "stt_diff"
    assert meta["settings"] == {"audio_eval": f"{VOICE}（全局生效）", "stt": ".env（全局生效）"}
    # 寫進卡片的格式由 RecordingItem 定義：評分資訊原樣保留
    stored = RecordingItem.model_validate({"date": "2026-11-26", "score": 80, **meta}).model_dump()
    assert stored["settings"] == meta["settings"] and stored["strategy"] == "stt_diff"
