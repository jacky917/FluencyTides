"""子卡片刪除工具鏈（common/deletion）的單元測試。

Unit tests for the child-card deletion toolkit (common/deletion).

重點覆蓋（docs/archive/child_card_deletion_toolkit_FEAT_2026-08-27.md §4 測試）：
1. log_repository 的 project 驗證。
2. profiles 的 verb_lemma 抽取掛鉤。
3. media_scan 的引用蒐集與跨專案聯集保護。
4. integrity 的跨專案回歸：VerbPair 檢查不誤刪 CoreVerb 的
   DB 紀錄、Context 卡與媒體（原 1.3 連鎖誤刪情境）。
5. child_deleter 的步驟順序與回滾：deleteNotes 失敗時
   MySQL 不 commit、母卡 JSON 還原。
"""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import scripts.common.env  # noqa: F401  # 載入 .env，供 app.core.config 使用

from scripts.common.database.log_repository import (
    KNOWN_PROJECTS,
    PROJECT_JP_CORE_VERB,
    PROJECT_JP_VERB_PAIR,
    GeneratedLogRepository,
    _validate_project,
)
from scripts.local_anki.common.deletion import child_deleter as child_deleter_mod
from scripts.local_anki.common.deletion import id_deleter as id_deleter_mod
from scripts.local_anki.common.deletion import integrity as integrity_mod
from scripts.local_anki.common.deletion.media_scan import (
    collect_required_media_from_notes,
)
from scripts.local_anki.common.deletion.profiles import get_profile


# ============================================================
# 共用 Fakes
# ============================================================

class FakeResult:
    """模擬 SQLAlchemy 執行結果。Fake SQLAlchemy result."""

    def __init__(self, rows=None, scalar_value=None, rowcount=0):
        self._rows = rows or []
        self._scalar = scalar_value
        self.rowcount = rowcount
        self.lastrowid = 1

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows


class FakeSession:
    """模擬 async DB session，依 SQL 內容分流回應。

    Fake async DB session dispatching on SQL content.
    """

    def __init__(self, log_rows):
        # log_rows: list of dict(id, source, master, context, cloze, project)
        self.log_rows = log_rows
        self.executed: list[tuple[str, dict]] = []
        self.committed = 0
        self.rolled_back = 0

    async def execute(self, clause, params=None):
        sql = str(clause)
        params = params or {}
        self.executed.append((sql, params))

        if "SELECT 1 FROM" in sql:
            return FakeResult(rows=[(1,)])

        if "SELECT id, source, master_note_id" in sql:
            # 完整性檢查的主查詢必須帶 project 過濾——
            # 若未過濾（params 無 project），回傳全部列以模擬舊版錯誤行為。
            project = params.get("project")
            rows = [
                (r["id"], r["source"], r["master"], r["context"], r["cloze"])
                for r in self.log_rows
                if project is None or r["project"] == project
            ]
            return FakeResult(rows=rows)

        if sql.strip().startswith("UPDATE generated_sentences_log"):
            return FakeResult(rowcount=1)
        if sql.strip().startswith("DELETE FROM generated_sentences_log"):
            return FakeResult(rowcount=1)
        if "FROM scripts" in sql:
            return FakeResult(rows=[])
        return FakeResult()

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1


def make_session_factory(session: FakeSession):
    """把 FakeSession 包成 async context manager 工廠。

    Wrap a FakeSession in an async-context-manager factory.
    """
    @asynccontextmanager
    async def factory():
        yield session
    return factory


