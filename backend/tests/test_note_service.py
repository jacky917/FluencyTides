"""NoteService 的測試（handlers 計畫書 §9 第 3 步、§8）。

Tests for the note service: capability and ownership checks, parameter
validation, the sync after writes, permissions and the handler list.

Anki 以記憶體裡的假客戶端代替；handler 用正式註冊的 6 個，必要時另建測試用註冊表。

Anki is an in-memory fake; the six registered handlers are used, plus a
test registry where needed.
"""

import asyncio
from unittest import mock

import pytest
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.exceptions import (
    FieldNotEditableError,
    HandlerOperationUnsupportedError,
    NoteNotFoundError,
    NoteNotOwnedByHandlerError,
    NoteTypeNotCreatableError,
)
from app.infrastructure.anki.client import AnkiConnectError
from app.schemas.anki import AnkiNoteInfo
from app.services.card_service import FieldMismatchError
from app.services.note_service import ANKI_UNREACHABLE, NoteService, standard_params_model
from app.services.task_handlers.capabilities import CreateCapability, HandlerCapabilities
from app.services.task_handlers.model_fields import model_field_names
from app.services.task_handlers.registry import HandlerNotFoundError, HandlerRegistry
from tests.test_handler_base import V2, FakeAnki, _card_service, _note, _relations, make_handler


def _run(coro):
    return asyncio.run(coro)


class SyncingAnki(FakeAnki):
    """會同步、認得 repo 裡所有筆記類型的假 Anki。"""

    def __init__(self, notes=(), *, sync_error: Exception | None = None, fields_error: Exception | None = None):
        super().__init__(notes)
        self.syncs: list[dict[str, bool]] = []
        self.sync_error = sync_error
        self.fields_error = fields_error

    async def sync(self, raise_errors: bool = False, force: bool = False) -> None:
        self.syncs.append({"raise_errors": raise_errors, "force": force})
        if self.sync_error:
            raise self.sync_error

    async def get_model_field_names(self, model_name: str) -> list[str]:
        if self.fields_error:
            raise self.fields_error
        # 以 repo 的定義檔為準（FakeAnki 的 Speaking_Coach_Dark 只有兩個欄位的簡化版）
        return list(model_field_names(model_name) or self.models.get(model_name, ()))


def _model_note(note_id: int, model: str, **values: str) -> AnkiNoteInfo:
    """依 repo 定義檔的欄位建一張筆記。A note with the fields of a repo model."""
    names = model_field_names(model)
    assert names, model
    return AnkiNoteInfo(
        noteId=note_id,
        modelName=model,
        tags=[],
        fields={name: {"value": values.get(name, ""), "order": i} for i, name in enumerate(names)},
        cards=[note_id + 1],
    )


def _service(anki: FakeAnki, relations=None, registry=None) -> NoteService:
    kwargs = {"registry": registry} if registry else {}
    return NoteService(_card_service(anki), relations or _relations(), **kwargs)


class _Params(BaseModel):
    prompt: str


def _param_handler(captured: list):
    """宣告 CREATE_PARAMS、覆寫建立的 handler，記下收到的參數。"""

    async def execute_create(self, card_service, relation_service, deck_name, model_name, parameters):
        captured.append((deck_name, model_name, parameters))
        return [42]

    return make_handler(
        "custom",
        CAPABILITIES=HandlerCapabilities(create=CreateCapability(
            supported=True, channels=("web",), flow="form", deck="selected", note_type=V2
        )),
        CREATE_PARAMS=_Params,
        execute_create=execute_create,
    )


# =============================================================================
# create
# =============================================================================


