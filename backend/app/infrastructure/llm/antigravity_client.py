"""
Antigravity CLI (headless) 結構化輸出客戶端模組（計畫 D8）。

以本機已登入的 Antigravity CLI（``agy``）完成結構化生成，對外介面與其他
provider 完全相同。subprocess 呼叫、schema 展平、payload 複核、修復重試、
路徑解析與審計落盤都在
:class:`~app.infrastructure.llm.cli_base.HeadlessCliLLMClient`；本類只提供
agy 專屬的命令列、stdin 格式、環境隔離與信封分流。

使用邊界與 claude-code 相同：僅供使用者手動發起、有始有終的自有批次任務。

關鍵設計決策（皆有 2026-10-14 實測依據，見計畫書
``llm_provider_hierarchy_FEAT_2026-09-06.md`` §D8「實測紀錄」）：

- **stdin 走 stream-json**：``-p`` 必須帶值、不讀 stdin（``-p -`` 的 ``-`` 會被當成
  字面 prompt），長 prompt 放命令列會撞 Windows 命令列長度上限。改用
  ``--input-format stream-json``，stdin 送一行 ``{"event":"user",...}``；輸出同為
  stream-json（NDJSON，只以 ``\\n`` 分行），取最後一行 ``event=result``
- **讀 ``structured_output``，不讀 ``response``**：``--json-schema`` 由 CLI 強制，
  模型多走一步呼叫 ``finish`` 工具，結果放在信封獨立的 ``structured_output``；
  ``response`` 文字混有散文與 ``toolAction`` 等額外欄位
- **system prompt 併入 stdin**：agy 沒有 ``--system-prompt``，以
  ``<system>…</system>`` 區塊放在 user prompt 前（端到端實測輸出合規）
- **HOME / USERPROFILE 隔離**：print 模式會讀 ``~/.gemini/GEMINI.md``（實測模型
  逐字引出使用者的開發規範），且把會話存在 ``~/.gemini`` 下。指向專用目錄後
  兩者皆隔離；Windows 的憑證在系統 keyring，認證不受影響
- **工具**：print 模式預設開放約 60 個工具，workspace 內寫檔不需許可、執行命令
  被自動拒絕（信封帶 ``denied_actions``、``status`` 仍為 SUCCESS 但無結果）。
  workdir 必須是專用空目錄；被拒且無結果視為可重試失敗，workdir 有殘留檔案時記警告
- **關閉自動更新**：實測期間 agy 自行由 1.1.27 升到 1.3.5、旗標行為隨之改變。
  設 ``AGY_CLI_DISABLE_AUTO_UPDATE=1``，版本由使用者手動 ``agy update``
- **``--model`` 給基底名**（``gemini-3.8-flash``），思考深度另給 ``--effort``；
  ``agy models`` 列出的 ``-<effort>`` 後綴是組合後的顯示名，傳給 ``--model`` 會被拒
- **錯誤分流看 gRPC 狀態名**：``error`` 欄位的固定形態是
  ``<STATUS_NAME> (code N)``，比對狀態名比猜英文措辭穩定；只有 ``error`` 為空時
  才退回看 stderr。服務端暫時不可用（實測當天約四成呼叫遇到 503）標為暫時性
  失敗，由基底退避後重試

English summary:
    Headless Antigravity CLI client. Sends the prompt over stream-json
    stdin (``-p`` cannot read stdin), reads the CLI-enforced
    ``structured_output`` instead of the free-text ``response``, folds the
    system prompt into stdin, isolates HOME so the user's GEMINI.md and
    conversation history stay out, disables the CLI's self-update, and
    triages errors by their gRPC status name.
"""

import json
import logging
import math
import os
import re
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
from app.infrastructure.llm.endpoints import EFFORT_VALUES, TYPE_ANTIGRAVITY, Endpoint

logger = logging.getLogger(__name__)

# 一律從 subprocess 環境剔除的變數前綴：Antigravity IDE 在自己的終端機設這組
# 變數（會話 ID、語言伺服器位址、CSRF token…），從 IDE 終端機啟動後端時
# 會讓 agy 黏到 IDE 的會話上
# Env var prefix always scrubbed: set by the Antigravity IDE's terminals.
_SCRUBBED_ENV_PREFIX = "ANTIGRAVITY_"

