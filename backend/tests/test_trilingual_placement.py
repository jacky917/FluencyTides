"""Tests for giving the LLM where a new trilingual card will sit (deck path, parent card, sibling titles).

給 LLM 新三語卡位置資訊（牌組路徑、上層卡、同一層標題）的測試。

Plan: ``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` D7.

計畫書：``docs/wip/trilingual_topic_tree_FEAT_2026-11-22.md`` D7。
"""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import ValidationError

from app.infrastructure.anki.client import AnkiConnectError
from app.schemas.llm.speaking_trilingual import GeneratedSaying, LLMTrilingualDraft
from app.services import speaking_trilingual_generate as generate_service
from app.services.speaking_trilingual_generate import DraftPlacement
from app.services.task_handlers.speaking_trilingual_handler import (
    SpeakingTrilingualDraftParams,
    SpeakingTrilingualHandler,
)

TRILINGUAL = "Speaking_Trilingual_Dark"
DECK = "日常會話::日本語面接::最終面接"


def _note(nid: int, title: str, prompt: str = "p", model: str = TRILINGUAL) -> SimpleNamespace:
    return SimpleNamespace(
        noteId=nid, modelName=model, cards=[nid * 10], fields={"Title": {"value": title}, "Prompt": {"value": prompt}}
    )


