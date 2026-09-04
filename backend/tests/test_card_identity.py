"""卡片身分證機制的測試（``cardId`` + ``noteId``）。

Tests for the card identity mechanism (``cardId`` + ``noteId``).

對應計劃文件 ``docs/archive/card_identity_writeback_FEAT_2026-08-11.md`` §3.2 的
決策表與 §3.5 的清除工具。全部以假 AnkiClient 驅動，不連 Anki。

Covers the decision table in §3.2 and the clearing tool in §3.5 of the plan
document. Everything runs against a fake AnkiClient; no Anki connection.
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

from scripts.local_anki.common import clear_audio_fields as clear_audio
from scripts.local_anki.Speaking_Trilingual_Dark import clear_identity as clear_tool
from scripts.local_anki.Speaking_Trilingual_Dark import import_cards as ic
from scripts.local_anki.common.card_identity import (
    clear_identity,
    identity_state,
    load_cards,
    read_identity,
    save_cards,
    set_identity,
)

MODEL = "Speaking_Trilingual_Dark"


def _make_card(card_id: str | None = None, note_id: int | None = None) -> dict[str, Any]:
    """產生一張最小可用的卡片物件。

    Build a minimal usable card object.

    Args:
        card_id: 要寫入的 ``cardId``；``None`` 表示不寫。The ``cardId`` to set;
            ``None`` omits it.
        note_id: 要寫入的 ``noteId``；``None`` 表示不寫。The ``noteId`` to set;
            ``None`` omits it.

    Returns:
        卡片物件。The card object.
    """
    card: dict[str, Any] = {"deckName": "", "modelName": MODEL}
    if card_id is not None:
        card["cardId"] = card_id
    if note_id is not None:
        card["noteId"] = note_id
    card["tags"] = ["Test"]
    card["fields"] = {"Prompt": "測試提示", "Context": "", "References_JA": []}
    return card


class _FakeNote:
    """模擬 ``AnkiNoteInfo``。Stands in for ``AnkiNoteInfo``."""

    def __init__(self, note_id: int, model_name: str, card_id: str) -> None:
        self.noteId = note_id
        self.modelName = model_name
        self.fields = {"Card_ID": {"value": card_id}, "Prompt": {"value": "測試提示"}}


class _FakeClient:
    """記錄呼叫的假 AnkiConnect 客戶端。

    A fake AnkiConnect client that records calls.

    Attributes:
        notes: 模擬 Anki 中存在的 note（nid → _FakeNote）。Notes that exist in
            the fake Anki, keyed by note ID.
        prompt_hits: ``find_notes`` 對 Prompt 查詢要回傳的 note ID。Note IDs
            that ``find_notes`` should return for Prompt queries.
        added: 被建立的 note。Notes that were created.
        updated: 被更新的 (note_id, 欄位名集合)。Updates as (note_id, field names).
    """

    def __init__(
        self,
        notes: dict[int, _FakeNote] | None = None,
        prompt_hits: list[int] | None = None,
    ) -> None:
        self.notes = notes or {}
        self.prompt_hits = prompt_hits or []
        self.added: list[Any] = []
        self.updated: list[tuple[int, set[str]]] = []
        self._next_nid = 9000

    async def get_notes_info(self, note_ids=None, query=None):
        return [self.notes[nid] for nid in (note_ids or []) if nid in self.notes]

    async def find_notes(self, query: str):
        return list(self.prompt_hits)

    async def update_note_fields(self, note_id: int, fields: dict[str, str]):
        self.updated.append((note_id, set(fields)))

    async def create_deck(self, deck: str):
        return 1

    async def add_note(self, note):
        self.added.append(note)
        self._next_nid += 1
        return self._next_nid

    async def close(self):
        return None


class TestIdentityHelpers(unittest.TestCase):
    """``card_identity`` 基礎行為。Basic behaviour of ``card_identity``."""

    def test_identity_state(self) -> None:
        """三種身分完整度應正確分類。Classify the three completeness states."""
        self.assertEqual(identity_state(_make_card("st-1", 111)), "complete")
        self.assertEqual(identity_state(_make_card()), "absent")
        self.assertEqual(identity_state(_make_card("st-1", None)), "partial")
        self.assertEqual(identity_state(_make_card(None, 111)), "partial")

    def test_note_id_accepts_string_digits(self) -> None:
        """從 Anki 複製貼上的字串 nid 應被正規化為 int。

        A string nid pasted from Anki is normalised to int.
        """
        card = _make_card("st-1", None)
        card["noteId"] = "1786354356058"
        self.assertEqual(read_identity(card), ("st-1", 1786354356058))
        self.assertEqual(identity_state(card), "complete")

    def test_bool_note_id_rejected(self) -> None:
        """``bool`` 是 ``int`` 的子類，不可被當成合法 nid。

        ``bool`` subclasses ``int`` and must not pass as a valid nid.
        """
        card = _make_card("st-1", None)
        card["noteId"] = True
        self.assertIsNone(read_identity(card)[1])

    def test_set_identity_is_idempotent_and_ordered(self) -> None:
        """重複寫入同值不算變更；鍵應排到 modelName 之後。

        Rewriting the same value is not a change; keys land after modelName.
        """
        card = _make_card()
        self.assertTrue(set_identity(card, "st-1", 111))
        self.assertFalse(set_identity(card, "st-1", 111))
        self.assertEqual(
            list(card), ["deckName", "modelName", "cardId", "noteId", "tags", "fields"]
        )

    def test_reorder_keeps_unknown_keys(self) -> None:
        """使用者自加的鍵不得在重排時被丟棄。

        User-added keys must survive reordering.
        """
        card = _make_card()
        card["myNote"] = "keep me"
        set_identity(card, "st-1", 111)
        self.assertEqual(card["myNote"], "keep me")

    def test_clear_identity(self) -> None:
        """清除後回到 absent；已無身分時回傳 False。

        Clearing returns the card to absent; a second call reports no change.
        """
        card = _make_card("st-1", 111)
        self.assertTrue(clear_identity(card))
        self.assertEqual(identity_state(card), "absent")
        self.assertFalse(clear_identity(card))

    def test_save_cards_roundtrip_and_format(self) -> None:
        """寫檔格式應為 indent=2、非 ASCII 不轉義、結尾換行。

        Files are written with indent=2, unescaped non-ASCII, trailing newline.
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            save_cards(path, [_make_card("st-1", 111)])
            raw = path.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("}\n]\n"))
            self.assertIn("測試提示", raw)  # 未被 \uXXXX 轉義
            self.assertIn('\n  {\n    "deckName"', raw)  # indent=2
            self.assertEqual(read_identity(load_cards(path)[0]), ("st-1", 111))