# agy 的 error 欄位形態：「Eligibility check failed: UNAVAILABLE (code 503): …」
# The error field carries "<STATUS_NAME> (code N)".
_STATUS_PATTERN = re.compile(r"\b([A-Z][A-Z_]{3,})\s*\(code\s*(\d+)\)")

# 依狀態名分流。只有 UNAVAILABLE 有實測依據（503 資格檢查）；其餘依 gRPC 語義
# Status-name triage; only UNAVAILABLE was observed, the rest follow gRPC semantics.
_QUOTA_STATUSES = frozenset({"RESOURCE_EXHAUSTED"})
_AUTH_STATUSES = frozenset({"UNAUTHENTICATED", "PERMISSION_DENIED"})

# 狀態名缺席時的措辭標記（小寫比對、只看 error 欄位）。只有模型名錯誤有實測
# 依據；額度與認證都未能觸發（1.3.3 changelog：個人額度耗盡會立即回報），
# 取常見措辭
# Wording fallbacks; only the invalid-model marker was observed.
_INVALID_MODEL_MARKERS = ("invalid model selection", "not recognized as a known model")
_QUOTA_MARKERS = ("quota", "rate limit", "resource exhausted")
_AUTH_MARKERS = ("not logged in", "not signed in", "unauthenticated", "login required")
# CLI 自身逾時（--print-timeout）的措辭未實測，只認明確提到該旗標的；籠統的
# 「timed out」可能是網路逾時。主要判斷靠 result 的 duration_seconds（見分流）
_TIMEOUT_MARKERS = ("print timeout", "print-timeout")
# duration_seconds 距 --print-timeout 不到這麼多秒，即視為 CLI 自身逾時
_TIMEOUT_DURATION_SLACK_SECONDS = 5

# 錯誤訊息中 CLI 原文 / stderr 的摘要長度上限
_SUMMARY_LIMIT = 300

# agy 自身逾時比 subprocess 逾時早觸發，讓 CLI 以結構化錯誤結束、而不是被
# subprocess 硬砍。餘裕取「180 秒」與「逾時的 20%」較小者：180 秒涵蓋實測最壞的
# 啟動開銷（agy 的計時起點未驗證，可能在啟動之後）；比例上限讓小設定值不至於
# 算出一兩秒就被中止的 print-timeout
_PRINT_TIMEOUT_MARGIN_SECONDS = 180
_PRINT_TIMEOUT_MARGIN_RATIO = 0.2

# 工具被拒或缺結果時附回 prompt 的修復回饋
_NO_TOOLS_FEEDBACK = (
    "不要呼叫任何工具（不要讀寫檔案、不要執行命令、不要搜尋），"
    "直接依指定的 JSON Schema 輸出完整結果。"
)


