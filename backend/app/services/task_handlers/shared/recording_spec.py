"""What a handler declares about recording evaluation.

handler 對錄音評分的宣告。

A handler whose cards can be recorded and scored sets ``RECORDING_EVAL`` to a :class:`RecordingEvaluation`: the
route key that decides the strategy and models, and a function that tells, for the field a recording goes into,
the target language, the evaluation prompt, where the reference answers are, and the extra parameters of the
``add_recording`` action. The Telegram voice handler finds the card's handler by note type and reads this
declaration; it never guesses from field names (docs/wip/recording_eval_settings_FEAT_2026-11-26.md D4).

卡片能錄音評分的 handler 把 ``RECORDING_EVAL`` 設成 ``RecordingEvaluation``：決定評分策略與模型的
用途鍵，以及一個函式——依錄音要寫進的欄位，說出目標語言、評分提示詞、參考答案在哪個欄位，以及
``add_recording`` 動作要另外帶的參數。Telegram 的語音 handler 依筆記類型找到卡片的 handler，再讀這份宣告；
不再看欄位名稱猜（docs/wip/recording_eval_settings_FEAT_2026-11-26.md D4）。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.infrastructure.llm.capabilities import CAPABILITY_AUDIO_EVAL
from app.infrastructure.llm.routing import Route


@dataclass(frozen=True)
class RecordingTarget:
    """How to score a recording that goes into one field.

    一則要寫進某個欄位的錄音該怎麼評分。

    Attributes:
        template: the evaluation prompt template, for example ``prompts/anki/Speaking_Trilingual_JA.j2``
            評分提示詞的模板，例如 ``prompts/anki/Speaking_Trilingual_JA.j2``
        reference_field: the field holding the reference answers (a JSON list of ``{"content", "status", ...}``)
            存參考答案的欄位（``{"content", "status", ...}`` 的 JSON 串列）
        target_language: the target language locale such as ``ja-JP``; ``None`` when the card does not say
            目標語言的 locale，例如 ``ja-JP``；卡片沒有指定時為 ``None``
        action_params: extra parameters of the ``add_recording`` action besides ``recording``, for example
            ``{"lang": "JA"}``
            ``add_recording`` 動作除了 ``recording`` 之外要另外帶的參數，例如 ``{"lang": "JA"}``
    """

    template: str
    reference_field: str
    target_language: str | None = None
    action_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Check that the template and reference field are named and ``action_params`` leaves ``recording`` alone.

        檢查有指定模板與參考答案欄位，且 ``action_params`` 沒有佔用 ``recording``。

        Raises:
            ValueError: if the template or the reference field is empty, or ``action_params`` has ``recording``
                若模板或參考答案欄位是空的，或 ``action_params`` 含 ``recording``
        """
        if not self.template.strip() or not self.reference_field.strip():
            raise ValueError("錄音評分的模板與參考答案欄位都要指定")
        if "recording" in self.action_params:
            raise ValueError("action_params 不能含 recording：那是錄音紀錄本身")


#: Given the recording's field name and the note's fields (``{name: {"value": ...}}``), return the target, or
#: ``None`` when that field cannot be recorded.
#: 依錄音的欄位名與筆記欄位（``{名稱: {"value": ...}}``）回傳目標；該欄位不能錄音時回 ``None``。
TargetResolver = Callable[[str, Mapping[str, Any]], RecordingTarget | None]


@dataclass(frozen=True)
class RecordingEvaluation:
    """A handler's declaration that its cards can be recorded and scored.

    handler 宣告「它的卡片能錄音評分」。

    Attributes:
        route: the route key whose settings decide the strategy and models (declared with ``voice=True``)
            決定評分策略與模型的用途鍵（以 ``voice=True`` 宣告）
        target_for: tells how to score a recording for a field; see :data:`TargetResolver`
            說出某個欄位的錄音該怎麼評分；見 ``TargetResolver``
    """

    route: Route
    target_for: TargetResolver

    def __post_init__(self) -> None:
        """Check that the route is a voice use that declares a scoring strategy.

        檢查用途鍵是宣告了評分策略的語音用途。

        Raises:
            ValueError: if the route is not declared with ``voice=True`` or lacks ``audio_eval``
                若用途鍵沒有以 ``voice=True`` 宣告，或沒有 ``audio_eval``
        """
        if not self.route.voice or CAPABILITY_AUDIO_EVAL not in self.route.capabilities:
            raise ValueError(f"錄音評分的用途鍵 {self.route.key!r} 要以 voice=True 宣告，並含 audio_eval")
