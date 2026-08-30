"""claude-code LLM Provider 的單元測試。

Unit tests for the claude-code LLM provider.

對應計劃文件 ``docs/wip/claude_code_llm_provider_FEAT_2026-08-27.md`` §4 的
測試清單。全部以假 subprocess 驅動，不實際呼叫 claude CLI、不消耗訂閱額度。

Covers the test checklist in §4 of the plan document. Everything runs against
a fake subprocess; the real claude CLI is never invoked and no subscription
quota is consumed.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm import claude_code_client as ccc
from app.infrastructure.llm.anthropic_client import AnthropicLLMClient
from app.infrastructure.llm.claude_code_client import ClaudeCodeLLMClient
from app.infrastructure.llm.client import LLMClient
from app.infrastructure.llm.factory import create_llm_client

# 測試用的最小 schema（帶 $defs，用於驗證展平行為）
NESTED_SCHEMA: dict[str, Any] = {
    "$defs": {
        "Inner": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }
    },
    "type": "object",
    "properties": {"inner": {"$ref": "#/$defs/Inner"}},
    "required": ["inner"],
}

VALID_PAYLOAD = {"inner": {"value": "ok"}}


def _envelope(**overrides: Any) -> str:
    """組出一份 CLI 的 ``--output-format json`` 信封。

    Build a CLI ``--output-format json`` envelope.

    Args:
        **overrides: 要覆蓋的信封欄位。Envelope fields to override.

    Returns:
        序列化後的信封 JSON 字串。The serialized envelope JSON.
    """
    envelope: dict[str, Any] = {
        "is_error": False,
        "result": json.dumps(VALID_PAYLOAD, ensure_ascii=False),
        "terminal_reason": "completed",
        "duration_ms": 1234,
        "num_turns": 2,
    }
    envelope.update(overrides)
    return json.dumps(envelope, ensure_ascii=False)


class _FakeCompleted:
    """模擬 ``subprocess.run`` 的回傳物件。Fake CompletedProcess."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")
        self.returncode = returncode


