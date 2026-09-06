"""
Elasticsearch 非同步客戶端與語料檢索模組。

Async Elasticsearch client and corpus search module (Sudachi analyzer).
"""

import logging
from elasticsearch import AsyncElasticsearch
from app.core.config import settings

logger = logging.getLogger(__name__)

# Global singleton client instance
_es_client: AsyncElasticsearch | None = None

def get_elasticsearch_client() -> AsyncElasticsearch:
    """
    獲取或建立 AsyncElasticsearch 客戶端的單例 (Singleton)。

    Get or create the singleton AsyncElasticsearch client.

    此函數會根據環境變數 `ELASTICSEARCH_HOSTS` 建立與 Elasticsearch 的非同步連線。
    如果客戶端已經存在，則直接回傳現有的實例，避免重複建立連線，節省系統資源。

    Creates an async connection to Elasticsearch based on the
    `ELASTICSEARCH_HOSTS` setting. If a client already exists, the existing
    instance is returned to avoid redundant connections.

    Returns:
        AsyncElasticsearch: 非同步 Elasticsearch 客戶端實例。The async
            Elasticsearch client instance.
    """
    global _es_client
    if _es_client is not None:
        return _es_client

    es_url = settings.ELASTICSEARCH_HOSTS

    logger.info(f"🔌 初始化 Elasticsearch Client (URL: {es_url})")

    auth = None
    if settings.ELASTICSEARCH_USERNAME and settings.ELASTICSEARCH_PASSWORD:
        auth = (settings.ELASTICSEARCH_USERNAME, settings.ELASTICSEARCH_PASSWORD)

    _es_client = AsyncElasticsearch(
        es_url,
        basic_auth=auth,
        verify_certs=False,  # Set to True if using valid CA in production
        request_timeout=30
    )

    return _es_client

async def dispose_elasticsearch_client():
    """
    優雅地關閉 Elasticsearch 客戶端連線池並釋放資源。

    Gracefully close the Elasticsearch client's connection pool and release
    its resources.

    在應用程式關閉 (例如 FastAPI 的 lifespan shutdown 事件) 時應該呼叫此函數，
    以確保所有與 Elasticsearch 的網路連線都被正確關閉，防止資源外洩。

    Should be called at application shutdown (e.g. FastAPI lifespan shutdown)
    to ensure all network connections to Elasticsearch are closed properly
    and no resources leak.
    """
    global _es_client
    if _es_client is not None:
        await _es_client.close()
        _es_client = None
        logger.info("🧹 Elasticsearch Client 已關閉。")

async def recreate_index():
    """
    重新建立 `fluencytides_dialogue` 索引，並套用 Sudachi 日文分詞器設定。

    Recreate the `fluencytides_dialogue` index with the Sudachi Japanese
    analyzer settings applied.

    此函數會定義索引的 Mapping，特別針對 `dialogue` 欄位指定 `sudachi_analyzer`，
    以確保寫入與搜尋時皆能透過 Sudachi 將日文動詞/形容詞**還原為原型並統一
    表記**（気づく/気付く、もらう/貰う、バレる/ばれる 收斂為同一 token），
    達到精準檢索。mapping 只宣告 `analyzer`、不宣告 `search_analyzer`，
    ES 預設查詢時沿用同一個 analyzer，索引端與查詢端必然一致。

    Defines the index mapping, assigning `sudachi_analyzer` to the
    `dialogue` field so Japanese verbs/adjectives are reduced to their
    normalized dictionary forms both at index and search time.

    ⚠️ 警告: 此操作會先檢查並刪除同名的現有索引，這意味著該索引內的所有資料將被清空！
    僅適用於全量資料同步 (MySQL -> ES) 前的初始化操作。

    Warning: any existing index with the same name is deleted first, wiping
    all of its data. Intended only as initialization before a full data sync
    (MySQL -> ES).
    """
    client = get_elasticsearch_client()
    index_name = "fluencytides_dialogue"

    # Define index settings and mappings for Sudachi
    # Using 'search_analyzer' to ensure we use Sudachi at search time as well.
    index_body = {
        "settings": {
            "analysis": {
                "analyzer": {
                    "sudachi_analyzer": {
                        "type": "custom",
                        "tokenizer": "sudachi_tokenizer",
                        "filter": [
                            # normalizedform 同時做「活用形還原」與「表記正規化」。
                            # 換掉 baseform 的理由：baseform 只還原活用形，
                            # 気づいた→気づく、気付いた→気付く 仍是兩個不同
                            # 的 token，母卡與語料表記不同就互相搜不到
                            # （気づく 母卡 vs 語料 気付く 244 句，ES 命中 0；
                            # 貰う 只撈到 6/103 句；ばれる vs バレる 命中 0）。
                            # normalizedform 一律收斂到 Sudachi 辭書的正規化
                            # 表記，兩邊自動對齊。
                            # 配套：正規化會合併異體字（揚げる→上げる、
                            # 降りる→下りる），UniDic 在 lemma 層同樣合併，
                            # 故驗證器需 sibling_surfaces 防護才不會讓規範
                            # 表記的母卡吃掉變體表記的句子。詳見
                            # docs/wip/es_sudachi_normalizedform_FIX_2026-09-05.md
                            "sudachi_normalizedform",
                            "sudachi_part_of_speech"
                        ]
                    }
                }
            }
        },
        "mappings": {
            "properties": {
                "script_id": {"type": "long"},
                "source": {"type": "keyword"},
                "dialogue": {
                    "type": "text",
                    "analyzer": "sudachi_analyzer"
                }
            }
        }
    }

    # Delete index if it exists
    exists = await client.indices.exists(index=index_name)
    if exists:
        logger.warning(f"⚠️ 發現舊的 {index_name} 索引，正在刪除...")
        await client.indices.delete(index=index_name)

    logger.info(f"✨ 正在建立全新的 {index_name} 索引 (Sudachi Mapping)...")
    await client.indices.create(index=index_name, body=index_body)
    logger.info("✅ 索引建立完成！")

