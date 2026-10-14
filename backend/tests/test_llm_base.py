"""``BaseLLMClient`` 抽象基底的單元測試（計畫 D2）。

Unit tests for the ``BaseLLMClient`` abstract base.

涵蓋：抽象方法強制、``SUPPORTS_EFFORT`` 檢查、model 覆寫優先序、
``model_label`` 與過渡 alias、``_retry_transient`` 的三種路徑。
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm.base import BaseLLMClient
from app.schemas.llm.base import LLMGenerateResult


class _Concrete(BaseLLMClient):
    """最小的具體實作，供基底行為測試。Minimal concrete subclass."""

    PROVIDER_NAME = "fake"

    async def generate_structured_data(self, system_prompt, user_prompt, response_schema):
        return LLMGenerateResult(
            raw_content="{}", parsed_data={}, model_name=self.model_label, attempt_count=1
        )


class _EffortAware(_Concrete):
    SUPPORTS_EFFORT = True
    MODEL_LABEL_STRIP_PREFIX = "claude-"

    def __init__(self, *, model=None, effort=None):
        super().__init__(model=model, effort=effort)
        self._effort = effort


class BaseSettingsCase(unittest.TestCase):
    def setUp(self) -> None:
        self._overrides = mock.patch.multiple(
            settings, LLM_PROVIDER="google", LLM_MODEL_NAME="gemini-3.5-flash"
        )
        self._overrides.start()
        self.addCleanup(self._overrides.stop)


class AbstractContractTests(BaseSettingsCase):
    def test_subclass_without_generate_cannot_be_instantiated(self) -> None:
        """漏實作抽象方法 → 實例化即 TypeError，不是呼叫時才炸。"""

        class Incomplete(BaseLLMClient):
            pass

        with self.assertRaises(TypeError):
            Incomplete()

    def test_concrete_subclass_instantiates(self) -> None:
        client = _Concrete()
        self.assertEqual(client.model_name, "gemini-3.5-flash")
        self.assertEqual(client.provider, "google")


class EffortSupportTests(BaseSettingsCase):
    def test_effort_rejected_when_unsupported(self) -> None:
        """SUPPORTS_EFFORT=False 給 effort → 拋錯，訊息與層級化前的工廠版本逐字相同。"""
        with self.assertRaises(LLMServiceError) as ctx:
            _Concrete(effort="high")

        self.assertEqual(
            str(ctx.exception),
            "effort 覆寫僅 claude-code provider 支援（目前 LLM_PROVIDER='google'）。",
        )

    def test_unsupported_effort_message_does_not_import_providers(self) -> None:
        """組錯誤訊息只查靜態註冊表，不得順手把 provider 模組（含 jsonschema）拉進來。"""
        import sys

        with mock.patch.dict(sys.modules):
            for name in list(sys.modules):
                if name.startswith("app.infrastructure.llm.claude_code_client") or name == "jsonschema":
                    del sys.modules[name]
            with mock.patch("importlib.import_module", side_effect=AssertionError("不得 import")):
                with self.assertRaises(LLMServiceError):
                    _Concrete(effort="high")

    def test_effort_accepted_when_supported(self) -> None:
        client = _EffortAware(effort="high")
        self.assertEqual(client.effort, "high")

    def test_no_effort_is_always_fine(self) -> None:
        self.assertIsNone(_Concrete().effort)


class ModelResolutionTests(BaseSettingsCase):
    def test_override_beats_settings(self) -> None:
        self.assertEqual(_Concrete(model="other").model_name, "other")

    def test_override_is_stripped(self) -> None:
        self.assertEqual(_Concrete(model="  padded  ").model_name, "padded")

    def test_empty_model_raises(self) -> None:
        with mock.patch.object(settings, "LLM_MODEL_NAME", ""):
            with self.assertRaises(LLMServiceError) as ctx:
                _Concrete()
        self.assertIn("LLM_MODEL_NAME", str(ctx.exception))


class LabelTests(BaseSettingsCase):
    def test_label_bare_for_google(self) -> None:
        self.assertEqual(_Concrete().model_label, "gemini-3.5-flash")

    def test_label_prefixed_with_effort_and_strip(self) -> None:
        with mock.patch.multiple(settings, LLM_PROVIDER="claude-code", LLM_MODEL_NAME="claude-opus-5"):
            client = _EffortAware(effort="medium")
        self.assertEqual(client.model_label, "(claude-code)opus-5@medium")

    def test_formatted_model_name_is_alias(self) -> None:
        client = _Concrete()
        self.assertEqual(client._formatted_model_name, client.model_label)


class RetryTransientTests(BaseSettingsCase):
    def _client(self) -> _Concrete:
        return _Concrete()

    def test_succeeds_after_transient_failures(self) -> None:
        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("503")
            return "ok"

        with mock.patch("app.infrastructure.llm.base.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            result = asyncio.run(
                self._client()._retry_transient(
                    op, is_transient=lambda e: isinstance(e, ConnectionError), max_retries=3
                )
            )
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)
        # 指數退避：1s, 2s
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1.0, 2.0])

    def test_non_transient_raises_immediately(self) -> None:
        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            raise ValueError("fatal")

        with self.assertRaises(ValueError):
            asyncio.run(
                self._client()._retry_transient(op, is_transient=lambda e: False, max_retries=3)
            )
        self.assertEqual(calls["n"], 1)

    def test_exhausted_raises_service_error(self) -> None:
        async def op():
            raise ConnectionError("503")

        with mock.patch("app.infrastructure.llm.base.asyncio.sleep", new=mock.AsyncMock()):
            with self.assertRaises(LLMServiceError) as ctx:
                asyncio.run(
                    self._client()._retry_transient(
                        op, is_transient=lambda e: True, max_retries=2
                    )
                )
        self.assertIn("2 次重試", str(ctx.exception))

    def test_operation_timeout_error_goes_through_is_transient(self) -> None:
        """operation 自己拋的 TimeoutError 不是本函式的逾時：非瞬時就原樣重拋、不重試。

        Python ≥ 3.11 的 asyncio.TimeoutError 即內建 TimeoutError，若靠型別分流會被
        誤當成 wait_for 逾時而重試。
        """
        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            raise TimeoutError("socket read timed out")

        with self.assertRaises(TimeoutError):
            asyncio.run(
                self._client()._retry_transient(
                    op, is_transient=lambda e: False, max_retries=3, timeout=None
                )
            )
        self.assertEqual(calls["n"], 1)

    def test_timeout_is_retried_then_raised(self) -> None:
        async def op():
            # 永不 set 的 Event：不用 asyncio.sleep，以免被下方的退避 sleep patch 一起短路
            await asyncio.Event().wait()

        with mock.patch("app.infrastructure.llm.base.asyncio.sleep", new=mock.AsyncMock()):
            with self.assertRaises(LLMServiceError) as ctx:
                asyncio.run(
                    self._client()._retry_transient(
                        op, is_transient=lambda e: False, max_retries=2, timeout=0.01
                    )
                )
        self.assertIn("逾時", str(ctx.exception))


class RegistryConsistencyTests(unittest.TestCase):
    """工廠註冊表的靜態旗標必須與每個類別的宣告一致（載入時核對）。"""

    def test_every_registered_provider_matches_its_class(self) -> None:
        from app.infrastructure.llm import factory
        from app.infrastructure.llm.cli_base import HeadlessCliLLMClient

        for key, entry in factory._REGISTRY.items():
            with self.subTest(provider=key):
                cls = factory.resolve_client_class(key)  # 不一致會在這裡 RuntimeError
                self.assertEqual(cls.PROVIDER_NAME, key)
                self.assertEqual(issubclass(cls, HeadlessCliLLMClient), entry.is_cli)
                self.assertEqual(cls.SUPPORTS_EFFORT, entry.supports_effort)
                self.assertEqual(factory.is_cli_provider(key), entry.is_cli)

    def test_mismatch_is_rejected_on_load(self) -> None:
        from app.infrastructure.llm import factory

        class Impostor(BaseLLMClient):
            PROVIDER_NAME = "someone-else"

            async def generate_structured_data(self, *a, **k):  # pragma: no cover
                raise NotImplementedError

        entry = factory._ProviderEntry("x", "Impostor", is_cli=True, supports_effort=True)
        with self.assertRaises(RuntimeError) as ctx:
            factory._verify_entry("claude-code", entry, Impostor)
        self.assertIn("PROVIDER_NAME", str(ctx.exception))

    def test_unregistered_provider_is_not_cli(self) -> None:
        from app.infrastructure.llm.factory import is_cli_provider

        for value in ("google", "openai", "yinli", "", None, "  Claude-Code "):
            with self.subTest(value=value):
                self.assertEqual(is_cli_provider(value), value is not None and value.strip().lower() == "claude-code")


class CliTimeoutTests(unittest.TestCase):
    """CLI 逾時每次呼叫才讀設定：/setconfig 只改 settings、不重建 singleton。"""

    def test_timeout_change_after_construction_applies_to_next_call(self) -> None:
        import subprocess
        import tempfile

        from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient

        with tempfile.TemporaryDirectory() as tmp:
            fake_cli = Path(tmp) / "claude.exe"
            fake_cli.write_text("", encoding="utf-8")
            with mock.patch.multiple(
                settings,
                LLM_PROVIDER="claude-code",
                LLM_MODEL_NAME="opus",
                LLM_CLAUDE_CODE_EFFORT="high",
                LLM_CLAUDE_CODE_CLI_PATH=str(fake_cli),
                LLM_CLAUDE_CODE_WORKDIR=str(Path(tmp) / "workdir"),
                LLM_CLAUDE_CODE_AUDIT_DIR="",
                LLM_CLAUDE_CODE_TIMEOUT_SECONDS=30.0,
                LLM_CLAUDE_CODE_OAUTH_TOKEN="",
            ):
                client = ClaudeCodeLLMClient()
                settings.LLM_CLAUDE_CODE_TIMEOUT_SECONDS = 600.0  # 模擬 /setconfig

                completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"{}", stderr=b"")
                with mock.patch.object(subprocess, "run", return_value=completed) as fake_run:
                    asyncio.run(client._invoke_cli("sys", "user", "{}"))

        self.assertEqual(fake_run.call_args.kwargs["timeout"], 600.0)


if __name__ == "__main__":
    unittest.main()
