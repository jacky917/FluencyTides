"""各 handler 的能力宣告、試點單字卡、建立參數與本機腳本保護的測試（handlers 計畫書 §9 第 2 步、§8）。

Tests for each handler's declarations, the vocabulary pilot, the create
parameter models, and the protection of the local batch-script endpoints.
"""

import asyncio
import inspect
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import settings
from app.core.dependencies import get_card_service, get_relation_service
from app.core.exceptions import FieldNotEditableError
from app.services.relation_service import RelationService
from app.services.task_handlers.base import BaseHandler
from app.services.task_handlers.capabilities import CreateCapability, HandlerCapabilities
from app.schemas.person_profile import PersonCreateParams
from app.services.task_handlers.expression_handler import ExpressionCreateParams
from app.services.task_handlers.jp_core_verb_handler import JPCoreVerbHandler
from app.services.task_handlers.jp_verb_pair_handler import JPVerbPairHandler
from app.services.task_handlers.registry import HandlerRegistry, handler_registry
from app.services.task_handlers.relation_graph import RelationGraphMixin
from app.services.task_handlers.speaking_coach_handler import SpeakingCoachCreateParams
from app.services.task_handlers.speaking_trilingual_handler import SpeakingTrilingualCreateParams
from app.services.task_handlers.vocabulary_mining_handler import VocabularyMiningHandler
from tests.test_handler_base import (
    V2,
    FakeAnki,
    _card_service,
    _labels,
    _note,
    _relations,
    _seed_relations,
    make_handler,
    session_factory,  # noqa: F401 - fixture
)


def _run(coro):
    return asyncio.run(coro)


def _caps(name: str) -> dict:
    return handler_registry.get_handler(name).CAPABILITIES.to_dict()


SPEAKING_ACTIONS = ["add_audio", "add_recording", "delete_recording"]


# =============================================================================
# §5 的初始值
# =============================================================================


class TestInitialCapabilities:
    def test_vocabulary_mining(self):
        assert _caps("vocabulary_mining") == {
            "create": {
                "supported": True, "channels": ["web", "tg"], "flow": "form", "deck": "selected",
                "fixed_deck": None, "note_type": V2, "reason": None,
            },
            "read": {"supported": True, "reason": None},
            "update_fields": {"supported": True, "reason": None},
            "update_json": {"supported": False, "reason": "尚未開放"},
            "delete": {"supported": True, "reason": None},
            "actions": [],
            "graph": {"supported": True, "links": True, "reason": None},
            "search": {"supported": False, "reason": "尚未開放"},
        }

    def test_speaking_coach(self):
        assert _caps("speaking_coach") == {
            "create": {
                "supported": False, "channels": ["tg"], "flow": None, "deck": None,
                "fixed_deck": None, "note_type": None, "reason": "網頁建卡尚未開放；可在 TG 建立",
            },
            "read": {"supported": True, "reason": None},
            "update_fields": {"supported": False, "reason": "對話卡的欄位修改尚未開放"},
            "update_json": {"supported": False, "reason": "尚未開放"},
            "delete": {"supported": False, "reason": "對話卡的刪除尚未開放"},
            "actions": SPEAKING_ACTIONS,
            "graph": {"supported": False, "links": False, "reason": "口說卡沒有知識圖譜"},
            "search": {"supported": False, "reason": "尚未開放"},
        }

    def test_speaking_trilingual(self):
        caps = _caps("speaking_trilingual")
        # 網頁的預覽流程（三語對話卡網頁建卡計畫）；TG 沒有建卡入口
        assert caps["create"] == {
            "supported": True, "channels": ["web"], "flow": "preview", "deck": "selected",
            "fixed_deck": None, "note_type": "Speaking_Trilingual_Dark", "reason": None,
        }
        assert caps["update_fields"] == {"supported": False, "reason": "三語對話卡的欄位修改尚未開放"}
        assert caps["delete"] == {"supported": False, "reason": "三語對話卡的刪除尚未開放"}
        assert caps["actions"] == SPEAKING_ACTIONS
        assert caps["graph"]["supported"] is False

    def test_expression_correction(self):
        caps = _caps("expression_correction")
        assert caps["create"] == {
            "supported": True, "channels": ["tg"], "flow": "preview", "deck": "fixed",
            "fixed_deck": "日本語::外語糾錯::母卡片", "note_type": "Expression_Master_Dark",
            "reason": "需要先預覽 LLM 結果再勾選子卡，目前只能在 TG 建立",
        }
        assert caps["update_fields"] == {"supported": False, "reason": "目前只支援新增音檔"}
        assert caps["delete"] == {"supported": False, "reason": "外語糾錯的母子卡刪除尚未開放"}
        assert caps["actions"] == ["add_audio"]
        assert caps["graph"] == {"supported": False, "links": False, "reason": "尚未實作"}

    @pytest.mark.parametrize(("name", "folder"), [("jp_verb_pair", "JP_VerbPair"), ("jp_core_verb", "JP_CoreVerb")])
    def test_jp_handlers_are_script_managed(self, name, folder):
        caps = _caps(name)
        assert caps["create"]["supported"] is False
        assert caps["create"]["channels"] == ["script"]
        assert "generate_child_cards.py" in caps["create"]["reason"]
        assert caps["update_fields"] == {"supported": False, "reason": "由本機批次腳本管理，網頁只能檢視"}
        assert caps["delete"]["supported"] is False
        assert f"scripts/local_anki/{folder}/delete_child_cards.py" in caps["delete"]["reason"]
        assert caps["actions"] == []
        assert caps["graph"]["supported"] is False

    def test_jp_shared_context_type_same_ops(self):
        """JP_Context_Dark 同屬兩個 jp handler，對既有筆記的能力必須相同（D4）。"""
        verb_pair, core_verb = handler_registry.handlers_for_note_type("JP_Context_Dark")
        assert verb_pair.CAPABILITIES.existing_note_ops() == core_verb.CAPABILITIES.existing_note_ops()

    def test_web_creatable_handlers(self):
        """Generate 頁可選單字卡（§6）、人脈卡（人脈卡計畫書 §3.11）與三語對話卡（預覽流程）。"""
        web = [h.handler_name for h in handler_registry.all_handlers() if "web" in h.CAPABILITIES.create.channels]
        assert sorted(web) == ["person_profile", "speaking_trilingual", "vocabulary_mining"]


