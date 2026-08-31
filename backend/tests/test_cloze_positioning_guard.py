"""cloze 挖空定位的促音截斷防線測試(提示詞規則 6 的機械執行)。

Tests for the sokuon-truncation guard in cloze positioning (mechanical
enforcement of prompt rule 6).

背景:2026-08-31 百張全量審查發現「チャイムが鳴っちゃう」被挖成
「が鳴っ」——縮約形 ちゃう 被截斷,留下殘缺填空形。防線:任何
cloze_blank 以促音「っ/ッ」結尾即拋 ClozePositioningError,
觸發既有 fail-fast 重試,不讓殘形卡片落地。
"""

import pytest

import scripts.common.env  # noqa: F401

from app.services.task_handlers.shared.cloze_positioning import (
    ClozePositioningError,
    position_cloze,
)

COMMON_KWARGS = dict(
    task_name="TEST_Cloze",
    model_name="test-model",
    prompt_text="(test prompt)",
    raw_response="(test response)",
)


class TestSokuonGuard:
    def test_blank_ending_in_sokuon_rejected(self):
        """「が鳴っ」型截斷 → 拒絕建卡(實際事故重現)。"""
        with pytest.raises(ClozePositioningError) as ctx:
            position_cloze(
                "チャイムが鳴っちゃうから戻らないと",
                ["が鳴っ"],
                "が鳴っちゃう",
                **COMMON_KWARGS,
            )
        assert "促音" in str(ctx.value)

    def test_katakana_sokuon_also_rejected(self):
        with pytest.raises(ClozePositioningError):
            position_cloze(
                "ベルがナッちゃう",
                ["ナッ"],
                "ナッちゃう",
                **COMMON_KWARGS,
            )

    def test_teru_contraction_truncation_rejected(self):
        """「〜ってる」被截斷(後接て)同樣攔下。"""
        with pytest.raises(ClozePositioningError):
            position_cloze(
                "ずっと待ってるからね",
                ["待っ"],
                "待ってる",
                **COMMON_KWARGS,
            )

    def test_sentence_final_emphatic_sokuon_passes(self):
        """句尾強調促音「終わったっ」是合法日文,不得誤攔。"""
        cloze, _ = position_cloze(
            "やっと終わったっ",
            ["終わったっ"],
            "終わったっ",
            **COMMON_KWARGS,
        )
        assert "____" in cloze

    def test_interrupted_speech_sokuon_passes(self):
        """中斷語「鳴っ——」忠於原文挖到促音,不得誤攔。"""
        cloze, _ = position_cloze(
            "チャイムが鳴っ——",
            ["が鳴っ"],
            "が鳴っ",
            **COMMON_KWARGS,
        )
        assert "____" in cloze

    def test_full_contraction_blank_passes(self):
        """完整縮約形「が鳴っちゃう」 → 正常定位。"""
        cloze, full = position_cloze(
            "チャイムが鳴っちゃうから戻らないと",
            ["が鳴っちゃう"],
            "が鳴っちゃう",
            **COMMON_KWARGS,
        )
        assert "____" in cloze
        assert "が鳴っちゃう" not in cloze
        assert "<u" in full and "が鳴っちゃう" in full

    def test_sokuon_inside_blank_is_fine(self):
        """促音在片段中間(如って形)不受影響。"""
        cloze, _ = position_cloze(
            "ドアを開けて入った",
            ["入った"],
            "入った",
            **COMMON_KWARGS,
        )
        assert "____" in cloze