class TestResolveExistingNote(unittest.IsolatedAsyncioTestCase):
    """§3.2 決策表的四種狀態。The four states of the §3.2 decision table."""

    HINT = 'clear_identity.py --name "日本語面接/Q社/逆質問" --index 2'

    async def _resolve(self, card, client, adopt=False):
        return await ic.resolve_existing_note(
            client, card, "牌組", "測試提示", "t.json #1", adopt, self.HINT
        )

    def _assert_actionable(self, diagnostic: str) -> None:
        """診斷必須含可直接複製執行的復原指令。

        A diagnostic must carry a copy-pasteable recovery command.

        只斷言出現 ``clear_identity.py`` 是不夠的——那段文字原本就在通用說明裡，
        即使 ``recovery_hint`` 未被代入也會通過。必須連 ``--name`` / ``--index``
        一起檢查，才能抓到「佔位符沒接上」這種靜默失效。

        Asserting only on ``clear_identity.py`` is not enough: that string already
        appears in the generic wording and would pass even if ``recovery_hint``
        were never interpolated. Checking for ``--name`` / ``--index`` too is what
        catches a placeholder that was never wired up.
        """
        self.assertIn(self.HINT, diagnostic)

    async def test_absent_creates_new_without_prompt_lookup(self) -> None:
        """兩者皆無 → 建新卡，且**不得**查詢 Prompt。

        Neither present -> create new, and Prompt must not be queried.
        """
        client = _FakeClient(prompt_hits=[555])  # 若誤查 Prompt 就會命中這個
        res = await self._resolve(_make_card(), client)
        self.assertEqual((res.note_id, res.source), (None, "new"))
        self.assertIsNone(res.diagnostic)

    async def test_absent_with_adopt_flag_takes_over(self) -> None:
        """兩者皆無 + 旗標 → 以 Prompt 接管既有卡。

        Neither present plus the flag -> adopt the existing note by Prompt.
        """
        client = _FakeClient(prompt_hits=[555])
        res = await self._resolve(_make_card(), client, adopt=True)
        self.assertEqual((res.note_id, res.source), (555, "adopted"))

    async def test_complete_and_consistent_resolves(self) -> None:
        """兩者皆有且一致 → 命中。Both present and consistent -> resolved."""
        client = _FakeClient({111: _FakeNote(111, MODEL, "st-1")})
        res = await self._resolve(_make_card("st-1", 111), client)
        self.assertEqual((res.note_id, res.source), (111, "identity"))

    async def test_complete_but_note_missing_is_blocked(self) -> None:
        """nid 查無 → 跳過並診斷，**不得**回退到 Prompt。

        Missing nid -> blocked with a diagnostic; no Prompt fallback.
        """
        client = _FakeClient(notes={}, prompt_hits=[555])
        res = await self._resolve(_make_card("st-1", 111), client)
        self.assertEqual((res.note_id, res.source), (None, "blocked"))
        self.assertIn("查無此 note", res.diagnostic)
        self._assert_actionable(res.diagnostic)

    async def test_complete_but_card_id_mismatch_is_blocked(self) -> None:
        """Card_ID 不符 → 跳過並診斷。Mismatched Card_ID -> blocked."""
        client = _FakeClient({111: _FakeNote(111, MODEL, "st-OTHER")}, prompt_hits=[555])
        res = await self._resolve(_make_card("st-1", 111), client)
        self.assertEqual(res.source, "blocked")
        self.assertIn("st-OTHER", res.diagnostic)
        self._assert_actionable(res.diagnostic)

    async def test_complete_but_wrong_model_is_blocked(self) -> None:
        """模型不符 → 跳過並診斷。Wrong model -> blocked."""
        client = _FakeClient({111: _FakeNote(111, "Other_Model", "st-1")})
        res = await self._resolve(_make_card("st-1", 111), client)
        self.assertEqual(res.source, "blocked")
        self.assertIn("模型", res.diagnostic)
        self._assert_actionable(res.diagnostic)

    async def test_adopt_blocks_on_multiple_candidates(self) -> None:
        """接管命中多張時必須跳過，不可任選一張。

        Adoption must stop when several notes match, not pick one arbitrarily.

        同牌組同 ``Prompt`` 的重複卡正是本計劃的成因；任選一張會讓另一張上的
        錄音變成孤兒，正是 G3 要防止的損失。

        Duplicates sharing a deck and ``Prompt`` are what this plan exists to
        fix; picking one would strand the other's recordings — the loss G3 was
        written to prevent.
        """
        client = _FakeClient(prompt_hits=[555, 556])
        res = await self._resolve(_make_card(), client, adopt=True)
        self.assertEqual(res.source, "blocked")
        self.assertIn("555", res.diagnostic)
        self.assertIn("556", res.diagnostic)

    async def test_adopt_query_is_model_scoped(self) -> None:
        """接管查詢必須限定模型，否則會接管到別種卡片。

        The adoption query must be model-scoped or it can bind a wrong-model note.
        """
        seen: list[str] = []

        class _RecordingClient(_FakeClient):
            async def find_notes(self, query: str):
                seen.append(query)
                return list(self.prompt_hits)

        await self._resolve(_make_card(), _RecordingClient(prompt_hits=[555]), adopt=True)
        self.assertIn(f'"note:{MODEL}"', seen[0])

    async def test_partial_identity_is_blocked_even_with_adopt(self) -> None:
        """只有其一 → 跳過；即使加了旗標也不接管。

        Only one half present -> blocked, even with the adopt flag on.
        """
        client = _FakeClient(prompt_hits=[555])
        for card in (_make_card("st-1", None), _make_card(None, 111)):
            res = await self._resolve(card, client, adopt=True)
            self.assertEqual(res.source, "blocked")
            self.assertIn("身分不完整", res.diagnostic)
            self._assert_actionable(res.diagnostic)


