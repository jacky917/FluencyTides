"""劇情流程圖的業務錯誤（由全域 handler 轉成 ``{"error_code", "message"}``）。

Story-flowchart business errors.
"""

from app.core.exceptions import FluencyTidesError


class StoryGameNotFoundError(FluencyTidesError):
    """這款遊戲沒有上線中的流程圖版本。No active build for the game."""

    error_code = "STORY_GAME_NOT_FOUND"
    status_code = 404


class StoryBuildNotFoundError(FluencyTidesError):
    """指定的版本不存在或不能讀。The requested build does not exist."""

    error_code = "STORY_BUILD_NOT_FOUND"
    status_code = 404


class StoryNodeNotFoundError(FluencyTidesError):
    """節點不在這個版本中。The node is not in this build."""

    error_code = "STORY_NODE_NOT_FOUND"
    status_code = 404


class StoryLineNotFoundError(FluencyTidesError):
    """台詞或筆記在流程圖上找不到。The line or note is not on the flowchart."""

    error_code = "STORY_LINE_NOT_FOUND"
    status_code = 404


class StoryLocateParamError(FluencyTidesError):
    """``locate`` 需要剛好一個 ``note_id`` 或 ``line_id``。Exactly one locate key is required."""

    error_code = "STORY_LOCATE_PARAM"
    status_code = 400


class StorySaveUnavailableError(FluencyTidesError):
    """這款遊戲或這句台詞不能產生存檔。Saves are unavailable for this game or line."""

    error_code = "STORY_SAVE_UNAVAILABLE"
    status_code = 409


class StorySaveSlotError(FluencyTidesError):
    """不認得的存檔欄位或路線預設。Unknown save slot or route preset."""

    error_code = "STORY_SAVE_SLOT"
    status_code = 400


class StoryAssetNotFoundError(FluencyTidesError):
    """素材（縮圖、存檔樣板）不存在或讀不到。An asset is missing."""

    error_code = "STORY_ASSET_NOT_FOUND"
    status_code = 404


class StoryAssetUnavailableError(FluencyTidesError):
    """素材來源（本機資料夾或 MinIO）暫時讀不到；和「物件不存在」分開。

    The asset source (local folder or MinIO) failed for a reason other than
    a missing object, e.g. MinIO is down or a file is unreadable.
    """

    error_code = "STORY_ASSET_UNAVAILABLE"
    status_code = 503


class StoryConfigInvalidError(FluencyTidesError):
    """上線版本的 ``story_builds.config`` 不符合這款遊戲的設定模型（應在匯入時擋下）。

    The stored build config fails the game adapter's config model; the
    importer should have rejected it.
    """

    error_code = "STORY_CONFIG_INVALID"
    status_code = 500