class FakeAnkiClient:
    """模擬 AnkiConnect 客戶端。Fake AnkiConnect client."""

    def __init__(self, notes_by_model=None, media_files=()):
        self.notes_by_model = notes_by_model or {}
        self.media_files = set(media_files)
        self.deleted_notes: list[int] = []
        self.deleted_media: list[str] = []
        self.updated_fields: dict[int, dict] = {}

    def _all_notes(self):
        return [n for lst in self.notes_by_model.values() for n in lst]

    async def _invoke(self, action, **params):
        if action == "version":
            return 6
        if action == "findNotes":
            query = params["query"]
            if query.startswith("note:"):
                model = query.split('"')[1]
                return [n["noteId"] for n in self.notes_by_model.get(model, [])]
            # 模擬 Anki 全文搜尋：比對所有筆記的原始欄位文字
            needle = query.strip('"')
            return [
                n["noteId"] for n in self._all_notes()
                if any(needle in f["value"] for f in n["fields"].values())
            ]
        if action == "notesInfo":
            ids = set(params["notes"])
            return [n for n in self._all_notes() if n["noteId"] in ids]
        if action == "getMediaFilesNames":
            return sorted(self.media_files)
        if action == "deleteMediaFile":
            self.deleted_media.append(params["filename"])
            self.media_files.discard(params["filename"])
            return None
        raise AssertionError(f"未預期的 AnkiConnect action: {action}")

    async def find_notes(self, query):
        return await self._invoke("findNotes", query=query)

    async def delete_notes(self, notes):
        self.deleted_notes.extend(notes)

    async def update_note_fields(self, note_id, fields):
        self.updated_fields[note_id] = fields

    async def close(self):
        pass


def note(note_id, model, fields):
    """組出 AnkiConnect notesInfo 形狀的筆記 dict。

    Build a note dict shaped like an AnkiConnect notesInfo entry.
    """
    return {
        "noteId": note_id,
        "modelName": model,
        "tags": [],
        "cards": [],
        "fields": {k: {"value": v, "order": i} for i, (k, v) in enumerate(fields.items())},
    }


# ============================================================
# 1. log_repository：project 驗證
# ============================================================

class TestProjectValidation:
    def test_known_projects_pass(self):
        for p in KNOWN_PROJECTS:
            _validate_project(p)  # 不應拋錯

    def test_unknown_project_raises(self):
        with pytest.raises(ValueError):
            _validate_project("jp_typo_project")

    def test_repo_methods_reject_unknown_project(self):
        repo = GeneratedLogRepository()
        session = FakeSession([])
        with pytest.raises(ValueError):
            asyncio.run(repo.get_record(session, 1, "見る", project="bad"))
        with pytest.raises(ValueError):
            asyncio.run(repo.clear_all_records(session, project="bad"))
        assert session.executed == []  # 驗證失敗時完全不碰 DB


# ============================================================
# 2. profiles：verb_lemma 抽取
# ============================================================

class TestLemmaExtraction:
    def test_verb_pair_lemma_from_cloze_json(self):
        profile = get_profile(PROJECT_JP_VERB_PAIR)
        cloze_fields = {
            "Verb_Pair_JSON": {"value": json.dumps(
                {"used": "transitive", "intransitive": "開く", "transitive": "開ける"},
                ensure_ascii=False,
            )}
        }
        assert profile.extract_verb_lemma(cloze_fields, None) == "開ける"

    def test_verb_pair_lemma_malformed_json(self):
        profile = get_profile(PROJECT_JP_VERB_PAIR)
        assert profile.extract_verb_lemma({"Verb_Pair_JSON": {"value": "{oops"}}, None) == ""

    def test_core_verb_lemma_strips_furigana_from_master_word(self):
        profile = get_profile(PROJECT_JP_CORE_VERB)
        master_fields = {"Word": {"value": "見[み]る"}}
        assert profile.extract_verb_lemma({}, master_fields) == "見る"

    def test_core_verb_lemma_without_master(self):
        profile = get_profile(PROJECT_JP_CORE_VERB)
        assert profile.extract_verb_lemma({}, None) == ""


# ============================================================
# 3. media_scan：引用蒐集
# ============================================================

