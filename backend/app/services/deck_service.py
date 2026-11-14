"""
牌組服務：在 Anki 新增牌組（deck_create 計畫書 §4、§5）。

Deck service: creates a deck in Anki.

- ``normalize_deck_name``  檢查並整理牌組名稱（去掉每一層前後的空白）
- ``DeckService.create``   已存在就拒絕 → 建立 → 寫入後同步 AnkiWeb

只做新增；改名、刪除、搬移卡片不在這裡（計畫書 §8）。

Creation only; renaming, deleting and moving cards are out of scope.
"""

import logging
import unicodedata

from app.core.exceptions import DeckExistsError, DeckNameInvalidError
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.services.note_service import sync_warning

logger = logging.getLogger(__name__)

#: Anki 牌組名的分層符號。Anki's deck level separator.
DECK_SEPARATOR = "::"
#: 牌組全名的長度上限（字元數，含分層符號）。Maximum length of a full deck name.
MAX_DECK_NAME_LENGTH = 200


def normalize_deck_name(name: str) -> str:
    """檢查牌組名稱，回傳整理後的全名（計畫書 §4）。

    Validate a deck name and return its normalized full name.

    以 ``::`` 分層，每一層去掉前後空白；任何一層是空的、或全名超過長度上限就拒絕。其他字元不擋，
    交給 Anki 判斷。

    Split on ``::`` and trim every level; reject an empty level or a name
    over the length limit. Other characters are left to Anki.

    Args:
        name: 使用者輸入的名稱，例如 ``日本語 :: 面接``。The name as typed.

    Returns:
        整理後的全名，例如 ``日本語::面接``。The normalized full name.

    Raises:
        DeckNameInvalidError: 名稱是空的、某一層是空的，或太長。
            The name is empty, has an empty level, or is too long.
    """
    if not name.strip():
        raise DeckNameInvalidError("牌組名稱不能是空的")
    levels = [level.strip() for level in name.split(DECK_SEPARATOR)]
    for index, level in enumerate(levels, start=1):
        if not level:
            raise DeckNameInvalidError(f"牌組名稱的第 {index} 層是空的")
    normalized = DECK_SEPARATOR.join(levels)
    if len(normalized) > MAX_DECK_NAME_LENGTH:
        raise DeckNameInvalidError(
            f"牌組名稱最長 {MAX_DECK_NAME_LENGTH} 字（目前 {len(normalized)} 字）"
        )
    return normalized


def _same_deck_key(name: str) -> str:
    """比對牌組是否同名用的鍵：近似 Anki 的規則（牌組名不分大小寫，也不分 Unicode 的組合寫法）。

    The key used to tell whether two deck names are the same deck; an
    approximation of Anki's rule (case-insensitive, Unicode-normalized).

    只是近似：真正的判斷在 Anki，所以 ``DeckService.create`` 建立後還會再看回傳的 id 是不是既有的。

    Only an approximation: Anki has the final say, which is why
    ``DeckService.create`` also checks the returned id after creating.

    Args:
        name: 牌組全名。A full deck name.

    Returns:
        比對用的鍵。The comparison key.
    """
    return unicodedata.normalize("NFC", name).casefold()


