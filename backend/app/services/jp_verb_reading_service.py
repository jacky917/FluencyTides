"""日文同表層多讀動詞的讀音判讀服務。

Reading-judgment service for Japanese verbs whose kanji surface has
multiple readings.

渲染 ``JP_VerbReading_Judge.j2``、以用途鍵 ``jp_verb_reading`` 生效的 LLM client
呼叫，並把輸出收斂成安全的結果：讀音不在該句候選內、或缺漏的句子，一律視為
「無法判定」（空字串），寧缺勿錯。要換模型請用 TG ``/models`` 設定這個用途
（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D8：請求不再能覆寫模型 / effort）。
Renders the judge template, calls the client of the ``jp_verb_reading``
route, and normalizes the output: readings outside the item's
candidates, or missing items, become "" (undetermined).

對應計畫 docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.2。
"""

import logging

from app.core.dependencies import get_template_engine
from app.infrastructure.llm.routing import llm_route
from app.schemas.llm.jp_verb_reading import (
    JudgeReadingItem,
    JudgeReadingsResponse,
    ReadingJudgment,
    ReadingJudgmentLLMOutput,
)

logger = logging.getLogger(__name__)

# LLM 用途鍵（生效設定的解析見 app.infrastructure.llm.routing）
JUDGE_LLM = llm_route("jp_verb_reading", "讀音判斷")

TEMPLATE_PATH = "prompts/anki/JP_VerbReading_Judge.j2"
SYSTEM_PROMPT = (
    "你是精通日語語音與語用的專家，任務是依語境判定多讀漢字動詞的實際讀音。"
    "只能從給定的候選讀音中選擇；無法確定時回空字串，絕不猜測。"
    "請嚴格依指定的 JSON 格式輸出。"
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

    async def judge(self, items: list[JudgeReadingItem]) -> JudgeReadingsResponse:
        """判讀一批台詞。Judge one batch of lines.

        Args:
            items: 待判句子（≤ 40）。Items to judge.

        Returns:
            JudgeReadingsResponse: 實際模型標籤與逐句結果。

        Raises:
            LLMServiceError: client 建立失敗（伺服器設定問題），或 LLM 呼叫失敗。
        """
        # 每次請求建立自己的 client（與生成 handler 相同的既有模式）
        llm_client = await JUDGE_LLM.client()

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