class ClaudeCodeClientTestBase(unittest.TestCase):
    """共用設定：以暫存目錄與假 CLI 路徑建立 client。

    Shared setup: build the client against a temp dir and a fake CLI path.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        fake_cli = tmp_path / "claude.exe"
        fake_cli.write_text("", encoding="utf-8")

        self._overrides = mock.patch.multiple(
            settings,
            LLM_PROVIDER="claude-code",
            LLM_MODEL_NAME="opus",
            LLM_CLAUDE_CODE_EFFORT="high",
            LLM_CLAUDE_CODE_CLI_PATH=str(fake_cli),
            LLM_CLAUDE_CODE_WORKDIR=str(tmp_path / "workdir"),
            LLM_CLAUDE_CODE_AUDIT_DIR="",  # 預設關閉審計，個別測試自行開啟
            LLM_CLAUDE_CODE_TIMEOUT_SECONDS=30.0,
            # 釘為空＝桌機剔除模式:測試不得受本機 .env 是否設了真實 token 影響
            LLM_CLAUDE_CODE_OAUTH_TOKEN="",
        )
        self._overrides.start()
        self.addCleanup(self._overrides.stop)
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = tmp_path

    def _run(self, client: ClaudeCodeLLMClient, **kwargs: Any) -> Any:
        """執行 ``generate_structured_data`` 的同步包裝。

        Synchronous wrapper around ``generate_structured_data``.
        """
        return asyncio.run(
            client.generate_structured_data(
                system_prompt=kwargs.get("system_prompt", "你是測試用的助手。"),
                user_prompt=kwargs.get("user_prompt", "請輸出測試資料。"),
                response_schema=kwargs.get("response_schema", NESTED_SCHEMA),
            )
        )


class HappyPathTests(ClaudeCodeClientTestBase):
    """成功路徑與標籤格式。Happy path and label format."""

    def test_returns_parsed_result(self) -> None:
        """合法信封 + 合法 JSON → 正確組裝 LLMGenerateResult。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_envelope())):
            result = self._run(client)

        self.assertEqual(result.parsed_data, VALID_PAYLOAD)
        self.assertEqual(result.attempt_count, 1)

    def test_model_label_includes_provider_and_effort(self) -> None:
        """標籤格式為 ``(provider)model@effort``（計劃 D7）。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_envelope())):
            result = self._run(client)

        self.assertEqual(result.model_name, "(claude-code)opus@high")

    def test_model_label_strips_claude_prefix(self) -> None:
        """完整模型 id 的 ``claude-`` 前綴在標籤中去除，``--model`` 保留全名。

        The ``claude-`` prefix is stripped from the label while the full
        model id is still passed to ``--model``.
        """
        with mock.patch.object(settings, "LLM_MODEL_NAME", "claude-opus-5"):
            client = ClaudeCodeLLMClient()
            with mock.patch.object(
                subprocess, "run", return_value=_FakeCompleted(_envelope())
            ) as run_mock:
                result = self._run(client)

        self.assertEqual(result.model_name, "(claude-code)opus-5@high")
        cmd = run_mock.call_args[0][0]
        self.assertIn("claude-opus-5", cmd)  # --model 用完整 id

    def test_strips_markdown_fences(self) -> None:
        """result 被 ```json 圍欄包住時仍能解析。"""
        fenced = "```json\n" + json.dumps(VALID_PAYLOAD) + "\n```"
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(_envelope(result=fenced))
        ):
            result = self._run(client)

        self.assertEqual(result.parsed_data, VALID_PAYLOAD)

    def test_envelope_tolerates_leading_noise(self) -> None:
        """信封前有雜訊行時仍能取出 JSON。"""
        noisy = "Warning: something\n" + _envelope()
        client = ClaudeCodeLLMClient()
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(noisy)):
            result = self._run(client)

        self.assertEqual(result.parsed_data, VALID_PAYLOAD)

    def test_envelope_tolerates_trailing_output(self) -> None:
        """信封後有尾隨輸出時仍能取出 JSON（raw_decode 只吃第一個物件）。"""
        trailing = _envelope() + "\nsome trailing chatter\n"
        client = ClaudeCodeLLMClient()
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(trailing)):
            result = self._run(client)

        self.assertEqual(result.parsed_data, VALID_PAYLOAD)

    def test_fatal_markers_are_not_matched_in_generated_content(self) -> None:
        """生成內容含 stderr 專屬標記字串時不得誤判為致命錯誤。

        實測確認 ``unrecognized_model`` 只會出現在 stderr；若連 stdout 一起
        掃描，卡片內容剛好含此字串就會被錯殺。
        """
        payload = {"inner": {"value": "討論 unrecognized_model 這個字串"}}
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess,
            "run",
            return_value=_FakeCompleted(_envelope(result=json.dumps(payload, ensure_ascii=False))),
        ):
            result = self._run(client)

        self.assertEqual(result.parsed_data, payload)


class CommandConstructionTests(ClaudeCodeClientTestBase):
    """命令列組裝、schema 展平、環境剔除。Command, schema, env."""

    def _capture_command(self) -> list[str]:
        """執行一次生成並回傳實際送出的命令列。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(_envelope())
        ) as fake_run:
            self._run(client)
        return fake_run.call_args.args[0]

    def test_command_contains_isolation_flags(self) -> None:
        """定案旗標組合齊備（計劃 D4）。"""
        command = self._capture_command()

        self.assertIn("-p", command)
        self.assertIn("--safe-mode", command)
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--output-format") + 1], "json")

    def test_model_and_effort_are_explicit(self) -> None:
        """model 與 effort 均明寫，不依賴 CLI 隱式預設（計劃 G2）。"""
        command = self._capture_command()

        self.assertEqual(command[command.index("--model") + 1], "opus")
        self.assertEqual(command[command.index("--effort") + 1], "high")

    def test_schema_is_flattened_before_sending(self) -> None:
        """送出的 schema 已展平 $defs/$ref（計劃 D4，A5 實測必要）。"""
        command = self._capture_command()
        sent_schema = json.loads(command[command.index("--json-schema") + 1])

        self.assertNotIn("$defs", json.dumps(sent_schema))
        self.assertNotIn("$ref", json.dumps(sent_schema))
        # 展平後 inner 應直接帶有 properties
        self.assertIn("value", sent_schema["properties"]["inner"]["properties"])

    def test_system_prompt_passed_as_flag(self) -> None:
        """system prompt 走 --system-prompt 而非拼進 user prompt。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(_envelope())
        ) as fake_run:
            self._run(client, system_prompt="ROLE-MARKER")

        command = fake_run.call_args.args[0]
        self.assertEqual(command[command.index("--system-prompt") + 1], "ROLE-MARKER")

    def test_user_prompt_piped_as_utf8_stdin(self) -> None:
        """user prompt 以 UTF-8 bytes 經 stdin 傳入（計劃 D5，A11 實測)。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(_envelope())
        ) as fake_run:
            self._run(client, user_prompt="日本語のテスト")

        self.assertEqual(
            fake_run.call_args.kwargs["input"], "日本語のテスト".encode("utf-8")
        )

    def test_auth_env_vars_are_scrubbed(self) -> None:
        """subprocess env 剔除認證干擾變數（計劃 D3，A12 實測)。"""
        client = ClaudeCodeLLMClient()
        polluted = {
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-stale",
            "ANTHROPIC_API_KEY": "sk-ant-api03-stale",
            "ANTHROPIC_BASE_URL": "https://example.invalid",
            "PATH": "/usr/bin",
        }
        with mock.patch.dict(ccc.os.environ, polluted, clear=True):
            with mock.patch.object(
                subprocess, "run", return_value=_FakeCompleted(_envelope())
            ) as fake_run:
                self._run(client)

        env = fake_run.call_args.kwargs["env"]
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertIn("PATH", env)  # 其餘環境變數保留


class FailureTriageTests(ClaudeCodeClientTestBase):
    """失敗分流（計劃 D6 表格逐列）。Failure triage per the D6 table."""

    def test_not_logged_in_raises_auth_error(self) -> None:
        """未認證 → 拋錯且訊息附 auth login 教學，不重試。"""
        envelope = _envelope(
            is_error=True, result="Not logged in · Please run /login", terminal_reason="api_error"
        )
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(envelope)
        ) as fake_run:
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("claude auth login", str(ctx.exception))
        self.assertEqual(fake_run.call_count, 1)  # 致命錯誤不重試

    def test_invalid_oauth_token_raises_auth_error(self) -> None:
        """壞 token 的 401 → 同樣視為認證失效。"""
        envelope = _envelope(
            is_error=True,
            result="Failed to authenticate. API Error: 401 OAuth access token is invalid.",
        )
        client = ClaudeCodeLLMClient()
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(envelope)):
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("claude auth login", str(ctx.exception))

    def test_unrecognized_model_raises_config_error(self) -> None:
        """模型名錯誤 → 設定錯誤,不重試。"""
        client = ClaudeCodeLLMClient()
        fake = _FakeCompleted(
            stdout=_envelope(is_error=True, result=""),
            stderr='[claude-code:unrecognized_model] {"model":"bogus"}',
        )
        with mock.patch.object(subprocess, "run", return_value=fake) as fake_run:
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("LLM_MODEL_NAME", str(ctx.exception))
        self.assertEqual(fake_run.call_count, 1)

    def test_no_access_model_fails_fast_without_retry(self) -> None:
        """模型存在但無存取權 → 設定錯誤,立即拋出不重試。

        此形態與「模型名打錯」不同:CLI 不給 unrecognized_model,而是回一段
        api_error 訊息(實測 claude-mythos-5)。若當成一般錯誤會白白重試。
        """
        envelope = _envelope(
            is_error=True,
            result=(
                "There's an issue with the selected model (claude-mythos-5). "
                "It may not exist or you may not have access to it."
            ),
            terminal_reason="api_error",
        )
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(envelope)
        ) as fake_run:
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("LLM_MODEL_NAME", str(ctx.exception))
        self.assertEqual(fake_run.call_count, 1)  # 不得重試

    def test_quota_error_message_contains_quota_marker(self) -> None:
        """額度耗盡 → 訊息含 'Quota',供腳本走暫停 60 秒路徑。"""
        envelope = _envelope(
            is_error=True, result="You have exceeded your usage limit", terminal_reason="api_error"
        )
        client = ClaudeCodeLLMClient()
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(envelope)):
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("Quota", str(ctx.exception))

    def test_structured_output_exhausted_retries_then_fails(self) -> None:
        """CLI 結構化重試耗盡 → 外層重試,全敗後訊息含腳本識別字串。"""
        envelope = _envelope(
            is_error=True,
            result=None,
            terminal_reason="structured_output_retry_exhausted",
            errors=["Failed to provide valid structured output after 5 attempts"],
        )
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(envelope)
        ) as fake_run:
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("LLM API 在所有重試後仍回傳空內容", str(ctx.exception))
        self.assertEqual(fake_run.call_count, ClaudeCodeLLMClient.MAX_RETRIES)

    def test_timeout_raises_service_error(self) -> None:
        """超時 → 進程被 kill 並拋 LLMServiceError。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30)
        ):
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("超時", str(ctx.exception))

    def test_missing_envelope_raises(self) -> None:
        """stdout 無 JSON 信封 → 明確報錯。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess, "run", return_value=_FakeCompleted(stdout="", stderr="boom")
        ):
            with self.assertRaises(LLMServiceError) as ctx:
                self._run(client)

        self.assertIn("未回傳 JSON 信封", str(ctx.exception))


class RepairRetryTests(ClaudeCodeClientTestBase):
    """帶錯誤回饋的修復重試。Repair retry with error feedback."""

    def test_schema_violation_retries_with_feedback_then_succeeds(self) -> None:
        """第一次 schema 不符 → 第二次 prompt 帶回饋 → 成功。"""
        bad = _envelope(result=json.dumps({"inner": {"wrong_key": 1}}))
        good = _envelope()
        client = ClaudeCodeLLMClient()

        with mock.patch.object(
            subprocess, "run", side_effect=[_FakeCompleted(bad), _FakeCompleted(good)]
        ) as fake_run:
            result = self._run(client, user_prompt="ORIGINAL-PROMPT")

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(fake_run.call_count, 2)

        second_stdin = fake_run.call_args_list[1].kwargs["input"].decode("utf-8")
        self.assertIn("ORIGINAL-PROMPT", second_stdin)
        self.assertIn("上次輸出未通過驗證", second_stdin)

    def test_invalid_json_retries_with_feedback(self) -> None:
        """第一次非法 JSON → 第二次帶回饋 → 成功。"""
        client = ClaudeCodeLLMClient()
        with mock.patch.object(
            subprocess,
            "run",
            side_effect=[_FakeCompleted(_envelope(result="not json at all")), _FakeCompleted(_envelope())],
        ) as fake_run:
            result = self._run(client)

        self.assertEqual(result.attempt_count, 2)
        second_stdin = fake_run.call_args_list[1].kwargs["input"].decode("utf-8")
        self.assertIn("上次輸出未通過驗證", second_stdin)


class InitializationTests(ClaudeCodeClientTestBase):
    """建構子驗證:effort 白名單與 CLI 探測。Constructor validation."""

    def test_invalid_effort_rejected_at_init(self) -> None:
        """非法 effort → 初始化即拋錯（CLI 會靜默回退,故必須攔在此)。"""
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_EFFORT", "ultra"):
            with self.assertRaises(LLMServiceError) as ctx:
                ClaudeCodeLLMClient()

        self.assertIn("LLM_CLAUDE_CODE_EFFORT", str(ctx.exception))

    def test_all_whitelisted_efforts_accepted(self) -> None:
        """白名單內的五個值皆可初始化。"""
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                with mock.patch.object(settings, "LLM_CLAUDE_CODE_EFFORT", effort):
                    self.assertEqual(ClaudeCodeLLMClient()._effort, effort)

    def test_configured_cli_path_must_exist(self) -> None:
        """設定的 CLI 路徑不存在 → 明確報錯。"""
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_CLI_PATH", str(self.tmp_path / "nope.exe")):
            with self.assertRaises(LLMServiceError) as ctx:
                ClaudeCodeLLMClient()

        self.assertIn("不存在", str(ctx.exception))

    def test_probe_fails_with_install_hint(self) -> None:
        """全部探測失敗 → 報錯附安裝教學。"""
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_CLI_PATH", ""), \
             mock.patch.object(ccc.Path, "home", return_value=self.tmp_path / "nohome"), \
             mock.patch.object(ccc.shutil, "which", return_value=None), \
             mock.patch.dict(ccc.os.environ, {}, clear=True):
            with self.assertRaises(LLMServiceError) as ctx:
                ClaudeCodeLLMClient()

        self.assertIn("找不到 claude 執行檔", str(ctx.exception))

    def test_workdir_is_created(self) -> None:
        """工作目錄不存在時自動建立。"""
        client = ClaudeCodeLLMClient()
        self.assertTrue(Path(client._workdir).is_dir())


class AuditTests(ClaudeCodeClientTestBase):
    """審計落盤。Audit persistence."""

    def test_audit_files_written(self) -> None:
        """啟用審計時落盤 prompt/answer/meta 三檔。"""
        audit_dir = self.tmp_path / "audit"
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_AUDIT_DIR", str(audit_dir)):
            client = ClaudeCodeLLMClient()
            with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_envelope())):
                self._run(client, system_prompt="SYS", user_prompt="USER")

        call_dirs = list(audit_dir.iterdir())
        self.assertEqual(len(call_dirs), 1)

        prompt_text = (call_dirs[0] / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("SYS", prompt_text)
        self.assertIn("USER", prompt_text)

        meta = json.loads((call_dirs[0] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["model_label"], "(claude-code)opus@high")
        self.assertEqual(meta["effort"], "high")

    def test_audit_disabled_writes_nothing(self) -> None:
        """審計目錄設為空字串時完全不落盤。"""
        client = ClaudeCodeLLMClient()
        self.assertIsNone(client._audit_dir)


class FactoryTests(unittest.TestCase):
    """工廠分流:API 模式必須零影響。Factory routing; API mode untouched."""

    def test_returns_llm_client_for_default_providers(self) -> None:
        """非 claude-code 的 provider 一律回傳原本的 LLMClient。"""
        for provider in ("google", "openai", "yinli", ""):
            with self.subTest(provider=provider):
                with mock.patch.object(settings, "LLM_PROVIDER", provider), \
                     mock.patch.object(ccc, "ClaudeCodeLLMClient") as never_used, \
                     mock.patch("app.infrastructure.llm.factory.LLMClient") as fake_llm:
                    client = create_llm_client()

                self.assertIs(client, fake_llm.return_value)
                never_used.assert_not_called()

    def test_returns_claude_code_client_when_selected(self) -> None:
        """provider=claude-code 時回傳 ClaudeCodeLLMClient。"""
        with mock.patch.object(settings, "LLM_PROVIDER", "claude-code"), \
             mock.patch.object(ccc, "ClaudeCodeLLMClient") as fake_cc:
            client = create_llm_client()

        self.assertIs(client, fake_cc.return_value)

    def test_provider_value_is_case_and_space_insensitive(self) -> None:
        """provider 值容忍大小寫與前後空白。"""
        with mock.patch.object(settings, "LLM_PROVIDER", "  Claude-Code  "), \
             mock.patch.object(ccc, "ClaudeCodeLLMClient") as fake_cc:
            client = create_llm_client()

        self.assertIs(client, fake_cc.return_value)

    def test_anthropic_stub_raises_clear_not_implemented_error(self) -> None:
        """provider=anthropic 目前為佔位:必須明確報錯,不得靜默降級。"""
        with mock.patch.object(settings, "LLM_PROVIDER", "anthropic"):
            with self.assertRaises(LLMServiceError) as ctx:
                create_llm_client()

        message = str(ctx.exception)
        self.assertIn("尚未實作", message)
        self.assertIn("claude-code", message)  # 指引使用者改用可用的 provider

    def test_anthropic_stub_rejects_direct_generate_call(self) -> None:
        """繞過建構子時 generate_structured_data 仍須擋下。"""
        stub = AnthropicLLMClient.__new__(AnthropicLLMClient)  # 跳過 __init__
        with self.assertRaises(LLMServiceError):
            asyncio.run(stub.generate_structured_data("sys", "user", {}))

    def test_claude_code_module_not_imported_in_api_mode(self) -> None:
        """API 模式不得於 import 期載入 claude-code provider。

        provider 模組依賴 ``jsonschema``；若在 import 期即載入，未安裝該套件
        的既有 API 模式部署會連應用都啟動不了（違反計劃 D10 的零影響保證）。
        因此 factory 對它採惰性 import，dependencies 只以 TYPE_CHECKING 引用。
        """
        import ast

        provider_modules = ("claude_code_client", "anthropic_client")
        for module_path in (
            _BACKEND_DIR / "app" / "infrastructure" / "llm" / "factory.py",
            _BACKEND_DIR / "app" / "core" / "dependencies.py",
        ):
            with self.subTest(module=module_path.name):
                tree = ast.parse(module_path.read_text(encoding="utf-8"))
                # 收集所有「模組層級」（非函式內、非 TYPE_CHECKING 區塊內）的 import
                top_level_imports: list[str] = []
                for node in tree.body:
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        top_level_imports.append(ast.unparse(node))

                offenders = [
                    line
                    for line in top_level_imports
                    if any(name in line for name in provider_modules)
                ]
                self.assertFalse(
                    offenders,
                    f"{module_path.name} 於模組層級直接 import 了 provider 模組: {offenders}",
                )

    def test_api_mode_does_not_read_claude_settings(self) -> None:
        """API 模式下即使 claude 專屬設定非法,也不影響建立 LLMClient。"""
        with mock.patch.object(settings, "LLM_PROVIDER", "google"), \
             mock.patch.object(settings, "LLM_CLAUDE_CODE_EFFORT", "totally-invalid"), \
             mock.patch("app.infrastructure.llm.factory.LLMClient") as fake_llm:
            client = create_llm_client()

        self.assertIs(client, fake_llm.return_value)


class SchemaResolverReuseTests(unittest.TestCase):
    """複用 LLMClient 的 schema 展平器,且免實例化。Resolver reuse."""

    def test_resolver_is_classmethod_usable_without_instance(self) -> None:
        """``_resolve_json_schema`` 可在不建立 LLMClient 的情況下呼叫。

        這保證 claude-code 模式下無需設定 LLM_API_KEY / LLM_BASE_URL
        （``LLMClient.__init__`` 會在缺少它們時拋錯）。
        """
        resolved = LLMClient._resolve_json_schema(NESTED_SCHEMA)

        self.assertNotIn("$defs", resolved)
        self.assertEqual(
            resolved["properties"]["inner"]["properties"]["value"]["type"], "string"
        )


class BuildEnvTests(ClaudeCodeClientTestBase):
    """_build_env 的認證模式分流（桌機剔除 vs headless 注入）。

    Credential-mode branching of _build_env: desktop scrub vs headless
    injection (docs/wip/claude_cli_in_container_FEAT_2026-08-29.md §D2).
    """

    def test_desktop_mode_scrubs_all_auth_vars(self) -> None:
        """token 未設定（預設）：環境殘留的認證變數全數剔除——行為與改動前一致。"""
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", ""):
            client = ClaudeCodeLLMClient()
            with mock.patch.dict(os.environ, {
                "CLAUDE_CODE_OAUTH_TOKEN": "stale-bad-token",
                "ANTHROPIC_API_KEY": "leftover",
                "ANTHROPIC_BASE_URL": "http://x",
            }):
                env = client._build_env()
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_headless_mode_injects_configured_token(self) -> None:
        """token 已設定（容器）：注入設定值，並蓋掉環境殘留的舊值。"""
        with mock.patch.object(
            settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-container"
        ):
            client = ClaudeCodeLLMClient()
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "stale-bad-token"}):
                env = client._build_env()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat-container")

    def test_headless_mode_still_scrubs_anthropic_vars(self) -> None:
        """headless 模式下 ANTHROPIC_* 衛生剔除不受影響。"""
        with mock.patch.object(
            settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-container"
        ):
            client = ClaudeCodeLLMClient()
            with mock.patch.dict(os.environ, {
                "ANTHROPIC_API_KEY": "leftover",
                "ANTHROPIC_BASE_URL": "http://x",
            }):
                env = client._build_env()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_whitespace_token_treated_as_unset(self) -> None:
        """空白字串視同未設定：仍走桌機剔除模式。"""
        with mock.patch.object(settings, "LLM_CLAUDE_CODE_OAUTH_TOKEN", "   "):
            client = ClaudeCodeLLMClient()
            with mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "stale"}):
                env = client._build_env()
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)


if __name__ == "__main__":
    unittest.main()
