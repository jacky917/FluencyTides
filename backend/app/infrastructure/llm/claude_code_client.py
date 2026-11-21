"""Structured-output client module for the Claude Code CLI (headless).

Claude Code CLI (headless) 結構化輸出客戶端模組。

Uses the locally logged-in Claude Code CLI (`claude -p`) instead of a billed API for structured generation,
offering exactly the same interface as the other providers, so upstream and downstream (each Task Handler, the
generation scripts) are unaware when the provider is switched.

Usage boundary (important):
    This provider is only for "user-initiated, finite batch jobs of one's own". Forbidden forms: a resident
    daemon, a public network endpoint, use by third parties, and integration into multi-user production paths.

2026-09-06 hierarchy: the subprocess call, schema flattening, payload re-validation, repair retries, path
resolution, and audit writing all moved up to :class:`~app.infrastructure.llm.cli_base.HeadlessCliLLMClient`;
this class keeps only the claude-specific parts: the command-line flags, the auth environment (scrubbed on a
desktop / token injected when headless), CLI envelope parsing and failure triage, and the desktop-app install
path as a fallback.

Key design decisions (all backed by measurements, see ``docs/archive/claude_cli_env_setup_FEAT_2026-08-27.md``):

- the subprocess environment scrubs ``CLAUDE_CODE_OAUTH_TOKEN`` by default: it takes priority over the on-disk
  credentials, and a leftover bad value overrides a valid login and causes 401. Exception: in a headless
  environment (container/server), once ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` is set, that value is injected instead,
  because there are no on-disk credentials there and the env token is the only way to authenticate
  (docs/archive/claude_cli_in_container_FEAT_2026-08-29.md §D2).
- on an invalid ``--effort`` value the CLI "silently falls back" to the default effort without reporting an
  error, so the value must be validated against the allowlist on the Python side (the base ``_validate_effort``).

以本機已登入的 Claude Code CLI（`claude -p`）取代計費 API 完成結構化生成，
對外提供與其他 provider 完全相同的介面，因此上下游（各 Task Handler、
生成腳本）切換 provider 時零感知。

使用邊界（重要）：
    本 provider 僅供「使用者手動發起、有始有終的自有批次任務」使用。
    禁止形態：常駐 daemon、對外網路 endpoint、供第三方使用、
    接入多用戶生產路徑。

2026-09-06 層級化：subprocess 呼叫、schema 展平、payload 複核、修復重試、
路徑解析與審計落盤全部上移到
``HeadlessCliLLMClient``；本類只剩
claude 專屬的部分——命令列旗標、認證環境（桌機剔除 / headless 注入 token）、
CLI 信封解析與失敗分流、桌面版安裝路徑兜底。

關鍵設計決策（皆有實測依據，見
``docs/archive/claude_cli_env_setup_FEAT_2026-08-27.md``）：
- subprocess 環境預設剔除 ``CLAUDE_CODE_OAUTH_TOKEN``：其優先級高於
  落盤憑證，殘留的壞值會蓋掉有效登入造成 401。例外：headless 環境
  （容器/伺服器）設定 ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` 後改為注入該值
  ——那裡沒有落盤憑證，env token 是唯一認證途徑
  （docs/archive/claude_cli_in_container_FEAT_2026-08-29.md §D2）。
- ``--effort`` 非法值時 CLI 會「靜默回退」到預設力度而不報錯，故必須在
  Python 端以白名單驗證（基底 ``_validate_effort``）。
"""

import json
import logging
import os
import shutil  # noqa: F401 - 測試經本模組 patch ``shutil.which``；探測本體在 cli_base
from pathlib import Path
from typing import ClassVar

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMServiceError
from app.infrastructure.llm.base import EffortArg
from app.infrastructure.llm.cli_base import (
    CliRuntime,
    HeadlessCliLLMClient,
    _RetryableCliError,
)
from app.infrastructure.llm.endpoints import EFFORT_VALUES, TYPE_CLAUDE_CODE, Endpoint

logger = logging.getLogger(__name__)


