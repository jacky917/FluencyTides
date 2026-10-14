"""
Headless CLI 型 LLM provider 的抽象中間層（計畫 D3）。

所有「以 subprocess 驅動本機 CLI 做結構化生成」的 provider（Claude Code 的
``claude -p``、Antigravity 的 ``agy -p``）共用的固定流程都在這裡，以
template method 組織：

- ``generate_structured_data``：帶錯誤回饋的修復重試迴圈
- ``_invoke_cli``：``asyncio.to_thread`` + ``subprocess.run``、UTF-8 解碼、超時
- ``_validate_payload``：fence 清理 → ``json.loads`` → dict 檢查 → ``jsonschema`` 複核
- CLI 路徑 / 工作目錄 / 審計目錄解析、審計落盤

CLI 之間真正不同的部分做成 hook，由子類提供：命令列（``_build_command``）、
subprocess 環境（``_build_env``）、stdin 內容（``_build_stdin``）、信封解析與
失敗分流（``_extract_result``）。

不可改的實作約束（2026-08-27 計畫 D5）：``_invoke_cli`` 必須用
``asyncio.to_thread`` + 同步 ``subprocess.run``，不可改 ``create_subprocess_exec``
——後者在 Windows 的 selector event loop（本專案多數腳本採用）下拋
``NotImplementedError``。

English summary:
    Abstract intermediate layer for subprocess-driven CLI providers. The
    invariant pipeline (repair-retry loop, process invocation, payload
    validation, path resolution, auditing) lives here as a template method;
    per-CLI differences (argv, environment, stdin composition, envelope
    parsing) are abstract hooks.
"""

import abc
import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import jsonschema

from app.core.exceptions import LLMRetryExhaustedError, LLMServiceError
from app.infrastructure.llm.base import BaseLLMClient
from app.infrastructure.llm.schema_utils import resolve_json_schema, strip_markdown_fences
from app.schemas.llm.base import LLMGenerateResult

logger = logging.getLogger(__name__)

# backend/ 根目錄，用於解析設定中的相對路徑
# The backend/ root, used to resolve relative paths from settings.
_BACKEND_DIR = Path(__file__).resolve().parents[3]


class _RetryableCliError(Exception):
    """代表可透過重試（含帶回饋修復）挽救的失敗。

    Internal marker for failures worth retrying, optionally with
    error feedback appended to the prompt.
    """

    def __init__(self, message: str, feedback: str | None = None) -> None:
        super().__init__(message)
        self.feedback = feedback


@dataclass(frozen=True)
class CliRuntime:
    """子類從自己的 ``LLM_<PROVIDER>_*`` 設定組出的執行參數。

    Runtime parameters a subclass assembles from its own settings block, so
    the base never reads provider-specific settings directly.

    Attributes:
        cli_path_setting: 設定的執行檔路徑；空字串＝自動探測。Configured
            binary path; empty means auto-probe.
        workdir_setting: 設定的工作目錄；空字串＝預設。Configured workdir.
        timeout_seconds: 單次呼叫超時秒數。Per-call timeout.
        audit_dir_setting: 審計目錄；空字串＝關閉。Audit dir; empty disables.
        effort_setting: 設定檔中的 effort（無覆寫時使用）。Effort from
            settings, used when no override is given.
    """

    cli_path_setting: str
    workdir_setting: str
    timeout_seconds: float
    audit_dir_setting: str
    effort_setting: str


