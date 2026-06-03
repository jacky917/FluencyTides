"""
MinIO 物件存儲業務服務模組。

本模組在 Infrastructure 層的 MinioClient 之上添加業務邏輯，
包括自動確保 Bucket 存在、檔案命名規範化、上傳後自動產生預簽名 URL 等。

當前定位（Phase 2）：
    - 作為基礎工具類「打地基」，提供獨立的媒體上傳/下載/列表/刪除 API。
    - 尚未與卡片生成流程整合（音檔生成 → MinIO 上傳 → Anki 引用）。

未來設想：
    - 語音合成結果（VOICEPEAK .wav）上傳至 MinIO，將永久鏈接放入筆記。
    - Telegram Bot 傳入音檔 → 轉換 → 上傳至 MinIO。

設計原則：
    - 此模組屬於 Service 層，透過注入 MinioClient 完成所有存儲操作。
    - 禁止在此處直接建立 MinIO SDK 連線或讀取環境變數。
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import StorageServiceError
from app.infrastructure.storage.minio_client import MinioClient, MinioStorageError
from app.schemas.storage import MinioObjectInfo, MinioUploadResult

logger = logging.getLogger(__name__)


class StorageService:
    """MinIO 物件存儲業務服務。

    在 Infrastructure 層的 MinioClient 之上添加：
    - 自動確保 Bucket 存在（冪等操作）。
    - 檔案命名規範化（日期前綴 + UUID 避衝突）。
    - 上傳後自動產生預簽名 URL 並回傳。

    Attributes:
        _minio_client: 注入的 MinioClient 實例。
        _default_bucket: 預設儲存桶名稱（從 Settings 讀取）。
    """

    def __init__(self, minio_client: MinioClient) -> None:
        """初始化 StorageService。

        Args:
            minio_client: 注入的 MinioClient 實例。
        """
        self._minio_client = minio_client
        self._default_bucket = settings.MINIO_DEFAULT_BUCKET

    async def upload_media(
        self,
        file: UploadFile,
        bucket_name: str | None = None,
        object_name_prefix: str | None = None,
    ) -> MinioUploadResult:
        """上傳媒體檔案至 MinIO。

        自動處理：
        1. 確保目標 Bucket 存在。
        2. 生成規範化的物件名稱（日期/UUID_原始檔名）。
        3. 將 UploadFile 暫存至臨時檔案後上傳。
        4. 上傳後自動產生預簽名 URL。

        Args:
            file: FastAPI UploadFile 物件。
            bucket_name: 目標儲存桶名稱。若為 None，使用預設桶。
            object_name_prefix: 物件名稱前綴（如 'voice/'）。

        Returns:
            MinioUploadResult，包含 presigned_url。

        Raises:
            StorageServiceError: 上傳過程中任何步驟失敗時。
        """
        target_bucket = bucket_name or self._default_bucket
        original_filename = file.filename or "unnamed_file"

        # 生成規範化物件名稱：{prefix}{date}/{uuid}_{filename}
        # 使用 UUID 避免同名檔案衝突，日期前綴便於後續整理。
        date_prefix = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
        unique_id = uuid.uuid4().hex[:8]
        prefix = object_name_prefix or ""
        object_name = f"{prefix}{date_prefix}/{unique_id}_{original_filename}"

        try:
            # 確保 Bucket 存在（冪等操作）
            await self._minio_client.ensure_bucket_exists(target_bucket)

            # 讀取上傳內容並暫存
            content = await file.read()
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(
                delete=False, suffix=f"_{original_filename}"
            ) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                # 上傳至 MinIO
                upload_result = await self._minio_client.upload_file(
                    bucket_name=target_bucket,
                    object_name=object_name,
                    file_path=tmp_path,
                )
            finally:
                # 清理暫存檔案
                os.unlink(tmp_path)

            # 產生預簽名 URL
            presigned_url = await self._minio_client.get_presigned_url(
                bucket_name=target_bucket,
                object_name=object_name,
            )

            # 將 presigned_url 填入結果
            upload_result.presigned_url = presigned_url

            logger.info(
                "媒體檔案上傳成功: %s/%s (%d bytes)",
                target_bucket,
                object_name,
                upload_result.file_size_bytes,
            )
            return upload_result

        except MinioStorageError as e:
            raise StorageServiceError(
                f"媒體檔案上傳失敗: {e.message}"
            ) from e

    async def list_media(
        self,
        prefix: str | None = None,
        bucket_name: str | None = None,
    ) -> list[MinioObjectInfo]:
        """列出儲存桶內的媒體檔案。

        Args:
            prefix: 物件名稱前綴過濾。
            bucket_name: 目標儲存桶名稱。若為 None，使用預設桶。

        Returns:
            MinioObjectInfo 列表。

        Raises:
            StorageServiceError: 列舉失敗時。
        """
        target_bucket = bucket_name or self._default_bucket

        try:
            return await self._minio_client.list_objects(
                bucket_name=target_bucket,
                prefix=prefix,
            )
        except MinioStorageError as e:
            raise StorageServiceError(
                f"列舉媒體檔案失敗: {e.message}"
            ) from e

    async def get_download_url(
        self,
        object_name: str,
        bucket_name: str | None = None,
        expires_days: int = 7,
    ) -> str:
        """取得媒體檔案的預簽名下載 URL。

        Args:
            object_name: 物件名稱（含路徑前綴）。
            bucket_name: 目標儲存桶名稱。若為 None，使用預設桶。
            expires_days: URL 有效天數，預設 7 天。

        Returns:
            預簽名下載 URL 字串。

        Raises:
            StorageServiceError: 產生 URL 失敗時。
        """
        target_bucket = bucket_name or self._default_bucket

        try:
            return await self._minio_client.get_presigned_url(
                bucket_name=target_bucket,
                object_name=object_name,
                expires_days=expires_days,
            )
        except MinioStorageError as e:
            raise StorageServiceError(
                f"產生預簽名 URL 失敗: {e.message}"
            ) from e

    async def delete_media(
        self,
        object_name: str,
        bucket_name: str | None = None,
    ) -> None:
        """刪除儲存桶內的媒體檔案。

        Args:
            object_name: 物件名稱（含路徑前綴）。
            bucket_name: 目標儲存桶名稱。若為 None，使用預設桶。

        Raises:
            StorageServiceError: 刪除失敗時。
        """
        target_bucket = bucket_name or self._default_bucket

        try:
            await self._minio_client.delete_file(
                bucket_name=target_bucket,
                object_name=object_name,
            )
            logger.info("媒體檔案已刪除: %s/%s", target_bucket, object_name)
        except MinioStorageError as e:
            raise StorageServiceError(
                f"刪除媒體檔案失敗: {e.message}"
            ) from e
