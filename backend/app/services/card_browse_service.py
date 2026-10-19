"""卡片瀏覽服務：不經過 task handler，直接以 Anki 搜尋列出任一牌組的卡片。

Card browsing service: lists cards of any deck straight from Anki search,
without going through a task handler (網頁版計畫階段 A).

- 牌組的 ``deck:"…"`` 搜尋本來就包含子牌組，選上層即可看到底下所有卡片。
- 卡面用 Anki 自己渲染的 ``question`` / ``answer``（``cardsInfo``），網頁不必為每種筆記
  類型各寫一個渲染器。Anki 渲染時會把 ``[sound:檔名]`` 換成 ``[anki:play:q:N]`` 這種標記
  （審閱畫面另外把它畫成播放鈕）；這裡依模板找回對應的檔名，換成網頁可播放的按鈕。
- 媒體檔在 Anki 媒體資料夾，以 ``retrieveMediaFile`` 讀出。只接受單一檔名：AnkiConnect
  本身也會取 basename，這裡回 400 是縱深防禦，也讓錯誤明確而不是被靜默改名。回應類型只
  允許音訊、影片與常見圖片，其他一律 ``application/octet-stream``：媒體端點與網頁同源，
  若照副檔名回 ``text/html`` / ``image/svg+xml``，媒體資料夾中的一個檔案就能在網頁的
  origin 執行腳本。
"""

import base64
import binascii
import html
import logging
import re
from pathlib import PurePosixPath

from app.core.exceptions import (
    AnkiServiceError,
    CardNotFoundError,
    InvalidAnkiSearchError,
    InvalidMediaFilenameError,
    MediaFileNotFoundError,
)
from app.infrastructure.anki.client import AnkiActionError, AnkiClient
from app.schemas.anki import AnkiCardInfo
from app.schemas.card_browse import CardPage, CardSummary, CardView

logger = logging.getLogger(__name__)

# Anki 搜尋語法在引號內仍有意義的字元：\ 要先處理，否則會重複跳脫
# Characters still special inside a quoted Anki search term; backslash first.
_SEARCH_SPECIALS = ("\\", '"', "*", "_")

# 媒體回應允許的類型（依副檔名，不用 mimetypes：Windows 會讀登錄檔，結果隨機器而異）。
# 只列音訊、影片與不能執行腳本的圖片；SVG 可以內嵌腳本，故不列入。
# Media types served as-is; anything else becomes application/octet-stream.
_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
    ".mp4": "video/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}
_FALLBACK_MEDIA_TYPE = "application/octet-stream"

_AV_TAG = re.compile(r"\[anki:play:([qa]):(\d+)\]")
_SOUND = re.compile(r"\[sound:(.+?)\]")
_TEMPLATE_REF = re.compile(r"\{\{(.*?)\}\}", re.S)


def deck_search_term(deck: str) -> str:
    """組出只比對這個牌組（含子牌組）的搜尋條件，名稱中的萬用字元照字面比對。

    Build a search term matching exactly this deck and its sub-decks; ``*``
    and ``_`` in the name are matched literally.

    Args:
        deck: 牌組完整名稱，例如 ``日本語::外語糾錯``。Full deck name.

    Returns:
        例如 ``deck:"日本語::外語糾錯"``。The search term.
    """
    escaped = deck
    for char in _SEARCH_SPECIALS:
        escaped = escaped.replace(char, f"\\{char}")
    return f'deck:"{escaped}"'


def build_query(deck: str | None, search: str | None) -> str:
    """牌組條件加上使用者的 Anki 搜尋語句（以括號接在後面）。

    Combine the deck term with the user's own Anki search, parenthesised.

    全部牌組用 ``deck:*``：Anki 的空白搜尋只會找目前牌組。使用者的語句若括號不對稱（例如
    ``x) OR (deck:*``）可以跳出牌組條件——只影響使用者自己的收藏，不另外防範。

    Args:
        deck: 牌組完整名稱；None 或空字串表示全部牌組。Deck, or all decks.
        search: 使用者輸入的 Anki 搜尋語句；可省略。Optional Anki search.

    Returns:
        Anki 搜尋語句。The Anki query.
    """
    query = deck_search_term(deck) if deck else "deck:*"
    search = (search or "").strip()
    return f"{query} ({search})" if search else query


def _summary(info: AnkiCardInfo) -> CardSummary:
    """``cardsInfo`` 的一筆轉成清單項目，欄位依筆記類型的順序。Convert one entry."""
    ordered = sorted(info.fields.items(), key=lambda item: item[1].order)
    return CardSummary(
        card_id=info.cardId,
        note_id=info.note,
        deck_name=info.deckName,
        model_name=info.modelName,
        ord=info.ord,
        fields={name: field.value for name, field in ordered},
    )