class TestCreate:
    def test_standard_create_writes_and_syncs_once(self):
        anki = SyncingAnki()
        result = _run(_service(anki).create(
            "vocabulary_mining", "English", V2, {"Expression": "allocate", "Target_Translation": "分配"}
        ))
        assert result == {"note_ids": [9001], "deck": "English", "sync_warning": None}
        (note,) = anki.added
        assert note.fields["Expression"] == "allocate"
        assert note.fields["Target_Translation"] == "分配"
        assert anki.syncs == [{"raise_errors": True, "force": True}]

    @pytest.mark.parametrize("params", [{}, {"Expression": ""}, {"Expression": "   "}, {"Target_POS": "v."}])
    def test_first_field_required(self, params):
        anki = SyncingAnki()
        with pytest.raises(ValidationError):
            _run(_service(anki).create("vocabulary_mining", "English", V2, params))
        assert anki.added == [] and anki.syncs == []

    def test_unknown_and_locked_fields_reach_handler_errors(self):
        anki = SyncingAnki()
        with pytest.raises(FieldMismatchError):
            _run(_service(anki).create("vocabulary_mining", "D", V2, {"Expression": "x", "Meaning": "y"}))
        with pytest.raises(FieldNotEditableError):
            _run(_service(anki).create("vocabulary_mining", "D", V2, {"Expression": "x", "Word_Audio": "a.mp3"}))
        assert anki.added == [] and anki.syncs == []

    def test_wrong_note_type(self):
        with pytest.raises(NoteTypeNotCreatableError):
            _run(_service(SyncingAnki()).create("vocabulary_mining", "D", "TOEIC_Coach_Dark", {"Expression": "x"}))

    def test_closed_create_gives_reason(self):
        with pytest.raises(HandlerOperationUnsupportedError, match="網頁建卡尚未開放"):
            _run(_service(SyncingAnki()).create("speaking_coach", "D", "Speaking_Coach_Dark", {"prompt": "x"}))

    def test_unknown_handler(self):
        with pytest.raises(HandlerNotFoundError):
            _run(_service(SyncingAnki()).create("nope", "D", V2, {}))

    def test_sync_failure_returns_warning_not_error(self):
        anki = SyncingAnki(sync_error=AnkiConnectError("timeout"))
        result = _run(_service(anki).create("vocabulary_mining", "D", V2, {"Expression": "x"}))
        assert result["note_ids"] == [9001]
        assert result["sync_warning"] == "⚠️ 已寫入 Anki，但同步 AnkiWeb 失敗；下次操作會再試。"

    def test_sync_conflict_warning(self):
        anki = SyncingAnki(sync_error=AnkiConnectError("同步衝突"))
        result = _run(_service(anki).create("vocabulary_mining", "D", V2, {"Expression": "x"}))
        assert "同步衝突" in result["sync_warning"] and "上傳" in result["sync_warning"]

    def test_params_model_validated_and_tg_bot_added(self):
        captured: list = []
        registry = HandlerRegistry()
        registry.register(_param_handler(captured))
        service = _service(SyncingAnki(), registry=registry)

        with mock.patch.object(settings, "TG_BOT_USERNAME", "default_bot"):
            _run(service.create("custom", "D", V2, {"prompt": "hi"}))
            _run(service.create("custom", "D", V2, {"prompt": "hi"}, tg_bot="tg_bot_me"))
        assert [c[2] for c in captured] == [
            {"prompt": "hi", "tg_bot": "default_bot"},
            {"prompt": "hi", "tg_bot": "tg_bot_me"},
        ]
        with pytest.raises(ValidationError):
            _run(service.create("custom", "D", V2, {}))

    def test_expression_accepts_tg_params_and_uses_fixed_deck(self):
        """TG 只傳這些參數（沒有語言欄位）；母卡固定建在外語糾錯牌組。"""
        from app.services.task_handlers.expression_handler import ExpressionCorrectionHandler

        anki = SyncingAnki()
        create = mock.AsyncMock(return_value=[1, 2])
        with mock.patch.object(ExpressionCorrectionHandler, "execute_create", create):
            result = _run(_service(anki).create(
                "expression_correction", "Ignored::Deck", "Expression_Master_Dark",
                {"original_text": "私は行くました", "context": "", "source_tag": "",
                 "llm_result": None, "selected_indices": [1], "allow_duplicate": False},
                tg_bot="me_bot",
            ))
        assert result["note_ids"] == [1, 2]
        assert result["deck"] == "日本語::外語糾錯::母卡片"
        args = create.await_args.args
        assert args[2] == "日本語::外語糾錯::母卡片"
        assert args[4]["tg_bot"] == "me_bot"
        assert args[4]["native_language"] == "中文"


