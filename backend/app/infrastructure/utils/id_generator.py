"""
卡片 ID 產生器工具模組。

Utility module for generating globally unique card IDs.
"""

import time
import uuid

def generate_unique_card_id(prefix: str = "ec") -> str:
    """產生全域唯一的卡片 ID (Card_ID)。

    Generate a globally unique card ID.

    結合前綴、Unix Timestamp(毫秒) 與 UUID4 確保絕對唯一性，
    供所有卡牌生成邏輯共用，避免 Deep Link 或後續參照發生衝突。

    Combines a prefix, a millisecond Unix timestamp, and a UUID4 fragment to
    guarantee uniqueness across all card-generation flows, preventing
    collisions in deep links and later references.

    Args:
        prefix: 卡片前綴，預設為 'ec' (Expression Correction)，
            例如口說教練可傳入 'sc'。Card ID prefix, defaults to 'ec'
            (Expression Correction); e.g. the speaking coach may pass 'sc'.

    Returns:
        str: 格式為 {prefix}-{timestamp}-{uuid_hex_8} 的唯一 ID。
            A unique ID formatted as {prefix}-{timestamp}-{uuid_hex_8}.
    """
    timestamp = int(time.time() * 1000)
    uuid_part = uuid.uuid4().hex[:8]
    return f"{prefix}-{timestamp}-{uuid_part}"
