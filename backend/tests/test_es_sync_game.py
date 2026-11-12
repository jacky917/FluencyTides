"""ES 同步（``sync_mysql_to_es.py``）的單一遊戲模式測試（假 ES、假語料庫）。

Tests for the single-game mode of the ES sync with a fake Elasticsearch
and corpus: upsert first then delete stale documents (no empty window),
other games untouched, refusal when the index is missing, and a failure
mid-write leaving the old documents in place.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from scripts.database.Elasticsearch import sync_mysql_to_es as sync

SW, SB = "サノバウィッチ", "千恋＊万花"
PROFILES = {"SabbatOfTheWitch": SW, "SenrenBanka": SB}


class FakeES:
    """記錄每個操作順序的假 ES。A fake ES that logs every operation in order."""

    def __init__(self, docs, exists=True):
        self.docs = dict(docs)  # _id → source
        self.ops: list[tuple[str, int]] = []
        self.index_exists = exists
        self.indices = SimpleNamespace(exists=self._exists)

    async def _exists(self, index):
        return self.index_exists


class FakeHelpers:
    def __init__(self, es, fail_after=None):
        self.es, self.fail_after = es, fail_after
        self.recreated = False

    async def async_bulk(self, client, actions, raise_on_error=True):
        n = 0
        for a in actions:
            if a.get("_op_type") == "delete":
                self.es.docs.pop(int(a["_id"]), None)
                self.es.ops.append(("delete", int(a["_id"])))
            else:
                if self.fail_after is not None and len([o for o in self.es.ops if o[0] == "index"]) >= self.fail_after:
                    raise RuntimeError("bulk 寫入失敗（測試）")
                self.es.docs[a["_id"]] = a["_source"]["source"]
                self.es.ops.append(("index", a["_id"]))
            n += 1
        return n, []

    async def async_scan(self, client, index, query, _source):
        source = query["query"]["term"]["source"]
        for i, s in list(self.es.docs.items()):
            if s == source:
                yield {"_id": str(i)}


def _patch(monkeypatch, es, helpers, lines):
    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def get_game_profile(session, game_id):
        return SimpleNamespace(game_id=game_id, es_source=PROFILES[game_id])

    async def list_game_ids(session):
        return list(PROFILES)

    async def iter_lines(session, game_id):
        for lid, text in lines.get(game_id, []):
            yield SimpleNamespace(id=lid, dialogue=text)

    async def noop():
        return None

    async def recreate():
        helpers.recreated = True
        es.docs.clear()

    monkeypatch.setattr(sync, "corpus_async_session_factory", Session)
    monkeypatch.setattr(sync, "get_game_profile", get_game_profile)
    monkeypatch.setattr(sync, "list_game_ids", list_game_ids)
    monkeypatch.setattr(sync, "iter_lines", iter_lines)
    monkeypatch.setattr(sync, "get_elasticsearch_client", lambda: es)
    monkeypatch.setattr(sync, "helpers", helpers)
    monkeypatch.setattr(sync, "recreate_index", recreate)
    monkeypatch.setattr(sync, "dispose_corpus_engine", noop)
    monkeypatch.setattr(sync, "dispose_elasticsearch_client", noop)


def test_single_game_upserts_then_deletes_stale_documents(monkeypatch):
    es = FakeES({1: SW, 2: SW, 10: SB, 99: SB})
    helpers = FakeHelpers(es)
    _patch(monkeypatch, es, helpers, {"SenrenBanka": [(10, "新"), (11, "新2"), (12, "  ")]})
    asyncio.run(sync.sync_mysql_to_es("SenrenBanka"))
    assert es.docs == {1: SW, 2: SW, 10: SB, 11: SB}  # 99 已不在語料庫 → 刪掉；空白台詞不寫
    assert not helpers.recreated
    kinds = [k for k, _ in es.ops]
    assert kinds == sorted(kinds, key=lambda k: k == "delete")  # 先寫完，才刪舊文件
    assert ("delete", 1) not in es.ops and ("delete", 2) not in es.ops  # 別款遊戲不動


def test_single_game_refuses_missing_index(monkeypatch):
    es = FakeES({}, exists=False)
    helpers = FakeHelpers(es)
    _patch(monkeypatch, es, helpers, {"SenrenBanka": [(10, "新")]})
    with pytest.raises(RuntimeError, match="不存在.*全量重建"):
        asyncio.run(sync.sync_mysql_to_es("SenrenBanka"))
    assert es.ops == [] and not helpers.recreated


def test_failure_mid_write_keeps_old_documents_and_says_how_to_rerun(monkeypatch, caplog):
    monkeypatch.setattr(sync, "BATCH_SIZE", 1)
    es = FakeES({1: SW, 10: SB, 99: SB})
    helpers = FakeHelpers(es, fail_after=1)
    _patch(monkeypatch, es, helpers, {"SenrenBanka": [(10, "a"), (11, "b"), (12, "c")]})
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="bulk"):
        asyncio.run(sync.sync_mysql_to_es("SenrenBanka"))
    assert 99 in es.docs and 1 in es.docs  # 沒有空窗：舊文件還在
    assert "--game SenrenBanka" in caplog.text


def test_full_sync_recreates_the_index(monkeypatch):
    es = FakeES({99: SB})
    helpers = FakeHelpers(es)
    _patch(monkeypatch, es, helpers, {"SabbatOfTheWitch": [(1, "a")], "SenrenBanka": [(10, "b")]})
    asyncio.run(sync.sync_mysql_to_es())
    assert helpers.recreated
    assert es.docs == {1: SW, 10: SB}
