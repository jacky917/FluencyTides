"""三語對話卡參考答案熟識度（0–9）的測試：判斷規則（後端與卡片 JS 同一張表）、匯入、評分提示詞、
stt_diff、stt_llm 與語音流程。

Tests for the familiarity (0–9) of trilingual reference answers.
"""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from app.bot.handlers import voice
from app.bot.state import UserState, UserStateManager
from app.core.dependencies import get_template_engine
from app.core.exceptions import FluencyTidesError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.infrastructure.audio_evaluator.stt_diff_evaluator import STTDiffEvaluator
from app.infrastructure.audio_evaluator.stt_llm_evaluator import STTLLMEvaluator
from app.services.task_handlers.shared.trilingual_familiarity import (
    familiarity_or_none,
    normalize_reference_familiarity,
    parse_familiarity,
)
from scripts.local_anki.common.card_identity import load_cards, read_identity
from tests.test_bot_replies import CHAT_ID, _anki, _message, _note_service, _run
from tests.test_card_identity import _FakeClient, _make_card, ic

BACK_TEMPLATE = Path("app/anki_models/Speaking_Trilingual_Dark_back.html")

#: 判斷規則的對照表：(值, 期望)。後端 familiarity_or_none 與卡片 readFamiliarity 都要符合。
#: (value, expected) — both the backend and the card JS must agree.
VECTORS = [
    (0, 0), (9, 9), (7.0, 7), ("7", 7), (" 3 ", 3),
    (None, None), ("", None), (" ", None),
    (10, None), (-1, None), (7.5, None), ("7.5", None), ("7.0", None), ("0x7", None),
    ("７", None), ("²", None), (True, None), (False, None), ([7], None), ("高", None),
]


# =============================================================================
# 判斷規則
# =============================================================================


class TestParse:
    @pytest.mark.parametrize(("value", "expected"), [(v, e) for v, e in VECTORS if e is not None or v in (None, "", " ")])
    def test_accepted_or_unrated(self, value, expected):
        assert parse_familiarity(value) == expected

    @pytest.mark.parametrize("value", [v for v, e in VECTORS if e is None and v not in (None, "", " ")])
    def test_invalid_values_name_the_familiarity(self, value):
        """不合格的值一律報「熟識度…」的錯（上標數字也不例外，不會冒出 int() 的訊息）。"""
        with pytest.raises(ValueError, match="^熟識度"):
            parse_familiarity(value)

    @pytest.mark.parametrize(("value", "expected"), VECTORS)
    def test_lenient_read_matches_the_table(self, value, expected):
        assert familiarity_or_none({"familiarity": value}) == expected

    def test_lenient_read_of_missing_or_non_dict(self):
        assert familiarity_or_none({}) is None
        assert familiarity_or_none("not a dict") is None


def _run_card_js(functions: list[str], script: str):
    """從卡片模板取出函式，用 node 執行 script，回傳它印出的 JSON。"""
    source = BACK_TEMPLATE.read_text(encoding="utf-8")
    bodies = []
    for name in functions:
        match = re.search(rf"( {{8}}function {name}\(.*?\n {{8}}\}})", source, re.DOTALL)
        assert match, f"卡片模板裡找不到 {name}"
        bodies.append(match.group(1))
    completed = subprocess.run(
        ["node", "-e", "\n".join(bodies) + "\n" + script], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return json.loads(completed.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能執行卡片模板的 JS")
class TestCardScript:
    def test_read_familiarity_matches_the_backend(self):
        values = json.dumps([v for v, _ in VECTORS])
        result = _run_card_js(
            ["readFamiliarity"],
            f"console.log(JSON.stringify({values}.map(function (v) {{ return readFamiliarity({{familiarity: v}}); }})))",
        )
        assert result == [expected for _, expected in VECTORS]

    def test_order_keeps_original_indexes(self):
        """依熟識度由低到高、沒填的排最後、同熟識度維持原順序；回傳的是原本的序號。"""
        refs = json.dumps([{}, {"familiarity": 9}, {"familiarity": 2}, {"familiarity": 7}, {"familiarity": 0}, {"familiarity": 2}])
        result = _run_card_js(["readFamiliarity", "familiarityOrder"], f"console.log(JSON.stringify(familiarityOrder({refs})))")
        assert result == [4, 2, 5, 3, 1, 0]


# =============================================================================
# 匯入前的正規化
# =============================================================================


class TestNormalize:
    def test_digit_string_becomes_int_and_blank_is_removed(self):
        fields = {"References_ZH": [{"content": "a", "familiarity": "7"}, {"content": "b", "familiarity": ""}, "壞資料"]}
        normalize_reference_familiarity(fields)
        assert fields["References_ZH"] == [{"content": "a", "familiarity": 7}, {"content": "b"}, "壞資料"]

    def test_json_string_without_changes_is_left_untouched(self):
        """參考答案的內文（<br>、HTML 實體、換行）不能被這個檢查動到。"""
        raw = json.dumps([{"content": "x<br>y &amp; z\n下一行", "familiarity": 3}], ensure_ascii=False)
        fields = {"References_JA": raw, "References_EN": "", "Prompt": "不動"}
        normalize_reference_familiarity(fields)
        assert fields == {"References_JA": raw, "References_EN": "", "Prompt": "不動"}

    def test_json_string_with_changes_keeps_the_content(self):
        fields = {"References_JA": json.dumps([{"content": "x<br>y &amp; z", "familiarity": "8"}])}
        normalize_reference_familiarity(fields)
        assert fields["References_JA"] == [{"content": "x<br>y &amp; z", "familiarity": 8}]

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ({"content": "a", "familiarity": 99}, "必須是 JSON 陣列"),
            (5, "必須是 JSON 陣列"),
            ('{"content": "a"}', "必須是 JSON 陣列"),
            ("[{壞掉", "不是合法的 JSON"),
            ([{"content": "a"}, {"content": "b", "familiarity": 12}], "References_JA 第 2 句：熟識度"),
        ],
    )
    def test_invalid_fields(self, value, message):
        with pytest.raises(ValueError, match=message):
            normalize_reference_familiarity({"References_JA": value})


