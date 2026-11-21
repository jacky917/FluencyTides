"""Commit staged media files into Anki on card creation or field update, and undo on failure (media_upload plan D1).

建卡、修改欄位時把暫存的媒體檔存進 Anki，失敗時撤回（media_upload 計畫書 D1）。

Creation and update either succeed completely or return to how things were before submitting:

建卡與修改要嘛整個成功，要嘛回到送出前的樣子：

1. Before submitting: the files are in the staging area (``media_staging``), not in Anki.
2. On submit: first store the staged files that are used into Anki, then write the note.
3. Note write succeeds: delete the staged files (they are already in Anki).
4. Note write fails: delete the files this attempt stored in Anki and keep the staged files, which returns to
   step 1; after fixing the problem the user can resubmit without uploading again.

1. 送出前：檔案在暫存區（``media_staging``），Anki 裡沒有。
2. 送出時：先把用到的暫存檔存進 Anki，再寫筆記。
3. 寫筆記成功：刪掉暫存檔（已經在 Anki 裡了）。
4. 寫筆記失敗：把這次存進 Anki 的檔案刪掉，暫存檔留著——回到第 1 步，修正後再送不必重新上傳。

Before deleting in step 4, check whether any note uses the file: for example Anki's response timed out but the
note was actually written, or on a retry with the same file the first attempt actually succeeded. If a note uses
it, do not delete it; better leave a file behind than let a card point at a missing audio file. If the query or
the deletion fails, likewise do not delete: log a warning and leave the file in Anki (Anki's "Check Media" can
clean it up).

第 4 步刪檔前會先查有沒有筆記用到這個檔案：例如 Anki 回應逾時、其實已經寫進筆記，或同一個檔案重試時
第一次其實成功了。有筆記用到就不刪，寧可留下檔案，也不讓卡片指向不存在的音檔。查詢或刪除失敗時同樣不刪，
記 warning，檔案留在 Anki（可以用 Anki 的「檢查媒體」清掉）。
"""

from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.core.exceptions import MediaUploadExpiredError
from app.infrastructure.anki.client import AnkiConnectError
from app.infrastructure.anki.search import contains_term
from app.schemas.anki import AnkiStoreMediaParams
from app.services.media_names import uploaded_media_kind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping

    from app.infrastructure.anki.client import AnkiClient
    from app.services.media_staging import MediaStaging

logger = logging.getLogger(__name__)


def staged_names(values: Mapping[str, object], fields: Iterable[str]) -> list[str]:
    """Return the uploaded names put into the media fields of the values to write.

    回傳這次要寫入的值裡，媒體欄位填了哪些上傳的檔名。

    Only values matching the ``POST /media`` naming format are picked: the current value (``[sound:…]``), blanks
    and hand-typed names are not picked and are left to the handler's usual rules (current value unchanged, blank
    clears, hand-typed rejected). Non-string values are ignored.

    只挑符合 ``POST /media`` 命名格式的值：原值（``[sound:…]``）、空白、手打的名字不會被挑出來，
    交給 handler 照原本的規則處理（原值不變、空白清空、手打的拒絕）。不是字串的值會被忽略。

    Args:
        values: the values to write (creation parameters or the fields to change)
            要寫入的值（建立參數或要改的欄位）
        fields: the names of the media fields (or media parameters)
            媒體欄位（或媒體參數）的名稱

    Returns:
        the names, in field order and without duplicates
        檔名，依欄位順序、不重複
    """
    names: list[str] = []
    for field in fields:
        value = values.get(field)
        name = value.strip() if isinstance(value, str) else ""
        if uploaded_media_kind(name) and name not in names:
            names.append(name)
    return names


@asynccontextmanager
async def committed_media(
    anki_client: AnkiClient, staging: MediaStaging, names: list[str]
) -> AsyncIterator[None]:
    """Store staged files in Anki around the write that follows; undo on failure, delete the staged files on success.

    把暫存檔存進 Anki，包住後面的寫入；寫入失敗時撤回，成功時刪掉暫存檔。

    On entry, every staged file is read first, then each is stored in Anki. Any exception raised inside the block
    (including cancellation) undoes the files stored by this attempt via ``undo_stored_media`` and is then
    re-raised unchanged.

    進入時先讀出所有暫存檔，再逐一存進 Anki。區塊內拋出的任何例外（含取消）都會先以 ``undo_stored_media``
    撤回這次存進去的檔案，再原樣往上拋。

    Usage::

        async with committed_media(anki, staging, names):
            await handler.execute_create(...)

    Args:
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端
        staging: the staging area
            暫存區
        names: the names to store in Anki (from ``staged_names``)
            要存進 Anki 的檔名（``staged_names``）

    Raises:
        AnkiConnectError: if storing fails or Anki changes the file name (the files stored by this attempt have
            been undone)
            若存檔失敗或 Anki 改了檔名（已撤回這次存進去的檔案）
        MediaUploadExpiredError: if a name is not in the staging area (expired, the backend restarted, or already
            used); nothing is written
            若有檔名不在暫存區（過期、後端重啟過、已經用掉）；什麼都沒寫
    """
    contents: dict[str, bytes] = {}
    for name in names:
        data = staging.read(name)
        if data is None:
            raise MediaUploadExpiredError(
                f"上傳的檔案 {name} 已經不在（超過一天、後端重新部署過，或已經用在別張卡上），請重新上傳"
            )
        contents[name] = data

    stored: list[str] = []
    try:
        for name, data in contents.items():
            result = await anki_client.store_media_file(
                AnkiStoreMediaParams(filename=name, data=base64.b64encode(data).decode("ascii"))
            )
            stored.append(result)
            if result != name:
                # 不該發生（AnkiConnect 預設先刪同名檔再寫）；改過的名字和欄位裡填的對不上，不能繼續寫筆記
                logger.error("Anki 存檔用的檔名和要求的不同：要求 %s，實際 %r", name, result)
                raise AnkiConnectError(
                    f"Anki 存檔時改了檔名（要求 {name}，實際 {result!r}）；請再試一次，仍失敗請檢查 AnkiConnect 的版本"
                )
        yield
    except BaseException:
        # 含 CancelledError（請求被中斷）：不確定筆記有沒有寫進去，撤回時會先查有沒有筆記用到
        await undo_stored_media(anki_client, stored)
        raise
    for name in names:
        staging.discard(name)


async def undo_stored_media(anki_client: AnkiClient, names: list[str]) -> None:
    """Undo the media files this write stored in Anki, deleting only those no note uses.

    撤回這次存進 Anki 的媒體檔：沒有任何筆記用到的才刪。

    An ``AnkiConnectError`` is not raised: it is logged as a warning and the file is left in place.

    ``AnkiConnectError`` 不往上拋：失敗時記 warning、檔案留著。

    Args:
        anki_client: the AnkiConnect client
            AnkiConnect 客戶端
        names: the names this write stored in Anki
            這次存進 Anki 的檔名
    """
    for name in names:
        try:
            used_by = await anki_client.find_notes(contains_term(None, name))
            if used_by:
                logger.warning("寫入失敗，但 %s 已被筆記 %s 使用（可能其實寫進去了），不刪", name, used_by[:5])
                continue
            await anki_client.delete_media_file(name)
            logger.info("寫入失敗，已從 Anki 刪掉這次存進去的 %s", name)
        except AnkiConnectError as exc:
            logger.warning("寫入失敗後無法撤回 %s，檔案留在 Anki 媒體資料夾（可用「檢查媒體」清掉）：%s", name, exc)
