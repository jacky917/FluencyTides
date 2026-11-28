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
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import (
    FieldNotEditableError,
    HandlerOperationUnsupportedError,
    InvalidNoteFieldsError,
    InvalidNoteQueryError,
    MediaFieldInvalidError,
    NoteNotFoundError,
)
from app.infrastructure.anki.search import note_types_query
from app.infrastructure.database.corpus_tables import corpus_metadata, rel_links_table
from app.schemas.anki import AnkiNoteInfo
from app.services.card_service import CardService, FieldMismatchError
from app.services.relation_service import RelationService
from app.services.task_handlers.base import BaseHandler, note_detail
from app.services.task_handlers.capabilities import (
    NOT_OFFERED,
    CreateCapability,
    CreatorCapabilities,
    GraphCapability,
    GraphSpec,
    HandlerCapabilities,
    Operation,
)
from app.services.task_handlers.creator import BaseCreator
from app.services.task_handlers.registry import CreatorNotFoundError, HandlerNotFoundError, HandlerRegistry
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
        **attrs,
    }
    return type(f"Handler_{name}", (base,), namespace)()


def make_creator(name: str = "demo", handler: BaseHandler | None = None, base: type = BaseCreator, **attrs) -> BaseCreator:
    """建一個只實作抽象方法的 creator；``attrs`` 覆寫類別屬性或方法（``HANDLER`` 預設同名）。

    給了 ``handler`` 就直接綁定，不經註冊表（測試建卡流程本身時用）。
    """
    namespace = {"creator_name": property(lambda self: name), "HANDLER": name, **attrs}
    creator = type(f"Creator_{name}", (base,), namespace)()
    if handler is not None:
        creator.bind(handler)
    return creator


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

    def test_web_channel_requires_supported(self):
        with pytest.raises(ValueError):
            CreateCapability(channels=("web",), reason="x")

    def test_web_channel_allows_preview_flow(self):
        """網頁也能用預覽流程（三語對話卡網頁建卡計畫 §6.1）。"""
        create = CreateCapability(supported=True, channels=("web",), flow="preview", deck="selected", note_type=V2)
        assert create.reason is None

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

    def test_tree_layout_has_no_relation_links(self):
        """樹狀圖譜的連線就是上下層，不能再宣告關聯連線。"""
        with pytest.raises(ValueError, match="layout='tree'"):
            GraphCapability(supported=True, links=True, layout="tree")

    def test_defaults_offer_read_only(self):
        """建好之後的能力預設只開放讀取；建卡不在 handler 上（handler 拆分計畫書 D2）。"""
        caps = HandlerCapabilities()
        assert caps.to_dict() == {
            "read": {"supported": True, "reason": None},
            "update_fields": {"supported": False, "reason": "尚未開放"},
            "update_json": {"supported": False, "reason": "尚未開放"},
            "delete": {"supported": False, "reason": "尚未開放"},
            "actions": [],
            "graph": {"supported": False, "links": False, "layout": "force", "reason": "尚未開放"},
            "search": {"supported": False, "reason": "尚未開放"},
        }

    def test_creator_defaults_offer_nothing(self):
        assert CreatorCapabilities().to_dict() == {
            "create": {
                "supported": False, "channels": [], "flow": None, "deck": None,
                "fixed_deck": None, "note_type": None, "reason": "尚未開放",
            },
            "fill": {"supported": False, "reason": "尚未開放"},
        }


# =============================================================================
# 註冊表檢查（D2；一種筆記類型一個 handler：handler 拆分計畫書 D4）
# =============================================================================


def _web_create(note_type: str = V2) -> CreateCapability:
    return CreateCapability(supported=True, channels=("web", "tg"), flow="form", deck="selected", note_type=note_type)


