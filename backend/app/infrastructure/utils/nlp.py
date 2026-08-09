"""
全域共用的 NLP 處理工具模組。

Shared NLP processing utilities (Japanese lemmatization via Fugashi).
"""

import fugashi

class NLPProcessor:
    """提供全域共用的 NLP 處理與正規化服務。

    Provides globally shared NLP processing and normalization services.

    Attributes:
        _tagger: 延遲初始化的 Fugashi Tagger 單例。
            Lazily initialized Fugashi Tagger singleton.
    """

    _tagger = None

    @classmethod
    def get_tagger(cls) -> fugashi.Tagger:
        """取得單例的 Fugashi Tagger 實例。

        Get the singleton Fugashi Tagger instance.

        Returns:
            fugashi.Tagger: 共用的 Tagger 實例。The shared Tagger instance.
        """
        if cls._tagger is None:
            cls._tagger = fugashi.Tagger()
        return cls._tagger

    @classmethod
    def normalize_verb(cls, verb: str) -> str:
        """將動詞進行 NLP 正規化 (Lemmatization)。

        Normalize a verb to its lemma form via NLP.

        例如將 '見つかる' 轉換為 '見付かる' 以對齊資料庫中的 verb_lemma。

        For example, converts '見つかる' to '見付かる' to align with the
        verb_lemma values stored in the database.

        Args:
            verb: 原始動詞字串。The raw verb string.

        Returns:
            正規化後的動詞原型 (lemma)；找不到動詞特徵則原樣回傳。
            The normalized lemma; returns the input unchanged if no verb
            feature is found.
        """
        tagger = cls.get_tagger()
        for node in tagger(verb):
            # 檢查是否為動詞，並取得 feature[7] (lemma)
            if len(node.feature) > 7 and node.feature[0] == "動詞":
                return node.feature[7]
        return verb
