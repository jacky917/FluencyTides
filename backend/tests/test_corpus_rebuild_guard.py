"""``rebuild.py`` 的保護：目標資料庫有還原不了的資料時拒絕，什麼都不刪。

Tests for the rebuild guard (``docs/wip/data_stores_AUDIT_2026-11-01.md`` §4：紀錄表只遷移一次，
任何腳本都不得清空). 用假連線，不碰資料庫。
"""

import re
import sys

import pytest

from scripts.database.corpus_rebuild import rebuild

ALL_TABLES = set(rebuild.DROP_ORDER)


class FakeCursor:
    """記錄 SQL、依查詢回答固定值的假 cursor。"""

    def __init__(self, conn):
        self.conn = conn
        self._one = None
        self._all = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append((sql, params))
        if "information_schema.tables" in sql:
            self._all = [(name,) for name in sorted(self.conn.tables)]
        elif sql.startswith("SELECT COUNT(*) FROM `game_profiles` WHERE game_id <>"):
            self._one = (self.conn.other_games,)
        elif sql.startswith("SELECT COUNT(*) FROM"):
            self._one = (self.conn.rows.get(re.search(r"FROM `(\w+)`", sql).group(1), 0),)
        elif sql.startswith("DROP TABLE"):
            raise AssertionError(f"不該刪表：{sql}")

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def __init__(self, tables=ALL_TABLES, rows=None, other_games=0):
        self.tables, self.rows, self.other_games = set(tables), rows or {}, other_games
        self.log = []
        self.committed = self.rolled_back = self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _found(conn: FakeConn) -> dict[str, int]:
    return rebuild.unrecoverable_data(conn.cursor(), "target_db")


def test_empty_or_missing_tables_are_fine():
    assert _found(FakeConn(tables=set())) == {}  # 全新的空資料庫
    assert _found(FakeConn()) == {}  # 表都在，但紀錄表是空的


@pytest.mark.parametrize("table", rebuild.PROTECTED_TABLES)
def test_any_row_in_a_record_table_counts(table):
    assert _found(FakeConn(rows={table: 1})) == {table: 1}


def test_content_tables_do_not_count():
    """內容表（台詞、場景…）可以從舊庫與校準檔重建，有資料不擋。"""
    assert _found(FakeConn(rows={"game_lines": 53196, "game_scenes": 900})) == {}


def test_another_games_profile_counts():
    """另外匯入的遊戲（千戀＊萬花）不是這支腳本建得回來的。"""
    assert _found(FakeConn(other_games=1)) == {"game_profiles（其他遊戲）": 1}


def test_database_name_is_passed_to_the_table_lookup():
    conn = FakeConn()
    _found(conn)
    assert conn.log[0][1] == ("target_db",)


def test_write_refuses_before_dropping_anything(monkeypatch):
    conn = FakeConn(rows={"card_gen_log": 6300, "rel_links": 37000})
    monkeypatch.setattr(rebuild, "connect", lambda database: conn)
    with pytest.raises(SystemExit, match="card_gen_log 6300 列、rel_links 37000 列"):
        rebuild.write(None, "yuzusoft", "fluencytides_corpus")  # 檢查在用到內容之前
    assert not any(sql.startswith(("DROP", "SET", "CREATE", "INSERT")) for sql, _ in conn.log)
    assert conn.rolled_back and not conn.committed and conn.closed


def test_main_refuses_whatever_the_env_says(monkeypatch):
    """.env 指向別的資料庫（換了一份 .env、在別台機器跑）也一樣擋：看的是目標資料庫裡的資料。"""
    conn = FakeConn(rows={"card_gen_log": 1})
    monkeypatch.setattr(rebuild, "connect", lambda database: conn)
    monkeypatch.setattr(rebuild.settings, "MYSQL_DATABASE", "yuzusoft")
    monkeypatch.setattr(sys, "argv", ["rebuild.py", "--new", "fluencytides_corpus"])
    monkeypatch.setattr(rebuild, "load_legacy", lambda old: pytest.fail("不該開始讀舊庫"))
    with pytest.raises(SystemExit, match="還原不了的資料"):
        rebuild.main()
    assert conn.closed


def test_main_still_refuses_the_live_database_first(monkeypatch):
    monkeypatch.setattr(rebuild, "connect", lambda database: pytest.fail("第一道檢查就該擋下，不必連線"))
    monkeypatch.setattr(rebuild.settings, "MYSQL_DATABASE", "fluencytides_corpus")
    monkeypatch.setattr(sys, "argv", ["rebuild.py"])
    with pytest.raises(SystemExit, match="已切換"):
        rebuild.main()
