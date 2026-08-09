"""
High 級別缺陷修復的回歸測試（S002–S011）。

Regression tests for the High-severity fixes (S002–S011).

對應文件：docs/15_Bug_Scan_Report.md。每個測試類別對應一條發現，
命名中保留 S 編號以便日後追溯。

See docs/15_Bug_Scan_Report.md. Each test class maps to one finding and
keeps the S-number in its name for traceability.
"""

import asyncio
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.utils.formatting import anki_field_to_tg_text, escape_tg
from app.services.task_handlers.shared.anki_transaction import AnkiNoteTransaction

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class _DummyError(Exception):
    """測試專用例外。Test-only exception."""


class TestAnkiNoteTransactionS002S003(unittest.TestCase):
    """補償式交易行為（S002／S003）。Compensating transaction (S002/S003)."""

    def _make_service(self, fail_on_call: int | None = None) -> MagicMock:
        """建立 mock CardService，可指定第幾次 create_note 失敗。

        Build a mock CardService, optionally failing on the Nth create_note.
        """
        service = MagicMock()
        created: list[int] = []

        async def _create(**kwargs: object) -> int:
            idx = len(created) + 1
            if fail_on_call is not None and idx == fail_on_call:
                raise _DummyError("duplicate")
            note_id = 1000 + idx
            created.append(note_id)
            return note_id

        service.create_note = AsyncMock(side_effect=_create)
        service.delete_note = AsyncMock(return_value=None)
        return service

    def test_success_keeps_all_notes(self) -> None:
        """全部成功時不應觸發任何刪除。No deletions when everything succeeds."""
        service = self._make_service()

        async def _run() -> list[int]:
            async with AnkiNoteTransaction(service) as tx:
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                return list(tx.created_ids)

        ids = asyncio.run(_run())
        self.assertEqual(ids, [1001, 1002])
        service.delete_note.assert_not_called()

    def test_rollback_in_reverse_order(self) -> None:
        """子卡失敗時應反序刪除已建立的卡片。Reverse-order rollback on failure."""
        service = self._make_service(fail_on_call=3)

        async def _run() -> None:
            async with AnkiNoteTransaction(service) as tx:
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])

        with self.assertRaises(_DummyError):
            asyncio.run(_run())

        deleted = [c.args[0] for c in service.delete_note.call_args_list]
        self.assertEqual(deleted, [1002, 1001])  # 反序：子卡先於母卡

    def test_rollback_covers_post_creation_failure(self) -> None:
        """建卡後的步驟失敗（如回寫母卡）同樣觸發回滾。Post-create failures roll back too."""
        service = self._make_service()

        async def _run() -> None:
            async with AnkiNoteTransaction(service) as tx:
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                raise _DummyError("append_to_list 失敗")

        with self.assertRaises(_DummyError):
            asyncio.run(_run())
        self.assertEqual(service.delete_note.await_count, 2)

    def test_rollback_failure_does_not_mask_original(self) -> None:
        """回滾本身失敗不得遮蔽原始例外。A failing rollback never masks the original."""
        service = self._make_service(fail_on_call=2)
        service.delete_note = AsyncMock(side_effect=_DummyError("刪除失敗"))

        async def _run() -> None:
            async with AnkiNoteTransaction(service) as tx:
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])
                await tx.create_note(deck_name="d", model_name="m", fields={}, tags=[])

        with self.assertRaises(_DummyError) as ctx:
            asyncio.run(_run())
        self.assertIn("duplicate", str(ctx.exception))  # 原始例外，非「刪除失敗」


