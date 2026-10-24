"""人脈卡「AI 分析與話題」的測試：遮名字、換回稱呼、LLM 呼叫與 TG 流程。

Tests for the person profile "AI insight and topics" flow.
"""

import json
from datetime import date
from unittest import mock

import pytest

from app.bot.handlers import commands, person_profile
from app.bot.utils.deep_link_parser import DeepLinkParser
from app.core.exceptions import LLMGenerationError, LLMQuotaError
from app.schemas.llm.person_profile import LLMPersonTopicsResult
from app.schemas.tg.deep_link import PersonLinkAction
from app.services import person_profile_topics as topics
from tests.test_bot_replies import NASTY, _assert_tg_html, _callback, _fsm, _message, _run, _sent
from tests.test_person_profile_bot import _anki, _card_service, _note, _note_service

TODAY = date(2026, 10, 24)

NAMES = {
    "Display_Name": "田中さん",
    "Name_ZH": "田中悠希",
    "Name_EN": "Yuki Tanaka",
    "Name_JA": "田中 悠希",
    "Name_JA_Kana": "たなか ゆうき",
    "Name_Romaji": "Tanaka Yūki",
}
FAMILY = [{"relation": "妻子", "name": "美咲", "note": "在醫院工作"}, {"relation": "貓", "name": "Momo"}, {"relation": "兒子"}]


def _fields(**values: str) -> dict[str, str]:
    return {**NAMES, "Company": "Geekly", **values}


def _lists(**values: list) -> dict[str, list]:
    return {"Family_JSON": FAMILY, **values}


def _result(text: str = "〔本人〕") -> dict:
    return {
        "insight": {
            "portrait": f"{text}做事細心。",
            "values": "重視家庭，常提到〔家人1〕。",
            "communication": "先聊近況。",
            "current_state": "準備 N1。",
            "cautions": "",
        },
        "topics": [{"title": "N1", "why": "下次要問", "opener": "〔本人〕さん、N1 の準備どう？"}],
    }


# =============================================================================
# 遮名字
# =============================================================================


class TestMask:
    def test_prompt_contains_no_names(self):
        prompt, mask = topics.build_prompt(
            _fields(Notes="上次跟田中先生、Yuki 和美咲一起吃飯", Work_Detail="tanaka 負責 IT"),
            _lists(Interactions_JSON=[{"date": "2026-10-01", "topics": "悠希的轉職", "quote": "ゆうき です"}]),
            TODAY,
        )
        for name in ("田中", "悠希", "ゆうき", "たなか", "Yuki", "yuki", "tanaka", "Tanaka", "美咲", "Momo"):
            assert name not in prompt, name
        assert "〔本人〕" in prompt and "〔家人1〕" in prompt and "〔家人2〕" in prompt
        assert "`〔家人1〕`：他的妻子" in prompt
        # 沒有名字的家人不需要代號，照樣列出關係
        assert "- 兒子" in prompt
        assert mask.restore == {"〔本人〕": "田中さん", "〔家人1〕": "美咲", "〔家人2〕": "Momo"}

    def test_excluded_fields_are_not_sent(self):
        prompt, _ = topics.build_prompt(
            _fields(Sensitive="離婚中", Appearance="黑框眼鏡", Name_Memo="悠閒", Introducer_Note_ID="123456789"),
            _lists(Contacts_JSON=[{"channel": "LINE", "handle": "tanaka_line_id"}]),
            TODAY,
        )
        for secret in ("離婚中", "黑框眼鏡", "123456789", "tanaka_line_id", "LINE："):
            assert secret not in prompt, secret
        assert "不想被主動提起的話題" in prompt

    def test_short_fragments_are_not_masked(self):
        """單一漢字、兩個字母的片段不遮：容易誤遮一般文字。"""
        mask = topics.build_mask({"Display_Name": "李さん", "Name_EN": "Bo Li"}, {})
        assert mask.mask("李さん和 Bo Li 去了 Li 公園") == "〔本人〕和 〔本人〕 去了 Li 公園"

    @pytest.mark.parametrize(
        ("display", "text", "expected"),
        [
            ("田中さん", "〔本人〕さん、元気？", "田中さん、元気？"),  # 稱呼已有敬稱：不重複
            ("田中さん", "跟〔本人〕聊", "跟田中さん聊"),
            ("Ken", "〔本人〕さん、元気？", "Kenさん、元気？"),  # 稱呼沒有敬稱：保留 LLM 加的
        ],
    )
    def test_unmask_honorifics(self, display, text, expected):
        mask = topics.build_mask({"Display_Name": display}, {})
        assert mask.unmask(text) == expected


