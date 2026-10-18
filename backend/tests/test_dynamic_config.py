"""``/setconfig`` 可修改設定清單（``MODIFY_*``）的測試。

Tests for the ``MODIFY_*`` whitelist behind ``/setconfig``: LLM settings
are no longer listed (llm_model_routing_FEAT_2026-10-15.md D8).
"""

import logging
import os
from unittest import mock

from app.core import dynamic_config


def _configs(env: dict[str, str]):
    """只看給定的環境變數（不讀開發機的 .env）。"""
    with mock.patch.dict(os.environ, env, clear=True):
        return dynamic_config.get_modifiable_configs(env_path="no-such-file.env")


def test_llm_settings_are_not_listed_and_warned_once(caplog):
    """.env 還留著 MODIFY_LLM_*：不列出（LLM 模型改由 /models 管理），每個鍵只記一次 WARNING；
    名稱裡含 LLM 但不是 LLM_ 開頭的設定（STT_LLM_MODEL_NAME）照常列出。"""
    dynamic_config._warned_reserved.clear()
    env = {
        "MODIFY_LLM_MODEL_NAME": "['claude-opus-5', 'claude-haiku-4-5']",
        "MODIFY_LLM_PROVIDER": "",
        "MODIFY_STT_LLM_MODEL_NAME": "['a', 'b']",
        "MODIFY_AUDIO_EVALUATOR_PROVIDER": "",
    }
    with caplog.at_level(logging.WARNING, logger=dynamic_config.__name__):
        first = _configs(env)
        second = _configs(env)

    assert first == second == {"STT_LLM_MODEL_NAME": ["a", "b"], "AUDIO_EVALUATOR_PROVIDER": None}
    warnings = [record.getMessage() for record in caplog.records]
    assert len(warnings) == 2
    assert any("MODIFY_LLM_MODEL_NAME" in message and "/models" in message for message in warnings)
