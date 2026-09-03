"""日文同表層多讀動詞的讀音判讀端點（專案無關）。

Project-agnostic endpoint for judging the reading of a Japanese verb whose
kanji surface has multiple readings.

路徑帶 ``jp``：本服務同時承載 TOEIC/英語等模組，「同表層多讀」是日文特有
的問題，語言專屬能力不該看起來像通用的
（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.4）。
"""

from fastapi import APIRouter, HTTPException, status

from app.schemas.llm.jp_verb_reading import JudgeReadingsRequest, JudgeReadingsResponse
from app.services.jp_verb_reading_service import InvalidOverrideError, JpVerbReadingService

router = APIRouter(prefix="/jp/verb-readings", tags=["JP Verb Readings"])


@router.post("/judge", response_model=JudgeReadingsResponse, status_code=status.HTTP_200_OK)
async def judge_readings(request: JudgeReadingsRequest) -> JudgeReadingsResponse:
    """判讀一批台詞中多讀動詞的實際讀音。

    Judge the actual reading of a multi-reading verb in a batch of lines.

    Args:
        request: 待判句子（≤ 40）與可選的模型/深度覆寫。Items and optional
            model / effort overrides.

    Returns:
        JudgeReadingsResponse: 實際使用的模型標籤與逐句結果；無法判定為空字串。

    Raises:
        HTTPException: 422 —— 模型 / effort 覆寫值不合法（呼叫端可修正的
            輸入問題）；其餘 LLM 呼叫失敗交由全域例外處理。
    """
    try:
        return await JpVerbReadingService().judge(
            request.items, model=request.model, effort=request.effort,
        )
    except InvalidOverrideError as e:
        raise HTTPException(status_code=422, detail=str(e))
