"""三語對話卡網頁建卡的測試（計畫書 ``docs/wip/trilingual_web_create_FEAT_2026-10-30.md`` §7）。

Tests for the trilingual speaking card web create: the preview-flow
declaration checks, the draft endpoint, the LLM draft service, the prompt
and how the chosen references are written.

API 測試用真實的 app（同 ``test_handlers_api``），只把 Anki 換成假的、LLM 生成換成固定結果。
"""

import asyncio
import html
import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import BaseModel, ValidationError

from app.core.exceptions import LLMGenerationError
from app.schemas.llm.speaking_trilingual import GeneratedSaying, LLMTrilingualDraft
from app.services import speaking_trilingual_generate as generate_service
from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import CreateCapability, HandlerCapabilities
from app.services.task_handlers.registry import handler_registry, validate_handler
from app.services.task_handlers.speaking_trilingual_handler import (
    MAX_REFERENCES_PER_LANG,
    MAX_TEXT_LENGTH,
    SpeakingTrilingualCreateParams,
    SpeakingTrilingualHandler,
)
from tests.test_handlers_api import _error, anki, client, relations  # noqa: F401 （pytest fixtures）

TRILINGUAL = "Speaking_Trilingual_Dark"


def _draft(**overrides) -> LLMTrilingualDraft:
    """LLM 回傳的草稿。An LLM draft."""
    values = {
        "prompt": "朋友約你週末爬山，但你已經有約了。",
        "context": "",
        "zh": [GeneratedSaying(content="這週不行耶，下週好不好？", note="對朋友的直接婉拒。", familiarity=8)],
        "ja": [
            GeneratedSaying(content="今週はちょっと無理かも。", note="對朋友。", familiarity=7),
            GeneratedSaying(content="  ", note="空白的句子會被丟掉", familiarity=3),
        ],
        "en": [GeneratedSaying(content="Can't this weekend, sorry!", note="", familiarity=8)],
    }
    return LLMTrilingualDraft(**{**values, **overrides})


# =============================================================================
# 宣告檢查（註冊表）
# =============================================================================


class _Params(BaseModel):
    text: str


def _handler(create: CreateCapability, *, draft_params=None, with_draft=False, create_params=_Params):
    """組一個最小的 handler 類別，用來測註冊表的檢查。"""

    class Probe(BaseHandler):
        CAPABILITIES = HandlerCapabilities(create=create)
        CREATE_PARAMS = create_params
        DRAFT_PARAMS = draft_params
        handler_name = "probe"
        title = "測試"
        supported_models = [TRILINGUAL]

        async def execute_create(self, *args, **kwargs):  # pragma: no cover - 不會被呼叫
            return []

    if with_draft:
        async def execute_draft(self, parameters, existing_tags):  # pragma: no cover - 不會被呼叫
            return {}

        Probe.execute_draft = execute_draft
    return Probe()


WEB_PREVIEW = CreateCapability(supported=True, channels=("web",), flow="preview", deck="selected", note_type=TRILINGUAL)
WEB_FORM = CreateCapability(supported=True, channels=("web",), flow="form", deck="selected", note_type=TRILINGUAL)


class TestDeclarations:
    def test_web_preview_needs_draft(self):
        with pytest.raises(RuntimeError, match="DRAFT_PARAMS"):
            validate_handler(_handler(WEB_PREVIEW))
        with pytest.raises(RuntimeError, match="DRAFT_PARAMS"):
            validate_handler(_handler(WEB_PREVIEW, draft_params=_Params))  # 沒覆寫 execute_draft

    def test_draft_without_web_preview_is_rejected(self):
        with pytest.raises(RuntimeError, match="沒有開放網頁的預覽流程"):
            validate_handler(_handler(WEB_FORM, draft_params=_Params, with_draft=True))

    def test_complete_web_preview_passes(self):
        validate_handler(_handler(WEB_PREVIEW, draft_params=_Params, with_draft=True))

    def test_trilingual_is_registered_with_web_preview(self):
        handler = handler_registry.get_handler("speaking_trilingual")
        validate_handler(handler)
        assert handler.CAPABILITIES.create.flow == "preview"


# =============================================================================
# 生成服務與提示詞
# =============================================================================


class TestTargetAudience:
    @pytest.mark.parametrize(
        ("audience", "note", "expected"),
        [
            ("不指定", "", None),
            ("不指定", "  ", None),
            ("不指定", "房東", "房東"),
            ("同事", "", "同事"),
            ("同事", "比我資深", "同事（比我資深）"),
        ],
    )
    def test_resolution(self, audience, note, expected):
        assert generate_service.target_audience(audience, note) == expected


