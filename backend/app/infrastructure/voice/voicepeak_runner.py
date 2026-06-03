"""
非同步 VOICEPEAK CLI 語音合成客戶端模組。

本模組封裝與 VOICEPEAK 語音合成引擎 (CLI 版) 的所有交互操作，
透過 asyncio.create_subprocess_exec() 實現完全非同步的子程序管理。

重構自 old/VOICEPEAK/utils/voicepeak_runner.py，改進如下：
- 改用 asyncio.create_subprocess_exec() 實現原生非同步子程序呼叫。
- 遷移舊代碼全部 CLI 參數支援（emotion、speed、pitch、volume）。
- 保留並強化環境變數隔離機制（iconv 崩潰防禦）。
- 新增 Pydantic V2 入參驗證（VoicepeakSynthesisRequest）。
- 新增自訂異常 VoicepeakSynthesisError 統一錯誤處理。
- 補齊完整的 Google Style Docstring 與設計決策註釋。

設計決策：
- 環境變數隔離（clean_env）：VOICEPEAK 在某些系統上會因繼承到
  父程序的 LANG/LC_ALL 環境變數而觸發 iconv 相關的編碼崩潰。
  透過構建一個僅包含最小必要變數的乾淨環境，可以徹底根除此問題。
  此機制從舊代碼繼承，已被驗證為穩定的防禦手段。
- 使用 asyncio.create_subprocess_exec() 而非 subprocess.run()，
  是因為 VOICEPEAK 的語音合成可能耗時數秒到數十秒，
  同步阻塞會直接凍結 FastAPI 事件迴圈。
- narrator_id 直接接受 CLI 英文 ID，角色名稱到 ID 的映射
  應由上層 Service 或 CharacterManager 負責轉換。

Dependencies:
    - asyncio: 非同步子程序管理
    - pydantic: 入參資料驗證
"""

import asyncio
import logging
import os
import platform
import shlex

from app.core.config import settings
from app.schemas.voice import VoicepeakSynthesisRequest, VoicepeakSynthesisResult

logger = logging.getLogger(__name__)


class VoicepeakSynthesisError(Exception):
    """VOICEPEAK 語音合成操作錯誤異常類別。

    當 VOICEPEAK CLI 執行失敗、找不到執行檔、或合成程序非正常退出時
    拋出此異常。

    Attributes:
        message: 錯誤訊息字串。
        return_code: CLI 程序的退出碼（若可取得）。
    """

    def __init__(self, message: str, return_code: int | None = None) -> None:
        """初始化 VoicepeakSynthesisError。

        Args:
            message: 描述錯誤原因的訊息字串。
            return_code: VOICEPEAK CLI 的退出碼，若未執行則為 None。
        """
        super().__init__(message)
        self.message = message
        self.return_code = return_code