class DeckService:
    """牌組的寫入操作（目前只有新增）。

    Deck write operations (creation only for now).

    Attributes:
        anki_client: AnkiConnect 客戶端。The AnkiConnect client.
    """

    def __init__(self, anki_client: AnkiClient) -> None:
        """初始化服務。Initialize the service.

        Args:
            anki_client: AnkiConnect 客戶端。The AnkiConnect client.
        """
        self.anki_client = anki_client

    async def create(self, name: str) -> dict[str, object]:
        """新增牌組：檢查名稱 → 已存在就拒絕 → 建立 → 同步 AnkiWeb 並等它完成。

        Create a deck: validate the name, reject an existing deck, create it,
        then sync with AnkiWeb and wait for it.

        建 ``A::B::C`` 時 Anki 會自動補上 ``A``、``A::B``。同步失敗時牌組仍已建立，以
        ``sync_warning`` 提醒，不拋錯。

        Anki adds the missing parents of ``A::B::C``. A failed sync leaves
        the deck created and is reported through ``sync_warning``.

        Args:
            name: 使用者輸入的牌組名稱。The deck name as typed.

        Returns:
            ``{"deck_name", "deck_id", "sync_warning"}``：``deck_name`` 是 Anki 裡實際的名稱，
            ``sync_warning`` 在同步成功時為 None。
            ``deck_name`` is the name as stored in Anki; ``sync_warning`` is
            None when the sync succeeded.

        Raises:
            DeckNameInvalidError: 400——名稱不合格。The name is invalid.
            DeckExistsError: 409——同名的牌組已存在（比對不分大小寫與 Unicode 的組合寫法；Anki 自己
                判定為既有牌組時也是）。訊息裡是 Anki 裡既有的寫法。
                A deck with this name exists (compared case-insensitively, or
                as judged by Anki itself); the message shows the existing name.
            AnkiConnectError: 502——連不上 Anki，或 Anki 拒絕這個名稱。
                Anki is unreachable or rejected the name.
        """
        normalized = normalize_deck_name(name)
        before = await self.anki_client.get_deck_names_and_ids()
        wanted = _same_deck_key(normalized)
        for existing in before:
            if _same_deck_key(existing) == wanted:
                raise DeckExistsError(f"牌組「{existing}」已存在")

        deck_id = await self.anki_client.create_deck(normalized)
        # AnkiConnect 的 createDeck 遇到已存在的牌組不會報錯，而是回傳舊牌組的 id。上面的比對只是近似
        # Anki 的同名規則，檢查和建立之間也可能有別處（手機同步、TG）建了同名牌組：拿到原本就有的 id
        # 表示什麼都沒建，照「已存在」回報，不當成成功。
        for existing, existing_id in before.items():
            if existing_id == deck_id:
                logger.warning("createDeck 回傳既有牌組 %s（id=%s），送出的名稱是 %r", existing, deck_id, normalized)
                raise DeckExistsError(f"牌組「{existing}」已存在")

        deck_name = await self._stored_name(deck_id, normalized)
        logger.info("已新增牌組 %s（id=%s）", deck_name, deck_id)

        warning = await sync_warning(self.anki_client)
        return {"deck_name": deck_name, "deck_id": deck_id, "sync_warning": warning}

    async def _stored_name(self, deck_id: int, normalized: str) -> str:
        """新牌組在 Anki 裡實際的名稱；查不到時用送出的名稱。

        The new deck's name as stored in Anki, falling back to the name sent.

        Anki 可能調整名稱（例如 Unicode 的組合寫法），前端要用實際的名稱才選得到新牌組。這一步在牌組
        建好之後：查詢失敗或查不到都不能當成建立失敗，只記下來並改用送出的名稱。

        Anki may adjust the name, and the frontend selects the deck by its
        stored name. The deck already exists at this point, so a failed or
        empty lookup is logged and never reported as a failed creation.

        Args:
            deck_id: ``create_deck`` 回傳的牌組 ID。The ID returned by ``create_deck``.
            normalized: 送給 Anki 的名稱。The name sent to Anki.

        Returns:
            Anki 裡的名稱；查不到時為 ``normalized``。The stored name, or ``normalized``.
        """
        try:
            decks = await self.anki_client.get_deck_names_and_ids()
        except AnkiConnectError as exc:
            logger.warning("已新增牌組（id=%s），但重新讀取牌組名稱失敗，改用送出的名稱 %r：%s", deck_id, normalized, exc)
            return normalized
        for stored_name, stored_id in decks.items():
            if stored_id == deck_id:
                return stored_name
        logger.warning("新牌組（id=%s）不在 Anki 的牌組清單裡，改用送出的名稱 %r", deck_id, normalized)
        return normalized
