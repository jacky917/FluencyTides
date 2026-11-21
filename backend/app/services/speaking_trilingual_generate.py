"""Web creation of trilingual speaking cards: ask the LLM for natural Chinese, Japanese, and English sayings.

三語對話卡的網頁建卡：請 LLM 為一個場景生成中、日、英的自然說法。

See plan ``docs/archive/trilingual_web_create_FEAT_2026-10-30.md``.

見計畫書 ``docs/archive/trilingual_web_create_FEAT_2026-10-30.md``。

The generation rule is "natural first, then score": each sentence is first written as what a native speaker would
really say, and its familiarity level is then rated from that sentence itself. The prompt is in
``prompts/anki/Speaking_Trilingual_Generate.j2`` and the output format is in ``app/schemas/llm/speaking_trilingual.py``.

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

#: The options of the audience dropdown; the first means unspecified (generate sayings for every audience).
#: 對象下拉選單的選項；第一個表示不指定（生成各種對象的說法）。
AUDIENCES: tuple[str, ...] = ("不指定", "長官或重要客戶", "初次見面或店員與客人", "同事", "朋友", "家人或伴侶")
ANY_AUDIENCE = AUDIENCES[0]


def target_audience(audience: str, audience_note: str) -> str | None:
    """Return the audience to generate for.

    回傳要生成的對象。

    Args:
        audience: the option chosen in the dropdown, one of ``AUDIENCES``
            下拉選單選的選項，為 ``AUDIENCES`` 之一
        audience_note: the free-text note; surrounding whitespace is ignored
            補充說明；前後空白會忽略

    Returns:
        the chosen audience with the note appended in parentheses, if an audience was chosen; the note alone if
        「不指定」 was chosen with a note; ``None`` (every audience) if neither is given
        下拉選了對象就用它（補充接在後面）；選「不指定」但有補充時以補充為準；都沒有為 ``None``（各種對象）
    """
    note = audience_note.strip()
    if audience == ANY_AUDIENCE:
        return note or None
    return f"{audience}（{note}）" if note else audience


def build_prompt(background: str, intent: str, audience: str | None, existing_tags: list[str] | None = None) -> str:
    """Render the generation prompt.

    組出提示詞。

    Args:
        background: the scene background
            場景背景
        intent: how to answer; an empty string lets the LLM come up with various positive and negative replies
            想怎麼答；空字串時由 LLM 想出各種正反回應
        audience: the audience; ``None`` means every audience
            對象；``None`` 表示各種對象
        existing_tags: topic tags already used on trilingual cards (sorted by count), so the LLM prefers reusing them
            when suggesting tags; ``None`` means none
            三語卡用過的主題標籤（依次數排序），讓 LLM 建議標籤時優先沿用；``None`` 表示沒有

    Returns:
        the rendered prompt
        組好的提示詞
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
    """Ask the LLM for a draft of trilingual sayings.

    呼叫 LLM 生成草稿。

    Args:
        background: the scene background
            場景背景
        intent: how to answer; may be empty
            想怎麼答；可為空字串
        audience: the audience; ``None`` means every audience
            對象；``None`` 表示各種對象
        existing_tags: topic tags already used on trilingual cards, sorted by count; ``None`` means none
            三語卡用過的主題標籤，依次數排序；``None`` 表示沒有

    Returns:
        the validated draft
        驗證過的草稿

    Raises:
        LLMGenerationError: if the LLM response does not match the format (the message names the model)
            若 LLM 回傳的格式不符（訊息帶模型名稱）
        LLMServiceError: if the LLM call fails (or a related error)
            若 LLM 呼叫失敗（或同類錯誤）
        TemplateEngineError: if the prompt template is broken (a code or deployment error)
            若提示詞模板有誤（程式或部署錯誤）
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
