"""Telegram Bot user state machine module.

Telegram Bot 使用者狀態機模組。

Provides simple in-memory state management for tracking multi-step flows.
Currently used mainly for Workflow B (recording evaluation) transitions:
1. User clicks a deep link -> enters the Recording state
2. User sends a voice message -> the card_id in the state is read and handled
3. Processing finished -> the state is cleared

提供簡易的 In-Memory 狀態管理，用於追蹤多步驟操作流程。
目前主要用於 Workflow B（錄音評分）的狀態切換：
1. 使用者點擊 Deep Link -> 進入 Recording 狀態
2. 使用者發送語音 -> 讀取狀態中的 card_id 並處理
3. 處理完畢 -> 清除狀態

Design decisions:
- An in-memory dict is used instead of Redis/DB because FluencyTides is a
  single-user local app with no cross-process state sharing. Switch to Redis
  if deployed to a multi-worker environment in the future.
- Every state carries an action field to distinguish operation types,
  leaving room for extension.

設計決策：
- 使用記憶體字典而非 Redis/DB，因為 FluencyTides 為單人本地使用，
  不需要跨進程共享狀態。若未來部署到多 Worker 環境，需改用 Redis。
- 所有狀態都帶有 action 欄位以區分不同操作類型，預留擴充空間。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class UserState:
    """The user's current operation state.

    使用者當前操作狀態。

    Attributes:
        action: the operation type identifier (for example ``'recording'``)
            操作類型標識（例如 ``'recording'``）
        card_id: the associated Anki Card_ID field value
            關聯的 Anki Card_ID 欄位值
        extra: additional context information (reserved for extension)
            附加上下文資訊（預留擴充）
        expires_at: the state's expiry time in UTC; ``None`` until
            ``UserStateManager.set_state`` fills in the default
            狀態過期時間（UTC）；在 ``UserStateManager.set_state`` 填入預設值之前為 ``None``
    """

    action: str
    card_id: str
    extra: dict[str, str] = field(default_factory=dict)
    expires_at: datetime | None = None


class UserStateManager:
    """A user state manager (in-memory singleton).

    使用者狀態管理器（In-Memory Singleton）。

    Tracks each user's current operation state keyed by chat_id.

    透過 chat_id 追蹤每位使用者的當前操作狀態。

    Design decisions:
    - Encapsulated in a class instead of a global dict so mocks can be
      injected during testing.
    - Uses the UserState dataclass rather than a bare dict to avoid silent
      errors from key typos.

    設計決策：
    - 不使用全域字典，改用類別封裝，便於測試時注入 Mock。
    - 使用 dataclass UserState 而非裸字典，避免 key typo 導致的靜默錯誤。
    """

    def __init__(self) -> None:
        """Create a manager with no states.

        建立沒有任何狀態的管理器。
        """
        self._states: dict[int, UserState] = {}

    def set_state(self, chat_id: int, state: UserState) -> None:
        """Set the user's state, replacing any previous one.

        設定使用者狀態，取代先前的狀態。

        If ``state.expires_at`` is ``None``, it is set (on the given object)
        to now plus ``TG_STATE_EXPIRE_MINUTES``.

        若 ``state.expires_at`` 為 ``None``，會在傳入的物件上設為現在加上
        ``TG_STATE_EXPIRE_MINUTES``。

        Args:
            chat_id: the Telegram chat ID
                Telegram Chat ID
            state: the UserState instance to set
                要設定的 UserState 實例
        """
        # 如果沒有特別設定過期時間，就自動以設定檔為主
        if state.expires_at is None:
            state.expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=settings.TG_STATE_EXPIRE_MINUTES)

        self._states[chat_id] = state
        logger.info(
            "狀態設定: chat_id=%d, action=%s, card_id=%s, expires_at=%s",
            chat_id,
            state.action,
            state.card_id,
            state.expires_at,
        )

    def get_state(self, chat_id: int) -> UserState | None:
        """Get the user's current state; an expired state is cleared.

        取得使用者的當前狀態；已過期的狀態會被清除。

        Args:
            chat_id: the Telegram chat ID
                Telegram Chat ID

        Returns:
            the UserState instance, or ``None`` if absent or expired
            UserState 實例，若無狀態或已過期則回傳 ``None``
        """
        state = self._states.get(chat_id)
        if state and state.expires_at and datetime.now(tz=timezone.utc) > state.expires_at:
            logger.info("狀態已過期: chat_id=%d, action=%s", chat_id, state.action)
            self.clear_state(chat_id)
            return None
        return state

    def clear_state(self, chat_id: int) -> None:
        """Clear the user's state; does nothing if there is none.

        清除使用者狀態；沒有狀態時不做任何事。

        Args:
            chat_id: the Telegram chat ID
                Telegram Chat ID
        """
        removed = self._states.pop(chat_id, None)
        if removed:
            logger.info(
                "狀態清除: chat_id=%d, 之前的 action=%s",
                chat_id,
                removed.action,
            )

    def has_state(self, chat_id: int) -> bool:
        """Check whether the user has an active state; an expired state is cleared.

        檢查使用者是否有活躍狀態；已過期的狀態會被清除。

        Args:
            chat_id: the Telegram chat ID
                Telegram Chat ID

        Returns:
            ``True`` if an unexpired state exists
            存在未過期的狀態時為 ``True``
        """
        # 利用 get_state 會自動清理過期狀態的特性
        return self.get_state(chat_id) is not None


# Singleton 實例，供 Dispatcher Middleware 注入到 Handler data 中
user_state_manager = UserStateManager()