# =============================================================================
# 試點：單字卡（D10）
# =============================================================================


class TestVocabularyPilot:
    def test_no_crud_overrides(self):
        """增刪改查全部用基底，圖譜用 RelationGraphMixin。"""
        for name in ("execute_create", "execute_read", "execute_update", "execute_delete"):
            assert getattr(VocabularyMiningHandler, name) is getattr(BaseHandler, name), name
        assert VocabularyMiningHandler.execute_read_graph is RelationGraphMixin.execute_read_graph
        assert issubclass(VocabularyMiningHandler, RelationGraphMixin)

    def test_declarations(self):
        handler = handler_registry.get_handler("vocabulary_mining")
        assert handler.supported_models == [V2]
        assert handler.RELATION_LABEL_FIELD == "Expression"
        assert (handler.GRAPH.subtitle, handler.GRAPH.pos) == ("Target_Translation", "Target_POS")
        assert set(handler.JSON_FIELDS) == {"Word_Family_JSON", "Synonyms_JSON", "Collocations_JSON"}
        assert set(handler.MEDIA_FIELDS) == {"Word_Audio", "Target_Example_Audio"}

    def test_create_from_expression_only(self):
        """TG 建單字卡傳的就是 {"Expression": 單字}（§7），建出 _v2、其餘欄位空白。"""
        anki = FakeAnki()
        ids = _run(VocabularyMiningHandler().execute_create(
            _card_service(anki), _relations(), "Default", V2, {"Expression": "allocate"}
        ))
        assert ids == [9001]
        (note,) = anki.added
        assert note.modelName == V2
        assert note.fields["Expression"] == "allocate"
        assert note.tags == ["VocabularyMining", "HandlerGenerated"]

    @pytest.mark.parametrize("field", ["Synonyms_JSON", "Word_Audio"])
    def test_json_and_media_fields_not_editable(self, field):
        anki = FakeAnki([_note(1, Expression="anyway")])
        with pytest.raises(FieldNotEditableError):
            _run(VocabularyMiningHandler().execute_update(
                _card_service(anki), _relations(), 1, {"action": "update_fields", "fields": {field: "x"}}
            ))
        assert anki.updated == []

    def test_renaming_expression_updates_both_relation_ends(self, session_factory):  # noqa: F811
        _seed_relations(session_factory, [(1, "anyway", 2, "regardless"), (2, "regardless", 1, "anyway")])
        anki = FakeAnki([_note(1, Expression="anyway"), _note(2, Expression="regardless")])

        async def update():
            async with session_factory() as session:
                await VocabularyMiningHandler().execute_update(
                    _card_service(anki), RelationService(session), 1,
                    {"action": "update_fields", "fields": {"Expression": "anyways"}},
                )

        _run(update())
        assert _labels(session_factory) == [("anyways", "regardless"), ("regardless", "anyways")]

    def test_graph_subtitle_reads_v2_translation(self):
        """原本讀不存在的 Meaning，副標永遠是空的。"""
        anki = FakeAnki([_note(1, Expression="anyway", Target_Translation="無論如何", Target_POS="adv.")])
        graph = _run(VocabularyMiningHandler().execute_read_graph(_card_service(anki), _relations()))
        (node,) = graph["nodes"]
        assert (node["translation"], node["pos"]) == ("無論如何", "adv.")