class VoicepeakRunner:
    """非同步 VOICEPEAK CLI 語音合成客戶端。

    封裝 VOICEPEAK 命令列工具的完整參數支援，包括角色選擇、
    情緒設定、語速/音高/音量調整，並透過環境變數隔離機制
    確保跨平台穩定性。

    所有 CLI 參數均接受英文 ID（而非日文顯示名稱），
    角色名稱到 CLI ID 的映射應由上層 CharacterManager 負責。

    Attributes:
        _executable_path: VOICEPEAK CLI 執行檔的絕對路徑。

    Example:
        >>> runner = VoicepeakRunner()
        >>> request = VoicepeakSynthesisRequest(
        ...     text="こんにちは",
        ...     output_path="/tmp/hello.wav",
        ...     narrator_id="Japanese Female 1",
        ...     emotions={"happy": 80},
        ...     speed=120,
        ... )
        >>> result = await runner.synthesize(request)
        >>> print(result.success)
        True
    """

    def __init__(
        self,
        executable_path: str | None = None,
    ) -> None:
        """初始化 VOICEPEAK 非同步語音合成客戶端。

        優先使用參數傳入的路徑，若未提供則從 Settings 讀取。

        Args:
            executable_path: VOICEPEAK CLI 執行檔路徑。
                             若為 None，從 Settings.VOICEPEAK_EXECUTABLE_PATH 讀取。

        Raises:
            VoicepeakSynthesisError: 執行檔路徑未提供且 Settings 中也未設定時。
        """
        self._executable_path = executable_path or settings.VOICEPEAK_EXECUTABLE_PATH

        if not self._executable_path:
            raise VoicepeakSynthesisError(
                "VOICEPEAK 執行檔路徑未設定。"
                "請設定環境變數 VOICEPEAK_EXECUTABLE_PATH 或在建構時傳入。"
            )

        # 檢查執行檔是否存在（僅記錄警告，不阻止初始化，
        # 因為某些部署環境可能在之後才掛載執行檔）
        if not os.path.exists(self._executable_path):
            logger.warning(
                "VOICEPEAK 執行檔路徑不存在: %s（將在首次合成時再確認）",
                self._executable_path,
            )

        logger.info(
            "VoicepeakRunner 初始化完成，執行檔路徑: %s",
            self._executable_path,
        )

    async def synthesize(
        self,
        request: VoicepeakSynthesisRequest,
    ) -> VoicepeakSynthesisResult:
        """執行 VOICEPEAK 語音合成。

        根據傳入的 Pydantic 模型參數組裝 CLI 命令，透過非同步子程序
        執行 VOICEPEAK，並在隔離的環境變數中運行以避免編碼問題。

        Args:
            request: VoicepeakSynthesisRequest Pydantic 模型實例，
                     包含所有合成所需參數。

        Returns:
            VoicepeakSynthesisResult: 包含合成結果的 Pydantic 模型。

        Raises:
            VoicepeakSynthesisError: VOICEPEAK CLI 執行失敗或非正常退出時。

        Example:
            >>> request = VoicepeakSynthesisRequest(
            ...     text="テスト",
            ...     output_path="/tmp/test.wav",
            ... )
            >>> result = await runner.synthesize(request)
        """
        command = self._build_command(request)

        # 以安全的 shlex.join/quote 格式記錄完整命令，方便除錯
        printable_cmd = " ".join(shlex.quote(arg) for arg in command)
        logger.info("執行 VOICEPEAK 指令: %s", printable_cmd)

        # 構建隔離的環境變數，防止 iconv 相關的編碼崩潰問題。
        # 詳見模組級 Docstring 的「設計決策」章節。
        clean_env = self._build_clean_env()

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_env,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error(
                    "VOICEPEAK 合成失敗 (Exit %d): %s",
                    process.returncode,
                    error_msg,
                )
                return VoicepeakSynthesisResult(
                    success=False,
                    output_path=request.output_path,
                    error_message=f"Exit {process.returncode}: {error_msg}",
                )

            logger.info(
                "VOICEPEAK 合成成功: %s",
                request.output_path,
            )
            return VoicepeakSynthesisResult(
                success=True,
                output_path=request.output_path,
            )

        except FileNotFoundError:
            error_msg = f"找不到 VOICEPEAK 執行檔: {self._executable_path}"
            logger.error(error_msg)
            raise VoicepeakSynthesisError(error_msg)
        except OSError as os_error:
            error_msg = f"VOICEPEAK 子程序啟動失敗: {os_error}"
            logger.error(error_msg)
            raise VoicepeakSynthesisError(error_msg) from os_error

    def _build_command(
        self,
        request: VoicepeakSynthesisRequest,
    ) -> list[str]:
        """根據合成請求組裝 VOICEPEAK CLI 命令列參數。

        將 Pydantic 模型中的各項參數轉換為 VOICEPEAK CLI 可接受的
        命令列引數格式。

        Args:
            request: VoicepeakSynthesisRequest 模型實例。

        Returns:
            完整的命令列引數字串列表。
        """
        command: list[str] = [
            self._executable_path,
            "-s", request.text,
            "-o", request.output_path,
        ]

        # 角色選擇（CLI 英文 ID）
        if request.narrator_id:
            command.extend(["-n", request.narrator_id])

        # 情緒參數：組裝為 "emotion1=value1,emotion2=value2" 格式
        if request.emotions:
            emotion_params = ",".join(
                f"{k}={v}" for k, v in request.emotions.items()
            )
            command.extend(["-e", emotion_params])

        # 語速、音高、音量調整
        if request.speed is not None:
            command.extend(["--speed", str(request.speed)])
        if request.pitch is not None:
            command.extend(["--pitch", str(request.pitch)])
        if request.volume is not None:
            command.extend(["--volume", str(request.volume)])

        return command

    @staticmethod
    def _build_clean_env() -> dict[str, str]:
        """構建用於 VOICEPEAK 子程序的隔離環境變數。

        VOICEPEAK 在某些系統上（特別是 macOS）會因繼承到父程序的
        環境變數而觸發 iconv 相關的編碼崩潰。此方法構建一個僅包含
        最小必要變數的乾淨環境，徹底隔離潛在的衝突。

        跨平台處理：
        - Unix/macOS: 設定 LANG/LC_ALL 為 ja_JP.UTF-8
        - Windows: 繼承 SystemRoot 與 TEMP 等必要路徑

        Returns:
            乾淨的環境變數字典。
        """
        if platform.system() == "Windows":
            # Windows 環境需要 SystemRoot 等系統變數才能正常運行子程序
            return {
                "PATH": os.environ.get("PATH", ""),
                "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
                "TEMP": os.environ.get("TEMP", r"C:\Windows\Temp"),
                "TMP": os.environ.get("TMP", r"C:\Windows\Temp"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
            }

        # Unix / macOS 環境：最小化環境變數，強制 UTF-8 編碼
        return {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": os.environ.get("HOME", "/tmp"),
            "USER": os.environ.get("USER", "unknown"),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            # 強制 ja_JP.UTF-8 編碼，避免 VOICEPEAK 的 iconv 崩潰。
            # 此設定已在舊版系統中驗證為穩定的防禦手段。
            "LANG": "ja_JP.UTF-8",
            "LC_ALL": "ja_JP.UTF-8",
        }
