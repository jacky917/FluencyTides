"""
MinIO 物件存儲相關 Pydantic V2 Schema 定義模組。

Pydantic V2 schemas for MinIO object storage.

本模組定義了與 MinIO (S3 相容) 物件存儲交互所需的所有資料結構，
涵蓋上傳結果、物件資訊、以及桶策略設定等跨邊界資料驗證。

Defines all data structures for interacting with MinIO (S3-compatible)
object storage: upload results, object info, and bucket-policy settings.

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 typing.Any。

All cross-boundary data structures are strongly validated with Pydantic V2;
raw dicts and typing.Any are strictly forbidden.

設計決策：
    - 採用 Multi-Model 模式（與 schemas/anki.py 一致），
      將「請求」與「回應」結構分開定義，確保 API 合約清晰。
    - MinioObjectInfo 的 last_modified 使用 Optional[str] 而非 datetime，
      是因為 MinIO SDK 回傳的時間格式可能因設定而異，在 Service 層再做轉換更安全。

Design decisions:
    - Multi-model pattern (consistent with schemas/anki.py): request and
      response structures are defined separately for clear API contracts.
    - MinioObjectInfo.last_modified is Optional[str] rather than datetime
      because the MinIO SDK's time format may vary; converting in the
      service layer is safer.
"""

from datetime import timedelta

from pydantic import BaseModel, Field


# ============================================================================
# 上傳操作 Schema
# ============================================================================

class MinioUploadResult(BaseModel):
    """MinIO 檔案上傳完成後的結果結構。

    Result structure returned after a MinIO file upload.

    封裝上傳操作的回傳資訊，供 Service 層組裝 API 回應使用。

    Wraps the upload result for the service layer to assemble API responses.

    Attributes:
        bucket_name: 目標儲存桶名稱。Target bucket name.
        object_name: 上傳後在 MinIO 上的物件名稱（含路徑前綴）。Object name
            on MinIO after upload (including path prefix).
        file_size_bytes: 上傳檔案的大小（位元組）。Uploaded file size in
            bytes.
        presigned_url: 預簽名下載 URL（若有產生）。Presigned download URL,
            if generated.
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
    """MinIO 物件的基本資訊結構。

    Basic MinIO object information.

    用於 list_objects 操作的回傳值，封裝每個物件的元資料。

    Return value of list_objects, wrapping each object's metadata.

    Attributes:
        object_name: 物件名稱（含路徑前綴）。Object name (with path prefix).
        size_bytes: 物件大小（位元組）。Object size in bytes.
        last_modified: 最後修改時間的 ISO 格式字串。Last-modified time as
            an ISO string.
        content_type: 物件的 MIME 類型（若可偵測）。MIME type, if
            detectable.
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
    """產生 MinIO 預簽名 URL 的請求參數結構。

    Request parameters for generating a MinIO presigned URL.

    封裝產生預簽名 URL 時所需的所有參數，
    確保 expires 的合法範圍在 Service 層即被驗證。

    Wraps all parameters needed to generate a presigned URL, so the valid
    expires range is validated at the service layer.

    Attributes:
        bucket_name: 目標儲存桶名稱。Target bucket name.
        object_name: 物件名稱（含路徑前綴）。Object name (with path prefix).
        expires: URL 有效期限。MinIO 的上限通常為 7 天。URL validity
            duration; MinIO's limit is typically 7 days.
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
    """S3 標準桶策略中的單一 Statement 結構。

    Single Statement in a standard S3 bucket policy.

    Attributes:
        Effect: 策略效果（Allow / Deny）。Policy effect (Allow / Deny).
        Principal: 策略適用主體。Policy principal.
        Action: 允許的操作列表。Allowed actions.
        Resource: 策略適用的資源 ARN 列表。Resource ARNs the policy applies
            to.
    """

    Effect: str = "Allow"
    Principal: dict[str, list[str]] = Field(
        default_factory=lambda: {"AWS": ["*"]},
    )
    Action: list[str] = Field(default_factory=list)
    Resource: list[str] = Field(default_factory=list)


class MinioBucketPolicy(BaseModel):
    """S3 標準桶策略的完整結構。

    Full standard S3 bucket-policy structure.

    用於 set_bucket_policy 操作，確保策略格式合法。

    Used by set_bucket_policy to guarantee a valid policy format.

    Attributes:
        Version: 策略版本，固定為 '2012-10-17'。Policy version, fixed at
            '2012-10-17'.
        Statement: 策略聲明列表。Policy statements.
    """

    Version: str = "2012-10-17"
    Statement: list[MinioBucketPolicyStatement] = Field(default_factory=list)
