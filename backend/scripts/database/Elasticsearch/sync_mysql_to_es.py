"""將語料庫的台詞（``game_lines``）全量同步至 Elasticsearch 索引。

Full sync of the corpus lines (``game_lines``) into the Elasticsearch
index, recreating the index first and bulk-writing cleaned dialogue
documents.

讀的是進語料庫的台詞（``status='active' AND in_corpus=1 AND kind IN ('line','bgv')``，
docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md D9 的 D），經
``scripts.common.database.corpus_repository.iter_lines`` 逐款遊戲依劇情順序讀出。
ES 文件格式不變（``script_id``、``source``、``dialogue``），所以搜尋程式不用改：
``script_id`` 是 ``game_lines.id``（沿用舊 ``scripts.id``），``source`` 是
``GameProfile.es_source``（舊 ``scripts.source``，魔女的夜宴是 ``サノバウィッチ``），
``dialogue`` 是帶振假名標記的日文（舊 ``scripts.dialogue``）。
The ES document shape is unchanged, so the search code needs no change.
"""

import sys
import time
import asyncio
import logging
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from elasticsearch import helpers

from app.core.config import settings
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.database.elasticsearch_client import get_elasticsearch_client, recreate_index, dispose_elasticsearch_client
from scripts.common.database.corpus_repository import get_game_profile, iter_lines, list_game_ids

BATCH_SIZE = 5000  # 每 5000 筆批次寫入一次

logger = logging.getLogger(__name__)


def build_action(line_id: int, source: str, dialogue: str | None) -> dict | None:
    """把一句台詞轉成 ES bulk action；空白台詞回傳 None。

    Turn one line into an ES bulk action; blank lines yield None.

    ``_id`` 顯式使用台詞 ID（MySQL 主鍵）：使寫入天然冪等（upsert）——重跑 sync 不
    recreate 也不會產生同 ID 的重複文檔，bulk 部分失敗後重試亦安全。
    The document ``_id`` is the line id, which keeps writes idempotent.

    Args:
        line_id: 台詞 ID。Line id.
        source: ES 的 ``source`` 欄。The ES ``source`` value.
        dialogue: 台詞（帶振假名標記）。The dialogue.

    Returns:
        dict | None: bulk action。The bulk action, or None.
    """
    if dialogue is None:
        return None
    # 清洗對話，去除換行等雜訊
    clean_dialogue = dialogue.replace('\n', '').strip()
    if not clean_dialogue:
        return None
    return {
        "_index": "fluencytides_dialogue",
        "_id": line_id,
        "_source": {
            "script_id": line_id,
            "source": source,
            "dialogue": clean_dialogue,
        },
    }


async def sync_mysql_to_es():
    """重建 ES 索引並從語料庫逐款遊戲流式讀取台詞批次寫入。

    Recreate the ES index, stream every game's corpus lines, and bulk
    index them in batches (idempotent via the line id as document _id).

    重建索引前先確認語料庫連得上、``game_profiles`` 至少有一款遊戲；任一不成立就拒絕執行，
    不會留下一個被清空的索引。同步中途出錯時記錄完整 traceback 並往上拋（``__main__`` 以非零
    結束碼退出），不印「同步完成」。
    Before recreating the index, refuse to run when the corpus is not
    configured or ``game_profiles`` has no games, so the index is never left
    empty. Failures are logged with a traceback and re-raised (non-zero exit).

    Raises:
        RuntimeError: 語料庫未設定或 ``game_profiles`` 沒有遊戲。The corpus is not
            configured or has no games.
    """
    print(f"🔌 連線至遠端資料庫 ({settings.MYSQL_HOST}:{settings.MYSQL_PORT}/{settings.MYSQL_DATABASE})...")

    total_processed = 0
    total_indexed = 0
    batch_actions = []

    start_time = time.time()

    try:
        if corpus_async_session_factory is None:
            raise RuntimeError("語料庫連線未設定（corpus_async_session_factory 是 None），請檢查 .env 的 MYSQL_* 設定")
        async with corpus_async_session_factory() as session:
            games = [await get_game_profile(session, game_id) for game_id in await list_game_ids(session)]
        if not games:
            raise RuntimeError(
                f"{settings.MYSQL_DATABASE}.game_profiles 沒有任何遊戲，拒絕重建索引"
                "（請確認 MYSQL_DATABASE 指向語料庫、且語料庫已匯入）"
            )

        # 1. 確認有資料可寫之後，才刪除舊索引並重建 Elasticsearch Index
        print("🔌 連線至 Elasticsearch 並重建 Index...")
        await recreate_index()
        es_client = get_elasticsearch_client()
        print("📥 開始從語料庫讀取 game_lines 資料 (全量同步)...")

        for game in games:
            print(f"🎮 {game.game_id}（ES source = {game.es_source}）")
            # 串流讀取期間同一個 session 不能做其他查詢，所以每款遊戲開一個 session
            async with corpus_async_session_factory() as session:
                async for line in iter_lines(session, game.game_id):
                    total_processed += 1
                    action = build_action(line.id, game.es_source, line.dialogue)
                    if action is None:
                        continue
                    batch_actions.append(action)
                    total_indexed += 1

                    # 批次寫入
                    if len(batch_actions) >= BATCH_SIZE:
                        print(f"  [進度] 讀取了 {total_processed} 筆台詞，正批次寫入 ES...")
                        await helpers.async_bulk(es_client, batch_actions)
                        batch_actions.clear()

        # 寫入最後殘餘的批次
        if batch_actions:
            print(f"  [進度] 讀取了 {total_processed} 筆台詞，正批次寫入最後一批...")
            await helpers.async_bulk(es_client, batch_actions)
            batch_actions.clear()

    except Exception:
        logger.exception(
            "❌ 同步失敗（已掃描 %d 筆、寫入 %d 筆）；索引可能不完整，修正後請重跑", total_processed, total_indexed
        )
        raise
    finally:
        await dispose_corpus_engine()
        await dispose_elasticsearch_client()

    elapsed = time.time() - start_time
    print(f"🎉 全量同步完成！共花費 {elapsed:.2f} 秒。")
    print(f"總共掃描 {total_processed} 筆台詞，並成功同步 {total_indexed} 筆至 Elasticsearch。")


if __name__ == "__main__":
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        asyncio.run(sync_mysql_to_es())
    except Exception:
        sys.exit(1)
