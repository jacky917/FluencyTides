"""人脈卡背面模板與後端的契約：常數一致、指紋等函式在 node 算出的結果與後端相同（登錄日計畫書 §4.3–§4.5）。

Contract tests between the person card's back template and the backend.

模板的 JS 自己寫死了欄位清單、區塊代號與指紋算法；改了後端卻沒改模板時，卡片上的「+」、「劃掉」與長按選單
（pitm／pfld）會在 TG 回「連結不支援」或「這一筆已經變了」，pytest 之外沒有東西會發現。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.bot.utils import person_profile_view as view
from app.schemas.person_profile import (
    PERSON_HISTORY_FIELDS,
    active_texts,
    entry_text,
    is_active,
    item_fingerprint,
    position_period,
)

BACK_TEMPLATE = Path("app/anki_models/Person_Profile_Dark_back.html")
SOURCE = BACK_TEMPLATE.read_text(encoding="utf-8")


class TestConstants:
    def test_history_fields_match(self):
        match = re.search(r"var HISTORY_FIELDS = \[(.*?)\];", SOURCE, re.DOTALL)
        assert match
        assert re.findall(r"'(\w+)'", match.group(1)) == list(PERSON_HISTORY_FIELDS)

    def test_section_codes_match(self):
        """區塊的「+」（psec）用的代號，就是 TG 認得的那些。"""
        assert set(re.findall(r"section: '(\w+)'", SOURCE)) == set(view.SECTION_FIELDS)

    def test_add_targets_are_known_lists(self):
        fields = set(re.findall(r"\{ field: '(\w+)' \}", SOURCE))
        assert fields and fields <= set(view.CODE_OF_LIST)

    def test_edit_targets_are_text_fields(self):
        """長按文字欄位的選單（pfld）只指向 TG「改資料」認得的文字欄位。"""
        fields = set(re.findall(r"longPressField\(.*?, '(\w+)'\)", SOURCE))
        assert {"Hometown", "Notes", "Met_Date", "Met_Where", "Birthday"} <= fields
        assert fields <= set(view.EDITABLE_FIELDS)

    def test_history_rows_cover_every_history_field(self):
        """每個會變的欄位都在卡片上有一列（Dietary 在見面前必看）。"""
        rows = set(re.findall(r"historyRow\('[^']*', '(\w+)'", SOURCE))
        # 職位由 careerRow（工作經歷）畫：要在區塊裡被呼叫，不是只有定義
        assert re.search(r"(?<!function )careerRow\(\)", SOURCE)
        assert rows | {"Dietary", "Company"} == set(PERSON_HISTORY_FIELDS)

    def test_hidden_tag_prefix_matches(self):
        """文字欄位的隱藏標籤：背面與認人正面的前綴都和後端相同。"""
        from app.schemas.person_profile import HIDDEN_TAG_PREFIX

        assert f"var HIDDEN_TAG_PREFIX = '{HIDDEN_TAG_PREFIX}';" in SOURCE
        recognize = Path("app/anki_models/Person_Profile_Dark_front_recognize.html").read_text(encoding="utf-8")
        assert f"'{HIDDEN_TAG_PREFIX}' + field" in recognize
        # 認人正面的文字線索各自能遮
        assert "['Met_Where', 'Met_Date', 'Appearance']" in recognize
        assert 'id="cue-Met_Date"' in recognize

    def test_birthday_badge_follows_the_hidden_tag(self):
        """生日設成隱藏時，頭部的徽章也要遮（否則看得出生日與年齡）。"""
        assert "hiddenFields.Birthday === true" in SOURCE


def _run_card_js(functions: list[str], script: str):
    """從卡片模板取出函式，用 node 執行 script，回傳它印出的 JSON。"""
    bodies = []
    for name in functions:
        match = re.search(rf"( {{4}}function {name}\(.*?\n {{4}}\}})", SOURCE, re.DOTALL)
        assert match, f"卡片模板裡找不到 {name}"
        bodies.append(match.group(1))
    completed = subprocess.run(
        ["node", "-e", "\n".join(bodies) + "\n" + script], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return json.loads(completed.stdout)


ITEMS = [
    {"value": "攝影", "added": "2026-11-01", "ended": ""},
    {"value": "登山 🏔️\n週末", "added": "", "ended": "2026-05-01"},
    {"company": "Geekly", "title": "エンジニア", "added": "", "ended": ""},
    {"company": "Sony", "added": "2026-01-01", "ended": ""},
    {"company": "", "title": None, "ended": "2025-01-01"},
    {"value": "4", "added": "", "ended": ""},
    # 原有六種清單的項目：指紋不能因為這次加鍵而改變
    {"added": "2026-10-01", "item": "介紹 Ken 給他", "type": "my_promise", "status": "open"},
    {"date": "2026-10-01", "topics": "轉職", "place": "中野"},
    # 工作經歷：有離職年月的是以前的工作；任職期間、說明、隱藏不進指紋
    {"company": "QBC", "title": "QA", "start": "2025", "end": "", "note": "x", "hidden": True, "added": "", "ended": ""},
    {"company": "Sony", "start": "2019-04", "end": "2021-03", "added": "", "ended": ""},
    {"company": "A", "end": "?", "ended": ""},
    {"company": "B", "ended": "2025-01-10"},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能執行卡片模板的 JS")
class TestCardScript:
    def test_fingerprint_matches_the_backend(self):
        result = _run_card_js(["itemFingerprint"], f"console.log(JSON.stringify({json.dumps(ITEMS)}.map(itemFingerprint)))")
        assert result == [item_fingerprint(item) for item in ITEMS]

    def test_entry_text_and_active_match_the_backend(self):
        names = ["Interests", "Interests", "Company", "Company", "Company", "Closeness", "Interests", "Interests",
                 "Company", "Company", "Company", "Company"]
        script = (
            f"var items = {json.dumps(ITEMS)}; var names = {json.dumps(names)};"
            "console.log(JSON.stringify(items.map(function (it, i) {"
            " return [entryText(names[i], it), isActiveEntry(it)]; })))"
        )
        result = _run_card_js(["isActiveEntry", "entryText"], script)
        assert result == [[entry_text(name, item), is_active(item)] for name, item in zip(names, ITEMS)]
        assert active_texts("Company", ITEMS[2:4]) == ["Geekly・エンジニア", "Sony"]

    def test_position_period_matches_the_backend(self):
        result = _run_card_js(["positionPeriod"], f"console.log(JSON.stringify({json.dumps(ITEMS)}.map(positionPeriod)))")
        assert result == [position_period(item) for item in ITEMS]