class TestRegistryChecks:
    def test_registers_valid_handler_and_maps_note_types(self):
        registry = HandlerRegistry()
        handler = make_handler()
        registry.register(handler)
        assert registry.get_handler("demo") is handler
        assert registry.handler_for_note_type(V2) is handler
        assert registry.handler_for_note_type("Notion_SRS_Dark") is None

    @pytest.mark.parametrize(
        ("attrs", "problem"),
        [
            ({"CAPABILITIES": HandlerCapabilities(actions=("add_audio",))}, "execute_update"),
            ({"CAPABILITIES": HandlerCapabilities(graph=GraphCapability(supported=True, links=True))}, "RelationGraphMixin"),
            (
                {"CAPABILITIES": HandlerCapabilities(graph=GraphCapability(supported=True, layout="tree"))},
                "execute_read_graph",
            ),
            ({"JSON_FIELDS": ("Nope_JSON",)}, "Nope_JSON"),
            ({"MEDIA_FIELDS": {"Nope_Audio": "audio"}}, "Nope_Audio"),
            ({"MEDIA_FIELDS": {"Word_Audio": "video"}}, "種類只能是"),
            ({"JSON_FIELDS": ("Synonyms_JSON",), "MEDIA_FIELDS": {"Synonyms_JSON": "audio"}}, "同時宣告"),
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
            MEDIA_FIELDS={"Word_Audio": "audio"},
            CAPABILITIES=HandlerCapabilities(graph=graph),
        ))

    def test_second_handler_for_a_note_type_rejected(self):
        """一種筆記類型只能有一個 handler，第二個直接失敗，什麼都不留（handler 拆分計畫書 D4、D8）。"""
        registry = HandlerRegistry()
        first = make_handler("first")
        registry.register(first)
        # 能力完全相同也一樣：不再比對能力，而是不允許共用
        with pytest.raises(ValueError, match=r"Handler 'second' .*TOEIC_Coach_Dark_v2（已歸 'first'）"):
            registry.register(make_handler("second", models=(V2, OTHER)))
        assert [h.handler_name for h in registry.all_handlers()] == ["first"]
        assert registry.handler_for_note_type(V2) is first
        assert registry.handler_for_note_type(OTHER) is None

    def test_duplicate_handler_name_rejected(self):
        registry = HandlerRegistry()
        registry.register(make_handler("same"))
        with pytest.raises(ValueError, match="已經被註冊過了"):
            registry.register(make_handler("same", models=(OTHER,)))

    def test_unknown_handler_raises_not_found(self):
        with pytest.raises(HandlerNotFoundError, match="任務處理器"):
            HandlerRegistry().get_handler("nope")


# =============================================================================
# creator 的註冊與檢查（handler 拆分計畫書 D4、D5、D7）
# =============================================================================


def _registry_with(*creators: BaseCreator, handlers: tuple = ()) -> HandlerRegistry:
    registry = HandlerRegistry()
    for handler in handlers or (make_handler(),):
        registry.register(handler)
    for creator in creators:
        registry.register_creator(creator)
    return registry


