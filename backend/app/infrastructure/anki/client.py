"""Async AnkiConnect API v6 full CRUD client.

非同步 AnkiConnect API v6 完整 CRUD 客戶端。

This module provides async Python wrappers for every major AnkiConnect feature, including:
- Deck management: create, delete, query, move cards
- Note management: add, update, delete, search notes
- Card management: search, suspend/unsuspend, ease factors
- Media management: store, read, delete media files
- Model queries and edits: model names, fields, templates
- Miscellaneous: version query, sync, permission request, batch actions

本模組提供對 AnkiConnect 所有主要功能的非同步 Python 封裝，包括：
- 牌組（Deck）管理：建立、刪除、查詢、移動卡片
- 筆記（Note）管理：新增、更新、刪除、搜尋筆記
- 卡片（Card）管理：搜尋、暫停/取消暫停、簡易度因子
- 媒體（Media）管理：儲存、讀取、刪除媒體檔案
- 模型（Model）查詢與修改：取得模型名稱、欄位、模板
- 雜項操作：版本查詢、同步、權限請求、批次操作

Refactored from old/Anki/utils/anki_connect.py to use httpx.AsyncClient for
fully async I/O suited to FastAPI's high concurrency. All connection parameters are managed by
core.config.Settings, no longer by scattered .env loading logic.

重構自 old/Anki/utils/anki_connect.py，改用 httpx.AsyncClient 實現
完全非同步 I/O，適配 FastAPI 的高併發場景。所有連線參數統一由
core.config.Settings 管理，不再依賴散落的 .env 載入邏輯。

Dependencies:
    - httpx: the async HTTP client
      非同步 HTTP 客戶端
    - pydantic: data validation
      資料驗證
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

from app.core.exceptions.infrastructure import AnkiServiceError

logger = logging.getLogger(__name__)


class AnkiConnectError(AnkiServiceError):
    """Exception class for AnkiConnect API errors.

    AnkiConnect API 錯誤異常類別。

    Raised when the AnkiConnect API returns an error or the connection fails.

    當 AnkiConnect API 回傳錯誤或連線失敗時拋出此異常。

    Attributes:
        message: the error message string
            錯誤訊息字串
    """

    def __init__(self, message: str) -> None:
        """Initialize AnkiConnectError.

        初始化 AnkiConnectError。

        Messages about sync status or ``ChangesRequired`` are replaced with a friendly hint to check the sync state
        in desktop Anki.

        與同步狀態或 ``ChangesRequired`` 相關的訊息，會被換成請使用者到電腦版 Anki 檢查同步狀態的友善提示。

        Args:
            message: the error message returned by the AnkiConnect API
                AnkiConnect API 回傳的錯誤訊息
        """
        # 將常見的生硬錯誤訊息轉化為友善提示
        if "Sync status" in message or "ChangesRequired" in message:
            message = "Anki 正在進行背景同步或發生衝突，請打開電腦版 Anki 檢查同步狀態（可能需要手動點擊「上傳到 AnkiWeb」解決衝突）。"
            
        super().__init__(message)


class AnkiActionError(AnkiConnectError):
    """An error reported by the action itself although AnkiConnect answered.

    AnkiConnect 有回應，但這個動作回報錯誤（例如搜尋語法錯誤、筆記不存在）。

    Examples are invalid search syntax or a missing note. Distinguishes caller errors from transport failures
    (connection refused, timeouts), which stay plain ``AnkiConnectError``.

    用來區分呼叫端的錯誤與傳輸失敗（連線被拒、逾時）；後者仍是一般的 ``AnkiConnectError``。
    """


class AnkiClient:
    """Async AnkiConnect API v6 full CRUD wrapper client.

    非同步 AnkiConnect API v6 完整 CRUD 封裝客戶端。

    Provides async Python wrappers for every major AnkiConnect feature, reusing a connection pool through
    httpx.AsyncClient to meet FastAPI's high-concurrency needs.

    提供對 AnkiConnect 所有主要功能的 Python 非同步封裝。
    使用 httpx.AsyncClient 實現連線池復用，適配 FastAPI 的高併發需求。

    Most methods that write, plus ``find_notes`` and ``get_notes_info``, call ``sync`` first; that call is
    debounced to once per 10 seconds and its errors are suppressed (see ``sync``).

    大多數寫入方法，以及 ``find_notes``、``get_notes_info``，會先呼叫 ``sync``；這次同步以 10 秒防抖，
    錯誤會被抑制（見 ``sync``）。

    All connection information is read from core.config.Settings:
    - ANKI_CONNECT_URL: the full AnkiConnect endpoint URL
    - ANKI_CONNECT_API_KEY: the API key (optional)
    - CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET: Cloudflare Access credentials (optional)

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
        """Initialize the async AnkiConnect client.

        初始化非同步 AnkiConnect 客戶端。

        Values passed as arguments take precedence; missing ones are read from Settings. Cloudflare Access headers
        are added when both credentials are set, and the transport retries failed connections 3 times.

        優先使用參數傳入的值，若未提供則從 Settings 讀取。兩個 Cloudflare Access 憑證都有設定時會加上對應的
        header，傳輸層對連線失敗自動重試 3 次。

        Args:
            url: the AnkiConnect endpoint URL; read from Settings when ``None`` or empty
                AnkiConnect 端點 URL。若為 ``None`` 或空字串，從 Settings 讀取
            api_key: the API key; read from Settings when ``None`` or empty
                API 金鑰。若為 ``None`` 或空字串，從 Settings 讀取
            timeout: the HTTP request timeout in seconds; defaults to 30
                HTTP 請求超時秒數，預設 30 秒
        """
        self._url = url or settings.ANKI_CONNECT_URL
        self._api_key = api_key or settings.ANKI_CONNECT_API_KEY or None
        self._timeout = timeout

        # 組裝 HTTP headers，若有 Cloudflare Access 憑證則加入
        headers: dict[str, str] = {}
        if settings.CF_ACCESS_CLIENT_ID and settings.CF_ACCESS_CLIENT_SECRET:
            headers["CF-Access-Client-Id"] = settings.CF_ACCESS_CLIENT_ID
            headers["CF-Access-Client-Secret"] = settings.CF_ACCESS_CLIENT_SECRET

        # 建立 httpx.AsyncClient 以支援連線池復用，並設定重試機制
        # AnkiConnect 在長時間的 sync 之後可能會斷開 keep-alive 連線，
        # 加上 retries=3 讓 httpx 在遇到 ReadError/BrokenResourceError 時自動重試。
        transport = httpx.AsyncHTTPTransport(retries=3)
        self._client = httpx.AsyncClient(
            transport=transport,
            headers=headers,
            timeout=self._timeout
        )
        self._last_sync_time = 0.0

        logger.info("AnkiConnect 非同步客戶端已初始化，目標地址: %s", self._url)

    async def close(self) -> None:
        """Close the underlying httpx connection pool.

        關閉底層 httpx 連線池。

        Should be called at application shutdown to release all HTTP connection resources.

        應在應用程式關閉時呼叫，釋放所有 HTTP 連線資源。
        """
        await self._client.aclose()
        logger.info("AnkiConnect 客戶端連線已關閉。")

    # ========================================================================
    # 核心方法（Core）
    # ========================================================================

    async def _invoke(
        self, action: str, *, _timeout: float | None = None, **params: object
    ) -> object:
        """Send an API request to AnkiConnect (the low-level method).

        向 AnkiConnect 發送 API 請求的底層方法。

        Builds the JSON-RPC request body, sends an async HTTP POST to the AnkiConnect server, and parses the
        response. Every public API method is built on this one. Connection errors and other network errors are
        retried up to 3 attempts in total, 0.5 seconds apart; timeouts and HTTP status errors are not retried.

        組裝 JSON-RPC 請求體，發送非同步 HTTP POST 請求至 AnkiConnect
        伺服器，並解析回應結果。此方法是所有公開 API 方法的基礎。連線錯誤與其他網路錯誤最多嘗試 3 次、
        間隔 0.5 秒；逾時與 HTTP 狀態碼錯誤不重試。

        S011 fix: actions needing a longer timeout (for example sync) pass ``_timeout``
        for that single request instead of mutating the shared client's
        timeout attribute, which would corrupt other requests under concurrency.
        ``_timeout`` is keyword-only and captured explicitly by the signature so
        it can never leak into ``**params`` and be sent to AnkiConnect as an
        API parameter.

        S011 修復：需要較長超時的動作（如 sync）改以 ``_timeout`` 對「單次請求」
        指定，不再竄改共享 client 的 timeout 屬性，避免併發下污染其他請求。
        ``_timeout`` 為 keyword-only 且由簽名顯式接住，確保不會混入 ``**params``
        被誤送給 AnkiConnect 當成 API 參數。

        Args:
            action: the AnkiConnect API action name, for example ``"deckNames"`` or ``"addNote"``
                AnkiConnect API 動作名稱，例如 ``"deckNames"``、``"addNote"``
            _timeout: the timeout in seconds for this request only; ``None`` uses the default
                本次請求專用的超時秒數；``None`` 表示使用預設值
            **params: keyword parameters passed to the API action
                傳遞給 API 動作的關鍵字參數

        Returns:
            the value of the ``result`` field in the API response; its type depends on the action
            API 回應中的 ``result`` 欄位值，型別取決於具體的 API 動作

        Raises:
            AnkiActionError: if AnkiConnect returns a non-null ``error`` field
                若 AnkiConnect 回傳的 ``error`` 欄位不為 null
            AnkiConnectError: if the server is unreachable, the request times out, or an HTTP or network error occurs
                若無法連接伺服器、請求超時，或發生 HTTP 或網路錯誤
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

        import asyncio
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self._client.post(
                    self._url,
                    json=req.model_dump(exclude_none=True),
                    # 單次請求覆寫，不動共享 client 狀態（S011）
                    # Per-request override; shared client state untouched.
                    timeout=_timeout if _timeout is not None else self._timeout,
                )
                response.raise_for_status()
                break  # 成功，跳出重試迴圈
            except httpx.ConnectError:
                if attempt < max_retries - 1:
                    logger.warning("無法連接到 AnkiConnect，正在重試 (%d/%d)...", attempt + 1, max_retries)
                    await asyncio.sleep(0.5)
                    continue
                logger.error("無法連接到 AnkiConnect 伺服器: %s", self._url)
                raise AnkiConnectError(
                    f"無法連接到 AnkiConnect 伺服器 ({self._url})。"
                    f"請確認 Anki 已啟動且 AnkiConnect 插件已安裝。"
                )
            except httpx.TimeoutException:
                # 超時通常不需要立即重試
                effective_timeout = _timeout if _timeout is not None else self._timeout
                logger.error("AnkiConnect 請求超時: action=%s", action)
                raise AnkiConnectError(
                    f"AnkiConnect 請求超時（{effective_timeout}秒），action: {action}"
                )
            except httpx.HTTPStatusError as e:
                # HTTP 狀態碼錯誤（如 400, 500），伺服器已正常回應，不重試
                logger.error("AnkiConnect HTTP 錯誤: %s", e)
                raise AnkiConnectError(f"AnkiConnect HTTP 錯誤: {e}")
            except httpx.RequestError as e:
                # 捕捉其他網路層級錯誤 (如 httpx.ReadError, BrokenResourceError 等)
                # 這些通常是 keep-alive 斷開或伺服器繁忙，可以重試
                if attempt < max_retries - 1:
                    logger.warning("AnkiConnect 網路請求異常 (可能為 keep-alive 斷開)，正在重試 (%d/%d): %s", attempt + 1, max_retries, e)
                    await asyncio.sleep(0.5)
                    continue
                logger.error("AnkiConnect 網路請求異常: %s", e)
                raise AnkiConnectError(f"AnkiConnect 網路請求異常: {e}")

        # 解析回應
        resp = AnkiActionResponse(**response.json())

        if resp.error is not None:
            logger.error(
                "AnkiConnect API 錯誤: %s (action: %s)", resp.error, action
            )
            raise AnkiActionError(resp.error)

        logger.debug("AnkiConnect 請求成功 -> action: %s", action)
        return resp.result

    # ========================================================================
    # 牌組操作（Deck Actions）
    # ========================================================================

    async def get_deck_names(self) -> list[str]:
        """Return all deck names.

        取得所有牌組名稱。

        Returns:
            a list of all deck names
            包含所有牌組名稱的字串串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗

        Example:
            >>> await client.get_deck_names()
            ['Default', 'Japanese::JLPT N3']
        """
        result = await self._invoke("deckNames")
        return list(result)  # type: ignore[arg-type]

    async def get_deck_names_and_ids(self) -> dict[str, int]:
        """Return all deck names and their IDs.

        取得所有牌組名稱及其對應的 ID。

        Returns:
            a dict mapping deck names to deck IDs
            字典，鍵為牌組名稱，值為牌組 ID

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗

        Example:
            >>> await client.get_deck_names_and_ids()
            {'Default': 1, 'Japanese::JLPT N3': 1519323742721}
        """
        result = await self._invoke("deckNamesAndIds")
        return dict(result)  # type: ignore[arg-type]

    async def create_deck(self, deck: str) -> int:
        """Create a new deck.

        建立新的牌組。

        An existing deck with the same name is not overwritten. Nested decks can be created with the ``::``
        separator.

        若已存在同名牌組，不會覆蓋。支援使用 ``::`` 分隔符建立巢狀牌組。

        Args:
            deck: the deck name; use ``::`` for sub-decks, for example ``"Japanese::Tokyo"``
                牌組名稱。使用 ``::`` 建立子牌組，例如 ``"Japanese::Tokyo"``

        Returns:
            the ID of the newly created deck
            新建立牌組的 ID

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗

        Example:
            >>> await client.create_deck('English::Vocabulary')
            1519323742721
        """
        result = await self._invoke("createDeck", deck=deck)
        return int(result)  # type: ignore[arg-type]

    async def delete_decks(
        self, decks: list[str], cards_too: bool = True
    ) -> None:
        """Delete the given decks.

        刪除指定的牌組。

        Args:
            decks: the names of the decks to delete
                要刪除的牌組名稱串列
            cards_too: whether to delete the cards in the decks too; AnkiConnect requires ``True``
                是否同時刪除牌組中的卡片。AnkiConnect 要求必須為 ``True``

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self._invoke("deleteDecks", decks=decks, cardsToo=cards_too)

    async def get_deck_config(self, deck: str) -> dict[str, object]:
        """Return the config group object of the given deck.

        取得指定牌組的設定群組物件。

        Args:
            deck: the deck name
                牌組名稱

        Returns:
            a dict of the deck configuration
            包含牌組設定的字典

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getDeckConfig", deck=deck)
        return dict(result)  # type: ignore[arg-type]

    async def change_deck(self, cards: list[int], deck: str) -> None:
        """Move the given cards to another deck.

        將指定卡片移動到另一個牌組。

        The target deck is created automatically if it does not exist.

        若目標牌組不存在，會自動建立。

        Args:
            cards: the IDs of the cards to move
                要移動的卡片 ID 串列
            deck: the target deck name
                目標牌組名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self._invoke("changeDeck", cards=cards, deck=deck)

    async def get_decks(self, cards: list[int]) -> dict[str, list[int]]:
        """Return the decks the given cards belong to.

        根據卡片 ID 取得其所屬的牌組。

        Args:
            cards: the card IDs
                卡片 ID 串列

        Returns:
            a dict mapping deck names to the IDs of the given cards in that deck
            字典，鍵為牌組名稱，值為屬於該牌組的卡片 ID 串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getDecks", cards=cards)
        return dict(result)  # type: ignore[arg-type]

    # ========================================================================
    # 筆記操作（Note Actions）
    # ========================================================================

    async def add_note(self, note: AnkiNote) -> int | None:
        """Create a single note.

        建立單一筆記。

        A Pydantic model guarantees that the note structure is correct, removing the risk of field errors the old
        version had with bare dicts. Note: this method has auto-sync built in.

        使用 Pydantic 模型保證傳入的筆記結構正確無誤，
        消除舊版使用裸字典時可能的欄位錯誤風險。
        【註】此方法內建 auto-sync 機制。

        Args:
            note: an AnkiNote Pydantic model instance
                AnkiNote Pydantic 模型實例

        Returns:
            the ID of the new note, or ``None`` if AnkiConnect returns no ID
            新建筆記的 ID；若 AnkiConnect 沒有回傳 ID 則為 ``None``

        Raises:
            AnkiActionError: if AnkiConnect rejects the note (for example, a duplicate)
                若 AnkiConnect 拒絕這筆筆記（例如重複）
            AnkiConnectError: if the API request fails
                若 API 請求失敗

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
        await self.sync()
        result = await self._invoke(
            "addNote", note=note.model_dump(exclude_none=True)
        )
        return int(result) if result is not None else None  # type: ignore[arg-type]

    async def add_notes(self, notes: list[AnkiNote]) -> list[int | None]:
        """Create multiple notes in batch.

        批次建立多筆筆記。

        Note: this method has auto-sync built in.

        【註】此方法內建 auto-sync 機制。

        Args:
            notes: the AnkiNote model instances
                AnkiNote 模型實例串列

        Returns:
            the note IDs, with ``None`` at the positions of notes that failed
            筆記 ID 串列，失敗的筆記對應位置為 ``None``

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        raw_notes = [n.model_dump(exclude_none=True) for n in notes]
        result = await self._invoke("addNotes", notes=raw_notes)
        return list(result)  # type: ignore[arg-type]

    async def update_note(
        self,
        note_id: int,
        fields: dict[str, str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Update the fields and/or tags of an existing note.

        更新現有筆記的欄位和/或標籤。

        Either ``fields`` or ``tags`` may be omitted without affecting the other. Note: this method has auto-sync
        built in.

        ``fields`` 和 ``tags`` 可擇一省略而不影響另一個。【註】此方法內建 auto-sync 機制。

        Args:
            note_id: the ID of the note to update
                要更新的筆記 ID
            fields: a dict of the fields to update; ``None`` leaves the fields unchanged
                要更新的欄位字典；``None`` 表示不改欄位
            tags: the new tag list, replacing the old tags; ``None`` leaves the tags unchanged
                新的標籤串列，將取代舊標籤；``None`` 表示不改標籤

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
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
        """Update only the field content of an existing note (tags are untouched).

        更新現有筆記的欄位內容（僅欄位，不影響標籤）。

        Note: this method has auto-sync built in.

        【註】此方法內建 auto-sync 機制。

        Args:
            note_id: the ID of the note to update
                要更新的筆記 ID
            fields: a dict of the fields to update
                要更新的欄位字典

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        note: dict[str, object] = {"id": note_id, "fields": fields}
        await self._invoke("updateNoteFields", note=note)

    async def delete_notes(self, notes: list[int]) -> None:
        """Delete the given notes (cards).

        刪除指定的筆記（卡片）。

        If a note has several cards, all of them are deleted together. Note: this method has auto-sync built in.

        若筆記有多張關聯卡片，所有關聯卡片都會被一起刪除。
        【註】此方法內建 auto-sync 機制。

        Args:
            notes: the IDs of the notes to delete
                要刪除的筆記 ID 串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke("deleteNotes", notes=notes)

    async def find_notes(self, query: str) -> list[int]:
        """Search notes by a query string.

        根據查詢字串搜尋筆記。

        Search syntax documentation: https://docs.ankiweb.net/searching.html
        Note: this method has auto-sync built in.

        搜尋語法文件：https://docs.ankiweb.net/searching.html
        【註】此方法內建 auto-sync 機制。

        Args:
            query: the Anki search query. Common examples: ``deck:Default`` (all notes in a deck), ``tag:english``
                (notes with a tag), ``added:7`` (notes added in the last 7 days).
                Anki 搜尋查詢語句。常用範例：``deck:Default``（指定牌組中的所有筆記）、``tag:english``
                （含有指定標籤的筆記）、``added:7``（最近 7 天新增的筆記）

        Returns:
            the IDs of the matching notes
            符合條件的筆記 ID 串列

        Raises:
            AnkiActionError: if the query syntax is invalid
                若查詢語法錯誤
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        result = await self._invoke("findNotes", query=query)
        return list(result)  # type: ignore[arg-type]

    async def get_notes_info(
        self,
        notes: list[int] | None = None,
        query: str | None = None,
    ) -> list[AnkiNoteInfo]:
        """Return detailed information of the given notes.

        取得指定筆記的詳細資訊。

        Information can be fetched by a list of note IDs or by a search query; give one of the two. If both are
        given, ``notes`` is used. Notes that do not exist are left out, so the result may be shorter than ``notes``.
        Note: this method has auto-sync built in.

        可透過筆記 ID 列表或搜尋查詢語句取得資訊。兩者擇一即可；兩者都給時使用 ``notes``。不存在的筆記會被略過，
        因此結果可能比 ``notes`` 短。【註】此方法內建 auto-sync 機制。

        Args:
            notes: the note IDs; give either this or ``query``
                筆記 ID 串列。與 ``query`` 二選一
            query: the Anki search query; give either this or ``notes``
                Anki 搜尋查詢語句。與 ``notes`` 二選一

        Returns:
            a list of AnkiNoteInfo model instances
            AnkiNoteInfo 模型實例串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
            ValueError: if neither ``notes`` nor ``query`` is provided
                若 ``notes`` 和 ``query`` 皆未提供
        """
        await self.sync()
        if notes is not None:
            result = await self._invoke("notesInfo", notes=notes)
        elif query is not None:
            result = await self._invoke("notesInfo", query=query)
        else:
            raise ValueError("必須提供 notes 或 query 參數其中之一")
        return [AnkiNoteInfo(**item) for item in result if item]  # type: ignore[union-attr]

    async def add_tags(self, notes: list[int], tags: str) -> None:
        """Add tags to the given notes.

        為指定筆記新增標籤。

        Args:
            notes: the note IDs
                筆記 ID 串列
            tags: the tags to add, separated by spaces
                要新增的標籤，多個標籤以空格分隔

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke("addTags", notes=notes, tags=tags)

    async def remove_tags(self, notes: list[int], tags: str) -> None:
        """Remove tags from the given notes.

        移除指定筆記的標籤。

        Args:
            notes: the note IDs
                筆記 ID 串列
            tags: the tags to remove, separated by spaces
                要移除的標籤，多個標籤以空格分隔

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke("removeTags", notes=notes, tags=tags)

    async def get_tags(self) -> list[str]:
        """Return all tags of the current user.

        取得當前使用者的所有標籤。

        Returns:
            the tag names
            標籤名稱字串串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getTags")
        return list(result)  # type: ignore[arg-type]

    async def replace_tags(
        self,
        notes: list[int],
        tag_to_replace: str,
        replace_with_tag: str,
    ) -> None:
        """Replace a tag in the given notes.

        替換指定筆記中的標籤。

        Args:
            notes: the note IDs
                筆記 ID 串列
            tag_to_replace: the tag name to be replaced
                要被替換的標籤名稱
            replace_with_tag: the new tag name
                替換後的新標籤名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
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
        """Replace a tag across all notes.

        在所有筆記中替換指定標籤。

        Args:
            tag_to_replace: the tag name to be replaced
                要被替換的標籤名稱
            replace_with_tag: the new tag name
                替換後的新標籤名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke(
            "replaceTagsInAllNotes",
            tag_to_replace=tag_to_replace,
            replace_with_tag=replace_with_tag,
        )

    async def clear_unused_tags(self) -> None:
        """Clear all unused tags.

        清除所有未使用的標籤。

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke("clearUnusedTags")

    async def can_add_notes(
        self, notes: list[dict[str, object]]
    ) -> list[bool]:
        """Check whether notes can be added, without creating them.

        檢查筆記是否可以被新增（不實際建立）。

        Used before a batch add to validate note parameters and to prevent generating duplicate cards.

        用於在批次新增前驗證筆記參數是否合法，以及防止重複生卡。

        Args:
            notes: the note objects, in the same format as the ``addNote`` parameter
                筆記物件串列，格式同 ``addNote`` 的參數

        Returns:
            one boolean per note, telling whether it can be added
            布林值串列，對應每個筆記是否可以被新增

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("canAddNotes", notes=notes)
        return list(result)  # type: ignore[arg-type]

    # ========================================================================
    # 卡片操作（Card Actions）
    # ========================================================================

    async def find_cards(self, query: str) -> list[int]:
        """Find cards using Anki search syntax.

        使用 Anki 搜尋語法查找卡片。

        Args:
            query: the Anki search query
                Anki 搜尋查詢語句

        Returns:
            the IDs of the matching cards
            符合條件的卡片 ID 串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("findCards", query=query)
        return list(result)  # type: ignore[arg-type]

    async def get_cards_info(
        self, cards: list[int]
    ) -> list[dict[str, object]]:
        """Return detailed information of the given cards.

        取得卡片的詳細資訊。

        Args:
            cards: the card IDs
                卡片 ID 串列

        Returns:
            a list of card info dicts
            卡片資訊字典串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("cardsInfo", cards=cards)
        return list(result)  # type: ignore[arg-type]

    async def suspend_cards(self, cards: list[int]) -> bool:
        """Suspend the given cards so they no longer appear in review scheduling.

        暫停指定的卡片（不會出現在複習排程中）。

        Args:
            cards: the IDs of the cards to suspend
                要暫停的卡片 ID 串列

        Returns:
            ``True`` if at least one card was suspended, otherwise ``False``
            若至少有一張卡片被成功暫停則為 ``True``，否則為 ``False``

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        result = await self._invoke("suspend", cards=cards)
        return bool(result)

    async def unsuspend_cards(self, cards: list[int]) -> bool:
        """Unsuspend the given cards.

        取消暫停指定的卡片。

        Args:
            cards: the IDs of the cards to unsuspend
                要取消暫停的卡片 ID 串列

        Returns:
            ``True`` if at least one card was unsuspended, otherwise ``False``
            若至少有一張卡片被成功取消暫停則為 ``True``，否則為 ``False``

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        result = await self._invoke("unsuspend", cards=cards)
        return bool(result)

    async def get_ease_factors(self, cards: list[int]) -> list[int]:
        """Return the ease factors of the given cards.

        取得指定卡片的簡易度因子。

        Args:
            cards: the card IDs
                卡片 ID 串列

        Returns:
            the ease factors, one per card; a common value is 2500 (the default)
            簡易度因子串列，常見值如 2500（預設）

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getEaseFactors", cards=cards)
        return list(result)  # type: ignore[arg-type]

    async def set_ease_factors(
        self, cards: list[int], ease_factors: list[int]
    ) -> list[bool]:
        """Set the ease factors of the given cards.

        設定指定卡片的簡易度因子。

        Args:
            cards: the card IDs
                卡片 ID 串列
            ease_factors: the new ease factors, one per card in the same order
                對應的新簡易度因子串列

        Returns:
            one boolean per card, telling whether it was set successfully
            布林值串列，表示每張卡片是否設定成功

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        result = await self._invoke(
            "setEaseFactors", cards=cards, easeFactors=ease_factors
        )
        return list(result)  # type: ignore[arg-type]

    async def are_suspended(self, cards: list[int]) -> list[bool | None]:
        """Check in batch whether the given cards are suspended.

        批次檢查卡片是否處於暫停狀態。

        Args:
            cards: the card IDs
                卡片 ID 串列

        Returns:
            one value per card: ``True`` if suspended, ``False`` if not, ``None`` if the card does not exist
            每張卡片一個值：``True`` 為已暫停，``False`` 為未暫停，``None`` 為卡片不存在

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("areSuspended", cards=cards)
        return list(result)  # type: ignore[arg-type]

    # ========================================================================
    # 媒體操作（Media Actions）
    # ========================================================================

    async def store_media_file(self, params: AnkiStoreMediaParams) -> str:
        """Store a media file into Anki's media folder.

        儲存媒體檔案到 Anki 的媒體資料夾。

        A Pydantic model validates the parameters, and at least one file source must be provided.

        使用 Pydantic 模型驗證參數，確保至少提供一種檔案來源。

        Args:
            params: an AnkiStoreMediaParams model instance
                AnkiStoreMediaParams 模型實例

        Returns:
            the file name actually stored
            實際儲存的檔案名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
            ValueError: if none of data, path or url is provided
                若 data、path、url 皆未提供
        """
        if params.data is None and params.path is None and params.url is None:
            raise ValueError("必須提供 data、path 或 url 參數其中之一")

        await self.sync()
        result = await self._invoke(
            "storeMediaFile", **params.model_dump(exclude_none=True)
        )
        return str(result)

    async def retrieve_media_file(self, filename: str) -> str | bool:
        """Read a file's content from Anki's media folder.

        讀取 Anki 媒體資料夾中的檔案內容。

        Args:
            filename: the file name
                檔案名稱

        Returns:
            the Base64-encoded file content, or ``False`` if the file does not exist
            Base64 編碼的檔案內容字串，若檔案不存在則回傳 ``False``

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("retrieveMediaFile", filename=filename)
        if isinstance(result, bool):
            return result
        return str(result)

    async def get_media_files_names(
        self, pattern: str = "*"
    ) -> list[str]:
        """Return the media file names matching a pattern.

        搜尋符合模式的媒體檔案名稱。

        Args:
            pattern: the glob pattern; the default ``"*"`` returns all files
                glob 模式字串，預設 ``"*"`` 回傳所有檔案

        Returns:
            the matching file names
            符合模式的檔案名稱串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getMediaFilesNames", pattern=pattern)
        return list(result)  # type: ignore[arg-type]

    async def delete_media_file(self, filename: str) -> None:
        """Delete the given file from Anki's media folder.

        刪除 Anki 媒體資料夾中的指定檔案。

        Args:
            filename: the name of the file to delete
                要刪除的檔案名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke("deleteMediaFile", filename=filename)

    async def get_media_dir_path(self) -> str:
        """Return the full media-folder path of the current profile.

        取得當前設定檔的媒體資料夾完整路徑。

        Returns:
            the absolute path of the media folder
            媒體資料夾的絕對路徑字串

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getMediaDirPath")
        return str(result)

    # ========================================================================
    # 模型操作（Model Actions）
    # ========================================================================

    async def get_model_names(self) -> list[str]:
        """Return all model (note type) names.

        取得所有模型（筆記類型）名稱。

        Returns:
            the model names
            模型名稱字串串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗

        Example:
            >>> await client.get_model_names()
            ['Basic', 'Cloze', 'TOEIC_Coach_Dark']
        """
        result = await self._invoke("modelNames")
        return list(result)  # type: ignore[arg-type]

    async def get_model_names_and_ids(self) -> dict[str, int]:
        """Return all model names and their IDs.

        取得所有模型名稱及其對應的 ID。

        Returns:
            a dict mapping model names to model IDs
            字典，鍵為模型名稱，值為模型 ID

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("modelNamesAndIds")
        return dict(result)  # type: ignore[arg-type]

    async def get_model_field_names(self, model_name: str) -> list[str]:
        """Return all field names of the given model.

        取得指定模型的所有欄位名稱。

        Args:
            model_name: the model name
                模型名稱

        Returns:
            the field names, in order
            欄位名稱字串串列，按順序排列

        Raises:
            AnkiActionError: if the model does not exist
                若模型不存在
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke(
            "modelFieldNames", modelName=model_name
        )
        return list(result)  # type: ignore[arg-type]

    async def get_model_templates(self, model_name: str) -> dict[str, dict[str, str]]:
        """Return the card templates of the given model, in template order.

        取得指定模型的卡片模板（依模板序號排列）。

        Args:
            model_name: the model name
                模型名稱

        Returns:
            a dict mapping each template name to ``{"Front": ..., "Back": ...}``
            模板名稱 → ``{"Front": ..., "Back": ...}``

        Raises:
            AnkiActionError: if the model does not exist
                若模型不存在
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("modelTemplates", modelName=model_name)
        return dict(result)  # type: ignore[arg-type]

    async def add_model_field(
        self, model_name: str, field_name: str, index: int | None = None
    ) -> None:
        """Add a field to the given model.

        新增欄位到指定的模型。

        Args:
            model_name: the model name
                模型名稱
            field_name: the name of the field to add
                新增的欄位名稱
            index: the 0-based insertion index; ``None`` appends the field at the end
                插入位置索引（0-based）。若為 ``None``，則附加於最後

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        kwargs: dict[str, object] = {
            "modelName": model_name,
            "fieldName": field_name,
        }
        if index is not None:
            kwargs["index"] = index
        await self._invoke("modelFieldAdd", **kwargs)

    async def rename_model_field(
        self, model_name: str, old_field_name: str, new_field_name: str
    ) -> None:
        """Rename a field in the given model.

        重新命名模型中的欄位。

        Args:
            model_name: the model name
                模型名稱
            old_field_name: the current field name
                原本的欄位名稱
            new_field_name: the new field name
                新的欄位名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke(
            "modelFieldRename",
            modelName=model_name,
            oldFieldName=old_field_name,
            newFieldName=new_field_name,
        )

    async def remove_model_field(
        self, model_name: str, field_name: str
    ) -> None:
        """Remove the given field from the model.

        從模型中刪除指定的欄位。

        Args:
            model_name: the model name
                模型名稱
            field_name: the name of the field to remove
                欲刪除的欄位名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke(
            "modelFieldRemove", modelName=model_name, fieldName=field_name
        )

    async def rename_model_template(
        self, model_name: str, old_template_name: str, new_template_name: str
    ) -> None:
        """Rename a card template of the given model (AnkiWeb will require a full sync).

        重新命名模型的卡片模板（AnkiWeb 會要求完整同步）。

        Args:
            model_name: the model name
                模型名稱
            old_template_name: the current template name
                原本的模板名稱
            new_template_name: the new template name
                新的模板名稱

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke(
            "modelTemplateRename",
            modelName=model_name,
            oldTemplateName=old_template_name,
            newTemplateName=new_template_name,
        )

    async def remove_model_template(self, model_name: str, template_name: str) -> None:
        """Remove a card template of the given model.

        刪除模型的卡片模板。

        Every card of that template in all notes (and its review history) is deleted with it, and AnkiWeb will
        require a full sync.

        所有筆記的那一種卡（與複習紀錄）一起刪掉，AnkiWeb 會要求完整同步。

        Args:
            model_name: the model name
                模型名稱
            template_name: the name of the template to remove
                欲刪除的模板名稱

        Raises:
            AnkiConnectError: if the API request fails (for example, this is the only template left)
                若 API 請求失敗（例如只剩這一個模板）
        """
        await self.sync()
        await self._invoke("modelTemplateRemove", modelName=model_name, templateName=template_name)

    async def reposition_model_field(
        self, model_name: str, field_name: str, index: int
    ) -> None:
        """Move a field to another position within the model.

        重新排列模型欄位的順序。

        Args:
            model_name: the model name
                模型名稱
            field_name: the name of the field to move
                欲移動的欄位名稱
            index: the 0-based target index
                目標位置索引（0-based）

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        await self._invoke(
            "modelFieldReposition",
            modelName=model_name,
            fieldName=field_name,
            index=index,
        )

    async def create_model(
        self,
        model_name: str,
        in_order_fields: list[str],
        css: str,
        card_templates: list[dict[str, str]],
        is_cloze: bool = False,
    ) -> object:
        """Create a new Anki note type (model).

        建立新的 Anki 筆記類型 (Model)。

        Args:
            model_name: the model name
                模型名稱
            in_order_fields: the field names, in order
                欄位名稱串列，按順序排列
            css: the CSS stylesheet
                CSS 樣式表字串
            card_templates: the card templates; each element must have the keys ``Name``, ``Front`` and ``Back``
                卡片樣板串列，每個元素需包含 ``Name``、``Front``、``Back`` 三個鍵
            is_cloze: whether it is a cloze type; defaults to ``False``
                是否為克漏字題型，預設 ``False``

        Returns:
            the ``createModel`` result returned by AnkiConnect
            AnkiConnect 回傳的 ``createModel`` 結果

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        return await self._invoke(
            "createModel",
            modelName=model_name,
            inOrderFields=in_order_fields,
            css=css,
            isCloze=is_cloze,
            cardTemplates=card_templates,
        )

    async def update_model_templates(
        self,
        model_name: str,
        templates: dict[str, dict[str, str]],
    ) -> None:
        """Update the HTML templates (Front/Back) of an existing model.

        更新現有模型的 HTML 樣板 (Front/Back)。

        Args:
            model_name: the model name
                模型名稱
            templates: the template definition dict, for example ``{"Card 1": {"Front": "...", "Back": "..."}}``
                樣板定義字典，例如 ``{"Card 1": {"Front": "...", "Back": "..."}}``

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        model_obj = {
            "name": model_name,
            "templates": templates
        }
        await self._invoke("updateModelTemplates", model=model_obj)

    async def update_model_styling(
        self,
        model_name: str,
        css: str,
    ) -> None:
        """Update the CSS stylesheet of an existing model.

        更新現有模型的 CSS 樣式表。

        Args:
            model_name: the model name
                模型名稱
            css: the CSS stylesheet
                CSS 字串

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        await self.sync()
        model_obj = {
            "name": model_name,
            "css": css
        }
        await self._invoke("updateModelStyling", model=model_obj)

    # ========================================================================
    # 雜項操作（Miscellaneous Actions）
    # ========================================================================

    async def get_version(self) -> int:
        """Return the AnkiConnect API version number.

        取得 AnkiConnect API 版本號。

        Returns:
            the API version, currently 6
            API 版本號整數，當前為 6

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("version")
        return int(result)  # type: ignore[arg-type]

    async def sync(self, raise_errors: bool = False, force: bool = False) -> None:
        """Trigger a sync between Anki and AnkiWeb.

        啟動 Anki 與 AnkiWeb 之間的同步。

        To avoid repeated triggers, a 10-second debounce is implemented: a call within 10 seconds of the last
        successful sync does nothing unless ``force`` is ``True``. A failed sync does not reset the debounce timer.

        為了避免重複觸發，實作了 10 秒的 Debounce（防抖）機制：距離上次成功同步不到 10 秒的呼叫不做任何事，
        除非 ``force`` 為 ``True``。同步失敗不會重設防抖計時。

        Args:
            raise_errors: whether to raise when the sync fails; defaults to ``False`` (errors are suppressed so
                local operations are not blocked)
                若為 ``True``，則在同步失敗時拋出例外。預設為 ``False``（自動抑制錯誤以防阻斷本地操作）
            force: whether to bypass the debounce and sync anyway; for when written data must be pushed
                若為 ``True``，則忽略 Debounce 機制強制作業。用於確認有寫入資料後必須推送的情況

        Raises:
            AnkiConnectError: if the sync fails and ``raise_errors`` is ``True``
                若同步失敗且 ``raise_errors`` 為 ``True``
        """
        import time
        now = time.time()
        if not force and (now - self._last_sync_time < 10.0):
            logger.debug("跳過 Anki 同步（距離上次同步小於 10 秒）。")
            return
            
        logger.info("正在觸發 Anki 同步...")
        # S011：以 per-request timeout 取代竄改共享 client.timeout，
        # 避免同時進行中的其他請求繼承到錯誤的超時設定（競態）。
        # S011: use a per-request timeout instead of mutating the shared
        # client.timeout, which would leak the wrong timeout into other
        # in-flight requests (race condition).
        try:
            await self._invoke("sync", _timeout=self.SYNC_TIMEOUT)
            self._last_sync_time = time.time()
            logger.info("✅ Anki 同步完成。")
        except AnkiConnectError as e:
            logger.warning("⚠️ Anki 同步失敗，這不影響本地操作，請確認 AnkiWeb 狀態或稍後手動同步: %s", e)
            if raise_errors:
                raise

    async def request_permission(self) -> dict[str, object]:
        """Request permission to use the AnkiConnect API.

        請求使用 AnkiConnect API 的權限。

        On the first call from an untrusted origin, Anki shows a popup asking the user whether to allow it.

        首次從不受信任的來源呼叫時，Anki 會顯示彈窗詢問使用者是否允許。

        Returns:
            a permission info dict with fields such as ``permission``, ``requireApiKey`` and ``version``
            權限資訊字典，包含 ``permission``、``requireApiKey``、``version`` 等欄位

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("requestPermission")
        return dict(result)  # type: ignore[arg-type]

    async def get_profiles(self) -> list[str]:
        """Return all Anki user profile names.

        取得所有 Anki 使用者設定檔名稱。

        Returns:
            the profile names
            設定檔名稱字串串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("getProfiles")
        return list(result)  # type: ignore[arg-type]

    async def multi(
        self, actions: list[dict[str, object]]
    ) -> list[object]:
        """Execute multiple API actions in a single request.

        在單一請求中執行多個 API 動作。

        Args:
            actions: the API actions; each element is a dict with ``action`` and optional ``params``
                API 動作串列，每個元素為包含 ``action`` 和可選 ``params`` 的字典

        Returns:
            the results, one per action in the same order
            結果串列，每個元素對應一個動作的回傳值

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗

        Example:
            >>> results = await client.multi([
            ...     {'action': 'deckNames'},
            ...     {'action': 'version'},
            ... ])
        """
        result = await self._invoke("multi", actions=actions)
        return list(result)  # type: ignore[arg-type]

    async def gui_browse(self, query: str) -> list[int]:
        """Open search results in Anki's card browser.

        在 Anki 的瀏覽器中打開搜尋結果。

        Args:
            query: the Anki search query
                Anki 搜尋查詢語句

        Returns:
            the IDs of the cards matching the query
            符合搜尋條件的卡片 ID 串列

        Raises:
            AnkiConnectError: if the API request fails
                若 API 請求失敗
        """
        result = await self._invoke("guiBrowse", query=query)
        return list(result)  # type: ignore[arg-type]