class TestMediaScan:
    def test_collects_from_master_cloze_and_context(self):
        profile = get_profile(PROJECT_JP_VERB_PAIR)
        masters = [note(1, profile.master_model, {
            "Intransitive_Data_JSON": json.dumps(
                [{"audio": "G_a.mp3", "avatar": "G_p1.png",
                  "cloze_note_id": 3, "context_note_id": 2}]
            ),
            "Transitive_Data_JSON": "[]",
        })]
        clozes = [note(3, profile.cloze_model, {"Audio": "G_a.mp3", "Avatar": "G_p1.png"})]
        contexts = [note(2, profile.context_model, {
            "Dialog_JSON": json.dumps(
                [{"audio": "G_b.mp3", "avatar": "G_p2.png"},
                 {"audio": "", "avatar": "none"}]
            ),
        })]
        required = collect_required_media_from_notes(profile, masters, clozes, contexts)
        assert required == {"G_a.mp3", "G_p1.png", "G_b.mp3", "G_p2.png"}

    def test_none_placeholder_and_empty_skipped(self):
        profile = get_profile(PROJECT_JP_VERB_PAIR)
        clozes = [note(3, profile.cloze_model, {"Audio": "", "Avatar": "none"})]
        assert collect_required_media_from_notes(profile, [], clozes, []) == set()


# ============================================================
# 4. integrity：跨專案回歸（原 1.3 連鎖誤刪情境）
# ============================================================

def _build_cross_project_world():
    """建立兩專案並存的測試世界。

    Build a world where both projects have healthy data plus one orphan
    media file and one unattributable context card.
    """
    vp = get_profile(PROJECT_JP_VERB_PAIR)
    cv = get_profile(PROJECT_JP_CORE_VERB)

    vp_master = note(100, vp.master_model, {
        "Intransitive_Data_JSON": json.dumps(
            [{"audio": "SabbatOfTheWitch_vp.mp3", "avatar": "none",
              "cloze_note_id": 300, "context_note_id": 200}]
        ),
        "Transitive_Data_JSON": "[]",
    })
    cv_master = note(110, cv.master_model, {
        "Word": "見[み]る",
        "Word_Data_JSON": json.dumps(
            [{"audio": "SabbatOfTheWitch_cv.mp3", "avatar": "none",
              "cloze_note_id": 310, "context_note_id": 210}]
        ),
    })
    vp_context = note(200, vp.context_model, {
        "Master_Note_ID": "100",
        "Dialog_JSON": json.dumps([{"audio": "SabbatOfTheWitch_vp.mp3", "avatar": "none"}]),
    })
    cv_context = note(210, cv.context_model, {
        "Master_Note_ID": "110",
        "Dialog_JSON": json.dumps([{"audio": "SabbatOfTheWitch_cv.mp3", "avatar": "none"}]),
    })
    # 母卡已死且無 DB 紀錄 → 無法歸屬，應只回報不刪除
    lost_context = note(220, vp.context_model, {
        "Master_Note_ID": "999",
        "Dialog_JSON": json.dumps([{"audio": "SabbatOfTheWitch_lost.mp3", "avatar": "none"}]),
    })
    # 未註冊筆記類型引用了同前綴媒體 → 保護集合看不見，須靠全集合交叉驗證攔下
    external_note = note(900, "Speaking_Coach_Dark", {
        "Front": "練習素材 SabbatOfTheWitch_extern.mp3 參照",
    })
    vp_cloze = note(300, vp.cloze_model, {
        "Audio": "SabbatOfTheWitch_vp.mp3", "Avatar": "none",
        "Master_Note_ID": "100", "Context_Note_ID": "200",
        "Verb_Pair_JSON": json.dumps({"used": "intransitive", "intransitive": "開く"}),
    })
    cv_cloze = note(310, cv.cloze_model, {
        "Audio": "SabbatOfTheWitch_cv.mp3", "Avatar": "none",
        "Master_Note_ID": "110", "Context_Note_ID": "210",
    })

    client = FakeAnkiClient(
        notes_by_model={
            vp.master_model: [vp_master],
            cv.master_model: [cv_master],
            vp.context_model: [vp_context, cv_context, lost_context],
            vp.cloze_model: [vp_cloze],
            cv.cloze_model: [cv_cloze],
            "Speaking_Coach_Dark": [external_note],
        },
        media_files={
            "SabbatOfTheWitch_vp.mp3",
            "SabbatOfTheWitch_cv.mp3",
            "SabbatOfTheWitch_lost.mp3",
            "SabbatOfTheWitch_orphan.mp3",  # 無任何引用 → 唯一該刪的
            "SabbatOfTheWitch_extern.mp3",  # 僅未註冊筆記類型引用 → 防線攔下
            "unrelated_prefix.mp3",         # 非本前綴 → 不碰
        },
    )

    log_rows = [
        {"id": 1, "source": "SabbatOfTheWitch", "master": 100,
         "context": 200, "cloze": 300, "project": PROJECT_JP_VERB_PAIR},
        {"id": 2, "source": "SabbatOfTheWitch", "master": 110,
         "context": 210, "cloze": 310, "project": PROJECT_JP_CORE_VERB},
    ]
    return vp, cv, client, log_rows


