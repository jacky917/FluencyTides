"""
MinIO 媒體存取 REST API 路由模組。

本模組定義了 /api/v1/storage/ 前綴下的所有端點，
提供媒體檔案的上傳、列表、預簽名 URL 取得、刪除等功能。

當前定位（Phase 2）：獨立的媒體存取 API，尚未與卡片生成流程整合。

設計原則（嚴格遵守 Clean Architecture）：
    - Controller 層零業務邏輯：僅負責接收請求、委託 Service、回傳結果。
    - 所有業務邏輯封裝在 StorageService 中。
    - 所有端點均受 API Key 認證保護。

MinIO media access REST API router module.

This module defines all endpoints under the /api/v1/storage/ prefix,
providing media file upload, listing, presigned URL retrieval, and deletion.

Current positioning (Phase 2): a standalone media access API, not yet
integrated with the card generation flow.

Design principles (strict Clean Architecture):
    - Zero business logic in controllers: they only receive requests,
      delegate to the service, and return results.
    - All business logic is encapsulated in StorageService.
    - Every endpoint is protected by API key authentication.
"""

import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.core.auth import verify_api_key
from app.core.dependencies import get_storage_service
from app.schemas.card import ErrorResponse
from app.schemas.storage_api import (
    StorageListResponse,
    StoragePresignedUrlResponse,
    StorageUploadResponse,
)
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/storage",
    tags=["Storage"],
    dependencies=[Depends(verify_api_key)],
)


@router.post(
    "/upload",
    response_model=StorageUploadResponse,
    responses={
        502: {"model": ErrorResponse, "description": "MinIO 服務異常"},
    },
    summary="上傳媒體檔案",
    description="上傳檔案至 MinIO 儲存桶，自動生成規範化名稱與預簽名 URL。",
)
async def upload_file(
    file: UploadFile = File(..., description="要上傳的媒體檔案"),
    prefix: str = Query(
        default="",
        description="物件名稱前綴（如 'voice/'）",
    ),
    storage_service: StorageService = Depends(get_storage_service),
) -> StorageUploadResponse:
    """上傳媒體檔案的 Controller 端點。

    Controller endpoint for uploading a media file.

    Args:
        file: 上傳的檔案。The uploaded file.
        prefix: 物件名稱前綴。Object name prefix.
        storage_service: 注入的 StorageService 實例。
            Injected StorageService instance.

    Returns:
        StorageUploadResponse 包含物件名稱、大小與預簽名 URL。
        StorageUploadResponse containing the object name, size, and
        presigned URL.
    """
    result = await storage_service.upload_media(
        file=file,
        object_name_prefix=prefix if prefix else None,
    )
    return StorageUploadResponse(
        object_name=result.object_name,
        bucket_name=result.bucket_name,
        file_size_bytes=result.file_size_bytes,
        presigned_url=result.presigned_url or "",
    )


@router.get(
    "/files",
    response_model=StorageListResponse,
    responses={
        502: {"model": ErrorResponse, "description": "MinIO 服務異常"},
    },
    summary="列出儲存桶內檔案",
    description="列出預設儲存桶內的所有或篩選後的媒體檔案。",
)
async def list_files(
    prefix: str | None = Query(
        default=None,
        description="物件名稱前綴過濾（如 'voice/20240101/'）",
    ),
    storage_service: StorageService = Depends(get_storage_service),
) -> StorageListResponse:
    """列出媒體檔案的 Controller 端點。

    Controller endpoint for listing media files.

    Args:
        prefix: 物件名稱前綴過濾。Object name prefix filter.
        storage_service: 注入的 StorageService 實例。
            Injected StorageService instance.

    Returns:
        StorageListResponse 包含檔案列表與總數。
        StorageListResponse containing the file list and total count.
    """
    files = await storage_service.list_media(prefix=prefix)
    return StorageListResponse(
        files=files,
        total_count=len(files),
    )


@router.get(
    "/presign/{object_name:path}",
    response_model=StoragePresignedUrlResponse,
    responses={
        502: {"model": ErrorResponse, "description": "MinIO 服務異常"},
    },
    summary="取得預簽名下載 URL",
    description="為指定物件產生帶有時效性的預簽名下載 URL。",
)
async def get_presigned_url(
    object_name: str,
    expires_days: int = Query(
        default=7,
        ge=1,
        le=7,
        description="URL 有效天數（1-7，MinIO 上限 7 天）",
    ),
    storage_service: StorageService = Depends(get_storage_service),
) -> StoragePresignedUrlResponse:
    """取得預簽名 URL 的 Controller 端點。

    Controller endpoint for obtaining a presigned download URL.

    Args:
        object_name: 物件名稱（含路徑前綴）。Object name (with path prefix).
        expires_days: URL 有效天數。Number of days the URL stays valid.
        storage_service: 注入的 StorageService 實例。
            Injected StorageService instance.

    Returns:
        StoragePresignedUrlResponse 包含 URL 與有效期限。
        StoragePresignedUrlResponse containing the URL and its expiry.
    """
    url = await storage_service.get_download_url(
        object_name=object_name,
        expires_days=expires_days,
    )
    return StoragePresignedUrlResponse(
        object_name=object_name,
        presigned_url=url,
        expires_days=expires_days,
    )


@router.delete(
    "/files/{object_name:path}",
    status_code=204,
    responses={
        502: {"model": ErrorResponse, "description": "MinIO 服務異常"},
    },
    summary="刪除媒體檔案",
    description="刪除儲存桶內指定的媒體檔案。此操作為冪等操作。",
)
async def delete_file(
    object_name: str,
    storage_service: StorageService = Depends(get_storage_service),
) -> None:
    """刪除媒體檔案的 Controller 端點。

    Controller endpoint for deleting a media file.

    Args:
        object_name: 物件名稱（含路徑前綴）。Object name (with path prefix).
        storage_service: 注入的 StorageService 實例。
            Injected StorageService instance.
    """
    await storage_service.delete_media(object_name=object_name)
