"""Diagnostic endpoint exposing which Japanese tokenizer dictionary is loaded.

日文分詞詞典的查詢端點（診斷用）。

Why it exists: the dictionary depends on the runtime environment (full unidic, or the fallback
unidic-lite), and the two judge boundaries such as nominalized continuative forms differently; the
difference shows up only in the generated cards, never as an error. Making it a queryable endpoint
lets environment differences be confirmed before a run, instead of being inferred from the cards
afterwards (see the module docstring of app/infrastructure/utils/jp_tokenizer.py).

The path includes ``jp``: this service also hosts other language modules, and the tokenizer
dictionary is Japanese-specific infrastructure.

Scope: this endpoint reports which dictionary the **backend process** uses. The card generation
scripts tokenize in their own processes, and each reveals its dictionary in its startup log; the two
are guaranteed to match only inside the same container.

存在理由：詞典由執行環境決定（完整版 unidic 或回退的 unidic-lite），兩者
對連用形名詞化等邊界的判定不同，而差異只會表現在產出的卡片上、不會報錯。
把它做成可查詢的端點，環境差異就能在跑之前確認，而不是事後從卡片反推
（見 app/infrastructure/utils/jp_tokenizer.py 的模組說明）。

路徑帶 ``jp``：本服務同時承載其他語言模組，分詞詞典是日文專屬的基礎設施。

注意範圍：本端點回答的是**後端行程**用哪本詞典。生卡腳本在自己的行程裡
分詞，其詞典由該腳本啟動時的 log 揭露；兩者在同一個容器內才必然一致。
"""

from fastapi import APIRouter

from app.infrastructure.utils.jp_tokenizer import resolve_dictionary
from app.schemas.jp_tokenizer import TokenizerDictionaryResponse

router = APIRouter(prefix="/jp/tokenizer", tags=["JP Tokenizer"])


@router.get("/dictionary", response_model=TokenizerDictionaryResponse)
async def get_dictionary() -> TokenizerDictionaryResponse:
    """Report the tokenizer dictionary actually loaded by this process.

    回報本行程實際載入的分詞詞典。

    Returns:
        the dictionary's kind, version, and path, and whether it is the preferred full dictionary
        詞典種類、版本、路徑，以及是否為偏好的完整版
    """
    dictionary = resolve_dictionary()
    return TokenizerDictionaryResponse(
        kind=dictionary.kind,
        version=dictionary.version,
        dicdir=dictionary.dicdir,
        is_preferred=dictionary.is_preferred,
    )
