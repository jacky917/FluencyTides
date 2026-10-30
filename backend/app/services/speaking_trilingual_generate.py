"""三語對話卡的網頁建卡：請 LLM 為一個場景生成中、日、英的自然說法（計畫書
``docs/wip/trilingual_web_create_FEAT_2026-10-30.md``）。

Generate natural ZH/JA/EN sayings for a scene (trilingual speaking card web create).

生成準則是「先自然，再打分」：每句先寫出母語者真的會說的話，再依這句話本身評熟識度；提示詞在
``prompts/anki/Speaking_Trilingual_Generate.j2``，輸出格式在 ``app/schemas/llm/speaking_trilingual.py``。
"""

import logging

from pydantic import ValidationError

from app.core.dependencies import get_template_engine
from app.core.exceptions import LLMGenerationError
from app.infrastructure.llm.routing import route
from app.schemas.llm.speaking_trilingual import LLMTrilingualDraft

logger = logging.getLogger(__name__)

GENERATE_ROUTE = route("speaking_trilingual", "三語對話卡：生成說法", capabilities={"llm"})

#: 對象下拉選單的選項；第一個表示不指定（生成各種對象的說法）。The audience options.
AUDIENCES: tuple[str, ...] = ("不指定", "長官或重要客戶", "初次見面或店員與客人", "同事", "朋友", "家人或伴侶")
ANY_AUDIENCE = AUDIENCES[0]


def target_audience(audience: str, audience_note: str) -> str | None:
    """要生成的對象：下拉選了對象就用它（補充接在後面）；選「不指定」但有補充時以補充為準；都沒有為 None。

    The audience to write for, or None for every audience.
    """
    note = audience_note.strip()
    if audience == ANY_AUDIENCE:
        return note or None
    return f"{audience}（{note}）" if note else audience


def build_prompt(background: str, intent: str, audience: str | None, existing_tags: list[str] | None = None) -> str:
    """組出提示詞。Render the prompt.

    Args:
        background: 場景背景。The scene.
        intent: 想怎麼答；空字串時由 LLM 想出各種正反回應。What to say, or empty.
        audience: 對象；None 表示各種對象。The audience, or None.
        existing_tags: 三語卡用過的主題標籤（依次數排序），讓 LLM 建議標籤時優先沿用。Existing tags.
    """
    return get_template_engine().render(
        "prompts/anki/Speaking_Trilingual_Generate.j2",
        background=background.strip(),
        intent=intent.strip(),
        audience=audience,
        existing_tags=existing_tags or [],
    )


async def generate(
    background: str, intent: str, audience: str | None, existing_tags: list[str] | None = None
) -> LLMTrilingualDraft:
    """呼叫 LLM 生成草稿。Ask the LLM for a draft.

    Raises:
        LLMServiceError 等：LLM 呼叫失敗。When the LLM call fails.
        LLMGenerationError: LLM 回傳的格式不符（訊息帶模型名稱）。On a malformed response.
        TemplateEngineError: 提示詞模板有誤（程式或部署錯誤）。When the prompt template is broken.
    """
    client = await GENERATE_ROUTE.client()
    result = await client.generate_structured_data(
        system_prompt=(
            "You are a native-level conversation coach in Taiwanese Mandarin, Japanese and English. "
            "You only write what native speakers really say. Output strict JSON."
        ),
        user_prompt=build_prompt(background, intent, audience, existing_tags),
        response_schema=LLMTrilingualDraft.model_json_schema(),
    )
    try:
        return LLMTrilingualDraft.model_validate(result.parsed_data)
    except ValidationError as e:
        # 調整提示詞時要看哪個模型、哪種輸出常出錯：記下前幾個錯誤與原始輸出的開頭
        logger.warning(
            "三語對話卡生成：%s 回傳的格式不對：%s；原始輸出：%.500s", result.model_name, e.errors()[:3], result.raw_content
        )
        raise LLMGenerationError(f"模型 {result.model_name} 回傳的格式不對，請再試一次。") from e
