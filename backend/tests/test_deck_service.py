"""牌組服務的測試（deck_create 計畫書 §7）：名稱規則與新增牌組的流程。

Tests for the deck service: the name rules and the create flow.
"""

import asyncio

import pytest

from app.core.exceptions import DeckExistsError, DeckNameInvalidError
from app.infrastructure.anki.client import AnkiConnectError
from app.services.deck_service import MAX_DECK_NAME_LENGTH, DeckService, normalize_deck_name


def _run(coro):
    return asyncio.run(coro)


class DeckAnki:
    """只有牌組與同步的假 Anki；依呼叫順序記下做了什麼。A fake Anki with decks and sync only.

    Args:
        decks: 一開始就有的牌組（名稱 → ID）。The decks to start with.
        sync_error: 同步時拋出的錯誤。Raised by ``sync``.
        create_error: 建立時拋出的錯誤。Raised by ``create_deck``.
        stored_name: Anki 實際存的名稱和送進去的不同時用。The name Anki stores instead.
        returns_existing: ``create_deck`` 不建立，直接回傳這個既有牌組的 ID（AnkiConnect 對已存在的
            牌組就是這樣）。Makes ``create_deck`` return this existing deck's ID.
        list_error_after_create: 建立之後再讀牌組清單時拋出的錯誤。Raised when listing after a create.
        hides_new_deck: 建立之後的清單裡沒有新牌組。The new deck is missing from later listings.
    """

    def __init__(
        self,
        decks=None,
        *,
        sync_error=None,
        create_error=None,
        stored_name=None,
        returns_existing=None,
        list_error_after_create=None,
        hides_new_deck=False,
    ):
        self.decks: dict[str, int] = dict(decks or {"Default": 1, "English::TOEIC": 2})
        self.sync_error = sync_error
        self.create_error = create_error
        self.stored_name = stored_name
        self.returns_existing = returns_existing
        self.list_error_after_create = list_error_after_create
        self.hides_new_deck = hides_new_deck
        self.created = False
        self.calls: list[str] = []

    async def get_deck_names_and_ids(self) -> dict[str, int]:
        self.calls.append("list")
        if self.created and self.list_error_after_create:
            raise self.list_error_after_create
        return dict(self.decks)

    async def create_deck(self, deck: str) -> int:
        self.calls.append(f"create:{deck}")
        if self.create_error:
            raise self.create_error
        self.created = True
        if self.returns_existing:
            return self.decks[self.returns_existing]
        deck_id = 1000 + len(self.decks)
        if not self.hides_new_deck:
            self.decks[self.stored_name or deck] = deck_id
        return deck_id

    async def sync(self, raise_errors: bool = False, force: bool = False) -> None:
        self.calls.append(f"sync:raise_errors={raise_errors},force={force}")
        if self.sync_error:
            raise self.sync_error

    def did(self, prefix: str) -> bool:
        """有沒有做過某一類呼叫。Whether a kind of call was made."""
        return any(call.startswith(prefix) for call in self.calls)


# =============================================================================
# normalize_deck_name
# =============================================================================


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("日本語", "日本語"),
        ("  日本語  ", "日本語"),
        ("日本語 :: 面接", "日本語::面接"),
        ("a::b::c", "a::b::c"),
        (" a ::b:: c ", "a::b::c"),
        ("全部牌組", "全部牌組"),  # 網頁的「全部牌組」以 null 表示，不衝突
        ("a:b", "a:b"),  # 單一個冒號不是分層符號
        ("中 文::with space", "中 文::with space"),  # 層內的空白保留
    ],
)
def test_normalize_keeps_valid_names(typed, expected):
    assert normalize_deck_name(typed) == expected


@pytest.mark.parametrize(
    ("typed", "message"),
    [
        ("", "牌組名稱不能是空的"),
        ("   ", "牌組名稱不能是空的"),
        ("::a", "牌組名稱的第 1 層是空的"),
        ("a::", "牌組名稱的第 2 層是空的"),
        ("a::::b", "牌組名稱的第 2 層是空的"),
        ("a::  ::b", "牌組名稱的第 2 層是空的"),
        ("::", "牌組名稱的第 1 層是空的"),
    ],
)
def test_normalize_rejects_empty_levels(typed, message):
    with pytest.raises(DeckNameInvalidError) as excinfo:
        normalize_deck_name(typed)
    assert str(excinfo.value) == message


def test_normalize_length_limit():
    assert normalize_deck_name("a" * MAX_DECK_NAME_LENGTH) == "a" * MAX_DECK_NAME_LENGTH
    with pytest.raises(DeckNameInvalidError) as excinfo:
        normalize_deck_name("a" * (MAX_DECK_NAME_LENGTH + 1))
    assert str(excinfo.value) == f"牌組名稱最長 {MAX_DECK_NAME_LENGTH} 字（目前 {MAX_DECK_NAME_LENGTH + 1} 字）"


