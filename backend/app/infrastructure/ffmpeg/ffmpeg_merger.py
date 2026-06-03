"""
非同步 FFmpeg 音訊拼接工具模組。

本模組封裝 FFmpeg 的音訊拼接操作，將多個 WAV 音檔依照順序
串接，並在句與句之間插入固定秒數的靜音。
內部透過 filter_complex 與 concat 濾鏡完成拼接，
避免手動產生中繼 WAV 檔。

重構自 old/VOICEPEAK/utils/ffmpeg_merger.py，改進：
- 獨立為 infrastructure/ffmpeg 子套件，為未來擴充影片處理做準備。
- 改用 asyncio.create_subprocess_exec 實現完全非同步的子程序執行。
- 入參改用 Pydantic V2 Schema (FfmpegMergeRequest) 驗證。
- 回傳值改用 FfmpegMergeResult Pydantic 模型。
- 新增自訂異常類別 FfmpegMergeError。
- 保留舊代碼中完整的 filter_complex concat 拼接邏輯。

設計決策：
- filter_complex 的 concat 策略為什麼先統一 aformat：
  VOICEPEAK 輸出的 WAV 可能因角色不同而有不同的取樣率或聲道數，
  FFmpeg 的 concat 濾鏡要求所有輸入串流格式一致，否則會報錯。
  因此每段音訊先透過 aformat 強制統一為 44100Hz / s16 / mono，
  確保 concat 穩定執行。
- anullsrc 產生的靜音也需要做格式統一，確保與語音串流完全一致。

Dependencies:
    - asyncio: 非同步子程序管理
    - shutil: 檢查 FFmpeg 執行檔是否存在
"""

import asyncio
import logging
import shutil
from pathlib import Path

from app.schemas.voice import FfmpegMergeRequest, FfmpegMergeResult

logger = logging.getLogger(__name__)


class FfmpegMergeError(Exception):
    """FFmpeg 音訊合併錯誤異常類別。

    當 FFmpeg 執行失敗（非零返回碼）或無法啟動時拋出。

    Attributes:
        message: 錯誤訊息字串。
        return_code: FFmpeg 的返回碼（若可取得）。
        stderr_output: 標準錯誤輸出內容（若可取得）。
    """

    def __init__(
        self,
        message: str,
        return_code: int | None = None,
        stderr_output: str = "",
    ) -> None:
        """初始化 FfmpegMergeError。

        Args:
            message: 合併失敗的錯誤描述。
            return_code: FFmpeg 的返回碼。
            stderr_output: FFmpeg 的標準錯誤輸出。
        """
        super().__init__(message)
        self.message = message
        self.return_code = return_code
        self.stderr_output = stderr_output


