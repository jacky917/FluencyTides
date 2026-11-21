"""Shared async FFmpeg client module.

非同步 FFmpeg 共用客戶端模組。

Wraps the low-level FFmpeg invocations, providing unified async execution, error handling and
pipe I/O. It supports file-path based processing (for example audio concatenation) as well as
fully in-memory pipe conversion (for example OGG to MP3). If video processing is added later, it
can use this module's core runner directly and avoid duplicated subprocess code.

封裝 FFmpeg 的底層呼叫，提供統一的非同步執行、錯誤處理與 I/O 管道操作。
支援基於檔案的路徑處理（例如音訊拼接），也支援全內存的 Pipe 轉換（例如 OGG 轉 MP3）。
未來若需擴充影片處理，可直接利用此模組的核心執行方法，避免出現重複的 subprocess 呼叫代碼。
"""

import asyncio
import logging
import shutil
from pathlib import Path

from app.schemas.voice import FfmpegMergeRequest, FfmpegMergeResult

logger = logging.getLogger(__name__)


class FfmpegError(Exception):
    """An FFmpeg execution error.

    FFmpeg 執行錯誤異常類別。

    Raised when FFmpeg fails (non-zero exit code) or cannot be started.

    當 FFmpeg 執行失敗（非零返回碼）或無法啟動時拋出。

    Attributes:
        message: the error message
            錯誤訊息字串
        return_code: FFmpeg's exit code, if available
            FFmpeg 的返回碼（若可取得）
        stderr_output: the captured standard error output, if available
            標準錯誤輸出內容（若可取得）
    """

    def __init__(
        self,
        message: str,
        return_code: int | None = None,
        stderr_output: str = "",
    ) -> None:
        """Initialize FfmpegError.

        初始化 FfmpegError。

        Args:
            message: the error message
                錯誤訊息字串
            return_code: FFmpeg's exit code; ``None`` when it is not available
                FFmpeg 的返回碼；無法取得時為 ``None``
            stderr_output: the captured standard error output
                標準錯誤輸出內容
        """
        super().__init__(message)
        self.message = message
        self.return_code = return_code
        self.stderr_output = stderr_output


class FfmpegTimeoutError(FfmpegError):
    """An error meaning FFmpeg ran past its time limit and was killed.

    FFmpeg 超過時間上限，已被中止。
    """


