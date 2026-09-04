"""
Claude Code CLI (headless) 結構化輸出客戶端模組。

以本機已登入的 Claude Code CLI（`claude -p`）取代計費 API 完成結構化生成，
對外提供與 :class:`~app.infrastructure.llm.client.LLMClient` 完全相同的介面，
因此上下游（各 Task Handler、生成腳本）切換 provider 時零感知。

使用邊界（重要）：
    本 provider 僅供「使用者手動發起、有始有終的自有批次任務」使用。
    禁止形態：常駐 daemon、對外網路 endpoint、供第三方使用、
    接入多用戶生產路徑。

關鍵設計決策（皆有實測依據，見
``docs/archive/claude_cli_env_setup_FEAT_2026-08-27.md``）：
- 使用 ``asyncio.to_thread`` + 同步 ``subprocess.run``，而非
  ``asyncio.create_subprocess_exec``：後者在 Windows 的
  ``WindowsSelectorEventLoopPolicy``（本專案多數腳本採用）下會拋
  ``NotImplementedError``。
- JSON Schema 送出前必須展平 ``$defs``/``$ref``：未展平會使 CLI 內建的
  結構化輸出重試全數耗盡。直接複用 ``LLMClient._resolve_json_schema``
  （classmethod，免實例化，故 claude-code 模式下無需設定 LLM_API_KEY）。
- subprocess 環境預設剔除 ``CLAUDE_CODE_OAUTH_TOKEN``：其優先級高於
  落盤憑證，殘留的壞值會蓋掉有效登入造成 401。例外：headless 環境
  （容器/伺服器）設定 ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` 後改為注入該值
  ——那裡沒有落盤憑證，env token 是唯一認證途徑
  （docs/archive/claude_cli_in_container_FEAT_2026-08-29.md §D2）。
- ``--effort`` 非法值時 CLI 會「靜默回退」到預設力度而不報錯，故必須在
  Python 端以白名單驗證；驗證只在本類別的建構子內進行，不做成全域
  設定驗證，以免影響 API 模式的啟動。

English summary:
    Headless Claude Code CLI client. Drives the locally authenticated
    `claude -p` binary to produce schema-conformant JSON, exposing the exact
    same interface as ``LLMClient`` so switching providers is transparent to
    all callers. Intended solely for user-initiated, finite batch jobs.
    Uses ``asyncio.to_thread`` + blocking ``subprocess.run`` because
    ``create_subprocess_exec`` is unavailable under Windows' selector event
    loop policy used across this project's scripts. Schemas are flattened
    before being passed to ``--json-schema``; the OAuth token env var is
    scrubbed so the on-disk credential wins; the effort value is whitelisted
    locally because the CLI silently falls back on invalid values.

Dependencies:
    - jsonschema: 輸出複核驗證。Output re-validation.
    - pydantic: 資料模型。Data models.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import jsonschema

from app.core.config import settings
from app.core.exceptions import LLMServiceError
from app.infrastructure.llm.client import LLMClient
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


class ClaudeCodeLLMClient:
    """以 Claude Code CLI (headless) 產生結構化 JSON 的客戶端。

    Client producing structured JSON via the headless Claude Code CLI.

    介面與 :class:`LLMClient` 一致：``generate_structured_data`` 為唯一公開
    方法，回傳 :class:`LLMGenerateResult`，失敗一律拋 ``LLMServiceError``。

    Attributes:
        _cli_path: 已解析的 claude 執行檔絕對路徑。Resolved CLI path.
        _model_name: 送給 ``--model`` 的模型名或別名。Model name/alias.
        _effort: 送給 ``--effort`` 的力度。Effort level.
        _formatted_model_name: 寫入 Anki tag 與去重紀錄的標籤，格式為
            ``(provider)model@effort``；model 部分去掉 ``claude-`` 前綴
            （如 ``claude-opus-5`` → ``opus-5``），provider 已寫明是
            claude-code，再帶 claude- 是冗餘。Label written to Anki tags
            and dedup records; the ``claude-`` prefix is stripped from the
            model part since the provider segment already says claude-code.
    """

    # 外層重試次數。CLI 內部對結構化輸出已自帶最多 5 次重試，
    # 故此處只需少量的「帶錯誤回饋修復」重試。
    MAX_RETRIES = 2

    # CLI 接受的 effort 值（實測三個模型皆全數接受）
    VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

    # 必須從 subprocess 環境剔除的變數：
    # - CLAUDE_CODE_OAUTH_TOKEN 優先級高於落盤憑證，殘留壞值會造成 401
    # - ANTHROPIC_* 為衛生性剔除，防未來版本改變認證優先級
    SCRUBBED_ENV_VARS = (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    )

    def __init__(self, *, model: str | None = None, effort: str | None = None) -> None:
        """驗證設定並解析 CLI 路徑。

        Validate settings and resolve the CLI path.

        Args:
            model: 覆寫模型名；None 沿用 ``LLM_MODEL_NAME``。Instance-scoped
                model override.
            effort: 覆寫思考深度；None 沿用 ``LLM_CLAUDE_CODE_EFFORT``。
                Instance-scoped effort override.

        Raises:
            LLMServiceError: 當 effort（設定值或覆寫值）非白名單值，或找不到
                claude 執行檔時。Raised when the effort is not in the
                whitelist, or the claude binary cannot be located.
        """
        effort_source = effort if effort is not None else settings.LLM_CLAUDE_CODE_EFFORT
        effort_origin = "effort 覆寫參數" if effort is not None else "LLM_CLAUDE_CODE_EFFORT"
        effort_value = (effort_source or "").strip().lower()
        if effort_value not in self.VALID_EFFORTS:
            raise LLMServiceError(
                f"{effort_origin} 值 '{effort_source}' 無效。"
                f"可用值：{', '.join(sorted(self.VALID_EFFORTS))}。"
                "（注意：CLI 對非法值會靜默回退到預設力度而不報錯，"
                "故必須在此攔截。）"
            )
        self._effort = effort_value

        # token 格式防呆:setup-token 產出是連續的 base64url 字串,內含
        # 空白幾乎必是複製時斷行(2026-08-31 實際發生:token 中段一個空格
        # 讓容器整晚 401)。在啟動時擋下,別讓壞 token 活到生成階段。
        token = (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip()
        if token and any(ch.isspace() for ch in token):
            raise LLMServiceError(
                "LLM_CLAUDE_CODE_OAUTH_TOKEN 內含空白字元——通常是複製 "
                "`claude setup-token` 輸出時被終端斷行切開。請重新完整複製"
                "(token 應為一段連續字串)後重啟。"
            )

        self._cli_path = self._resolve_cli_path()
        self._model_name = (model or settings.LLM_MODEL_NAME or "").strip()
        if not self._model_name:
            raise LLMServiceError("模型名為空：LLM_MODEL_NAME 未設定且未提供覆寫。")

        provider = (settings.LLM_PROVIDER or "").strip().lower()
        provider_prefix = f"({provider})" if provider and provider not in ("google", "openai") else ""
        # 標籤中的模型名去掉 claude- 前綴（claude-opus-5 → opus-5）：
        # provider 段已寫明 claude-code，模型名再帶 claude- 是冗餘。
        # --model 參數仍使用完整的 self._model_name，不受影響。
        display_model = self._model_name.removeprefix("claude-")
        self._formatted_model_name = f"{provider_prefix}{display_model}@{self._effort}"

        self._workdir = self._resolve_workdir()
        self._audit_dir = self._resolve_audit_dir()

        logger.info(
            "ClaudeCodeLLMClient 初始化完成，CLI: %s，目標模型: %s",
            self._cli_path,
            self._formatted_model_name,
        )

    # ------------------------------------------------------------------
    # 初始化輔助（Initialization helpers）
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_cli_path() -> str:
        """依序探測 claude 執行檔位置。

        Locate the claude binary: explicit setting, then the native install
        path, then PATH, then the desktop app's versioned directory.

        Returns:
            claude 執行檔的絕對路徑。Absolute path to the claude binary.

        Raises:
            LLMServiceError: 全部探測失敗時。When every probe fails.
        """
        configured = (settings.LLM_CLAUDE_CODE_CLI_PATH or "").strip()
        if configured:
            if not Path(configured).is_file():
                raise LLMServiceError(
                    f"LLM_CLAUDE_CODE_CLI_PATH 指向的檔案不存在: {configured}"
                )
            return configured

        native = Path.home() / ".local" / "bin" / "claude.exe"
        if native.is_file():
            return str(native)

        on_path = shutil.which("claude")
        if on_path:
            return on_path

        # 桌面版 App 內嵌的版本化路徑（升版後會變，僅作最後兜底）
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates = sorted(Path(appdata).glob("Claude/claude-code/*/claude.exe"))
            if candidates:
                return str(candidates[-1])

        raise LLMServiceError(
            "找不到 claude 執行檔。請安裝 Claude Code CLI（`irm https://claude.ai/install.ps1 | iex`）"
            "並確認已加入 PATH，或以 LLM_CLAUDE_CODE_CLI_PATH 明確指定路徑。"
        )

    @staticmethod
    def _resolve_workdir() -> str:
        """取得（必要時建立）CLI 執行用的專用空目錄。

        Return (creating if needed) the dedicated empty working directory.

        以空目錄作為 cwd，與 ``--safe-mode`` 疊加構成雙保險，確保不會意外
        載入專案的 CLAUDE.md 等脈絡。

        Returns:
            工作目錄的絕對路徑。Absolute path of the working directory.
        """
        configured = (settings.LLM_CLAUDE_CODE_WORKDIR or "").strip()
        workdir = Path(configured) if configured else _BACKEND_DIR / ".claude_code_workdir"
        if not workdir.is_absolute():
            workdir = _BACKEND_DIR / workdir
        workdir.mkdir(parents=True, exist_ok=True)
        return str(workdir)

    @staticmethod
    def _resolve_audit_dir() -> Path | None:
        """取得審計目錄；設定為空字串時代表關閉審計。

        Return the audit directory, or ``None`` when auditing is disabled.

        Returns:
            審計目錄路徑，或 ``None``。The audit directory, or ``None``.
        """
        configured = (settings.LLM_CLAUDE_CODE_AUDIT_DIR or "").strip()
        if not configured:
            return None
        audit_dir = Path(configured)
        if not audit_dir.is_absolute():
            audit_dir = _BACKEND_DIR / audit_dir
        return audit_dir

    # ------------------------------------------------------------------
    # 公開介面（Public API）
    # ------------------------------------------------------------------

    async def generate_structured_data(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, object],
    ) -> LLMGenerateResult:
        """呼叫本機 Claude Code CLI 並取得符合 response_schema 的 JSON 資料。

        Invoke the local Claude Code CLI and obtain JSON data conforming to
        ``response_schema``.

        三道防線：CLI 的 ``--json-schema`` 於 harness 層強制（內建最多 5 次
        重試）、本方法以 ``jsonschema`` 複核、呼叫端 handler 再以 Pydantic
        模型驗證業務語義。

        Args:
            system_prompt: 系統提示，將以 ``--system-prompt`` 整替 CLI 的
                預設系統提示。System prompt, replacing the CLI default.
            user_prompt: 使用者輸入內容，經 stdin 以 UTF-8 傳入。User input,
                piped through stdin as UTF-8.
            response_schema: JSON Schema 定義字典。JSON Schema dict.

        Returns:
            LLMGenerateResult Pydantic 模型實例。An LLMGenerateResult instance.

        Raises:
            LLMServiceError: 認證失效、模型名錯誤、額度耗盡、超時，或所有
                重試後仍無法取得合規 JSON 時。Raised on auth failure, bad
                model name, quota exhaustion, timeout, or when no valid JSON
                is obtained after all retries.
        """
        logger.info(
            "LLM 結構化生成請求 -> model: %s, user_prompt 長度: %d 字元",
            self._formatted_model_name,
            len(user_prompt),
        )

        # CLI 不支援 $defs/$ref，送出前必須展平（複用 LLMClient 的 classmethod）
        resolved_schema = LLMClient._resolve_json_schema(response_schema)
        schema_text = json.dumps(resolved_schema, ensure_ascii=False)

        current_prompt = user_prompt
        last_message = ""

        for attempt in range(1, self.MAX_RETRIES + 1):
            stdout_text, stderr_text = await self._invoke_cli(
                system_prompt, current_prompt, schema_text
            )

            try:
                raw_content, parsed_data = self._parse_and_validate(
                    stdout_text, stderr_text, resolved_schema
                )
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
                model_name=self._formatted_model_name,
                attempt_count=attempt,
            )

        # 訊息刻意包含此字串：生成腳本的錯誤分級表據此走
        # 「記錄失敗並跳過此句」的優雅路徑，而非中止整批。
        raise LLMServiceError(
            f"LLM API 在所有重試後仍回傳空內容（Claude Code CLI）。最後一次原因: {last_message}"
        )

    # ------------------------------------------------------------------
    # 內部實作（Internals）
    # ------------------------------------------------------------------

    def _build_command(self, system_prompt: str, schema_text: str) -> list[str]:
        """組裝 CLI 命令列參數。

        Assemble the CLI argument vector.

        Args:
            system_prompt: 系統提示。The system prompt.
            schema_text: 已展平並序列化的 JSON Schema。Flattened schema JSON.

        Returns:
            完整的命令列參數列表。The full argument list.
        """
        return [
            self._cli_path,
            "-p",
            "--safe-mode",              # 隔離 CLAUDE.md / skills / hooks / MCP
            "--tools", "",              # 停用全部內建工具，純文本生成
            "--no-session-persistence", # 批量呼叫不留會話殘骸
            "--model", self._model_name,
            "--effort", self._effort,
            "--output-format", "json",
            "--system-prompt", system_prompt,
            "--json-schema", schema_text,
        ]

    def _build_env(self) -> dict[str, str]:
        """複製當前環境並依認證模式處理 token 變數。

        Copy the current environment, handling auth variables according to
        the configured credential mode.

        兩種模式（依 ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` 是否設定）：
        - 桌機模式（未設定，預設）：剔除全部 SCRUBBED_ENV_VARS——環境殘留的
          壞 token 優先級高於落盤憑證，會蓋掉有效登入造成 401，故強制走
          落盤憑證。
        - headless 模式（已設定，容器/伺服器）：沒有落盤憑證可用，改為注入
          設定中的 token 供 CLI 認證；ANTHROPIC_* 仍剔除（衛生性防護不變）。
        Desktop mode (token unset) scrubs every auth variable to force the
        on-disk credential; headless mode (token set) injects the configured
        token instead, while ANTHROPIC_* stays scrubbed.

        Returns:
            供 subprocess 使用的環境變數字典。The environment for the
            subprocess.
        """
        env = os.environ.copy()
        for name in self.SCRUBBED_ENV_VARS:
            env.pop(name, None)

        configured_token = (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip()
        if configured_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = configured_token
        return env

    async def _invoke_cli(
        self, system_prompt: str, user_prompt: str, schema_text: str
    ) -> tuple[str, str]:
        """實際執行 CLI 並取回 stdout / stderr。

        Run the CLI and return decoded stdout/stderr.

        以 ``asyncio.to_thread`` 包裝同步 ``subprocess.run``；不可改用
        ``asyncio.create_subprocess_exec``（Windows selector event loop 下
        不支援，本專案多數腳本採用該 policy）。

        Args:
            system_prompt: 系統提示。The system prompt.
            user_prompt: 使用者輸入。The user input.
            schema_text: 已展平的 schema JSON。The flattened schema JSON.

        Returns:
            ``(stdout, stderr)`` 已解碼字串。Decoded stdout and stderr.

        Raises:
            LLMServiceError: 執行超時或無法啟動進程時。On timeout or when the
                process cannot be started.
        """
        command = self._build_command(system_prompt, schema_text)
        timeout = settings.LLM_CLAUDE_CODE_TIMEOUT_SECONDS

        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                command,
                input=user_prompt.encode("utf-8"),
                capture_output=True,
                cwd=self._workdir,
                env=self._build_env(),
                timeout=timeout,
            )

        try:
            completed = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired as timeout_error:
            logger.error("Claude Code CLI 執行超時（%s 秒）。", timeout)
            raise LLMServiceError(
                f"Claude Code CLI 執行超時（{timeout} 秒）。"
            ) from timeout_error
        except OSError as os_error:
            logger.error("無法啟動 Claude Code CLI: %s", os_error)
            raise LLMServiceError(
                f"無法啟動 Claude Code CLI ({self._cli_path}): {os_error}"
            ) from os_error

        # 一律顯式以 UTF-8 解碼，不依賴系統編碼（Windows cp950 會產生亂碼）
        return (
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
        )

    def _parse_and_validate(
        self,
        stdout_text: str,
        stderr_text: str,
        resolved_schema: dict[str, object],
    ) -> tuple[str, dict[str, object]]:
        """解析 CLI 信封、分流失敗形態，並複核輸出是否符合 schema。

        Parse the CLI envelope, triage failures, and re-validate the payload
        against the schema.

        Args:
            stdout_text: CLI 的標準輸出。The CLI's stdout.
            stderr_text: CLI 的標準錯誤。The CLI's stderr.
            resolved_schema: 已展平的 JSON Schema。The flattened schema.

        Returns:
            ``(raw_content, parsed_data)``。

        Raises:
            LLMServiceError: 不可重試的致命失敗（認證、設定錯誤、額度）。
                Fatal, non-retryable failures.
            _RetryableCliError: 可重試的失敗（結構化重試耗盡、JSON 不合規）。
                Retryable failures.
        """
        self._raise_for_fatal_markers(stderr_text)

        envelope = self._extract_envelope(stdout_text, stderr_text)

        if envelope.get("is_error"):
            self._raise_for_error_envelope(envelope, stderr_text)

        raw_content = envelope.get("result")
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise _RetryableCliError("CLI 信封中的 result 欄位為空或非字串。")

        cleaned = LLMClient._strip_markdown_fences(raw_content)

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

        return raw_content, parsed_data

    def _raise_for_fatal_markers(self, stderr_text: str) -> None:
        """偵測 stderr 中的致命標記並拋出對應錯誤。

        Detect fatal markers on stderr and raise accordingly.

        僅檢查 stderr：實測確認這兩個標記由 CLI 專門寫入 stderr，而 stdout
        僅承載 JSON 信封。若連 stdout 一起掃描，生成內容剛好含有這些字串時
        會造成誤判。

        Only stderr is scanned: both markers are written exclusively to
        stderr by the CLI, while stdout carries only the JSON envelope.
        Scanning stdout too would misclassify generated content that happens
        to contain these strings.

        Args:
            stderr_text: CLI 的標準錯誤。The CLI's stderr.

        Raises:
            LLMServiceError: 命中致命標記時。When a fatal marker is present.
        """
        if "unrecognized_model" in stderr_text:
            raise LLMServiceError(
                f"Claude Code CLI 不認識模型名 '{self._model_name}'。"
                "請確認 LLM_MODEL_NAME 為有效別名（opus / sonnet / haiku / fable）或完整模型名。"
            )

        if "--json-schema is not valid JSON" in stderr_text:
            raise LLMServiceError(
                "傳給 Claude Code CLI 的 JSON Schema 無效（展平後仍非合法 JSON）。"
            )

    def _raise_for_error_envelope(
        self, envelope: dict[str, object], stderr_text: str
    ) -> None:
        """依信封內容分流錯誤形態。

        Triage the error envelope into fatal or retryable failures.

        Args:
            envelope: 已解析的 CLI 信封。The parsed CLI envelope.
            stderr_text: CLI 的標準錯誤。The CLI's stderr.

        Raises:
            LLMServiceError: 認證失效或額度耗盡等致命失敗。Fatal failures.
            _RetryableCliError: 結構化輸出重試耗盡等可重試失敗。Retryable
                failures.
        """
        result_text = str(envelope.get("result") or "")
        errors = envelope.get("errors")
        errors_text = "; ".join(str(item) for item in errors) if isinstance(errors, list) else ""
        terminal_reason = str(envelope.get("terminal_reason") or "")
        combined = f"{result_text} {errors_text} {stderr_text}".lower()

        if "not logged in" in combined or "oauth access token is invalid" in combined:
            raise LLMServiceError(
                "Claude Code CLI 未認證或憑證失效。請在終端執行 `claude auth login` 後重試"
                "（可用 `claude auth status` 確認）。"
            )

        # 模型存在但本帳號無存取權（如 claude-mythos-5）。此形態與「模型名打錯」
        # 不同：CLI 不會給 unrecognized_model，而是回一段 api_error 訊息。屬設定
        # 錯誤，重試無用，必須立即拋出而非浪費重試次數。
        # Model exists but this account lacks access. Unlike a typo (which yields
        # `unrecognized_model` on stderr), this returns an api_error message.
        # It is a configuration error - retrying cannot help.
        if "may not have access to it" in combined:
            raise LLMServiceError(
                f"Claude Code CLI 無法使用模型 '{self._model_name}'："
                "該模型不存在或本帳號無存取權。請確認 LLM_MODEL_NAME 為訂閱可用的模型"
                "（別名 opus / sonnet / haiku / fable，或完整模型名）。"
            )

        # 額度耗盡：訊息刻意包含 "Quota"，生成腳本據此走「暫停 60 秒後跳句」路徑
        if any(marker in combined for marker in ("quota", "rate limit", "usage limit", "429")):
            raise LLMServiceError(
                f"Claude Code CLI 回報額度或速率限制 (Quota): {result_text or errors_text}"
            )

        if terminal_reason == "structured_output_retry_exhausted":
            raise _RetryableCliError(
                f"CLI 內建結構化輸出重試已耗盡: {errors_text or result_text}",
                feedback="上次輸出未能符合指定的 JSON Schema，請嚴格依 schema 逐欄位輸出。",
            )

        raise _RetryableCliError(
            f"CLI 回報錯誤 (terminal_reason={terminal_reason}): {result_text or errors_text}"
        )

    @staticmethod
    def _extract_envelope(stdout_text: str, stderr_text: str) -> dict[str, object]:
        """從 stdout 取出 ``--output-format json`` 的信封物件。

        Extract the ``--output-format json`` envelope from stdout.

        CLI 偶爾會在信封前輸出警告行，故取第一個 ``{`` 之後的內容解析。

        Args:
            stdout_text: CLI 的標準輸出。The CLI's stdout.
            stderr_text: CLI 的標準錯誤（僅用於錯誤訊息）。The CLI's stderr,
                used only for error messages.

        Returns:
            解析後的信封字典。The parsed envelope dict.

        Raises:
            LLMServiceError: stdout 中找不到可解析的 JSON 信封時。When no
                parseable envelope is present.
        """
        start = stdout_text.find("{")
        if start == -1:
            raise LLMServiceError(
                f"Claude Code CLI 未回傳 JSON 信封。stderr: {stderr_text.strip()[:300]}"
            )
        try:
            # 以 raw_decode 只吃掉第一個完整的 JSON 物件，
            # 容忍信封後方可能出現的尾隨輸出。
            envelope, _ = json.JSONDecoder().raw_decode(stdout_text[start:])
        except json.JSONDecodeError as decode_error:
            raise LLMServiceError(
                f"無法解析 Claude Code CLI 的 JSON 信封: {decode_error}"
            ) from decode_error

        if not isinstance(envelope, dict):
            raise LLMServiceError("Claude Code CLI 的 JSON 信封不是物件。")
        return envelope

    def _write_audit(
        self, system_prompt: str, user_prompt: str, raw_content: str, attempt: int
    ) -> None:
        """將本次呼叫的 prompt 與輸出寫入審計目錄。

        Persist this call's prompt and output to the audit directory.

        審計失敗不得影響主流程，故所有例外僅記錄警告。

        Args:
            system_prompt: 系統提示。The system prompt.
            user_prompt: 實際送出的使用者輸入。The user input actually sent.
            raw_content: CLI 回傳的原始 JSON 文字。The raw JSON returned.
            attempt: 成功時的嘗試次數。The attempt count on success.
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
                        "model": self._model_name,
                        "effort": self._effort,
                        "model_label": self._formatted_model_name,
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