class TestIntegrityCrossProject:
    def test_verb_pair_check_does_not_touch_core_verb_data(self, monkeypatch):
        vp, cv, client, log_rows = _build_cross_project_world()
        session = FakeSession(log_rows)
        monkeypatch.setattr(
            integrity_mod, "corpus_async_session_factory", make_session_factory(session)
        )

        total_issues = asyncio.run(
            integrity_mod.run_integrity_check(vp, is_execute=True, client=client)
        )

        # CoreVerb 的子卡與 Context 不得被刪
        assert 210 not in client.deleted_notes
        assert 310 not in client.deleted_notes
        # 無法歸屬的 Context 只回報，不刪除
        assert 220 not in client.deleted_notes
        # CoreVerb / 未歸屬卡引用中的媒體不得被刪；孤兒媒體要刪；他前綴不碰
        assert client.deleted_media == ["SabbatOfTheWitch_orphan.mp3"]
        assert "SabbatOfTheWitch_cv.mp3" in client.media_files
        assert "SabbatOfTheWitch_lost.mp3" in client.media_files
        assert "unrelated_prefix.mp3" in client.media_files
        # 未註冊筆記類型引用的同前綴檔案 → 全集合交叉驗證攔下，不刪除
        assert "SabbatOfTheWitch_extern.mp3" in client.media_files
        # DB：不得有任何軟刪除 UPDATE（兩專案各自的紀錄在自己 project 下都健康）
        soft_deletes = [
            (sql, p) for sql, p in session.executed
            if "SET is_deleted = TRUE" in sql
        ]
        assert soft_deletes == []
        # 主查詢必須帶 project 參數（跨專案過濾的回歸防線）
        main_queries = [
            (sql, p) for sql, p in session.executed
            if "SELECT id, source, master_note_id" in sql
        ]
        assert main_queries and all(
            p.get("project") == PROJECT_JP_VERB_PAIR for _, p in main_queries
        )
        # 僅有的問題應是：1 個孤兒媒體 + 1 張無法歸屬的 Context（回報）
        # + 1 個被全集合交叉驗證攔下的媒體（回報）
        assert total_issues == 3

    def test_core_verb_check_symmetric(self, monkeypatch):
        vp, cv, client, log_rows = _build_cross_project_world()
        session = FakeSession(log_rows)
        monkeypatch.setattr(
            integrity_mod, "corpus_async_session_factory", make_session_factory(session)
        )

        asyncio.run(integrity_mod.run_integrity_check(cv, is_execute=True, client=client))

        assert 200 not in client.deleted_notes
        assert 300 not in client.deleted_notes
        assert "SabbatOfTheWitch_vp.mp3" in client.media_files


# ============================================================
# 5. child_deleter：步驟順序與回滾
# ============================================================

class FakeRepo:
    """模擬 GeneratedLogRepository（僅刪卡工具用到的方法）。

    Fake GeneratedLogRepository limited to the deleter's methods.
    """

    def __init__(self):
        self.delete_calls: list[dict] = []
        self.auto_increment_resets = 0

    async def count_record_by_note_ids(self, session, m, c, x, *, project):
        return 1

    async def delete_record_by_note_ids(self, session, m, c, x, *, project, hard, commit):
        self.delete_calls.append({
            "master": m, "cloze": c, "context": x,
            "project": project, "hard": hard, "commit": commit,
        })
        return 1

    async def reset_auto_increment(self, session):
        self.auto_increment_resets += 1


