"""Shared NLP processing utilities (Japanese lemmatization via Fugashi).

全域共用的 NLP 處理工具模組（以 Fugashi 進行日文詞形還原）。
"""

import fugashi

from app.infrastructure.utils.jp_tokenizer import create_tagger

class NLPProcessor:
    """Globally shared NLP processing and normalization services.

    提供全域共用的 NLP 處理與正規化服務。

    Attributes:
        _tagger: the lazily initialized Fugashi Tagger singleton
            延遲初始化的 Fugashi Tagger 單例
    """

    _tagger = None

    @classmethod
    def get_tagger(cls) -> fugashi.Tagger:
        """Return the singleton Fugashi Tagger instance, creating it on first use.

        取得單例的 Fugashi Tagger 實例，首次呼叫時建立。

        Returns:
            the shared Tagger instance
            共用的 Tagger 實例
        """
        if cls._tagger is None:
            # 詞典明確指定，不讓 fugashi 自行偵測（見 jp_tokenizer 模組說明）
            cls._tagger = create_tagger()
        return cls._tagger

    @classmethod
    def normalize_verb(cls, verb: str) -> str:
        """Normalize a verb to its lemma form via NLP (lemmatization).

        將動詞進行 NLP 正規化 (Lemmatization)。

        For example, converts ``見つかる`` to ``見付かる`` to align with the ``verb_lemma`` values stored in the
        database. Only the first token tagged as a verb is used.

        例如將 ``見つかる`` 轉換為 ``見付かる`` 以對齊資料庫中的 ``verb_lemma``。只取第一個被標為動詞的詞。

        Args:
            verb: the raw verb string
                原始動詞字串

        Returns:
            the normalized lemma, or ``verb`` unchanged if no verb feature is found
            正規化後的動詞原型 (lemma)；找不到動詞特徵則原樣回傳 ``verb``
        """
        tagger = cls.get_tagger()
        for node in tagger(verb):
            # 檢查是否為動詞，並取得 feature[7] (lemma)
            if len(node.feature) > 7 and node.feature[0] == "動詞":
                return node.feature[7]
        return verb