class TestCreatorChecks:
    def test_check_binds_each_creator_to_its_handler(self):
        handler = make_handler()
        creator = make_creator(CAPABILITIES=CreatorCapabilities(create=_web_create()))
        registry = _registry_with(creator, handlers=(handler,))
        with pytest.raises(RuntimeError, match="還沒綁定"):
            creator.handler  # noqa: B018 - 綁定之前讀不到
        registry.check_creators()
        assert registry.get_creator("demo") is creator
        assert creator.handler is handler
        assert creator.title == handler.title  # 沒覆寫時沿用 handler 的名稱
        assert registry.creators_for_handler("demo") == [creator]
        assert registry.all_creators() == [creator]
        registry.check_creators()  # 重複呼叫沒有副作用
        assert creator.handler is handler

    def test_unknown_creator_raises_not_found(self):
        with pytest.raises(CreatorNotFoundError, match="建卡流程") as info:
            HandlerRegistry().get_creator("nope")
        assert isinstance(info.value, HandlerNotFoundError)  # 錯誤碼與拆分前相同
        assert info.value.error_code == "HANDLER_NOT_FOUND"

    @pytest.mark.parametrize(
        ("attrs", "problem"),
        [
            ({"HANDLER": ""}, "HANDLER"),
            ({"CAPABILITIES": CreatorCapabilities(create=CreateCapability(channels=("script",), reason="腳本"))},
             "execute_generate"),
        ],
    )
    def test_declaration_without_implementation_fails(self, attrs, problem):
        registry = HandlerRegistry()
        with pytest.raises(RuntimeError, match=problem):
            registry.register_creator(make_creator(**attrs))
        assert registry.all_creators() == []

    def test_duplicate_creator_name_rejected(self):
        registry = HandlerRegistry()
        registry.register_creator(make_creator())
        with pytest.raises(ValueError, match="已經被註冊過了"):
            registry.register_creator(make_creator())

    def test_script_channel_with_execute_generate_passes(self):
        async def execute_generate(self, card_service, relation_service, parameters):
            return {}

        registry = _registry_with(make_creator(
            CAPABILITIES=CreatorCapabilities(create=CreateCapability(channels=("script",), reason="腳本")),
            execute_generate=execute_generate,
        ))
        registry.check_creators()

    @pytest.mark.parametrize(
        ("creator", "problem"),
        [
            (make_creator(HANDLER="nope"), "HANDLER='nope' 沒有註冊"),
            (make_creator("other", HANDLER="demo"), "必須和它的 handler 'demo' 同名"),
            (make_creator(CAPABILITIES=CreatorCapabilities(create=_web_create(OTHER))), "create.note_type"),
            # 不開放建卡但寫了別人的 note_type 也要擋（D5：有值時就檢查）
            (make_creator(CAPABILITIES=CreatorCapabilities(create=CreateCapability(
                channels=("script",), note_type=OTHER, reason="腳本"
            )), execute_generate=lambda self: None), "create.note_type"),
            (make_creator(PRODUCES=(V2, "Notion_SRS_Dark")), "Notion_SRS_Dark"),
            (
                make_creator(CAPABILITIES=CreatorCapabilities(create=_web_create()), PRODUCES=(OTHER,)),
                "沒有包含 create.note_type",
            ),
        ],
    )
    def test_check_against_handlers_fails(self, creator, problem):
        registry = _registry_with(creator)
        with pytest.raises(RuntimeError, match=problem):
            registry.check_creators()
        with pytest.raises(RuntimeError, match="還沒綁定"):
            creator.handler  # noqa: B018 - 檢查不過時不綁定任何 creator

    def test_first_stage_allows_one_creator_per_handler(self):
        """第一階段的網址只有一個名稱，一個 handler 最多一個 creator（handler 拆分計畫書 D7）。"""
        registry = _registry_with(make_creator(), make_creator("second", HANDLER="demo"))
        with pytest.raises(RuntimeError, match="有多個 creator"):
            registry.check_creators()
        # GET /handlers 取 creator 的地方也不默默挑一個
        with pytest.raises(RuntimeError, match="有多個 creator"):
            registry.creator_for_handler("demo")

    def test_creator_for_handler(self):
        creator = make_creator()
        registry = _registry_with(creator, handlers=(make_handler(), make_handler("bare", models=(OTHER,))))
        assert registry.creator_for_handler("demo") is creator
        assert registry.creator_for_handler("bare") is None

    def test_creator_registered_after_the_check_is_bound_on_the_spot(self):
        """check_creators 之後才註冊的 creator 當場檢查並綁定，不會留下沒綁定的。"""
        other = make_handler("other", models=(OTHER,))
        registry = _registry_with(make_creator(), handlers=(make_handler(), other))
        registry.check_creators()
        late = make_creator("other")
        registry.register_creator(late)
        assert late.handler is other

    def test_creator_registered_after_the_check_is_rejected_when_inconsistent(self):
        registry = _registry_with(make_creator())
        registry.check_creators()
        with pytest.raises(RuntimeError, match="HANDLER='nope' 沒有註冊"):
            registry.register_creator(make_creator("late", HANDLER="nope"))
        assert [c.creator_name for c in registry.all_creators()] == ["demo"]

    def test_creator_may_produce_note_types_of_other_handlers(self):
        """jp 的 creator 會寫出共用的 Context 卡，它歸另一個 handler。"""
        context = make_handler("context", models=(OTHER,))
        creator = make_creator(PRODUCES=(V2, OTHER))
        registry = _registry_with(creator, handlers=(make_handler(), context))
        registry.check_creators()
        assert registry.producers_of(OTHER) == [creator]
        assert creator.produced_note_types() == (V2, OTHER)

    def test_produced_note_types_default_to_create_note_type(self):
        assert make_creator(CAPABILITIES=CreatorCapabilities(create=_web_create())).produced_note_types() == (V2,)
        assert make_creator().produced_note_types() == ()