class FfmpegMerger:
    """非同步 FFmpeg 音訊拼接工具。

    封裝 FFmpeg 的音訊拼接流程，支援多段 WAV 音檔串接，
    並在相鄰語句間自動插入指定秒數的靜音。

    Attributes:
        _ffmpeg_bin: FFmpeg 執行檔名稱或絕對路徑。

    Example:
        >>> merger = FfmpegMerger()
        >>> request = FfmpegMergeRequest(
        ...     input_paths=["/tmp/1.wav", "/tmp/2.wav"],
        ...     output_path="/tmp/merged.wav",
        ...     silence_seconds=0.5,
        ... )
        >>> result = await merger.merge_with_silence(request)
        >>> print(result.success)
        True
    """

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        """初始化 FFmpeg 拼接工具。

        Args:
            ffmpeg_bin: FFmpeg 可執行檔名稱或絕對路徑。

        Raises:
            FileNotFoundError: 系統中找不到指定的 FFmpeg 執行檔。
        """
        self._ffmpeg_bin = ffmpeg_bin
        if shutil.which(self._ffmpeg_bin) is None:
            raise FileNotFoundError(
                f"找不到 FFmpeg 執行檔: {self._ffmpeg_bin}"
            )
        logger.info("FfmpegMerger 已初始化，執行檔: %s", self._ffmpeg_bin)

    async def merge_with_silence(
        self,
        request: FfmpegMergeRequest,
    ) -> FfmpegMergeResult:
        """將多段語音依序拼接，並在相鄰語句間插入靜音。

        使用 FFmpeg 的 filter_complex 與 concat 濾鏡進行拼接，
        所有輸入音訊先透過 aformat 統一為 44100Hz / s16 / mono
        以確保 concat 穩定執行。

        Args:
            request: FfmpegMergeRequest Pydantic 模型實例，
                     包含輸入檔案清單、輸出路徑與靜音秒數。

        Returns:
            FfmpegMergeResult: 合併結果，包含成功狀態與輸出路徑。

        Raises:
            FfmpegMergeError: 當 FFmpeg 返回非零碼或無法啟動時。

        Example:
            >>> request = FfmpegMergeRequest(
            ...     input_paths=["a.wav", "b.wav", "c.wav"],
            ...     output_path="merged.wav",
            ...     silence_seconds=0.3,
            ... )
            >>> result = await merger.merge_with_silence(request)
        """
        input_paths = request.input_paths
        output_path = request.output_path
        silence_seconds = request.silence_seconds
        total_inputs = len(input_paths)

        # 確保輸出目錄存在
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 組裝 FFmpeg 命令
        command = self._build_command(
            input_paths, output_path, silence_seconds
        )

        logger.info(
            "開始 FFmpeg 音訊拼接: %d 個輸入 → %s (靜音 %.2f 秒)",
            total_inputs,
            output_path,
            silence_seconds,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            _stdout, stderr = await process.communicate()

            if process.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                error_msg = (
                    f"FFmpeg 音訊拼接失敗 (Exit {process.returncode}): "
                    f"{stderr_text}"
                )
                logger.error(error_msg)

                return FfmpegMergeResult(
                    output_path=output_path,
                    success=False,
                    segment_count=total_inputs,
                    error_message=error_msg,
                )

            logger.info("FFmpeg 音訊拼接成功: %s", output_path)
            return FfmpegMergeResult(
                output_path=output_path,
                success=True,
                segment_count=total_inputs,
            )

        except FileNotFoundError:
            error_msg = f"FFmpeg 執行檔不存在: {self._ffmpeg_bin}"
            logger.error(error_msg)
            raise FfmpegMergeError(error_msg)
        except OSError as err:
            error_msg = f"無法啟動 FFmpeg 子程序: {err}"
            logger.error(error_msg)
            raise FfmpegMergeError(error_msg) from err

    def _build_command(
        self,
        input_paths: list[str],
        output_path: str,
        silence_seconds: float,
    ) -> list[str]:
        """組裝完整的 FFmpeg 拼接命令。

        此方法保留舊代碼中完整的 filter_complex concat 邏輯：
        1. 每段語音輸入先做 aformat 統一格式
        2. 每兩段語音之間插入 anullsrc 靜音
        3. 用 concat 濾鏡將所有片段串接為一個輸出

        Args:
            input_paths: 需要拼接的音訊檔路徑清單。
            output_path: 最終輸出的音訊檔路徑。
            silence_seconds: 句間靜音秒數。

        Returns:
            組裝完成的命令列參數字串列表。
        """
        command: list[str] = [self._ffmpeg_bin, "-y"]

        # 步驟 1: 加入語音輸入，索引為 0..(n-1)
        for wav_path in input_paths:
            command.extend(["-i", wav_path])

        # 步驟 2: 為每個句間加入虛擬靜音輸入
        # 索引會接在語音索引後方
        silence_count = max(len(input_paths) - 1, 0)
        for _ in range(silence_count):
            command.extend([
                "-f", "lavfi",
                "-t", f"{silence_seconds}",
                "-i", "anullsrc=r=44100:cl=mono",
            ])

        # 步驟 3: 建構 filter_complex 字串
        filter_parts: list[str] = []
        concat_inputs: list[str] = []

        # 每段語音先透過 aformat 統一格式，避免 concat 因
        # 取樣率/聲道/位元深度不一致而報錯。
        for speech_index in range(len(input_paths)):
            label_name = f"s{speech_index}"
            filter_parts.append(
                f"[{speech_index}:a]aformat=sample_rates=44100"
                f":sample_fmts=s16:channel_layouts=mono[{label_name}]"
            )
            concat_inputs.append(f"[{label_name}]")

            # 在每段語音後插入靜音（最後一段不插入）
            if speech_index < len(input_paths) - 1:
                silence_input_index = len(input_paths) + speech_index
                silence_label = f"sil{speech_index}"
                # anullsrc 來源也做格式統一，確保 concat 穩定
                filter_parts.append(
                    f"[{silence_input_index}:a]aformat=sample_rates=44100"
                    f":sample_fmts=s16:channel_layouts=mono[{silence_label}]"
                )
                concat_inputs.append(f"[{silence_label}]")

        # concat 需要精確指定輸入段數 n = 語音段數 + 靜音段數
        concat_segment_count = len(concat_inputs)
        filter_parts.append(
            f"{''.join(concat_inputs)}"
            f"concat=n={concat_segment_count}:v=0:a=1[outa]"
        )
        filter_complex = ";".join(filter_parts)

        command.extend([
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            output_path,
        ])

        return command