class HeadlessCliLLMClient(BaseLLMClient):
    """subprocess 驅動 headless CLI 的抽象客戶端。

    Abstract client driving a headless CLI through subprocess.

    Attributes:
        BINARY_NAME: 執行檔名（不含副檔名），用於 PATH 探測。Binary name.
        DISPLAY_NAME: 錯誤訊息用的人類可讀名稱。Human-readable name.
        SETTINGS_PREFIX: 該 provider 的設定前綴（錯誤訊息用）。Settings
            prefix, e.g. ``LLM_CLAUDE_CODE``.
        VALID_EFFORTS: 該 CLI 接受的 effort 白名單。Effort whitelist.
        SCRUBBED_ENV_VARS: 一律從 subprocess 環境剔除的變數。Env vars
            always scrubbed from the subprocess.
        INSTALL_HINT: 探測失敗時附在錯誤訊息的安裝指引。Install hint.
        MAX_RETRIES: 外層修復重試次數。Outer repair-retry count.
    """

    SUPPORTS_EFFORT: ClassVar[bool] = True

    BINARY_NAME: ClassVar[str] = ""
    DISPLAY_NAME: ClassVar[str] = "CLI"
    SETTINGS_PREFIX: ClassVar[str] = "LLM_CLI"
    VALID_EFFORTS: ClassVar[frozenset[str]] = frozenset()
    SCRUBBED_ENV_VARS: ClassVar[tuple[str, ...]] = ()
    INSTALL_HINT: ClassVar[str] = ""
    MAX_RETRIES: ClassVar[int] = 2

    # 基底宣告為 str | None；CLI 一律經白名單驗證，必有值
    _effort: str

    def __init__(self, *, model: str | None = None, effort: str | None = None) -> None:
        """驗證 effort 與憑證、解析 CLI 路徑與目錄。

        Validate effort and credentials, then resolve the CLI path and
        directories.

        檢查順序：effort 白名單 → 憑證（子類 hook）→ 模型名（基底）→ CLI 路徑。
        與層級化前不完全相同——層級化前 CLI 路徑在模型名之前檢查；只在「模型名
        為空且找不到 CLI」同時成立時，最先看到的錯誤訊息不同。
        Order: effort, credentials, model name, CLI path. The pre-refactor
        client checked the CLI path before the model name; the first error
        shown differs only when both are missing.

        Args:
            model: 覆寫模型名。Model override.
            effort: 覆寫思考深度。Effort override.

        Raises:
            LLMServiceError: effort 非白名單值、憑證不合格、或找不到執行檔時。
        """
        runtime = self._runtime()
        validated_effort = self._validate_effort(effort, runtime.effort_setting)
        self._check_credentials()
        super().__init__(model=model, effort=effort)
        self._effort = validated_effort
        self._cli_path = self._resolve_cli_path(runtime.cli_path_setting)
        self._workdir = self._resolve_workdir(runtime.workdir_setting)
        self._audit_dir = self._resolve_audit_dir(runtime.audit_dir_setting)

    # ------------------------------------------------------------------
    # 公開屬性（Public attributes）
    # ------------------------------------------------------------------

    @property
    def cli_path(self) -> str:
        """已解析的執行檔絕對路徑。Resolved absolute binary path."""
        return self._cli_path

    @classmethod
    def default_effort(cls) -> str | None:
        """未帶覆寫時的 effort，來源與 ``__init__`` 驗證的是同一個 ``CliRuntime``。

        The default effort, read from the same ``CliRuntime`` the constructor
        validates — one source, so script-side labels cannot diverge.
        """
        return cls._runtime().effort_setting

    # ------------------------------------------------------------------
    # 子類 hook（Subclass hooks）
    # ------------------------------------------------------------------

    @classmethod
    @abc.abstractmethod
    def _runtime(cls) -> CliRuntime:
        """從該 provider 自己的 ``LLM_<PROVIDER>_*`` 設定組出執行參數。

        Assemble runtime parameters from the provider's own settings block.
        這是基底唯一取得 provider 專屬設定的途徑。
        """

    @abc.abstractmethod
    def _build_command(self, system_prompt: str, schema_text: str) -> list[str]:
        """組裝 CLI 命令列參數。Assemble the CLI argument vector."""

    @abc.abstractmethod
    def _extract_result(self, stdout_text: str, stderr_text: str) -> str:
        """從 CLI 輸出取出模型回答的原始文字，並分流失敗形態。

        Extract the model's raw answer from CLI output and triage failures.

        Raises:
            LLMServiceError: 不可重試的致命失敗（認證、設定、額度）。
            _RetryableCliError: 可重試的失敗。
        """

    def _check_credentials(self) -> None:
        """建構期的憑證格式檢查；預設不檢查。

        Construction-time credential validation; no-op by default. Runs after
        the effort whitelist and before the model name / CLI path probes.

        Raises:
            LLMServiceError: 子類判定憑證不合格時。
        """

    def _build_env(self) -> dict[str, str]:
        """複製當前環境並剔除 ``SCRUBBED_ENV_VARS``。

        Copy the current environment minus ``SCRUBBED_ENV_VARS``.
        """
        env = os.environ.copy()
        for name in self.SCRUBBED_ENV_VARS:
            env.pop(name, None)
        return env

    def _build_stdin(self, system_prompt: str, user_prompt: str) -> str:
        """組出經 stdin 送入的內容；預設只送 user prompt。

        Compose the stdin payload; by default only the user prompt (the
        system prompt travels as a flag). CLIs without a system-prompt flag
        override this to fold both together.
        """
        return user_prompt

    def _extra_cli_candidates(self) -> list[Path]:
        """PATH 探測失敗後的額外候選路徑（預設無）。

        Extra candidate paths tried after the PATH probe (none by default).
        """
        return []

    # ------------------------------------------------------------------
    # 初始化輔助（Initialization helpers）
    # ------------------------------------------------------------------

    def _validate_effort(self, override: str | None, setting_value: str) -> str:
        """以白名單驗證 effort（CLI 對非法值會靜默回退，故必須攔在此）。

        Whitelist-validate effort; the CLI silently falls back on invalid
        values, so it must be caught here.
        """
        source = override if override is not None else setting_value
        origin = "effort 覆寫參數" if override is not None else f"{self.SETTINGS_PREFIX}_EFFORT"
        value = (source or "").strip().lower()
        if value not in self.VALID_EFFORTS:
            raise LLMServiceError(
                f"{origin} 值 '{source}' 無效。"
                f"可用值：{', '.join(sorted(self.VALID_EFFORTS))}。"
                "（注意：CLI 對非法值會靜默回退到預設力度而不報錯，"
                "故必須在此攔截。）"
            )
        return value

    def _default_cli_candidates(self) -> list[Path]:
        """原生安裝的標準路徑，依平台只回一個候選。

        Standard native-install location, one candidate per platform.
        Windows 只認 ``.exe``：層級化前的探測順序是 ``~/.local/bin/<name>.exe``
        → PATH，不可在 PATH 之前多插一個無副檔名的候選（會撿到殘留的 shim）。
        """
        local_bin = Path.home() / ".local" / "bin"
        binary = f"{self.BINARY_NAME}.exe" if os.name == "nt" else self.BINARY_NAME
        return [local_bin / binary]

    def _resolve_cli_path(self, configured: str) -> str:
        """依序探測執行檔：設定值 → 原生安裝路徑 → PATH → 子類額外候選。

        Locate the binary: explicit setting, native install path, PATH,
        then subclass-provided candidates.

        Raises:
            LLMServiceError: 全部探測失敗時。When every probe fails.
        """
        configured = (configured or "").strip()
        if configured:
            if not Path(configured).is_file():
                raise LLMServiceError(
                    f"{self.SETTINGS_PREFIX}_CLI_PATH 指向的檔案不存在: {configured}"
                )
            return configured

        for candidate in self._default_cli_candidates():
            if candidate.is_file():
                return str(candidate)

        on_path = shutil.which(self.BINARY_NAME)
        if on_path:
            return on_path

        for candidate in self._extra_cli_candidates():
            if candidate.is_file():
                return str(candidate)

        raise LLMServiceError(f"找不到 {self.BINARY_NAME} 執行檔。{self.INSTALL_HINT}")

    def _resolve_workdir(self, configured: str) -> str:
        """取得（必要時建立）CLI 執行用的專用空目錄。

        Return (creating if needed) the dedicated empty working directory.
        以空目錄作為 cwd，確保不會意外載入專案脈絡。
        """
        configured = (configured or "").strip()
        default_name = f".{self.PROVIDER_NAME.replace('-', '_')}_workdir"
        workdir = Path(configured) if configured else _BACKEND_DIR / default_name
        if not workdir.is_absolute():
            workdir = _BACKEND_DIR / workdir
        workdir.mkdir(parents=True, exist_ok=True)
        return str(workdir)

    @staticmethod
    def _resolve_audit_dir(configured: str) -> Path | None:
        """取得審計目錄；設定為空字串時代表關閉審計。

        Return the audit directory, or None when auditing is disabled.
        """
        configured = (configured or "").strip()
        if not configured:
            return None
        audit_dir = Path(configured)
        if not audit_dir.is_absolute():
            audit_dir = _BACKEND_DIR / audit_dir
        return audit_dir

    # ------------------------------------------------------------------
    # 公開介面（Public API）—— template method
    # ------------------------------------------------------------------

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """呼叫 CLI 並取得符合 ``response_schema`` 的 JSON 資料。

        Invoke the CLI and obtain JSON conforming to ``response_schema``.

        三道防線：CLI 自身的 schema 強制、本方法以 ``jsonschema`` 複核、
        呼叫端 handler 再以 Pydantic 模型驗證業務語義。驗證失敗時把原因附回
        prompt 做修復重試。

        Raises:
            LLMServiceError: 致命失敗、超時、或所有重試後仍無合規 JSON 時。
        """
        logger.info(
            "LLM 結構化生成請求 -> model: %s, user_prompt 長度: %d 字元",
            self.model_label,
            len(user_prompt),
        )

        # CLI 普遍不支援 $defs/$ref，送出前必須展平
        resolved_schema = resolve_json_schema(response_schema)
        schema_text = json.dumps(resolved_schema, ensure_ascii=False)

        current_prompt = user_prompt
        last_message = ""

        for attempt in range(1, self.MAX_RETRIES + 1):
            stdin_text = self._build_stdin(system_prompt, current_prompt)
            stdout_text, stderr_text = await self._invoke_cli(
                system_prompt, stdin_text, schema_text
            )

            try:
                raw_content = self._extract_result(stdout_text, stderr_text)
                parsed_data = self._validate_payload(raw_content, resolved_schema)
            except _RetryableCliError as retryable:
                last_message = str(retryable)
                logger.warning(
                    "CLI 結構化輸出未通過驗證 (第 %d/%d 次): %s",
                    attempt,
                    self.MAX_RETRIES,
                    last_message,
                )
                if attempt < self.MAX_RETRIES and retryable.feedback:
                    # 帶錯誤回饋的修復重試：把驗證失敗原因附回 prompt
                    current_prompt = (
                        f"{user_prompt}\n\n"
                        f"【上次輸出未通過驗證】\n{retryable.feedback}\n"
                        "請修正後重新輸出完整且合規的 JSON。"
                    )
                continue

            logger.info(
                "LLM 結構化輸出成功 -> 第 %d 次嘗試, 回傳 %d 個欄位",
                attempt,
                len(parsed_data),
            )
            self._write_audit(system_prompt, current_prompt, raw_content, attempt)
            return LLMGenerateResult(
                raw_content=raw_content,
                parsed_data=parsed_data,
                model_name=self.model_label,
                attempt_count=attempt,
            )

        # 訊息刻意包含分級表識別字串：生成腳本據此走「記錄失敗並跳過此句」路徑
        raise LLMRetryExhaustedError(
            f"LLM API 在所有重試後仍回傳空內容（{self.DISPLAY_NAME}）。最後一次原因: {last_message}"
        )

    # ------------------------------------------------------------------
    # 內部實作（Internals）
    # ------------------------------------------------------------------

    async def _invoke_cli(
        self, system_prompt: str, stdin_text: str, schema_text: str
    ) -> tuple[str, str]:
        """實際執行 CLI 並取回 stdout / stderr。

        Run the CLI and return decoded stdout/stderr.

        Raises:
            LLMServiceError: 執行超時或無法啟動進程時。
        """
        command = self._build_command(system_prompt, schema_text)
        # 每次呼叫才讀設定（不在建構時快取）：/setconfig 只改 settings、不重建
        # singleton，執行期調整的逾時必須下一次呼叫即生效（與層級化前一致）
        timeout = self._runtime().timeout_seconds

        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                command,
                input=stdin_text.encode("utf-8"),
                capture_output=True,
                cwd=self._workdir,
                env=self._build_env(),
                timeout=timeout,
            )

        try:
            completed = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as timeout_error:
            logger.error("%s 執行超時（%s 秒）。", self.DISPLAY_NAME, timeout)
            raise LLMServiceError(
                f"{self.DISPLAY_NAME} 執行超時（{timeout} 秒）。"
            ) from timeout_error
        except OSError as os_error:
            logger.error("無法啟動 %s: %s", self.DISPLAY_NAME, os_error)
            raise LLMServiceError(
                f"無法啟動 {self.DISPLAY_NAME} ({self._cli_path}): {os_error}"
            ) from os_error

        # 一律顯式以 UTF-8 解碼，不依賴系統編碼（Windows cp950 會產生亂碼）
        return (
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _validate_payload(
        raw_content: str, resolved_schema: dict[str, object]
    ) -> dict[str, object]:
        """fence 清理 → ``json.loads`` → dict 檢查 → ``jsonschema`` 複核。

        Raises:
            _RetryableCliError: 任一步驟失敗（附修復回饋）。
        """
        cleaned = strip_markdown_fences(raw_content)

        try:
            parsed_data = json.loads(cleaned)
        except json.JSONDecodeError as decode_error:
            raise _RetryableCliError(
                f"輸出非有效 JSON: {decode_error}",
                feedback=f"輸出不是有效的 JSON：{decode_error}",
            ) from decode_error

        if not isinstance(parsed_data, dict):
            raise _RetryableCliError(
                "輸出的 JSON 頂層不是物件。",
                feedback="輸出的 JSON 頂層必須是物件（object）。",
            )

        try:
            jsonschema.validate(instance=parsed_data, schema=resolved_schema)
        except jsonschema.ValidationError as validation_error:
            location = "/".join(str(part) for part in validation_error.absolute_path) or "(root)"
            raise _RetryableCliError(
                f"輸出未通過 schema 複核於 {location}: {validation_error.message}",
                feedback=f"欄位 `{location}` 不符 schema：{validation_error.message}",
            ) from validation_error

        return parsed_data

    def _write_audit(
        self, system_prompt: str, user_prompt: str, raw_content: str, attempt: int
    ) -> None:
        """將本次呼叫的 prompt 與輸出寫入審計目錄；失敗只記警告。

        Persist this call's prompt and output; audit failures never break
        generation.
        """
        if self._audit_dir is None:
            return

        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            call_dir = self._audit_dir / f"{stamp}_{uuid.uuid4().hex[:8]}"
            call_dir.mkdir(parents=True, exist_ok=True)

            (call_dir / "prompt.md").write_text(
                f"# System Prompt\n\n{system_prompt}\n\n# User Prompt\n\n{user_prompt}\n",
                encoding="utf-8",
            )
            (call_dir / "answer.json").write_text(raw_content, encoding="utf-8")
            (call_dir / "meta.json").write_text(
                json.dumps(
                    {
                        # 欄位集合刻意與層級化前一致（計畫 D10：審計三檔逐位元不變）
                        "model": self._model_name,
                        "effort": self._effort,
                        "model_label": self.model_label,
                        "attempt_count": attempt,
                        "cli_path": self._cli_path,
                        "timestamp": stamp,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as audit_error:  # noqa: BLE001 - 審計失敗不得中斷生成
            logger.warning("寫入 LLM 審計紀錄失敗（不影響生成）: %s", audit_error)
