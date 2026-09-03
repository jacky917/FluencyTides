"""日文同表層多讀動詞的讀音判讀端點（專案無關）。

Project-agnostic endpoint for judging the reading of a Japanese verb whose
kanji surface has multiple readings.

路徑帶 ``jp``：本服務同時承載 TOEIC/英語等模組，「同表層多讀」是日文特有
的問題，語言專屬能力不該看起來像通用的
（docs/wip/verb_reading_judgments_FEAT_2026-09-02.md §3.4）。
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.core.exceptions.infrastructure import LLMServiceError
from app.schemas.llm.jp_verb_reading import JudgeReadingsRequest, JudgeReadingsResponse
from app.services.jp_verb_reading_service import InvalidModelOverrideError, JpVerbReadingService

logger = logging.getLogger(__name__)

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
        HTTPException: 422 —— 模型覆寫不在白名單、effort 非法等 client 建立
            失敗；其餘 LLM 呼叫失敗交由全域例外處理。
    """
    service = JpVerbReadingService()
    try:
        return await service.judge(request.items, model=request.model, effort=request.effort)
    except InvalidModelOverrideError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LLMServiceError as e:
        # client 建立階段的設定錯誤（effort 白名單、token 格式）屬呼叫端可修正的輸入問題
        if "effort" in str(e).lower() or "EFFORT" in str(e):
            raise HTTPException(status_code=422, detail=str(e))
        raise
