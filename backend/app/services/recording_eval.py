"""Route keys of recording evaluation.

錄音評分的用途鍵。

Every card type that can be recorded declares its own route key ``recording_eval.<handler name>`` (the same as
``handler_name`` in ``handler_registry``) as a voice use. Each capability resolves in three layers: the key itself →
the voice global ``*voice`` → ``.env``; while the voice global's switch is on, every card type uses the voice
global as a whole (docs/wip/recording_eval_settings_FEAT_2026-11-26.md D1–D3). There is no shared flow layer
``recording_eval`` any more.

每個能錄音的卡片類型宣告自己的用途鍵 ``recording_eval.<handler 名>``（與 ``handler_registry`` 的
``handler_name`` 相同），並標為語音用途。每個能力依三層解析：用途鍵本身 → 語音全域 ``*voice`` →
``.env``；語音全域的開關開著時，所有卡片類型整組用語音全域（docs/wip/recording_eval_settings_FEAT_2026-11-26.md
D1–D3）。不再有共用的流程層 ``recording_eval``。

At the ``.env`` level, ``llm`` defaults to the audio endpoint plus ``STT_LLM_MODEL_NAME`` (the same as ``stt_llm``
before capability routing), not the ``LLM_PROVIDER`` used for card creation; the routing layer applies this to
every voice use.

``llm`` 在 ``.env`` 那一層的預設是 audio 端點加上 ``STT_LLM_MODEL_NAME``（與能力路由之前的
``stt_llm`` 相同），不跟著建卡用的 ``LLM_PROVIDER``；路由層對所有語音用途都這樣處理。

A handler uses its key through its ``RECORDING_EVAL`` declaration (``task_handlers.shared.recording_spec``).

handler 透過自己的 ``RECORDING_EVAL`` 宣告使用這些鍵（``task_handlers.shared.recording_spec``）。
"""

from app.infrastructure.llm.capabilities import (
    CAPABILITY_AUDIO_EVAL,
    CAPABILITY_AUDIO_LLM,
    CAPABILITY_LLM,
    CAPABILITY_STT,
)
from app.infrastructure.llm.routing import route

# 錄音評分會用到的能力：策略，以及各策略需要的模型與工具
RECORDING_CAPABILITIES = frozenset({CAPABILITY_AUDIO_EVAL, CAPABILITY_AUDIO_LLM, CAPABILITY_STT, CAPABILITY_LLM})

# 用途鍵的前綴；完整的鍵是 recording_eval.<handler 名>
ROUTE_KEY_PREFIX = "recording_eval"

RECORDING_EVAL_TRILINGUAL = route(
    f"{ROUTE_KEY_PREFIX}.speaking_trilingual",
    "三語卡錄音評分",
    capabilities=RECORDING_CAPABILITIES,
    voice=True,
)

RECORDING_EVAL_COACH = route(
    f"{ROUTE_KEY_PREFIX}.speaking_coach",
    "對話卡錄音評分",
    capabilities=RECORDING_CAPABILITIES,
    voice=True,
)

