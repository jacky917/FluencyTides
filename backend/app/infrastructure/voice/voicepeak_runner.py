"""Async VOICEPEAK CLI speech-synthesis client module.

非同步 VOICEPEAK CLI 語音合成客戶端模組。

This module wraps all interaction with the VOICEPEAK speech-synthesis engine (CLI edition),
using asyncio.create_subprocess_exec() for fully async subprocess management.

本模組封裝與 VOICEPEAK 語音合成引擎 (CLI 版) 的所有交互操作，
透過 asyncio.create_subprocess_exec() 實現完全非同步的子程序管理。

Refactored from old/VOICEPEAK/utils/voicepeak_runner.py, with these improvements:
- Uses asyncio.create_subprocess_exec() for native async subprocess calls.
- Carries over every CLI parameter the old code supported (emotion, speed, pitch, volume).
- Keeps and hardens the environment-variable isolation (the defense against iconv crashes).
- Adds Pydantic V2 input validation (VoicepeakSynthesisRequest).
- Adds the custom VoicepeakSynthesisError for unified error handling.
- Completes the Google Style docstrings and the design-decision comments.

重構自 old/VOICEPEAK/utils/voicepeak_runner.py，改進如下：
- 改用 asyncio.create_subprocess_exec() 實現原生非同步子程序呼叫。
- 遷移舊代碼全部 CLI 參數支援（emotion、speed、pitch、volume）。
- 保留並強化環境變數隔離機制（iconv 崩潰防禦）。
- 新增 Pydantic V2 入參驗證（VoicepeakSynthesisRequest）。
- 新增自訂異常 VoicepeakSynthesisError 統一錯誤處理。
- 補齊完整的 Google Style Docstring 與設計決策註釋。

Design decisions:
- Environment-variable isolation (clean_env): on some systems VOICEPEAK hits iconv-related
  encoding crashes because it inherits the parent process's LANG/LC_ALL variables. Building a
  clean environment with only the minimal required variables removes the problem entirely (on
  Unix/macOS LANG/LC_ALL are then set explicitly to ja_JP.UTF-8). This mechanism comes from the
  old code and has been verified as a stable defense.
- asyncio.create_subprocess_exec() is used instead of subprocess.run() because VOICEPEAK
  synthesis can take from several seconds to tens of seconds, and blocking synchronously would
  freeze the FastAPI event loop.
- narrator_id takes the CLI English ID directly; mapping character names to IDs is the job of
  the upper-layer Service or CharacterManager.

設計決策：
- 環境變數隔離（clean_env）：VOICEPEAK 在某些系統上會因繼承到
  父程序的 LANG/LC_ALL 環境變數而觸發 iconv 相關的編碼崩潰。
  透過構建一個僅包含最小必要變數的乾淨環境，可以徹底根除此問題
  （Unix/macOS 上會明確把 LANG/LC_ALL 設為 ja_JP.UTF-8）。
  此機制從舊代碼繼承，已被驗證為穩定的防禦手段。
- 使用 asyncio.create_subprocess_exec() 而非 subprocess.run()，
  是因為 VOICEPEAK 的語音合成可能耗時數秒到數十秒，
  同步阻塞會直接凍結 FastAPI 事件迴圈。
- narrator_id 直接接受 CLI 英文 ID，角色名稱到 ID 的映射
  應由上層 Service 或 CharacterManager 負責轉換。

Dependencies:
    - asyncio: async subprocess management
      非同步子程序管理
    - pydantic: input data validation
      入參資料驗證
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
    """An error in a VOICEPEAK speech-synthesis operation.

    VOICEPEAK 語音合成操作錯誤異常類別。

    Raised when the executable path is not configured, the executable cannot be found, or the
    synthesis subprocess cannot be started. A non-zero exit of the CLI is reported in the
    synthesis result instead.

    執行檔路徑未設定、找不到執行檔，或合成子程序無法啟動時拋出此異常。CLI 以非零碼結束時改記在
    合成結果中。

    Attributes:
        message: the error message
            錯誤訊息字串
        return_code: the CLI process's exit code, if available
            CLI 程序的退出碼（若可取得）
    """

    def __init__(self, message: str, return_code: int | None = None) -> None:
        """Initialize VoicepeakSynthesisError.

        初始化 VoicepeakSynthesisError。

        Args:
            message: the message describing the cause of the error
                描述錯誤原因的訊息字串
            return_code: the VOICEPEAK CLI exit code; ``None`` if it never ran
                VOICEPEAK CLI 的退出碼，若未執行則為 ``None``
        """
        super().__init__(message)
        self.message = message
        self.return_code = return_code


class VoicepeakRunner:
    """An async VOICEPEAK CLI speech-synthesis client.

    非同步 VOICEPEAK CLI 語音合成客戶端。

    Wraps full support for the VOICEPEAK command-line tool's parameters, including narrator
    selection, emotion settings and speed/pitch/volume adjustment, and uses environment-variable
    isolation to keep it stable across platforms.

    封裝 VOICEPEAK 命令列工具的完整參數支援，包括角色選擇、
    情緒設定、語速/音高/音量調整，並透過環境變數隔離機制
    確保跨平台穩定性。

    All CLI parameters take English IDs (not Japanese display names); mapping character names to
    CLI IDs is the job of the upper-layer CharacterManager.

    所有 CLI 參數均接受英文 ID（而非日文顯示名稱），
    角色名稱到 CLI ID 的映射應由上層 CharacterManager 負責。

    Attributes:
        _executable_path: the absolute path to the VOICEPEAK CLI executable
            VOICEPEAK CLI 執行檔的絕對路徑

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
        """Initialize the async VOICEPEAK speech-synthesis client.

        初始化 VOICEPEAK 非同步語音合成客戶端。

        The path passed as an argument takes priority; if none is given it is read from Settings. A
        path that does not exist only logs a warning, because some deployments mount the executable
        later.

        優先使用參數傳入的路徑，若未提供則從 Settings 讀取。路徑不存在時只記錄警告，因為某些部署
        環境可能在之後才掛載執行檔。

        Args:
            executable_path: the VOICEPEAK CLI executable path; when ``None``, it is read from
                ``Settings.VOICEPEAK_EXECUTABLE_PATH``
                VOICEPEAK CLI 執行檔路徑；若為 ``None``，從 ``Settings.VOICEPEAK_EXECUTABLE_PATH`` 讀取

        Raises:
            VoicepeakSynthesisError: if no executable path is given and none is set in Settings
                若執行檔路徑未提供且 Settings 中也未設定
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
        """Run VOICEPEAK speech synthesis.

        執行 VOICEPEAK 語音合成。

        Builds the CLI command from the parameters of the given Pydantic model, and runs VOICEPEAK
        in an async subprocess with isolated environment variables to avoid encoding problems.

        根據傳入的 Pydantic 模型參數組裝 CLI 命令，透過非同步子程序
        執行 VOICEPEAK，並在隔離的環境變數中運行以避免編碼問題。

        Args:
            request: the request model containing every parameter the synthesis needs
                VoicepeakSynthesisRequest Pydantic 模型實例，包含所有合成所需參數

        Returns:
            the synthesis result; when the CLI exits with a non-zero code, ``success`` is ``False``
            and ``error_message`` holds the exit code and stderr
            合成結果；CLI 以非零碼結束時 ``success`` 為 ``False``，``error_message`` 含退出碼與 stderr

        Raises:
            VoicepeakSynthesisError: if the VOICEPEAK executable cannot be found or the subprocess
                cannot be started
                若找不到 VOICEPEAK 執行檔或子程序無法啟動

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
        """Build the VOICEPEAK CLI command-line arguments from the synthesis request.

        根據合成請求組裝 VOICEPEAK CLI 命令列參數。

        Converts each parameter of the Pydantic model into the command-line argument format the
        VOICEPEAK CLI accepts; optional parameters that are not set are left out.

        將 Pydantic 模型中的各項參數轉換為 VOICEPEAK CLI 可接受的
        命令列引數格式；未設定的選用參數不加入。

        Args:
            request: the synthesis request
                VoicepeakSynthesisRequest 模型實例

        Returns:
            the complete list of command-line argument strings, starting with the executable path
            完整的命令列引數字串列表，第一個為執行檔路徑
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
        """Build the isolated environment variables for the VOICEPEAK subprocess.

        構建用於 VOICEPEAK 子程序的隔離環境變數。

        On some systems (notably macOS) VOICEPEAK hits iconv-related encoding crashes because it
        inherits the parent process's environment variables. This method builds a clean environment
        with only the minimal required variables, fully isolating potential conflicts.

        VOICEPEAK 在某些系統上（特別是 macOS）會因繼承到父程序的
        環境變數而觸發 iconv 相關的編碼崩潰。此方法構建一個僅包含
        最小必要變數的乾淨環境，徹底隔離潛在的衝突。

        Cross-platform handling:
        - Unix/macOS: sets LANG/LC_ALL to ja_JP.UTF-8.
        - Windows: inherits required paths such as SystemRoot and TEMP.

        跨平台處理：
        - Unix/macOS: 設定 LANG/LC_ALL 為 ja_JP.UTF-8
        - Windows: 繼承 SystemRoot 與 TEMP 等必要路徑

        Returns:
            the clean environment-variable dict
            乾淨的環境變數字典
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