class TestStandardParamsModel:
    def test_schema_lists_editable_fields_with_first_required(self):
        model = standard_params_model("M", ("Front", "Back Side", "Data_JSON", "Audio"), frozenset({"Data_JSON", "Audio"}))
        schema = model.model_json_schema(by_alias=True)
        assert list(schema["properties"]) == ["Front", "Back Side"]
        assert schema["properties"]["Back Side"]["title"] == "Back Side"
        assert schema["required"] == ["Front"]
        dumped = model.model_validate({"Front": " x ", "Back Side": "y", "Extra": "z"}).model_dump(by_alias=True)
        assert dumped == {"Front": "x", "Back Side": "y", "Extra": "z"}


# =============================================================================
# read / update_fields / delete：歸屬與能力
# =============================================================================


class TestOwnershipAndCapabilities:
    def test_read_through_handler(self):
        anki = SyncingAnki([_note(1, Expression="anyway")])
        detail = _run(_service(anki).read("vocabulary_mining", 1))
        assert detail["note_id"] == 1 and detail["fields"][0] == {"name": "Expression", "value": "anyway"}

    def test_note_not_owned(self):
        anki = SyncingAnki([_model_note(5, "Speaking_Coach_Dark", Prompt="hi")])
        service = _service(anki)
        with pytest.raises(NoteNotOwnedByHandlerError):
            _run(service.read("vocabulary_mining", 5))
        with pytest.raises(NoteNotOwnedByHandlerError):
            _run(service.update_fields("vocabulary_mining", 5, {"Expression": "x"}))
        with pytest.raises(NoteNotOwnedByHandlerError):
            _run(service.delete("vocabulary_mining", 5))
        assert anki.updated == [] and anki.deleted == [] and anki.syncs == []

    def test_shared_context_readable_from_both_jp_handlers(self):
        anki = SyncingAnki([_model_note(7, "JP_Context_Dark")])
        service = _service(anki)
        for name in ("jp_verb_pair", "jp_core_verb"):
            assert _run(service.read(name, 7))["note_type"] == "JP_Context_Dark"

    def test_closed_operations_touch_nothing(self):
        anki = SyncingAnki([_model_note(5, "Speaking_Coach_Dark", Prompt="hi")])
        relations = _relations()
        service = _service(anki, relations)
        with pytest.raises(HandlerOperationUnsupportedError, match="對話卡的刪除尚未開放"):
            _run(service.delete("speaking_coach", 5))
        with pytest.raises(HandlerOperationUnsupportedError, match="對話卡的欄位修改尚未開放"):
            _run(service.update_fields("speaking_coach", 5, {"Prompt": "x"}))
        assert anki.deleted == [] and anki.updated == [] and anki.syncs == []
        relations.delete_relations_for_note.assert_not_awaited()

    def test_update_fields_returns_note_and_warning(self):
        anki = SyncingAnki([_note(1, Expression="anyway")])
        relations = _relations()
        result = _run(_service(anki, relations).update_fields("vocabulary_mining", 1, {"Target_POS": "adv."}))
        assert anki.updated == [(1, {"Target_POS": "adv."})]
        assert result["note_id"] == 1 and result["sync_warning"] is None
        assert anki.syncs == [{"raise_errors": True, "force": True}]

    def test_delete_note_and_relations_then_sync(self):
        anki = SyncingAnki([_note(1, Expression="anyway")], sync_error=AnkiConnectError("x"))
        relations = _relations()
        warning = _run(_service(anki, relations).delete("vocabulary_mining", 1))
        assert anki.deleted == [1]
        relations.delete_relations_for_note.assert_awaited_once_with(1)
        assert warning.startswith("⚠️")

    def test_missing_note(self):
        with pytest.raises(NoteNotFoundError):
            _run(_service(SyncingAnki()).delete("vocabulary_mining", 404))


