"""
Telegram Deep Link 解析器模組。

負責將 `/start <payload>` 的 payload 字串解析為對應的 DeepLinkAction Pydantic Model。
這實踐了 Clean Architecture，確保 Controller 層不需要自己解析字串格式。
"""

import logging

from app.schemas.deep_link import (
    DeepLinkAction,
    DeleteEntryAction,
    GenerateCardAction,
    RecordAudioAction,
)

logger = logging.getLogger(__name__)


class DeepLinkParser:
    """Deep Link 解析器。"""

    @staticmethod
    def parse(payload: str) -> DeepLinkAction | None:
        """解析 Deep Link Payload。

        支援的格式：
        - rec_{Card_ID} : 啟動錄音
        - del_{Section}_{Index}_{Card_ID} : 刪除紀錄
        - gen_{Card_ID} 或 gen_new : 生成或編輯卡片
        - recording_{Card_ID} : 相容舊版 Anki 按鈕

        Args:
            payload: `/start` 之後的字串。

        Returns:
            解析出的 DeepLinkAction 子類別，如果格式無效則回傳 None。
        """
        if not payload:
            return None

        parts = payload.split("_")
        prefix = parts[0]

        try:
            if prefix == "rec" or prefix == "recording":
                # rec_{Card_ID}
                # 可能的格式: rec_SC_20231012_153022
                if len(parts) >= 2:
                    card_id = payload[len(prefix) + 1 :]
                    return RecordAudioAction(card_id=card_id)

            elif prefix == "del":
                # del_{section}_{index}_{card_id}
                if len(parts) >= 4:
                    section = parts[1]
                    if section not in ("ref", "rec"):
                        return None
                    index = int(parts[2])
                    card_id = payload[len(prefix) + len(section) + len(parts[2]) + 3 :]
                    return DeleteEntryAction(
                        section=section, index=index, card_id=card_id
                    )

            elif prefix == "gen":
                # gen_{target_id}
                if len(parts) >= 2:
                    target_id = payload[len(prefix) + 1 :]
                    return GenerateCardAction(target_id=target_id)

        except (ValueError, TypeError) as e:
            logger.warning("解析 Deep Link 失敗: payload=%s, error=%s", payload, e)
            return None

        # 未知的 prefix
        logger.warning("未知的 Deep Link Prefix: %s", payload)
        return None
