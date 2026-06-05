"""
非同步 MinIO (S3 相容) 物件存儲客戶端模組。

本模組封裝與 MinIO 物件存儲服務的所有交互操作，包括：
- 儲存桶（Bucket）管理：建立、檢查、設定策略
- 檔案（Object）管理：上傳、下載、刪除、列舉
- 預簽名 URL 產生：提供有時效性的外部存取連結

重構自 old/VOICEPEAK/utils/minio_store.py，改進如下：
- 使用 asyncio.to_thread() 包裝同步 MinIO SDK，實現非同步 I/O。
- 所有連線參數統一由 core.config.Settings 管理，不再依賴散落的 .env 載入。
- 新增 Pydantic 模型驗證回傳資料結構（MinioUploadResult, MinioObjectInfo 等）。
- 新增自訂異常 MinioStorageError 統一錯誤處理。
- 補齊舊代碼中所有 CRUD 功能（download、delete、list、policy）。

設計決策：
- 使用 asyncio.to_thread() 而非原生 async 客戶端，是因為官方 minio-py SDK
  目前尚無原生 async 支援。to_thread() 將同步 I/O 操作委託給執行緒池，
  避免阻塞 FastAPI 的事件迴圈，在中低並發場景下效能損耗可忽略。
- 密碼日誌遮蔽：初始化時僅記錄 access_key，secret_key 以星號遮蔽，
  避免在日誌中洩露敏感憑證。
- presigned URL 的有效期限預設 7 天，對齊 MinIO 伺服器端上限，
  超過此值伺服器會自動拒絕。

Dependencies:
    - minio: MinIO Python SDK (同步)
    - pydantic: 資料驗證
"""

import asyncio
import logging
from datetime import timedelta

from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.schemas.storage import (
    MinioBucketPolicy,
    MinioBucketPolicyStatement,
    MinioObjectInfo,
    MinioUploadResult,
)

logger = logging.getLogger(__name__)


class MinioStorageError(Exception):
    """MinIO 物件存儲操作錯誤異常類別。

    當 MinIO SDK 操作失敗、連線逾時或參數錯誤時拋出此異常，
    統一 Infrastructure 層的錯誤處理界面。

    Attributes:
        message: 錯誤訊息字串。
    """

    def __init__(self, message: str) -> None:
        """初始化 MinioStorageError。

        Args:
            message: 描述錯誤原因的訊息字串。
        """
        super().__init__(message)
        self.message = message


