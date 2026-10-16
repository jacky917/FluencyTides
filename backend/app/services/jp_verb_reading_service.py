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


class InvalidOverrideError(ValueError):
    """模型 / effort 覆寫值不合法（呼叫端可修正的輸入問題）。

    Invalid model / effort override — a caller-correctable input error.
    """


def validate_model_override(model: str | None) -> None:
    """模型覆寫值須在 ``MODIFY_LLM_MODEL_NAME`` 白名單內（未設白名單則不限）。

    Validate a model override against the LLM_MODEL_NAME whitelist when one
    is configured; unrestricted when no whitelist exists.

    Args:
        model: 覆寫值；None 表示沿用設定。Override value, or None.

    Raises:
        InvalidOverrideError: 值不在白名單內。When outside the whitelist.
    """
    if model is None:
        return
    options = get_modifiable_configs().get("LLM_MODEL_NAME")
    if options and model not in options:
        raise InvalidOverrideError(
            f"model '{model}' 不在可用清單內。可選：{', '.join(options)}"
            "（清單來自後端 .env 的 MODIFY_LLM_MODEL_NAME；provider 為 claude-code / antigravity 時請在其中加入對應模型名，antigravity 用基底名）"
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
            model: 覆寫模型。與 effort 都是 None 時走用途鍵 ``jp_verb_reading``
                的路由設定；只要帶了其中一個，就改用 ``.env`` 的 provider 加覆寫值，
                不看路由設定（P3 移除覆寫）。Model override.
            effort: 覆寫思考深度；規則同上。Effort override.

        Returns:
            JudgeReadingsResponse: 實際模型標籤與逐句結果。

        Raises:
            InvalidOverrideError: 模型覆寫不在白名單，或 effort 覆寫非法 /
                provider 不支援（client 建立階段被拒）。
            LLMServiceError: 未帶覆寫時的 client 建立失敗，或 LLM 呼叫失敗。
        """
        validate_model_override(model)
        # 每次請求建立自己的 client（與生成 handler 相同的既有模式）。未帶覆寫
        # 時走用途鍵 jp_verb_reading 的路由設定；帶覆寫時沿用舊行為（.env 的
        # provider + 覆寫值）。TODO(P3): 移除覆寫——目前帶覆寫時的「任何」建立失敗都報
        # 成 422，連金鑰未設、找不到 CLI、端點設定有誤這類伺服器問題也會被說成覆寫無效
        # （導入前即如此，覆寫移除後自然消失）。帶覆寫時的建立失敗（effort 不在白名單、
        # provider 不支援 effort）是呼叫端的輸入問題，轉成 InvalidOverrideError
        # 讓端點回 422；未帶覆寫的失敗則是伺服器設定問題，原樣拋出。
        if model is None and effort is None:
            llm_client = await JUDGE_LLM.client()
        else:
            try:
                llm_client = create_llm_client(model=model, effort=effort)
            except LLMServiceError as e:
                raise InvalidOverrideError(str(e)) from e

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
