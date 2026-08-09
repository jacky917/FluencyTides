"""
Speaking_Coach_Dark 相關的 LLM 結構化輸出 Schema。

LLM structured-output schemas for Speaking_Coach_Dark.
"""

from pydantic import BaseModel, Field

class AudioEvaluationResult(BaseModel):
    """Workflow B: 語音評分結果。

    Workflow B: audio evaluation result.

    LLM 型評分器透過 JSON Schema 強制輸出此結構；STT 型評分器
    （stt_diff）以程式直接建構。

    LLM-based evaluators are forced to output this structure via JSON
    Schema; STT-based evaluators (stt_diff) construct it directly.

    輸出契約（STT 計畫 §2.5）：`feedback` 一律為 Telegram HTML 安全
    標記（僅 <s>/<b> 等白名單標籤，內容已轉義）或純文字；
    `feedback_anki_html` 僅由 stt_diff 提供，含 <span style> 的完整
    HTML 差異，供寫回 Anki 卡片渲染。

    Output contract (STT plan §2.5): `feedback` is always Telegram-HTML
    safe markup (whitelisted tags such as <s>/<b> with escaped content)
    or plain text; `feedback_anki_html` is provided only by stt_diff and
    carries the full <span style> HTML diff for Anki card rendering.

    Attributes:
        score: 總分 (0-100)。Total score (0-100).
        feedback: AI 評語文字（TG 安全）。AI feedback text (TG-safe).
        transcript: 語音逐字稿。Speech transcript.
        feedback_anki_html: 寫回 Anki 用的紅綠標記差異 HTML；非 stt_diff
            模式為 None。Red/green diff HTML for Anki write-back; None
            outside stt_diff mode.
        evaluator_label: 本次評分使用的模式與模型標籤（如
            'stt_diff · faster-whisper-large-v3'），由各 evaluator 於回傳
            前自行填入，供 TG 結果訊息顯示。Label of the mode and model
            used for this evaluation (e.g. 'stt_diff ·
            faster-whisper-large-v3'), set by each evaluator before
            returning, shown in the TG result message.
    """

    score: int = Field(ge=0, le=100)
    feedback: str
    transcript: str = ""
    feedback_anki_html: str | None = None
    evaluator_label: str | None = None