class TestProductionRegistry:
    def test_handlers_in_order(self):
        from app.services.task_handlers.registry import handler_registry

        handlers = handler_registry.all_handlers()
        assert [(h.handler_name, h.title) for h in handlers] == [
            ("vocabulary_mining", "單字卡"),
            ("expression_correction", "外語糾錯"),
            ("speaking_coach", "對話卡"),
            ("speaking_trilingual", "三語對話卡"),
            ("jp_verb_pair", "自他動詞"),
            ("jp_core_verb", "核心動詞"),
            ("jp_context", "日文語境卡"),
            ("person_profile", "人脈卡"),
        ]
        assert handler_registry.handler_for_note_type("JP_Context_Dark").handler_name == "jp_context"
        assert handler_registry.handler_for_note_type(V2).handler_name == "vocabulary_mining"

    def test_every_creator_is_named_after_and_bound_to_its_handler(self):
        """一種建法的類型 handler 與 creator 同名，用途鍵、草稿、網址都不用改（handler 拆分計畫書 D6）。"""
        from app.services.task_handlers.registry import handler_registry

        creators = handler_registry.all_creators()
        assert [c.creator_name for c in creators] == [
            "vocabulary_mining", "expression_correction", "speaking_coach", "speaking_trilingual",
            "jp_verb_pair", "jp_core_verb", "person_profile",
        ]
        for creator in creators:
            assert creator.handler is handler_registry.get_handler(creator.creator_name)

    def test_both_jp_creators_produce_the_shared_context_cards(self):
        from app.services.task_handlers.registry import handler_registry

        producers = [c.creator_name for c in handler_registry.producers_of("JP_Context_Dark")]
        assert producers == ["jp_verb_pair", "jp_core_verb"]
        assert handler_registry.creators_for_handler("jp_context") == []

    def test_no_creator_declares_what_existing_cards_can_do(self):
        """creator 只宣告怎麼建；建好之後的宣告只在 handler 上（handler 拆分計畫書 D2、D10）。"""
        from app.services.task_handlers.registry import handler_registry

        handler_only = (
            "GRAPH", "RELATION_LABEL_FIELD", "JSON_FIELDS", "JSON_FIELD_SPECS", "MEDIA_FIELDS", "SEARCH_FIELDS",
            "RECORDING_EVAL", "execute_read", "execute_update", "execute_delete", "query_notes",
            "execute_read_graph", "after_json_update", "reject_locked_fields", "field_texts",
        )
        for creator in handler_registry.all_creators():
            leaked = [name for name in handler_only if hasattr(creator, name)]
            assert leaked == [], (creator.creator_name, leaked)
            assert not isinstance(creator, BaseHandler)


# =============================================================================
# 標準 CRUD（D10）
# =============================================================================