class ClaudeCodeLLMClient(HeadlessCliLLMClient):
    """Client that produces structured JSON through the Claude Code CLI (headless).

    以 Claude Code CLI (headless) 產生結構化 JSON 的客戶端。

    Attributes:
        _cli_path: the resolved path of the claude executable
            已解析的 claude 執行檔路徑
        _model_name: the model name or alias sent to ``--model``
            送給 ``--model`` 的模型名或別名
        _effort: the effort sent to ``--effort``; ``None`` omits the flag
            送給 ``--effort`` 的力度；None 時省略此旗標
    """

    PROVIDER_NAME: ClassVar[str] = "claude-code"
    BINARY_NAME: ClassVar[str] = "claude"
    DISPLAY_NAME: ClassVar[str] = "Claude Code CLI"
    SETTINGS_PREFIX: ClassVar[str] = "LLM_CLAUDE_CODE"

    # 標籤中的模型名去掉 claude- 前綴（claude-opus-5 → opus-5）：
    # provider 段已寫明 claude-code，模型名再帶 claude- 是冗餘。
    # --model 參數仍使用完整的 _model_name，不受影響。
    MODEL_LABEL_STRIP_PREFIX: ClassVar[str] = "claude-"

    # 外層重試次數。CLI 內部對結構化輸出已自帶最多 5 次重試，
    # 故此處只需少量的「帶錯誤回饋修復」重試。
    MAX_RETRIES: ClassVar[int] = 2

    # CLI 接受的 effort 值（實測三個模型皆全數接受）；值域唯一定義在 endpoints.EFFORT_VALUES
    VALID_EFFORTS: ClassVar[frozenset[str]] = EFFORT_VALUES[TYPE_CLAUDE_CODE]
    ENDPOINT_TYPES: ClassVar[frozenset[str]] = frozenset({TYPE_CLAUDE_CODE})

    # 必須從 subprocess 環境剔除的變數：
    # - CLAUDE_CODE_OAUTH_TOKEN 優先級高於落盤憑證，殘留壞值會造成 401
    # - ANTHROPIC_* 為衛生性剔除，防未來版本改變認證優先級
    SCRUBBED_ENV_VARS: ClassVar[tuple[str, ...]] = (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    )

    INSTALL_HINT: ClassVar[str] = (
        "請安裝 Claude Code CLI（`irm https://claude.ai/install.ps1 | iex`）"
        "並確認已加入 PATH，或以 LLM_CLAUDE_CODE_CLI_PATH 明確指定路徑。"
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: EffortArg = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        """Validate the effort allowlist and the token format, then resolve the CLI path (order kept by the base).

        驗證 effort 白名單與 token 格式，並解析 CLI 路徑（順序由基底維持）。

        Args:
            model: the model-name override; ``None`` keeps ``LLM_MODEL_NAME``
                覆寫模型名；None 沿用 ``LLM_MODEL_NAME``
            effort: the thinking-depth override; ``None`` keeps ``LLM_CLAUDE_CODE_EFFORT``, ``OMIT_EFFORT`` sends no
                ``--effort``
                覆寫思考深度；None 沿用 ``LLM_CLAUDE_CODE_EFFORT``，``OMIT_EFFORT`` 不傳 ``--effort``
            endpoint: the built-in ``claude-code`` endpoint
                內建 ``claude-code`` 端點

        Raises:
            LLMServiceError: if the endpoint type mismatches, effort is not an allowlisted value, the token contains
                whitespace, the model name is empty, or the claude executable cannot be found
                若端點類型不符、effort 非白名單值、token 內含空白、模型名為空，或找不到 claude 執行檔
        """
        super().__init__(model=model, effort=effort, endpoint=endpoint)
        logger.info(
            "ClaudeCodeLLMClient 初始化完成，CLI: %s，目標模型: %s",
            self._cli_path,
            self.model_label,
        )

    # ------------------------------------------------------------------
    # Hook 實作（Hook implementations）
    # ------------------------------------------------------------------

    @classmethod
    def _runtime(cls) -> CliRuntime:
        """Assemble the runtime parameters from the ``LLM_CLAUDE_CODE_*`` settings.

        從 ``LLM_CLAUDE_CODE_*`` 設定組出執行參數。
        """
        return CliRuntime(
            cli_path_setting=settings.LLM_CLAUDE_CODE_CLI_PATH,
            workdir_setting=settings.LLM_CLAUDE_CODE_WORKDIR,
            timeout_seconds=settings.LLM_CLAUDE_CODE_TIMEOUT_SECONDS,
            audit_dir_setting=settings.LLM_CLAUDE_CODE_AUDIT_DIR,
            effort_setting=settings.LLM_CLAUDE_CODE_EFFORT,
        )

    def _check_credentials(self) -> None:
        """Guard the token format: whitespace inside is almost always a line break from copying.

        token 格式防呆：內含空白幾乎必是複製時斷行。

        On 2026-08-31 one stray space cost a container a night of 401s. It is caught at construction, so a bad
        token never reaches generation. An unset token passes.

        2026-08-31 曾因一個多餘空白讓容器整夜 401。在建構時攔下，壞 token 就不會進到生成階段。未設定 token 時通過。

        Raises:
            LLMServiceError: if ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` contains whitespace
                若 ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` 內含空白字元
        """
        token = (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip()
        if token and any(ch.isspace() for ch in token):
            raise LLMServiceError(
                "LLM_CLAUDE_CODE_OAUTH_TOKEN 內含空白字元——通常是複製 "
                "`claude setup-token` 輸出時被終端斷行切開。請重新完整複製"
                "(token 應為一段連續字串)後重啟。"
            )

    def _extra_cli_candidates(self) -> list[Path]:
        """Return the versioned path embedded in the desktop app (changes after upgrades; only a last resort).

        桌面版 App 內嵌的版本化路徑（升版後會變，僅作最後兜底）。

        Only the last match in sorted order under ``%APPDATA%`` is returned; empty when ``APPDATA`` is unset.

        只回傳 ``%APPDATA%`` 下排序後的最後一個符合者；未設 ``APPDATA`` 時為空。
        """
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return []
        candidates = sorted(Path(appdata).glob("Claude/claude-code/*/claude.exe"))
        return [candidates[-1]] if candidates else []

    def _build_command(self, system_prompt: str, schema_text: str) -> list[str]:
        """Assemble the CLI command-line arguments.

        組裝 CLI 命令列參數。

        When ``self._effort`` is ``None`` (``OMIT_EFFORT`` was received at construction), ``--effort`` is omitted and
        the CLI uses its own default (verified to complete normally with claude 2.1.292 on 2026-10-15).

        ``self._effort`` 為 None（建構時收到 ``OMIT_EFFORT``）時省略 ``--effort``，
        由 CLI 用自己的預設（2026-10-15 以 claude 2.1.292 實測可正常完成）。
        """
        effort_args = ["--effort", self._effort] if self._effort else []
        return [
            self._cli_path,
            "-p",
            "--safe-mode",              # 隔離 CLAUDE.md / skills / hooks / MCP
            "--tools", "",              # 停用全部內建工具，純文本生成
            "--no-session-persistence", # 批量呼叫不留會話殘骸
            "--model", self._model_name,
            *effort_args,
            "--output-format", "json",
            "--system-prompt", system_prompt,
            "--json-schema", schema_text,
        ]

    def _build_env(self) -> dict[str, str]:
        """Handle the token variables according to the authentication mode.

        依認證模式處理 token 變數。

        Two modes (depending on whether ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` is set):

        - desktop mode (unset, the default): scrub all SCRUBBED_ENV_VARS; a bad token left in the environment takes
          priority over the on-disk credentials and would override a valid login, causing 401, so the on-disk
          credentials are forced.
        - headless mode (set, container/server): no on-disk credentials are available, so the configured token is
          injected for the CLI to authenticate; ANTHROPIC_* is still scrubbed (the hygiene guard is unchanged).

        兩種模式（依 ``LLM_CLAUDE_CODE_OAUTH_TOKEN`` 是否設定）：
        - 桌機模式（未設定，預設）：剔除全部 SCRUBBED_ENV_VARS——環境殘留的
          壞 token 優先級高於落盤憑證，會蓋掉有效登入造成 401，故強制走
          落盤憑證。
        - headless 模式（已設定，容器/伺服器）：沒有落盤憑證可用，改為注入
          設定中的 token 供 CLI 認證；ANTHROPIC_* 仍剔除（衛生性防護不變）。
        """
        env = super()._build_env()
        configured_token = (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip()
        if configured_token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = configured_token
        return env

    def _extract_result(self, stdout_text: str, stderr_text: str) -> str:
        """Parse the CLI envelope and triage the failure forms.

        解析 CLI 信封並分流失敗形態。

        Raises:
            LLMQuotaError: if the CLI reports a quota or rate limit
                若 CLI 回報額度或速率限制
            LLMServiceError: on fatal failures such as authentication, settings, or a missing or unparsable envelope
                認證、設定、信封缺失或無法解析等致命失敗
            _RetryableCliError: on retryable failures such as exhausted structured-output retries or an empty result
                結構化重試耗盡、result 為空等可重試失敗
        """
        self._raise_for_fatal_markers(stderr_text)

        envelope = self._extract_envelope(stdout_text, stderr_text)

        if envelope.get("is_error"):
            self._raise_for_error_envelope(envelope, stderr_text)

        raw_content = envelope.get("result")
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise _RetryableCliError("CLI 信封中的 result 欄位為空或非字串。")
        return raw_content

    # ------------------------------------------------------------------
    # claude 專屬的信封分流（Claude-specific envelope triage）
    # ------------------------------------------------------------------

    def _raise_for_fatal_markers(self, stderr_text: str) -> None:
        """Detect fatal markers in stderr and raise the matching error.

        偵測 stderr 中的致命標記並拋出對應錯誤。

        Only stderr is checked: measurements confirm the CLI writes these two markers specifically to stderr, while
        stdout carries only the JSON envelope. Scanning stdout as well would misjudge generated content that
        happens to contain these strings.

        僅檢查 stderr：實測確認這兩個標記由 CLI 專門寫入 stderr，而 stdout
        僅承載 JSON 信封。若連 stdout 一起掃描，生成內容剛好含有這些字串時
        會造成誤判。

        Raises:
            LLMServiceError: if the model name is unrecognized or the JSON Schema is invalid
                若模型名不被認得，或 JSON Schema 無效
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
        """Triage the error form according to the envelope content; this method always raises.

        依信封內容分流錯誤形態；此方法一律拋出例外。

        Raises:
            LLMQuotaError: if the quota or rate limit is exhausted
                若額度或速率限制耗盡
            LLMServiceError: if not authenticated, or the account cannot use the model
                若未認證，或本帳號無法使用該模型
            _RetryableCliError: for every other error, with repair feedback when the CLI's structured-output retries
                are exhausted
                其他所有錯誤；CLI 內建結構化輸出重試耗盡時附修復回饋
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
        if "may not have access to it" in combined:
            raise LLMServiceError(
                f"Claude Code CLI 無法使用模型 '{self._model_name}'："
                "該模型不存在或本帳號無存取權。請確認 LLM_MODEL_NAME 為訂閱可用的模型"
                "（別名 opus / sonnet / haiku / fable，或完整模型名）。"
            )

        # 額度耗盡：訊息刻意包含 "Quota"，生成腳本據此走「暫停 60 秒後跳句」路徑
        if any(marker in combined for marker in ("quota", "rate limit", "usage limit", "429")):
            raise LLMQuotaError(
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
        """Extract the ``--output-format json`` envelope object from stdout.

        從 stdout 取出 ``--output-format json`` 的信封物件。

        The CLI occasionally prints warning lines before the envelope, so parsing starts at the first ``{``;
        ``raw_decode`` consumes only the first complete JSON object, tolerating trailing output.

        CLI 偶爾會在信封前輸出警告行，故取第一個 ``{`` 之後的內容解析；
        以 ``raw_decode`` 只吃掉第一個完整的 JSON 物件，容忍尾隨輸出。

        Returns:
            the envelope dict
            信封字典

        Raises:
            LLMServiceError: if no parsable JSON envelope object is found in stdout
                stdout 中找不到可解析的 JSON 信封物件時
        """
        start = stdout_text.find("{")
        if start == -1:
            raise LLMServiceError(
                f"Claude Code CLI 未回傳 JSON 信封。stderr: {stderr_text.strip()[:300]}"
            )
        try:
            envelope, _ = json.JSONDecoder().raw_decode(stdout_text[start:])
        except json.JSONDecodeError as decode_error:
            raise LLMServiceError(
                f"無法解析 Claude Code CLI 的 JSON 信封: {decode_error}"
            ) from decode_error

        if not isinstance(envelope, dict):
            raise LLMServiceError("Claude Code CLI 的 JSON 信封不是物件。")
        return envelope
