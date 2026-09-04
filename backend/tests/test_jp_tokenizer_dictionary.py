"""分詞詞典解析的測試（偏好完整版、缺席時回退並示警）。

Tests for tokenizer dictionary resolution: prefer the full dictionary,
fall back loudly, fail when neither is usable.

背景：``fugashi.Tagger()`` 不帶參數時會靜默在 unidic / unidic-lite 之間
自行挑選，導致同一份程式碼在不同環境產出不同的分詞結果
（2026-09-04 容器內產出的 490 張子卡有 6 張在完整版下不成立）。本測試把
「解析順序」與「回退必須示警」釘住。

Everything runs against a fake module table; no dictionary files are read.
"""

import logging
import os
import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.infrastructure.utils import jp_tokenizer


class ResolveDictionaryTest(unittest.TestCase):
    """解析順序與示警行為。Resolution order and warning behaviour."""

    def setUp(self) -> None:
        jp_tokenizer.resolve_dictionary.cache_clear()
        self.addCleanup(jp_tokenizer.resolve_dictionary.cache_clear)

    def _install(self, available: dict[str, str], populated: set[str]) -> None:
        """假造 import 與 sys.dic 存在性。Fake imports and sys.dic presence.

        Args:
            available: ``{模組名: DICDIR}``，代表可 import 的詞典套件。
            populated: 其中哪些的 ``sys.dic`` 真的存在（完整版未下載時
                套件裝了但目錄是空的）。
        """
        def fake_import(name: str):
            if name not in available:
                raise ImportError(name)
            return SimpleNamespace(DICDIR=available[name])

        def fake_isfile(path: str) -> bool:
            return os.path.dirname(path) in populated

        self.enterContext(unittest.mock.patch.object(
            jp_tokenizer.importlib, "import_module", fake_import))
        self.enterContext(unittest.mock.patch.object(
            jp_tokenizer.os.path, "isfile", fake_isfile))
        self.enterContext(unittest.mock.patch.object(
            jp_tokenizer, "_read_version", lambda dicdir: "test-version"))

    def test_prefers_full_unidic_when_downloaded(self):
        """兩本都在時取完整版，且以 INFO 記錄。Full dictionary wins."""
        self._install({"unidic": "/full", "unidic_lite": "/lite"}, {"/full", "/lite"})
        with self.assertLogs(jp_tokenizer.logger, level=logging.INFO) as logs:
            result = jp_tokenizer.resolve_dictionary()
        self.assertEqual(result.kind, jp_tokenizer.KIND_UNIDIC)
        self.assertEqual(result.dicdir, "/full")
        self.assertTrue(result.is_preferred)
        self.assertTrue(any(r.levelno == logging.INFO for r in logs.records))

    def test_falls_back_when_full_dictionary_not_downloaded(self):
        """完整版套件在、詞典沒下載 → 回退 lite 並記 WARNING。

        The package being importable is not enough; an empty DICDIR must
        fall back, and the fallback must warn.
        """
        self._install({"unidic": "/full", "unidic_lite": "/lite"}, {"/lite"})
        with self.assertLogs(jp_tokenizer.logger, level=logging.WARNING) as logs:
            result = jp_tokenizer.resolve_dictionary()
        self.assertEqual(result.kind, jp_tokenizer.KIND_UNIDIC_LITE)
        self.assertFalse(result.is_preferred)
        self.assertIn("unidic download", logs.records[0].getMessage())

    def test_falls_back_when_full_package_absent(self):
        """完整版套件根本沒裝 → 回退 lite。Missing package falls back."""
        self._install({"unidic_lite": "/lite"}, {"/lite"})
        with self.assertLogs(jp_tokenizer.logger, level=logging.WARNING):
            self.assertEqual(
                jp_tokenizer.resolve_dictionary().kind, jp_tokenizer.KIND_UNIDIC_LITE)

    def test_raises_when_no_dictionary_usable(self):
        """兩本都不可用時直接失敗——分詞是驗證的地基，不該帶錯繼續跑。

        Neither usable: fail fast rather than tokenize with nothing.
        """
        self._install({}, set())
        with self.assertRaises(RuntimeError):
            jp_tokenizer.resolve_dictionary()

    def test_result_is_cached(self):
        """解析只做一次（log 每行程一行）。Resolution is cached."""
        self._install({"unidic": "/full"}, {"/full"})
        with self.assertLogs(jp_tokenizer.logger, level=logging.INFO) as logs:
            first = jp_tokenizer.resolve_dictionary()
            second = jp_tokenizer.resolve_dictionary()
        self.assertIs(first, second)
        self.assertEqual(len(logs.records), 1)


if __name__ == "__main__":
    unittest.main()