# =============================================================================
# run_action
# =============================================================================


class TestRunAction:
    def test_action_not_declared(self):
        anki = SyncingAnki([_note(1)])
        with pytest.raises(HandlerOperationUnsupportedError, match="add_audio"):
            _run(_service(anki).run_action(1, "add_audio", {}))
        with pytest.raises(HandlerOperationUnsupportedError):
            _run(_service(anki).run_action(1, "update_fields", {"fields": {}}))
        assert anki.updated == [] and anki.syncs == []

    def test_note_type_without_handler(self):
        anki = SyncingAnki([_model_note(3, "Notion_SRS_Dark")])
        with pytest.raises(HandlerOperationUnsupportedError, match="沒有 handler 負責 Notion_SRS_Dark"):
            _run(_service(anki).run_action(3, "add_audio", {}))

    @pytest.mark.parametrize(
        ("model", "action", "params", "field"),
        [
            ("Speaking_Coach_Dark", "add_recording", {"recording": {"score": 90}}, "Recordings"),
            ("Speaking_Coach_Dark", "delete_recording", {"index": 0}, "Recordings"),
            ("Speaking_Coach_Dark", "add_audio",
             {"field_name": "Prompt_Audios", "index": "last", "audio": "a.mp3"}, "Prompt_Audios"),
            ("Speaking_Trilingual_Dark", "add_recording", {"lang": "JA", "recording": {"score": 1}}, "Recordings_JA"),
            ("Speaking_Trilingual_Dark", "delete_recording", {"lang": "JA", "index": 0}, "Recordings_JA"),
            ("Speaking_Trilingual_Dark", "add_audio",
             {"field_name": "Prompt_Audios", "index": "JA", "audio": "a.mp3"}, "Prompt_Audios"),
            ("Expression_Master_Dark", "add_audio", {"field_name": "Answer_Audios", "audio": "a.mp3"}, "Answer_Audios"),
        ],
    )
    def test_every_declared_action_runs(self, model, action, params, field):
        """§8：每個 handler 宣告的每個動作都真的能執行，並在之後同步。"""
        values = {"Recordings": '[{"score": 1}]', "Recordings_JA": '[{"score": 1}]'}
        anki = SyncingAnki([_model_note(9, model, **values)])
        warning = _run(_service(anki).run_action(9, action, params))
        assert warning is None
        assert [list(fields) for _, fields in anki.updated] == [[field]]
        assert anki.syncs == [{"raise_errors": True, "force": True}]

    def test_declared_actions_are_all_covered(self):
        """上面的參數化涵蓋所有宣告的動作，新增動作時要補測試。"""
        from app.services.task_handlers.registry import handler_registry

        declared = {(h.handler_name, a) for h in handler_registry.all_handlers() for a in h.CAPABILITIES.actions}
        assert declared == {
            ("speaking_coach", "add_audio"), ("speaking_coach", "add_recording"), ("speaking_coach", "delete_recording"),
            ("speaking_trilingual", "add_audio"), ("speaking_trilingual", "add_recording"),
            ("speaking_trilingual", "delete_recording"),
            ("expression_correction", "add_audio"),
        }


# =============================================================================
# get（通用讀取）與 permissions
# =============================================================================


