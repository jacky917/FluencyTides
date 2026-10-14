"""antigravity LLM Provider 的單元測試（計畫 §4 測試 / P3）。

Unit tests for the antigravity LLM provider.

全部以假 subprocess 驅動，不實際呼叫 agy、不消耗額度。信封樣本依 2026-10-14
實測的原始輸出結構簡化（計畫書 ``llm_provider_hierarchy_FEAT_2026-09-06.md``
§D8「實測紀錄」）：欄位與形態相同，工具清單、usage 數字與內容換成測試值。
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.core.exceptions import (
    LLMQuotaError,
    LLMRetryExhaustedError,
    LLMServiceError,
    LLMServiceUnavailableError,
)
from app.infrastructure.llm import cli_base, factory
from app.infrastructure.llm.antigravity_client import AntigravityLLMClient
from app.services.runtime_config_service import RuntimeConfigService

_SKIP_IDENTIFIER = "LLM API 在所有重試後仍回傳空內容"
_ELIGIBILITY_503 = "Eligibility check failed: UNAVAILABLE (code 503): The service is currently unavailable."

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

_DROP = object()  # 覆寫成 _DROP 代表刪掉該欄位

_USAGE = {"input_tokens": 15048, "output_tokens": 2, "thinking_tokens": 0, "cache_read_tokens": 0, "total_tokens": 15050}


def _result_line(**overrides: Any) -> str:
    """組出 stream-json 的 ``event=result`` 行（預設為成功且帶 structured_output）。"""
    result: dict[str, Any] = {
        "conversation_id": "9694daa1-57cd-4e71-ac96-bdd3a346d32d",
        "status": "SUCCESS",
        # response 刻意放散文加額外欄位：實測就是這樣，客戶端不得讀它
        "response": "Done.\n{\"toolAction\":\"Finishing task\"}\n",
        "duration_seconds": 2.43,
        "num_turns": 1,
        "structured_output": VALID_PAYLOAD,
        "usage": _USAGE,
    }
    result.update(overrides)
    for key in [k for k, v in result.items() if v is _DROP]:
        del result[key]
    return json.dumps({"event": "result", "result": result}, ensure_ascii=False)


def _stream(*result_overrides: dict[str, Any], prefix_events: bool = True) -> str:
    """組出完整的 stream-json stdout：init → step_update… → result。"""
    lines: list[str] = []
    if prefix_events:
        lines += [
            json.dumps({"event": "init", "conversation_id": "x", "init": {"model": "gemini-3.8-flash", "tools": ["finish"], "permission_mode": "request-review"}}),
            json.dumps({"event": "step_update", "step_update": {"step_index": 0, "state": "DONE", "step_type": "user_input"}}),
            json.dumps({"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "finish"}}),
        ]
    lines.append(_result_line(**(result_overrides[0] if result_overrides else {})))
    return "\n".join(lines) + "\n"


def _error_stream(error: str) -> str:
    """實測的 stream-json 錯誤形態：只有一行 result，status=ERROR，沒有 structured_output。"""
    return _stream({
        "conversation_id": "", "status": "ERROR", "response": "", "error": error,
        "duration_seconds": 0, "num_turns": 0, "structured_output": _DROP,
    }, prefix_events=False)


class _FakeCompleted:
    """模擬 ``subprocess.run`` 的回傳物件。Fake CompletedProcess."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout.encode("utf-8")
        self.stderr = stderr.encode("utf-8")
        self.returncode = returncode


