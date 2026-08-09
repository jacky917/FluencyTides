"""
非同步 FFmpeg 共用客戶端模組。

Shared async FFmpeg client module.

封裝 FFmpeg 的底層呼叫，提供統一的非同步執行、錯誤處理與 I/O 管道操作。
支援基於檔案的路徑處理（例如音訊拼接），也支援全內存的 Pipe 轉換（例如 OGG 轉 MP3）。
未來若需擴充影片處理，可直接利用此模組的核心執行方法，避免出現重複的 subprocess 呼叫代碼。

Wraps low-level FFmpeg invocations with unified async execution, error
handling, and pipe I/O. Supports file-path based processing (e.g. audio
concatenation) as well as fully in-memory pipe conversion (e.g. OGG to MP3).
Future video features can reuse the core runner to avoid duplicated
subprocess code.
"""

import asyncio
import logging
import shutil
from pathlib import Path

from app.schemas.voice import FfmpegMergeRequest, FfmpegMergeResult

logger = logging.getLogger(__name__)


class FfmpegError(Exception):
    """FFmpeg 執行錯誤異常類別。

    Exception raised for FFmpeg execution errors.

    當 FFmpeg 執行失敗（非零返回碼）或無法啟動時拋出。

    Raised when FFmpeg fails (non-zero exit code) or cannot be started.

    Attributes:
        message: 錯誤訊息字串。The error message string.
        return_code: FFmpeg 的返回碼（若可取得）。FFmpeg's exit code, if
            available.
        stderr_output: 標準錯誤輸出內容（若可取得）。Captured stderr output,
            if available.
    """

    def __init__(
        self,
        message: str,
        return_code: int | None = None,
        stderr_output: str = "",
    ) -> None:
        """初始化 FfmpegError。

        Initialize FfmpegError.

        Args:
            message: 錯誤訊息字串。The error message string.
            return_code: FFmpeg 的返回碼。FFmpeg's exit code.
            stderr_output: 標準錯誤輸出內容。Captured stderr output.
        """
        super().__init__(message)
        self.message = message
        self.return_code = return_code
        self.stderr_output = stderr_output


