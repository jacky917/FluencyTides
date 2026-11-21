"""Route keys of recording evaluation.

錄音評分的用途鍵。

See the capability routing plan capability_routing_FEAT_2026-10-19.md §4.4 example 2 and §8.1.

見能力路由計畫 capability_routing_FEAT_2026-10-19.md §4.4 例 2、§8.1。

The flow key of recording evaluation is ``recording_eval``; a handler that needs settings different from the flow
defaults declares a branch ``recording_eval.<handler name>`` (the same as ``handler_name`` in ``handler_registry``).
At evaluation time the route key is chosen by the card's handler: the branch if one is declared, otherwise the flow
key; each capability then resolves upward on its own (branch → flow → global → .env).

錄音評分的流程鍵是 ``recording_eval``；需要和流程預設不同設定的 handler 另宣告分支
``recording_eval.<handler 名>``（與 ``handler_registry`` 的 ``handler_name`` 相同）。評分時依
卡片的 handler 找用途鍵：有宣告分支就用分支，沒有就用流程鍵；每個能力再各自往上找
（分支 → 流程 → 全域 → .env）。

At the ``.env`` level, ``llm`` defaults to the audio endpoint plus ``STT_LLM_MODEL_NAME`` (the same as ``stt_llm``
before capability routing), not the ``LLM_PROVIDER`` used for card creation.

``llm`` 在 ``.env`` 那一層的預設是 audio 端點加上 ``STT_LLM_MODEL_NAME``（與能力路由之前的
``stt_llm`` 相同），不跟著建卡用的 ``LLM_PROVIDER``。
"""

from app.infrastructure.audio_evaluator.base import BaseAudioEvaluator
from app.infrastructure.audio_evaluator.factory import EvaluatorPlan, evaluator_for
from app.infrastructure.llm import routing
from app.infrastructure.llm.capabilities import (
    CAPABILITY_AUDIO_EVAL,
    CAPABILITY_AUDIO_LLM,
    CAPABILITY_LLM,
    CAPABILITY_STT,
    audio_text_default,
)
from app.infrastructure.llm.routing import route

# 錄音評分會用到的能力：策略，以及各策略需要的模型與工具
RECORDING_CAPABILITIES = frozenset({CAPABILITY_AUDIO_EVAL, CAPABILITY_AUDIO_LLM, CAPABILITY_STT, CAPABILITY_LLM})

RECORDING_EVAL = route(
    "recording_eval",
    "錄音評分",
    capabilities=RECORDING_CAPABILITIES,
    env_defaults={CAPABILITY_LLM: audio_text_default},
)

RECORDING_EVAL_TRILINGUAL = route(
    "recording_eval.speaking_trilingual",
    "三語卡錄音評分",
    capabilities=RECORDING_CAPABILITIES,
)


def route_key_for_handler(handler_name: str) -> str:
    """Return the route key for a card's handler.

    回傳卡片的 handler 對應的用途鍵。

    Args:
        handler_name: the card's handler name, as in ``handler_registry``
            卡片的 handler 名稱，與 ``handler_registry`` 相同

    Returns:
        the branch ``recording_eval.<handler_name>`` if declared, otherwise the flow key ``recording_eval``
        有宣告分支 ``recording_eval.<handler_name>`` 就用分支，否則用流程鍵 ``recording_eval``
    """
    branch = f"{RECORDING_EVAL.key}.{handler_name}"
    return branch if routing.get_route(branch) is not None else RECORDING_EVAL.key


async def evaluator_for_handler(handler_name: str) -> tuple[BaseAudioEvaluator, EvaluatorPlan]:
    """Get the recording evaluator for a card's handler.

    依卡片的 handler 取得錄音評分器。

    Args:
        handler_name: the card's handler name
            卡片的 handler 名稱

    Returns:
        the evaluator and the plan it was built from
        評分器與建出它的計畫

    Raises:
        AudioEvaluatorUnavailableError: if no evaluator can be built from the current settings
            若依目前的設定建不出評分器
    """
    return await evaluator_for(route_key_for_handler(handler_name))
