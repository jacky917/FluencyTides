"""Shared reply texts for the Telegram bot.

Telegram Bot 共用的回覆文字。

The whole bot uses one wording per situation (wording rules in
docs/archive/tg_bot_copy_review_2026-10-18.md §2): failures say what to do next, dynamic content is always
escaped, and internal names (Card ID, English field names, class names) are never shown to the user.

同一件事全 bot 用同一個說法（用語規則見 docs/archive/tg_bot_copy_review_2026-10-18.md §2）：
失敗附下一步、動態內容一律轉義、不給使用者看內部名稱（Card ID、欄位英文名、類別名）。
"""

from app.bot.utils.formatting import escape_tg
from app.core.config import settings

CARD_NOT_FOUND = "❌ Anki 裡找不到這張卡片（可能已刪除或尚未同步）。"
ANKI_UNREACHABLE = "❌ 連不上 Anki，請確認電腦版 Anki 已開啟。"
NEWCARD_RESTART = "請用 /newcard 重新開始。"
FLOW_ENDED_ALERT = "⌛ 流程已結束，請用 /newcard 重新開始。"
UPLOAD_EXPIRED_ALERT = "⌛ 已過期，請回 Anki 重新點「＋」上傳。"
UPLOAD_RETRY = "請回 Anki 重新點「＋」上傳。"

# 例外訊息放進回覆時的長度上限（先截斷再轉義，見 escape_tg）
ERROR_DETAIL_LIMIT = 200

# Anki JSON 欄位的中文名稱
_FIELD_LABELS = {"References": "參考範本", "Recordings": "歷史錄音", "Prompt_Audios": "正面語音"}


def state_minutes() -> int:
    """Return how many minutes a waiting state such as recording or uploading stays valid.

    回傳錄音、上傳等等待狀態的有效分鐘數。

    Returns:
        the ``TG_STATE_EXPIRE_MINUTES`` setting
        ``TG_STATE_EXPIRE_MINUTES`` 設定值
    """
    return settings.TG_STATE_EXPIRE_MINUTES


def error_detail(error: object, limit: int = ERROR_DETAIL_LIMIT) -> str:
    """Turn an exception into text safe to embed in an HTML reply (truncated, then escaped).

    把例外訊息轉成可放進 HTML 回覆的文字（先截斷再轉義）。

    Errors from AnkiConnect, httpx and the like often contain ``<`` or ``&`` (for example
    ``<Response [500]>`` or a URL with a query string). Without escaping, the failure message itself
    cannot be sent and the user only sees a stalled "processing" message.

    AnkiConnect、httpx 等錯誤常含 ``<``、``&``（例如 ``<Response [500]>``、帶查詢字串的網址），
    不轉義的話那則失敗訊息本身就送不出去，使用者只看到停住的「處理中」。

    Args:
        error: the exception (or any object) to describe
            要描述的例外（或任意物件）
        limit: the maximum length before truncation
            截斷前的長度上限

    Returns:
        the escaped text; the class name when the exception's string is empty
        轉義後的文字；例外字串為空時為類別名稱
    """
    # 有些例外的字串是空的（例如 httpx.ReadTimeout()、TimeoutError()）：退回類別名，免得「失敗：」後面什麼都沒有。
    # 錯誤原文可能含 AnkiConnect／LLM 的內部網址——這是單人使用的 bot，保留原文對排錯比隱藏更有用
    return escape_tg(str(error) or type(error).__name__, limit)


def sync_suffix(warning: str | None) -> str:
    """Format the service layer's sync warning (plain text) to append after a Telegram reply.

    把服務層的同步提醒（純文字）接在 TG 回覆後面。

    Args:
        warning: the sync warning; ``None`` or empty when the sync succeeded
            同步提醒；同步成功時為 ``None`` 或空字串

    Returns:
        the warning preceded by a blank line, or an empty string when the sync succeeded
        前面空一行的提醒；同步成功時為空字串
    """
    return f"\n\n{warning}" if warning else ""


def upload_done_text(field_name: str | None, speaker_name: str, sync_warning: str) -> str:
    """Return the confirmation message after an uploaded clip is written back to Anki.

    回傳上傳語音寫回 Anki 後的確認訊息。

    The speaker name is free text typed by the user (or a file name) and must be escaped: if the message
    cannot be sent, Anki has already been written and the state already cleared, yet the screen stays at
    "writing", so the user thinks it failed and does it again.

    說話者名稱是使用者自由輸入的文字（或檔名），一定要轉義：送不出去的話 Anki 已寫入、狀態也已
    清除，畫面卻停在「寫入中」，使用者會以為失敗而重做。

    Args:
        field_name: the Anki JSON field the clip was added to; see ``field_label``
            語音加入的 Anki JSON 欄位；見 ``field_label``
        speaker_name: the speaker name, escaped and truncated to 50 characters
            說話者名稱，會轉義並截斷為 50 字
        sync_warning: the suffix from ``sync_suffix``, appended as-is
            ``sync_suffix`` 產生的後綴，原樣接在後面

    Returns:
        the confirmation message in Telegram HTML
        Telegram HTML 格式的確認訊息
    """
    return f"✅ 已加入{field_label(field_name)}（說話者：{escape_tg(speaker_name, 50)}）{sync_warning}"


def field_label(field_name: str | None) -> str:
    """Return the Chinese display name of an Anki JSON field.

    回傳 Anki JSON 欄位的中文名稱。

    Trilingual-card fields include the language (for example 「參考範本（日文）」). Unknown field names
    are returned escaped as-is.

    三語卡欄位會帶語言（例如「參考範本（日文）」）。未知的欄位名稱轉義後原樣回傳。

    Args:
        field_name: the Anki field name; ``None`` is treated as an empty string
            Anki 欄位名稱；``None`` 視為空字串

    Returns:
        the display name, safe to embed in Telegram HTML
        可安全嵌入 Telegram HTML 的顯示名稱
    """
    from app.services.task_handlers.shared.trilingual_lang import LANG_DISPLAY, lang_from_field

    name = field_name or ""
    lang = lang_from_field(name)
    if lang:
        base = name.rsplit("_", 1)[0]
        return f"{_FIELD_LABELS.get(base, escape_tg(base))}（{LANG_DISPLAY[lang]}）"
    return _FIELD_LABELS.get(name, escape_tg(name))