class TestPrompt:
    @pytest.mark.parametrize("intent", ["", "婉拒，改約下週"])
    @pytest.mark.parametrize("audience", [None, "朋友"])
    def test_renders_every_combination(self, intent, audience):
        """StrictUndefined 下每種輸入都能渲染，且「先自然，再打分」的準則一定在。"""
        prompt = generate_service.build_prompt("  朋友約我爬山  ", intent, audience)
        assert "朋友約我爬山" in prompt
        assert "先自然，再打分" in prompt
        assert "濫竽充數" in prompt
        assert ("他想表達的意思" in prompt) == bool(intent)
        if audience:
            assert "只寫對這個對象自然的說法" in prompt
        else:
            assert "對不同關係的人" in prompt
        if not intent:
            assert "答應、拒絕、保留" in prompt


def _route_returning(parsed: object):
    client = SimpleNamespace(
        generate_structured_data=mock.AsyncMock(
            return_value=SimpleNamespace(parsed_data=parsed, model_name="test-model", raw_content="{}")
        )
    )
    return SimpleNamespace(client=mock.AsyncMock(return_value=client)), client


class TestGenerate:
    def test_valid_output(self):
        route, llm = _route_returning(_draft().model_dump())
        with mock.patch.object(generate_service, "GENERATE_ROUTE", route):
            draft = asyncio.run(generate_service.generate("背景", "", "朋友"))
        assert draft.ja[0].familiarity == 7
        assert "朋友" in llm.generate_structured_data.await_args.kwargs["user_prompt"]

    @pytest.mark.parametrize(
        "parsed",
        [
            {"prompt": "x", "context": "", "zh": [], "ja": [], "en": [{"content": "a", "note": "", "familiarity": 12}]},
            {"prompt": "x"},
            None,
        ],
        ids=["familiarity-out-of-range", "missing-languages", "nothing"],
    )
    def test_malformed_output(self, parsed):
        route, _ = _route_returning(parsed)
        with mock.patch.object(generate_service, "GENERATE_ROUTE", route), pytest.raises(
            LLMGenerationError, match="test-model"
        ):
            asyncio.run(generate_service.generate("背景", "", None))


# =============================================================================
# handler：草稿與建卡
# =============================================================================


class TestExecuteDraft:
    def test_shapes_create_params(self):
        generate = mock.AsyncMock(return_value=_draft())
        with mock.patch.object(generate_service, "generate", generate):
            result = asyncio.run(SpeakingTrilingualHandler().execute_draft(
                {"background": "背景", "intent": "", "audience": "同事", "audience_note": "比我資深"},
                ["Interview"],
            ))
        assert generate.await_args.args == ("背景", "", "同事（比我資深）", ["Interview"])
        assert result.pop("tags") == []
        assert result["prompt"] == "朋友約你週末爬山，但你已經有約了。"
        assert result["references_ja"] == [{"content": "今週はちょっと無理かも。", "note": "對朋友。", "familiarity": 7}]
        assert len(result["references_zh"]) == 1 and len(result["references_en"]) == 1
        # 草稿就是建卡端點會接受的形狀
        assert SpeakingTrilingualCreateParams.model_validate(result).model_dump() == result


class TestCreateParams:
    def test_familiarity_follows_the_shared_rule(self):
        params = SpeakingTrilingualCreateParams.model_validate({
            "prompt": "p",
            "references_ja": [
                {"content": " a ", "familiarity": "7"},
                {"content": "b", "familiarity": ""},
                {"content": "c"},
            ],
        })
        assert [(r.content, r.familiarity) for r in params.references_ja] == [("a", 7), ("b", None), ("c", None)]

    @pytest.mark.parametrize("bad", [10, -1, True, "7.0", 2.5])
    def test_invalid_familiarity_rejected(self, bad):
        with pytest.raises(ValidationError, match="熟識度"):
            SpeakingTrilingualCreateParams.model_validate(
                {"prompt": "p", "references_en": [{"content": "a", "familiarity": bad}]}
            )

    def test_generous_limits(self):
        """上限只擋異常的量：正常草稿（二十幾句、每句幾十字）遠遠碰不到。"""
        ok = {"content": "あ" * MAX_TEXT_LENGTH, "note": "說" * MAX_TEXT_LENGTH}
        SpeakingTrilingualCreateParams.model_validate(
            {"prompt": "p", "references_ja": [ok] * MAX_REFERENCES_PER_LANG}
        )
        too_many = {"prompt": "p", "references_ja": [{"content": "a"}] * (MAX_REFERENCES_PER_LANG + 1)}
        too_long = {"prompt": "p", "references_en": [{"content": "a" * (MAX_TEXT_LENGTH + 1)}]}
        too_long_note = {"prompt": "p", "references_zh": [{"content": "a", "note": "n" * (MAX_TEXT_LENGTH + 1)}]}
        long_prompt = {"prompt": "p" * (MAX_TEXT_LENGTH + 1)}
        for bad in (too_many, too_long, too_long_note, long_prompt):
            with pytest.raises(ValidationError):
                SpeakingTrilingualCreateParams.model_validate(bad)

    def test_blank_content_rejected(self):
        with pytest.raises(ValidationError):
            SpeakingTrilingualCreateParams.model_validate({"prompt": "p", "references_zh": [{"content": "  "}]})


