"""Deck service: create decks in Anki (deck_create plan §4, §5).

牌組服務：在 Anki 新增牌組（deck_create 計畫書 §4、§5）。

- ``normalize_deck_name``  validate and tidy a deck name (trim whitespace around every level)
- ``DeckService.create``   reject an existing deck → create → sync AnkiWeb after the write

- ``normalize_deck_name``  檢查並整理牌組名稱（去掉每一層前後的空白）
- ``DeckService.create``   已存在就拒絕 → 建立 → 寫入後同步 AnkiWeb

Creation only; renaming, deleting and moving cards are not handled here (plan §8).

只做新增；改名、刪除、搬移卡片不在這裡（計畫書 §8）。
"""

import logging
import unicodedata

from app.core.exceptions import DeckExistsError, DeckNameInvalidError
from app.infrastructure.anki.client import AnkiClient, AnkiConnectError
from app.services.note_service import sync_warning

logger = logging.getLogger(__name__)

#: Anki's deck level separator.
#: Anki 牌組名的分層符號。
DECK_SEPARATOR = "::"
#: The maximum length of a full deck name (in characters, separators included).
#: 牌組全名的長度上限（字元數，含分層符號）。
MAX_DECK_NAME_LENGTH = 200


def normalize_deck_name(name: str) -> str:
    """Validate a deck name and return the normalized full name (plan §4).

    檢查牌組名稱，回傳整理後的全名（計畫書 §4）。

    Split on ``::`` and trim whitespace around every level; reject the name if any level is empty or the full name
    exceeds the length limit. Other characters are not blocked and are left to Anki.

    以 ``::`` 分層，每一層去掉前後空白；任何一層是空的、或全名超過長度上限就拒絕。其他字元不擋，
    交給 Anki 判斷。

    Args:
        name: the name as typed by the user, for example ``日本語 :: 面接``
            使用者輸入的名稱，例如 ``日本語 :: 面接``

    Returns:
        the normalized full name, for example ``日本語::面接``
        整理後的全名，例如 ``日本語::面接``

    Raises:
        DeckNameInvalidError: if the name is empty, a level is empty, or the name is too long
            若名稱是空的、某一層是空的，或太長
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
    """Return the key used to tell whether two deck names are the same deck.

    回傳比對牌組是否同名用的鍵。

    It approximates Anki's rule (deck names are case-insensitive and ignore Unicode composition differences). It is
    only an approximation: Anki has the final say, which is why ``DeckService.create`` also checks after creating
    whether the returned id belongs to an existing deck.

    近似 Anki 的規則（牌組名不分大小寫，也不分 Unicode 的組合寫法）。只是近似：真正的判斷在 Anki，所以
    ``DeckService.create`` 建立後還會再看回傳的 id 是不是既有的。

    Args:
        name: a full deck name
            牌組全名

    Returns:
        the comparison key
        比對用的鍵
    """
    return unicodedata.normalize("NFC", name).casefold()


class DeckService:
    """Deck write operations (creation only for now).

    牌組的寫入操作（目前只有新增）。

    Attributes:
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端
    """

    def __init__(self, anki_client: AnkiClient) -> None:
        """Initialize the service.

        初始化服務。

        Args:
            anki_client: the AnkiConnect client
                AnkiConnect 客戶端
        """
        self.anki_client = anki_client

    async def create(self, name: str) -> dict[str, object]:
        """Create a deck: validate the name → reject an existing deck → create → sync AnkiWeb and wait for it.

        新增牌組：檢查名稱 → 已存在就拒絕 → 建立 → 同步 AnkiWeb 並等它完成。

        When creating ``A::B::C``, Anki automatically adds ``A`` and ``A::B``. If the sync fails, the deck is still
        created; this is reported through ``sync_warning`` rather than raised.

        建 ``A::B::C`` 時 Anki 會自動補上 ``A``、``A::B``。同步失敗時牌組仍已建立，以
        ``sync_warning`` 提醒，不拋錯。

        Args:
            name: the deck name as typed by the user
                使用者輸入的牌組名稱

        Returns:
            ``{"deck_name", "deck_id", "sync_warning"}``, where ``deck_name`` is the actual name in Anki and
            ``sync_warning`` is ``None`` when the sync succeeded
            ``{"deck_name", "deck_id", "sync_warning"}``，``deck_name`` 是 Anki 裡實際的名稱，
            ``sync_warning`` 在同步成功時為 ``None``

        Raises:
            AnkiConnectError: 502, if Anki is unreachable or rejects the name
                502——若連不上 Anki，或 Anki 拒絕這個名稱
            DeckExistsError: 409, if a deck with the same name exists (compared ignoring case and Unicode
                composition, or judged an existing deck by Anki itself). The message shows the existing name in Anki.
                409——若同名的牌組已存在（比對不分大小寫與 Unicode 的組合寫法；Anki 自己
                判定為既有牌組時也是）。訊息裡是 Anki 裡既有的寫法。
            DeckNameInvalidError: 400, if the name is invalid
                400——若名稱不合格
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
        """Return the new deck's actual name in Anki, falling back to the name sent.

        回傳新牌組在 Anki 裡實際的名稱；查不到時用送出的名稱。

        Anki may adjust the name (for example its Unicode composition), and the frontend needs the actual name to
        select the new deck. This step runs after the deck is created, so neither a failed lookup nor a missing
        deck may count as a failed creation; it is only logged and the name sent is used instead.

        Anki 可能調整名稱（例如 Unicode 的組合寫法），前端要用實際的名稱才選得到新牌組。這一步在牌組
        建好之後：查詢失敗或查不到都不能當成建立失敗，只記下來並改用送出的名稱。

        Args:
            deck_id: the deck ID returned by ``create_deck``
                ``create_deck`` 回傳的牌組 ID
            normalized: the name sent to Anki
                送給 Anki 的名稱

        Returns:
            the name in Anki, or ``normalized`` if it cannot be found
            Anki 裡的名稱；查不到時為 ``normalized``
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
