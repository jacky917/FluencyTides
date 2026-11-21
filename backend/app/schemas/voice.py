"""Pydantic V2 schemas for speech synthesis and audio processing.

語音合成與音訊處理相關 Pydantic V2 Schema 定義模組。

Defines all data structures needed to interact with the VOICEPEAK speech
synthesis engine and FFmpeg audio processing, covering synthesis
requests/results and audio merge requests/results.

本模組定義了與 VOICEPEAK 語音合成引擎以及 FFmpeg 音訊處理
交互所需的所有資料結構，涵蓋合成請求/結果、音訊合併請求/結果。

All cross-boundary data structures are strongly validated with Pydantic V2;
raw dicts and ``typing.Any`` are strictly forbidden.

所有跨邊界交互的資料結構均透過 Pydantic V2 進行強型別驗證，
嚴禁使用裸字典 (raw dict) 或 ``typing.Any``。

Design decisions:
    - The ``emotions`` field is ``dict[str, int]`` rather than a custom Enum,
      because VOICEPEAK's emotion parameter names depend on the character
      config file and not every valid value can be enumerated at compile time.
    - ``speed``/``pitch``/``volume`` are restricted to 0~200 with ``Field``,
      matching the valid VOICEPEAK CLI parameter ranges.

設計決策：
    - ``emotions`` 欄位使用 ``dict[str, int]`` 而非自定義 Enum，
      是因為 VOICEPEAK 的情緒參數名稱取決於角色設定檔，
      無法在編譯期窮舉所有合法值。
    - ``speed``/``pitch``/``volume`` 使用 ``Field`` 限制 0~200 範圍，
      對齊 VOICEPEAK CLI 的合法參數範圍。
"""


from pydantic import BaseModel, Field


# ============================================================================
# VOICEPEAK 語音合成 Schema
# ============================================================================

class VoicepeakSynthesisRequest(BaseModel):
    """The request parameters for VOICEPEAK speech synthesis.

    VOICEPEAK 語音合成請求參數結構。

    Wraps all parameters needed to call the VOICEPEAK CLI. Pydantic validation
    makes sure the values are within the valid ranges, so errors are not
    reported only when the CLI runs. ``narrator_id`` looks like
    ``'Japanese Female 1'``.

    封裝呼叫 VOICEPEAK CLI 所需的所有參數，透過 Pydantic 驗證
    確保參數值在合法範圍內，避免 CLI 執行時才報錯。``narrator_id`` 例如 ``'Japanese Female 1'``。
    """

    text: str = Field(
        ...,
        min_length=1,
        description="要合成的文字內容，不可為空",
    )
    output_path: str = Field(
        ...,
        min_length=1,
        description="輸出 WAV 檔案的絕對路徑",
    )
    narrator_id: str | None = Field(
        default=None,
        description="VOICEPEAK CLI 角色 ID（英文）",
    )
    emotions: dict[str, int] | None = Field(
        default=None,
        description="情緒參數字典，鍵為情緒 CLI ID，值為強度 (0-100)",
    )
    speed: int | None = Field(
        default=None,
        ge=0,
        le=200,
        description="語速調整 (0-200)，100 為正常速度",
    )
    pitch: int | None = Field(
        default=None,
        ge=0,
        le=200,
        description="音高調整 (0-200)，100 為正常音高",
    )
    volume: int | None = Field(
        default=None,
        ge=0,
        le=200,
        description="音量調整 (0-200)，100 為正常音量",
    )


class VoicepeakSynthesisResult(BaseModel):
    """The result of VOICEPEAK speech synthesis.

    VOICEPEAK 語音合成結果結構。

    Wraps the information returned after the synthesis completes.

    封裝合成操作完成後的回傳資訊。

    Attributes:
        success: whether the synthesis succeeded
            合成是否成功
        output_path: the path of the WAV file actually written
            實際輸出的 WAV 檔案路徑
    """

    success: bool
    output_path: str
    error_message: str | None = Field(
        default=None,
        description="失敗時的錯誤訊息",
    )


# ============================================================================
# FFmpeg 音訊合併 Schema
# ============================================================================

class FfmpegMergeRequest(BaseModel):
    """The request parameters for an FFmpeg audio merge.

    FFmpeg 音訊合併請求參數結構。

    Wraps all parameters needed to concatenate several WAV files. The order of
    ``input_paths`` is the output order, and ``silence_seconds`` of silence is
    inserted between every two segments.

    封裝多段 WAV 音檔拼接所需的所有參數。``input_paths`` 的順序即輸出順序，
    每兩段音訊之間插入 ``silence_seconds`` 秒的靜音。
    """

    input_paths: list[str] = Field(
        ...,
        min_length=1,
        description="需要拼接的音檔路徑清單，至少一個",
    )
    output_path: str = Field(
        ...,
        min_length=1,
        description="最終輸出的 WAV 檔路徑",
    )
    silence_seconds: float = Field(
        default=0.5,
        ge=0.0,
        description="句間靜音秒數，預設 0.5 秒",
    )


class FfmpegMergeResult(BaseModel):
    """The result of an FFmpeg audio merge.

    FFmpeg 音訊合併結果結構。

    Wraps the information returned after the merge completes.

    封裝合併操作完成後的回傳資訊。

    Attributes:
        success: whether the merge succeeded
            合併是否成功
        output_path: the path of the merged file actually written
            實際輸出的合併後檔案路徑
    """

    success: bool
    output_path: str
    segment_count: int = Field(
        default=0,
        ge=0,
        description="拼接的音訊段數（含靜音段）",
    )
    error_message: str | None = Field(
        default=None,
        description="失敗時的錯誤訊息",
    )