async def search_dialogue_by_verb(target_verb: str, game_name_jp: str = None, limit: int = 100, last_script_id: int = 0) -> list[dict]:
    """
    透過 Elasticsearch 搜尋包含指定動詞的台詞，自動運用 Sudachi 的變形還原能力。

    Search dialogue lines containing the given verb via Elasticsearch,
    leveraging Sudachi's inflection normalization.

    支援基於 `script_id` 的游標分頁 (Cursor-based Pagination)，確保檢索順序穩定且高效。

    Supports cursor-based pagination on `script_id` for stable, efficient
    ordering.

    Args:
        target_verb: 要搜尋的日文動詞（原型），例如 "広める"。The Japanese
            verb (base form) to search for, e.g. "広める".
        game_name_jp: 欲過濾的遊戲來源名稱，若未提供則搜尋全域語料庫，
            預設為 None。Optional game-source filter; searches the whole
            corpus when None (default).
        limit: 限制回傳的最大筆數，預設為 100。Maximum number of results,
            defaults to 100.
        last_script_id: 游標 ID，僅回傳 `script_id` 大於此值的結果，用於
            高效向後翻頁，預設為 0。Cursor: only rows with `script_id`
            greater than this value are returned; defaults to 0.

    Returns:
        list[dict]: 包含匹配台詞資訊的字典列表。A list of dicts describing
            matched dialogue lines.
            每個字典包含以下鍵值 (each dict contains):
            - script_id (int): 腳本在 MySQL 中的原始 ID。Original script ID
              in MySQL.
            - source (str): 來源遊戲名稱。Source game name.
            - dialogue (str): 台詞文本。Dialogue text.

    Raises:
        elasticsearch.ApiError: 當 ES|QL 語法錯誤或查詢失敗時拋出。Raised
            when the ES|QL syntax is invalid or the query fails.
    """
    client = get_elasticsearch_client()

    import re

    # 針對 ES|QL 處理字串，跳脫雙引號
    safe_verb = target_verb.replace('"', '\\"')

    # =========================================================================
    # [防禦性檢索：漢字強校驗]
    # 目的：解決 Elasticsearch 分詞器（如 Sudachi）在處理生僻詞彙或同義詞時的嚴重誤判。
    # 案例：搜尋「潤かす」時，ES 會將其拆分為「潤」與「かす」，並因 MATCH 的 OR 邏輯
    #       返回大量僅包含「かす」（例如「そかそか」）的無關台詞。
    # 解法：動態提取目標動詞中的「漢字」，並利用 ES|QL 的 LIKE 語法強制要求字串包含該漢字。
    # =========================================================================

    # 提取目標動詞中的所有漢字片段 (連續的漢字視為單一片段，範圍 \u4e00-\u9faf)
    # 例："潤かす" -> ["潤"], "思い出す" -> ["思", "出"], "ふやかす" -> []
    kanji_fragments = re.findall(r'[\u4e00-\u9faf]+', target_verb)
    like_clauses = ""

    if kanji_fragments:
        # 若目標動詞包含漢字，加上 LIKE "*漢字*" 過濾條件
        # 例：若片段為 ["思", "出"]，則生成 'dialogue LIKE "*思*"' 與 'dialogue LIKE "*出*"'
        clauses = [f'dialogue LIKE "*{k}*"' for k in kanji_fragments]

        # 將 LIKE 條件用 AND 串接，拼接到 ES|QL 的 WHERE 子句最後方
        # 這樣 ES 在完成 MATCH 檢索後，會嚴格過濾掉不包含目標漢字的「分詞假陽性」結果
        like_clauses = " AND " + " AND ".join(clauses)

    if game_name_jp:
        safe_game_name = game_name_jp.replace('"', '\\"')
        query_string = f"""
            FROM fluencytides_dialogue
            | WHERE MATCH(dialogue, "{safe_verb}") AND source == "{safe_game_name}" AND script_id > {last_script_id}{like_clauses}
            | SORT script_id ASC
            | LIMIT {limit}
        """
    else:
        query_string = f"""
            FROM fluencytides_dialogue
            | WHERE MATCH(dialogue, "{safe_verb}") AND script_id > {last_script_id}{like_clauses}
            | SORT script_id ASC
            | LIMIT {limit}
        """

    response = await client.esql.query(query=query_string)

    columns = [col['name'] for col in response.body.get('columns', [])]
    values = response.body.get('values', [])

    results = []
    for row in values:
        results.append(dict(zip(columns, row)))

    return results
