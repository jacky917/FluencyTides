"""
Telegram Bot 共用的回覆文字。

Shared reply texts for the Telegram bot.

同一件事全 bot 用同一個說法（用語規則見 docs/archive/tg_bot_copy_review_2026-10-18.md §2）：
失敗附下一步、動態內容一律轉義、不給使用者看內部名稱（Card ID、欄位英文名、類別名）。

One wording per situation across the bot: failures say what to do next,
dynamic content is always escaped, and internal names are never shown.
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
    """錄音、上傳等等待狀態的有效分鐘數。Minutes a waiting state stays valid."""
    return settings.TG_STATE_EXPIRE_MINUTES


def error_detail(error: object, limit: int = ERROR_DETAIL_LIMIT) -> str:
    """例外訊息轉成可放進 HTML 回覆的文字（先截斷再轉義）。

    Turn an exception into text safe to embed in an HTML reply.

    AnkiConnect、httpx 等錯誤常含 ``<``、``&``（例如 ``<Response [500]>``、帶查詢字串的網址），
    不轉義的話那則失敗訊息本身就送不出去，使用者只看到停住的「處理中」。
    """
    # 有些例外的字串是空的（例如 httpx.ReadTimeout()、TimeoutError()）：退回類別名，免得「失敗：」後面什麼都沒有。
    # 錯誤原文可能含 AnkiConnect／LLM 的內部網址——這是單人使用的 bot，保留原文對排錯比隱藏更有用
    return escape_tg(str(error) or type(error).__name__, limit)


def upload_done_text(field_name: str | None, speaker_name: str, sync_warning: str) -> str:
    """上傳語音寫回 Anki 後的確認訊息。The confirmation after an uploaded clip is written back.

    說話者名稱是使用者自由輸入的文字（或檔名），一定要轉義：送不出去的話 Anki 已寫入、狀態也已
    清除，畫面卻停在「寫入中」，使用者會以為失敗而重做。
    """
    return f"✅ 已加入{field_label(field_name)}（說話者：{escape_tg(speaker_name, 50)}）{sync_warning}"


def field_label(field_name: str | None) -> str:
    """Anki JSON 欄位的中文名稱；三語卡帶語言（例如「參考範本（日文）」）。

    Display name of an Anki JSON field, with the language for trilingual
    fields. Unknown fields are returned escaped as-is.
    """
    from app.services.task_handlers.shared.trilingual_lang import LANG_DISPLAY, lang_from_field

    name = field_name or ""
    lang = lang_from_field(name)
    if lang:
        base = name.rsplit("_", 1)[0]
        return f"{_FIELD_LABELS.get(base, escape_tg(base))}（{LANG_DISPLAY[lang]}）"
    return _FIELD_LABELS.get(name, escape_tg(name))