class FfmpegClient:
    """A unified async FFmpeg client.

    非同步 FFmpeg 統一客戶端。

    Wraps the underlying subprocess calls with clean argument passing and
    exception handling.

    封裝底層的 subprocess 呼叫，提供優雅的參數傳遞與例外處理機制。
    """

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        """Initialize the FFmpeg client.

        初始化 FFmpeg 客戶端。

        Args:
            ffmpeg_bin: the FFmpeg executable name or absolute path
                FFmpeg 可執行檔名稱或絕對路徑

        Raises:
            FileNotFoundError: if the given FFmpeg executable cannot be found on the system
                若系統中找不到指定的 FFmpeg 執行檔
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
        timeout: float | None = None,
    ) -> tuple[bytes, str]:
        """Run an FFmpeg command; the low-level core method.

        執行 FFmpeg 命令的底層核心方法。

        On timeout, or when the caller is cancelled (for example an interrupted HTTP request), the
        ffmpeg subprocess is killed, so no process is left behind using CPU. When there is nothing to
        feed to stdin, stdin is connected to ``DEVNULL``: by default ffmpeg reads stdin for
        interactive keys, and inheriting the development machine's terminal may swallow input or hang.

        逾時或呼叫端被取消（例如 HTTP 請求中斷）時會結束 ffmpeg 子程序，不留下繼續佔用 CPU 的程序。
        沒有要餵 stdin 時 stdin 接到 ``DEVNULL``：ffmpeg 預設會讀 stdin 等互動按鍵，繼承開發機的終端機
        可能吃掉輸入或卡住。

        Args:
            args: the argument list passed to FFmpeg (excluding the ffmpeg executable itself)
                傳遞給 FFmpeg 的參數列表（不含 ffmpeg 執行檔本身）
            input_bytes: the data to pass through stdin (pipe:0), if needed
                若需透過 stdin (pipe:0) 傳遞資料，可在此提供
            timeout: the longest run time in seconds; ``None`` means no limit
                最長執行秒數；``None`` 表示不限

        Returns:
            ``(stdout_bytes, stderr_string)``, the standard output and standard error
            ``(stdout_bytes, stderr_string)``，標準輸出與標準錯誤

        Raises:
            FfmpegError: if FFmpeg exits with a non-zero code or fails to start (``return_code`` is
                ``None`` when it fails to start)
                若 FFmpeg 返回非零碼或啟動失敗（啟動失敗時 ``return_code`` 為 ``None``）
            FfmpegTimeoutError: if ``timeout`` is exceeded (the subprocess has been ended)
                若超過 ``timeout``（子程序已結束）
        """
        command = [self._ffmpeg_bin] + args

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE if input_bytes else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(process.communicate(input=input_bytes), timeout)
            except (TimeoutError, asyncio.CancelledError) as exc:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                if isinstance(exc, asyncio.CancelledError):
                    raise
                error_msg = f"FFmpeg 執行超過 {timeout:g} 秒，已中止"
                logger.error(error_msg)
                raise FfmpegTimeoutError(message=error_msg) from exc
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
        """Convert audio bytes to MP3 entirely in memory.

        將音訊二進位資料全內存轉換為 MP3 格式。

        Uses pipes, so no temporary files are created. There is no time limit.

        使用 pipe 機制，不產生任何暫存檔案。不設時間上限。

        Args:
            audio_data: the source audio bytes (for example OGG/WAV)
                來源音訊的二進位資料 (如 OGG/WAV)

        Returns:
            the converted MP3 bytes
            轉換後的 MP3 二進位資料

        Raises:
            FfmpegError: if the conversion fails
                若轉檔失敗
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

    async def convert_file_to_mp3(self, source: Path, timeout: float | None = None) -> bytes:
        """Convert a media file on disk to MP3 (audio only), keeping the output in memory.

        把磁碟上的影音檔轉成 MP3（只取聲音），輸出留在記憶體。

        The input is a file rather than a pipe: m4a/mp4 often keep their index (moov) at the end of
        the file, and ffmpeg must be able to seek back to decode it, so reading from stdin fails.

        輸入用檔案而不是 pipe：m4a／mp4 的索引（moov）常放在檔尾，ffmpeg 要能往回讀才解得開，
        從 stdin 讀會失敗。

        Args:
            source: the source file path (m4a, webm and so on)
                來源檔案路徑（m4a、webm 等）
            timeout: the longest run time in seconds; ``None`` means no limit
                最長執行秒數；``None`` 表示不限

        Returns:
            the MP3 bytes
            MP3 的二進位資料

        Raises:
            FfmpegError: if the conversion fails (damaged file, no audio track and so on), or ffmpeg
                cannot start (``return_code`` is ``None``)
                若轉檔失敗（檔案損壞、沒有音軌等），或 ffmpeg 無法啟動（``return_code`` 為 ``None``）
            FfmpegTimeoutError: if ``timeout`` is exceeded
                若超過 ``timeout``
        """
        args = [
            "-y",
            "-i", str(source),
            "-vn",           # 不要影像（mp4 可能帶影片）
            "-f", "mp3",
            "pipe:1",
        ]
        stdout_data, _ = await self._run_command(args, timeout=timeout)
        logger.info("檔案轉 MP3 完成: %s → %d bytes", source.name, len(stdout_data))
        return stdout_data

    async def merge_with_silence(
        self,
        request: FfmpegMergeRequest,
    ) -> FfmpegMergeResult:
        """Concatenate several speech segments in order, inserting silence between adjacent ones.

        將多段語音依序拼接，並在相鄰語句間插入靜音。

        The output directory is created if missing. FFmpeg failures do not raise; they are reported
        in the result.

        輸出目錄不存在時會建立。FFmpeg 失敗不拋例外，而是記在結果中。

        Args:
            request: the merge request (input paths, output path and silence length)
                合併請求（輸入路徑、輸出路徑與靜音長度）

        Returns:
            the merge result, including the success flag, output path, segment count and, on failure,
            the error message
            合併結果，包含成功狀態、輸出路徑、片段數，失敗時另含錯誤訊息
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
        """Build the FFmpeg argument list needed for the concatenation.

        組裝拼接所需的參數列表。

        Every input and silence segment is converted to 44.1 kHz, 16-bit, mono before concatenation.

        每段輸入與靜音在拼接前都轉成 44.1 kHz、16 位元、單聲道。

        Args:
            input_paths: the input audio file paths
                輸入音檔路徑列表
            output_path: the output file path
                輸出檔案路徑
            silence_seconds: the silence between adjacent segments, in seconds
                相鄰語句間的靜音秒數

        Returns:
            the complete FFmpeg argument list
            完整的 FFmpeg 參數列表
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