class AntigravityLLMClient(HeadlessCliLLMClient):
    """以 Antigravity CLI (headless) 產生結構化 JSON 的客戶端。

    Client producing structured JSON via the headless Antigravity CLI.

    Attributes:
        _cli_path: 已解析的 agy 執行檔絕對路徑。Resolved CLI path.
        _model_name: 送給 ``--model`` 的基底模型名。Base model name.
        _effort: 送給 ``--effort`` 的力度。Effort level.
    """

    PROVIDER_NAME: ClassVar[str] = "antigravity"
    BINARY_NAME: ClassVar[str] = "agy"
    DISPLAY_NAME: ClassVar[str] = "Antigravity CLI"
    SETTINGS_PREFIX: ClassVar[str] = "LLM_ANTIGRAVITY"

    # 總嘗試 3 次（claude 為 2）：2026-10-14 實測約四成呼叫遇到
    # 「Eligibility check failed: UNAVAILABLE (code 503)」，重試即過；
    # 暫時性失敗之間的退避由基底 TRANSIENT_BACKOFF_SECONDS 控制
    MAX_RETRIES: ClassVar[int] = 3

    # agy 1.3.5 的 effort 值域（1.1.27 只有前三檔，自動更新後擴充）；唯一定義在 endpoints.EFFORT_VALUES
    VALID_EFFORTS: ClassVar[frozenset[str]] = EFFORT_VALUES[TYPE_ANTIGRAVITY]
    ENDPOINT_TYPES: ClassVar[frozenset[str]] = frozenset({TYPE_ANTIGRAVITY})

    INSTALL_HINT: ClassVar[str] = (
        "請安裝 Antigravity CLI 並完成登入（Windows 預設裝在 %LOCALAPPDATA%\\agy\\bin，"
        "安裝時會加入使用者 PATH，但安裝前已啟動的程序看不到），或以 LLM_ANTIGRAVITY_CLI_PATH 明確指定路徑。"
    )

    def __init__(
        self,
        *,
        model: str | None = None,
        effort: EffortArg = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        """驗證與路徑解析全在基底；本覆寫另建專用 home 並記初始化 log。

        Validation and path resolution happen in the base (effort, model
        name, CLI path); this override prepares the dedicated home.

        Args:
            model: 覆寫模型名（基底名，如 ``gemini-3.8-flash``）；None 沿用
                ``LLM_MODEL_NAME``。Model override.
            effort: 覆寫思考深度；None 沿用 ``LLM_ANTIGRAVITY_EFFORT``，
                ``OMIT_EFFORT`` 不傳 ``--effort``。Effort override.
            endpoint: 內建 ``antigravity`` 端點。The built-in endpoint.

        Raises:
            LLMServiceError: effort 非白名單值、模型名為空、
                ``LLM_ANTIGRAVITY_CLI_PATH`` 指向不存在的檔案、找不到 agy 執行檔，
                或無法建立專用 home 時。
        """
        super().__init__(model=model, effort=effort, endpoint=endpoint)
        if settings.LLM_ANTIGRAVITY_ISOLATE_HOME:
            self._ensure_home_dir()
        logger.info(
            "AntigravityLLMClient 初始化完成，CLI: %s，目標模型: %s",
            self._cli_path,
            self.model_label,
        )

    @property
    def home_dir(self) -> Path:
        """隔離用的專用 home：工作目錄旁的 ``<workdir>_home``。

        The dedicated home used for isolation, next to the workdir.

        刻意不放在 workdir 裡面：workdir 是 agy 的 workspace，模型可以列出與寫入
        其中檔案，不該看得到自己的會話紀錄。初始為空，之後每次呼叫累積一個會話
        檔，不會自動清理。
        """
        workdir = Path(self._workdir)
        return workdir.with_name(f"{workdir.name}_home")

    # ------------------------------------------------------------------
    # Hook 實作（Hook implementations）
    # ------------------------------------------------------------------

    @classmethod
    def _runtime(cls) -> CliRuntime:
        """從 ``LLM_ANTIGRAVITY_*`` 設定組出執行參數。Runtime from settings."""
        return CliRuntime(
            cli_path_setting=settings.LLM_ANTIGRAVITY_CLI_PATH,
            workdir_setting=settings.LLM_ANTIGRAVITY_WORKDIR,
            timeout_seconds=settings.LLM_ANTIGRAVITY_TIMEOUT_SECONDS,
            audit_dir_setting=settings.LLM_ANTIGRAVITY_AUDIT_DIR,
            effort_setting=settings.LLM_ANTIGRAVITY_EFFORT,
        )

    def _extra_cli_candidates(self) -> list[Path]:
        """Windows 安裝路徑 ``%LOCALAPPDATA%\\agy\\bin\\agy.exe``。

        The Windows install location (2026-10-14 實測 A8). 官方安裝會把它加入
        使用者 PATH，但在安裝前就已啟動的程序（後端、終端機）拿不到新的 PATH，
        故留這個兜底。
        """
        local_appdata = os.environ.get("LOCALAPPDATA")
        if os.name != "nt" or not local_appdata:
            return []
        return [Path(local_appdata) / "agy" / "bin" / "agy.exe"]

    def _build_command(self, system_prompt: str, schema_text: str) -> list[str]:
        """組裝 CLI 命令列參數；system prompt 走 stdin（見 ``_build_stdin``）。

        Assemble the CLI argument vector; the system prompt travels on stdin.
        ``self._effort`` 為 None（建構時收到 ``OMIT_EFFORT``）時省略 ``--effort``，
        由 agy 用自己的預設（2026-10-15 實測可正常完成）。
        """
        print_timeout = self._print_timeout_seconds()
        effort_args = ["--effort", self._effort] if self._effort else []
        return [
            self._cli_path,
            "--input-format", "stream-json",   # prompt 經 stdin（-p 不讀 stdin）
            "--output-format", "stream-json",  # stream-json 輸入的必要搭配
            # 限制終端機工具（縱深防禦；實測 headless 下執行命令本就會被自動拒絕）
            "--sandbox",
            "--disable-slash-commands",        # 台詞以 / 開頭時不被當成指令
            "--model", self._model_name,
            *effort_args,
            "--print-timeout", f"{print_timeout}s",
            "--json-schema", schema_text,
        ]

    def _build_stdin(self, system_prompt: str, user_prompt: str) -> str:
        """組出一行 stream-json 使用者訊息，system prompt 以區塊併入。

        Compose one stream-json user message with the system prompt folded in
        as a delimited block (agy has no ``--system-prompt``). ``json.dumps``
        會把內容中的 ``\\n`` 跳脫，整則訊息保證只佔一行。
        """
        content = f"<system>\n{system_prompt}\n</system>\n\n{user_prompt}"
        message = {"event": "user", "message": {"role": "user", "content": content}}
        return json.dumps(message, ensure_ascii=False) + "\n"

    def _build_env(self) -> dict[str, str]:
        """剔除 IDE 變數、關閉自動更新，並（預設）隔離 HOME。

        Scrub IDE variables, disable self-update and, by default, isolate HOME.

        不用基底的 ``SCRUBBED_ENV_VARS``：IDE 設的變數名不固定（實測只看到其中
        一部分），改以前綴剔除。隔離開關每次呼叫才讀設定；關閉時 GEMINI.md 會
        進入生成脈絡。

        Raises:
            LLMServiceError: 無法建立專用 home 時。
        """
        env = super()._build_env()
        for name in list(env):
            if name.upper().startswith(_SCRUBBED_ENV_PREFIX):
                env.pop(name)
        env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1"
        if settings.LLM_ANTIGRAVITY_ISOLATE_HOME:
            home = self._ensure_home_dir()
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
        return env

    async def _invoke_cli(
        self, system_prompt: str, stdin_text: str, schema_text: str
    ) -> tuple[str, str]:
        """執行 CLI 後檢查 workdir 是否仍為空。

        Run the CLI, then check the workdir is still empty.

        workspace 內寫檔不需許可（實測 A4）：留下的檔案會被下一次呼叫的模型看到，
        跨呼叫污染脈絡，卻不會出現在 ``denied_actions``。這裡只記警告、不刪除，
        讓使用者決定。
        """
        output = await super()._invoke_cli(system_prompt, stdin_text, schema_text)
        try:
            leftovers = sorted(path.name for path in Path(self._workdir).iterdir())
        except OSError as scan_error:
            logger.warning("無法檢查 agy 工作目錄 %s: %s", self._workdir, scan_error)
        else:
            if leftovers:
                logger.warning(
                    "agy 在工作目錄 %s 留下檔案（可能污染後續呼叫，請檢查後清除）: %s",
                    self._workdir,
                    leftovers[:20],
                )
        return output

    def _extract_result(self, stdout_text: str, stderr_text: str) -> str:
        """取最後一個 ``event=result``，分流失敗，回傳 ``structured_output`` 的 JSON 文字。

        Take the last ``event=result``, triage failures, and return the
        ``structured_output`` serialized as JSON text.

        Raises:
            LLMServiceError: 沒有任何事件（用法錯誤）、status 不明、模型選擇被拒、
                未登入、CLI 自身逾時等致命失敗。
            LLMQuotaError: 額度或速率限制。
            _RetryableCliError: 服務暫時不可用、CLI 中途結束（暫時性）；工具被拒、
                缺少結構化結果（附修復回饋）。
        """
        result = self._find_result_event(stdout_text, stderr_text)

        status = result.get("status")
        if status not in ("SUCCESS", "ERROR"):
            # 信封格式漂移（手動 agy update 後）：重試只會每句燒三次慢速呼叫
            raise LLMServiceError(
                f"Antigravity CLI 的 result 信封格式不符預期（status={status!r}，"
                f"欄位={sorted(result)}）；可能是 agy 版本變更。"
            )
        if status == "ERROR":
            self._raise_for_error_result(result, stderr_text)

        denied = result.get("denied_actions")
        denied_names = ", ".join(
            str(item.get("display_name") or item.get("action"))
            for item in (denied if isinstance(denied, list) else [])
            if isinstance(item, dict)
        )
        structured = result.get("structured_output")
        if isinstance(structured, dict):
            if denied:
                # 被拒的工具沒有副作用；模型之後仍經 finish 產出結果，就採用
                logger.warning("agy 曾嘗試呼叫被拒的工具（%s），但仍產出結構化結果，採用之。", denied_names)
            return json.dumps(structured, ensure_ascii=False)

        if denied:
            raise _RetryableCliError(
                f"agy 嘗試呼叫工具被拒（{denied_names or denied}），未產出結構化結果。",
                feedback=_NO_TOOLS_FEEDBACK,
            )
        raise _RetryableCliError(
            "agy 的 result 沒有可用的 structured_output"
            f"（實際型別：{type(structured).__name__}；模型未經 finish 工具輸出結果）。",
            feedback=_NO_TOOLS_FEEDBACK,
        )

    # ------------------------------------------------------------------
    # agy 專屬的輔助（agy-specific helpers）
    # ------------------------------------------------------------------

    def _print_timeout_seconds(self) -> int:
        """傳給 ``--print-timeout`` 的秒數：比 subprocess 逾時早觸發。

        每次呼叫才讀設定，/setconfig 調整下一次即生效。下限 1 秒——agy 1.3.5
        把 0 解讀為不限。
        """
        timeout = self._runtime().timeout_seconds
        margin = min(_PRINT_TIMEOUT_MARGIN_SECONDS, timeout * _PRINT_TIMEOUT_MARGIN_RATIO)
        return max(1, math.floor(timeout - margin))

    def _ensure_home_dir(self) -> Path:
        """建立（若不存在）專用 home。

        Raises:
            LLMServiceError: 無法建立時——不讓它被基底包成「無法啟動 CLI」。
        """
        home = self.home_dir
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError as mkdir_error:
            raise LLMServiceError(
                f"無法建立 agy 專用 home 目錄 {home}: {mkdir_error}"
                "（可設 LLM_ANTIGRAVITY_WORKDIR 換位置，或設 LLM_ANTIGRAVITY_ISOLATE_HOME=false）"
            ) from mkdir_error
        return home

    @staticmethod
    def _find_result_event(stdout_text: str, stderr_text: str) -> dict[str, object]:
        """從 stream-json 輸出由後往前找最後一個 ``event=result``。

        Find the last ``event=result`` event in the stream-json output.

        只以 ``\\n`` 分行（NDJSON 的行界）：``str.splitlines()`` 還會在
        U+2028 / U+0085 等字元切行，生成內容含這些字元時會把 result 行切斷。

        Raises:
            LLMServiceError: 一個事件、一行 JSON 都沒有——實測唯一觀察到的形態是
                用法錯誤（只寫 stderr），屬確定性錯誤，不論 exit code 一律不重試。
            _RetryableCliError: 有其他事件或被截斷的 JSON 行但沒有 result（CLI 中途
                結束、輸出被截斷），視為暫時性失敗。
        """
        seen_events: list[str] = []
        undecodable = 0
        for line in reversed(stdout_text.split("\n")):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                undecodable += 1
                continue
            if not isinstance(event, dict):
                continue
            seen_events.append(str(event.get("event")))
            if event.get("event") == "result" and isinstance(event.get("result"), dict):
                return event["result"]

        stderr_tail = stderr_text.strip()[-_SUMMARY_LIMIT:] or "（無）"
        if not seen_events and not undecodable:
            # 不論 exit code：基底不傳 returncode，且實測唯一的形態是用法錯誤
            raise LLMServiceError(
                "Antigravity CLI 沒有輸出任何事件（多為命令列用法錯誤）。"
                f"stderr 尾端: {stderr_tail}"
            )
        raise _RetryableCliError(
            f"Antigravity CLI 中途結束、未回傳 result（已見事件: {sorted(set(seen_events))}，"
            f"無法解析的行數: {undecodable}）。stderr 尾端: {stderr_tail}",
            transient=True,
        )

    def _raise_for_error_result(self, result: dict[str, object], stderr_text: str) -> None:
        """分流 ``status=ERROR`` 的 result。Triage an error result.

        優先看 ``error`` 欄位的 gRPC 狀態名，再看措辭；只有 ``error`` 為空時才
        退回 stderr——stderr 混有說明與 log，裡面的數字或字眼會造成誤判。
        """
        error_text = str(result.get("error") or "")
        source = error_text or stderr_text.strip()
        lowered = source.lower()
        match = _STATUS_PATTERN.search(source)
        status_name = match.group(1) if match else ""
        detail = (source[:_SUMMARY_LIMIT] if error_text else source[-_SUMMARY_LIMIT:]) or "（無訊息）"
        context = f"conversation_id={result.get('conversation_id') or '（無）'}"

        if any(marker in lowered for marker in _INVALID_MODEL_MARKERS):
            raise LLMServiceError(
                f"Antigravity CLI 拒絕模型選擇（model='{self._model_name}', effort='{self._effort}'）。"
                "--model 要給基底名（如 gemini-3.8-flash，去掉 `agy models` 列出的 -<effort> 後綴），"
                f"並確認該模型支援此 effort。（CLI 訊息：{detail}）"
            )

        # 額度耗盡：訊息刻意包含 "Quota"，生成腳本據此走「暫停 60 秒後跳句」路徑
        # 解析得到狀態名時只依狀態名與 code 分流：說明文字可能提到 quota 服務、
        # unauthenticated proxy 之類，措辭只在沒有狀態名時才當依據
        status_code = match.group(2) if match else ""
        if status_name:
            is_quota = status_name in _QUOTA_STATUSES or status_code == "429"
            is_auth = status_name in _AUTH_STATUSES
        else:
            is_quota = any(marker in lowered for marker in _QUOTA_MARKERS)
            is_auth = any(marker in lowered for marker in _AUTH_MARKERS)

        if is_quota:
            raise LLMQuotaError(f"Antigravity CLI 回報額度或速率限制 (Quota)（{context}）: {detail}")

        if is_auth:
            isolation_hint = (
                "若在自己的終端機可正常使用 agy，可能是 HOME 隔離所致："
                "試設 LLM_ANTIGRAVITY_ISOLATE_HOME=false。"
                if settings.LLM_ANTIGRAVITY_ISOLATE_HOME
                else ""
            )
            raise LLMServiceError(
                f"Antigravity CLI 未登入、憑證失效或無權限。請在終端執行 `agy` 完成登入後重試。"
                f"{isolation_hint}（CLI 訊息：{detail}）"
            )

        # CLI 自身逾時：措辭未實測，主要看實耗時間是否已到 --print-timeout。
        # 與基底的 subprocess 逾時一致，視為致命——重試只會每次再等滿一輪
        print_timeout = self._print_timeout_seconds()
        duration = result.get("duration_seconds")
        hit_print_timeout = (
            isinstance(duration, (int, float))
            and duration >= print_timeout - _TIMEOUT_DURATION_SLACK_SECONDS
        )
        if hit_print_timeout or any(marker in lowered for marker in _TIMEOUT_MARKERS):
            raise LLMServiceError(
                f"Antigravity CLI 自身逾時（--print-timeout={print_timeout}s，實耗 {duration}s，{context}）。"
                f"可調高 LLM_ANTIGRAVITY_TIMEOUT_SECONDS。（CLI 訊息：{detail}）"
            )

        # 其餘一律視為暫時性，最常見的是「Eligibility check failed: UNAVAILABLE (code 503)」
        raise _RetryableCliError(
            f"Antigravity CLI 回報錯誤（status={status_name or '未標示'}，{context}）: {detail}",
            transient=True,
        )