# =============================================================================
# 建立參數（D3）
# =============================================================================


class TestCreateParams:
    def test_handlers_overriding_create_declare_params(self):
        params = {h.handler_name: h.CREATE_PARAMS for h in handler_registry.all_handlers()}
        assert params == {
            "vocabulary_mining": None,
            "expression_correction": ExpressionCreateParams,
            "speaking_coach": SpeakingCoachCreateParams,
            "speaking_trilingual": SpeakingTrilingualCreateParams,
            "jp_verb_pair": None,
            "jp_core_verb": None,
            "person_profile": PersonCreateParams,
        }

    def test_speaking_language_normalized_like_tg(self):
        params = SpeakingCoachCreateParams.model_validate({"prompt": "Order coffee", "target_language": "ja-jp"})
        assert params.model_dump() == {"prompt": "Order coffee", "context": "", "target_language": "ja-JP"}
        assert SpeakingCoachCreateParams(prompt="x").target_language == "en-US"

    @pytest.mark.parametrize("bad", [{"prompt": ""}, {}, {"prompt": "x", "target_language": "english"},
                                     {"prompt": "x", "target_language": "other"}])
    def test_speaking_invalid(self, bad):
        with pytest.raises(ValidationError):
            SpeakingCoachCreateParams.model_validate(bad)

    def test_speaking_schema_for_form(self):
        schema = SpeakingCoachCreateParams.model_json_schema()
        assert schema["required"] == ["prompt"]
        assert schema["properties"]["prompt"]["x-ui"] == "textarea"
        assert schema["properties"]["target_language"]["examples"] == ["en-US", "ja-JP", "zh-TW"]
        assert "tg_bot" not in schema["properties"]

    def test_trilingual_prompt_required(self):
        with pytest.raises(ValidationError):
            SpeakingTrilingualCreateParams.model_validate({"context": "x"})
        assert SpeakingTrilingualCreateParams(prompt="x").model_dump() == {
            "prompt": "x", "context": "", "references_zh": [], "references_ja": [], "references_en": [],
        }

    def test_expression_accepts_exactly_what_tg_sends(self):
        """expression_fsm 不傳語言欄位；預設值要與 handler 自己讀參數時的預設相同。"""
        llm_result = {
            "error_comparison": "e", "grammar_correction": "g", "reorganized_expression": "r",
            "grammar_micro_points": [], "reorganized_micro_points": [],
        }
        params = ExpressionCreateParams.model_validate({
            "original_text": "私は行くました",
            "context": "",
            "source_tag": "",
            "llm_result": llm_result,
            "selected_indices": [1, 3],
            "allow_duplicate": True,
        })
        dumped = params.model_dump()
        assert dumped["native_language"] == "中文"
        assert dumped["target_language"] == "日文"
        assert dumped["selected_indices"] == [1, 3]
        assert dumped["allow_duplicate"] is True
        assert dumped["llm_result"]["grammar_correction"] == "g"
        assert "tg_bot" not in dumped

    def test_expression_original_text_required(self):
        with pytest.raises(ValidationError):
            ExpressionCreateParams.model_validate({"context": "x"})