class TestImportWriteBack(unittest.IsolatedAsyncioTestCase):
    """匯入流程的身分寫回與檔案安全。Identity write-back and file safety."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, cards) -> None:
        self.path.write_text(
            json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    async def test_new_card_writes_identity_back(self) -> None:
        """建卡後身分應寫回 JSON。Identity is written back after creation."""
        self._write([_make_card()])
        client = _FakeClient()
        stats = await ic.import_cards(self.path, dry_run=False, client=client)

        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["identity_written"], 1)
        card_id, note_id = read_identity(load_cards(self.path)[0])
        self.assertTrue(card_id and card_id.startswith("st-"))
        self.assertEqual(note_id, 9001)

    async def test_dry_run_never_writes_file(self) -> None:
        """``--dry-run`` 不得改檔。Dry run must not modify the file."""
        self._write([_make_card()])
        before = self.path.read_bytes()
        stats = await ic.import_cards(self.path, dry_run=True, client=_FakeClient())
        self.assertEqual(stats["created"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_dry_run_never_uploads_media(self) -> None:
        """``--dry-run`` 不得上傳媒體到 Anki。

        Dry run must not upload media to Anki.

        媒體上傳發生在 ``_normalize_fields``, 而它在 dry_run 判斷**之前**執行,
        因此上傳需要自己的閘門 —— 否則 `--help` 宣稱的「不寫入 Anki」不成立。

        Uploads happen in ``_normalize_fields``, which runs *before* the dry_run
        check, so the upload needs its own gate — otherwise the "writes nothing
        to Anki" promise in ``--help`` does not hold.
        """
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "hello.ogg"
            media.write_bytes(b"fake audio")
            path = Path(tmp) / "t.json"
            card = _make_card()
            card["fields"]["Prompt_Audios"] = [
                {"lang": "JA", "audio": str(media), "speaker": "S", "avatar": ""}
            ]
            save_cards(path, [card])

            uploads: list[str] = []

            class _UploadRecordingClient(_FakeClient):
                async def _invoke(self, action, **kwargs):
                    uploads.append(action)
                    return None

            await ic.import_cards(path, dry_run=True, client=_UploadRecordingClient())
            self.assertEqual(uploads, [])

    async def test_dry_run_with_adoption_never_writes_file(self) -> None:
        """接管路徑在 ``--dry-run`` 下同樣不得改檔。

        The adoption path must also leave the file untouched under dry run.

        這條與上一條不可合併：新卡路徑在 dry-run 時根本取不到 note_id，
        因此不會觸發身分寫回；唯有接管路徑會在 dry-run 中算出完整身分，
        是真正能驗證「寫檔閘門」的案例。

        This cannot be merged with the previous test: the new-card path never
        obtains a note_id under dry run, so it never reaches the write-back.
        Only the adoption path produces a complete identity during a dry run,
        making it the case that genuinely exercises the write gate.
        """
        self._write([_make_card()])
        before = self.path.read_bytes()
        client = _FakeClient({555: _FakeNote(555, MODEL, "st-EXISTING")}, prompt_hits=[555])
        stats = await ic.import_cards(
            self.path, dry_run=True, adopt_by_prompt=True, client=client
        )
        self.assertEqual(stats["adopted"], 1)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_second_run_is_idempotent(self) -> None:
        """第二次執行不應改檔（身分已存在且一致）。

        A second run leaves the file untouched.
        """
        self._write([_make_card()])
        await ic.import_cards(self.path, dry_run=False, client=_FakeClient())
        after_first = self.path.read_bytes()

        card_id, note_id = read_identity(load_cards(self.path)[0])
        client = _FakeClient({note_id: _FakeNote(note_id, MODEL, card_id)})
        stats = await ic.import_cards(self.path, dry_run=False, client=client)

        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["identity_written"], 0)
        self.assertEqual(self.path.read_bytes(), after_first)

    async def test_blocked_card_is_not_created_and_file_untouched(self) -> None:
        """身分失效 → 不建卡、不改檔、計入 blocked。

        A broken identity creates nothing and leaves the file untouched.
        """
        self._write([_make_card("st-1", 111)])
        before = self.path.read_bytes()
        client = _FakeClient(notes={}, prompt_hits=[555])
        stats = await ic.import_cards(self.path, dry_run=False, client=client)

        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["created"], 0)
        self.assertEqual(client.added, [])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_adopt_writes_back_anki_card_id(self) -> None:
        """接管時應收編 Anki 端的 Card_ID，而非新生成的。

        Adoption stores Anki's Card_ID, not a freshly generated one.
        """
        self._write([_make_card()])
        client = _FakeClient({555: _FakeNote(555, MODEL, "st-EXISTING")}, prompt_hits=[555])
        stats = await ic.import_cards(
            self.path, dry_run=False, adopt_by_prompt=True, client=client
        )

        self.assertEqual(stats["adopted"], 1)
        self.assertEqual(client.added, [])
        self.assertEqual(read_identity(load_cards(self.path)[0]), ("st-EXISTING", 555))

    async def test_identity_survives_midway_exception(self) -> None:
        """迴圈中途拋例外時，已建卡片的身分仍須落地。

        Identities of already-created cards must reach disk even if the loop
        raises midway.

        否則那些卡片在 Anki 存在、在 JSON 卻沒有身分，下次重跑會再建一次，
        正是本設計要消滅的重複卡問題。

        Otherwise those cards exist in Anki but have no identity in the JSON,
        so the next run duplicates them — the very problem this design removes.
        """
        self._write([_make_card(), _make_card()])

        # 注入點選 create_deck：add_note / update_note_fields 的例外在迴圈內就被
        # 接住並計為 failed，不會中斷整批；create_deck 等呼叫則會往外拋。
        # Inject at create_deck: exceptions from add_note / update_note_fields are
        # caught inside the loop and counted as failures, while create_deck and
        # friends propagate and abort the batch.
        class _FailOnSecondDeck(_FakeClient):
            async def create_deck(self, deck: str):
                if self.added:
                    raise ConnectionError("模擬連線中斷")
                return await super().create_deck(deck)

        with self.assertRaises(ConnectionError):
            await ic.import_cards(self.path, dry_run=False, client=_FailOnSecondDeck())

        states = [identity_state(c) for c in load_cards(self.path)]
        self.assertEqual(states, ["complete", "absent"])

    async def test_update_excludes_user_data_fields(self) -> None:
        """更新模式不得寫入 Recordings / Prompt_Audios / Card_ID / TG_Bot。

        Update mode must never write user-data fields.
        """
        self._write([_make_card("st-1", 111)])
        client = _FakeClient({111: _FakeNote(111, MODEL, "st-1")})
        await ic.import_cards(
            self.path, dry_run=False, update_existing=True, client=client
        )

        self.assertEqual(len(client.updated), 1)
        written = client.updated[0][1]
        protected = {
            "Prompt_Audios", "Recordings_ZH", "Recordings_JA", "Recordings_EN",
            "Card_ID", "TG_Bot",
        }
        self.assertEqual(written & protected, set())
        self.assertIn("Prompt", written)


class TestMediaPathPreservation(unittest.IsolatedAsyncioTestCase):
    """手寫的絕對素材路徑不得被身分寫回破壞。

    Hand-written absolute media paths must survive the identity write-back.
    """

    async def test_absolute_media_paths_survive_import(self) -> None:
        """匯入後 JSON 內的絕對路徑必須原封不動。

        Absolute paths in the JSON must be untouched after an import.

        ``_process_media_paths`` 會把絕對路徑就地改寫成純檔名（供 Anki 使用）。
        若傳入的是 ``cards_data`` 裡的原 dict，身分寫回時就會把使用者手寫的素材
        路徑一併覆寫——而 ``jsons/`` 未進版控，那是唯一的一份。

        ``_process_media_paths`` rewrites absolute paths to bare filenames in
        place for Anki's benefit. Passing the original dict from ``cards_data``
        would make the identity write-back overwrite the user's hand-written
        media paths — and ``jsons/`` is git-ignored, so that is the only copy.
        """
        with tempfile.TemporaryDirectory() as tmp:
            media = Path(tmp) / "hello.ogg"
            media.write_bytes(b"fake audio")
            path = Path(tmp) / "t.json"

            card = _make_card()
            card["fields"]["Prompt_Audios"] = [
                {"lang": "JA", "audio": str(media), "speaker": "S", "avatar": ""}
            ]
            save_cards(path, [card])

            await ic.import_cards(path, dry_run=False, client=_FakeClient())

            stored = load_cards(path)[0]["fields"]["Prompt_Audios"][0]["audio"]
            self.assertEqual(stored, str(media))


class TestClearAudioFields(unittest.TestCase):
    """S065 第三個呼叫點：兩種儲存格式與巢狀結構都要處理。

    The third S065 call site: both stored formats and the nested shape.
    """

    def test_both_stored_formats_yield_filenames(self) -> None:
        """轉義與未轉義的 ``Recordings_*`` 都要能取出檔名。

        Both escaped and unescaped ``Recordings_*`` yield filenames.
        """
        escaped = "[{&quot;date&quot;: &quot;2026-07-18&quot;, &quot;audio&quot;: &quot;a.ogg&quot;}]"
        raw = '[{"date": "2026-07-18", "audio": "b.ogg"}]'
        self.assertEqual(clear_audio.extract_audio_from_json_field("Recordings_JA", escaped)[0], ["a.ogg"])
        self.assertEqual(clear_audio.extract_audio_from_json_field("Recordings_JA", raw)[0], ["b.ogg"])

    def test_nested_reference_audios_are_found(self) -> None:
        """``References_*`` 的音檔藏在 ``audios`` 子陣列，必須遞迴取出。

        ``References_*`` nest audio in an ``audios`` sub-list; recurse into it.
        """
        refs = json.dumps(
            [{"date": "2026-08-10", "content": "x", "status": 1,
              "audios": [{"audio": "ref.mp3", "speaker": "S"}]}],
            ensure_ascii=False,
        )
        files, is_audio, must_skip = clear_audio.extract_audio_from_json_field("References_JA", refs)
        self.assertEqual(files, ["ref.mp3"])
        self.assertTrue(is_audio)
        self.assertFalse(must_skip)

    def test_corrupted_field_signals_skip(self) -> None:
        """損毀欄位要回報「整個跳過」，不可與「不是 JSON 陣列」混為一談。

        A corrupted field must signal "skip entirely", distinct from "not a
        JSON array" — otherwise the caller rewrites it while logging an error.
        """
        files, is_audio, must_skip = clear_audio.extract_audio_from_json_field("Recordings_JA", "[{bad")
        self.assertEqual((files, is_audio, must_skip), ([], False, True))

    def test_empty_field_is_not_a_skip(self) -> None:
        """空欄位不是損毀，應允許呼叫端往下走標籤路徑。

        An empty field is not corruption; the caller may still try sound tags.
        """
        self.assertEqual(clear_audio.extract_audio_from_json_field("X", "")[2], False)
        self.assertEqual(clear_audio.extract_audio_from_json_field("X", "[]")[2], False)


class TestClearIdentityTool(unittest.TestCase):
    """§3.5 身分清除工具。The §3.5 identity-clearing tool."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.json"
        save_cards(
            self.path,
            [_make_card("st-1", 111), _make_card("st-2", 222), _make_card("st-3", 333)],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_dry_run_does_not_write(self) -> None:
        """``--dry-run`` 不得改檔。Dry run must not modify the file."""
        before = self.path.read_bytes()
        cleared = clear_tool.clear_file(self.path, index=None, dry_run=True)
        self.assertEqual(cleared, 3)
        self.assertEqual(self.path.read_bytes(), before)

    def test_index_clears_only_that_card(self) -> None:
        """``--index`` 只清指定卡，其餘身分保留。

        ``--index`` clears only that card and leaves the others intact.
        """
        self.assertEqual(clear_tool.clear_file(self.path, index=2, dry_run=False), 1)
        states = [identity_state(c) for c in load_cards(self.path)]
        self.assertEqual(states, ["complete", "absent", "complete"])

    def test_other_content_is_byte_identical(self) -> None:
        """清除身分後，其餘內容必須逐字不變。

        Everything other than the identity keys stays byte-identical.
        """
        before = [
            {k: v for k, v in c.items() if k not in ("cardId", "noteId")}
            for c in load_cards(self.path)
        ]
        clear_tool.clear_file(self.path, index=None, dry_run=False)
        self.assertEqual(load_cards(self.path), before)

    def test_out_of_range_index_raises(self) -> None:
        """``--index`` 超出範圍應明確報錯，而非靜默無動作。

        An out-of-range ``--index`` raises instead of silently doing nothing.
        """
        with self.assertRaises(IndexError):
            clear_tool.clear_file(self.path, index=99, dry_run=False)


if __name__ == "__main__":
    unittest.main()
