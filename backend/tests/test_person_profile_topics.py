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
from app.schemas.person_profile import TEXT_UNMASKED_TAG
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
    def test_prompt_contains_no_registered_names(self):
        prompt, mask = topics.build_prompt(
            _fields(Notes="上次跟田中先生、Yuki Tanaka 和美咲一起吃飯，Momo 也在", Work_Detail="田中悠希負責 IT"),
            _lists(Interactions_JSON=[{"date": "2026-10-01", "topics": "田中 悠希的轉職", "quote": "たなか ゆうき です"}]),
            TODAY,
        )
        for name in ("田中", "悠希", "たなか ゆうき", "Yuki Tanaka", "Tanaka Yūki", "美咲", "Momo"):
            assert name not in prompt, name
        assert "〔本人〕" in prompt and "〔家人1〕" in prompt and "〔家人2〕" in prompt
        assert "`〔家人1〕`：他的妻子" in prompt
        # 沒有名字的家人不需要代號，照樣列出關係
        assert "- 兒子" in prompt
        assert mask.restore == {"〔本人〕": "田中さん", "〔家人1〕": "美咲", "〔家人2〕": "Momo"}

    def test_unmasked_text_keeps_names_but_not_name_fields(self):
        """關掉內文遮名字：內文照原文（ゆうき不會被換掉），姓名欄位照樣不送。"""
        prompt, mask = topics.build_prompt(
            _fields(Notes="ゆうきを出して転職した"), _lists(), TODAY, mask_text=False
        )
        assert "ゆうきを出して転職した" in prompt and "美咲" in prompt
        assert "Yuki Tanaka" not in prompt and "Tanaka Yūki" not in prompt
        assert "內文沒有遮名字" in prompt and "`〔家人1〕`" not in prompt
        # 回來的代號照樣換回稱呼
        assert mask.unmask("〔本人〕さん") == "田中さん"

    def test_excluded_fields_are_not_sent(self):
        prompt, _ = topics.build_prompt(
            _fields(Sensitive="離婚中", Appearance="黑框眼鏡", Name_Memo="悠閒", Introducer_Note_ID="123456789"),
            _lists(Contacts_JSON=[{"channel": "LINE", "handle": "tanaka_line_id"}]),
            TODAY,
        )
        for secret in ("離婚中", "黑框眼鏡", "123456789", "tanaka_line_id", "LINE："):
            assert secret not in prompt, secret
        assert "不想被主動提起的話題" in prompt

    def test_whole_values_even_short(self):
        """完整值再短也遮；稱呼去掉敬稱的部分也遮。"""
        mask = topics.build_mask({"Display_Name": "李さん", "Name_EN": "Bo Li"}, {"Family_JSON": [{"relation": "貓", "name": "Mo"}]})
        assert mask.mask("李さん、李和 Bo Li 帶 Mo 去公園") == "〔本人〕、〔本人〕和 〔本人〕 帶 〔家人1〕 去公園"

    def test_given_name_alone_in_any_kana(self):
        """只寫名字也遮：漢字、平假名、片假名、英文、羅馬拼音；姓不另外拆出來遮。"""
        mask = topics.build_mask(NAMES, {"Family_JSON": [{"relation": "妻子", "name": "みさき"}]})
        assert mask.mask("悠希、ゆうき、ユウキ、Yuki、Yūki") == "〔本人〕、〔本人〕、〔本人〕、〔本人〕、〔本人〕"
        assert mask.mask("ミサキとたなか") == "〔家人1〕とたなか"

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
        assert buttons == ["pp:topic:5", "pp:s:5", "pp:mask:5:off"]

    def test_tag_turns_off_text_masking(self):
        note = _note()
        note["tags"] = ["PersonProfile", TEXT_UNMASKED_TAG]
        parsed = LLMPersonTopicsResult.model_validate(_result())
        with mock.patch.object(topics, "suggest", mock.AsyncMock(return_value=parsed)) as suggest:
            _, status = _press("pp:topic:5", _card_service(note))
        assert suggest.await_args.kwargs == {"mask_text": False}
        labels = [b.text for row in status.edit_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert "內文名字：不遮（按此改成遮）" in labels

    @pytest.mark.parametrize(("choice", "method"), [("off", "add_tags"), ("on", "remove_tags")])
    def test_toggle_uses_the_tag(self, choice, method):
        card_service = _card_service()
        card_service.anki_client.add_tags = mock.AsyncMock()
        card_service.anki_client.remove_tags = mock.AsyncMock()
        card_service.anki_client.sync = mock.AsyncMock()
        query, _ = _press(f"pp:mask:5:{choice}", card_service)
        getattr(card_service.anki_client, method).assert_awaited_once_with([5], TEXT_UNMASKED_TAG)
        card_service.anki_client.sync.assert_awaited_once()
        assert "下次分析時生效" in _sent(query.message.answer)[0]
        new_toggle = query.message.edit_reply_markup.await_args.kwargs["reply_markup"].inline_keyboard[-1][0]
        assert new_toggle.callback_data == f"pp:mask:5:{'on' if choice == 'off' else 'off'}"

    @pytest.mark.parametrize(("state", "method"), [("off", "add_tags"), ("on", "remove_tags")])
    def test_card_toggle_icon_link(self, state, method):
        """卡面的眼睛圖示：深連結 pmask，index 是切換後的狀態（on＝遮）。"""
        action = DeepLinkParser.parse(f"pmask-Display_Name-{state}-PP_20261024_123456_abcd")
        assert isinstance(action, PersonLinkAction) and action.verb == "mask" and action.index == state
        card_service = _card_service()
        for name in ("add_tags", "remove_tags", "sync"):
            setattr(card_service.anki_client, name, mock.AsyncMock())
        message, _ = _message(f"/start pmask-Display_Name-{state}-PP_1")
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), card_service, state=_fsm(), note_service=_note_service()
        ))
        getattr(card_service.anki_client, method).assert_awaited_once_with([5], TEXT_UNMASKED_TAG)
        message.edit_reply_markup.assert_not_awaited()  # 使用者自己的 /start 訊息不能改
        assert "下次分析時生效" in _sent(message.answer)[-1]

    def test_card_toggle_icon_bad_state(self):
        message, _ = _message("/start pmask-Display_Name-maybe-PP_1")
        card_service = _card_service()
        card_service.anki_client.add_tags = mock.AsyncMock()
        _run(commands.command_start_handler(
            message, _anki(), mock.MagicMock(), card_service, state=_fsm(), note_service=_note_service()
        ))
        assert "這個連結不支援" in _sent(message.answer)[-1]
        card_service.anki_client.add_tags.assert_not_awaited()

    def test_toggle_with_bad_data(self):
        query, _ = _press("pp:mask:5:maybe", _card_service())
        assert "按鈕已失效" in _sent(query.message.answer)[0]

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
