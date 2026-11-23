"""The web card-creation session: one draft for the whole site at a time.

網頁的建卡工作階段：全站同一時間只有一份草稿。

Plan: ``docs/wip/server_drafts_FEAT_2026-11-04.md``.

- Drafts live only in memory (D1): they are gone when the backend restarts, a cost the user accepts (R1).
- One ``asyncio.Lock`` guards every state transition (D6): inside the lock only "check → change state" happens;
  calling the LLM and writing to Anki happen outside it, so when two requests arrive at once only one succeeds
  and the other gets 409 with the current draft.
- Generation runs in the background (D4): the request responds within a second, unaffected by Cloudflare's
  roughly 100-second limit. A draft cannot be discarded while generating (D8); the result (including a timeout
  failure) must come back first.
- Only the web entry points (``app/api/drafts.py``, ``app/api/handlers.py``) go through here; TG and local scripts
  do not (D2, D10).
- Relies on a single process (R4): the TG bot runs in the same process; ``--workers`` would give every worker its
  own copy.

計畫書：``docs/wip/server_drafts_FEAT_2026-11-04.md``。

- 草稿只存在記憶體（D1）：後端重啟就沒了，使用者接受這個代價（R1）。
- 一把 ``asyncio.Lock`` 保護所有狀態轉換（D6）：鎖內只做「檢查 → 改狀態」，呼叫 LLM、寫 Anki 都在鎖外，
  所以兩個請求同時到時只有一個會成功，另一個拿到 409 與目前的草稿。
- 生成在背景跑（D4）：請求一秒內回應，不受 Cloudflare 約 100 秒的限制。生成中不能廢棄（D8），
  要等結果回來（含逾時失敗）。
- 只有網頁的入口（``app/api/drafts.py``、``app/api/handlers.py``）經過這裡；TG 與本機腳本不經過（D2、D10）。
- 依賴單一 process（R4）：TG bot 也在同一個 process；``--workers`` 會讓每個 worker 各有一份。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine, Mapping
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

#: The draft statuses (D3). ``form_creating`` is a form-based card creation temporarily holding the slot (D2): no
#: input and no generated result.
#: 草稿的狀態（D3）。``form_creating`` 是填表建卡暫時佔用槽位（D2）：沒有輸入、沒有生成結果。
DraftStatus = Literal["generating", "ready", "failed", "creating", "form_creating"]

#: The size limit of the web editing state (``state``), in UTF-8 bytes (D5).
#: 網頁編輯狀態（``state``）的大小上限（UTF-8 位元組，D5）。
MAX_STATE_BYTES = 256 * 1024

#: Generation: draft input → ``{"params", "suggested_tags"}``.
#: 生成：草稿輸入 → ``{"params", "suggested_tags"}``。
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
    """One card-creation draft (§5).

    一份建卡草稿（§5）。

    How statuses map to fields (D3): ``result`` has a value only in ``ready`` and ``creating``; ``error_*`` only in
    ``failed``; ``form_creating`` (a form-based creation holding the slot) has no ``input``, ``result`` or
    ``generation_started_at``. The status changes only through ``CreateSession`` transitions, and every change
    (including saving the editing state) calls :meth:`touch`.

    狀態與欄位的對應（D3）：``result`` 只在 ``ready``、``creating`` 時有值；``error_*`` 只在 ``failed`` 時有值；
    ``form_creating``（填表建卡佔用）沒有 ``input``、``result``、``generation_started_at``。狀態只經由
    ``CreateSession`` 的轉換改變，每次變更（含存編輯狀態）都呼叫 ``touch``。

    Attributes:
        id: a UUID4 hex string
            UUID4 hex
        handler_name: the handler name
            handler 名稱
        status: the status
            狀態
        input: the generation input (after ``DRAFT_PARAMS`` validation); may also carry system keys (the duplicate
            decision, the reason for skipping the duplicate check; see ``task_handlers.base.DRAFT_SYSTEM_KEYS``)
            生成的輸入（``DRAFT_PARAMS`` 驗證後）；可能另帶系統鍵（重複決定、略過查重複的原因，
            見 ``task_handlers.base.DRAFT_SYSTEM_KEYS``）
        result: the generated result ``{"params", "suggested_tags"}``
            生成結果 ``{"params", "suggested_tags"}``
        state: the web editing state, not interpreted by the backend
            網頁的編輯狀態，後端不解讀
        error_code: the error code when generation failed
            生成失敗時的錯誤碼
        error_message: the error message when generation failed
            生成失敗時的錯誤訊息
        version: incremented on every change
            每次變更加一
        created_at: the creation time, in UTC
            建立時間，UTC
        updated_at: the last update time, in UTC
            最後更新時間，UTC
        generation_started_at: when the latest generation started; ``None`` for a form-based creation hold
            最近一次開始生成的時間；填表建卡佔用時為 ``None``
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
        """Record a change: increment the version and update the time.

        有變更：版本加一、更新時間。
        """
        self.version += 1
        self.updated_at = _now()

    def to_dict(self) -> dict[str, object]:
        """Return this draft in the API shape (§4.1).

        回傳 API 的形狀（§4.1）。

        Returns:
            the draft as a dict; times are ISO 8601 strings ending in ``Z``, and ``error`` is ``None`` unless
            generation failed
            草稿字典；時間是以 ``Z`` 結尾的 ISO 8601 字串，``error`` 除生成失敗外為 ``None``
        """
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
    """The site-wide card-creation slot and its lock (D1, D2).

    全站唯一的建卡槽位與它的鎖（D1、D2）。

    Attributes:
        current: the current draft, or ``None`` if there is none. Read-only; it changes only through this class's
            methods (under the lock).
            目前的草稿；沒有時為 ``None``。只讀；改變一律經由這個類別的方法（鎖內）。
    """

    def __init__(self) -> None:
        """Create an empty slot.

        建立空的槽位。
        """
        self.current: Draft | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        # 啟動背景生成的方式：測試換成收集 coroutine、自己執行（TestClient 每個請求的事件迴圈結束時，
        # 留在上面的 task 會被丟掉）
        self._spawn: Callable[[Coroutine[Any, Any, None]], object] = asyncio.create_task

    def snapshot(self) -> dict[str, object] | None:
        """Return the current draft in the API shape.

        回傳目前的草稿（API 形狀）。

        Returns:
            the current draft, or ``None`` if there is none
            目前的草稿；沒有時為 ``None``
        """
        return self.current.to_dict() if self.current else None

    def get(self, draft_id: str) -> Draft:
        """Return the draft in the slot with this ID.

        回傳槽位裡的這份草稿。

        For read-only use, for example getting the handler name before a retry.

        只讀用途，例如重試前取 handler 名稱。

        Args:
            draft_id: the draft ID
                草稿 ID

        Returns:
            the current draft
            目前的草稿

        Raises:
            DraftNotFoundError: if the draft in the slot is not this one
                若槽位裡不是這份草稿
        """
        draft = self.current
        if draft is None or draft.id != draft_id:
            raise DraftNotFoundError(_GONE_MESSAGE)
        return draft

    # =========================================================================
    # 生成
    # =========================================================================

    async def start(self, handler_name: str, parameters: dict[str, object], generate: Generate) -> dict[str, object]:
        """Start generating (D4): the slot must be empty; create a ``generating`` draft and generate in the background.

        開始生成（D4）：槽位必須是空的；建一份 ``generating`` 的草稿，背景生成。

        Args:
            handler_name: the handler name
                handler 名稱
            parameters: the validated draft input
                已驗證的草稿輸入
            generate: the generation function; its result is written back to the draft when it returns
                生成函式，結果回來時寫回草稿

        Returns:
            the new draft in the API shape
            新的草稿（API 形狀）

        Raises:
            CreateSessionBusyError: if a draft already exists
                若已經有一份草稿
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
        """Retry after a failed generation, reusing the original input unless ``parameters`` is given (D3).

        生成失敗後重試：沿用原本的輸入，給了 ``parameters`` 就換成新的輸入（D3）。

        Args:
            draft_id: the draft ID
                草稿 ID
            version: the draft version the caller last saw
                呼叫端最後看到的草稿版本
            generate: the generation function
                生成函式
            parameters: the new validated input; ``None`` keeps the original input
                新的已驗證輸入；``None`` 表示沿用原本的輸入

        Returns:
            the draft, now ``generating``, in the API shape
            改為 ``generating`` 的草稿（API 形狀）

        Raises:
            DraftNotFailedError: if the draft is not in the failed state
                若不是失敗的狀態
            DraftNotFoundError: if the draft in the slot is not this one
                若槽位裡不是這份草稿
            DraftVersionConflictError: if the version does not match
                若版本不符
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
        """Start the background generation; called under the lock.

        啟動背景生成（鎖內呼叫）。
        """
        task = self._spawn(self._generate(draft.id, draft.handler_name, dict(draft.input or {}), generate))
        if isinstance(task, asyncio.Task):
            task.add_done_callback(self._on_task_done)
            self._task = task

    async def _generate(
        self, draft_id: str, handler_name: str, parameters: dict[str, object], generate: Generate
    ) -> None:
        """Run the generation in the background and write the result back to the same draft.

        背景生成，結果寫回同一份草稿。

        Business errors (``FluencyTidesError``, mostly from the LLM) are stored in the draft with their original
        error code and message; any other exception is logged and sent as a TG alert (background work bypasses the
        HTTP crash handler), and stored as a generic failure that reveals no internal details.

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
        """Safety net: mark the draft failed if the background task died before writing back.

        安全網：背景 task 在寫回之前異常結束時，把草稿標成失敗。

        This keeps the slot from staying stuck in generating.

        槽位才不會一直卡在生成中。
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
        """Save the web editing state (D5), only when the draft is ``ready`` and the version matches.

        存網頁的編輯狀態（D5）：只在 ``ready`` 時、版本相符才寫入。

        Args:
            draft_id: the draft ID
                草稿 ID
            state: the editing state; at most ``MAX_STATE_BYTES`` when serialized as UTF-8 JSON
                編輯狀態；序列化成 UTF-8 JSON 後不可超過 ``MAX_STATE_BYTES``
            version: the draft version the caller last saw
                呼叫端最後看到的草稿版本

        Returns:
            the updated draft in the API shape
            更新後的草稿（API 形狀）

        Raises:
            DraftBusyError: if the draft is not ``ready``
                若不是 ``ready``
            DraftNotFoundError: if the draft in the slot is not this one
                若槽位裡不是這份草稿
            DraftStateTooLargeError: if the state exceeds the limit
                若超過上限
            DraftVersionConflictError: if the version does not match
                若版本不符
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
        """Discard a draft (D8): only ``ready`` or ``failed``; not while generating or creating.

        廢棄（D8）：只限 ``ready``、``failed``；生成中、建立中都不行。

        Args:
            draft_id: the draft ID
                草稿 ID

        Raises:
            DraftBusyError: if the draft is generating or being created
                若生成中或建立中
            DraftNotFoundError: if the draft in the slot is not this one
                若槽位裡不是這份草稿
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
        """Claim the slot before creating a card (D2, D7).

        建卡前佔用槽位（D2、D7）。

        - Creating from a draft (``draft_id`` and ``version`` given together): the slot must hold this draft, for the
          same handler, ``ready``, with a matching version; it becomes ``creating``.
        - Form-based creation (neither given): the slot must be empty; a ``form_creating`` hold is put in.

        - 從草稿建卡（``draft_id``、``version`` 一起帶）：槽位裡必須是這份草稿、同一個 handler、``ready``、
          版本相符，改成 ``creating``。
        - 填表建卡（都不帶）：槽位必須是空的，放進一份 ``form_creating`` 的佔用。

        Args:
            handler_name: the handler creating the card
                建卡的 handler
            draft_id: the draft ID; ``None`` for form-based creation
                草稿 ID；填表建卡為 ``None``
            version: the draft version, required with ``draft_id`` (the API guarantees the pair via ``DraftRef``);
                a missing version is treated as a version conflict
                草稿版本，帶 ``draft_id`` 時必填（API 以 ``DraftRef`` 保證成對）；缺少時視為版本不符

        Returns:
            the claimed draft ID, to pass to :meth:`finish_create`
            佔用的草稿 ID，交給 ``finish_create``

        Raises:
            CreateSessionBusyError: if a draft already exists during form-based creation, or the draft is for
                another kind of card
                若填表建卡時已經有一份草稿，或草稿是另一種卡片的
            DraftBusyError: if the draft is not ``ready``
                若不是 ``ready``
            DraftNotFoundError: if the draft in the slot is not this one
                若槽位裡不是這份草稿
            DraftVersionConflictError: if the version does not match
                若版本不符
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
        """Finish creating: on success clear the slot; on failure return the draft to ``ready`` (D7).

        建卡結束：成功就清空槽位；失敗時草稿退回 ``ready``（D7）。

        A form-based creation hold is cleared directly either way. If the slot no longer holds the claimed draft,
        this only logs an error.

        填表建卡的佔用不論成敗都直接清掉。槽位裡已不是佔用的草稿時只記 error。

        Args:
            draft_id: the ID returned by :meth:`begin_create`
                ``begin_create`` 回傳的 ID
            created: whether ``NoteService.create`` returned normally. An error raised after writing to Anki (or a
                cancelled request) also counts as failure, and the draft returns to ``ready`` (plan §11.4).
                ``NoteService.create`` 是否正常回傳。寫入 Anki 之後才拋錯（或請求被取消）也算失敗，
                草稿會退回 ``ready``（計畫書 §11.4）。
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

    async def finish_apply(
        self, draft_id: str, *, done: bool, progress: Mapping[str, object]
    ) -> dict[str, object] | None:
        """Finish writing an "AI 整理" draft (``person_llm_fill`` plan D7), claimed with :meth:`begin_create`.

        「AI 整理」的草稿寫入結束（``person_llm_fill`` 計畫書 D7），之前以 ``begin_create`` 佔用。

        When everything chosen was written the slot is cleared. Otherwise the draft returns to ``ready`` with the
        progress merged into ``result["params"]`` (the new card's note ID, the indexes already written, and the
        reasons the others were rejected), so writing again only writes the rest and never creates a second card.
        If the slot no longer holds the claimed draft, this only logs an error.

        勾選的都寫入了就清空槽位；否則草稿退回 ``ready``，進度併進 ``result["params"]``（新卡的 Note ID、已寫入的
        序號、其他項目被拒絕的原因），再寫一次只會補寫剩下的，不會建出第二張卡。槽位裡已不是佔用的草稿時只記 error。

        Args:
            draft_id: the ID returned by :meth:`begin_create`
                ``begin_create`` 回傳的 ID
            done: whether every chosen proposal was written
                勾選的建議是否都寫入了
            progress: the keys to merge into ``result["params"]``
                要併進 ``result["params"]`` 的鍵

        Returns:
            the draft in the API shape when it stays, or ``None`` when the slot was cleared (or lost)
            草稿還在時為 API 形狀的草稿；槽位清空了（或已不在）時為 ``None``
        """
        async with self._lock:
            draft = self.current
            if draft is None or draft.id != draft_id:
                # 寫入中不能廢棄（D8）：走到這裡表示有地方違反了這個前提
                logger.error("AI 整理寫入結束時槽位裡不是佔用的草稿 %s（現在是 %s）", draft_id, draft and draft.id)
                return None
            if done:
                self.current = None
                return None
            result = dict(draft.result or {})
            result["params"] = {**dict(result.get("params") or {}), **progress}  # type: ignore[arg-type]
            draft.result = result
            draft.status = "ready"
            draft.touch()
            return draft.to_dict()

    # =========================================================================
    # 關機
    # =========================================================================

    async def shutdown(self) -> None:
        """Cancel the background generation at shutdown.

        關機時取消背景生成。

        The claude-code and antigravity subprocesses run in threads, so cancelling the task cannot stop them (R2);
        the slot lives in memory and is empty after a restart anyway, so no state needs to be written back.

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
        """Return why the draft cannot be operated on now, depending on its current status.

        回傳草稿不能操作時的說明，依目前狀態而定。
        """
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
        """Require the draft to be ``ready`` (the precondition for editing and creating).

        草稿必須是 ``ready``（修改、建立的前提）。

        Raises:
            DraftBusyError: if the draft is not ``ready``
                若不是 ``ready``
        """
        if draft.status != "ready":
            raise DraftBusyError(cls._busy_message(draft), draft.to_dict())

    @staticmethod
    def _check_version(draft: Draft, version: int) -> None:
        if draft.version != version:
            raise DraftVersionConflictError("這份草稿在其他地方改過了", draft.to_dict())


def _alert(where: str, error: str) -> None:
    """Send a TG alert for an unexpected error in background work.

    背景工作的未預期錯誤發 TG 警報。

    Errors in HTTP requests are handled by ``global_crash_middleware`` instead. Without a running event loop, only a
    warning is logged.

    HTTP 請求的由 ``global_crash_middleware`` 處理。沒有執行中的事件迴圈時只記 warning。
    """
    from app.bot.alerts import send_crash_alert

    try:
        asyncio.get_running_loop().create_task(send_crash_alert(url=where, method="TASK", error_msg=error))
    except RuntimeError:
        logger.warning("沒有事件迴圈，無法發送警報：%s %s", where, error)


#: The only card-creation slot in the process (D1).
#: process 內唯一的建卡槽位（D1）。
create_session = CreateSession()


def get_create_session() -> CreateSession:
    """FastAPI dependency: return the only card-creation slot in the process.

    FastAPI 依賴：回傳 process 內唯一的建卡槽位。

    Returns:
        the process-wide slot
        process 內唯一的建卡槽位
    """
    return create_session