class TestGet:
    def test_vocabulary_allowed(self):
        anki = SyncingAnki([_note(1, Expression="anyway")])
        result = _run(_service(anki).get(1))
        assert result["handler"] == "vocabulary_mining"
        assert result["permissions"] == {
            "update_fields": {"allowed": True, "reason": None},
            "delete": {"allowed": True, "reason": None},
        }
        assert result["fields"][0] == {"name": "Expression", "value": "anyway"}

    def test_closed_permissions_carry_reasons(self):
        anki = SyncingAnki([_model_note(5, "Speaking_Coach_Dark"), _model_note(7, "JP_Context_Dark")])
        speaking = _run(_service(anki).get(5))["permissions"]
        assert speaking["update_fields"] == {"allowed": False, "reason": "對話卡的欄位修改尚未開放"}
        jp = _run(_service(anki).get(7))
        assert jp["handler"] == "jp_verb_pair"  # 第一個註冊的
        assert "delete_child_cards.py" in jp["permissions"]["delete"]["reason"]

    def test_note_type_without_handler(self):
        anki = SyncingAnki([_model_note(3, "Notion_SRS_Dark")])
        result = _run(_service(anki).get(3))
        assert result["handler"] is None
        reason = "沒有 handler 負責 Notion_SRS_Dark"
        assert result["permissions"] == {
            "update_fields": {"allowed": False, "reason": reason},
            "delete": {"allowed": False, "reason": reason},
        }

    def test_missing_note(self):
        with pytest.raises(NoteNotFoundError):
            _run(_service(SyncingAnki()).get(404))


# =============================================================================
# list_handlers 與 graph
# =============================================================================


class TestListHandlers:
    def test_shape_and_create_schemas(self):
        handlers = {h["name"]: h for h in _run(_service(SyncingAnki()).list_handlers())}
        assert list(handlers) == [
            "vocabulary_mining", "expression_correction", "speaking_coach",
            "speaking_trilingual", "jp_verb_pair", "jp_core_verb", "person_profile",
        ]
        vocab = handlers["vocabulary_mining"]
        assert vocab["title"] == "單字卡" and vocab["note_types"] == [V2]
        schema = vocab["create_schema"]
        assert schema["required"] == ["Expression"]
        assert "Synonyms_JSON" not in schema["properties"] and "Word_Audio" not in schema["properties"]
        assert schema["properties"]["Target_Translation"] == {"default": "", "title": "Target_Translation", "type": "string"}
        assert vocab["json_fields"] == ["Word_Family_JSON", "Synonyms_JSON", "Collocations_JSON"]
        assert vocab["media_fields"] == ["Word_Audio", "Target_Example_Audio"]
        # 不開放、或不是表單流程的沒有表單
        for name in ("expression_correction", "speaking_coach", "speaking_trilingual", "jp_verb_pair", "jp_core_verb"):
            assert handlers[name]["create_schema"] is None, name

    def test_anki_unreachable_keeps_other_capabilities(self):
        anki = SyncingAnki(fields_error=AnkiConnectError("connection refused"))
        handlers = {h["name"]: h for h in _run(_service(anki).list_handlers())}
        vocab = handlers["vocabulary_mining"]
        assert vocab["create_schema"] is None
        assert vocab["capabilities"]["create"]["reason"] == ANKI_UNREACHABLE
        assert vocab["capabilities"]["create"]["supported"] is True
        assert vocab["capabilities"]["delete"]["supported"] is True


class TestGraph:
    def test_closed_graph(self):
        with pytest.raises(HandlerOperationUnsupportedError, match="口說卡沒有知識圖譜"):
            _run(_service(SyncingAnki()).graph("speaking_coach"))

    def test_vocabulary_graph(self):
        anki = SyncingAnki([_note(1, Expression="anyway", Target_Translation="無論如何")])
        graph = _run(_service(anki).graph("vocabulary_mining", "English"))
        assert graph["nodes"][0]["translation"] == "無論如何"
        assert graph["label_field"] == "Expression"
