"""Speaking_Coach_Dark 身分機制的測試。

Tests for the Speaking_Coach_Dark identity mechanism.

對應計劃文件 ``docs/wip/speaking_coach_identity_FEAT_2026-08-11.md``。
共用模組 ``common/card_identity`` 本身的測試屬前置案
（``tests/test_card_identity.py``），本檔不重複，只涵蓋 **Coach 專屬**的行為：

- §3.3 決策表在本 model 上的四種狀態
- §3.3 更新模式的保護欄位是**單數**的 ``Recordings``（三語卡是三個後綴欄位，
  照抄會漏掉它而清空使用者錄音）
- §3.4 ``Target_Language`` 的預設與覆寫

Only Coach-specific behaviour is covered here; the shared ``card_identity``
module is tested by the upstream plan. The singular ``Recordings`` field is the
one most likely to be missed when copying the trilingual implementation, which
would wipe the user's recordings.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.local_anki.common.card_identity import (
    identity_state,
    load_cards,
    read_identity,
    save_cards,
)
from scripts.local_anki.Speaking_Coach_Dark import clear_identity as clear_tool
from scripts.local_anki.Speaking_Coach_Dark import import_cards as ic

MODEL = "Speaking_Coach_Dark"


def _make_card(
    card_id: str | None = None,
    note_id: int | None = None,
    target_language: str = "",
    recordings: list | None = None,
) -> dict[str, Any]:
    """產生一張最小可用的 Coach 卡片物件。

    Build a minimal usable Coach card object.

    Args:
        card_id: 要寫入的 ``cardId``；``None`` 表示不寫。The ``cardId`` to set.
        note_id: 要寫入的 ``noteId``；``None`` 表示不寫。The ``noteId`` to set.
        target_language: ``Target_Language`` 欄位值。The field value.
        recordings: ``Recordings`` 內容。The recordings list.

    Returns:
        卡片物件。The card object.
    """
    card: dict[str, Any] = {"deckName": "", "modelName": MODEL}
    if card_id is not None:
        card["cardId"] = card_id
    if note_id is not None:
        card["noteId"] = note_id
    card["tags"] = ["Test"]
    card["fields"] = {
        "Prompt": "テスト質問",
        "Prompt_Audios": [],
        "Context": "",
        "Recordings": recordings if recordings is not None else [],
        "References": [],
        "Target_Language": target_language,
    }
    return card


class _FakeNote:
    """模擬 ``AnkiNoteInfo``。Stands in for ``AnkiNoteInfo``."""

    def __init__(self, note_id: int, model_name: str, card_id: str) -> None:
        self.noteId = note_id
        self.modelName = model_name
        self.fields = {
            "Card_ID": {"value": card_id},
            "Prompt": {"value": "テスト質問"},
            "Recordings": {"value": "[]"},
        }


class _FakeClient:
    """記錄呼叫的假 AnkiConnect 客戶端。

    A fake AnkiConnect client that records calls.

    Attributes:
        notes: 模擬 Anki 中存在的 note。Notes that exist in the fake Anki.
        prompt_hits: ``find_notes`` 要回傳的 note ID。IDs returned by find_notes.
        added: 被建立的 note。Notes that were created.
        updated: 被更新的 (note_id, 欄位字典)。Updates as (note_id, fields).
    """

    def __init__(
        self,
        notes: dict[int, _FakeNote] | None = None,
        prompt_hits: list[int] | None = None,
    ) -> None:
        self.notes = notes or {}
        self.prompt_hits = prompt_hits or []
        self.added: list[Any] = []
        self.updated: list[tuple[int, dict[str, str]]] = []
        self._next_nid = 7000

    async def get_notes_info(self, note_ids=None, query=None):
        return [self.notes[nid] for nid in (note_ids or []) if nid in self.notes]

    async def find_notes(self, query: str):
        return list(self.prompt_hits)

    async def update_note_fields(self, note_id: int, fields: dict[str, str]):
        self.updated.append((note_id, dict(fields)))

    async def create_deck(self, deck: str):
        return 1

    async def add_note(self, note):
        self.added.append(note)
        self._next_nid += 1
        return self._next_nid

    async def close(self):
        return None


class TestDeckResolution(unittest.TestCase):
    """牌組推導使用 Coach 專屬的根牌組設定。

    Deck resolution uses the Coach-specific root deck setting.
    """

    def test_uses_coach_root_deck(self) -> None:
        """根牌組必須取自 ``SPEAKING_COACH_ROOT_DECK``，不可共用三語卡的設定。

        The root must come from ``SPEAKING_COACH_ROOT_DECK``, not the
        trilingual one — the two tracks must be relocatable independently.
        """
        original = ic.settings.SPEAKING_COACH_ROOT_DECK
        try:
            ic.settings.SPEAKING_COACH_ROOT_DECK = "測試根牌組"
            deck = ic.resolve_deck_name(ic.JSONS_DIR / "面接（2026-06-07）.json", "")
            self.assertEqual(deck, "測試根牌組::面接（2026-06-07）")
        finally:
            ic.settings.SPEAKING_COACH_ROOT_DECK = original


class TestResolveExistingNote(unittest.IsolatedAsyncioTestCase):
    """§3.3 決策表的四種狀態。The four states of the decision table."""

    HINT = 'clear_identity.py --name "面接（2026-06-07）" --index 2'

    async def _resolve(self, card, client, adopt=False):
        return await ic.resolve_existing_note(
            client, card, "牌組", "テスト質問", "t.json #1", adopt, self.HINT
        )

    def _assert_actionable(self, diagnostic: str) -> None:
        """診斷必須含可直接複製執行的復原指令。

        A diagnostic must carry a copy-pasteable recovery command.

        只斷言出現 ``clear_identity.py`` 是不夠的——那段文字在通用說明裡本來
        就有，即使佔位符沒接上也會通過。

        Asserting only on ``clear_identity.py`` would pass even if the
        placeholder were never interpolated.
        """
        self.assertIn(self.HINT, diagnostic)

    async def test_absent_creates_new_without_prompt_lookup(self) -> None:
        """兩者皆無 → 建新卡，且**不得**查詢 Prompt。

        Neither present -> create new, and Prompt must not be queried.
        """
        client = _FakeClient(prompt_hits=[555])
        res = await self._resolve(_make_card(), client)
        self.assertEqual((res.note_id, res.source), (None, "new"))

    async def test_absent_with_adopt_flag_takes_over(self) -> None:
        """兩者皆無 + 旗標 → 以 Prompt 接管既有卡。

        Neither present plus the flag -> adopt by Prompt.
        """
        client = _FakeClient(prompt_hits=[555])
        res = await self._resolve(_make_card(), client, adopt=True)
        self.assertEqual((res.note_id, res.source), (555, "adopted"))

    async def test_adopt_query_is_model_scoped(self) -> None:
        """接管查詢必須限定為本 model，否則會接管到三語卡。

        The adoption query must be scoped to this model; ``Prompt`` is also a
        field on Speaking_Trilingual_Dark and Anki's field search is
        model-agnostic.
        """
        seen: list[str] = []

        class _Recording(_FakeClient):
            async def find_notes(self, query: str):
                seen.append(query)
                return list(self.prompt_hits)

        await self._resolve(_make_card(), _Recording(prompt_hits=[555]), adopt=True)
        self.assertIn(f'"note:{MODEL}"', seen[0])

    async def test_complete_and_consistent_resolves(self) -> None:
        """兩者皆有且一致 → 命中。Both present and consistent -> resolved."""
        client = _FakeClient({111: _FakeNote(111, MODEL, "sc-1")})
        res = await self._resolve(_make_card("sc-1", 111), client)
        self.assertEqual((res.note_id, res.source), (111, "identity"))

    async def test_complete_but_note_missing_is_blocked(self) -> None:
        """nid 查無 → 跳過並診斷，**不得**回退到 Prompt。

        Missing nid -> blocked with a diagnostic; no Prompt fallback.
        """
        client = _FakeClient(notes={}, prompt_hits=[555])
        res = await self._resolve(_make_card("sc-1", 111), client)
        self.assertEqual((res.note_id, res.source), (None, "blocked"))
        self._assert_actionable(res.diagnostic)

    async def test_complete_but_wrong_model_is_blocked(self) -> None:
        """模型不符 → 跳過。Wrong model -> blocked."""
        client = _FakeClient({111: _FakeNote(111, "Speaking_Trilingual_Dark", "sc-1")})
        res = await self._resolve(_make_card("sc-1", 111), client)
        self.assertEqual(res.source, "blocked")
        self._assert_actionable(res.diagnostic)

    async def test_partial_identity_is_blocked_even_with_adopt(self) -> None:
        """只有其一 → 跳過；即使加了旗標也不接管。

        Only one half present -> blocked, even with the adopt flag on.
        """
        client = _FakeClient(prompt_hits=[555])
        for card in (_make_card("sc-1", None), _make_card(None, 111)):
            res = await self._resolve(card, client, adopt=True)
            self.assertEqual(res.source, "blocked")
            self._assert_actionable(res.diagnostic)


class TestCoachSpecificFields(unittest.IsolatedAsyncioTestCase):
    """Coach 專屬的欄位處理。Coach-specific field handling."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    async def test_target_language_defaults_when_empty(self) -> None:
        """``Target_Language`` 留空時填入預設值。

        An empty ``Target_Language`` is filled with the default.

        留空會讓 STT 退化為自動偵測, 評分樣板的「目標語言」硬性門檻也形同虛設,
        因此不能原樣寫入空字串。

        An empty value makes STT fall back to auto-detection and voids the
        target-language threshold in the prompt template, so it must not be
        written through as-is.
        """
        fields = await ic._normalize_fields(_FakeClient(), _make_card()["fields"])
        self.assertEqual(fields["Target_Language"], ic.DEFAULT_TARGET_LANGUAGE)

    async def test_target_language_explicit_value_wins(self) -> None:
        """JSON 有指定時以其為準。An explicit value is honoured."""
        fields = await ic._normalize_fields(
            _FakeClient(), _make_card(target_language="en-US")["fields"]
        )
        self.assertEqual(fields["Target_Language"], "en-US")

    async def test_update_protects_singular_recordings(self) -> None:
        """更新模式**不得**寫入單數的 ``Recordings``。

        Update mode must never write the singular ``Recordings`` field.

        三語卡排除的是 ``Recordings_ZH/JA/EN``；照抄那份清單會漏掉本 model 的
        單數欄位, 使用者的錄音就會被 JSON 裡的空陣列覆蓋。

        The trilingual model excludes three suffixed fields; copying that list
        verbatim would miss the singular field here and let the empty array in
        the JSON overwrite the user's recordings.
        """
        save_cards(self.path, [_make_card("sc-1", 111)])
        client = _FakeClient({111: _FakeNote(111, MODEL, "sc-1")})
        await ic.import_cards(self.path, dry_run=False, update_existing=True, client=client)

        self.assertEqual(len(client.updated), 1)
        written = set(client.updated[0][1])
        self.assertNotIn("Recordings", written)
        for f in ("Prompt_Audios", "Card_ID", "TG_Bot"):
            self.assertNotIn(f, written)
        # Target_Language 刻意**不**保護——62 張空值卡要靠更新模式補齊
        self.assertIn("Target_Language", written)
        self.assertIn("Prompt", written)

    async def test_new_card_writes_identity_back(self) -> None:
        """建卡後身分應寫回 JSON。Identity is written back after creation."""
        save_cards(self.path, [_make_card()])
        stats = await ic.import_cards(self.path, dry_run=False, client=_FakeClient())
        self.assertEqual(stats["created"], 1)
        card_id, note_id = read_identity(load_cards(self.path)[0])
        self.assertTrue(card_id and card_id.startswith("sc-"))
        self.assertEqual(note_id, 7001)

    async def test_dry_run_never_writes_file(self) -> None:
        """``--dry-run`` 不得改檔（接管路徑才會觸發寫回閘門）。

        Dry run must not modify the file; the adoption path is what exercises
        the write gate, since the new-card path never obtains a note_id.
        """
        save_cards(self.path, [_make_card()])
        before = self.path.read_bytes()
        client = _FakeClient({555: _FakeNote(555, MODEL, "sc-EXISTING")}, prompt_hits=[555])
        stats = await ic.import_cards(
            self.path, dry_run=True, adopt_by_prompt=True, client=client
        )
        self.assertEqual(stats["adopted"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_blocked_card_creates_nothing(self) -> None:
        """身分失效 → 不建卡、計入 blocked。

        A broken identity creates nothing and counts as blocked.
        """
        save_cards(self.path, [_make_card("sc-1", 111)])
        client = _FakeClient(notes={}, prompt_hits=[555])
        stats = await ic.import_cards(self.path, dry_run=False, client=client)
        self.assertEqual((stats["blocked"], stats["created"]), (1, 0))
        self.assertEqual(client.added, [])


class TestClearIdentityTool(unittest.TestCase):
    """Coach 版的身分清除工具。The Coach identity-clearing tool."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.json"
        save_cards(self.path, [_make_card("sc-1", 111), _make_card("sc-2", 222)])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_points_at_coach_jsons_dir(self) -> None:
        """``JSONS_DIR`` 必須指向 Coach 自己的目錄。

        ``JSONS_DIR`` must point at the Coach directory, not the trilingual one
        — a stale copy-paste here would clear the wrong model's identities.
        """
        self.assertEqual(clear_tool.JSONS_DIR.parent.name, "Speaking_Coach_Dark")

    def test_dry_run_does_not_write(self) -> None:
        """``--dry-run`` 不得改檔。Dry run must not modify the file."""
        before = self.path.read_bytes()
        self.assertEqual(clear_tool.clear_file(self.path, index=None, dry_run=True), 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_index_clears_only_that_card(self) -> None:
        """``--index`` 只清指定卡。``--index`` clears only that card."""
        self.assertEqual(clear_tool.clear_file(self.path, index=1, dry_run=False), 1)
        self.assertEqual(
            [identity_state(c) for c in load_cards(self.path)], ["absent", "complete"]
        )

    def test_other_content_is_byte_identical(self) -> None:
        """清除身分後其餘內容必須逐字不變。

        Everything other than the identity keys stays byte-identical.
        """
        before = [
            {k: v for k, v in c.items() if k not in ("cardId", "noteId")}
            for c in load_cards(self.path)
        ]
        clear_tool.clear_file(self.path, index=None, dry_run=False)
        self.assertEqual(load_cards(self.path), before)


if __name__ == "__main__":
    unittest.main()