class TestTelegramFormattingS010(unittest.TestCase):
    """Telegram 文字安全化（S010）。Telegram text safety (S010)."""

    def test_strips_anki_html(self) -> None:
        """Anki 欄位的 HTML 應被移除。Anki field HTML is stripped."""
        out = anki_field_to_tg_text("<div>今日は<b>暑い</b>ですね</div>")
        self.assertEqual(out, "今日は暑いですね")

    def test_escapes_special_chars(self) -> None:
        """< 與 & 必須被轉義。< and & must be escaped."""
        out = anki_field_to_tg_text("3 &lt; 5 &amp; 7")
        self.assertIn("&lt;", out)
        self.assertIn("&amp;", out)
        self.assertNotIn("< 5", out)

    def test_unclosed_tag_cannot_break_message(self) -> None:
        """未閉合標籤不得殘留於輸出。Unclosed tags never survive."""
        out = anki_field_to_tg_text("<span style='color:red'>あ")
        self.assertEqual(out, "あ")

    def test_break_tags_become_spaces(self) -> None:
        """換行標籤轉為空白，避免字詞黏連。Break tags become spaces."""
        out = anki_field_to_tg_text("<div>foo</div><div>bar</div>")
        self.assertEqual(out, "foo bar")

    def test_truncation_before_escape(self) -> None:
        """先截斷再轉義，長度上限以原文計算。Truncate before escaping."""
        out = anki_field_to_tg_text("&" * 50, limit=10)
        self.assertTrue(out.endswith("..."))
        self.assertEqual(out.count("&amp;"), 10)

    def test_escape_tg_plain_input(self) -> None:
        """純文字轉義工具行為正確。escape_tg behaves correctly."""
        self.assertEqual(escape_tg("<b>x"), "&lt;b&gt;x")
        self.assertEqual(escape_tg(""), "")


class TestAnkiClientTimeoutS011(unittest.TestCase):
    """per-request timeout（S011）。Per-request timeout (S011)."""

    def test_sync_uses_per_request_timeout(self) -> None:
        """sync() 應以參數傳遞 timeout，且不改動共享 client 屬性。"""
        from app.infrastructure.anki.client import AnkiClient

        client = AnkiClient()
        original_timeout = client._client.timeout

        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"result": None, "error": None})

        with patch.object(
            client._client, "post", new=AsyncMock(return_value=response)
        ) as mock_post:
            asyncio.run(client.sync(force=True))

        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], client.SYNC_TIMEOUT)
        # 共享 client 的 timeout 屬性必須維持不變（競態的根因）
        self.assertEqual(client._client.timeout, original_timeout)

    def test_timeout_kwarg_not_sent_as_api_param(self) -> None:
        """_timeout 不得混入送給 AnkiConnect 的 params。"""
        from app.infrastructure.anki.client import AnkiClient

        client = AnkiClient()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json = MagicMock(return_value={"result": None, "error": None})

        with patch.object(
            client._client, "post", new=AsyncMock(return_value=response)
        ) as mock_post:
            asyncio.run(client.sync(force=True))

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["action"], "sync")
        self.assertNotIn("_timeout", payload.get("params") or {})