# =============================================================================
# LLM 呼叫
# =============================================================================


def _route(parsed: dict):
    client = mock.MagicMock()
    client.generate_structured_data = mock.AsyncMock(
        return_value=mock.MagicMock(parsed_data=parsed, model_name="m1", raw_content="{}")
    )
    return mock.MagicMock(client=mock.AsyncMock(return_value=client)), client


class TestSuggest:
    def test_names_are_restored(self):
        route, client = _route(_result())
        with mock.patch.object(topics, "TOPICS_ROUTE", route):
            result = _run(topics.suggest(_fields(), _lists(), TODAY))
        assert result.insight.portrait == "田中さん做事細心。"
        assert result.insight.values == "重視家庭，常提到美咲。"
        assert result.topics[0].opener == "田中さん、N1 の準備どう？"
        sent = client.generate_structured_data.await_args.kwargs
        assert "田中" not in sent["user_prompt"]
        assert sent["response_schema"] == LLMPersonTopicsResult.model_json_schema()

    def test_malformed_reply(self):
        route, _ = _route({"topics": []})
        with mock.patch.object(topics, "TOPICS_ROUTE", route), pytest.raises(LLMGenerationError, match="m1"):
            _run(topics.suggest(_fields(), _lists(), TODAY))

    def test_route_is_a_branch_of_person_profile(self):
        assert topics.TOPICS_ROUTE.key == "person_profile.topics"
        assert topics.TOPICS_ROUTE.capabilities == {"llm"}


# =============================================================================
# TG
# =============================================================================


def _press(data: str, card_service):
    query = _callback(data)
    status = mock.MagicMock()
    status.edit_text = mock.AsyncMock()
    status.delete = mock.AsyncMock()
    query.message.answer = mock.AsyncMock(return_value=status)
    _run(person_profile.on_person_button(query, _fsm(), _note_service(), card_service))
    return query, status


class TestTopicsFlow:
    def test_summary_has_topics_button(self):
        buttons = [b.callback_data for row in person_profile.summary_keyboard(5).inline_keyboard for b in row]
        assert "pp:topic:5" in buttons

    def test_shows_insight_and_topics(self):
        card_service = _card_service(_note(Family_JSON=json.dumps(FAMILY, ensure_ascii=False)))
        parsed = LLMPersonTopicsResult.model_validate(_result(NASTY))
        with mock.patch.object(topics, "suggest", mock.AsyncMock(return_value=parsed)):
            _, status = _press("pp:topic:5", card_service)
        (text,) = _sent(status.edit_text)
        _assert_tg_html(text)
        assert NASTY not in text
        assert "<b>田中さん 是個怎樣的人</b>" in text and "<b>可以聊的話題</b>" in text
        assert "留意" not in text  # 空的不列
        buttons = [b.callback_data for row in status.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
                   for b in row]
        assert buttons == ["pp:topic:5", "pp:s:5"]

    def test_llm_failure_offers_retry(self):
        card_service = _card_service()
        with mock.patch.object(topics, "suggest", mock.AsyncMock(side_effect=LLMQuotaError("429 <x>"))):
            _, status = _press("pp:topic:5", card_service)
        (text,) = _sent(status.edit_text)
        _assert_tg_html(text)
        assert "額度" in text and "人脈卡：分析與想話題" in text and "再按" in text
        assert status.edit_text.await_args.kwargs["reply_markup"] is not None

    def test_card_link(self):
        action = DeepLinkParser.parse("ptopic-Display_Name-none-PP_20261024_123456_abcd")
        assert isinstance(action, PersonLinkAction) and action.verb == "topic"
        message, status = _message("/start ptopic-Display_Name-none-PP_1")
        parsed = LLMPersonTopicsResult.model_validate(_result())
        with mock.patch.object(topics, "suggest", mock.AsyncMock(return_value=parsed)) as suggest:
            _run(commands.command_start_handler(
                message, _anki(), mock.MagicMock(), _card_service(), state=_fsm(), note_service=_note_service()
            ))
        suggest.assert_awaited_once()
        assert "可以聊的話題" in _sent(status.edit_text)[0]
