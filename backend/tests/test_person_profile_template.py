"""人脈卡模板與後端的契約：常數一致、指紋等函式在 node 算出的結果與後端相同（登錄日計畫書 §4.3–§4.5；
工作經歷計畫書 §12：只有一種卡片，正面是完整資料，背面是同一份畫面）。

Contract tests between the person card template and the backend.

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
    HIDEABLE_TEXT_FIELDS,
    PERSON_HISTORY_FIELDS,
    active_texts,
    entry_text,
    is_active,
    item_fingerprint,
    position_period,
)

MODEL_DIR = Path("app/anki_models")
#: 正面：完整資料與所有 JS。The front, which holds the whole card and its script.
SOURCE = (MODEL_DIR / "Person_Profile_Dark_front.html").read_text(encoding="utf-8")
BACK = (MODEL_DIR / "Person_Profile_Dark_back.html").read_text(encoding="utf-8")


class TestSingleCard:
    def test_one_template_whose_back_wraps_the_front(self):
        definition = json.loads((MODEL_DIR / "Person_Profile_Dark.json").read_text(encoding="utf-8"))
        assert definition["cardTemplates"] == [
            {"Name": "人脈卡", "Front": "Person_Profile_Dark_front.html", "Back": "Person_Profile_Dark_back.html"}
        ]
        assert re.search(r'<div class="pp-back">\{\{FrontSide\}\}</div>', BACK)

    @pytest.mark.parametrize("source", [SOURCE, BACK], ids=["front", "back"])
    def test_comments_have_no_field_braces(self, source):
        """Anki 連 HTML 註解裡的雙大括號也會代換（背面註解寫了 FrontSide 會把整個正面塞進註解）。"""
        for comment in re.findall(r"<!--.*?-->", source, re.DOTALL):
            assert "{{" not in comment

    def test_back_side_does_not_swallow_clicks(self):
        """背面的遮擋由 CSS 拿掉；coverIfHidden 在 .pp-back 裡不能攔下第一次點擊。"""
        assert "onBackSide(node)) return;" in SOURCE
        css = (MODEL_DIR / "Person_Profile_Dark_style.css").read_text(encoding="utf-8")
        assert ".pp-back .is-hidden > .hidden-cover" in css

    def test_new_fields_are_on_the_card(self):
        definition = json.loads((MODEL_DIR / "Person_Profile_Dark.json").read_text(encoding="utf-8"))
        for name in ("Goals_JSON", "Common_Ground", "Needs", "Dislikes"):
            assert name in definition["inOrderFields"]
            assert f'<div id="f-{name}">{{{{{name}}}}}</div>' in SOURCE


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
        """長按文字欄位的選單（pfld）只指向 TG 認得的文字欄位（照片另有自己的選單）。"""
        fields = set(re.findall(r"longPressField\(.*?, '(\w+)'\)", SOURCE))
        fields |= set(re.findall(r"tgUrl\('pfld', '(\w+)'", SOURCE))
        assert {"Hometown", "Notes", "Met_Date", "Met_Where", "Birthday", "Display_Name", "Photo"} <= fields
        assert fields <= set(view.EDITABLE_FIELDS) | {"Photo"}

    def test_hideable_fields_match(self):
        """卡面會遮的文字欄位就是 TG 顯示隱藏開關的那些（HIDEABLE_TEXT_FIELDS）。"""
        fields = set(re.findall(r"longPressField\(.*?, '(\w+)'\)", SOURCE))
        fields |= set(re.findall(r"tgUrl\('pfld', '(\w+)'", SOURCE))
        assert fields == set(HIDEABLE_TEXT_FIELDS)

    def test_name_and_photo_are_hideable(self):
        """名字一個開關遮整個名字區（名字記法也跟著遮），沒有照片時首字頭像改成問號。"""
        assert "var nameHidden = hiddenFields.Display_Name === true;" in SOURCE
        assert "coverIfHidden(nameBox, nameHidden" in SOURCE
        assert "nameHidden && hiddenFields.Name_Memo !== true" in SOURCE
        assert "hiddenFields.Photo === true" in SOURCE
        assert "displayName && !maskInitial ? displayName.charAt(0) : '?'" in SOURCE

    def test_history_rows_cover_every_history_field(self):
        """每個會變的欄位都在卡片上有一列（Dietary 在見面前必看）。"""
        rows = set(re.findall(r"historyRow\((?:'[^']*'|null), '(\w+)'", SOURCE))
        # 職位由 careerRow（工作經歷）畫：要在區塊裡被呼叫，不是只有定義
        assert re.search(r"(?<!function )careerRow\(\)", SOURCE)
        assert rows | {"Dietary", "Company"} == set(PERSON_HISTORY_FIELDS)

    def test_hidden_tag_prefix_matches(self):
        """文字欄位的隱藏標籤前綴和後端相同。"""
        from app.schemas.person_profile import HIDDEN_TAG_PREFIX

        assert f"var HIDDEN_TAG_PREFIX = '{HIDDEN_TAG_PREFIX}';" in SOURCE

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
