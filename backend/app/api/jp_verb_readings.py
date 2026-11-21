"""Project-agnostic endpoint for judging the reading of a Japanese verb whose kanji surface has multiple readings.

日文同表層多讀動詞的讀音判讀端點（專案無關）。

The path includes ``jp``: this service also hosts TOEIC/English and other modules, and "one surface,
several readings" is a Japanese-specific problem; a language-specific capability should not look
generic (docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.4).

The model is decided by the route key ``jp_verb_reading`` (Telegram ``/setconfig``); requests cannot
override it.

路徑帶 ``jp``：本服務同時承載 TOEIC/英語等模組，「同表層多讀」是日文特有
的問題，語言專屬能力不該看起來像通用的
（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.4）。

使用的模型由用途鍵 ``jp_verb_reading`` 決定（TG ``/setconfig``），請求不能覆寫。
"""

from fastapi import APIRouter, status

from app.schemas.llm.jp_verb_reading import JudgeReadingsRequest, JudgeReadingsResponse
from app.services.jp_verb_reading_service import JpVerbReadingService

router = APIRouter(prefix="/jp/verb-readings", tags=["JP Verb Readings"])


@router.post("/judge", response_model=JudgeReadingsResponse, status_code=status.HTTP_200_OK)
async def judge_readings(request: JudgeReadingsRequest) -> JudgeReadingsResponse:
    """Judge the actual reading of a multi-reading verb in a batch of lines.

    判讀一批台詞中多讀動詞的實際讀音。

    Args:
        request: the sentences to judge (at most 40). Extra fields such as the removed ``model`` /
            ``effort`` make FastAPI return 422.
            待判句子（≤ 40）。帶已移除的 ``model`` / ``effort`` 等多餘欄位時，由 FastAPI 回 422。

    Returns:
        the label of the model actually used and the per-sentence results; an empty string when a
        reading cannot be determined
        實際使用的模型標籤與逐句結果；無法判定為空字串

    Raises:
        LLMServiceError: if building the client or calling the LLM fails; left to the global exception
            handling
            若 client 建立或 LLM 呼叫失敗，交由全域例外處理
    """
    return await JpVerbReadingService().judge(request.items)