def _card_service(notes: list, decks: dict[int, str]):
    """A fake card service whose Anki holds these notes in these decks.

    假的卡片服務，Anki 裡的筆記放在指定的牌組。
    """
    by_id = {n.noteId: n for n in notes}

    async def notes_info(ids):
        return [by_id[i] for i in ids if i in by_id]

    async def cards_info(cards):
        return [{"cardId": c, "note": c // 10, "deckName": decks[c // 10]} for c in cards if c // 10 in decks]

    anki = SimpleNamespace(get_notes_info=notes_info, get_cards_info=cards_info)
    return SimpleNamespace(find_notes=mock.AsyncMock(return_value=list(by_id)), anki_client=anki)


def _context(service, **params):
    return asyncio.run(SpeakingTrilingualHandler().draft_context(service, {"deck": "", "parent_note_id": "", **params}))


NOTES = [
    _note(1, "転職理由", "面試官問你轉職理由"),
    _note(2, "強みと活かし方"),
    _note(3, "前の会社を2年で辞めた理由"),
]
DECKS = {1: DECK, 2: DECK, 3: f"{DECK}::転職理由"}


class TestDraftContext:
    def test_chosen_deck_gives_the_path_and_sibling_titles(self):
        context = _context(_card_service(NOTES, DECKS), deck=DECK)
        assert context["deck_path"] == DECK
        assert context["sibling_titles"] == ["転職理由", "強みと活かし方"]
        assert context["parent_title"] is None

    def test_parent_moves_the_card_into_its_title_deck(self):
        """選了上層卡：位置是它的同名子牌組，帶上它的標題與情境，同一層是那個牌組裡已有的卡。"""
        context = _context(_card_service(NOTES, DECKS), deck=DECK, parent_note_id="1")
        assert context["deck_path"] == f"{DECK}::転職理由"
        assert (context["parent_title"], context["parent_prompt"]) == ("転職理由", "面試官問你轉職理由")
        assert context["sibling_titles"] == ["前の会社を2年で辞めた理由"]

    def test_card_that_owns_the_chosen_deck_counts_as_the_parent(self):
        """沒選上層卡，但選的牌組本身是某張卡的同名子牌組：那張卡就是上層卡。"""
        context = _context(_card_service(NOTES, DECKS), deck=f"{DECK}::転職理由")
        assert (context["parent_title"], context["parent_prompt"]) == ("転職理由", "面試官問你轉職理由")

    @pytest.mark.parametrize("parent", ["999", "4"], ids=["missing", "no-title"])
    def test_unusable_parent_is_left_out(self, parent):
        """上層卡不存在或沒有標題：草稿照樣用選的牌組（建卡時才報錯）。"""
        notes = [*NOTES, _note(4, "")]
        context = _context(_card_service(notes, {**DECKS, 4: DECK}), deck=DECK, parent_note_id=parent)
        assert context["deck_path"] == DECK and context["parent_title"] is None

    def test_no_deck_no_context(self):
        assert _context(_card_service(NOTES, DECKS)) == {}


class TestDraftParams:
    def test_deck_and_parent_are_optional(self):
        params = SpeakingTrilingualDraftParams.model_validate({"background": "b"}).model_dump()
        assert (params["deck"], params["parent_note_id"]) == ("", "")

    def test_parent_must_be_a_note_id(self):
        with pytest.raises(ValidationError):
            SpeakingTrilingualDraftParams.model_validate({"background": "b", "parent_note_id": "abc"})

    def test_widgets_are_declared_for_the_web_form(self):
        props = SpeakingTrilingualDraftParams.model_json_schema()["properties"]
        assert (props["deck"]["x-ui"], props["parent_note_id"]["x-ui"]) == ("deck", "parent-card")


def _draft() -> LLMTrilingualDraft:
    saying = GeneratedSaying(content="a", note="", familiarity=5)
    return LLMTrilingualDraft(prompt="p", title="転職理由の深掘り", context="", zh=[saying], ja=[saying], en=[saying])


class TestExecuteDraft:
    def test_placement_reaches_the_llm_and_the_parent_is_carried_over(self):
        generate = mock.AsyncMock(return_value=_draft())
        context = {"deck_path": DECK, "parent_title": "転職理由", "parent_prompt": "x", "sibling_titles": ["a"],
                   "sibling_decks": []}
        params = {"background": "b", "intent": "", "audience": "不指定", "audience_note": "", "deck": DECK,
                  "parent_note_id": "1"}
        with mock.patch.object(generate_service, "generate", generate):
            result = asyncio.run(SpeakingTrilingualHandler().execute_draft(params, [], context))
        assert generate.await_args.args[4] == DraftPlacement(**context)
        assert result["parent_note_id"] == 1

    def test_without_context_no_placement(self):
        generate = mock.AsyncMock(return_value=_draft())
        params = {"background": "b", "intent": "", "audience": "不指定", "audience_note": "", "deck": "",
                  "parent_note_id": ""}
        with mock.patch.object(generate_service, "generate", generate):
            result = asyncio.run(SpeakingTrilingualHandler().execute_draft(params, [], {}))
        assert generate.await_args.args[4] is None
        assert result["parent_note_id"] is None


class TestPrompt:
    def test_placement_section(self):
        placement = DraftPlacement(
            deck_path="日常會話::日本語面接::Queen Bee Capital株式会社", parent_title="転職理由",
            parent_prompt="面試官問你轉職理由", sibling_titles=["強みと活かし方"], sibling_decks=["逆質問"],
        )
        prompt = generate_service.build_prompt("背景", "", None, [], placement)
        assert "日常會話 › 日本語面接 › Queen Bee Capital株式会社" in prompt
        assert "「転職理由」——面試官問你轉職理由" in prompt
        assert "強みと活かし方" in prompt and "逆質問" in prompt
        assert "以上面的場景為準" in prompt
        assert "只用場景、牌組路徑與上層卡裡有的資訊" in prompt

    @pytest.mark.parametrize("placement", [None, DraftPlacement()], ids=["none", "empty"])
    def test_no_placement_keeps_the_original_rule(self, placement):
        prompt = generate_service.build_prompt("背景", "", None, [], placement)
        assert "這張卡在牌組裡的位置" not in prompt
        assert "只用場景裡有的資訊" in prompt


def test_anki_failure_while_reading_the_context_still_generates():
    """讀情境時 Anki 連不上：照樣生成，只是不給位置資訊（與讀不到既有標籤相同）。"""
    from app.services.note_service import NoteService

    handler = SimpleNamespace(
        draft_context=mock.AsyncMock(side_effect=AnkiConnectError("down")),
        execute_draft=mock.AsyncMock(return_value={"title": "t", "tags": []}),
    )
    service = NoteService.__new__(NoteService)
    service.registry = SimpleNamespace(get_handler=lambda name: handler)
    service.card_service = SimpleNamespace()
    service.tags = mock.AsyncMock(return_value=[])
    result = asyncio.run(service.generate_draft("speaking_trilingual", {"background": "b"}))
    assert handler.execute_draft.await_args.args[2] == {}
    assert result["params"] == {"title": "t"}