class TestImport:
    """匯入腳本：不合格的卡跳過、字串數字轉成整數、磁碟上的 JSON 檔不被正規化改到。"""

    def _import(self, cards):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            path.write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
            client = _FakeClient()
            stats = asyncio.run(ic.import_cards(path, dry_run=False, client=client))
            return stats, client, load_cards(path)

    def test_bad_card_is_skipped_and_good_card_is_normalized(self):
        bad, good = _make_card(), _make_card()
        bad["fields"]["References_JA"] = [{"content": "a", "familiarity": 12}]
        good["fields"]["References_JA"] = [{"content": "b", "familiarity": "7"}, {"content": "c", "familiarity": ""}]
        stats, client, on_disk = self._import([bad, good])

        assert (stats["failed"], stats["created"]) == (1, 1)
        (note,) = client.added
        stored = AnkiJsonFieldManager.parse_field_string(note.fields["References_JA"])
        assert stored == [{"content": "b", "familiarity": 7}, {"content": "c"}]
        # 跳過的卡沒有寫回身分；JSON 檔裡使用者寫的原值保持不變
        assert read_identity(on_disk[0]) == (None, None)
        assert on_disk[1]["fields"]["References_JA"] == [{"content": "b", "familiarity": "7"}, {"content": "c", "familiarity": ""}]

    def test_string_field_content_reaches_anki_unchanged(self):
        card = _make_card()
        raw = json.dumps([{"content": "x<br>y &amp; z", "familiarity": 2}], ensure_ascii=False)
        card["fields"]["References_JA"] = raw
        _, client, _ = self._import([card])
        assert client.added[0].fields["References_JA"] == raw


# =============================================================================
# 評分提示詞
# =============================================================================

LABEL = {"ZH": "熟識度", "JA": "親しさ", "EN": "Familiarity"}


def _render(lang, **extra):
    return get_template_engine().render(
        f"prompts/anki/Speaking_Trilingual_{lang}.j2",
        prompt_text="P",
        context_text="C",
        reference_answers=["正式", "隨意", "未標"],
        target_language=None,
        disable_markdown=False,
        **extra,
    )


def _reference_lines(prompt):
    return [line.strip() for line in prompt.split("\n") if re.match(r"\s+\d\. ", line)]


@pytest.mark.parametrize("lang", ["ZH", "JA", "EN"])
def test_prompt_labels_each_reference(lang):
    prompt = _render(lang, reference_familiarity=[2, 8, None])
    assert _reference_lines(prompt) == [f"1. 【{LABEL[lang]} 2】正式", f"2. 【{LABEL[lang]} 8】隨意", "3. 未標"]
    assert "0–9" in prompt or "0〜9" in prompt


@pytest.mark.parametrize("lang", ["ZH", "JA", "EN"])
def test_prompt_shows_familiarity_zero(lang):
    """0 是最正式的一級；寫成 {% if fam %} 時會悄悄消失。"""
    prompt = _render(lang, reference_familiarity=[0, None, None])
    assert _reference_lines(prompt)[0] == f"1. 【{LABEL[lang]} 0】正式"
    assert "0–9" in prompt or "0〜9" in prompt


@pytest.mark.parametrize("lang", ["ZH", "JA", "EN"])
def test_prompt_without_familiarity(lang):
    """沒傳、全部未標或比參考答案短時都能渲染；沒有熟識度時不出現數字與刻度說明。"""
    for extra in ({}, {"reference_familiarity": [None, None, None]}, {"reference_familiarity": []}):
        prompt = _render(lang, **extra)
        assert _reference_lines(prompt) == ["1. 正式", "2. 隨意", "3. 未標"]
        assert "0–9" not in prompt and "0〜9" not in prompt
    short = _render(lang, reference_familiarity=[5])
    assert _reference_lines(short)[1:] == ["2. 隨意", "3. 未標"]


