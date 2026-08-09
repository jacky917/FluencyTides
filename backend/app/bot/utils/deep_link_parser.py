"""
Telegram Deep Link 解析器模組。

Telegram deep link parser module.

負責將 `/start <payload>` 的 payload 字串解析為對應的 DeepLinkAction Pydantic Model。
這實踐了 Clean Architecture，確保 Controller 層不需要自己解析字串格式。

Parses the `/start <payload>` string into the corresponding DeepLinkAction
Pydantic model. This follows Clean Architecture so that the controller layer
never has to parse string formats by itself.
"""

import logging

from app.schemas.tg.deep_link import (
    DeepLinkAction,
    DeleteEntryAction,
    GenerateCardAction,
    RecordAudioAction,
    AddAudioAction,
    TTSAudioAction,
)

logger = logging.getLogger(__name__)


class DeepLinkParser:
    """Deep Link 解析器。

    Deep link parser.
    """

    @staticmethod
    def parse(payload: str) -> DeepLinkAction | None:
        """解析 Deep Link Payload。

        Parse a deep link payload.

        支援的格式：{Action}-{FieldName}-{Index}-{Card_ID}
        - Action: rec, del, addaudio, ttsaudio, gen
        - FieldName: 對應 Anki 的欄位名稱 (例如 Recordings)
        - Index: 數字或 'last'，如果不適用則為 'none'
        - Card_ID: 卡片的唯一識別碼

        Supported format: {Action}-{FieldName}-{Index}-{Card_ID}
        - Action: rec, del, addaudio, ttsaudio, gen
        - FieldName: the Anki field name (e.g. Recordings)
        - Index: a number or 'last'; 'none' when not applicable
        - Card_ID: the card's unique identifier

        Args:
            payload: `/start` 之後的字串。The string after `/start`.

        Returns:
            解析出的 DeepLinkAction 子類別，如果格式無效則回傳 None。
            The parsed DeepLinkAction subclass, or None if the format is
            invalid.
        """
        if not payload:
            return None

        # 針對 gen_{target_id} 這種舊版非標準指令，先做特例處理
        if payload.startswith("gen_"):
            return GenerateCardAction(target_id=payload[4:])

        parts = payload.split("-", 3)
        if len(parts) != 4:
            logger.warning("解析 Deep Link 失敗: 格式不符合 4 段式要求 (payload=%s)", payload)
            return None

        action, field_name, index, card_id = parts

        try:
            if action in ("rec", "recording"):
                return RecordAudioAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action == "del":
                return DeleteEntryAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action == "addaudio":
                return AddAudioAction(
                    field_name=field_name, index=index, card_id=card_id
                )
            elif action == "ttsaudio":
                return TTSAudioAction(
                    field_name=field_name, index=index, card_id=card_id
                )
        except (ValueError, TypeError) as e:
            logger.warning("解析 Deep Link 失敗: payload=%s, error=%s", payload, e)
            return None

        # 未知的 prefix
        logger.warning("未知的 Deep Link Action: %s", action)
        return None
