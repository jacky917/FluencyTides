"""Anki 媒體延遲上傳模組。

Deferred Anki media uploader: parses the API's kept_dialog, reads local
audio/avatar files, and uploads them via AnkiConnect as Base64 with retry.

負責解析 API 回傳的 kept_dialog，讀取本地的實體語音與圖檔，
轉換為 Base64 並透過 AnkiConnect 執行延遲上傳，內建重試機制。
"""

import base64
import logging
from pathlib import Path
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class AnkiMediaUploader:
    """Anki 媒體延遲上傳器（音檔與頭像）。

    Deferred uploader for audio and avatar media into Anki.
    """

    def __init__(self, anki_client, voice_dir: Path, avatar_dir: Path, source_game: str):
        """初始化上傳器。

        Initialize the uploader.

        Args:
            anki_client: AnkiConnect 客戶端。AnkiConnect client.
            voice_dir: 音檔本地目錄。Local voice-file directory.
            avatar_dir: 頭像本地目錄。Local avatar directory.
            source_game: 檔名前綴用的遊戲代號。Game id used as filename prefix.
        """
        self.anki_client = anki_client
        self.voice_dir = voice_dir
        self.avatar_dir = avatar_dir
        self.source_game = source_game

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=lambda retry_state: logger.warning(
            f"⚠️ AnkiConnect 媒體上傳失敗，準備進行第 {retry_state.attempt_number} 次重試... "
            f"原因: {retry_state.outcome.exception()}"
        )
    )
    async def _upload_single_file(self, filename: str, data_b64: str) -> None:
        """實際呼叫 AnkiConnect 的方法，具有重試保護。

        Actual AnkiConnect call with retry protection.

        Args:
            filename: Anki 端目標檔名。Target filename in Anki.
            data_b64: Base64 編碼的檔案內容。Base64-encoded file content.
        """
        await self.anki_client._invoke("storeMediaFile", filename=filename, data=data_b64)

    async def upload_media(self, kept_dialog: list[dict]) -> None:
        """處理 kept_dialog，將用到的音檔與頭像上傳至 Anki。

        Walk kept_dialog and upload every referenced audio file and avatar
        to Anki (each avatar only once).

        Args:
            kept_dialog: API 回傳的保留對話清單。Kept dialog turns from the
                API response.
        """
        logger.info(f"⏳ 開始執行 Anki 媒體延遲上傳 (共 {len(kept_dialog)} 筆保留對話)...")
        
        uploaded_avatars: set[str] = set()
        
        for idx, turn in enumerate(kept_dialog):
            speaker = turn.get("speaker", "?")
            audio_filename = turn.get("audio", "")
            avatar_filename = turn.get("avatar", "none")
            logger.info(f"   [{idx}] speaker={speaker}, audio='{audio_filename}', avatar='{avatar_filename}'")
            
            # 1. 上傳音檔
            if audio_filename:
                # 假設傳來的檔名是帶有前綴的，還原成本地實際檔名
                original_audio_name = audio_filename.replace(f"{self.source_game}_", "")
                audio_path = self.voice_dir / original_audio_name
                logger.debug(f"       🔍 音檔本地路徑: {audio_path} (存在: {audio_path.exists()})")
                
                if audio_path.exists():
                    try:
                        with open(audio_path, "rb") as f:
                            b64_data = base64.b64encode(f.read()).decode("utf-8")
                        await self._upload_single_file(audio_filename, b64_data)
                        logger.info(f"       ✔️ 成功上傳音檔: {audio_filename}")
                    except Exception as e:
                        logger.error(f"       ❌ 無法上傳音檔 {audio_filename}: {e}")
                else:
                    logger.warning(f"       ⏭️ 音檔實體不存在，跳過: {audio_path}")
            
            # 2. 上傳頭像 (同一角色只上傳一次)
            if avatar_filename and avatar_filename != "none" and avatar_filename not in uploaded_avatars:
                original_avatar_name = avatar_filename.replace(f"{self.source_game}_", "")
                avatar_path = self.avatar_dir / original_avatar_name
                logger.debug(f"       🔍 頭像本地路徑: {avatar_path} (存在: {avatar_path.exists()})")
                
                if avatar_path.exists():
                    try:
                        with open(avatar_path, "rb") as f:
                            b64_data = base64.b64encode(f.read()).decode("utf-8")
                        await self._upload_single_file(avatar_filename, b64_data)
                        uploaded_avatars.add(avatar_filename)
                        logger.info(f"       ✔️ 成功上傳頭像: {avatar_filename}")
                    except Exception as e:
                        logger.error(f"       ❌ 無法上傳頭像 {avatar_filename}: {e}")
                else:
                    logger.warning(f"       ⏭️ 頭像實體不存在，跳過: {avatar_path}")
                    
        logger.info(f"✅ 媒體延遲上傳完成 (共上傳了 {len(uploaded_avatars)} 種頭像)。")