def _parse_infos(infos: list[dict[str, object]]) -> list[AnkiCardInfo]:
    """解析 ``cardsInfo``；搜尋與讀取之間被刪除的卡回傳空物件，略過。

    Parse ``cardsInfo``, skipping the empty objects returned for cards
    deleted between search and read. A malformed entry raises (500): the
    AnkiConnect contract changed and should not be papered over.
    """
    parsed = [AnkiCardInfo.model_validate(info) for info in infos if info]
    if len(parsed) < len(infos):
        logger.debug("cardsInfo 中有 %d 張卡已不存在，略過。", len(infos) - len(parsed))
    return parsed


async def browse_cards(
    anki: AnkiClient, deck: str | None, search: str | None, page: int, page_size: int
) -> CardPage:
    """列出一頁卡片，新到舊（卡片 ID 是建立時間戳）。

    List one page of cards, newest first (card IDs are creation timestamps).
    Every call fetches all matching IDs (needed for ``total``) and reads only
    the requested page with ``cardsInfo``.

    Args:
        anki: AnkiConnect 客戶端。AnkiConnect client.
        deck: 牌組完整名稱（含子牌組）；None 為全部。Deck, or all decks.
        search: 額外的 Anki 搜尋語句。Extra Anki search.
        page: 頁碼（1 起算）。Page number, from 1.
        page_size: 每頁張數。Page size.

    Returns:
        這一頁的卡片與總數。``total`` 含搜尋後才被刪除的卡，可能比實際多。The page and
        the total.

    Raises:
        InvalidAnkiSearchError: 使用者的搜尋語句被 Anki 拒絕。The user's search was rejected.
        AnkiServiceError: AnkiConnect 連不上或其他錯誤。On other AnkiConnect errors.
    """
    try:
        found = await anki.find_cards(build_query(deck, search))
    except AnkiActionError as error:
        # 牌組條件由程式組出、不會有語法錯誤；有使用者語句時，錯誤歸給它
        if search and search.strip():
            raise InvalidAnkiSearchError(f"Anki 拒絕這個搜尋（多半是語法錯誤）：{error.message}") from error
        raise
    card_ids = sorted(found, reverse=True)
    start = (page - 1) * page_size
    page_ids = card_ids[start : start + page_size]
    infos = _parse_infos(await anki.get_cards_info(page_ids)) if page_ids else []
    return CardPage(cards=[_summary(info) for info in infos], total=len(card_ids), page=page, page_size=page_size)


def template_sounds(template: str, fields: dict[str, str]) -> list[str]:
    """依模板中欄位出現的順序，列出其中的 ``[sound:檔名]``，即 Anki 標記序號對應的檔案。

    List the ``[sound:…]`` files of the fields a template references, in
    reference order — the order Anki numbers its ``[anki:play]`` tags.

    限制：條件區塊（``{{#X}}``）隱藏的欄位、``{{tts}}`` 產生的語音也會讓 Anki 的序號與
    這裡不同；對不上時呼叫端會顯示「找不到音檔」。``{{FrontSide}}`` 不計（背面標記只算背面
    自己的部分，正面的標記仍是 ``q``）。

    Args:
        template: 正面或背面模板。The front or back template.
        fields: 欄位名 → 原始值。Raw field values.

    Returns:
        依序的檔名。The file names in order.
    """
    sounds: list[str] = []
    for match in _TEMPLATE_REF.finditer(template):
        spec = match.group(1).strip()
        if not spec or spec[0] in "#^/!" or spec.startswith("tts "):
            continue
        name = spec.rsplit(":", 1)[-1].strip()  # 去掉 furigana: 等過濾器
        if name == "FrontSide":
            continue
        sounds.extend(_SOUND.findall(fields.get(name, "")))
    return sounds


def _play_button(filename: str) -> str:
    """一個播放鈕；網頁注入 iframe 的腳本負責點擊後播放。A play button for the bridge."""
    name = html.escape(filename, quote=True)
    return f'<button type="button" class="replay-button soundLink" data-anki-sound="{name}" title="{name}">▶</button>'


_MISSING_SOUND = '<span class="replay-button-missing" title="找不到這個播放標記對應的音檔">🔇</span>'


def replace_av_tags(face_html: str, question_sounds: list[str], answer_sounds: list[str]) -> str:
    """把 ``[anki:play:q|a:N]`` 換成播放鈕；序號對不到檔案時換成「找不到音檔」。

    Replace Anki's play tags with play buttons, or a "missing" marker when
    the index has no matching file.
    """

    def replace(match: re.Match[str]) -> str:
        sounds = question_sounds if match.group(1) == "q" else answer_sounds
        index = int(match.group(2))
        return _play_button(sounds[index]) if index < len(sounds) else _MISSING_SOUND

    return _AV_TAG.sub(replace, face_html)