def _created_fields(anki) -> dict[str, str]:
    note = anki.added[-1]
    fields = note["fields"] if isinstance(note, dict) else note.fields
    return dict(fields)


def _references(fields: dict[str, str], lang: str) -> list[dict]:
    return json.loads(html.unescape(fields[f"References_{lang}"]))


# =============================================================================
# REST API
# =============================================================================


def test_list_handlers_exposes_draft_schema(client):
    handlers = {h["name"]: h for h in client.get("/api/v1/handlers").json()}
    trilingual = handlers["speaking_trilingual"]
    assert trilingual["create_schema"] is None  # 預覽流程沒有一般建卡表單
    schema = trilingual["draft_schema"]
    assert schema["required"] == ["background"]
    assert schema["properties"]["audience"]["enum"][0] == "不指定"
    assert handlers["vocabulary_mining"]["draft_schema"] is None


def test_draft_endpoint(client, anki):
    with mock.patch.object(generate_service, "generate", mock.AsyncMock(return_value=_draft())):
        response = client.post("/api/v1/handlers/speaking_trilingual/draft", json={"params": {"background": "爬山"}})
    assert response.status_code == 200
    params = response.json()["params"]
    assert params["references_zh"][0]["familiarity"] == 8
    assert anki.added == []  # 草稿不寫入


def test_draft_requires_background(client):
    response = client.post("/api/v1/handlers/speaking_trilingual/draft", json={"params": {"background": ""}})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "params", "background"]


@pytest.mark.parametrize("handler", ["vocabulary_mining", "expression_correction"])
def test_draft_refused_without_web_preview(client, handler):
    response = client.post(f"/api/v1/handlers/{handler}/draft", json={"params": {}})
    assert _error(response) == (400, "HANDLER_OPERATION_UNSUPPORTED")


def test_llm_failure_is_reported(client):
    failing = mock.AsyncMock(side_effect=LLMGenerationError("模型 m 回傳的格式不對，請再試一次。"))
    with mock.patch.object(generate_service, "generate", failing):
        response = client.post("/api/v1/handlers/speaking_trilingual/draft", json={"params": {"background": "x"}})
    assert response.status_code >= 400
    assert "回傳的格式不對" in response.json()["message"]


def test_create_writes_chosen_references(client, anki):
    response = client.post("/api/v1/handlers/speaking_trilingual/notes", json={
        "deck": "Default",
        "note_type": TRILINGUAL,
        "params": {
            "prompt": "朋友約你爬山",
            "context": "",
            "references_ja": [
                {"content": "今週はちょっと…", "note": "對朋友<委婉>", "familiarity": 7},
                {"content": "あいにく先約がございまして。", "note": "", "familiarity": None},
            ],
            "references_en": [{"content": "Rock & roll!", "note": "x", "familiarity": 9}],
        },
    })
    assert response.status_code == 201, response.json()
    fields = _created_fields(anki)
    today = date.today().isoformat()
    assert _references(fields, "JA") == [
        {"content": "今週はちょっと…", "note": "對朋友<委婉>", "familiarity": 7, "status": 1, "date": today, "audios": []},
        # 空的說明與未標的熟識度不寫入（與匯入的資料一致）
        {"content": "あいにく先約がございまして。", "status": 1, "date": today, "audios": []},
    ]
    assert _references(fields, "ZH") == []
    # 與語音流程、匯入相同：JSON 經 HTML 轉義後寫入
    assert "&lt;委婉&gt;" in fields["References_JA"] and "&amp;" in fields["References_EN"]
    assert json.loads(fields["Recordings_JA"]) == []


def test_create_rejects_invalid_familiarity(client, anki):
    response = client.post("/api/v1/handlers/speaking_trilingual/notes", json={
        "deck": "Default",
        "note_type": TRILINGUAL,
        "params": {"prompt": "p", "references_zh": [{"content": "a", "familiarity": 12}]},
    })
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:3] == ["body", "params", "references_zh"]
    assert anki.added == []
