"""Pydantic V2 response schemas for the storage REST API.

MinIO 存取 API 回應 Pydantic V2 Schema 定義模組。

Defines the response structures needed by the Storage REST API endpoints.
Unlike schemas/storage.py (models internal to the infrastructure layer), the
models here are used only by the API controller layer to format responses.

本模組定義了 Storage REST API 端點所需的回應結構。
與 schemas/storage.py（Infrastructure 層內部模型）區分，
此處的模型專供 API Controller 層格式化回應使用。

Design decisions:
    - API-layer response models are separated from infrastructure-layer
      models, following the best practice of separating extraction schemas
      from application schemas.
    - API responses may include extra derived fields (for example
      ``total_count``), while the infrastructure layer only cares about raw
      data.

設計決策：
    - 將 API 層回應模型與 Infrastructure 層模型分離，
      遵循「提取 Schema 與應用 Schema 分離」的最佳實踐。
    - API 回應可包含額外的衍生欄位（如 ``total_count``），
      而 Infrastructure 層僅關注原始資料。
"""

from pydantic import BaseModel, Field

from app.schemas.storage import MinioObjectInfo


class StorageUploadResponse(BaseModel):
    """The API response structure for a successful file upload.

    檔案上傳成功的 API 回應結構。

    ``object_name`` includes the path prefix.

    ``object_name`` 含路徑前綴。
    """

    object_name: str = Field(
        ...,
        description="上傳後在 MinIO 上的物件名稱",
    )
    bucket_name: str = Field(
        ...,
        description="目標儲存桶名稱",
    )
    file_size_bytes: int = Field(
        ...,
        ge=0,
        description="上傳檔案的大小（位元組）",
    )
    presigned_url: str = Field(
        ...,
        description="預簽名下載 URL",
    )


class StorageListResponse(BaseModel):
    """The API response structure for a file listing.

    檔案列表的 API 回應結構。
    """

    files: list[MinioObjectInfo] = Field(
        default_factory=list,
        description="物件資訊列表",
    )
    total_count: int = Field(
        ...,
        ge=0,
        description="符合條件的物件總數",
    )


class StoragePresignedUrlResponse(BaseModel):
    """The API response structure for a presigned URL.

    預簽名 URL 的 API 回應結構。
    """

    object_name: str = Field(
        ...,
        description="物件名稱",
    )
    presigned_url: str = Field(
        ...,
        description="產生的預簽名 URL",
    )
    expires_days: int = Field(
        ...,
        ge=1,
        description="URL 有效天數",
    )
