"""錄音評分的宣告與紀錄：handler 的錄音目標、來源標籤、卡面的評分資訊（recording_eval_settings 計畫書 D4、D5）。

Tests for what handlers declare about recording evaluation, the source labels, and
the evaluation info shown on the card faces.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.infrastructure.llm import routing
from app.infrastructure.llm.routing import ResolvedRoute, RouteChoice, source_label
from app.services.task_handlers.speaking_coach_handler import SpeakingCoachHandler
from app.services.task_handlers.speaking_trilingual_handler import SpeakingTrilingualHandler

ANKI_MODELS = Path(__file__).resolve().parents[1] / "app" / "anki_models"
TEMPLATES = [ANKI_MODELS / "Speaking_Coach_Dark_back.html", ANKI_MODELS / "Speaking_Trilingual_Dark_back.html"]


def _coach(field_name, fields=None):
    return SpeakingCoachHandler.RECORDING_EVAL.target_for(field_name, fields or {})


def _trilingual(field_name):
    return SpeakingTrilingualHandler.RECORDING_EVAL.target_for(field_name, {})


# ----------------------------------------------------------------------
# handler 宣告的錄音目標（D4）：和改之前 voice.py 寫死的行為相同
# ----------------------------------------------------------------------


def test_coach_target_matches_the_old_behaviour():
    target = _coach("Recordings", {"Target_Language": {"value": " ja-JP "}})
    assert (target.template, target.reference_field, target.target_language) == (
        "prompts/audio_evaluator.j2", "References", "ja-JP",
    )
    assert target.action_params == {}
    assert _coach("Recordings", {"Target_Language": {"value": "  "}}).target_language is None
    assert _coach("Recordings").target_language is None


@pytest.mark.parametrize("lang, locale", [("ZH", "zh-TW"), ("JA", "ja-JP"), ("EN", "en-US")])
def test_trilingual_target_follows_the_field_suffix(lang, locale):
    target = _trilingual(f"Recordings_{lang}")
    assert (target.template, target.reference_field, target.target_language) == (
        f"prompts/anki/Speaking_Trilingual_{lang}.j2", f"References_{lang}", locale,
    )
    assert target.action_params == {"lang": lang}


@pytest.mark.parametrize("field_name", ["Recordings_JA", "References", "Prompt_Audios", ""])
def test_coach_rejects_other_fields(field_name):
    assert _coach(field_name) is None


@pytest.mark.parametrize("field_name", ["Recordings", "References_JA", "Recordings_XX", "Prompt_Audios"])
def test_trilingual_rejects_other_fields(field_name):
    """不再看欄位名猜：只有 Recordings_<語言> 能錄音。"""
    assert _trilingual(field_name) is None


# ----------------------------------------------------------------------
# 來源標籤（TG 與錯誤訊息用；網頁的 sourceLabel 依同一套規則標出全域與全局生效，但措辭不同）
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "resolved, label",
    [
        (ResolvedRoute("k", "route", RouteChoice("audio", "m", None)), "用途"),
        (ResolvedRoute("k", "global", RouteChoice("audio", "m", None), global_key="*"), "全域（文字）"),
        (ResolvedRoute("k", "global", RouteChoice("audio", "m", None), global_key="*voice"), "全域（語音）"),
        (
            ResolvedRoute("k", "global", RouteChoice("audio", "m", None), global_key="*voice", forced=True),
            "全域（語音，全局生效）",
        ),
        (ResolvedRoute("k", "env", None), ".env"),
        (ResolvedRoute("k", "env", None, global_key="*voice", forced=True), ".env（全局生效）"),
    ],
)
def test_source_label(resolved, label):
    assert source_label(resolved) == label


def test_unrecognised_switch_value_counts_as_off(caplog):
    choices = {routing.SWITCH_CAPABILITY: {"*voice": RouteChoice("yes", "", None)}}
    routing.invalidate_cache()
    assert routing.switch_enabled(choices, "*voice") is False
    assert "無法辨識" in caplog.text


# ----------------------------------------------------------------------
# 卡面的評分資訊（D5）
# ----------------------------------------------------------------------


def _function(template: Path, name: str) -> str:
    match = re.search(rf"( {{8}}function {name}\(.*?\n {{8}}\}})", template.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{template.name} 裡找不到 {name}"
    return match.group(1)


def test_both_templates_share_the_same_meta_function():
    """兩份卡面的 recEvalMeta 是同一份（改一份忘了另一份時，這裡會擋下）。"""
    first, second = (_function(template, "recEvalMeta") for template in TEMPLATES)
    assert first == second


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能執行卡片模板的 JS")
def test_meta_line_on_the_card():
    records = [
        {"date": "2026-11-20", "score": 80},  # 舊紀錄：不顯示
        {
            "strategy": "stt_llm",
            "models": {"stt": "(whisper)Systran/faster-whisper-small", "llm": "(audio)gemini-2.5-flash"},
            "evaluated_at": "2026-11-26T06:03:12Z",
        },
        {"strategy": "listen", "models": {"audio_llm": "(audio)gemini-3.5-flash"}, "evaluated_at": "not a time"},
    ]
    script = f"console.log(JSON.stringify({json.dumps(records)}.map(recEvalMeta)))"
    completed = subprocess.run(
        ["node", "-e", _function(TEMPLATES[1], "recEvalMeta") + "\n" + script],
        capture_output=True, text=True, encoding="utf-8", check=True, env={"TZ": "UTC", **_path_env()},
    )
    assert json.loads(completed.stdout) == [
        "",
        "stt_llm · Systran/faster-whisper-small · gemini-2.5-flash · 11/26 06:03",
        "listen · gemini-3.5-flash",
    ]


def _path_env() -> dict[str, str]:
    import os

    return {key: value for key, value in os.environ.items() if key != "TZ"}


# ----------------------------------------------------------------------
# 所有宣告 RECORDING_EVAL 的 handler 的契約（新增卡片類型時自動受檢）
# ----------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"


def _recording_handlers():
    import app.main  # noqa: F401 - 匯入時註冊所有 handler
    from app.services.recording_eval import RECORDING_CAPABILITIES, ROUTE_KEY_PREFIX
    from app.services.task_handlers.registry import handler_registry

    handlers = [handler for handler in handler_registry.all_handlers() if handler.RECORDING_EVAL is not None]
    assert handlers, "至少要有一種卡片能錄音評分"
    return handlers, RECORDING_CAPABILITIES, ROUTE_KEY_PREFIX


def test_every_recording_declaration_fits_its_handler_and_note_type():
    """每個宣告都：用語音用途鍵 recording_eval.<handler 名>、handler 支援 add_recording；筆記類型上每個
    Recordings* 欄位都能錄音，參考答案欄位在筆記上，提示詞模板檔存在。"""
    handlers, capabilities, prefix = _recording_handlers()
    for handler in handlers:
        spec = handler.RECORDING_EVAL
        name = handler.handler_name
        assert spec.route.voice, name
        assert spec.route.key == f"{prefix}.{name}", name
        assert spec.route.capabilities == capabilities, name
        assert "add_recording" in handler.CAPABILITIES.actions, name
        for note_type in handler.supported_models:
            definition = json.loads((ANKI_MODELS / f"{note_type}.json").read_text(encoding="utf-8"))
            fields = definition["inOrderFields"]
            recording_fields = [field for field in fields if field.startswith("Recordings")]
            assert recording_fields, (name, note_type)
            for field in recording_fields:
                target = spec.target_for(field, {})
                assert target is not None, (name, field)
                assert target.reference_field in fields, (name, field, target.reference_field)
                assert (TEMPLATES_DIR / target.template).is_file(), (name, field, target.template)


def test_declaration_errors_fail_at_import_time():
    """宣告錯誤在建構時就失敗：語音能力漏標 voice、非語音的用途鍵、空的模板或欄位、action_params 佔用 recording。"""
    from app.services.task_handlers.shared.recording_spec import RecordingEvaluation, RecordingTarget

    with pytest.raises(ValueError, match="voice=True"):
        routing.Route("test.no_voice", "d", frozenset({"audio_eval", "llm"}))
    text_route = routing.Route("test.text_only", "d", frozenset({"llm"}))
    with pytest.raises(ValueError, match="voice=True"):
        RecordingEvaluation(text_route, lambda field, fields: None)
    with pytest.raises(ValueError):
        RecordingTarget(template="", reference_field="References")
    with pytest.raises(ValueError):
        RecordingTarget(template="prompts/audio_evaluator.j2", reference_field=" ")
    with pytest.raises(ValueError, match="recording"):
        RecordingTarget(template="prompts/audio_evaluator.j2", reference_field="References",
                        action_params={"recording": {}})


def test_resolved_route_rejects_inconsistent_global_and_forced():
    """ResolvedRoute：global_key 只能是兩個全域；forced 不會和 source=route 或全域鍵本身一起出現。"""
    choice = RouteChoice("yinli", "m", None)
    with pytest.raises(ValueError):
        ResolvedRoute("k", "route", choice, global_key="bogus")
    with pytest.raises(ValueError):
        ResolvedRoute("k", "route", choice, forced=True)
    with pytest.raises(ValueError):
        ResolvedRoute("*voice", "global", choice, global_key="*voice", forced=True)
    assert ResolvedRoute("k", "global", choice, global_key="*voice", forced=True).forced