class FfmpegClient:
    """非同步 FFmpeg 統一客戶端。

    Unified async FFmpeg client.

    封裝底層的 subprocess 呼叫，提供優雅的參數傳遞與例外處理機制。

    Wraps the underlying subprocess calls with clean argument passing and
    exception handling.
    """

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        """初始化 FFmpeg 客戶端。

        Initialize the FFmpeg client.

        Args:
            ffmpeg_bin: FFmpeg 可執行檔名稱或絕對路徑。FFmpeg executable
                name or absolute path.

        Raises:
            FileNotFoundError: 系統中找不到指定的 FFmpeg 執行檔。Raised when
                the FFmpeg executable cannot be found on the system.
        """
        self._ffmpeg_bin = ffmpeg_bin
        if shutil.which(self._ffmpeg_bin) is None:
            raise FileNotFoundError(
                f"找不到 FFmpeg 執行檔: {self._ffmpeg_bin}"
            )
        logger.info("FfmpegClient 已初始化，執行檔: %s", self._ffmpeg_bin)

    async def _run_command(
        self,
        args: list[str],
        input_bytes: bytes | None = None,
    ) -> tuple[bytes, str]:
        """執行 FFmpeg 命令的底層核心方法。

        Low-level core method that runs an FFmpeg command.

        Args:
            args: 傳遞給 FFmpeg 的參數列表（不含 ffmpeg 執行檔本身）。
                Argument list passed to FFmpeg (excluding the executable
                itself).
            input_bytes: 若需透過 stdin (pipe:0) 傳遞資料，可在此提供。
                Optional bytes to feed via stdin (pipe:0).

        Returns:
            (stdout_bytes, stderr_string): 標準輸出與標準錯誤。The captured
            stdout bytes and stderr string.

        Raises:
            FfmpegError: 當 FFmpeg 返回非零碼或啟動失敗時。Raised when
                FFmpeg exits non-zero or fails to start.
        """
        command = [self._ffmpeg_bin] + args
        
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if input_bytes else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_data, stderr_data = await process.communicate(input=input_bytes)
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            if process.returncode != 0:
                error_msg = f"FFmpeg 執行失敗 (Exit {process.returncode}): {stderr_text}"
                logger.error(error_msg)
                raise FfmpegError(
                    message=error_msg,
                    return_code=process.returncode,
                    stderr_output=stderr_text,
                )

            return stdout_data, stderr_text

        except OSError as err:
            error_msg = f"無法啟動 FFmpeg 子程序: {err}"
            logger.error(error_msg)
            raise FfmpegError(message=error_msg) from err

    async def convert_to_mp3(self, audio_data: bytes) -> bytes:
        """將音訊二進位資料全內存轉換為 MP3 格式。

        Convert audio bytes to MP3 entirely in memory.

        使用 pipe 機制，不產生任何暫存檔案。

        Uses pipes, so no temporary files are created.

        Args:
            audio_data: 來源音訊的二進位資料 (如 OGG/WAV)。Source audio
                bytes (e.g. OGG/WAV).

        Returns:
            轉換後的 MP3 二進位資料。The converted MP3 bytes.

        Raises:
            FfmpegError: 轉檔失敗時拋出。Raised when conversion fails.
        """
        logger.info("開始透過 FFmpeg Pipe 進行音訊 MP3 轉檔 (大小: %d bytes)", len(audio_data))
        
        args = [
            "-y",
            "-i", "pipe:0",  # 從 stdin 讀取
            "-f", "mp3",     # 強制輸出格式為 mp3
            "pipe:1",        # 寫入至 stdout
        ]
        
        stdout_data, _ = await self._run_command(args, input_bytes=audio_data)
        logger.info("音訊 MP3 轉檔完成，輸出大小: %d bytes", len(stdout_data))
        
        return stdout_data

    async def merge_with_silence(
        self,
        request: FfmpegMergeRequest,
    ) -> FfmpegMergeResult:
        """將多段語音依序拼接，並在相鄰語句間插入靜音。

        Concatenate multiple audio segments in order, inserting silence
        between adjacent segments.

        Args:
            request: FfmpegMergeRequest Pydantic 模型實例。The
                FfmpegMergeRequest model instance.

        Returns:
            FfmpegMergeResult: 合併結果，包含成功狀態與輸出路徑。The merge
                result, including success flag and output path.
        """
        input_paths = request.input_paths
        output_path = request.output_path
        silence_seconds = request.silence_seconds
        total_inputs = len(input_paths)

        # 確保輸出目錄存在
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        args = self._build_merge_args(input_paths, output_path, silence_seconds)

        logger.info(
            "開始 FFmpeg 音訊拼接: %d 個輸入 → %s (靜音 %.2f 秒)",
            total_inputs,
            output_path,
            silence_seconds,
        )

        try:
            await self._run_command(args)
            logger.info("FFmpeg 音訊拼接成功: %s", output_path)
            return FfmpegMergeResult(
                output_path=output_path,
                success=True,
                segment_count=total_inputs,
            )
        except FfmpegError as e:
            return FfmpegMergeResult(
                output_path=output_path,
                success=False,
                segment_count=total_inputs,
                error_message=e.message,
            )

    def _build_merge_args(
        self,
        input_paths: list[str],
        output_path: str,
        silence_seconds: float,
    ) -> list[str]:
        """組裝拼接所需的參數列表。

        Build the FFmpeg argument list for the concatenation job.

        Args:
            input_paths: 輸入音檔路徑列表。Input audio file paths.
            output_path: 輸出檔案路徑。Output file path.
            silence_seconds: 相鄰語句間的靜音秒數。Silence duration between
                adjacent segments, in seconds.

        Returns:
            完整的 FFmpeg 參數列表。The complete FFmpeg argument list.
        """
        args: list[str] = ["-y"]

        for wav_path in input_paths:
            args.extend(["-i", wav_path])

        silence_count = max(len(input_paths) - 1, 0)
        for _ in range(silence_count):
            args.extend([
                "-f", "lavfi",
                "-t", f"{silence_seconds}",
                "-i", "anullsrc=r=44100:cl=mono",
            ])

        filter_parts: list[str] = []
        concat_inputs: list[str] = []

        for speech_index in range(len(input_paths)):
            label_name = f"s{speech_index}"
            filter_parts.append(
                f"[{speech_index}:a]aformat=sample_rates=44100"
                f":sample_fmts=s16:channel_layouts=mono[{label_name}]"
            )
            concat_inputs.append(f"[{label_name}]")

            if speech_index < len(input_paths) - 1:
                silence_input_index = len(input_paths) + speech_index
                silence_label = f"sil{speech_index}"
                filter_parts.append(
                    f"[{silence_input_index}:a]aformat=sample_rates=44100"
                    f":sample_fmts=s16:channel_layouts=mono[{silence_label}]"
                )
                concat_inputs.append(f"[{silence_label}]")

        concat_segment_count = len(concat_inputs)
        filter_parts.append(
            f"{''.join(concat_inputs)}"
            f"concat=n={concat_segment_count}:v=0:a=1[outa]"
        )
        filter_complex = ";".join(filter_parts)

        args.extend([
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            output_path,
        ])

        return args
