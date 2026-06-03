"""
非同步 AnkiConnect API v6 完整 CRUD 客戶端。

本模組提供對 AnkiConnect 所有主要功能的非同步 Python 封裝，包括：
- 牌組（Deck）管理：建立、刪除、查詢、移動卡片
- 筆記（Note）管理：新增、更新、刪除、搜尋筆記
- 卡片（Card）管理：搜尋、暫停/取消暫停、簡易度因子
- 媒體（Media）管理：儲存、讀取、刪除媒體檔案
- 模型（Model）查詢：取得模型名稱、欄位
- 雜項操作：版本查詢、同步、權限請求、批次操作

重構自 old/Anki/utils/anki_connect.py，改用 httpx.AsyncClient 實現
完全非同步 I/O，適配 FastAPI 的高併發場景。所有連線參數統一由
core.config.Settings 管理，不再依賴散落的 .env 載入邏輯。

Dependencies:
    - httpx: 非同步 HTTP 客戶端
    - pydantic: 資料驗證
"""

import logging

import httpx

from app.core.config import settings
from app.schemas.anki import (
    AnkiActionRequest,
    AnkiActionResponse,
    AnkiNote,
    AnkiNoteInfo,
    AnkiStoreMediaParams,
)

logger = logging.getLogger(__name__)


class AnkiConnectError(Exception):
    """AnkiConnect API 錯誤異常類別。

    當 AnkiConnect API 回傳錯誤或連線失敗時拋出此異常。

    Attributes:
        message: 錯誤訊息字串。
    """

    def __init__(self, message: str) -> None:
        """初始化 AnkiConnectError。

        Args:
            message: AnkiConnect API 回傳的錯誤訊息。
        """
        super().__init__(message)
        self.message = message