class TestCreateParamsRegistryRule:
    def _open_create(self):
        return HandlerCapabilities(create=CreateCapability(
            supported=True, channels=("web",), flow="form", deck="selected", note_type=V2
        ))

    def test_overridden_create_needs_params(self):
        async def execute_create(self, *args, **kwargs):
            return []

        with pytest.raises(RuntimeError, match="CREATE_PARAMS"):
            HandlerRegistry().register(make_handler(CAPABILITIES=self._open_create(), execute_create=execute_create))

    def test_params_need_overridden_create(self):
        with pytest.raises(RuntimeError, match="標準建立"):
            HandlerRegistry().register(make_handler(CREATE_PARAMS=SpeakingTrilingualCreateParams))


# =============================================================================
# 本機腳本保護（D7、G8）
# =============================================================================


@pytest.fixture
def script_client():
    """真實的 app（不進入 lifespan），卡片與關聯服務換成假的，並設定 API 金鑰。"""
    from app.main import app

    app.dependency_overrides[get_card_service] = lambda: mock.Mock(name="card_service")
    app.dependency_overrides[get_relation_service] = lambda: mock.Mock(name="relation_service")
    try:
        with mock.patch.object(settings, "API_SECRET_KEY", "s3cret"):
            yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_card_service, None)
        app.dependency_overrides.pop(get_relation_service, None)


REQUEST = {
    "master_note_id": 11,
    "deck_name": "日本語::動詞",
    "target_verb": "開[あ]く",
    "source_game": "GAME",
    "context_dialogue": [{"speaker": "A", "text": "ドアが開く", "is_target": True}],
}
RESULT = {"context_note_id": 21, "cloze_note_id": 22, "kept_dialog": [{"voice": "a.ogg"}], "llm_model": "m"}


class TestScriptEndpoints:
    @pytest.mark.parametrize(
        ("url", "cls", "body"),
        [
            ("/api/v1/verb-pair/generate-child-cards", JPVerbPairHandler, REQUEST),
            ("/api/v1/core-verb/generate-child-cards", JPCoreVerbHandler, REQUEST),  # target_verb_span 可省略
            ("/api/v1/core-verb/generate-child-cards", JPCoreVerbHandler, {**REQUEST, "target_verb_span": [0, 2]}),
        ],
    )
    def test_contract_without_api_key(self, script_client, url, cls, body):
        with mock.patch.object(cls, "execute_generate", mock.AsyncMock(return_value=RESULT)) as generate:
            response = script_client.post(url, json=body)  # 不帶 X-API-Key

        assert response.status_code == 200
        assert response.json() == {"status": "success", "message": "已成功產生子卡片並關聯至母卡片", "data": RESULT}
        parameters = generate.await_args.kwargs["parameters"]
        assert {k: parameters[k] for k in REQUEST} == REQUEST
        assert parameters["action"] == "generate_child_cards"
        assert set(generate.await_args.kwargs) == {"card_service", "relation_service", "parameters"}

    @pytest.mark.parametrize("url", ["/api/v1/verb-pair/generate-child-cards", "/api/v1/core-verb/generate-child-cards"])
    def test_request_fields_unchanged(self, script_client, url):
        missing = {k: v for k, v in REQUEST.items() if k != "target_verb"}
        assert script_client.post(url, json=missing).status_code == 422

    @pytest.mark.parametrize("cls", [JPVerbPairHandler, JPCoreVerbHandler])
    def test_handlers_instantiate_and_keep_generate_signature(self, cls):
        handler = cls()
        assert list(inspect.signature(handler.execute_generate).parameters) == [
            "card_service", "relation_service", "parameters",
        ]

    def test_script_channel_handlers_have_execute_generate(self):
        for handler in handler_registry.all_handlers():
            if "script" in handler.CAPABILITIES.create.channels:
                assert callable(getattr(handler, "execute_generate", None)), handler.handler_name

    @pytest.mark.parametrize(
        ("method", "url"),
        [
            ("post", "/api/v1/example/verb-pair/create"),
            ("post", "/api/v1/verb-pair/create"),
            ("put", "/api/v1/verb-pair/1/update"),
        ],
    )
    def test_removed_leftovers_are_gone(self, script_client, method, url):
        """D8 移除的殘骸端點。"""
        assert getattr(script_client, method)(url, json={}).status_code in (404, 405)