class MinioClient:
    """非同步 MinIO (S3 相容) 物件存儲客戶端。

    提供對 MinIO 所有常用操作的 Python 非同步封裝。
    使用 asyncio.to_thread() 將同步 SDK 操作委託給執行緒池，
    適配 FastAPI 的高併發非同步架構。

    所有連線資訊從 core.config.Settings 統一讀取：
    - MINIO_HOST: MinIO 伺服器主機位址
    - MINIO_PORT: MinIO 伺服器埠號
    - MINIO_ACCESS_KEY: 存取金鑰
    - MINIO_SECRET_KEY: 秘密金鑰
    - MINIO_SECURE: 是否使用 HTTPS

    Attributes:
        _client: MinIO SDK 客戶端實例。
        _endpoint: 組裝後的完整連線端點字串。

    Example:
        >>> client = MinioClient()
        >>> await client.ensure_bucket_exists("my-bucket")
        >>> result = await client.upload_file("my-bucket", "test.wav", "/tmp/test.wav")
        >>> print(result.object_name)
        'test.wav'
    """

    # MinIO 預簽名 URL 預設有效天數（伺服器端上限通常為 7 天）
    DEFAULT_PRESIGNED_EXPIRES_DAYS = 7

    def __init__(self) -> None:
        """根據 Settings 的設定初始化 MinIO 客戶端。

        從全域 Settings 讀取連線參數，建立 MinIO SDK 客戶端實例，
        並記錄初始化資訊（遮蔽敏感憑證）。

        Raises:
            MinioStorageError: MinIO 客戶端初始化失敗時。
        """
        self._endpoint = f"{settings.MINIO_HOST}:{settings.MINIO_PORT}"

        # 安全地遮蔽密碼（保留前後各 2 碼，隱藏中間部分），
        # 避免在日誌中洩露完整的 secret_key。
        secret_key = settings.MINIO_SECRET_KEY
        masked_secret = (
            f"{secret_key[:2]}****{secret_key[-2:]}"
            if len(secret_key) > 4
            else "****"
        )

        logger.info("正在初始化 MinIO 客戶端...")
        logger.info(
            "配置參數 -> Endpoint: %s, AccessKey: %s, SecretKey: %s, Secure: %s",
            self._endpoint,
            settings.MINIO_ACCESS_KEY,
            masked_secret,
            settings.MINIO_SECURE,
        )

        try:
            self._client = Minio(
                endpoint=self._endpoint,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
            )
            logger.info("MinIO 客戶端初始化完成。")
        except Exception as init_error:
            logger.error("MinIO 客戶端初始化失敗: %s", init_error)
            raise MinioStorageError(
                f"MinIO 客戶端初始化失敗: {init_error}"
            ) from init_error

    # ========================================================================
    # 儲存桶操作（Bucket Actions）
    # ========================================================================

    async def ensure_bucket_exists(self, bucket_name: str) -> None:
        """檢查指定的儲存桶是否存在，若不存在則自動建立。

        此方法是冪等的——重複呼叫不會產生錯誤或副作用。

        Args:
            bucket_name: 欲檢查或建立的儲存桶名稱。
                         命名規則需符合 S3 標準（小寫字母、數字、連字號）。

        Raises:
            MinioStorageError: 操作 Bucket 失敗時（例如權限不足）。

        Example:
            >>> await client.ensure_bucket_exists("anki-media")
        """

        def _sync_check() -> None:
            if not self._client.bucket_exists(bucket_name):
                self._client.make_bucket(bucket_name)
                logger.info("Bucket '%s' 建立成功。", bucket_name)
            else:
                logger.debug("Bucket '%s' 已存在。", bucket_name)

        try:
            await asyncio.to_thread(_sync_check)
        except S3Error as err:
            logger.error("檢查或建立 Bucket '%s' 失敗: %s", bucket_name, err)
            raise MinioStorageError(
                f"MinIO Bucket 操作失敗 ({bucket_name}): {err}"
            ) from err

    async def set_bucket_public_read(self, bucket_name: str) -> None:
        """透過 Policy 將指定的 Bucket 設置為永久公開唯讀。

        ⚠️ 安全警告：此操作會讓 Bucket 內所有物件可被任何人透過 URL 直接存取，
        不需要認證。僅建議在開發環境或明確需要公開存取的場景下使用。
        在生產環境中，應優先使用 presigned URL 提供有時效性的存取。

        Args:
            bucket_name: 目標儲存桶名稱。

        Raises:
            MinioStorageError: 設定策略失敗時。
        """
        # 使用 Pydantic 模型組裝 S3 標準的公開唯讀 Policy，
        # 確保 JSON 結構合法且可追蹤。
        policy = MinioBucketPolicy(
            Statement=[
                MinioBucketPolicyStatement(
                    Effect="Allow",
                    Principal={"AWS": ["*"]},
                    Action=["s3:GetBucketLocation", "s3:GetObject"],
                    Resource=[
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*",
                    ],
                )
            ]
        )

        def _sync_set_policy() -> None:
            self._client.set_bucket_policy(
                bucket_name,
                policy.model_dump_json(),
            )

        try:
            await asyncio.to_thread(_sync_set_policy)
            # 使用明確的日誌等級 WARNING 提醒此操作的安全風險
            logger.warning(
                "⚠️ Bucket '%s' 已設置為公開唯讀。"
                "請確認此操作的安全意圖。",
                bucket_name,
            )
        except Exception as err:
            logger.error("設置 Bucket '%s' 公開權限失敗: %s", bucket_name, err)
            raise MinioStorageError(
                f"設置公開權限失敗 ({bucket_name}): {err}"
            ) from err

    # ========================================================================
    # 檔案操作（Object Actions）
    # ========================================================================

    async def upload_file(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str,
    ) -> MinioUploadResult:
        """將本機檔案上傳至指定的儲存桶。

        使用 MinIO SDK 的 fput_object 方法進行上傳，
        並回傳結構化的上傳結果（Pydantic 模型）。

        Args:
            bucket_name: 目標儲存桶名稱。
            object_name: 上傳後在 MinIO 上的物件名稱
                         （可包含路徑前綴，例如 'voice/20240101/audio.wav'）。
            file_path: 本機端要上傳的檔案實體路徑。

        Returns:
            MinioUploadResult: 包含 bucket_name、object_name、file_size_bytes 的結構化結果。

        Raises:
            MinioStorageError: 檔案上傳失敗時。

        Example:
            >>> result = await client.upload_file("media", "audio/test.wav", "/tmp/test.wav")
            >>> print(result.object_name)
            'audio/test.wav'
        """

        def _sync_upload() -> int:
            """同步上傳並回傳檔案大小。"""
            result = self._client.fput_object(bucket_name, object_name, file_path)
            return result.size if hasattr(result, "size") else 0

        try:
            file_size = await asyncio.to_thread(_sync_upload)
            logger.info(
                "檔案 '%s' 成功上傳為 '%s/%s'（%d bytes）。",
                file_path,
                bucket_name,
                object_name,
                file_size,
            )
            return MinioUploadResult(
                bucket_name=bucket_name,
                object_name=object_name,
                file_size_bytes=file_size,
            )
        except S3Error as err:
            logger.error("上傳失敗 (%s -> %s/%s): %s", file_path, bucket_name, object_name, err)
            raise MinioStorageError(
                f"MinIO 上傳失敗 ({file_path} -> {bucket_name}/{object_name}): {err}"
            ) from err

    async def download_file(
        self,
        bucket_name: str,
        object_name: str,
        file_path: str,
    ) -> None:
        """自指定的儲存桶下載檔案至本機。

        使用 MinIO SDK 的 fget_object 方法進行下載，
        目標路徑的父目錄若不存在不會自動建立。

        Args:
            bucket_name: 來源儲存桶名稱。
            object_name: MinIO 上的物件名稱（含路徑前綴）。
            file_path: 下載至本機的目標路徑與檔名。

        Raises:
            MinioStorageError: 檔案下載失敗時。

        Example:
            >>> await client.download_file("media", "audio/test.wav", "/tmp/downloaded.wav")
        """

        def _sync_download() -> None:
            self._client.fget_object(bucket_name, object_name, file_path)

        try:
            await asyncio.to_thread(_sync_download)
            logger.info(
                "檔案 '%s/%s' 成功下載至 '%s'。",
                bucket_name,
                object_name,
                file_path,
            )
        except S3Error as err:
            logger.error(
                "下載失敗 (%s/%s -> %s): %s",
                bucket_name,
                object_name,
                file_path,
                err,
            )
            raise MinioStorageError(
                f"MinIO 下載失敗 ({bucket_name}/{object_name} -> {file_path}): {err}"
            ) from err

    async def delete_file(
        self,
        bucket_name: str,
        object_name: str,
    ) -> None:
        """刪除指定儲存桶內的目標檔案。

        此操作是冪等的——若物件不存在，不會拋出錯誤。

        Args:
            bucket_name: 目標儲存桶名稱。
            object_name: 欲刪除的物件名稱（含路徑前綴）。

        Raises:
            MinioStorageError: 刪除操作失敗時（例如權限不足）。

        Example:
            >>> await client.delete_file("media", "audio/old_file.wav")
        """

        def _sync_delete() -> None:
            self._client.remove_object(bucket_name, object_name)

        try:
            await asyncio.to_thread(_sync_delete)
            logger.info("檔案 '%s/%s' 已成功刪除。", bucket_name, object_name)
        except S3Error as err:
            logger.error(
                "刪除失敗 (%s/%s): %s", bucket_name, object_name, err
            )
            raise MinioStorageError(
                f"MinIO 刪除失敗 ({bucket_name}/{object_name}): {err}"
            ) from err

    async def list_objects(
        self,
        bucket_name: str,
        prefix: str | None = None,
        recursive: bool = True,
    ) -> list[MinioObjectInfo]:
        """列出指定儲存桶內的物件資訊清單。

        回傳結構化的 Pydantic 模型列表，而非裸字典或僅列印日誌。

        Args:
            bucket_name: 欲查詢的儲存桶名稱。
            prefix: 物件名稱前綴過濾（例如 'voice/20240101/'），
                    若為 None 則列出所有物件。
            recursive: 是否遞迴掃描子目錄，預設 True。

        Returns:
            MinioObjectInfo Pydantic 模型實例列表。

        Raises:
            MinioStorageError: 讀取檔案清單失敗時。

        Example:
            >>> objects = await client.list_objects("media", prefix="voice/")
            >>> for obj in objects:
            ...     print(f"{obj.object_name}: {obj.size_bytes} bytes")
        """

        def _sync_list() -> list[MinioObjectInfo]:
            objects = self._client.list_objects(
                bucket_name, prefix=prefix, recursive=recursive
            )
            result: list[MinioObjectInfo] = []
            for obj in objects:
                last_modified_str: str | None = None
                if obj.last_modified is not None:
                    last_modified_str = obj.last_modified.isoformat()

                result.append(
                    MinioObjectInfo(
                        object_name=obj.object_name or "",
                        size_bytes=obj.size or 0,
                        last_modified=last_modified_str,
                        content_type=obj.content_type,
                    )
                )
            return result

        try:
            objects = await asyncio.to_thread(_sync_list)
            logger.debug(
                "Bucket '%s' 列舉完成，共 %d 個物件（prefix=%s）。",
                bucket_name,
                len(objects),
                prefix or "無",
            )
            return objects
        except S3Error as err:
            logger.error(
                "列舉 Bucket '%s' 失敗: %s", bucket_name, err
            )
            raise MinioStorageError(
                f"MinIO 列舉失敗 ({bucket_name}): {err}"
            ) from err

    # ========================================================================
    # 預簽名 URL（Presigned URL）
    # ========================================================================

    async def get_presigned_url(
        self,
        bucket_name: str,
        object_name: str,
        expires_days: int = DEFAULT_PRESIGNED_EXPIRES_DAYS,
    ) -> str:
        """產生帶有時效性的預簽名下載網址 (Presigned URL)。

        適合用於私有 Bucket 分享臨時存取權限給外部系統（如 Telegram Bot
        回傳音檔連結、或前端直接下載媒體檔）。

        Args:
            bucket_name: 目標儲存桶名稱。
            object_name: 欲產生連結的物件名稱（含路徑前綴）。
            expires_days: URL 的有效天數，預設為 7 天。
                          MinIO 伺服器端上限通常為 7 天，超過會被拒絕。

        Returns:
            產生的預簽名 URL 字串。

        Raises:
            MinioStorageError: 產生 URL 失敗時。

        Example:
            >>> url = await client.get_presigned_url("media", "audio/test.wav", expires_days=3)
            >>> print(url)
            'http://127.0.0.1:9000/media/audio/test.wav?...'
        """

        def _sync_url() -> str:
            return self._client.presigned_get_object(
                bucket_name,
                object_name,
                expires=timedelta(days=expires_days),
            )

        try:
            url = await asyncio.to_thread(_sync_url)
            logger.info(
                "產生預簽名 URL 成功 (%s/%s，有效期限 %d 天)。",
                bucket_name,
                object_name,
                expires_days,
            )
            return url
        except S3Error as err:
            logger.error(
                "產生預簽名 URL 失敗 (%s/%s): %s",
                bucket_name,
                object_name,
                err,
            )
            raise MinioStorageError(
                f"MinIO 預簽名 URL 產生失敗 ({bucket_name}/{object_name}): {err}"
            ) from err