class AnkiClient:
    """非同步 AnkiConnect API v6 完整 CRUD 封裝客戶端。

    提供對 AnkiConnect 所有主要功能的 Python 非同步封裝。
    使用 httpx.AsyncClient 實現連線池復用，適配 FastAPI 的高併發需求。

    所有連線資訊從 core.config.Settings 統一讀取：
    - ANKI_CONNECT_URL: 完整的 AnkiConnect 端點 URL
    - ANKI_CONNECT_API_KEY: API 金鑰（可選）
    - CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET: Cloudflare Access 憑證（可選）

    Example:
        >>> client = AnkiClient()
        >>> decks = await client.get_deck_names()
        >>> print(decks)
        ['Default', 'Japanese::JLPT N3']
        >>> await client.close()
    """

    # AnkiConnect API 版本，固定使用 v6
    API_VERSION = 6

    # HTTP 請求預設超時秒數
    DEFAULT_TIMEOUT = 30.0

    # 同步操作超時秒數（sync 動作可能較慢）
    SYNC_TIMEOUT = 60.0

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """初始化非同步 AnkiConnect 客戶端。

        優先使用參數傳入的值，若未提供則從 Settings 讀取。

        Args:
            url: AnkiConnect 端點 URL。若為 None，從 Settings 讀取。
            api_key: API 金鑰。若為 None，從 Settings 讀取。
            timeout: HTTP 請求超時秒數，預設 30 秒。
        """
        self._url = url or settings.ANKI_CONNECT_URL
        self._api_key = api_key or settings.ANKI_CONNECT_API_KEY
        self._timeout = timeout

        # 組裝 HTTP headers，若有 Cloudflare Access 憑證則加入
        headers: dict[str, str] = {}
        if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
            headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
            headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET

        # 建立 httpx.AsyncClient 以支援連線池復用
        self._client = httpx.AsyncClient(headers=headers, timeout=self._timeout)

        logger.info("AnkiConnect 非同步客戶端已初始化，目標地址: %s", self._url)

    async def close(self) -> None:
        """關閉底層 httpx 連線池。

        應在應用程式關閉時呼叫，釋放所有 HTTP 連線資源。
        """
        await self._client.aclose()
        logger.info("AnkiConnect 客戶端連線已關閉。")

    # ========================================================================
    # 核心方法（Core）
    # ========================================================================

    async def _invoke(self, action: str, **params: object) -> object:
        """向 AnkiConnect 發送 API 請求的底層方法。

        組裝 JSON-RPC 請求體，發送非同步 HTTP POST 請求至 AnkiConnect
        伺服器，並解析回應結果。此方法是所有公開 API 方法的基礎。

        Args:
            action: AnkiConnect API 動作名稱，例如 'deckNames'、'addNote'。
            **params: 傳遞給 API 動作的關鍵字參數。

        Returns:
            API 回應中的 result 欄位值，型別取決於具體的 API 動作。

        Raises:
            AnkiConnectError: 當 AnkiConnect 回傳 error 欄位不為 null、
                              無法連接伺服器、或請求超時時。
        """
        # 組裝請求 payload
        req = AnkiActionRequest(
            action=action,
            params=params if params else None,
            key=self._api_key,
        )

        # 記錄 API 呼叫日誌（不記錄 key 以避免洩露敏感資訊）
        logger.debug(
            "發送 AnkiConnect 請求 -> action: %s, params: %s",
            action,
            params if params else "無",
        )

        try:
            response = await self._client.post(
                self._url,
                json=req.model_dump(exclude_none=True),
            )
            response.raise_for_status()
        except httpx.ConnectError:
            logger.error("無法連接到 AnkiConnect 伺服器: %s", self._url)
            raise AnkiConnectError(
                f"無法連接到 AnkiConnect 伺服器 ({self._url})。"
                f"請確認 Anki 已啟動且 AnkiConnect 插件已安裝。"
            )
        except httpx.TimeoutException:
            logger.error("AnkiConnect 請求超時: action=%s", action)
            raise AnkiConnectError(
                f"AnkiConnect 請求超時（{self._timeout}秒），action: {action}"
            )
        except httpx.HTTPStatusError as e:
            logger.error("AnkiConnect HTTP 錯誤: %s", e)
            raise AnkiConnectError(f"AnkiConnect HTTP 錯誤: {e}")

        # 解析回應
        resp = AnkiActionResponse(**response.json())

        if resp.error is not None:
            logger.error(
                "AnkiConnect API 錯誤: %s (action: %s)", resp.error, action
            )
            raise AnkiConnectError(resp.error)

        logger.debug("AnkiConnect 請求成功 -> action: %s", action)
        return resp.result

    # ========================================================================
    # 牌組操作（Deck Actions）
    # ========================================================================

    async def get_deck_names(self) -> list[str]:
        """取得所有牌組名稱。

        Returns:
            包含所有牌組名稱的字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> await client.get_deck_names()
            ['Default', 'Japanese::JLPT N3']
        """
        result = await self._invoke("deckNames")
        return list(result)  # type: ignore[arg-type]

    async def get_deck_names_and_ids(self) -> dict[str, int]:
        """取得所有牌組名稱及其對應的 ID。

        Returns:
            字典，鍵為牌組名稱（str），值為牌組 ID（int）。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> await client.get_deck_names_and_ids()
            {'Default': 1, 'Japanese::JLPT N3': 1519323742721}
        """
        result = await self._invoke("deckNamesAndIds")
        return dict(result)  # type: ignore[arg-type]

    async def create_deck(self, deck: str) -> int:
        """建立新的牌組。

        若已存在同名牌組，不會覆蓋。支援使用 '::' 分隔符建立巢狀牌組。

        Args:
            deck: 牌組名稱。使用 '::' 建立子牌組，例如 'Japanese::Tokyo'。

        Returns:
            新建立牌組的 ID。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> await client.create_deck('English::Vocabulary')
            1519323742721
        """
        result = await self._invoke("createDeck", deck=deck)
        return int(result)  # type: ignore[arg-type]

    async def delete_decks(
        self, decks: list[str], cards_too: bool = True
    ) -> None:
        """刪除指定的牌組。

        Args:
            decks: 要刪除的牌組名稱列表。
            cards_too: 是否同時刪除牌組中的卡片。AnkiConnect 要求必須為 True。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("deleteDecks", decks=decks, cardsToo=cards_too)

    async def get_deck_config(self, deck: str) -> dict[str, object]:
        """取得指定牌組的設定群組物件。

        Args:
            deck: 牌組名稱。

        Returns:
            包含牌組設定的字典。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getDeckConfig", deck=deck)
        return dict(result)  # type: ignore[arg-type]

    async def change_deck(self, cards: list[int], deck: str) -> None:
        """將指定卡片移動到另一個牌組。

        若目標牌組不存在，會自動建立。

        Args:
            cards: 要移動的卡片 ID 列表。
            deck: 目標牌組名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("changeDeck", cards=cards, deck=deck)

    async def get_decks(self, cards: list[int]) -> dict[str, list[int]]:
        """根據卡片 ID 取得其所屬的牌組。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            字典，鍵為牌組名稱，值為屬於該牌組的卡片 ID 列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getDecks", cards=cards)
        return dict(result)  # type: ignore[arg-type]

    # ========================================================================
    # 筆記操作（Note Actions）
    # ========================================================================

    async def add_note(self, note: AnkiNote) -> int | None:
        """新增一則筆記到指定牌組。

        使用 Pydantic 模型保證傳入的筆記結構正確無誤，
        消除舊版使用裸字典時可能的欄位錯誤風險。

        Args:
            note: AnkiNote Pydantic 模型實例。

        Returns:
            新建筆記的 ID（int），若建立失敗則回傳 None。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> note = AnkiNote(
            ...     deckName='Default',
            ...     modelName='Basic',
            ...     fields={'Front': 'apple', 'Back': '蘋果'},
            ...     tags=['english', 'fruit'],
            ... )
            >>> await client.add_note(note)
            1496198395707
        """
        result = await self._invoke(
            "addNote", note=note.model_dump(exclude_none=True)
        )
        return int(result) if result is not None else None  # type: ignore[arg-type]

    async def add_notes(self, notes: list[AnkiNote]) -> list[int | None]:
        """批次新增多則筆記。

        Args:
            notes: AnkiNote 模型實例列表。

        Returns:
            筆記 ID 列表，失敗的筆記對應位置為 None。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        raw_notes = [n.model_dump(exclude_none=True) for n in notes]
        result = await self._invoke("addNotes", notes=raw_notes)
        return list(result)  # type: ignore[arg-type]

    async def update_note(
        self,
        note_id: int,
        fields: dict[str, str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """更新現有筆記的欄位和/或標籤。

        fields 和 tags 可擇一省略而不影響另一個。

        Args:
            note_id: 要更新的筆記 ID。
            fields: 要更新的欄位字典。
            tags: 新的標籤列表，將取代舊標籤。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        note: dict[str, object] = {"id": note_id}
        if fields is not None:
            note["fields"] = fields
        if tags is not None:
            note["tags"] = tags
        await self._invoke("updateNote", note=note)

    async def update_note_fields(
        self,
        note_id: int,
        fields: dict[str, str],
    ) -> None:
        """更新現有筆記的欄位內容（僅欄位，不影響標籤）。

        Args:
            note_id: 要更新的筆記 ID。
            fields: 要更新的欄位字典。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        note: dict[str, object] = {"id": note_id, "fields": fields}
        await self._invoke("updateNoteFields", note=note)

    async def delete_notes(self, notes: list[int]) -> None:
        """刪除指定的筆記。

        若筆記有多張關聯卡片，所有關聯卡片都會被一起刪除。

        Args:
            notes: 要刪除的筆記 ID 列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("deleteNotes", notes=notes)

    async def find_notes(self, query: str) -> list[int]:
        """使用 Anki 搜尋語法查找筆記。

        搜尋語法文件：https://docs.ankiweb.net/searching.html

        Args:
            query: Anki 搜尋查詢語句。
                   常用範例：
                   - 'deck:Default' — 指定牌組中的所有筆記
                   - 'tag:english' — 含有指定標籤的筆記
                   - 'added:7' — 最近 7 天新增的筆記

        Returns:
            符合條件的筆記 ID 列表。

        Raises:
            AnkiConnectError: 查詢語法錯誤或 API 請求失敗時。
        """
        result = await self._invoke("findNotes", query=query)
        return list(result)  # type: ignore[arg-type]

    async def get_notes_info(
        self,
        notes: list[int] | None = None,
        query: str | None = None,
    ) -> list[AnkiNoteInfo]:
        """取得筆記的詳細資訊。

        可透過筆記 ID 列表或搜尋查詢語句取得資訊。兩者擇一即可。

        Args:
            notes: 筆記 ID 列表。與 query 二選一。
            query: Anki 搜尋查詢語句。與 notes 二選一。

        Returns:
            AnkiNoteInfo 模型實例列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
            ValueError: notes 和 query 皆未提供時。
        """
        if notes is not None:
            result = await self._invoke("notesInfo", notes=notes)
        elif query is not None:
            result = await self._invoke("notesInfo", query=query)
        else:
            raise ValueError("必須提供 notes 或 query 參數其中之一")
        return [AnkiNoteInfo(**item) for item in result]  # type: ignore[union-attr]

    async def add_tags(self, notes: list[int], tags: str) -> None:
        """為指定筆記新增標籤。

        Args:
            notes: 筆記 ID 列表。
            tags: 要新增的標籤，多個標籤以空格分隔。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("addTags", notes=notes, tags=tags)

    async def remove_tags(self, notes: list[int], tags: str) -> None:
        """移除指定筆記的標籤。

        Args:
            notes: 筆記 ID 列表。
            tags: 要移除的標籤，多個標籤以空格分隔。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("removeTags", notes=notes, tags=tags)

    async def get_tags(self) -> list[str]:
        """取得當前使用者的所有標籤。

        Returns:
            標籤名稱字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getTags")
        return list(result)  # type: ignore[arg-type]

    async def replace_tags(
        self,
        notes: list[int],
        tag_to_replace: str,
        replace_with_tag: str,
    ) -> None:
        """替換指定筆記中的標籤。

        Args:
            notes: 筆記 ID 列表。
            tag_to_replace: 要被替換的標籤名稱。
            replace_with_tag: 替換後的新標籤名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke(
            "replaceTags",
            notes=notes,
            tag_to_replace=tag_to_replace,
            replace_with_tag=replace_with_tag,
        )

    async def replace_tags_in_all_notes(
        self,
        tag_to_replace: str,
        replace_with_tag: str,
    ) -> None:
        """在所有筆記中替換指定標籤。

        Args:
            tag_to_replace: 要被替換的標籤名稱。
            replace_with_tag: 替換後的新標籤名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke(
            "replaceTagsInAllNotes",
            tag_to_replace=tag_to_replace,
            replace_with_tag=replace_with_tag,
        )

    async def clear_unused_tags(self) -> None:
        """清除所有未使用的標籤。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("clearUnusedTags")

    async def can_add_notes(
        self, notes: list[dict[str, object]]
    ) -> list[bool]:
        """檢查筆記是否可以被新增（不實際建立）。

        用於在批次新增前驗證筆記參數是否合法，以及防止重複生卡。

        Args:
            notes: 筆記物件列表，格式同 addNote 的參數。

        Returns:
            布林值列表，對應每個筆記是否可以被新增。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("canAddNotes", notes=notes)
        return list(result)  # type: ignore[arg-type]

    # ========================================================================
    # 卡片操作（Card Actions）
    # ========================================================================

    async def find_cards(self, query: str) -> list[int]:
        """使用 Anki 搜尋語法查找卡片。

        Args:
            query: Anki 搜尋查詢語句。

        Returns:
            符合條件的卡片 ID 列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("findCards", query=query)
        return list(result)  # type: ignore[arg-type]

    async def get_cards_info(
        self, cards: list[int]
    ) -> list[dict[str, object]]:
        """取得卡片的詳細資訊。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            卡片資訊字典列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("cardsInfo", cards=cards)
        return list(result)  # type: ignore[arg-type]

    async def suspend_cards(self, cards: list[int]) -> bool:
        """暫停指定的卡片（不會出現在複習排程中）。

        Args:
            cards: 要暫停的卡片 ID 列表。

        Returns:
            True 若至少有一張卡片被成功暫停，否則 False。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("suspend", cards=cards)
        return bool(result)

    async def unsuspend_cards(self, cards: list[int]) -> bool:
        """取消暫停指定的卡片。

        Args:
            cards: 要取消暫停的卡片 ID 列表。

        Returns:
            True 若至少有一張卡片被成功取消暫停，否則 False。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("unsuspend", cards=cards)
        return bool(result)

    async def get_ease_factors(self, cards: list[int]) -> list[int]:
        """取得指定卡片的簡易度因子。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            簡易度因子列表，常見值如 2500（預設）。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getEaseFactors", cards=cards)
        return list(result)  # type: ignore[arg-type]

    async def set_ease_factors(
        self, cards: list[int], ease_factors: list[int]
    ) -> list[bool]:
        """設定指定卡片的簡易度因子。

        Args:
            cards: 卡片 ID 列表。
            ease_factors: 對應的新簡易度因子列表。

        Returns:
            布林值列表，表示每張卡片是否設定成功。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke(
            "setEaseFactors", cards=cards, easeFactors=ease_factors
        )
        return list(result)  # type: ignore[arg-type]

    async def are_suspended(self, cards: list[int]) -> list[bool | None]:
        """批次檢查卡片是否處於暫停狀態。

        Args:
            cards: 卡片 ID 列表。

        Returns:
            布林值列表：True=已暫停, False=未暫停, None=卡片不存在。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("areSuspended", cards=cards)
        return list(result)  # type: ignore[arg-type]

    # ========================================================================
    # 媒體操作（Media Actions）
    # ========================================================================

    async def store_media_file(self, params: AnkiStoreMediaParams) -> str:
        """儲存媒體檔案到 Anki 的媒體資料夾。

        使用 Pydantic 模型驗證參數，確保至少提供一種檔案來源。

        Args:
            params: AnkiStoreMediaParams 模型實例。

        Returns:
            實際儲存的檔案名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。
            ValueError: data、path、url 皆未提供時。
        """
        if params.data is None and params.path is None and params.url is None:
            raise ValueError("必須提供 data、path 或 url 參數其中之一")

        result = await self._invoke(
            "storeMediaFile", **params.model_dump(exclude_none=True)
        )
        return str(result)

    async def retrieve_media_file(self, filename: str) -> str | bool:
        """讀取 Anki 媒體資料夾中的檔案內容。

        Args:
            filename: 檔案名稱。

        Returns:
            Base64 編碼的檔案內容字串，若檔案不存在則回傳 False。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("retrieveMediaFile", filename=filename)
        if isinstance(result, bool):
            return result
        return str(result)

    async def get_media_files_names(
        self, pattern: str = "*"
    ) -> list[str]:
        """搜尋符合模式的媒體檔案名稱。

        Args:
            pattern: glob 模式字串，預設 '*' 回傳所有檔案。

        Returns:
            符合模式的檔案名稱列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getMediaFilesNames", pattern=pattern)
        return list(result)  # type: ignore[arg-type]

    async def delete_media_file(self, filename: str) -> None:
        """刪除 Anki 媒體資料夾中的指定檔案。

        Args:
            filename: 要刪除的檔案名稱。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        await self._invoke("deleteMediaFile", filename=filename)

    async def get_media_dir_path(self) -> str:
        """取得當前設定檔的媒體資料夾完整路徑。

        Returns:
            媒體資料夾的絕對路徑字串。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getMediaDirPath")
        return str(result)

    # ========================================================================
    # 模型操作（Model Actions）
    # ========================================================================

    async def get_model_names(self) -> list[str]:
        """取得所有模型（筆記類型）名稱。

        Returns:
            模型名稱字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> await client.get_model_names()
            ['Basic', 'Cloze', 'TOEIC_Coach_Dark']
        """
        result = await self._invoke("modelNames")
        return list(result)  # type: ignore[arg-type]

    async def get_model_names_and_ids(self) -> dict[str, int]:
        """取得所有模型名稱及其對應的 ID。

        Returns:
            字典，鍵為模型名稱（str），值為模型 ID（int）。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("modelNamesAndIds")
        return dict(result)  # type: ignore[arg-type]

    async def get_model_field_names(self, model_name: str) -> list[str]:
        """取得指定模型的所有欄位名稱。

        Args:
            model_name: 模型名稱。

        Returns:
            欄位名稱字串列表，按順序排列。

        Raises:
            AnkiConnectError: 模型不存在或 API 請求失敗時。
        """
        result = await self._invoke(
            "modelFieldNames", modelName=model_name
        )
        return list(result)  # type: ignore[arg-type]

    async def create_model(
        self,
        model_name: str,
        in_order_fields: list[str],
        css: str,
        card_templates: list[dict[str, str]],
        is_cloze: bool = False,
    ) -> object:
        """建立新的 Anki 筆記類型 (Model)。

        Args:
            model_name: 模型名稱。
            in_order_fields: 欄位名稱列表，按順序排列。
            css: CSS 樣式表字串。
            card_templates: 卡片樣板列表，每個元素需包含
                            'Name'、'Front'、'Back' 三個鍵。
            is_cloze: 是否為克漏字題型，預設 False。

        Returns:
            AnkiConnect 回傳的 createModel 結果。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        return await self._invoke(
            "createModel",
            modelName=model_name,
            inOrderFields=in_order_fields,
            css=css,
            isCloze=is_cloze,
            cardTemplates=card_templates,
        )

    # ========================================================================
    # 雜項操作（Miscellaneous Actions）
    # ========================================================================

    async def get_version(self) -> int:
        """取得 AnkiConnect API 版本號。

        Returns:
            API 版本號整數，當前為 6。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("version")
        return int(result)  # type: ignore[arg-type]

    async def sync(self) -> None:
        """同步本地 Anki 集合至 AnkiWeb。

        Raises:
            AnkiConnectError: 同步失敗時。
        """
        await self._invoke("sync")

    async def request_permission(self) -> dict[str, object]:
        """請求使用 AnkiConnect API 的權限。

        首次從不受信任的來源呼叫時，Anki 會顯示彈窗詢問使用者是否允許。

        Returns:
            權限資訊字典，包含 permission, requireApiKey, version 等欄位。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("requestPermission")
        return dict(result)  # type: ignore[arg-type]

    async def get_profiles(self) -> list[str]:
        """取得所有 Anki 使用者設定檔名稱。

        Returns:
            設定檔名稱字串列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("getProfiles")
        return list(result)  # type: ignore[arg-type]

    async def multi(
        self, actions: list[dict[str, object]]
    ) -> list[object]:
        """在單一請求中執行多個 API 動作。

        Args:
            actions: API 動作列表，每個元素為包含 action 和可選 params 的字典。

        Returns:
            結果列表，每個元素對應一個動作的回傳值。

        Raises:
            AnkiConnectError: API 請求失敗時。

        Example:
            >>> results = await client.multi([
            ...     {'action': 'deckNames'},
            ...     {'action': 'version'},
            ... ])
        """
        result = await self._invoke("multi", actions=actions)
        return list(result)  # type: ignore[arg-type]

    async def gui_browse(self, query: str) -> list[int]:
        """在 Anki 的瀏覽器中打開搜尋結果。

        Args:
            query: Anki 搜尋查詢語句。

        Returns:
            符合搜尋條件的卡片 ID 列表。

        Raises:
            AnkiConnectError: API 請求失敗時。
        """
        result = await self._invoke("guiBrowse", query=query)
        return list(result)  # type: ignore[arg-type]
