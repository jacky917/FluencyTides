"""Pydantic V2 schemas for MinIO object storage.

MinIO 物件存儲相關 Pydantic V2 Schema 定義模組。

Defines all data structures needed to interact with MinIO (S3-compatible)
object storage, covering cross-boundary validation of upload results, object
information, and bucket-policy settings.

本模組定義了與 MinIO (S3 相容) 物件存儲交互所需的所有資料結構，
涵蓋上傳結果、物件資訊、以及桶策略設定等跨邊界資料驗證。

All cross-boundary data structures are strongly validated with Pydantic V2;
raw dicts and ``typing.Any`` are strictly forbidden.

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 ``typing.Any``。

Design decisions:
    - Multi-model pattern (consistent with schemas/anki.py): request and
      response structures are defined separately so the API contract stays
      clear.
    - ``MinioObjectInfo.last_modified`` is ``str | None`` rather than
      ``datetime`` because the time format returned by the MinIO SDK may vary
      with the configuration; converting in the service layer is safer.

設計決策：
    - 採用 Multi-Model 模式（與 schemas/anki.py 一致），
      將「請求」與「回應」結構分開定義，確保 API 合約清晰。
    - ``MinioObjectInfo`` 的 ``last_modified`` 使用 ``str | None`` 而非 ``datetime``，
      是因為 MinIO SDK 回傳的時間格式可能因設定而異，在 Service 層再做轉換更安全。
"""

from datetime import timedelta

from pydantic import BaseModel, Field


# ============================================================================
# 上傳操作 Schema
# ============================================================================

class MinioUploadResult(BaseModel):
    """The result structure after a MinIO file upload completes.

    MinIO 檔案上傳完成後的結果結構。

    Wraps the information returned by the upload for the service layer to
    assemble API responses.

    封裝上傳操作的回傳資訊，供 Service 層組裝 API 回應使用。

    Attributes:
        bucket_name: the target bucket name
            目標儲存桶名稱
        object_name: the object name on MinIO after the upload, including the path prefix
            上傳後在 MinIO 上的物件名稱（含路徑前綴）
    """

    bucket_name: str
    object_name: str
    file_size_bytes: int = Field(
        default=0,
        ge=0,
        description="上傳檔案的大小（位元組），0 表示未偵測",
    )
    presigned_url: str | None = Field(
        default=None,
        description="預簽名下載 URL，若未產生則為 None",
    )


# ============================================================================
# 物件查詢 Schema
# ============================================================================

class MinioObjectInfo(BaseModel):
    """Basic information about a MinIO object.

    MinIO 物件的基本資訊結構。

    The return value of ``list_objects``, wrapping each object's metadata.

    用於 ``list_objects`` 操作的回傳值，封裝每個物件的元資料。

    Attributes:
        object_name: the object name, including the path prefix
            物件名稱（含路徑前綴）
    """

    object_name: str
    size_bytes: int = Field(
        default=0,
        ge=0,
        description="物件大小（位元組）",
    )
    last_modified: str | None = Field(
        default=None,
        description="最後修改時間的 ISO 格式字串",
    )
    content_type: str | None = Field(
        default=None,
        description="物件的 MIME 類型",
    )


# ============================================================================
# 預簽名 URL 請求 Schema
# ============================================================================

class MinioPresignedUrlRequest(BaseModel):
    """The request parameters for generating a MinIO presigned URL.

    產生 MinIO 預簽名 URL 的請求參數結構。

    Wraps all parameters needed to generate a presigned URL, so that the valid
    range of ``expires`` is validated at the service layer. MinIO's limit is
    usually 7 days.

    封裝產生預簽名 URL 時所需的所有參數，
    確保 ``expires`` 的合法範圍在 Service 層即被驗證。MinIO 的上限通常為 7 天。

    Attributes:
        bucket_name: the target bucket name
            目標儲存桶名稱
        object_name: the object name, including the path prefix
            物件名稱（含路徑前綴）
    """

    bucket_name: str
    object_name: str
    expires: timedelta = Field(
        default_factory=lambda: timedelta(days=7),
        description="URL 有效期限，預設 7 天（MinIO 上限）",
    )


# ============================================================================
# 桶策略 Schema
# ============================================================================

class MinioBucketPolicyStatement(BaseModel):
    """A single Statement in a standard S3 bucket policy.

    S3 標準桶策略中的單一 Statement 結構。

    Attributes:
        Effect: the policy effect (Allow / Deny)
            策略效果（Allow / Deny）
        Principal: the principal the policy applies to
            策略適用主體
        Action: the allowed actions
            允許的操作列表
        Resource: the ARNs of the resources the policy applies to
            策略適用的資源 ARN 列表
    """

    Effect: str = "Allow"
    Principal: dict[str, list[str]] = Field(
        default_factory=lambda: {"AWS": ["*"]},
    )
    Action: list[str] = Field(default_factory=list)
    Resource: list[str] = Field(default_factory=list)


class MinioBucketPolicy(BaseModel):
    """The full structure of a standard S3 bucket policy.

    S3 標準桶策略的完整結構。

    Used by ``set_bucket_policy`` to make sure the policy format is valid.

    用於 ``set_bucket_policy`` 操作，確保策略格式合法。

    Attributes:
        Version: the policy version, fixed at ``'2012-10-17'``
            策略版本，固定為 ``'2012-10-17'``
        Statement: the policy statements
            策略聲明列表
    """

    Version: str = "2012-10-17"
    Statement: list[MinioBucketPolicyStatement] = Field(default_factory=list)