class TestStandardCreate:
    """標準建立在 creator 上，欄位的 JSON／媒體宣告由它的 handler 決定（handler 拆分計畫書 D5）。"""

    def test_writes_fields_to_note_type_and_deck(self):
        anki = FakeAnki()
        creator = make_creator(handler=make_handler())
        ids = _run(creator.execute_create(
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

    def test_json_fields_rejected_before_writing(self):
        anki = FakeAnki()
        creator = make_creator(handler=make_handler(JSON_FIELDS=("Synonyms_JSON",), MEDIA_FIELDS={"Word_Audio": "audio"}))
        with pytest.raises(FieldNotEditableError, match="Synonyms_JSON"):
            _run(creator.execute_create(
                _card_service(anki), _relations(), "D", V2, {"Expression": "x", "Synonyms_JSON": "y"}
            ))
        assert anki.added == []

    def test_media_field_takes_an_uploaded_name_and_stores_anki_markup(self):
        """media_upload 計畫書 §4：填 /media 產生的檔名成功，欄位存成 [sound:檔名]。"""
        anki = FakeAnki()
        creator = make_creator(handler=make_handler(MEDIA_FIELDS={"Word_Audio": "audio"}))
        _run(creator.execute_create(
            _card_service(anki), _relations(), "D", V2, {"Expression": "x", "Word_Audio": "web_audio_20261114_010203_0a1b2c.mp3"}
        ))
        (note,) = anki.added
        assert note.fields["Word_Audio"] == "[sound:web_audio_20261114_010203_0a1b2c.mp3]"

    @pytest.mark.parametrize(
        "value", ["a.mp3", "[sound:web_audio_20261114_010203_0a1b2c.mp3]", "web_image_20261114_010203_0a1b2c.jpg", "web_audio_20261114_010203_0a1b2c.exe", "../web_audio_20261114_010203_0a1b2c.mp3"]
    )
    def test_media_field_rejects_anything_else_before_writing(self, value):
        anki = FakeAnki()
        creator = make_creator(handler=make_handler(MEDIA_FIELDS={"Word_Audio": "audio"}))
        with pytest.raises(MediaFieldInvalidError, match="Word_Audio"):
            _run(creator.execute_create(
                _card_service(anki), _relations(), "D", V2, {"Expression": "x", "Word_Audio": value}
            ))
        assert anki.added == []

    def test_empty_media_field_stays_empty(self):
        anki = FakeAnki()
        creator = make_creator(handler=make_handler(MEDIA_FIELDS={"Word_Audio": "audio"}))
        _run(creator.execute_create(_card_service(anki), _relations(), "D", V2, {"Expression": "x", "Word_Audio": "  "}))
        assert anki.added[0].fields["Word_Audio"] == ""

    def test_unknown_field_is_field_mismatch(self):
        anki = FakeAnki()
        with pytest.raises(FieldMismatchError):
            _run(make_creator(handler=make_handler()).execute_create(_card_service(anki), _relations(), "D", V2, {"Meaning": "x"}))
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

    def test_json_fields_rejected(self):
        anki = FakeAnki([_note(1)])
        handler = make_handler(JSON_FIELDS=("Synonyms_JSON",), MEDIA_FIELDS={"Word_Audio": "audio"})
        with pytest.raises(FieldNotEditableError):
            self._update(handler, anki, _relations(), {"Target_POS": "n.", "Synonyms_JSON": "[]"})
        assert anki.updated == []

    def test_media_field_rejects_typed_names_and_writes_nothing(self):
        anki = FakeAnki([_note(1)])
        handler = make_handler(MEDIA_FIELDS={"Word_Audio": "audio"})
        with pytest.raises(MediaFieldInvalidError):
            self._update(handler, anki, _relations(), {"Target_POS": "n.", "Word_Audio": "[sound:x.mp3]"})
        assert anki.updated == []

    def test_media_field_replaced_with_an_uploaded_name(self):
        anki = FakeAnki([_note(1, Word_Audio="[sound:old.mp3]")])
        handler = make_handler(MEDIA_FIELDS={"Word_Audio": "audio"})
        self._update(handler, anki, _relations(), {"Word_Audio": "web_audio_20261114_010203_0a1b2c.mp3"})
        assert anki.updated == [(1, {"Word_Audio": "[sound:web_audio_20261114_010203_0a1b2c.mp3]"})]

    def test_media_field_sent_back_unchanged_is_not_written(self):
        """欄位原本的值（腳本或 TG 寫的，不是 web_ 開頭）原樣送回代表沒動，不檢查也不寫。"""
        anki = FakeAnki([_note(1, Word_Audio="[sound:old.mp3]")])
        handler = make_handler(MEDIA_FIELDS={"Word_Audio": "audio"})
        self._update(handler, anki, _relations(), {"Word_Audio": "[sound:old.mp3]", "Target_POS": "n."})
        assert anki.updated == [(1, {"Target_POS": "n."})]

    def test_media_field_can_be_cleared(self):
        anki = FakeAnki([_note(1, Word_Audio="[sound:old.mp3]")])
        handler = make_handler(MEDIA_FIELDS={"Word_Audio": "audio"})
        self._update(handler, anki, _relations(), {"Word_Audio": ""})
        assert anki.updated == [(1, {"Word_Audio": ""})]

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

    def test_no_notes_gives_an_empty_graph_without_reading_relations(self):
        """牌組裡沒有這個 handler 的筆記時回空圖，不能退回查全部關聯（否則每個牌組都顯示同一張圖）。"""
        relations = _relations()
        relations.get_relations_by_note_ids.return_value = [_relation(10, "痩せる", "痩せる_cloze", "has_cloze", 7, 8)]
        graph = _run(self._handler().execute_read_graph(_card_service(FakeAnki()), relations, "JP_VerbPair"))
        relations.get_relations_by_note_ids.assert_not_awaited()
        assert graph == {"nodes": [], "links": [], "label_fields": [], "label_field": "Expression"}

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
            await conn.run_sync(corpus_metadata.create_all)

    _run(setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    _run(engine.dispose())


def _seed_relations(session_factory, rows):
    async def seed():
        async with session_factory() as session:
            # 關聯存在語料庫的 rel_links（筆記 ID 存字串）
            for source_id, source, target_id, target in rows:
                await session.execute(insert(rel_links_table).values(
                    relation_type="user.synonym", src_type="note",
                    src_id=None if source_id is None else str(source_id), src_label=source,
                    dst_type="note", dst_id=None if target_id is None else str(target_id), dst_label=target,
                    created_by="user",
                ))
            await session.commit()

    _run(seed())


def _labels(session_factory):
    async def read():
        async with session_factory() as session:
            rows = (await session.execute(
                select(rel_links_table.c.src_label, rel_links_table.c.dst_label).order_by(rel_links_table.c.relation_id)
            )).all()
            return [(source, target) for source, target in rows]

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
