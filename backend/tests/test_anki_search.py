"""Anki 搜尋跳脫的測試（設計書 relation_sync_and_search_escape_FIX_2026-10-19.md §4）。

Tests for escaping values placed into Anki search queries. The rules were
verified against the real Anki with read-only queries (design doc §4.1).
"""

import pytest

from app.infrastructure.anki.search import (
    deck_term,
    escape_search_value,
    field_term,
    note_type_term,
    note_types_query,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ('say "hi"', 'say \\"hi\\"'),
        ("a_b*c", "a\\_b\\*c"),
        ("a:b", "a\\:b"),
        # 反斜線先處理：跳脫用的反斜線不會再被跳脫一次
        ('\\"', '\\\\\\"'),
        ("", ""),
    ],
)
def test_escape_search_value(value, expected):
    assert escape_search_value(value) == expected


def test_field_term_keeps_newlines_and_html():
    """值裡的換行、HTML 照字面比對，不需處理（正式 Anki 已驗證）。"""
    value = "第一行\n<b>粗體</b> & 其他"
    assert field_term("My_Original", value) == f'"My_Original:{value}"'


def test_field_term_escapes_colons():
    """冒號要跳脫：否則值以 re: / nc: 開頭時會被當成正規表示式或忽略重音搜尋（正式 Anki 已驗證）。"""
    assert field_term("My_Original", "時間: 10:30") == '"My_Original:時間\\: 10\\:30"'
    assert field_term("My_Original", "re:.*") == '"My_Original:re\\:.\\*"'


def test_field_term_escapes_specials():
    assert field_term("Card_ID", "SC_2026*") == '"Card_ID:SC\\_2026\\*"'


def test_deck_and_note_type_terms():
    # 跳脫後的 :: 照常比對子牌組（正式 Anki 已驗證筆數相同）
    assert deck_term("日本語::外語糾錯") == 'deck:"日本語\\:\\:外語糾錯"'
    assert deck_term("A_B") == 'deck:"A\\_B"'
    assert note_type_term("Speaking_Coach_Dark") == 'note:"Speaking\\_Coach\\_Dark"'


def test_note_types_query_with_and_without_deck():
    assert note_types_query(["A", "B_C"]) == '(note:"A" OR note:"B\\_C")'
    assert note_types_query(["A"], "日本語") == 'deck:"日本語" (note:"A")'
    assert note_types_query(["A"], "") == '(note:"A")'


def test_duplicate_location_lookup_matches_note_type_and_first_field():
    """重複位置反查：以 Anki 的重複條件（同筆記類型、第一個欄位）完全比對；反查失敗回 None，保留原錯誤。"""
    import asyncio
    from unittest import mock

    from app.infrastructure.anki.client import AnkiConnectError
    from app.schemas.anki import AnkiNote
    from app.services.anki_model_manager import AnkiModelManager

    # 第一個欄位以模型定義為準，不看 note.fields 的順序（可能跟著 LLM 回應走）
    client = mock.MagicMock(
        find_notes=mock.AsyncMock(return_value=[]),
        get_model_field_names=mock.AsyncMock(return_value=["Front", "Back"]),
    )
    manager = AnkiModelManager(client)
    note = AnkiNote(deckName="D", modelName="X_Y", fields={"Back": "z", "Front": 'a"b:c'})
    assert asyncio.run(manager._lookup_duplicate_location(note)) is None
    assert client.find_notes.await_args.args[0] == 'note:"X\\_Y" "Front:a\\"b\\:c"'

    client.find_notes = mock.AsyncMock(side_effect=AnkiConnectError("down"))
    assert asyncio.run(manager._lookup_duplicate_location(note)) is None
