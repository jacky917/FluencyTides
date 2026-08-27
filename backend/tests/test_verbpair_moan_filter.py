"""純呻吟句過濾器（JP_VERB_PAIR_FILTER_MOAN_SENTENCES）的單元測試。

Unit tests for the pure-moan sentence filter.

測試素材取自 2026-08-27 dry-run 全量體檢的真實分類結果：
被判定為純呻吟的句子必須攔下；帶少量擬態記號的正常句必須放行。
"""

import scripts.common.env  # noqa: F401  # 載入 .env，供 app.core.config 使用

from scripts.fastapi_client.JP_VerbPair.generate_child_cards import _is_moan_sentence


class TestIsMoanSentence:
    def test_pure_moan_sentences_are_filtered(self):
        """體檢分類為「純呻吟級」的實句 → 攔下。Pure-moan lines are caught."""
        samples = [
            "「ほぐして、もっとほぐしてぇ……はっ、はっ、んっ、んん………ぁぁぁぁあぁあっ！」",
            "「あ、やめっ、んっ、にゃぁっ……はっ、はひっ、はひっ……あ、あ、あぁぁッッ、イくッ……」",
            "「んんぅぅぅぅぁぁぁぁあああぁぁっ、んはぁー……はっ、ああぁっ！　らめらめ……溶けちゃいますぅ……ッ」",
            "「あっ、またトロトロが零れちゃう……れろん、ちゅ、ちゅぅぅぅぅぅ……ちゅ、ちゅばちゅば」",
            "「ひゃんっ……あ、あ、あぁぁぁ……ヤダぁ、止まらない、止まりませんよぉ……あ、あ、はぁぁぁぁ……っ」",
        ]
        for s in samples:
            assert _is_moan_sentence(s), f"應攔下: {s[:25]}"

    def test_normal_sentences_pass(self):
        """一般句（含帶 ♪ 等單一記號的）→ 放行。Normal lines pass."""
        samples = [
            "「今度の日曜日なら、空いてるぞ？」",
            "作業内容自体は難しくないものの、結構時間がかかりそうだ。",
            "「本当だ、ケーキも美味！　いいねこのお店……本当に通っちゃいそうだよ、んんー♪」",
            "そう答えて、仮屋も更衣室に向かう。",
            "「あれ？　保科は残ってるのに、綾地さんはもう帰ったの？」",
            "私だって、気持ちがこもってます。私の心は、ちゃんとここに有りますよ。",
        ]
        for s in samples:
            assert not _is_moan_sentence(s), f"不應攔下: {s[:25]}"

    def test_empty_string_passes(self):
        assert not _is_moan_sentence("")
