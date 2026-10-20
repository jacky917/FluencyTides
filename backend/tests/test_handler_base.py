"""BaseHandler 標準 CRUD、標準圖譜、關聯圖譜與註冊表檢查的測試（handlers 計畫書 §9 第 1 步、§8）。

Tests for the BaseHandler standard CRUD and graph, the relation graph mixin,
the capability declarations and the registry checks.

Anki 以記憶體裡的假客戶端代替；``CardService`` 用真的，因此欄位嚴格比對、重複檢查等行為與正式相同。
關聯表用記憶體 SQLite。

Anki is an in-memory fake; ``CardService`` is real, so its strict field
checks behave as in production. Relations use in-memory SQLite.
"""

import asyncio
import re
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.exceptions import (
    FieldNotEditableError,
    HandlerOperationUnsupportedError,
    InvalidNoteFieldsError,
    InvalidNoteQueryError,
    NoteNotFoundError,
)
from app.infrastructure.anki.search import note_types_query
from app.infrastructure.database.models import CardRelation
from app.schemas.anki import AnkiNoteInfo
from app.services.card_service import CardService, FieldMismatchError
from app.services.relation_service import RelationService
from app.services.task_handlers.base import BaseHandler, note_detail
from app.services.task_handlers.capabilities import (
    NOT_OFFERED,
    CreateCapability,
    GraphCapability,
    GraphSpec,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.registry import HandlerRegistry
from app.services.task_handlers.relation_graph import RelationGraphMixin

V2 = "TOEIC_Coach_Dark_v2"
V2_FIELDS = [
    "Expression", "Word_Audio", "Target_Example_Sentence", "Target_Example_Japanese",
    "Target_Example_Audio", "Target_Phonetic", "Target_POS", "Target_Translation", "Target_Nuance",
    "Target_Example_Translation", "Chunking", "Word_Family_JSON", "Synonyms_JSON", "Collocations_JSON",
]
OTHER = "Speaking_Coach_Dark"
OTHER_FIELDS = ["Prompt", "Card_ID"]


def _run(coro):
    return asyncio.run(coro)


# =============================================================================
# 假的 Anki 與 handler
# =============================================================================


def _note(note_id: int, model: str = V2, card: int | None = None, **values: str) -> AnkiNoteInfo:
    names = V2_FIELDS if model == V2 else OTHER_FIELDS
    return AnkiNoteInfo(
        noteId=note_id,
        modelName=model,
        tags=["t"],
        fields={name: {"value": values.get(name, ""), "order": i} for i, name in enumerate(names)},
        cards=[card if card is not None else note_id + 1],
    )


class FakeAnki:
    """記憶體裡的 AnkiConnect：只實作這裡用到的方法，並記下查詢與寫入。"""

    def __init__(self, notes=(), queues: dict[int, int] | None = None) -> None:
        self.notes = {n.noteId: n for n in notes}
        self.queues = queues or {}
        self.models = {V2: V2_FIELDS, OTHER: OTHER_FIELDS}
        self.queries: list[str] = []
        self.added: list = []
        self.updated: list[tuple[int, dict[str, str]]] = []
        self.deleted: list[int] = []
        self.notes_info_calls = 0
        self.next_id = 9000

    async def find_notes(self, query: str) -> list[int]:
        self.queries.append(query)
        types = {t.replace(r"\_", "_") for t in re.findall(r'note:"([^"]+)"', query)}
        return [n.noteId for n in self.notes.values() if n.modelName in types]

    async def get_notes_info(self, notes: list[int]) -> list[AnkiNoteInfo]:
        self.notes_info_calls += 1
        return [self.notes[i] for i in notes if i in self.notes]

    async def get_cards_info(self, cards: list[int]) -> list[dict[str, object]]:
        by_card = {n.cards[0]: n.noteId for n in self.notes.values()}
        return [{"cardId": c, "note": by_card[c], "queue": self.queues.get(by_card[c], 0)} for c in cards]

    async def get_model_field_names(self, model_name: str) -> list[str]:
        return list(self.models[model_name])

    async def add_notes(self, notes) -> list[int]:
        self.added.extend(notes)
        self.next_id += 1
        return [self.next_id]

    async def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        self.updated.append((note_id, fields))

    async def delete_notes(self, notes: list[int]) -> None:
        self.deleted.extend(notes)


def _card_service(anki: FakeAnki) -> CardService:
    manager = mock.Mock()
    manager.ensure_deck_exists = mock.AsyncMock()
    return CardService(anki, manager)


def _relations() -> mock.Mock:
    relations = mock.Mock(spec=RelationService)
    relations.update_labels_for_note = mock.AsyncMock(return_value=0)
    relations.delete_relations_for_note = mock.AsyncMock(return_value=0)
    relations.get_relations_by_note_ids = mock.AsyncMock(return_value=[])
    return relations


def make_handler(name: str = "demo", models=(V2,), base: type = BaseHandler, **attrs) -> BaseHandler:
    """建一個只實作抽象方法的 handler；``attrs`` 覆寫類別屬性或方法。"""
    namespace = {
        "handler_name": property(lambda self: name),
        "title": property(lambda self: "示範"),
        "supported_models": property(lambda self: list(models)),
        "get_input_schema": lambda self: {},
        "execute_read_list": lambda self, *a, **k: [],
        **attrs,
    }
    return type(f"Handler_{name}", (base,), namespace)()


# =============================================================================
# 能力宣告的形狀
# =============================================================================


class TestCapabilityShapes:
    def test_closed_operations_always_have_a_reason(self):
        assert Operation().reason == NOT_OFFERED
        assert Operation(reason="對話卡的刪除尚未開放").reason == "對話卡的刪除尚未開放"
        assert CreateCapability().reason == NOT_OFFERED
        assert GraphCapability().reason == NOT_OFFERED

    def test_open_operations_default_to_no_reason(self):
        """開放的能力不能帶著「尚未開放」回給前端（§4.1 的 reason 為 null）。"""
        assert Operation(supported=True).reason is None
        assert GraphCapability(supported=True, links=True).reason is None
        create = CreateCapability(supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=V2)
        assert create.reason is None

    def test_unknown_channel_rejected(self):
        with pytest.raises(ValueError):
            CreateCapability(channels=("email",), reason="x")

    def test_web_channel_requires_supported_form(self):
        with pytest.raises(ValueError):
            CreateCapability(channels=("web",), reason="x")
        with pytest.raises(ValueError):
            CreateCapability(supported=True, channels=("web",), flow="preview", deck="selected", note_type=V2)

    def test_supported_create_needs_settings(self):
        with pytest.raises(ValueError, match="note_type、flow、deck"):
            CreateCapability(supported=True, channels=("web",), flow="form")
        with pytest.raises(ValueError, match="fixed_deck"):
            CreateCapability(supported=True, channels=("web",), flow="form", deck="fixed", note_type=V2)
        with pytest.raises(ValueError, match="web 或 tg"):
            CreateCapability(supported=True, channels=("script",), flow="form", deck="selected", note_type=V2)

    def test_create_without_web_needs_reason(self):
        with pytest.raises(ValueError, match="要去哪裡建"):
            CreateCapability(supported=True, channels=("tg",), flow="preview", deck="selected", note_type=V2, reason=None)

    def test_links_need_graph(self):
        with pytest.raises(ValueError):
            GraphCapability(links=True)

    def test_defaults_offer_read_only(self):
        caps = HandlerCapabilities()
        assert caps.to_dict() == {
            "create": {
                "supported": False, "channels": [], "flow": None, "deck": None,
                "fixed_deck": None, "note_type": None, "reason": "尚未開放",
            },
            "read": {"supported": True, "reason": None},
            "update_fields": {"supported": False, "reason": "尚未開放"},
            "delete": {"supported": False, "reason": "尚未開放"},
            "actions": [],
            "graph": {"supported": False, "links": False, "reason": "尚未開放"},
        }


# =============================================================================
# 註冊表檢查（D2、D4）
# =============================================================================


def _web_create(note_type: str = V2) -> CreateCapability:
    return CreateCapability(supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=note_type)


class TestRegistryChecks:
    def test_registers_valid_handler_and_maps_note_types(self):
        registry = HandlerRegistry()
        handler = make_handler(CAPABILITIES=HandlerCapabilities(create=_web_create()))
        registry.register(handler)
        assert registry.get_handler("demo") is handler
        assert registry.handler_for_note_type(V2) is handler
        assert registry.handler_for_note_type("Notion_SRS_Dark") is None
        assert registry.handlers_for_note_type("Notion_SRS_Dark") == []

    @pytest.mark.parametrize(
        ("attrs", "problem"),
        [
            ({"CAPABILITIES": HandlerCapabilities(create=_web_create(OTHER))}, "create.note_type"),
            (
                {"CAPABILITIES": HandlerCapabilities(create=CreateCapability(channels=("script",), reason="腳本"))},
                "execute_generate",
            ),
            ({"CAPABILITIES": HandlerCapabilities(actions=("add_audio",))}, "execute_update"),
            ({"CAPABILITIES": HandlerCapabilities(graph=GraphCapability(supported=True, links=True))}, "RelationGraphMixin"),
            ({"JSON_FIELDS": ("Nope_JSON",)}, "Nope_JSON"),
            ({"MEDIA_FIELDS": ("Nope_Audio",)}, "Nope_Audio"),
            ({"GRAPH": GraphSpec(subtitle="Meaning")}, "Meaning"),
            ({"RELATION_LABEL_FIELD": "Word"}, "Word"),
        ],
    )
    def test_declaration_without_implementation_fails(self, attrs, problem):
        registry = HandlerRegistry()
        with pytest.raises(RuntimeError, match=problem):
            registry.register(make_handler(**attrs))
        assert registry.all_handlers() == []
        assert registry.handler_for_note_type(V2) is None

    def test_missing_definition_file_reported_as_such(self):
        with pytest.raises(RuntimeError, match="沒有定義檔"):
            HandlerRegistry().register(make_handler(models=("No_Such_Model",), JSON_FIELDS=("Data_JSON",)))
        # 沒宣告欄位時不需要定義檔
        HandlerRegistry().register(make_handler(models=("No_Such_Model",)))

    def test_script_channel_with_execute_generate_passes(self):
        async def execute_generate(self, card_service, relation_service, parameters):
            return {}

        registry = HandlerRegistry()
        registry.register(make_handler(
            CAPABILITIES=HandlerCapabilities(create=CreateCapability(channels=("script",), reason="腳本")),
            execute_generate=execute_generate,
        ))

    def test_actions_with_overridden_update_pass(self):
        async def execute_update(self, card_service, relation_service, note_id, parameters):
            return None

        registry = HandlerRegistry()
        registry.register(make_handler(
            CAPABILITIES=HandlerCapabilities(actions=("add_audio",)), execute_update=execute_update
        ))

    def test_relation_graph_needs_label_field_and_links(self):
        graph = GraphCapability(supported=True, links=True)
        with pytest.raises(RuntimeError, match="RELATION_LABEL_FIELD"):
            HandlerRegistry().register(make_handler(base=RelationGraphMixin, CAPABILITIES=HandlerCapabilities(graph=graph)))
        with pytest.raises(RuntimeError, match="graph.links"):
            HandlerRegistry().register(make_handler(
                base=RelationGraphMixin,
                RELATION_LABEL_FIELD="Expression",
                CAPABILITIES=HandlerCapabilities(graph=GraphCapability(supported=True)),
            ))
        HandlerRegistry().register(make_handler(
            base=RelationGraphMixin,
            RELATION_LABEL_FIELD="Expression",
            GRAPH=GraphSpec(subtitle="Target_Translation", pos="Target_POS"),
            JSON_FIELDS=("Synonyms_JSON",),
            MEDIA_FIELDS=("Word_Audio",),
            CAPABILITIES=HandlerCapabilities(graph=graph),
        ))

    def test_shared_note_type_requires_same_existing_note_ops(self):
        registry = HandlerRegistry()
        first = make_handler("first")
        registry.register(first)
        with pytest.raises(RuntimeError, match="共用筆記類型"):
            registry.register(make_handler(
                "second", CAPABILITIES=HandlerCapabilities(delete=Operation(supported=True))
            ))
        assert [h.handler_name for h in registry.all_handlers()] == ["first"]

        # 建立能力不同不影響（建立不是對既有筆記的操作）
        second = make_handler("second", CAPABILITIES=HandlerCapabilities(create=_web_create()))
        registry.register(second)
        assert registry.handlers_for_note_type(V2) == [first, second]
        assert registry.handler_for_note_type(V2) is first


class TestProductionRegistry:
    def test_six_handlers_in_order(self):
        from app.services.task_handlers.registry import handler_registry

        handlers = handler_registry.all_handlers()
        assert [(h.handler_name, h.title) for h in handlers] == [
            ("vocabulary_mining", "單字卡"),
            ("expression_correction", "外語糾錯"),
            ("speaking_coach", "對話卡"),
            ("speaking_trilingual", "三語對話卡"),
            ("jp_verb_pair", "自他動詞"),
            ("jp_core_verb", "核心動詞"),
        ]
        assert [h.handler_name for h in handler_registry.handlers_for_note_type("JP_Context_Dark")] == [
            "jp_verb_pair", "jp_core_verb",
        ]
        assert handler_registry.handler_for_note_type(V2).handler_name == "vocabulary_mining"

    def test_jp_handlers_instantiate_without_args(self):
        """腳本端點直接 new 這兩個 handler（D7），不能多出抽象方法。"""
        from app.services.task_handlers.jp_core_verb_handler import JPCoreVerbHandler
        from app.services.task_handlers.jp_verb_pair_handler import JPVerbPairHandler

        assert JPVerbPairHandler().handler_name == "jp_verb_pair"
        assert JPCoreVerbHandler().handler_name == "jp_core_verb"


# =============================================================================
# 標準 CRUD（D10）
# =============================================================================


class TestStandardCreate:
    def test_writes_fields_to_note_type_and_deck(self):
        anki = FakeAnki()
        handler = make_handler()
        ids = _run(handler.execute_create(
            _card_service(anki), _relations(), "English::TOEIC", V2,
            {"Expression": "allocate", "Target_Translation": "分配", "Chunking": None},
        ))
        assert ids == [9001]
        (note,) = anki.added
        assert (note.deckName, note.modelName, note.tags) == ("English::TOEIC", V2, ["HandlerGenerated"])
        assert note.fields["Expression"] == "allocate"
        assert note.fields["Target_Translation"] == "分配"
        assert note.fields["Chunking"] == ""
        assert note.fields["Word_Audio"] == ""  # 沒填的欄位由 CardService 補空字串

    def test_json_and_media_fields_rejected_before_writing(self):
        anki = FakeAnki()
        handler = make_handler(JSON_FIELDS=("Synonyms_JSON",), MEDIA_FIELDS=("Word_Audio",))
        for field in ("Synonyms_JSON", "Word_Audio"):
            with pytest.raises(FieldNotEditableError, match=field):
                _run(handler.execute_create(_card_service(anki), _relations(), "D", V2, {"Expression": "x", field: "y"}))
        assert anki.added == []

    def test_unknown_field_is_field_mismatch(self):
        anki = FakeAnki()
        with pytest.raises(FieldMismatchError):
            _run(make_handler().execute_create(_card_service(anki), _relations(), "D", V2, {"Meaning": "x"}))
        assert anki.added == []


class TestStandardRead:
    def test_note_detail_shape_in_field_order(self):
        anki = FakeAnki([_note(1, Expression="anyway", Target_Translation="無論如何")])
        detail = _run(make_handler().execute_read(_card_service(anki), _relations(), 1))
        assert detail["note_id"] == 1
        assert detail["note_type"] == V2
        assert detail["tags"] == ["t"]
        assert detail["cards"] == [2]
        assert [f["name"] for f in detail["fields"]] == V2_FIELDS
        assert detail["fields"][0] == {"name": "Expression", "value": "anyway"}

    def test_fields_sorted_by_order_not_dict_order(self):
        note = {
            "noteId": 5, "modelName": "M", "tags": [], "cards": [6],
            "fields": {"B": {"value": "b", "order": 1}, "A": {"value": "a", "order": 0}},
        }
        assert note_detail(note)["fields"] == [{"name": "A", "value": "a"}, {"name": "B", "value": "b"}]

    def test_missing_note(self):
        with pytest.raises(NoteNotFoundError):
            _run(make_handler().execute_read(_card_service(FakeAnki()), _relations(), 404))


class TestStandardUpdate:
    def _update(self, handler, anki, relations, fields, action="update_fields"):
        return _run(handler.execute_update(
            _card_service(anki), relations, 1, {"action": action, "fields": fields}
        ))

    def test_writes_only_changed_fields(self):
        anki = FakeAnki([_note(1, Expression="anyway", Target_Translation="無論如何")])
        relations = _relations()
        self._update(make_handler(), anki, relations, {"Expression": "anyway", "Target_Translation": "儘管如此"})
        assert anki.updated == [(1, {"Target_Translation": "儘管如此"})]
        relations.update_labels_for_note.assert_not_awaited()
        assert anki.notes_info_calls == 1  # 比對用的讀取沿用到欄位檢查，不再讀第二次

    def test_no_change_writes_nothing(self):
        anki = FakeAnki([_note(1, Expression="anyway")])
        self._update(make_handler(), anki, _relations(), {"Expression": "anyway"})
        assert anki.updated == []

    def test_fields_must_be_a_mapping(self):
        anki = FakeAnki([_note(1)])
        with pytest.raises(InvalidNoteFieldsError):
            self._update(make_handler(), anki, _relations(), ["Expression"])
        assert anki.updated == []

    def test_other_actions_unsupported(self):
        anki = FakeAnki([_note(1)])
        with pytest.raises(HandlerOperationUnsupportedError, match="add_audio"):
            self._update(make_handler(), anki, _relations(), {}, action="add_audio")
        assert anki.updated == []

    def test_json_and_media_fields_rejected(self):
        anki = FakeAnki([_note(1)])
        handler = make_handler(JSON_FIELDS=("Synonyms_JSON",), MEDIA_FIELDS=("Word_Audio",))
        with pytest.raises(FieldNotEditableError):
            self._update(handler, anki, _relations(), {"Target_POS": "n.", "Word_Audio": "[sound:x.mp3]"})
        assert anki.updated == []

    def test_unknown_field_is_field_mismatch(self):
        anki = FakeAnki([_note(1)])
        with pytest.raises(FieldMismatchError):
            self._update(make_handler(), anki, _relations(), {"Meaning": "x"})
        assert anki.updated == []

    def test_relation_label_field_change_updates_both_ends(self):
        anki = FakeAnki([_note(1, Expression="anyway")])
        relations = _relations()
        self._update(make_handler(RELATION_LABEL_FIELD="Expression"), anki, relations, {"Expression": " anyways "})
        relations.update_labels_for_note.assert_awaited_once_with(1, "anyways")

    def test_without_relation_label_field_relations_untouched(self):
        anki = FakeAnki([_note(1, Expression="anyway")])
        relations = _relations()
        self._update(make_handler(), anki, relations, {"Expression": "anyways"})
        relations.update_labels_for_note.assert_not_awaited()


class TestStandardDelete:
    def test_deletes_note_then_relations(self):
        calls: list[str] = []
        card_service = mock.Mock()
        card_service.delete_note = mock.AsyncMock(side_effect=lambda _id: calls.append("note"))
        relations = _relations()
        relations.delete_relations_for_note.side_effect = lambda _id: calls.append("relations")
        _run(make_handler().execute_delete(card_service, relations, 7))
        assert calls == ["note", "relations"]
        card_service.delete_note.assert_awaited_once_with(7)
        relations.delete_relations_for_note.assert_awaited_once_with(7)

    def test_note_delete_failure_keeps_relations(self):
        card_service = mock.Mock()
        card_service.delete_note = mock.AsyncMock(side_effect=RuntimeError("anki down"))
        relations = _relations()
        with pytest.raises(RuntimeError):
            _run(make_handler().execute_delete(card_service, relations, 7))
        relations.delete_relations_for_note.assert_not_awaited()


# =============================================================================
# 標準圖譜（D12）
# =============================================================================


def _graph(handler, anki, **kwargs):
    return _run(handler.execute_read_graph(_card_service(anki), _relations(), kwargs.pop("deck", None), **kwargs))


class TestStandardGraph:
    def _anki(self):
        return FakeAnki([
            _note(1, Expression="<b>anyway</b>", Target_Translation="無論如何"),
            _note(2, Expression=""),
            _note(3, model=OTHER, Prompt="Order coffee"),
        ])

    def test_nodes_default_to_note_id_for_any_note_type(self):
        handler = make_handler(models=(V2, OTHER))
        graph = _graph(handler, self._anki())
        assert graph["links"] == []
        assert graph["label_field"] is None
        assert graph["label_fields"] == V2_FIELDS + OTHER_FIELDS
        assert graph["nodes"] == [
            {"id": "1", "note_id": 1, "note_type": V2, "label": "1"},
            {"id": "2", "note_id": 2, "note_type": V2, "label": "2"},
            {"id": "3", "note_id": 3, "note_type": OTHER, "label": "3"},
        ]

    def test_label_field_uses_clean_value_and_falls_back_to_note_id(self):
        graph = _graph(make_handler(models=(V2, OTHER)), self._anki(), label_field="Expression")
        assert graph["label_field"] == "Expression"
        assert [n["label"] for n in graph["nodes"]] == ["anyway", "2", "3"]

    def test_unknown_label_field_rejected(self):
        with pytest.raises(InvalidNoteQueryError, match="Meaning"):
            _graph(make_handler(), self._anki(), label_field="Meaning")

    def test_note_type_filter(self):
        anki = self._anki()
        graph = _graph(make_handler(models=(V2, OTHER)), anki, deck="English", note_type=OTHER, label_field="Prompt")
        assert [n["label"] for n in graph["nodes"]] == ["Order coffee"]
        assert graph["label_fields"] == OTHER_FIELDS
        assert anki.queries == [note_types_query([OTHER], "English")]

    def test_foreign_note_type_rejected(self):
        with pytest.raises(InvalidNoteQueryError):
            _graph(make_handler(), self._anki(), note_type=OTHER)

    def test_declared_default_label_and_subtitle(self):
        handler = make_handler(GRAPH=GraphSpec(default_label="Expression", subtitle="Target_Translation"))
        graph = _graph(handler, self._anki())
        assert graph["label_field"] == "Expression"
        assert graph["nodes"][0] == {
            "id": "1", "note_id": 1, "note_type": V2, "label": "anyway", "translation": "無論如何",
        }

    def test_query_notes_rejects_foreign_note_type(self):
        anki = self._anki()
        with pytest.raises(InvalidNoteQueryError):
            _run(make_handler().query_notes(_card_service(anki), None, OTHER))
        assert anki.queries == []

    def test_no_notes(self):
        graph = _graph(make_handler(), FakeAnki())
        assert graph["nodes"] == []
        assert graph["label_fields"] == V2_FIELDS


# =============================================================================
# 關聯圖譜（RelationGraphMixin，D12）
# =============================================================================


def _relation(rid, source, target, rel_type, source_id=None, target_id=None):
    return SimpleNamespace(
        id=rid, source_label=source, target_label=target, relation_type=rel_type,
        source_note_id=source_id, target_note_id=target_id,
    )


class TestRelationGraph:
    def _handler(self):
        return make_handler(
            base=RelationGraphMixin,
            RELATION_LABEL_FIELD="Expression",
            GRAPH=GraphSpec(subtitle="Target_Translation", pos="Target_POS"),
        )

    def test_status_links_and_ghost_nodes(self):
        """與原本 vocabulary_mining 的圖譜輸出一致（只差副標、詞性改讀 _v2 的欄位）。"""
        anki = FakeAnki(
            [
                _note(1, Expression=" anyway ", Target_Translation="<i>無論如何</i>", Target_POS="adv."),
                _note(2, Expression="allocate", Target_Translation="分配", Target_POS="v."),
                _note(3, Expression="", Target_Translation="空的"),
                _note(4, Expression="assign"),
                _note(5, Expression="defer"),
            ],
            queues={1: 2, 2: 1, 4: -1, 5: 0},
        )
        relations = _relations()
        relations.get_relations_by_note_ids.return_value = [
            _relation(10, "anyway", "anyhow", "synonym", 1, None),
            _relation(11, "allocate", "allocate funds", "collocation", 2, None),
            _relation(12, "budget", "allocate", "related", None, 2),
            _relation(13, "anyway", "allocate", "antonym", 1, 2),
        ]
        graph = _run(self._handler().execute_read_graph(_card_service(anki), relations, "English"))

        relations.get_relations_by_note_ids.assert_awaited_once_with({1, 2, 3, 4, 5})
        assert graph["label_fields"] == []
        assert graph["label_field"] == "Expression"
        assert graph["nodes"] == [
            {"id": "anyway", "group": 1, "val": 20, "label": "anyway", "translation": "無論如何",
             "pos": "adv.", "note_id": 1, "status": "review"},
            {"id": "allocate", "group": 1, "val": 20, "label": "allocate", "translation": "分配",
             "pos": "v.", "note_id": 2, "status": "learning"},
            {"id": "assign", "group": 1, "val": 20, "label": "assign", "translation": "",
             "pos": "", "note_id": 4, "status": "suspended"},
            {"id": "defer", "group": 1, "val": 20, "label": "defer", "translation": "",
             "pos": "", "note_id": 5, "status": "new"},
            {"id": "anyhow", "group": 2, "val": 10, "note_id": None},
            {"id": "allocate funds", "group": 3, "val": 10, "note_id": None},
            {"id": "budget", "group": 4, "val": 10, "label": "budget", "note_id": None},
        ]
        assert graph["links"] == [
            {"source": "anyway", "target": "anyhow", "label": "Synonym", "relation_id": 10},
            {"source": "allocate", "target": "allocate funds", "label": "Collocation", "relation_id": 11},
            {"source": "budget", "target": "allocate", "label": "Related", "relation_id": 12},
            {"source": "anyway", "target": "allocate", "label": "Antonym", "relation_id": 13},
        ]

    def test_no_notes_queries_all_relations(self):
        relations = _relations()
        graph = _run(self._handler().execute_read_graph(_card_service(FakeAnki()), relations))
        relations.get_relations_by_note_ids.assert_awaited_once_with(None)
        assert graph["nodes"] == [] and graph["links"] == []

    def test_label_field_not_accepted(self):
        with pytest.raises(InvalidNoteQueryError, match="Expression"):
            _run(self._handler().execute_read_graph(
                _card_service(FakeAnki()), _relations(), label_field="Target_Translation"
            ))

    def test_foreign_note_type_rejected(self):
        with pytest.raises(InvalidNoteQueryError):
            _run(self._handler().execute_read_graph(_card_service(FakeAnki()), _relations(), note_type=OTHER))


# =============================================================================
# RelationService.update_labels_for_note（D12）
# =============================================================================


@pytest.fixture
def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    _run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    _run(engine.dispose())


def _seed_relations(session_factory, rows):
    async def seed():
        async with session_factory() as session:
            for source_id, source, target_id, target in rows:
                session.add(CardRelation(
                    source_note_id=source_id, target_note_id=target_id, relation_type="synonym",
                    source_label=source, target_label=target,
                ))
            await session.commit()

    _run(seed())


def _labels(session_factory):
    async def read():
        async with session_factory() as session:
            rows = (await session.execute(select(CardRelation).order_by(CardRelation.id))).scalars().all()
            return [(r.source_label, r.target_label) for r in rows]

    return _run(read())


class TestUpdateLabelsForNote:
    def test_updates_source_and_target_ends(self, session_factory):
        _seed_relations(session_factory, [
            (1, "anyway", None, "anyhow"),
            (2, "regardless", 1, "anyway"),
            (2, "regardless", 3, "nevertheless"),
            (1, "anyway", 1, "anyway"),
        ])

        async def update():
            async with session_factory() as session:
                return await RelationService(session).update_labels_for_note(1, "anyways")

        assert _run(update()) == 4
        assert _labels(session_factory) == [
            ("anyways", "anyhow"),
            ("regardless", "anyways"),
            ("regardless", "nevertheless"),
            ("anyways", "anyways"),
        ]

    def test_unreported_row_count_returns_none(self):
        session = mock.Mock()
        session.execute = mock.AsyncMock(return_value=SimpleNamespace(rowcount=-1))
        session.commit = mock.AsyncMock()
        assert _run(RelationService(session).update_labels_for_note(1, "x")) is None
        session.commit.assert_awaited_once()

    def test_standard_update_keeps_graph_connected(self, session_factory):
        """改名稱欄位後，兩端的標籤都跟著改，圖譜不會分裂成兩個節點。"""
        _seed_relations(session_factory, [(1, "anyway", 2, "regardless"), (2, "regardless", 1, "anyway")])
        anki = FakeAnki([_note(1, Expression="anyway"), _note(2, Expression="regardless")])
        handler = make_handler(base=RelationGraphMixin, RELATION_LABEL_FIELD="Expression")

        async def update():
            async with session_factory() as session:
                await handler.execute_update(
                    _card_service(anki), RelationService(session), 1,
                    {"action": "update_fields", "fields": {"Expression": "anyways"}},
                )

        _run(update())
        assert _labels(session_factory) == [("anyways", "regardless"), ("regardless", "anyways")]
