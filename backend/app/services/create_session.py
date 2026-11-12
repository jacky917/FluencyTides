"""網頁的建卡工作階段：全站同一時間只有一份草稿（``docs/wip/server_drafts_FEAT_2026-11-04.md``）。

The web create session: one draft for the whole site at a time.

- 草稿只存在記憶體（D1）：後端重啟就沒了，使用者接受這個代價（R1）。
- 一把 ``asyncio.Lock`` 保護所有狀態轉換（D6）：鎖內只做「檢查 → 改狀態」，呼叫 LLM、寫 Anki 都在鎖外，
  所以兩個請求同時到時只有一個會成功，另一個拿到 409 與目前的草稿。
- 生成在背景跑（D4）：請求一秒內回應，不受 Cloudflare 約 100 秒的限制。生成中不能廢棄（D8），
  要等結果回來（含逾時失敗）。
- 只有網頁的入口（``app/api/drafts.py``、``app/api/handlers.py``）經過這裡；TG 與本機腳本不經過（D2、D10）。
- 依賴單一 process（R4）：TG bot 也在同一個 process；``--workers`` 會讓每個 worker 各有一份。

Drafts live in memory only; one lock guards every transition; generation
runs in the background. Only the web entry points go through this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.exceptions import (
    CreateSessionBusyError,
    DraftBusyError,
    DraftNotFailedError,
    DraftNotFoundError,
    DraftStateTooLargeError,
    DraftVersionConflictError,
    FluencyTidesError,
)

logger = logging.getLogger(__name__)

#: 草稿的狀態（D3）。``form_creating`` 是填表建卡暫時佔用槽位（D2）：沒有輸入、沒有生成結果。
#: Draft statuses; ``form_creating`` is a form create holding the slot.
DraftStatus = Literal["generating", "ready", "failed", "creating", "form_creating"]

#: 網頁編輯狀態（``state``）的大小上限（UTF-8 位元組，D5）。Size limit of the editing state.
MAX_STATE_BYTES = 256 * 1024

#: 生成：草稿輸入 → ``{"params", "suggested_tags"}``。Draft input to the generated result.
Generate = Callable[[dict[str, object]], Awaitable[dict[str, object]]]

_BUSY_MESSAGE = "目前有一份建卡草稿還沒處理完，請先建立或廢棄"
_FORM_CREATING_MESSAGE = "正在建立另一張卡片，請稍候"
_OTHER_HANDLER_MESSAGE = "目前的草稿是另一種卡片的，請先建立或廢棄那一份"
_GONE_MESSAGE = "這份草稿已不在（可能已建立、在其他地方廢棄，或伺服器重新啟動過）"
_UNEXPECTED_MESSAGE = "生成時發生未預期的錯誤，已通知管理員；可以重試或廢棄"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


@dataclass
class Draft:
    """一份建卡草稿（§5）。

    One create draft.

    狀態與欄位的對應（D3）：``result`` 只在 ``ready``、``creating`` 時有值；``error_*`` 只在 ``failed`` 時有值；
    ``form_creating``（填表建卡佔用）沒有 ``input``、``result``、``generation_started_at``。狀態只經由
    ``CreateSession`` 的轉換改變，每次變更（含存編輯狀態）都呼叫 :meth:`touch`。

    Attributes:
        id: UUID4 hex。
        handler_name: handler 名稱。The handler.
        status: 狀態。The status.
        input: 生成的輸入（``DRAFT_PARAMS`` 驗證後）；可能另帶系統鍵（重複決定、略過查重複的原因，
            見 ``task_handlers.base.DRAFT_SYSTEM_KEYS``）。The generation input.
        result: 生成結果 ``{"params", "suggested_tags"}``。The generated result.
        state: 網頁的編輯狀態，後端不解讀。The web editing state.
        error_code、error_message: 生成失敗時。On failure.
        version: 每次變更加一。Bumped on every change.
        created_at、updated_at: UTC 時間。
        generation_started_at: 最近一次開始生成的時間；填表建卡佔用時為 None。
    """

    handler_name: str
    status: DraftStatus
    input: dict[str, object] | None = None
    result: dict[str, object] | None = None
    state: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    version: int = 1
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    generation_started_at: datetime | None = None

    def touch(self) -> None:
        """有變更：版本加一、更新時間。Bump the version after a change."""
        self.version += 1
        self.updated_at = _now()

    def to_dict(self) -> dict[str, object]:
        """API 的形狀（§4.1）。The API shape."""
        error = {"error_code": self.error_code, "message": self.error_message} if self.error_code else None
        return {
            "id": self.id,
            "handler": self.handler_name,
            "status": self.status,
            "input": self.input,
            "result": self.result,
            "state": self.state,
            "error": error,
            "version": self.version,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "generation_started_at": _iso(self.generation_started_at),
        }


class CreateSession:
    """全站唯一的建卡槽位與它的鎖（D1、D2）。

    The site-wide create slot and its lock.

    Attributes:
        current: 目前的草稿；沒有時為 None。只讀；改變一律經由這個類別的方法（鎖內）。
            The current draft; read-only outside this class.
    """

    def __init__(self) -> None:
        """建立空的槽位。Create an empty slot."""
        self.current: Draft | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        # 啟動背景生成的方式：測試換成收集 coroutine、自己執行（TestClient 每個請求的事件迴圈結束時，
        # 留在上面的 task 會被丟掉）
        self._spawn: Callable[[Coroutine[Any, Any, None]], object] = asyncio.create_task

    def snapshot(self) -> dict[str, object] | None:
        """目前的草稿（API 形狀），沒有時為 None。The current draft, or None."""
        return self.current.to_dict() if self.current else None

    def get(self, draft_id: str) -> Draft:
        """槽位裡的這份草稿（只讀用途，例如重試前取 handler 名稱）。The current draft with this ID.

        Raises:
            DraftNotFoundError: 槽位裡不是這份草稿。Not the current draft.
        """
        draft = self.current
        if draft is None or draft.id != draft_id:
            raise DraftNotFoundError(_GONE_MESSAGE)
        return draft

    # =========================================================================
    # 生成
    # =========================================================================

    async def start(self, handler_name: str, parameters: dict[str, object], generate: Generate) -> dict[str, object]:
        """開始生成（D4）：槽位必須是空的；建一份 ``generating`` 的草稿，背景生成。

        Start generating; the slot must be empty.

        Args:
            handler_name: handler 名稱。The handler.
            parameters: 已驗證的草稿輸入。The validated input.
            generate: 生成函式，結果回來時寫回草稿。The generator.

        Returns:
            新的草稿。The new draft.

        Raises:
            CreateSessionBusyError: 已經有一份草稿。A draft already exists.
        """
        async with self._lock:
            if self.current is not None:
                raise CreateSessionBusyError(_BUSY_MESSAGE, self.current.to_dict())
            draft = Draft(handler_name=handler_name, status="generating", input=dict(parameters))
            draft.generation_started_at = draft.created_at
            # 先啟動背景生成，成功了才放進槽位：啟動失敗時槽位不會卡在生成中
            self._launch(draft, generate)
            self.current = draft
            return draft.to_dict()

    async def retry(
        self,
        draft_id: str,
        version: int,
        generate: Generate,
        parameters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """生成失敗後重試：沿用原本的輸入，給了 ``parameters`` 就換成新的輸入（D3）。

        Retry a failed draft, optionally with new input.

        Raises:
            DraftNotFoundError: 槽位裡不是這份草稿。Not the current draft.
            DraftNotFailedError: 不是失敗的狀態。Not failed.
            DraftVersionConflictError: 版本不符。Stale version.
        """
        async with self._lock:
            draft = self.get(draft_id)
            if draft.status != "failed":
                raise DraftNotFailedError("這份草稿不是生成失敗的狀態，不能重試", draft.to_dict())
            self._check_version(draft, version)
            if parameters is not None:
                draft.input = dict(parameters)
            # 先啟動背景生成，成功了才改狀態：啟動失敗時草稿仍是失敗，可以再試或廢棄
            self._launch(draft, generate)
            draft.status = "generating"
            draft.error_code = draft.error_message = None
            draft.generation_started_at = _now()
            draft.touch()
            return draft.to_dict()

    def _launch(self, draft: Draft, generate: Generate) -> None:
        """啟動背景生成（鎖內呼叫）。Start the background generation; called under the lock."""
        task = self._spawn(self._generate(draft.id, draft.handler_name, dict(draft.input or {}), generate))
        if isinstance(task, asyncio.Task):
            task.add_done_callback(self._on_task_done)
            self._task = task

    async def _generate(
        self, draft_id: str, handler_name: str, parameters: dict[str, object], generate: Generate
    ) -> None:
        """背景生成，結果寫回同一份草稿。Run the generation and write the result back.

        業務錯誤（``FluencyTidesError``，主要是 LLM 的）照原本的錯誤碼與訊息存進草稿；其他例外記 log、
        發 TG 警報（背景工作不經過 HTTP 的崩潰攔截），存成不透露內部細節的通用失敗。
        """
        result: dict[str, object] | None = None
        error: tuple[str, str] | None = None
        try:
            result = await generate(parameters)
        except asyncio.CancelledError:
            # 只在關機時發生；槽位在記憶體裡，重啟後本來就是空的
            logger.info("草稿 %s（%s）的生成在關機時被取消", draft_id, handler_name)
            raise
        except FluencyTidesError as exc:
            logger.warning("草稿 %s（%s）生成失敗：%s %s", draft_id, handler_name, exc.error_code, exc.message)
            error = (exc.error_code, exc.message)
        except Exception as exc:  # noqa: BLE001 - 背景工作沒有 HTTP 回應，失敗要存進草稿讓網頁看得到
            logger.exception("草稿 %s（%s）生成時發生未預期的錯誤", draft_id, handler_name)
            _alert(f"background:draft/{handler_name}/{draft_id}", repr(exc))
            error = ("DRAFT_GENERATION_FAILED", _UNEXPECTED_MESSAGE)
        async with self._lock:
            self._task = None
            draft = self.current
            if draft is None or draft.id != draft_id or draft.status != "generating":
                # 生成中不能廢棄（D8），正常不會走到這裡：表示有地方違反了這個前提
                logger.error("草稿 %s 生成結束時已不在槽位裡（現在是 %s），丟掉結果", draft_id, draft and draft.id)
                return
            if error is None:
                draft.status = "ready"
                draft.result = result
            else:
                draft.status = "failed"
                draft.error_code, draft.error_message = error
            draft.touch()

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        """安全網：背景 task 在寫回之前異常結束時，把草稿標成失敗，槽位才不會一直卡在生成中。

        Safety net: mark the draft failed if the task died before writing back.
        """
        if task.cancelled() or task.exception() is None:
            return
        logger.error("背景生成異常結束，草稿改標為失敗", exc_info=task.exception())
        _alert("background:draft/task", repr(task.exception()))
        draft = self.current
        if draft is not None and draft.status == "generating":
            # 回呼在事件迴圈裡同步執行，鎖內的轉換都沒有 await，不會與它交錯
            draft.status = "failed"
            draft.error_code, draft.error_message = "DRAFT_GENERATION_FAILED", _UNEXPECTED_MESSAGE
            draft.touch()
        if self._task is task:
            self._task = None

    # =========================================================================
    # 預覽中的修改、廢棄
    # =========================================================================

    async def save_state(self, draft_id: str, state: dict[str, object], version: int) -> dict[str, object]:
        """存網頁的編輯狀態（D5）：只在 ``ready`` 時、版本相符才寫入。

        Save the web editing state.

        Raises:
            DraftNotFoundError: 槽位裡不是這份草稿。Not the current draft.
            DraftBusyError: 不是 ``ready``。Not ready.
            DraftVersionConflictError: 版本不符。Stale version.
            DraftStateTooLargeError: 超過上限。Too large.
        """
        size = len(json.dumps(state, ensure_ascii=False).encode("utf-8"))
        if size > MAX_STATE_BYTES:
            raise DraftStateTooLargeError(f"編輯內容太大（{size // 1024} KB），無法儲存")
        async with self._lock:
            draft = self.get(draft_id)
            self._require_ready(draft)
            self._check_version(draft, version)
            draft.state = state
            draft.touch()
            return draft.to_dict()

    async def discard(self, draft_id: str) -> None:
        """廢棄（D8）：只限 ``ready``、``failed``；生成中、建立中都不行。

        Discard a ready or failed draft.

        Raises:
            DraftNotFoundError: 槽位裡不是這份草稿。Not the current draft.
            DraftBusyError: 生成中或建立中。Generating or being created.
        """
        async with self._lock:
            draft = self.get(draft_id)
            if draft.status not in ("ready", "failed"):
                raise DraftBusyError(self._busy_message(draft), draft.to_dict())
            self.current = None

    # =========================================================================
    # 建卡
    # =========================================================================

    async def begin_create(self, handler_name: str, draft_id: str | None, version: int | None) -> str:
        """建卡前佔用槽位（D2、D7）。

        Claim the slot before writing to Anki.

        - 從草稿建卡（``draft_id``、``version`` 一起帶）：槽位裡必須是這份草稿、同一個 handler、``ready``、
          版本相符，改成 ``creating``。
        - 填表建卡（都不帶）：槽位必須是空的，放進一份 ``form_creating`` 的佔用。

        Args:
            handler_name: 建卡的 handler。The handler.
            draft_id: 草稿 ID；填表建卡為 None。The draft.
            version: 草稿版本，帶 ``draft_id`` 時必填（API 以 ``DraftRef`` 保證成對）。The version.

        Returns:
            佔用的草稿 ID，交給 :meth:`finish_create`。The claimed draft ID.

        Raises:
            CreateSessionBusyError: 填表建卡時已經有一份草稿，或草稿是另一種卡片的。The slot is taken.
            DraftNotFoundError: 槽位裡不是這份草稿。Not the current draft.
            DraftBusyError: 不是 ``ready``。Not ready.
            DraftVersionConflictError: 版本不符。Stale version.
        """
        async with self._lock:
            if draft_id is None:
                if self.current is not None:
                    raise CreateSessionBusyError(_BUSY_MESSAGE, self.current.to_dict())
                draft = Draft(handler_name=handler_name, status="form_creating")
                self.current = draft
                return draft.id
            draft = self.get(draft_id)
            if draft.handler_name != handler_name:
                raise CreateSessionBusyError(_OTHER_HANDLER_MESSAGE, draft.to_dict())
            self._require_ready(draft)
            self._check_version(draft, version if version is not None else -1)
            draft.status = "creating"
            draft.touch()
            return draft.id

    async def finish_create(self, draft_id: str, *, created: bool) -> None:
        """建卡結束：成功就清空槽位；失敗時草稿退回 ``ready``，填表建卡的佔用直接清掉（D7）。

        Release the slot after creating.

        Args:
            draft_id: :meth:`begin_create` 回傳的 ID。The claimed draft.
            created: ``NoteService.create`` 是否正常回傳。寫入 Anki 之後才拋錯（或請求被取消）也算失敗，
                草稿會退回 ``ready``（計畫書 §11.4）。Whether the create call returned normally.
        """
        async with self._lock:
            draft = self.current
            if draft is None or draft.id != draft_id:
                # 建立中不能廢棄（D8）：走到這裡表示有地方違反了這個前提
                logger.error("建卡結束時槽位裡不是佔用的草稿 %s（現在是 %s）", draft_id, draft and draft.id)
                return
            if created or draft.status == "form_creating":
                self.current = None
                return
            draft.status = "ready"
            draft.touch()

    # =========================================================================
    # 關機
    # =========================================================================

    async def shutdown(self) -> None:
        """關機時取消背景生成。Cancel the background generation at shutdown.

        claude-code、antigravity 的子程序在執行緒裡跑，取消 task 停不下它（R2）；
        槽位在記憶體裡，重啟後本來就是空的，所以不必寫回狀態。
        """
        task = self._task
        if task and not task.done():
            task.cancel()

    # =========================================================================
    # 共用檢查
    # =========================================================================

    @staticmethod
    def _busy_message(draft: Draft) -> str:
        """草稿不能操作時的說明，依目前狀態而定。Why the draft cannot be touched now."""
        match draft.status:
            case "generating":
                return "還在生成，請等結果回來"
            case "creating":
                return "正在建立，請稍候"
            case "form_creating":
                return _FORM_CREATING_MESSAGE
            case "failed":
                return "這份草稿生成失敗，請重試或廢棄"
            case "ready":
                return _BUSY_MESSAGE

    @classmethod
    def _require_ready(cls, draft: Draft) -> None:
        """草稿必須是 ``ready``（修改、建立的前提）。The draft must be ready."""
        if draft.status != "ready":
            raise DraftBusyError(cls._busy_message(draft), draft.to_dict())

    @staticmethod
    def _check_version(draft: Draft, version: int) -> None:
        if draft.version != version:
            raise DraftVersionConflictError("這份草稿在其他地方改過了", draft.to_dict())


def _alert(where: str, error: str) -> None:
    """背景工作的未預期錯誤發 TG 警報（HTTP 請求的由 ``global_crash_middleware`` 處理）。

    Send a Telegram crash alert for a background failure.
    """
    from app.bot.alerts import send_crash_alert

    try:
        asyncio.get_running_loop().create_task(send_crash_alert(url=where, method="TASK", error_msg=error))
    except RuntimeError:
        logger.warning("沒有事件迴圈，無法發送警報：%s %s", where, error)


#: process 內唯一的建卡槽位（D1）。The process-wide create slot.
create_session = CreateSession()


def get_create_session() -> CreateSession:
    """FastAPI 依賴：回傳 process 內唯一的建卡槽位。The process-wide create slot."""
    return create_session
