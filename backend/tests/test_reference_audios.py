"""Tests that importer updates keep the audio attached to reference answers.

匯入腳本更新卡片時保留參考答案音檔的測試。
"""

import asyncio
import html
import json
from types import SimpleNamespace

import scripts.common.env  # noqa: F401  # 載入 .env，供 app.core.config 使用
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from scripts.local_anki.common.reference_audios import (
    merge_reference_audios,
    preserve_reference_audios,
)

AUDIO = {"audio": "tg_1.mp3", "speaker": "我", "avatar": "none"}


def _encoded(items: list) -> str:
    return html.escape(json.dumps(items, ensure_ascii=False))


class _Client:
    """A fake AnkiConnect client holding one note.

    只持有一張筆記的假 AnkiConnect 客戶端。
    """

    def __init__(self, fields: dict[str, str]) -> None:
        self.fields = {k: {"value": v} for k, v in fields.items()}

    async def get_notes_info(self, note_ids):
        return [SimpleNamespace(noteId=note_ids[0], fields=self.fields)]


def _run(fields_in_anki, update_fields, present, reference_fields=("References_JA",)):
    return asyncio.run(preserve_reference_audios(
        _Client(fields_in_anki), 1, update_fields, reference_fields, present, "test",
    ))


class TestMergeReferenceAudios:
    def test_audio_follows_matching_content(self):
        merged, lost = merge_reference_audios(
            [{"content": "おはよう", "audios": [AUDIO]}],
            [{"content": "おはよう", "note": "改過的說明"}, {"content": "新句子"}],
        )
        assert merged == [
            {"content": "おはよう", "note": "改過的說明", "audios": [AUDIO]},
            {"content": "新句子"},
        ]
        assert lost == []

    def test_answer_with_audio_missing_from_json_is_kept_at_the_end(self):
        merged, kept = merge_reference_audios(
            [{"content": "舊句", "audios": [AUDIO]}], [{"content": "新句"}]
        )
        assert merged == [{"content": "新句"}, {"content": "舊句", "audios": [AUDIO]}]
        assert kept == ["舊句"]

    def test_duplicate_sentence_gets_the_audio_once(self):
        merged, kept = merge_reference_audios(
            [{"content": "a", "audios": [AUDIO]}], [{"content": "a"}, {"content": "a", "note": "x"}]
        )
        assert merged == [{"content": "a", "audios": [AUDIO]}, {"content": "a", "note": "x"}]
        assert kept == []

    def test_same_file_is_not_duplicated(self):
        merged, _ = merge_reference_audios(
            [{"content": "a", "audios": [AUDIO]}], [{"content": "a", "audios": [AUDIO]}]
        )
        assert merged[0]["audios"] == [AUDIO]


class TestPreserveReferenceAudios:
    def test_existing_audio_is_merged_into_the_update(self):
        result = _run(
            {"References_JA": _encoded([{"content": "おはよう", "audios": [AUDIO]}])},
            {"Prompt": "p", "References_JA": _encoded([{"content": "おはよう", "status": 1}])},
            {"Prompt", "References_JA"},
        )
        written = AnkiJsonFieldManager.parse_field_string(result["References_JA"])
        assert written == [{"content": "おはよう", "status": 1, "audios": [AUDIO]}]
        assert result["Prompt"] == "p"

    def test_field_not_in_json_is_not_overwritten(self):
        """A language the JSON does not write keeps its Anki value.

        JSON 沒寫的語言保留 Anki 原值（以前會被補成 ``[]`` 覆寫）。
        """
        result = _run(
            {"References_EN": _encoded([{"content": "hi", "audios": [AUDIO]}])},
            {"Prompt": "p", "References_EN": "[]"},
            {"Prompt"},
            reference_fields=("References_EN",),
        )
        assert "References_EN" not in result

    def test_edited_sentence_keeps_audio_and_other_edits_go_through(self):
        """Fixing a typo in a recorded sentence must not block the rest of the update.

        改了帶音檔句子的錯字，不能擋住其餘的更新；舊句連同音檔保留在最後。
        """
        result = _run(
            {"References_JA": _encoded([{"content": "舊句", "audios": [AUDIO]}])},
            {"References_JA": _encoded([{"content": "改寫後的句子"}, {"content": "新增的句子"}])},
            {"References_JA"},
        )
        written = AnkiJsonFieldManager.parse_field_string(result["References_JA"])
        assert written == [
            {"content": "改寫後的句子"},
            {"content": "新增的句子"},
            {"content": "舊句", "audios": [AUDIO]},
        ]

    def test_corrupted_anki_value_is_not_overwritten(self):
        result = _run(
            {"References_JA": "[{broken"},
            {"References_JA": _encoded([{"content": "a"}])},
            {"References_JA"},
        )
        assert "References_JA" not in result