# =============================================================================
# 評分器
# =============================================================================


def _stt_diff(transcript, refs, familiarity):
    whisper = mock.MagicMock(transcribe=mock.AsyncMock(return_value=transcript))
    return asyncio.run(STTDiffEvaluator(whisper).evaluate_audio(
        audio_data=b"x", audio_filename="a.ogg", prompt_text="", context_text="",
        reference_answers=refs, target_language="ja-JP", reference_familiarity=familiarity,
    ))


@pytest.mark.parametrize(("refs", "familiarity", "expected"), [
    (["週末はどちらかへお出かけになりましたか", "週末どっか行った？"], [1, 8], 8),
    (["週末どっか行った？", "全然ちがう文"], [0, 9], 0),
])
def test_stt_diff_names_the_matched_familiarity(refs, familiarity, expected):
    result = _stt_diff("週末どっか行った", refs, familiarity)
    assert result.feedback.startswith(f"比對對象：熟識度 {expected} 的說法\n")
    assert result.feedback_anki_html.startswith(f"比對對象：熟識度 {expected} 的說法<br>")


def test_stt_diff_without_familiarity_is_unchanged():
    plain = _stt_diff("週末どっか行った", ["週末どっか行った？"], None)
    unrated = _stt_diff("週末どっか行った", ["週末どっか行った？"], [None])
    short = _stt_diff("週末どっか行った", ["別の文", "週末どっか行った？"], [5])
    assert "熟識度" not in plain.feedback and plain.feedback == unrated.feedback
    assert "熟識度" not in short.feedback


def test_stt_llm_puts_familiarity_into_the_prompt():
    whisper = mock.MagicMock(model_name="w", transcribe=mock.AsyncMock(return_value="週末どっか行った"))
    llm = mock.MagicMock(model_name="m", model_label="m")
    llm.generate_structured_data = mock.AsyncMock(return_value=mock.MagicMock(
        parsed_data={"score": 80, "feedback": "ok", "transcript": "t"}, raw_content="{}",
    ))
    asyncio.run(STTLLMEvaluator(whisper, llm).evaluate_audio(
        b"x", "a.ogg", "P", "C", ["週末どっか行った？"],
        template_name="prompts/anki/Speaking_Trilingual_JA.j2", reference_familiarity=[8],
    ))
    assert "【親しさ 8】週末どっか行った？" in llm.generate_structured_data.await_args.kwargs["system_prompt"]


# =============================================================================
# 語音流程：熟識度跟著啟用中的參考答案一起交給評分器
# =============================================================================


def _voice_with_references(references_value):
    note = mock.MagicMock(noteId=1, fields={"References_JA": {"value": references_value}})
    manager = UserStateManager()
    manager.set_state(CHAT_ID, UserState(action="recording", card_id="ST_1", extra={"field_name": "Recordings_JA"}))
    evaluator = mock.MagicMock()
    evaluator.evaluate_audio = mock.AsyncMock(side_effect=FluencyTidesError("stop here"))
    message, _ = _message()
    message.voice = mock.MagicMock(file_id="f")
    message.bot = mock.MagicMock()
    message.bot.get_file = mock.AsyncMock(return_value=mock.MagicMock(file_path="p"))
    message.bot.download_file = mock.AsyncMock()
    lookup = mock.AsyncMock(return_value=(evaluator, mock.MagicMock(strategy_name="listen")))
    with mock.patch.object(voice.recording_eval, "evaluator_for_handler", lookup):
        _run(voice.process_voice_handler(message, _anki(get_notes_info=mock.AsyncMock(return_value=[note])),
                                         _note_service(), manager))
    return evaluator.evaluate_audio.await_args.kwargs


def test_voice_passes_familiarity_aligned_with_active_references(caplog):
    references = [
        {"content": "停用的", "status": 0, "familiarity": 5},
        {"content": "正式", "status": 1, "familiarity": 1},
        {"content": "沒標", "status": 1},
        {"content": "隨意", "status": 1, "familiarity": "8"},
        {"content": "改壞的", "status": 1, "familiarity": "八"},
    ]
    kwargs = _voice_with_references(json.dumps(references, ensure_ascii=False))
    assert kwargs["reference_answers"] == ["正式", "沒標", "隨意", "改壞的"]
    assert kwargs["reference_familiarity"] == [1, None, 8, None]
    assert kwargs["template_name"] == "prompts/anki/Speaking_Trilingual_JA.j2"
    # 不合格的值當成未標，但留下紀錄（指出卡片與第幾句）
    assert any("ST_1" in r.getMessage() and "第 4 句" in r.getMessage() for r in caplog.records)


def test_voice_with_corrupted_references_still_scores(caplog):
    kwargs = _voice_with_references("[{壞掉")
    assert (kwargs["reference_answers"], kwargs["reference_familiarity"]) == ([], [])
    assert any("ST_1" in r.getMessage() and "References_JA" in r.getMessage() for r in caplog.records)
