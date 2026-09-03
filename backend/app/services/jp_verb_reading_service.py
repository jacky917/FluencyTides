"""日文同表層多讀動詞的讀音判讀服務。

Reading-judgment service for Japanese verbs whose kanji surface has
multiple readings.

渲染 ``JP_VerbReading_Judge.j2``、以（可覆寫模型/深度的）請求範圍 LLM client
呼叫，並把輸出收斂成安全的結果：讀音不在該句候選內、或缺漏的句子，一律視為
「無法判定」（空字串），寧缺勿錯。
Renders the judge template, calls a request-scoped LLM client (model /
effort overridable), and normalizes the output: readings outside the
item's candidates, or missing items, become "" (undetermined).

對應計畫 docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.2。
"""

import logging

from app.core.dependencies import get_template_engine
from app.core.dynamic_config import get_modifiable_configs
from app.core.exceptions.infrastructure import LLMServiceError
from app.infrastructure.llm.factory import create_llm_client
from app.schemas.llm.jp_verb_reading import (
    JudgeReadingItem,
    JudgeReadingsResponse,
    ReadingJudgment,
    ReadingJudgmentLLMOutput,
)

logger = logging.getLogger(__name__)

TEMPLATE_PATH = "prompts/anki/JP_VerbReading_Judge.j2"
SYSTEM_PROMPT = (
    "你是精通日語語音與語用的專家，任務是依語境判定多讀漢字動詞的實際讀音。"
    "只能從給定的候選讀音中選擇；無法確定時回空字串，絕不猜測。"
    "請嚴格依指定的 JSON 格式輸出。"
)


class InvalidModelOverrideError(ValueError):
    """模型覆寫值不在白名單內。Model override outside the whitelist."""


def validate_model_override(model: str | None) -> None:
    """模型覆寫值須在 ``MODIFY_LLM_MODEL_NAME`` 白名單內（未設白名單則不限）。

    Validate a model override against the LLM_MODEL_NAME whitelist when one
    is configured; unrestricted when no whitelist exists.

    Args:
        model: 覆寫值；None 表示沿用設定。Override value, or None.

    Raises:
        InvalidModelOverrideError: 值不在白名單內。When outside the whitelist.
    """
    if model is None:
        return
    options = get_modifiable_configs().get("LLM_MODEL_NAME")
    if options and model not in options:
        raise InvalidModelOverrideError(
            f"model '{model}' 不在可用清單內。可選：{', '.join(options)}"
            "（清單來自後端 .env 的 MODIFY_LLM_MODEL_NAME；provider 為 claude-code 時請在其中加入 claude 模型名）"
        )


def normalize_results(
    items: list[JudgeReadingItem], raw: ReadingJudgmentLLMOutput
) -> list[ReadingJudgment]:
    """把 LLM 輸出對齊到請求的每一句，並套用 fail-closed 規則。

    Align LLM output to the request items and apply fail-closed rules:
    a reading not among the item's candidates, or an item missing from the
    output, becomes "".

    Args:
        items: 請求的句子。Request items.
        raw: LLM 結構化輸出。Parsed LLM output.

    Returns:
        list[ReadingJudgment]: 與 items 同序、同長度。Same order/length as items.
    """
    by_id: dict[int, str] = {}
    for r in raw.results:
        by_id.setdefault(r.script_id, (r.reading or "").strip())

    out: list[ReadingJudgment] = []
    for item in items:
        reading = by_id.get(item.script_id)
        if reading is None:
            logger.warning("判讀輸出缺少 script_id=%s，視為無法判定", item.script_id)
            reading = ""
        elif reading and reading not in item.candidates:
            logger.warning(
                "判讀輸出 '%s' 不在候選 %s 內（script_id=%s），視為無法判定",
                reading, item.candidates, item.script_id,
            )
            reading = ""
        out.append(ReadingJudgment(script_id=item.script_id, reading=reading))
    return out


class JpVerbReadingService:
    """讀音判讀服務。Reading-judgment service."""

    async def judge(
        self,
        items: list[JudgeReadingItem],
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> JudgeReadingsResponse:
        """判讀一批台詞。Judge one batch of lines.

        Args:
            items: 待判句子（≤ 40）。Items to judge.
            model: 覆寫模型；None 沿用設定。Model override.
            effort: 覆寫思考深度；None 沿用設定。Effort override.

        Returns:
            JudgeReadingsResponse: 實際模型標籤與逐句結果。

        Raises:
            InvalidModelOverrideError: 模型覆寫不在白名單。
            LLMServiceError: client 建立失敗（如 effort 非法）或呼叫失敗。
        """
        validate_model_override(model)
        # 每次請求建立自己的 client（與生成 handler 相同的既有模式）；有覆寫
        # 時不動 app.state.llm_client、不寫回設定。
        llm_client = create_llm_client(model=model, effort=effort)

        prompt_text = get_template_engine().render(TEMPLATE_PATH, items=items)
        raw = await llm_client.generate_structured_data(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt_text,
            response_schema=ReadingJudgmentLLMOutput.model_json_schema(),
        )
        parsed = ReadingJudgmentLLMOutput.model_validate(raw.parsed_data)
        return JudgeReadingsResponse(
            llm_model=raw.model_name,
            results=normalize_results(items, parsed),
        )


__all__ = [
    "InvalidModelOverrideError",
    "JpVerbReadingService",
    "LLMServiceError",
    "normalize_results",
    "validate_model_override",
]