class TestScriptBootstrapS004S006(unittest.TestCase):
    """腳本 bootstrap 防回歸（S004–S006）。Script bootstrap guards (S004–S006)."""

    # 與深度耦合的 bootstrap 寫法，一律禁止（改用向上尋找 app/ 的片段）
    _DEPTH_PATTERNS = (
        re.compile(r"parents\[\d+\]"),
        re.compile(r"parent\.parent\.parent"),
    )
    # 硬編碼絕對路徑的 sys.path 操作
    _ABS_PATH_PATTERN = re.compile(r"sys\.path\.(insert|append)\([^)]*[A-Za-z]:[\\/]")

    def _script_files(self) -> list[Path]:
        """收集受檢的腳本檔（排除 old/ 與 __pycache__）。"""
        return [
            p
            for p in (_BACKEND_DIR / "scripts").rglob("*.py")
            if "old" not in p.parts and "__pycache__" not in p.parts
        ]

    def test_no_hardcoded_absolute_syspath(self) -> None:
        """腳本不得以絕對路徑操作 sys.path（S006）。"""
        offenders = [
            str(p.relative_to(_BACKEND_DIR))
            for p in self._script_files()
            if self._ABS_PATH_PATTERN.search(p.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [], f"發現硬編碼絕對路徑: {offenders}")

    def test_fixed_scripts_use_depth_independent_bootstrap(self) -> None:
        """本次修復的 8 個腳本不得再使用深度耦合寫法（S004–S006）。"""
        fixed = [
            "scripts/local_anki/update_tg_bot_links.py",
            "scripts/local_anki/JP_VerbPair/migrate_master_cards.py",
            "scripts/local_anki/Expression_Correction/20260620_migrate_and_update_expression.py",
            "scripts/local_anki/Expression_Correction/generate_expression_cards.py",
            "scripts/common/template_validators/speaking_coach_dark_validator.py",
            "scripts/database/MySQL/import_sql_dumps.py",
            "scripts/database/Elasticsearch/test_analyze.py",
            "scripts/database/Elasticsearch/test_esql_search.py",
        ]
        offenders: list[str] = []
        for rel in fixed:
            path = _BACKEND_DIR / rel
            self.assertTrue(path.exists(), f"檔案不存在: {rel}")
            content = path.read_text(encoding="utf-8")
            if any(pat.search(content) for pat in self._DEPTH_PATTERNS):
                offenders.append(rel)
        self.assertEqual(offenders, [], f"仍使用深度耦合 bootstrap: {offenders}")

    def test_migrate_master_cards_imports_re(self) -> None:
        """migrate_master_cards.py 必須有 import re（S004）。"""
        content = (
            _BACKEND_DIR / "scripts/local_anki/JP_VerbPair/migrate_master_cards.py"
        ).read_text(encoding="utf-8")
        self.assertRegex(content, r"(?m)^import re$")


class TestInitDbSchemaS007(unittest.TestCase):
    """DDL 與 Repository 欄位一致性（S007）。DDL/repository parity (S007)."""

    def test_ddl_contains_all_repository_columns(self) -> None:
        """init_db 的 DDL 必須涵蓋 repository 使用的所有欄位。"""
        ddl_src = (
            _BACKEND_DIR / "scripts/common/database/init_db.py"
        ).read_text(encoding="utf-8")
        for column in ("failure_count", "llm_model"):
            self.assertIn(column, ddl_src, f"DDL 缺少欄位: {column}")

    def test_backfill_helper_exists(self) -> None:
        """必須提供既有資料表的冪等欄位補齊（CREATE IF NOT EXISTS 不會補欄位）。"""
        src = (_BACKEND_DIR / "scripts/common/database/init_db.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_ensure_columns", src)
        self.assertIn("information_schema", src)


class TestBuildNlpIndexConnectionsS008(unittest.TestCase):
    """SSCursor 讀寫連線分離（S008）。Separate read/write connections (S008)."""

    def test_uses_separate_connections(self) -> None:
        """讀寫必須使用兩條獨立連線，且各自關閉。"""
        src = (
            _BACKEND_DIR
            / "scripts/database/MySQL/JP_VerbPair/build_nlp_index.py"
        ).read_text(encoding="utf-8")
        self.assertIn("read_conn = pymysql.connect", src)
        self.assertIn("write_conn = pymysql.connect", src)
        self.assertIn("read_conn.cursor(SSCursor)", src)
        self.assertIn("write_conn.cursor()", src)
        self.assertIn("read_conn.close()", src)
        self.assertIn("write_conn.close()", src)
        # 不得再於 streaming 期間 commit 讀取連線
        self.assertNotIn("read_conn.commit()", src)


class TestFrontendAuthS009(unittest.TestCase):
    """前端認證由代理層注入（S009）。Proxy-injected auth (S009)."""

    def test_nginx_template_injects_api_key(self) -> None:
        """nginx 樣板需注入 X-API-Key 且使用 envsubst 變數。"""
        template = (
            _BACKEND_DIR.parent / "frontend/nginx.conf.template"
        ).read_text(encoding="utf-8")
        self.assertIn("proxy_set_header X-API-Key", template)
        self.assertIn("${API_SECRET_KEY}", template)

    def test_frontend_client_has_no_api_key(self) -> None:
        """前端原始碼不得夾帶 API 金鑰（避免打包進 bundle）。"""
        client_src = (
            _BACKEND_DIR.parent / "frontend/src/api/client.ts"
        ).read_text(encoding="utf-8")
        # 只允許出現在說明性註解中，不得出現實際的 header 設定
        self.assertNotIn("'X-API-Key':", client_src)
        self.assertNotIn('"X-API-Key":', client_src)


if __name__ == "__main__":
    unittest.main()
