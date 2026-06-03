"""
Telegram Bot 使用者狀態機模組。

提供簡易的 In-Memory 狀態管理，用於追蹤多步驟操作流程。
目前主要用於 Workflow B（錄音評分）的狀態切換：
1. 使用者點擊 Deep Link -> 進入 Recording 狀態
2. 使用者發送語音 -> 讀取狀態中的 card_id 並處理
3. 處理完畢 -> 清除狀態

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
    """使用者當前操作狀態。

    Attributes:
        action: 操作類型標識（例如 'recording'）。
        card_id: 關聯的 Anki Card_ID 欄位值。
        extra: 附加上下文資訊（預留擴充）。
    """

    action: str
    card_id: str
    extra: dict[str, str] = field(default_factory=dict)
    expires_at: datetime | None = None


class UserStateManager:
    """使用者狀態管理器（In-Memory Singleton）。

    透過 chat_id 追蹤每位使用者的當前操作狀態。

    設計決策：
    - 不使用全域字典，改用類別封裝，便於測試時注入 Mock。
    - 使用 dataclass UserState 而非裸字典，避免 key typo 導致的靜默錯誤。
    """

    def __init__(self) -> None:
        """初始化空的狀態字典。"""
        self._states: dict[int, UserState] = {}

    def set_state(self, chat_id: int, state: UserState) -> None:
        """設定使用者狀態。

        Args:
            chat_id: Telegram Chat ID。
            state: 要設定的 UserState 實例。
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
        """取得使用者的當前狀態。

        Args:
            chat_id: Telegram Chat ID。

        Returns:
            UserState 實例，若無狀態或已過期則回傳 None。
        """
        state = self._states.get(chat_id)
        if state and state.expires_at and datetime.now(tz=timezone.utc) > state.expires_at:
            logger.info("狀態已過期: chat_id=%d, action=%s", chat_id, state.action)
            self.clear_state(chat_id)
            return None
        return state

    def clear_state(self, chat_id: int) -> None:
        """清除使用者狀態。

        Args:
            chat_id: Telegram Chat ID。
        """
        removed = self._states.pop(chat_id, None)
        if removed:
            logger.info(
                "狀態清除: chat_id=%d, 之前的 action=%s",
                chat_id,
                removed.action,
            )

    def has_state(self, chat_id: int) -> bool:
        """檢查使用者是否有活躍狀態。

        Args:
            chat_id: Telegram Chat ID。

        Returns:
            是否存在有效狀態。
        """
        # 利用 get_state 會自動清理過期狀態的特性
        return self.get_state(chat_id) is not None


# Singleton 實例，供 Dispatcher Middleware 注入到 Handler data 中
user_state_manager = UserStateManager()