class AntigravityClientTestBase(unittest.TestCase):
    """共用設定：以暫存目錄與假 CLI 路徑建立 client，並以局部替換短路暫時性失敗的退避等待。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)
        fake_cli = tmp_path / "agy.exe"
        fake_cli.write_text("", encoding="utf-8")

        self._overrides = mock.patch.multiple(
            settings,
            LLM_PROVIDER="antigravity",
            LLM_MODEL_NAME="gemini-3.8-flash",
            LLM_ANTIGRAVITY_EFFORT="high",
            LLM_ANTIGRAVITY_CLI_PATH=str(fake_cli),
            LLM_ANTIGRAVITY_WORKDIR=str(tmp_path / "workdir"),
            LLM_ANTIGRAVITY_AUDIT_DIR="",  # 預設關閉審計，個別測試自行開啟
            LLM_ANTIGRAVITY_TIMEOUT_SECONDS=300.0,
            LLM_ANTIGRAVITY_ISOLATE_HOME=True,
        )
        self._overrides.start()
        self.addCleanup(self._overrides.stop)
        self.addCleanup(self._tmp.cleanup)
        sleep_patch = mock.patch.object(AntigravityLLMClient, "_sleep", new=mock.AsyncMock())
        self.sleep_mock = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.tmp_path = tmp_path

    def _generate(self, client: AntigravityLLMClient, **kwargs: Any) -> Any:
        return asyncio.run(
            client.generate_structured_data(
                system_prompt=kwargs.get("system_prompt", "你是測試用的助手。"),
                user_prompt=kwargs.get("user_prompt", "請輸出測試資料。"),
                response_schema=kwargs.get("response_schema", NESTED_SCHEMA),
            )
        )

    def _run_ok(self, *stdouts: str, stderr: str = "", **kwargs: Any) -> tuple[Any, mock.MagicMock]:
        """預期成功：依序回傳假輸出，回傳 (結果, run mock)。例外直接上拋，失敗原因清楚可見。"""
        client = AntigravityLLMClient()
        side_effect = [_FakeCompleted(out, stderr) for out in stdouts]
        with mock.patch.object(subprocess, "run", side_effect=side_effect) as run_mock:
            result = self._generate(client, **kwargs)
        return result, run_mock

    def _run_error(self, *stdouts: str, stderr: str = "", returncode: int = 0) -> tuple[LLMServiceError, mock.MagicMock]:
        """預期失敗：回傳 (拋出的 LLMServiceError 族例外, run mock)。"""
        client = AntigravityLLMClient()
        side_effect = [_FakeCompleted(out, stderr, returncode) for out in stdouts]
        with mock.patch.object(subprocess, "run", side_effect=side_effect) as run_mock:
            with self.assertRaises(LLMServiceError) as ctx:
                self._generate(client)
        return ctx.exception, run_mock


class HappyPathTests(AntigravityClientTestBase):
    """成功路徑與標籤格式。"""

    def test_returns_structured_output_not_response_text(self) -> None:
        """取 structured_output；response 的散文與 toolAction 不得混進結果。"""
        result, _ = self._run_ok(_stream())
        self.assertEqual(result.parsed_data, VALID_PAYLOAD)
        self.assertEqual(json.loads(result.raw_content), VALID_PAYLOAD)
        self.assertEqual(result.attempt_count, 1)

    def test_model_label(self) -> None:
        result, _ = self._run_ok(_stream())
        self.assertEqual(result.model_name, "(antigravity)gemini-3.8-flash@high")

    def test_effort_override_in_label_and_command(self) -> None:
        client = AntigravityLLMClient(effort="LOW")
        self.assertEqual(client.model_label, "(antigravity)gemini-3.8-flash@low")
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_stream())) as run_mock:
            self._generate(client)
        command = run_mock.call_args[0][0]
        self.assertEqual(command[command.index("--effort") + 1], "low")

    def test_result_line_without_prefix_events(self) -> None:
        result, _ = self._run_ok(_stream(prefix_events=False))
        self.assertEqual(result.parsed_data, VALID_PAYLOAD)

    def test_ignores_non_json_noise_lines(self) -> None:
        result, _ = self._run_ok("warning: something\n" + _stream() + "trailing chatter\n")
        self.assertEqual(result.parsed_data, VALID_PAYLOAD)

    def test_unicode_line_separators_in_content_do_not_break_the_result_line(self) -> None:
        """NDJSON 只以 \\n 分行；str.splitlines() 會在 U+2028 / U+2029 / U+0085 切斷 result 行。"""
        payload = {"inner": {"value": "a\u2028b\u2029c\u0085d"}}
        result, _ = self._run_ok(_stream({"structured_output": payload}))
        self.assertEqual(result.parsed_data, payload)

    def test_crlf_line_endings(self) -> None:
        """Windows 是主要平台；行尾為 \r\n 時仍能解析。"""
        result, _ = self._run_ok(_stream().replace("\n", "\r\n"))
        self.assertEqual(result.parsed_data, VALID_PAYLOAD)

    def test_last_result_event_wins(self) -> None:
        """多個 result 事件時取最後一個。"""
        stdout = _error_stream(_ELIGIBILITY_503) + _stream()
        result, _ = self._run_ok(stdout)
        self.assertEqual(result.parsed_data, VALID_PAYLOAD)


class CommandConstructionTests(AntigravityClientTestBase):
    """命令列、stdin、環境。"""

    def _capture(self, **kwargs: Any) -> mock.MagicMock:
        _, run_mock = self._run_ok(_stream(), **kwargs)
        return run_mock

    def test_command_flags(self) -> None:
        command = self._capture().call_args[0][0]
        self.assertEqual(command[0], settings.LLM_ANTIGRAVITY_CLI_PATH)
        # stdin 只能走 stream-json：-p 必須帶值、不讀 stdin（實測 A1）
        self.assertNotIn("-p", command)
        self.assertNotIn("--print", command)
        self.assertEqual(command[command.index("--input-format") + 1], "stream-json")
        self.assertEqual(command[command.index("--output-format") + 1], "stream-json")
        self.assertIn("--sandbox", command)
        self.assertIn("--disable-slash-commands", command)
        self.assertEqual(command[command.index("--model") + 1], "gemini-3.8-flash")
        self.assertNotIn("--dangerously-skip-permissions", command)
        self.assertNotIn("--system-prompt", command)

    def test_print_timeout_fires_before_subprocess_timeout(self) -> None:
        """--print-timeout 比 subprocess 逾時早觸發，且每次呼叫才讀設定。"""
        client = AntigravityLLMClient()
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_TIMEOUT_SECONDS", 900.0), \
             mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_stream())) as run_mock:
            self._generate(client)
        command = run_mock.call_args[0][0]
        # 900 秒：餘裕取 180 秒（涵蓋實測最壞的啟動開銷）
        self.assertEqual(command[command.index("--print-timeout") + 1], "720s")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 900.0)

    def test_print_timeout_invariants(self) -> None:
        """任何設定下都要 0 < print-timeout < subprocess 逾時（agy 把 0 解讀為不限），
        且小設定值不能被固定餘裕吃到只剩一兩秒。"""
        for timeout in (2.0, 10.0, 60.0, 300.0, 900.0, 3600.0):
            with self.subTest(timeout=timeout), \
                 mock.patch.object(settings, "LLM_ANTIGRAVITY_TIMEOUT_SECONDS", timeout):
                command = self._capture().call_args[0][0]
                print_timeout = int(command[command.index("--print-timeout") + 1].rstrip("s"))
                self.assertGreater(print_timeout, 0)
                self.assertLess(print_timeout, timeout)
                self.assertGreaterEqual(print_timeout, int(timeout * 0.8) - 1)

    def test_schema_is_flattened(self) -> None:
        command = self._capture().call_args[0][0]
        schema = json.loads(command[command.index("--json-schema") + 1])
        self.assertNotIn("$defs", schema)
        self.assertEqual(schema["properties"]["inner"]["properties"], {"value": {"type": "string"}})

    def test_stdin_is_one_stream_json_user_message_with_system_block(self) -> None:
        """agy 沒有 --system-prompt：system prompt 以 <system> 區塊併入；整則訊息只佔一行（以 \\n 計）。"""
        user_prompt = "USER 資料\n第二行\u2028同一行"
        stdin = self._capture(system_prompt="SYS 角色", user_prompt=user_prompt).call_args.kwargs["input"].decode("utf-8")
        self.assertEqual(stdin.count("\n"), 1)
        self.assertTrue(stdin.endswith("\n"))
        message = json.loads(stdin)
        self.assertEqual(message["event"], "user")
        self.assertEqual(message["message"]["role"], "user")
        self.assertEqual(message["message"]["content"], f"<system>\nSYS 角色\n</system>\n\n{user_prompt}")

    def test_cwd_is_dedicated_workdir(self) -> None:
        self.assertEqual(Path(self._capture().call_args.kwargs["cwd"]), self.tmp_path / "workdir")

    def test_env_isolates_home_and_disables_auto_update(self) -> None:
        env = self._capture().call_args.kwargs["env"]
        home = self.tmp_path / "workdir_home"
        self.assertEqual(env["HOME"], str(home))
        self.assertEqual(env["USERPROFILE"], str(home))
        self.assertTrue(home.is_dir())
        self.assertEqual(env["AGY_CLI_DISABLE_AUTO_UPDATE"], "1")

    def test_home_is_outside_workdir(self) -> None:
        """home 不可放進 workspace：模型能列出與寫入 workspace 內的檔案。"""
        client = AntigravityLLMClient()
        workdir = Path(client._workdir)
        self.assertNotIn(workdir, client.home_dir.parents)
        self.assertNotEqual(workdir, client.home_dir)

    def test_default_home_matches_gitignore(self) -> None:
        """WORKDIR 留空時 home 為 backend/.antigravity_workdir_home——會話紀錄含 prompt，必須被 .gitignore 涵蓋。"""
        fake_backend = self.tmp_path / "backend"
        with mock.patch.object(cli_base, "_BACKEND_DIR", fake_backend), \
             mock.patch.object(settings, "LLM_ANTIGRAVITY_WORKDIR", ""):
            client = AntigravityLLMClient()
        self.assertEqual(client.home_dir, fake_backend / ".antigravity_workdir_home")
        gitignore = (_BACKEND_DIR.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("backend/.antigravity_workdir/", gitignore)
        self.assertIn("backend/.antigravity_workdir_home/", gitignore)

    def test_isolation_setting_is_read_per_call(self) -> None:
        """建構後關閉隔離，下一次呼叫即沿用真實 home；自動更新關閉與 IDE 剔除不受影響。"""
        client = AntigravityLLMClient()
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_ISOLATE_HOME", False), \
             mock.patch.dict(os.environ, {"USERPROFILE": "C:\\real\\home", "HOME": "/real/home", "ANTIGRAVITY_X": "1"}), \
             mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_stream())) as run_mock:
            self._generate(client)
        env = run_mock.call_args.kwargs["env"]
        self.assertEqual(env["USERPROFILE"], "C:\\real\\home")
        self.assertEqual(env["HOME"], "/real/home")
        self.assertEqual(env["AGY_CLI_DISABLE_AUTO_UPDATE"], "1")
        self.assertNotIn("ANTIGRAVITY_X", env)

    def test_home_not_created_when_isolation_is_off(self) -> None:
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_ISOLATE_HOME", False):
            self._capture()
        self.assertFalse((self.tmp_path / "workdir_home").exists())

    def test_ide_variables_are_scrubbed(self) -> None:
        with mock.patch.dict(os.environ, {
            "ANTIGRAVITY_CONVERSATION_ID": "ide-session",
            "ANTIGRAVITY_CSRF_TOKEN": "secret",
            "KEEP_ME": "1",
        }):
            env = self._capture().call_args.kwargs["env"]
        self.assertFalse([name for name in env if name.upper().startswith("ANTIGRAVITY_")])
        self.assertEqual(env["KEEP_ME"], "1")


class FailureTriageTests(AntigravityClientTestBase):
    """失敗分流：致命 / 額度 / 暫時性 / 可修復。"""

    # --- 致命 ---

    def test_invalid_model_is_fatal_with_cli_text_and_effort(self) -> None:
        error, run_mock = self._run_error(_error_stream(
            'invalid model selection (--model "gemini-3.8-flash-low" --effort ""): model gemini-3.8-flash-low '
            "is not recognized as a known model or custom model in settings"
        ))
        self.assertIs(type(error), LLMServiceError)
        self.assertIn("基底名", str(error))
        self.assertIn("effort='high'", str(error))
        self.assertIn("is not recognized as a known model", str(error))  # CLI 原文不丟
        self.assertEqual(run_mock.call_count, 1)

    def test_permission_denied_status_is_fatal_with_isolation_hint(self) -> None:
        error, run_mock = self._run_error(_error_stream("request failed: PERMISSION_DENIED (code 403): caller lacks access"))
        self.assertIs(type(error), LLMServiceError)
        self.assertIn("LLM_ANTIGRAVITY_ISOLATE_HOME=false", str(error))
        self.assertEqual(run_mock.call_count, 1)

    def test_auth_wording_is_fatal(self) -> None:
        error, run_mock = self._run_error(_error_stream("not logged in: please sign in"))
        self.assertIs(type(error), LLMServiceError)
        self.assertIn("登入", str(error))
        self.assertEqual(run_mock.call_count, 1)

    def test_cli_print_timeout_wording_is_fatal(self) -> None:
        error, run_mock = self._run_error(_error_stream("print timeout reached after 4m30s"))
        self.assertIs(type(error), LLMServiceError)
        self.assertIn("LLM_ANTIGRAVITY_TIMEOUT_SECONDS", str(error))
        self.assertEqual(run_mock.call_count, 1)

    def test_cli_timeout_detected_by_duration_whatever_the_wording(self) -> None:
        """逾時措辭未實測；實耗時間已到 --print-timeout 就是 CLI 自身逾時，不當暫時性重試三輪。"""
        # setUp 的逾時 300 秒 → --print-timeout 240 秒
        timed_out = _stream({
            "conversation_id": "c1", "status": "ERROR", "response": "", "structured_output": _DROP,
            "error": "DEADLINE_EXCEEDED (code 504): context deadline exceeded", "duration_seconds": 239.8,
        }, prefix_events=False)
        error, run_mock = self._run_error(timed_out)
        self.assertIs(type(error), LLMServiceError)
        self.assertIn("--print-timeout=240s", str(error))
        self.assertEqual(run_mock.call_count, 1)

    def test_short_deadline_error_is_still_transient(self) -> None:
        """同樣的狀態名但實耗遠低於 --print-timeout：服務端的暫時性錯誤，照常重試。"""
        quick = _stream({
            "conversation_id": "c1", "status": "ERROR", "response": "", "structured_output": _DROP,
            "error": "DEADLINE_EXCEEDED (code 504): upstream deadline", "duration_seconds": 3.0,
        }, prefix_events=False)
        result, run_mock = self._run_ok(quick, _stream())
        self.assertEqual(result.attempt_count, 2)

    def test_no_event_at_all_is_fatal_regardless_of_exit_code(self) -> None:
        """stdout 沒有任何事件、也沒有被截斷的 JSON 行：實測唯一的形態是用法錯誤（只寫 stderr）。

        刻意不看 exit code（基底不傳 returncode）：exit 1 與 exit 2 一樣不重試。
        """
        usage = "Error: --input-format stream-json reads prompts from stdin, so a prompt given on the command line would be ignored"
        for returncode in (2, 1):
            with self.subTest(returncode=returncode):
                error, run_mock = self._run_error("", stderr=usage, returncode=returncode)
                self.assertIs(type(error), LLMServiceError)
                self.assertIn("沒有輸出任何事件", str(error))
                self.assertIn("--input-format", str(error))
                self.assertEqual(run_mock.call_count, 1)

    def test_only_truncated_json_lines_are_transient(self) -> None:
        """沒有完整事件但有被截斷的 JSON 行：CLI 中途結束，不是用法錯誤。"""
        result, run_mock = self._run_ok('{"event":"init","conversation_id":"x","in', _stream())
        self.assertEqual(result.attempt_count, 2)
        self.sleep_mock.assert_awaited_once_with(10.0)

    def test_unknown_status_is_fatal(self) -> None:
        """信封格式漂移時，重試只會每句燒三次慢速呼叫。"""
        error, run_mock = self._run_error(_stream({"status": "PARTIAL"}))
        self.assertIs(type(error), LLMServiceError)
        self.assertIn("status='PARTIAL'", str(error))
        self.assertEqual(run_mock.call_count, 1)

    def test_missing_status_is_fatal(self) -> None:
        error, _ = self._run_error(_stream({"status": _DROP}))
        self.assertIn("status=None", str(error))

    # --- 額度 ---

    def test_resource_exhausted_status_is_quota(self) -> None:
        error, run_mock = self._run_error(_error_stream("RESOURCE_EXHAUSTED (code 8): individual limit reached"))
        self.assertIsInstance(error, LLMQuotaError)
        self.assertIn("Quota", str(error))
        self.assertEqual(run_mock.call_count, 1)

    def test_code_429_is_quota(self) -> None:
        error, _ = self._run_error(_error_stream("request failed: TOO_MANY_REQUESTS (code 429)"))
        self.assertIsInstance(error, LLMQuotaError)

    def test_bare_429_digits_are_not_quota(self) -> None:
        """「429」只認狀態碼位置；其他地方出現的數字不算。

        注意這只在例外型別層成立：生成腳本仍以 ``"429" in 回應文字`` 判斷額度，
        訊息中的 CLI 原文或 conversation_id 碰巧含 429 時腳本會誤判（計畫 §8，
        腳本改讀 error_code 後解除）。
        """
        error, run_mock = self._run_error(*[_error_stream("UNAVAILABLE (code 503): request id 1429-abc")] * 3)
        self.assertNotIsInstance(error, LLMQuotaError)
        self.assertEqual(run_mock.call_count, 3)

    def test_status_name_takes_precedence_over_wording(self) -> None:
        """已解析出狀態名時不看措辭：說明文字提到 quota 服務不代表額度耗盡。"""
        error, run_mock = self._run_error(*[_error_stream("UNAVAILABLE (code 503): failed to reach quota service")] * 3)
        self.assertIsInstance(error, LLMServiceUnavailableError)
        self.assertEqual(run_mock.call_count, 3)

    def test_wording_is_used_when_no_status_name(self) -> None:
        error, _ = self._run_error(_error_stream("rate limit exceeded, try again later"))
        self.assertIsInstance(error, LLMQuotaError)

    def test_stderr_is_used_when_error_field_is_empty(self) -> None:
        error, run_mock = self._run_error(_error_stream(""), stderr="error: RESOURCE_EXHAUSTED (code 8): individual limit reached")
        self.assertIsInstance(error, LLMQuotaError)
        self.assertEqual(run_mock.call_count, 1)

    def test_stderr_is_ignored_when_error_field_is_present(self) -> None:
        """stderr 混有說明與 log；有 error 欄位時不得因 stderr 的字眼改判額度或認證。"""
        error, run_mock = self._run_error(
            *[_error_stream(_ELIGIBILITY_503)] * 3, stderr="hint: quota docs at ...; unauthenticated requests are rejected"
        )
        self.assertNotIsInstance(error, LLMQuotaError)
        self.assertIn("服務在 3 次嘗試後仍不可用", str(error))
        self.assertEqual(run_mock.call_count, 3)

    # --- 暫時性（服務端）---

    def test_eligibility_503_is_retried_after_backoff(self) -> None:
        result, run_mock = self._run_ok(_error_stream(_ELIGIBILITY_503), _stream())
        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(run_mock.call_count, 2)
        self.sleep_mock.assert_awaited_once_with(10.0)

    def test_two_503s_then_success_on_third_attempt(self) -> None:
        """總嘗試 3 次（比 claude 多一次）是因應實測約四成的 503；改回 2 會讓這句永久跳過。"""
        result, _ = self._run_ok(_error_stream(_ELIGIBILITY_503), _error_stream(_ELIGIBILITY_503), _stream())
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep_mock.await_args_list], [10.0, 30.0])

    def test_503_exhaustion_is_service_unavailable_not_empty_content(self) -> None:
        """基礎設施故障不能偽裝成「空內容」：那會讓腳本記成安全審查攔截。"""
        error, run_mock = self._run_error(*[_error_stream(_ELIGIBILITY_503)] * 3)
        self.assertIsInstance(error, LLMServiceUnavailableError)
        self.assertEqual(error.error_code, "LLM_SERVICE_UNAVAILABLE")
        self.assertNotIn(_SKIP_IDENTIFIER, str(error))
        # 最後一次失敗之後不再退避
        self.assertEqual([call.args[0] for call in self.sleep_mock.await_args_list], [10.0, 30.0])
        self.assertIn("Antigravity CLI 服務在 3 次嘗試後仍不可用", str(error))
        self.assertIn("UNAVAILABLE", str(error))
        self.assertEqual(run_mock.call_count, 3)

    def test_empty_error_message_still_describes_itself(self) -> None:
        error, _ = self._run_error(*[_error_stream("")] * 3)
        self.assertIn("（無訊息）", str(error))
        self.assertIn("status=未標示", str(error))

    def test_events_without_result_are_transient(self) -> None:
        """參數已被接受、CLI 中途結束（有事件、沒有 result）：暫時性，重試。"""
        truncated = "\n".join(_stream().splitlines()[:2]) + '\n{"event":"result","res'
        result, run_mock = self._run_ok(truncated, _stream())
        self.assertEqual(result.attempt_count, 2)
        self.sleep_mock.assert_awaited_once_with(10.0)

    # --- 暫時性與內容問題混合：最後一次的類別決定耗盡時的例外 ---

    def test_transient_then_content_failure_exhausts_as_skip(self) -> None:
        denied = _stream({"response": "", "structured_output": _DROP,
                          "denied_actions": [{"action": "command", "display_name": "RunCommand"}]})
        error, _ = self._run_error(_error_stream(_ELIGIBILITY_503), _error_stream(_ELIGIBILITY_503), denied)
        self.assertIsInstance(error, LLMRetryExhaustedError)
        self.assertIn(_SKIP_IDENTIFIER, str(error))

    def test_content_then_transient_failure_exhausts_as_unavailable(self) -> None:
        denied = _stream({"response": "", "structured_output": _DROP,
                          "denied_actions": [{"action": "command", "display_name": "RunCommand"}]})
        error, _ = self._run_error(denied, denied, _error_stream(_ELIGIBILITY_503))
        self.assertIsInstance(error, LLMServiceUnavailableError)
        self.sleep_mock.assert_not_awaited()  # 暫時性失敗是最後一次，之後不再等待

    def test_backoff_only_after_transient_failures(self) -> None:
        denied = _stream({"response": "", "structured_output": _DROP,
                          "denied_actions": [{"action": "command", "display_name": "RunCommand"}]})
        result, _ = self._run_ok(_error_stream(_ELIGIBILITY_503), denied, _stream())
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual([call.args[0] for call in self.sleep_mock.await_args_list], [10.0])

    # --- 內容層（可修復）---

    def test_denied_tool_with_structured_output_is_accepted(self) -> None:
        """被拒的工具沒有副作用；模型之後仍產出結果就採用，不浪費一次慢速呼叫。"""
        stdout = _stream({"denied_actions": [{"action": "command", "display_name": "RunCommand"}]})
        with self.assertLogs("app.infrastructure.llm.antigravity_client", level="WARNING") as logs:
            result, run_mock = self._run_ok(stdout)
        self.assertEqual(result.parsed_data, VALID_PAYLOAD)
        self.assertEqual(run_mock.call_count, 1)
        self.assertIn("RunCommand", "\n".join(logs.output))

    def test_denied_tool_without_result_is_retried_with_no_tools_feedback(self) -> None:
        denied = _stream({
            "response": "", "structured_output": _DROP,
            "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
        })
        result, run_mock = self._run_ok(denied, _stream())
        self.assertEqual(result.attempt_count, 2)
        retry_stdin = json.loads(run_mock.call_args_list[1].kwargs["input"].decode("utf-8"))
        self.assertIn("不要呼叫任何工具", retry_stdin["message"]["content"])
        self.sleep_mock.assert_not_awaited()  # 內容層失敗不退避

    def test_denied_tool_exhaustion_names_the_tool(self) -> None:
        denied = _stream({
            "response": "", "structured_output": _DROP,
            "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
        })
        error, _ = self._run_error(*[denied] * 3)
        self.assertIsInstance(error, LLMRetryExhaustedError)
        self.assertIn(_SKIP_IDENTIFIER, str(error))
        self.assertIn("RunCommand", str(error))

    def test_missing_structured_output_is_retried(self) -> None:
        """不能退回去讀 response：那裡混有散文與額外欄位。"""
        missing = _stream({"structured_output": _DROP, "response": json.dumps(VALID_PAYLOAD)})
        result, run_mock = self._run_ok(missing, _stream())
        self.assertEqual(result.attempt_count, 2)

    def test_non_object_structured_output_is_retried(self) -> None:
        stringly = _stream({"structured_output": json.dumps(VALID_PAYLOAD)})
        error, _ = self._run_error(*[stringly] * 3)
        self.assertIsInstance(error, LLMRetryExhaustedError)
        self.assertIn("實際型別：str", str(error))

    def test_schema_mismatch_is_repaired_with_feedback(self) -> None:
        """CLI 強制 schema 之外，基底的 jsonschema 複核是客戶端內的最後一道防線。"""
        bad = _stream({"structured_output": {"inner": {"value": 123}}})
        result, run_mock = self._run_ok(bad, _stream())
        self.assertEqual(result.attempt_count, 2)
        retry_content = json.loads(run_mock.call_args_list[1].kwargs["input"].decode("utf-8"))["message"]["content"]
        self.assertIn("上次輸出未通過驗證", retry_content)
        self.assertTrue(retry_content.startswith("<system>"))  # 修復重試仍帶 system 區塊

    def test_error_text_in_generated_content_is_not_misread(self) -> None:
        """成功結果的內容含 quota / 503 等字樣時不得誤判（分流只在 status=ERROR 時進行）。"""
        payload = {"inner": {"value": "quota 429 not logged in invalid model selection UNAVAILABLE (code 503)"}}
        result, _ = self._run_ok(_stream({"structured_output": payload}))
        self.assertEqual(result.parsed_data, payload)

    def test_subprocess_timeout_is_service_error(self) -> None:
        client = AntigravityLLMClient()
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="agy", timeout=300)):
            with self.assertRaises(LLMServiceError) as ctx:
                self._generate(client)
        self.assertIn("超時", str(ctx.exception))


class WorkdirHygieneTests(AntigravityClientTestBase):
    def test_leftover_files_in_workdir_are_logged(self) -> None:
        """workspace 內寫檔不需許可（實測 A4）；留下的檔案會污染下一次呼叫，必須留下紀錄。"""
        client = AntigravityLLMClient()
        workdir = Path(client._workdir)

        def fake_run(*_args: Any, **_kwargs: Any) -> _FakeCompleted:
            (workdir / "probe.txt").write_text("hi", encoding="utf-8")
            return _FakeCompleted(_stream())

        with mock.patch.object(subprocess, "run", side_effect=fake_run), \
             self.assertLogs("app.infrastructure.llm.antigravity_client", level="WARNING") as logs:
            self._generate(client)
        self.assertIn("probe.txt", "\n".join(logs.output))


class InitializationTests(AntigravityClientTestBase):
    """建構子：effort 白名單、CLI 探測、專用 home。"""

    def test_invalid_effort_rejected(self) -> None:
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_EFFORT", "ultra"):
            with self.assertRaises(LLMServiceError) as ctx:
                AntigravityLLMClient()
        self.assertIn("LLM_ANTIGRAVITY_EFFORT", str(ctx.exception))

    def test_all_five_efforts_accepted(self) -> None:
        for effort in ("low", "medium", "high", "xhigh", "max"):
            self.assertEqual(AntigravityLLMClient(effort=effort).effort, effort)

    def test_missing_configured_cli_path(self) -> None:
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_CLI_PATH", str(self.tmp_path / "nope.exe")):
            with self.assertRaises(LLMServiceError) as ctx:
                AntigravityLLMClient()
        self.assertIn("LLM_ANTIGRAVITY_CLI_PATH", str(ctx.exception))

    def test_home_is_created_at_construction(self) -> None:
        AntigravityLLMClient()
        self.assertTrue((self.tmp_path / "workdir_home").is_dir())

    def test_home_creation_failure_is_service_error(self) -> None:
        """不讓 mkdir 失敗被基底包成「無法啟動 CLI」。"""
        blocker = self.tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        with mock.patch.object(AntigravityLLMClient, "home_dir", new_callable=mock.PropertyMock, return_value=blocker / "home"):
            with self.assertRaises(LLMServiceError) as ctx:
                AntigravityLLMClient()
        self.assertIn("無法建立 agy 專用 home", str(ctx.exception))

    def test_home_creation_failure_at_call_time_is_service_error(self) -> None:
        """建構後才開啟隔離、而 home 建不起來：不得被基底包成「無法啟動 CLI」，也不得啟動 CLI。"""
        blocker = self.tmp_path / "blocker"
        blocker.write_text("", encoding="utf-8")
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_ISOLATE_HOME", False):
            client = AntigravityLLMClient()
        with mock.patch.object(AntigravityLLMClient, "home_dir", new_callable=mock.PropertyMock, return_value=blocker / "home"), \
             mock.patch.object(subprocess, "run") as run_mock:
            with self.assertRaises(LLMServiceError) as ctx:
                self._generate(client)
        self.assertIn("無法建立 agy 專用 home", str(ctx.exception))
        run_mock.assert_not_called()

    def test_home_created_at_call_time_when_isolation_turned_on(self) -> None:
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_ISOLATE_HOME", False):
            client = AntigravityLLMClient()
        self.assertFalse(client.home_dir.exists())
        with mock.patch.object(subprocess, "run", return_value=_FakeCompleted(_stream())) as run_mock:
            self._generate(client)
        self.assertTrue(client.home_dir.is_dir())
        self.assertEqual(run_mock.call_args.kwargs["env"]["HOME"], str(client.home_dir))

    def test_windows_candidate_is_localappdata(self) -> None:
        """只替換本模組的 os 參照，不碰全域 os.name（否則非 Windows 上 Path 會壞）。"""
        fake_os = SimpleNamespace(name="nt", environ={"LOCALAPPDATA": str(self.tmp_path / "lad")})
        client = AntigravityLLMClient()
        with mock.patch("app.infrastructure.llm.antigravity_client.os", fake_os):
            self.assertEqual(client._extra_cli_candidates(), [self.tmp_path / "lad" / "agy" / "bin" / "agy.exe"])

    def test_no_extra_candidate_off_windows(self) -> None:
        fake_os = SimpleNamespace(name="posix", environ={"LOCALAPPDATA": str(self.tmp_path / "lad")})
        client = AntigravityLLMClient()
        with mock.patch("app.infrastructure.llm.antigravity_client.os", fake_os):
            self.assertEqual(client._extra_cli_candidates(), [])

    def test_localappdata_install_found_when_not_on_path(self) -> None:
        """安裝前已啟動的程序拿不到新 PATH 時，仍能找到 Windows 官方安裝位置（實測 A8）。"""
        installed = self.tmp_path / "lad" / "agy" / "bin" / "agy.exe"
        installed.parent.mkdir(parents=True)
        installed.write_text("", encoding="utf-8")
        fake_os = SimpleNamespace(name="nt", environ={"LOCALAPPDATA": str(self.tmp_path / "lad")})
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_CLI_PATH", ""), \
             mock.patch("app.infrastructure.llm.antigravity_client.os", fake_os), \
             mock.patch("app.infrastructure.llm.cli_base.shutil.which", return_value=None), \
             mock.patch.object(Path, "home", return_value=self.tmp_path / "nohome"):
            client = AntigravityLLMClient()
        self.assertEqual(Path(client.cli_path), installed)

    def test_not_found_hint(self) -> None:
        fake_os = SimpleNamespace(name="nt", environ={"LOCALAPPDATA": str(self.tmp_path / "empty")})
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_CLI_PATH", ""), \
             mock.patch("app.infrastructure.llm.antigravity_client.os", fake_os), \
             mock.patch("app.infrastructure.llm.cli_base.shutil.which", return_value=None), \
             mock.patch.object(Path, "home", return_value=self.tmp_path / "nohome"):
            with self.assertRaises(LLMServiceError) as ctx:
                AntigravityLLMClient()
        self.assertIn("找不到 agy 執行檔", str(ctx.exception))
        self.assertIn("LLM_ANTIGRAVITY_CLI_PATH", str(ctx.exception))


class AuditTests(AntigravityClientTestBase):
    def test_audit_files_written(self) -> None:
        audit_dir = self.tmp_path / "audit"
        with mock.patch.object(settings, "LLM_ANTIGRAVITY_AUDIT_DIR", str(audit_dir)):
            self._run_ok(_stream(), system_prompt="SYS", user_prompt="USER")
        calls = list(audit_dir.iterdir())
        self.assertEqual(len(calls), 1)
        self.assertEqual({p.name for p in calls[0].iterdir()}, {"prompt.md", "answer.json", "meta.json"})
        self.assertEqual(json.loads((calls[0] / "answer.json").read_text(encoding="utf-8")), VALID_PAYLOAD)
        meta = json.loads((calls[0] / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["model_label"], "(antigravity)gemini-3.8-flash@high")


class FactoryAndDiagnosticsTests(AntigravityClientTestBase):
    def test_factory_routes_to_antigravity(self) -> None:
        """真實類別經註冊表解析（同時核對靜態旗標）並實例化。"""
        self.assertIs(factory.resolve_client_class("antigravity"), AntigravityLLMClient)
        self.assertTrue(factory.is_cli_provider("antigravity"))
        self.assertIn("antigravity", factory.providers_supporting_effort())
        self.assertIsInstance(factory.create_llm_client(effort="max"), AntigravityLLMClient)

    def test_runtime_info_has_cli_block_and_probes_with_isolated_env(self) -> None:
        """計畫 §7：antigravity 下回 CLI 診斷區塊（區塊鍵沿用既有的 claude_code）。

        版本探測要帶 client 的環境：診斷端點不得成為意外觸發 agy 自動升版的入口。
        """
        client = AntigravityLLMClient()
        version = _FakeCompleted("1.3.5\n")
        with mock.patch("app.services.runtime_config_service.subprocess.run", return_value=version) as run_mock:
            info = asyncio.run(RuntimeConfigService().get_runtime_info(SimpleNamespace(llm_client=client)))
        block = info["claude_code"]
        self.assertEqual(block["cli_version"], "1.3.5")
        self.assertEqual(block["effort"], "high")
        self.assertEqual(block["account"]["status"], "unsupported")
        self.assertEqual(info["llm_label"], "(antigravity)gemini-3.8-flash@high")
        self.assertEqual(run_mock.call_args.kwargs["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"], "1")


if __name__ == "__main__":
    unittest.main()