async def _resolve_av_tags(anki: AnkiClient, info: AnkiCardInfo) -> tuple[str, str]:
    """正反面中有播放標記時，讀模板找回檔名並換成播放鈕；沒有標記就不多查一次。

    Resolve play tags via the note type's templates; skipped when absent.
    """
    if not (_AV_TAG.search(info.question) or _AV_TAG.search(info.answer)):
        return info.question, info.answer
    templates = list((await anki.get_model_templates(info.modelName)).values())
    # 克漏字類型只有一個模板，ord 是克漏字編號
    template = templates[info.ord] if info.ord < len(templates) else templates[0]
    fields = {name: field.value for name, field in info.fields.items()}
    question_sounds = template_sounds(template.get("Front", ""), fields)
    answer_sounds = template_sounds(template.get("Back", ""), fields)
    return (
        replace_av_tags(info.question, question_sounds, answer_sounds),
        replace_av_tags(info.answer, question_sounds, answer_sounds),
    )


async def get_card(anki: AnkiClient, card_id: int) -> CardView:
    """讀一張卡與 Anki 渲染好的正反面（播放標記換成播放鈕）。

    Read one card with its rendered faces, play tags turned into buttons.

    Args:
        anki: AnkiConnect 客戶端。AnkiConnect client.
        card_id: 卡片 ID。Card ID.

    Returns:
        卡片與卡面。The card and its faces.

    Raises:
        CardNotFoundError: Anki 中沒有這張卡。The card does not exist.
        AnkiServiceError: AnkiConnect 錯誤。On AnkiConnect errors.
    """
    infos = _parse_infos(await anki.get_cards_info([card_id]))
    if not infos:
        raise CardNotFoundError(f"Anki 裡找不到卡片 {card_id}（可能已刪除或尚未同步）。")
    info = infos[0]
    question, answer = await _resolve_av_tags(anki, info)
    return CardView(**_summary(info).model_dump(), question=question, answer=answer)


def validate_media_filename(filename: str) -> str:
    """只接受媒體資料夾中的單一檔名。Accept a bare file name only.

    Args:
        filename: 檔名。The file name.

    Returns:
        原檔名。The same name.

    Raises:
        InvalidMediaFilenameError: 空字串、含路徑分隔符號或 NUL、或為 ``.`` / ``..``。
            Empty, containing a path separator or NUL, or ``.`` / ``..``.
    """
    if not filename or filename in {".", ".."} or any(char in filename for char in "/\\\0"):
        raise InvalidMediaFilenameError(f"不是有效的媒體檔名：{filename!r}")
    return filename


def media_type_for(filename: str) -> str:
    """依副檔名決定回應類型；不在允許清單的一律 ``application/octet-stream``。

    The response type for a media file; unknown or scriptable types fall
    back to ``application/octet-stream``.
    """
    return _MEDIA_TYPES.get(PurePosixPath(filename).suffix.lower(), _FALLBACK_MEDIA_TYPE)


async def read_media(anki: AnkiClient, filename: str) -> tuple[bytes, str]:
    """從 Anki 媒體資料夾讀出一個檔案。Read a file from Anki's media folder.

    Args:
        anki: AnkiConnect 客戶端。AnkiConnect client.
        filename: 單一檔名。A bare file name.

    Returns:
        ``(內容, 回應類型)``；類型見 :func:`media_type_for`。內容可以是空的（0 位元組的檔案）。
        The content and its response type.

    Raises:
        InvalidMediaFilenameError: 檔名不合格。Invalid name.
        MediaFileNotFoundError: 檔案不存在（AnkiConnect 回傳 False）。Missing file.
        AnkiServiceError: AnkiConnect 錯誤，或回傳的內容不是 base64。On errors.
    """
    validate_media_filename(filename)
    encoded = await anki.retrieve_media_file(filename)
    if encoded is False:
        raise MediaFileNotFoundError(f"Anki 媒體資料夾中沒有 {filename}。")
    try:
        content = base64.b64decode(str(encoded), validate=True)
    except (binascii.Error, ValueError) as error:
        logger.error("AnkiConnect 回傳的媒體檔不是有效的 base64：%s（長度 %d）：%s", filename, len(str(encoded)), error)
        raise AnkiServiceError(f"讀取媒體檔 {filename} 失敗：AnkiConnect 回傳的內容無法解碼。") from error
    return content, media_type_for(filename)
