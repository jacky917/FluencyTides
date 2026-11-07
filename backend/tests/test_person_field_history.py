"""人脈卡會變的欄位：文字轉清單、舊資料轉換、共用的「今天」（docs/wip/person_field_history_FEAT_2026-11-07.md）。

Tests for the person card's history fields, the legacy conversion and the
shared "today".
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core import clock
from app.core.config import Settings, settings
from app.core.exceptions import AnkiFieldCorruptedError
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.schemas.person_profile import (
    PERSON_HISTORY_FIELDS,
    ClosenessEntry,
    HistoryEntry,
    HistoryField,
    Position,
    active_texts,
    entry_text,
    history_from_text,
    html_to_text,
    is_active,
    legacy_to_history,
)


def _is_list(raw: str) -> bool:
    try:
        AnkiJsonFieldManager.parse_field_string(raw)
    except AnkiFieldCorruptedError:
        return False
    return True


def _parsed(changes: dict[str, str], name: str) -> list:
    return AnkiJsonFieldManager.parse_field_string(changes[name])


def _convert(fields: dict[str, str]):
    return legacy_to_history(fields, _is_list)


class TestHistoryFromText:
    def test_split_fields(self):
        assert history_from_text("Interests", "登山, 攝影、 咖啡，\n露營", "2026-11-07") == [
            {"value": v, "added": "2026-11-07", "ended": ""} for v in ("登山", "攝影", "咖啡", "露營")
        ]

    def test_other_fields_are_one_entry(self):
        """D4：只有興趣、飲食喜好、語言切；其他整段一筆（多行保留）。"""
        assert history_from_text("Work_Detail", "後端、API\n帶新人", "") == [
            {"value": "後端、API\n帶新人", "added": "", "ended": ""}
        ]

    def test_position(self):
        assert history_from_text("Company", " Geekly ", "", title=" PM ") == [
            {"company": "Geekly", "title": "PM", "added": "", "ended": ""}
        ]
        assert history_from_text("Company", "", "", title="") == []

    def test_empty(self):
        assert history_from_text("Interests", " ,、 ", "2026-11-07") == []

    def test_duplicates_in_one_input_are_kept_once(self):
        assert [e["value"] for e in history_from_text("Interests", "登山、攝影、登山", "")] == ["登山", "攝影"]

    def test_single_value_fields_cannot_split(self):
        with pytest.raises(ValueError):
            HistoryField(HistoryEntry, "x", single=True, split=True)

    def test_split_flags(self):
        assert {name for name, spec in PERSON_HISTORY_FIELDS.items() if spec.split} == {
            "Interests", "Food_Drink", "Languages"
        }
        assert {name for name, spec in PERSON_HISTORY_FIELDS.items() if spec.single} == {
            "Residence", "Timezone", "Relationship", "Closeness"
        }


class TestEntries:
    def test_entry_text_and_active(self):
        items = [
            {"company": "Geekly", "title": "PM", "ended": ""},
            {"company": "舊公司", "title": "", "ended": "2025-01-01"},
            {"company": "", "title": "顧問"},
            "壞掉的",
        ]
        assert [entry_text("Company", i) for i in items] == ["Geekly・PM", "舊公司", "顧問", "壞掉的"]
        assert [is_active(i) for i in items] == [True, False, True, False]
        assert active_texts("Company", items) == ["Geekly・PM", "顧問"]

    def test_models(self):
        with pytest.raises(ValidationError):
            Position.model_validate({"company": "", "title": ""})
        with pytest.raises(ValidationError):
            ClosenessEntry.model_validate({"value": "0"})
        with pytest.raises(ValidationError):
            PERSON_HISTORY_FIELDS["Interests"].model.model_validate({"value": "x", "added": "11/07"})
        # 多餘的鍵保留
        assert PERSON_HISTORY_FIELDS["Interests"].model.model_validate({"value": "x", "note": 1}).model_dump()["note"] == 1


class TestHtmlToText:
    def test_breaks_and_entities(self):
        assert html_to_text("a<br>b &amp; c<div>d</div>&nbsp;") == "a\nb & cd"
        assert html_to_text("<ul><li>A</li><li>B</li></ul>") == "A\nB"
        assert html_to_text("") == ""


class TestLegacyConversion:
    def test_converts_text_fields(self):
        """D2：舊文字轉成清單，登錄日留空（日期不明），全部有效；職稱併進職位、Title 清空。"""
        fields = {
            "Interests": "登山、攝影<br>咖啡",
            "Work_Detail": "後端<br>帶新人",
            "Company": "Geekly",
            "Title": "PM",
            "Closeness": "4",
            "Residence": "",
            "Notes": "不是會變的欄位",
        }
        result = _convert(fields)
        changes = result.changes
        assert result.dropped == [] and result.skipped == []
        assert set(changes) == {"Interests", "Work_Detail", "Company", "Closeness", "Title"}
        assert [e["value"] for e in _parsed(changes, "Interests")] == ["登山", "攝影", "咖啡"]
        assert _parsed(changes, "Work_Detail") == [{"value": "後端\n帶新人", "added": "", "ended": ""}]
        assert _parsed(changes, "Company") == [{"company": "Geekly", "title": "PM", "added": "", "ended": ""}]
        assert changes["Title"] == ""

    def test_html_entities_round_trip(self):
        """HTML 轉成純文字、再以 JSON 存回（跳脫一次）：讀回來是原本的字。"""
        changes = _convert({"Praise": 'A &amp; B &lt;x&gt; "引號"'}).changes
        assert _parsed(changes, "Praise")[0]["value"] == 'A & B <x> "引號"'
        assert "&amp;amp;" not in changes["Praise"]

    def test_title_alone(self):
        changes = _convert({"Company": "", "Title": "社長"}).changes
        assert _parsed(changes, "Company")[0]["title"] == "社長" and changes["Title"] == ""

    def test_lists_are_left_alone(self):
        """已經轉過的欄位不動：可以重複執行。"""
        result = _convert({"Interests": "[]", "Company": '[{"company": "A"}]', "Title": "PM"})
        assert result.changes == {}
        # Company 已經是清單、Title 卻還有字：列出來請手動處理
        assert result.leftover_title == "PM"
        once = _convert({"Interests": "登山"}).changes
        assert _convert(once).changes == {}

    def test_blank_html_is_left_alone(self):
        """只有空標籤：讀起來就是空清單，不用轉。"""
        assert _convert({"Interests": "<br>"}).changes == {}

    def test_broken_json_is_not_wrapped_as_text(self):
        """看起來是壞掉的 JSON：不包成一筆文字（會蓋掉還能手動修的資料），列出來。"""
        result = _convert({"Interests": '[{"value": "登山"'})
        assert result.changes == {} and result.skipped == ["Interests"]

    def test_values_that_do_not_fit_the_model_are_dropped(self):
        """親密度寫「很熟」：不轉進去（之後網頁存這欄才不會整欄驗證失敗），列出來請手動補。"""
        result = _convert({"Closeness": "很熟"})
        assert result.changes == {"Closeness": ""} and result.dropped == [("Closeness", "很熟")]


class TestClock:
    def test_today_follows_the_setting(self, monkeypatch):
        for zone in ("Pacific/Kiritimati", "Etc/GMT+12"):
            monkeypatch.setattr(settings, "APP_TIMEZONE", zone)
            assert clock.now().tzinfo == ZoneInfo(zone)
            assert clock.today() == datetime.now(ZoneInfo(zone)).date()
            assert clock.today_iso() == clock.today().isoformat()

    def test_invalid_timezone_is_rejected_at_startup(self):
        with pytest.raises(ValidationError):
            Settings(APP_TIMEZONE="Mars/Olympus")
        assert Settings(APP_TIMEZONE=" Asia/Taipei ").APP_TIMEZONE == "Asia/Taipei"
