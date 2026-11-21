"""Story-flowchart business errors.

劇情流程圖的業務錯誤。

The global handler turns them into ``{"error_code", "message"}``.

由全域 handler 轉成 ``{"error_code", "message"}``。
"""

from app.core.exceptions import FluencyTidesError


class StoryGameNotFoundError(FluencyTidesError):
    """The game has no active flowchart build.

    這款遊戲沒有上線中的流程圖版本。
    """

    error_code = "STORY_GAME_NOT_FOUND"
    status_code = 404


class StoryBuildNotFoundError(FluencyTidesError):
    """The requested build does not exist or cannot be read.

    指定的版本不存在或不能讀。
    """

    error_code = "STORY_BUILD_NOT_FOUND"
    status_code = 404


class StoryNodeNotFoundError(FluencyTidesError):
    """The node is not in this build.

    節點不在這個版本中。
    """

    error_code = "STORY_NODE_NOT_FOUND"
    status_code = 404


class StoryLineNotFoundError(FluencyTidesError):
    """The line or note cannot be found on the flowchart.

    台詞或筆記在流程圖上找不到。
    """

    error_code = "STORY_LINE_NOT_FOUND"
    status_code = 404


class StoryLocateParamError(FluencyTidesError):
    """``locate`` needs exactly one of ``note_id`` or ``line_id``.

    ``locate`` 需要剛好一個 ``note_id`` 或 ``line_id``。
    """

    error_code = "STORY_LOCATE_PARAM"
    status_code = 400


class StorySaveUnavailableError(FluencyTidesError):
    """No save can be produced for this game or this line.

    這款遊戲或這句台詞不能產生存檔。
    """

    error_code = "STORY_SAVE_UNAVAILABLE"
    status_code = 409


class StorySaveSlotError(FluencyTidesError):
    """An unknown save slot or route preset.

    不認得的存檔欄位或路線預設。
    """

    error_code = "STORY_SAVE_SLOT"
    status_code = 400


class StoryAssetNotFoundError(FluencyTidesError):
    """An asset (thumbnail or save template) does not exist or cannot be read.

    素材（縮圖、存檔樣板）不存在或讀不到。
    """

    error_code = "STORY_ASSET_NOT_FOUND"
    status_code = 404


class StoryAssetUnavailableError(FluencyTidesError):
    """The asset source (local folder or MinIO) is temporarily unreadable; kept apart from "object not found".

    素材來源（本機資料夾或 MinIO）暫時讀不到；和「物件不存在」分開。

    It covers failures other than a missing object, for example MinIO being down or a file being unreadable.

    涵蓋物件不存在以外的失敗，例如 MinIO 停擺或檔案讀不到。
    """

    error_code = "STORY_ASSET_UNAVAILABLE"
    status_code = 503


class StoryConfigInvalidError(FluencyTidesError):
    """The active build's ``story_builds.config`` fails this game's config model.

    上線版本的 ``story_builds.config`` 不符合這款遊戲的設定模型。

    The importer should have rejected it.

    應在匯入時擋下。
    """

    error_code = "STORY_CONFIG_INVALID"
    status_code = 500


class StorySaveTemplateError(FluencyTidesError):
    """The save template is malformed, so the writer cannot produce a save.

    存檔樣板的格式不對，寫入器無法產生存檔。

    The extractor or importer should have rejected it.

    應在擷取或匯入時擋下。
    """

    error_code = "STORY_SAVE_TEMPLATE"
    status_code = 500
