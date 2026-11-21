"""Utility module for generating globally unique card IDs.

卡片 ID 產生器工具模組。
"""

import time
import uuid

def generate_unique_card_id(prefix: str = "ec") -> str:
    """Generate a globally unique card ID (Card_ID).

    產生全域唯一的卡片 ID (Card_ID)。

    Combines a prefix, a millisecond Unix timestamp, and the first 8 hex digits of a UUID4 to guarantee
    uniqueness. Shared by all card-generation flows, so deep links and later references never collide.

    結合前綴、Unix Timestamp(毫秒) 與 UUID4 確保絕對唯一性，
    供所有卡牌生成邏輯共用，避免 Deep Link 或後續參照發生衝突。

    Args:
        prefix: the card ID prefix; defaults to ``"ec"`` (Expression Correction). For example, the speaking
            coach may pass ``"sc"``.
            卡片前綴，預設為 ``"ec"`` (Expression Correction)，例如口說教練可傳入 ``"sc"``

    Returns:
        a unique ID in the form ``{prefix}-{timestamp}-{uuid_hex_8}``
        格式為 ``{prefix}-{timestamp}-{uuid_hex_8}`` 的唯一 ID
    """
    timestamp = int(time.time() * 1000)
    uuid_part = uuid.uuid4().hex[:8]
    return f"{prefix}-{timestamp}-{uuid_part}"