def test_normalize_length_counts_after_trimming():
    """長度以整理後的全名計算：前後的空白不算。"""
    padded = "  " + "a" * MAX_DECK_NAME_LENGTH + "  "
    assert normalize_deck_name(padded) == "a" * MAX_DECK_NAME_LENGTH


# =============================================================================
# DeckService.create
# =============================================================================


def test_create_creates_then_syncs():
    anki = DeckAnki()
    result = _run(DeckService(anki).create("日本語 :: 面接"))

    assert result == {"deck_name": "日本語::面接", "deck_id": anki.decks["日本語::面接"], "sync_warning": None}
    # 契約是順序：先查有沒有同名的，用整理後的名稱建立，建好才同步並等它完成
    create = anki.calls.index("create:日本語::面接")
    sync = anki.calls.index("sync:raise_errors=True,force=True")
    assert anki.calls[0] == "list"
    assert 0 < create < sync


def test_create_rejects_invalid_name_before_touching_anki():
    anki = DeckAnki()
    with pytest.raises(DeckNameInvalidError):
        _run(DeckService(anki).create("a::"))
    assert anki.calls == []


def test_create_rejects_existing_deck():
    anki = DeckAnki()
    with pytest.raises(DeckExistsError) as excinfo:
        _run(DeckService(anki).create(" English :: TOEIC "))
    assert str(excinfo.value) == "牌組「English::TOEIC」已存在"
    assert not anki.did("create") and not anki.did("sync")


def test_create_treats_case_difference_as_existing():
    """Anki 的牌組名不分大小寫：只差大小寫時是同一個牌組，訊息用 Anki 裡的寫法。"""
    anki = DeckAnki()
    with pytest.raises(DeckExistsError) as excinfo:
        _run(DeckService(anki).create("english::toeic"))
    assert str(excinfo.value) == "牌組「English::TOEIC」已存在"


def test_create_treats_unicode_forms_as_existing():
    """同一個字的兩種 Unicode 寫法（é 一個字元，或 e 加上重音符號）是同一個牌組。"""
    anki = DeckAnki({"Café": 7})
    with pytest.raises(DeckExistsError) as excinfo:
        _run(DeckService(anki).create("Café"))
    assert str(excinfo.value) == "牌組「Café」已存在"
    assert not anki.did("create")


def test_create_reports_existing_when_anki_returns_an_old_id():
    """AnkiConnect 對已存在的牌組不報錯、回傳舊 id：我們的比對沒擋到時，也不能當成建立成功。"""
    anki = DeckAnki(returns_existing="English::TOEIC")
    with pytest.raises(DeckExistsError) as excinfo:
        _run(DeckService(anki).create("Anki 認為和 TOEIC 同名的名稱"))
    assert str(excinfo.value) == "牌組「English::TOEIC」已存在"
    assert not anki.did("sync")  # 什麼都沒建，不同步


def test_create_allows_child_of_existing_deck():
    anki = DeckAnki()
    result = _run(DeckService(anki).create("English::TOEIC::Part5"))
    assert result["deck_name"] == "English::TOEIC::Part5"


def test_create_returns_the_name_anki_stored():
    """Anki 調整了名稱（這裡是把 e＋重音符號存成單一字元 é）時，回傳它實際存的名稱：前端用它選取新牌組。"""
    anki = DeckAnki(stored_name="Café")
    result = _run(DeckService(anki).create("Café"))
    assert result["deck_name"] == "Café"


def test_create_succeeds_when_reading_the_name_back_fails():
    """牌組已建好，之後讀清單失敗：不能回報成建立失敗；改用送出的名稱，照常同步。"""
    anki = DeckAnki(list_error_after_create=AnkiConnectError("Anki 沒有回應"))
    result = _run(DeckService(anki).create("新牌組"))

    assert result["deck_name"] == "新牌組"
    assert result["sync_warning"] is None
    assert anki.did("sync")


def test_create_falls_back_to_the_sent_name_when_the_deck_is_not_listed():
    anki = DeckAnki(hides_new_deck=True)
    result = _run(DeckService(anki).create(" 新牌組 "))
    assert result["deck_name"] == "新牌組"
    assert anki.did("sync")


def test_create_sync_failure_is_a_warning():
    anki = DeckAnki(sync_error=AnkiConnectError("AnkiWeb 連線逾時"))
    result = _run(DeckService(anki).create("新牌組"))

    assert result["deck_name"] == "新牌組"
    assert "新牌組" in anki.decks  # 牌組仍已建立
    assert result["sync_warning"] == "⚠️ 已寫入 Anki，但同步 AnkiWeb 失敗；下次操作會再試。"


def test_create_propagates_anki_errors():
    anki = DeckAnki(create_error=AnkiConnectError("Anki 拒絕這個名稱"))
    with pytest.raises(AnkiConnectError):
        _run(DeckService(anki).create("新牌組"))
    assert not anki.did("sync")  # 沒建成就不同步
