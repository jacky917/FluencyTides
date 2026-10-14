"""
Claude Code CLI (headless) 結構化輸出客戶端模組。

以本機已登入的 Claude Code CLI（`claude -p`）取代計費 API 完成結構化生成，
對外提供與其他 provider 完全相同的介面，因此上下游（各 Task Handler、
生成腳本）切換 provider 時零感知。

使用邊界（重要）：
    本 provider 僅供「使用者手動發起、有始有終的自有批次任務」使用。
    禁止形態：常駐 daemon、對外網路 endpoint、供第三方使用、
    接入多用戶生產路徑。

2026-09-06 層級化：subprocess 呼叫、schema 展平、payload 複核、修復重試、
路徑解析與審計落盤全部上移到
:class:`~app.infrastructure.llm.cli_base.HeadlessCliLLMClient`；本類只剩
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

English summary:
    Headless Claude Code CLI client. Drives the locally authenticated
    `claude -p` binary to produce schema-conformant JSON. Intended solely
    for user-initiated, finite batch jobs. The subprocess pipeline lives in
    ``HeadlessCliLLMClient``; this class supplies only the claude-specific
    argv, credential environment, envelope triage and install-path probe.
"""

import json
import logging
import os
import shutil  # noqa: F401 - 測試經本模組 patch ``shutil.which``；探測本體在 cli_base
from pathlib import Path
from typing import ClassVar

from app.core.config import settings
from app.core.exceptions import LLMQuotaError, LLMServiceError
from app.infrastructure.llm.cli_base import (
    CliRuntime,
    HeadlessCliLLMClient,
    _RetryableCliError,
)

logger = logging.getLogger(__name__)


class ClaudeCodeLLMClient(HeadlessCliLLMClient):
    """以 Claude Code CLI (headless) 產生結構化 JSON 的客戶端。

    Client producing structured JSON via the headless Claude Code CLI.

    Attributes:
        _cli_path: 已解析的 claude 執行檔絕對路徑。Resolved CLI path.
        _model_name: 送給 ``--model`` 的模型名或別名。Model name/alias.
        _effort: 送給 ``--effort`` 的力度。Effort level.
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

    # CLI 接受的 effort 值（實測三個模型皆全數接受）
    VALID_EFFORTS: ClassVar[frozenset[str]] = frozenset({"low", "medium", "high", "xhigh", "max"})

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

    def __init__(self, *, model: str | None = None, effort: str | None = None) -> None:
        """驗證 effort 白名單與 token 格式，並解析 CLI 路徑（順序由基底維持）。

        Validate effort and the token format, then resolve the CLI path.

        Args:
            model: 覆寫模型名；None 沿用 ``LLM_MODEL_NAME``。Model override.
            effort: 覆寫思考深度；None 沿用 ``LLM_CLAUDE_CODE_EFFORT``。
                Effort override.

        Raises:
            LLMServiceError: effort 非白名單值、token 內含空白、或找不到
                claude 執行檔時。
        """
        super().__init__(model=model, effort=effort)
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
        """從 ``LLM_CLAUDE_CODE_*`` 設定組出執行參數。Runtime from settings."""
        return CliRuntime(
            cli_path_setting=settings.LLM_CLAUDE_CODE_CLI_PATH,
            workdir_setting=settings.LLM_CLAUDE_CODE_WORKDIR,
            timeout_seconds=settings.LLM_CLAUDE_CODE_TIMEOUT_SECONDS,
            audit_dir_setting=settings.LLM_CLAUDE_CODE_AUDIT_DIR,
            effort_setting=settings.LLM_CLAUDE_CODE_EFFORT,
        )

    def _check_credentials(self) -> None:
        """token 格式防呆：內含空白幾乎必是複製時斷行。

        Reject a token containing whitespace — almost always a copy/paste
        line-break (2026-08-31: one stray space cost a container a night of
        401s). Caught at construction so a bad token never reaches generation.
        """
        token = (settings.LLM_CLAUDE_CODE_OAUTH_TOKEN or "").strip()
        if token and any(ch.isspace() for ch in token):
            raise LLMServiceError(
                "LLM_CLAUDE_CODE_OAUTH_TOKEN 內含空白字元——通常是複製 "
                "`claude setup-token` 輸出時被終端斷行切開。請重新完整複製"
                "(token 應為一段連續字串)後重啟。"
            )

    def _extra_cli_candidates(self) -> list[Path]:
        """桌面版 App 內嵌的版本化路徑（升版後會變，僅作最後兜底）。

        The desktop app's versioned install directory, used as a last resort.
        """
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return []
        candidates = sorted(Path(appdata).glob("Claude/claude-code/*/claude.exe"))
        return [candidates[-1]] if candidates else []

    def _build_command(self, system_prompt: str, schema_text: str) -> list[str]:
        """組裝 CLI 命令列參數。Assemble the CLI argument vector."""
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
        """依認證模式處理 token 變數。

        Handle auth variables according to the configured credential mode.

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
        """解析 CLI 信封並分流失敗形態。

        Parse the CLI envelope and triage failures.

        Raises:
            LLMServiceError: 認證、設定、額度等致命失敗。
            _RetryableCliError: 結構化重試耗盡、result 為空等可重試失敗。
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
        """偵測 stderr 中的致命標記並拋出對應錯誤。

        僅檢查 stderr：實測確認這兩個標記由 CLI 專門寫入 stderr，而 stdout
        僅承載 JSON 信封。若連 stdout 一起掃描，生成內容剛好含有這些字串時
        會造成誤判。
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
        """依信封內容分流錯誤形態。Triage the error envelope."""
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
        """從 stdout 取出 ``--output-format json`` 的信封物件。

        CLI 偶爾會在信封前輸出警告行，故取第一個 ``{`` 之後的內容解析；
        以 ``raw_decode`` 只吃掉第一個完整的 JSON 物件，容忍尾隨輸出。

        Raises:
            LLMServiceError: stdout 中找不到可解析的 JSON 信封時。
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