class DeleterFakeAnkiClient(FakeAnkiClient):
    """刪卡工具用的 Anki fake：get_notes_info 回傳物件、可注入刪卡失敗。

    Deleter-flavoured Anki fake: object-shaped notes-info and injectable
    delete failure.
    """

    def __init__(self, notes_by_model, fail_delete=False):
        super().__init__(notes_by_model)
        self.fail_delete = fail_delete

    async def get_notes_info(self, note_ids):
        found = [n for n in self._all_notes() if n["noteId"] in set(note_ids)]
        return [
            SimpleNamespace(
                noteId=n["noteId"], modelName=n["modelName"], fields=n["fields"]
            )
            for n in found
        ]

    async def delete_notes(self, notes):
        if self.fail_delete:
            raise RuntimeError("AnkiConnect deleteNotes 失敗（測試注入）")
        await super().delete_notes(notes)


def _run_deleter(monkeypatch, *, fail_delete, allow_regen=False, dry_run=False):
    """組裝 fakes 並執行一次單母卡刪除流程。

    Assemble the fakes and run one master-scoped deletion.
    """
    vp = get_profile(PROJECT_JP_VERB_PAIR)
    master_fields_json = json.dumps(
        [{"audio": "a.mp3", "avatar": "none", "cloze_note_id": 300, "context_note_id": 200}]
    )
    client = DeleterFakeAnkiClient(
        notes_by_model={
            vp.master_model: [note(100, vp.master_model, {
                "Intransitive_Data_JSON": master_fields_json,
                "Transitive_Data_JSON": "[]",
            })],
            vp.context_model: [note(200, vp.context_model, {"Master_Note_ID": "100"})],
            vp.cloze_model: [note(300, vp.cloze_model, {"Master_Note_ID": "100"})],
        },
        fail_delete=fail_delete,
    )
    session = FakeSession([])
    repo = FakeRepo()
    removed_json_calls: list[tuple] = []
    integrity_calls: list[dict] = []

    async def fake_safe_read_list(card_service, note_id, field_name):
        if field_name == "Intransitive_Data_JSON":
            return json.loads(master_fields_json)
        return []

    async def fake_remove_from_list(card_service, note_id, field_name, index):
        removed_json_calls.append((note_id, field_name, index))
        return True

    async def fake_integrity(profile, is_execute, client=None):
        integrity_calls.append({"is_execute": is_execute})
        return 0

    async def noop():
        pass

    monkeypatch.setattr(child_deleter_mod, "AnkiClient", lambda: client)
    monkeypatch.setattr(
        child_deleter_mod, "corpus_async_session_factory", make_session_factory(session)
    )
    monkeypatch.setattr(child_deleter_mod, "dispose_corpus_engine", noop)
    monkeypatch.setattr(child_deleter_mod, "AnkiModelManager", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(child_deleter_mod, "CardService", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(child_deleter_mod, "GeneratedLogRepository", lambda: repo)
    monkeypatch.setattr(child_deleter_mod, "run_integrity_check", fake_integrity)
    monkeypatch.setattr(
        child_deleter_mod.AnkiJsonFieldManager, "safe_read_list", fake_safe_read_list
    )
    monkeypatch.setattr(
        child_deleter_mod.AnkiJsonFieldManager, "remove_from_list", fake_remove_from_list
    )

    asyncio.run(child_deleter_mod.run_child_deletion(
        vp, dry_run=dry_run, allow_regen=allow_regen, master_nid=100,
    ))
    return client, session, repo, removed_json_calls, integrity_calls


class TestChildDeleter:
    def test_success_path_commits_after_note_deletion(self, monkeypatch):
        client, session, repo, removed, integrity_calls = _run_deleter(
            monkeypatch, fail_delete=False
        )
        # 母卡 JSON 移除、DB 標記（不 commit）、刪卡、commit
        assert removed == [(100, "Intransitive_Data_JSON", 0)]
        assert repo.delete_calls == [{
            "master": 100, "cloze": 300, "context": 200,
            "project": PROJECT_JP_VERB_PAIR, "hard": False, "commit": False,
        }]
        assert client.deleted_notes == [300, 200]
        assert session.committed == 1
        assert session.rolled_back == 0
        # 事後完整性檢查以相同 dry_run 設定執行
        assert integrity_calls == [{"is_execute": True}]

    def test_allow_regen_requests_hard_delete(self, monkeypatch):
        _, _, repo, _, _ = _run_deleter(monkeypatch, fail_delete=False, allow_regen=True)
        assert repo.delete_calls[0]["hard"] is True
        # 硬刪除成功後收斂 AUTO_INCREMENT
        assert repo.auto_increment_resets == 1

    def test_soft_delete_does_not_reset_auto_increment(self, monkeypatch):
        _, _, repo, _, _ = _run_deleter(monkeypatch, fail_delete=False, allow_regen=False)
        assert repo.auto_increment_resets == 0

    def test_delete_failure_rolls_back_db_and_restores_master_json(self, monkeypatch):
        client, session, repo, removed, integrity_calls = _run_deleter(
            monkeypatch, fail_delete=True
        )
        # deleteNotes 失敗：MySQL 不得 commit、要 rollback
        assert session.committed == 0
        assert session.rolled_back == 1
        # 母卡 JSON 曾被修改 → 必須用備份還原
        assert removed == [(100, "Intransitive_Data_JSON", 0)]
        assert 100 in client.updated_fields
        restored = client.updated_fields[100]
        assert json.loads(restored["Intransitive_Data_JSON"])[0]["cloze_note_id"] == 300
        # 子卡未被刪除
        assert client.deleted_notes == []
        # 完整性檢查仍會執行（協助確認殘留狀態）
        assert integrity_calls == [{"is_execute": True}]

    def test_wrong_project_triple_is_rejected(self, monkeypatch, tmp_path):
        """把 CoreVerb 的卡片誤填進 VerbPair 的精確三元組清單 → 類型驗證拒絕。

        A CoreVerb triple pasted into the VerbPair task list must be
        rejected by the note-model validation before anything is touched.
        """
        vp = get_profile(PROJECT_JP_VERB_PAIR)
        cv = get_profile(PROJECT_JP_CORE_VERB)
        # 三張卡都存在，但全是 CoreVerb 的筆記類型
        client = DeleterFakeAnkiClient(
            notes_by_model={
                cv.master_model: [note(100, cv.master_model, {"Word_Data_JSON": "[]"})],
                cv.context_model: [note(200, cv.context_model, {"Master_Note_ID": "100"})],
                cv.cloze_model: [note(300, cv.cloze_model, {"Master_Note_ID": "100"})],
            },
        )
        session = FakeSession([])
        repo = FakeRepo()
        integrity_calls: list[dict] = []

        async def fake_integrity(profile, is_execute, client=None):
            integrity_calls.append({"is_execute": is_execute})
            return 0

        async def noop():
            pass

        monkeypatch.setattr(child_deleter_mod, "AnkiClient", lambda: client)
        monkeypatch.setattr(
            child_deleter_mod, "corpus_async_session_factory", make_session_factory(session)
        )
        monkeypatch.setattr(child_deleter_mod, "dispose_corpus_engine", noop)
        monkeypatch.setattr(child_deleter_mod, "AnkiModelManager", lambda *a, **k: SimpleNamespace())
        monkeypatch.setattr(child_deleter_mod, "CardService", lambda *a, **k: SimpleNamespace())
        monkeypatch.setattr(child_deleter_mod, "GeneratedLogRepository", lambda: repo)
        monkeypatch.setattr(child_deleter_mod, "run_integrity_check", fake_integrity)

        config = tmp_path / "delete_child_cards.json"
        config.write_text(json.dumps(
            [{"master_nid": 100, "cloze_nid": 300, "context_nid": 200}]
        ), encoding="utf-8")

        asyncio.run(child_deleter_mod.run_child_deletion(
            vp, dry_run=False, config_path=config,
        ))

        # 類型不符 → 步驟 0 拒絕：什麼都不能動
        assert client.deleted_notes == []
        assert client.updated_fields == {}
        assert repo.delete_calls == []
        assert session.committed == 0

    def test_dry_run_touches_nothing(self, monkeypatch):
        client, session, repo, removed, _ = _run_deleter(
            monkeypatch, fail_delete=False, dry_run=True
        )
        assert removed == []            # remove_from_list 未被呼叫
        assert repo.delete_calls == []  # DB 未被標記
        assert client.deleted_notes == []
        assert session.committed == 0


# ============================================================
# 6. id_deleter：以 DB id 為入口的通用刪除
# ============================================================

class TestParseIdTokens:
    def test_single_comma_and_range_mixed(self):
        assert id_deleter_mod.parse_id_tokens(["555", "600,601", "10-12"]) == \
            [10, 11, 12, 555, 600, 601]

    def test_deduplicates(self):
        assert id_deleter_mod.parse_id_tokens(["5,5", "4-6"]) == [4, 5, 6]

    def test_invalid_token_raises(self):
        with pytest.raises(ValueError):
            id_deleter_mod.parse_id_tokens(["abc"])

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError):
            id_deleter_mod.parse_id_tokens(["10-5"])


class IdLookupFakeSession:
    """回應 id_deleter 解析查詢與硬刪除的 fake session。

    Fake session answering the id_deleter resolution query and the
    db-only hard-delete statement.
    """

    def __init__(self, rows):
        # rows: (id, project, verb_lemma, is_deleted, master, context, cloze)
        self.rows = rows
        self.hard_delete_sqls: list[str] = []
        self.auto_increment_resets = 0
        self.committed = 0

    async def execute(self, clause, params=None):
        sql = str(clause)
        if "AUTO_INCREMENT" in sql:
            self.auto_increment_resets += 1
            return FakeResult()
        if "DELETE FROM generated_sentences_log" in sql:
            self.hard_delete_sqls.append(sql)
            return FakeResult(rowcount=sql.count(",") + 1)
        assert "FROM generated_sentences_log WHERE id IN" in sql
        return FakeResult(rows=self.rows)

    async def commit(self):
        self.committed += 1


class IdAnkiFakeClient:
    """id_deleter 的子卡存活檢查用 fake。Fake for child-liveness checks."""

    def __init__(self, alive_ids):
        self.alive_ids = set(alive_ids)

    async def get_notes_info(self, note_ids):
        return [
            SimpleNamespace(noteId=nid) for nid in note_ids if nid in self.alive_ids
        ]

    async def close(self):
        pass


def _run_id_deleter(monkeypatch, rows, ids, *, dry_run, allow_regen, alive_ids=()):
    """組裝 fakes 並執行 run_deletion_by_log_ids。

    Assemble fakes and run run_deletion_by_log_ids.
    """
    session = IdLookupFakeSession(rows)
    monkeypatch.setattr(
        id_deleter_mod, "corpus_async_session_factory", make_session_factory(session)
    )
    monkeypatch.setattr(id_deleter_mod, "AnkiClient", lambda: IdAnkiFakeClient(alive_ids))

    dispatched: list[dict] = []

    async def fake_run_child_deletion(profile, *, dry_run, allow_regen, tasks, **kw):
        dispatched.append({
            "project": profile.project_key,
            "dry_run": dry_run,
            "allow_regen": allow_regen,
            "tasks": tasks,
        })

    monkeypatch.setattr(id_deleter_mod, "run_child_deletion", fake_run_child_deletion)
    asyncio.run(id_deleter_mod.run_deletion_by_log_ids(
        ids, dry_run=dry_run, allow_regen=allow_regen,
    ))
    return session, dispatched


class TestRunDeletionByLogIds:
    def test_groups_by_project_and_dispatches(self, monkeypatch):
        """混合兩專案 + 查無 id → 正確分組並逐專案調用核心。

        Mixed-project rows and a missing id must be grouped/skipped
        correctly and dispatched per project.
        """
        rows = [
            (1, PROJECT_JP_VERB_PAIR, "開く", 0, 100, 200, 300),
            (2, PROJECT_JP_CORE_VERB, "見る", 0, 110, 210, 310),
        ]
        session, dispatched = _run_id_deleter(
            monkeypatch, rows, [1, 2, 999],
            dry_run=False, allow_regen=True,
            alive_ids=[200, 300, 210, 310],
        )

        assert len(dispatched) == 2
        by_project = {d["project"]: d for d in dispatched}
        assert by_project[PROJECT_JP_VERB_PAIR]["tasks"] == [
            {"master_nid": 100, "cloze_nid": 300, "context_nid": 200}
        ]
        assert by_project[PROJECT_JP_CORE_VERB]["tasks"] == [
            {"master_nid": 110, "cloze_nid": 310, "context_nid": 210}
        ]
        # 旗標透傳；卡片俱在 → 不走 DB 硬刪路徑
        assert all(d["allow_regen"] and not d["dry_run"] for d in dispatched)
        assert session.hard_delete_sqls == []

    def test_soft_mode_skips_cardless_rows(self, monkeypatch):
        """軟刪除模式：純失敗紀錄與子卡已消失的紀錄都只跳過，DB 不動。

        Under soft-delete semantics, failure-only rows and rows whose
        cards are gone are skipped; the DB is untouched.
        """
        rows = [
            (3, PROJECT_JP_VERB_PAIR, "強まる", 0, 100, None, None),   # 純失敗紀錄
            (5, PROJECT_JP_VERB_PAIR, "空く", 1, 100, 200, 300),      # 子卡已消失
        ]
        session, dispatched = _run_id_deleter(
            monkeypatch, rows, [3, 5, 999],
            dry_run=False, allow_regen=False,
            alive_ids=[],  # Anki 中無任何存活子卡
        )
        assert dispatched == []
        assert session.hard_delete_sqls == []
        assert session.committed == 0

    def test_allow_regen_hard_deletes_cardless_rows(self, monkeypatch):
        """--allow-regen：無卡可刪的紀錄直接硬刪 DB 列。

        With --allow-regen, cardless rows are hard-deleted from the DB.
        """
        rows = [
            (3, PROJECT_JP_VERB_PAIR, "強まる", 0, 100, None, None),   # 純失敗紀錄
            (5, PROJECT_JP_VERB_PAIR, "空く", 1, 100, 200, 300),      # 子卡已消失
            (6, PROJECT_JP_CORE_VERB, "見る", 0, 110, 210, 310),      # 完整卡片組
        ]
        session, dispatched = _run_id_deleter(
            monkeypatch, rows, [3, 5, 6],
            dry_run=False, allow_regen=True,
            alive_ids=[210, 310],  # 只有 id=6 的子卡存活
        )
        # 3 與 5 走 DB 硬刪；6 照常派給 CoreVerb 核心
        assert len(session.hard_delete_sqls) == 1
        assert "IN (3, 5)" in session.hard_delete_sqls[0]
        # 硬刪後收斂 AUTO_INCREMENT（DELETE commit + ALTER 的 commit）
        assert session.auto_increment_resets == 1
        assert session.committed == 2
        assert [d["project"] for d in dispatched] == [PROJECT_JP_CORE_VERB]

    def test_allow_regen_dry_run_does_not_touch_db(self, monkeypatch):
        """--allow-regen + Dry Run：只預告硬刪清單，不執行。

        Dry run with --allow-regen only previews the hard-delete list.
        """
        rows = [(3, PROJECT_JP_VERB_PAIR, "強まる", 0, 100, None, None)]
        session, dispatched = _run_id_deleter(
            monkeypatch, rows, [3],
            dry_run=True, allow_regen=True,
            alive_ids=[],
        )
        assert dispatched == []
        assert session.hard_delete_sqls == []
        assert session.committed == 0
