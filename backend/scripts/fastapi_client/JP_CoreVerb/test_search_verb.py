"""JP_CoreVerb 分桶驗證腳本（開發/調參專用，計劃 §6.7）。

Bucketing verification script for JP_CoreVerb (dev/tuning only, plan §6.7):
runs the exact production selection funnel against real ES corpus data and
prints four diagnostic reports, with zero writes to Anki, DB, or LLM.

對真實 ES 語料執行「與正式生成完全一致」的整條選句漏斗
（只 import ``funnel.run_selection_funnel``，不自帶任何過濾/分桶邏輯），
輸出四種報告供調參：

    1. 漏斗各層統計（ES 命中 → 過濾後 → 驗證通過，含拒絕原因分佈）。
    2. 搭配桶 × 活用形桶 的分桶矩陣表格。
    3. zigzag 選取軌跡：每句標注（搭配桶 / 活用形桶 / 章節 / Pass 1 或 2）。
    4. 未覆蓋桶清單（有候選但配額內未選中——判斷配額是否該調）。

設定來源：
    **不讀 .env / settings**——所有選句參數寫死在檔案頂部的 ``TEST_CONFIG``
    map，改代碼即改測試條件，調參迭代零摩擦。
    （唯二例外：ES 與 MySQL 的「連線」本身必須走 settings——
    ``elasticsearch_client`` / ``corpus_database`` 依 settings 建立連線，
    此為基礎設施而非選句參數，可接受；選句參數全部本檔寫死。）

零寫入：不碰 Anki / 不寫 DB / 不呼叫 LLM，可對真實 ES 反覆執行。
預設 ``occupied`` 傳空（純看當前語料分佈）；加 ``--with-occupied``
可選擇性讀取 ``generated_sentences_log`` 模擬增量平衡後的選取結果（唯讀）。

Example:
    $ python backend/scripts/fastapi_client/JP_CoreVerb/test_search_verb.py
    $ python backend/scripts/fastapi_client/JP_CoreVerb/test_search_verb.py --with-occupied
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env（僅供基礎設施連線使用）
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from sqlalchemy import bindparam, text

from app.infrastructure.database.corpus_database import (
    corpus_async_session_factory,
    dispose_corpus_engine,
)
from app.infrastructure.database.elasticsearch_client import (
    dispose_elasticsearch_client,
    search_dialogue_by_verb,
)
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.funnel import (
    VerbSearchConfig,
    format_selection_report,
    run_selection_funnel,
    strip_furigana,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# =============================================================================
# TEST_CONFIG：所有選句參數寫死於此（不讀 .env / settings）。
# 改代碼即改測試條件——per-verb 覆寫的鍵與 verb_search_config.json 同構。
# =============================================================================
TEST_CONFIG = {
    # 待驗證的動詞清單（去標音字典形）
    "verbs": ["見せる"],
    # 全域選句參數
    "quota": 15,                  # 每動詞配額（max_cards）
    "max_per_chapter": 2,         # 同一章節最多取句數
    "min_sentence_length": 8,     # 目標句最短長度
    "page_size": 500,             # ES 游標分頁每頁筆數
    "game_name_jp": "サノバウィッチ",
    # per-verb include/exclude 覆寫（省略的動詞全走預設）
    "per_verb": {
        "見せる": {
            "exclude_speakers": ["柊史"],
            "exclude_narration": True,
        },
    },
}


def _build_cfg(verb: str) -> VerbSearchConfig:
    """由 ``TEST_CONFIG`` 組出單一動詞的漏斗設定。

    Build a single verb's funnel config from ``TEST_CONFIG``.

    Args:
        verb: 動詞字典形（去標音）。Dictionary form of the verb (furigana stripped).

    Returns:
        VerbSearchConfig: 漏斗設定（與正式腳本注入的結構完全相同）。
        The funnel config, identical in shape to the production script's.
    """
    overrides = TEST_CONFIG["per_verb"].get(verb, {})
    return VerbSearchConfig(
        verb_display=verb,
        verb_lemma=strip_furigana(verb),
        include_keywords=list(overrides.get("include_keywords", [])),
        exclude_keywords=list(overrides.get("exclude_keywords", [])),
        exclude_speakers=list(overrides.get("exclude_speakers", [])),
        exclude_narration=bool(overrides.get("exclude_narration", False)),
        exclude_script_ids=[int(x) for x in overrides.get("exclude_script_ids", [])],
        max_cards=int(overrides.get("max_cards", TEST_CONFIG["quota"])),
        max_per_chapter=int(TEST_CONFIG["max_per_chapter"]),
        min_sentence_length=int(TEST_CONFIG["min_sentence_length"]),
        allow_auxiliary=bool(overrides.get("allow_auxiliary", False)),
        priority_collocations=list(overrides.get("priority_collocations", [])),
        page_size=int(TEST_CONFIG["page_size"]),
        game_name_jp=TEST_CONFIG["game_name_jp"],
    )


async def _es_fetcher(keyword: str, last_script_id: int, page_size: int) -> list[dict]:
    """注入漏斗的 ES 游標分頁抓取器（僅 ES 連線走 settings）。

    ES cursor-pagination fetcher injected into the funnel; only the ES
    connection itself goes through settings.

    Args:
        keyword: ES 檢索關鍵字。Search keyword for ES.
        last_script_id: 游標（上一頁最後的 script_id）。Cursor: last page's last id.
        page_size: 每頁筆數。Rows per page.

    Returns:
        list[dict]: ES 命中列（含 ``script_id`` 與 ``dialogue``）。
        ES hit rows containing ``script_id`` and ``dialogue``.
    """
    return await search_dialogue_by_verb(
        target_verb=keyword,
        game_name_jp=TEST_CONFIG["game_name_jp"],
        limit=page_size,
        last_script_id=last_script_id,
    )


def _make_metadata_fetcher(session):
    """建立注入漏斗的章節/說話者查詢器（唯讀 MySQL）。

    Build the chapter/speaker metadata fetcher injected into the funnel
    (read-only MySQL access).

    Args:
        session: 語料庫 async session。Corpus async session.

    Returns:
        Callable: ``(script_ids) -> {script_id: {"chapter", "speaker"}}``。
        Async callable mapping script ids to chapter/speaker metadata.
    """

    async def metadata_fetcher(script_ids: list[int]) -> dict[int, dict]:
        if not script_ids:
            return {}
        query = text(
            "SELECT id, chapter, role_name FROM scripts WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        result = await session.execute(query, {"ids": list(script_ids)})
        return {
            int(row[0]): {"chapter": row[1] or "", "speaker": row[2] or ""}
            for row in result.fetchall()
        }

    return metadata_fetcher


async def _fetch_occupied(session, verb_lemma: str) -> list[dict]:
    """讀取該動詞已生成句（唯讀），供 ``--with-occupied`` 模擬增量平衡。

    Read-only fetch of the verb's already-generated sentences, used by
    ``--with-occupied`` to simulate incremental balancing.

    Args:
        session: 語料庫 async session。Corpus async session.
        verb_lemma: 動詞字典形。Dictionary form of the verb.

    Returns:
        list[dict]: 每項含 ``script_id / sentence / chapter / speaker``。
        Each item contains ``script_id / sentence / chapter / speaker``.
    """
    from scripts.common.database.log_repository import GeneratedLogRepository

    script_ids = await GeneratedLogRepository().get_generated_script_ids(
        session, verb_lemma
    )
    if not script_ids:
        return []
    query = text(
        "SELECT id, dialogue, chapter, role_name FROM scripts WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    result = await session.execute(query, {"ids": list(script_ids)})
    return [
        {
            "script_id": int(row[0]),
            "sentence": row[1] or "",
            "chapter": row[2] or "",
            "speaker": row[3] or "",
        }
        for row in result.fetchall()
    ]


async def main() -> None:
    """腳本進入點：對 TEST_CONFIG 的每個動詞跑漏斗並列印四段報告。

    Script entry point: run the funnel for every verb in ``TEST_CONFIG`` and
    print the four-section report.
    """
    parser = argparse.ArgumentParser(description="JP_CoreVerb 分桶驗證腳本（零寫入）")
    parser.add_argument(
        "--with-occupied",
        action="store_true",
        help="讀取 generated_sentences_log（唯讀）計入桶佔用，模擬增量平衡後的選取",
    )
    args = parser.parse_args()

    import fugashi

    logger.info("🧠 初始化 Fugashi NLP Tagger (UniDic)...")
    tagger = fugashi.Tagger()

    logger.info("=== JP_CoreVerb 分桶驗證腳本（零寫入，選句參數全部寫死於 TEST_CONFIG） ===")
    try:
        async with corpus_async_session_factory() as session:
            metadata_fetcher = _make_metadata_fetcher(session)
            for verb in TEST_CONFIG["verbs"]:
                verb_cfg = _build_cfg(verb)
                occupied: list[dict] = []
                exclude_generated: set[tuple[int, str]] | None = None
                if args.with_occupied:
                    from scripts.common.database.log_repository import (
                        GeneratedLogRepository,
                    )

                    occupied = await _fetch_occupied(session, verb_cfg.verb_lemma)
                    exclude_generated = await GeneratedLogRepository().get_logged_keys(
                        session, verb_cfg.verb_lemma
                    )
                    logger.info(
                        f"♻️ --with-occupied：'{verb_cfg.verb_lemma}' 已生成 {len(occupied)} 筆計入桶佔用，"
                        f"{len(exclude_generated)} 筆紀錄（含軟刪除/失敗）於過濾層排除。"
                    )
                report = await run_selection_funnel(
                    verb_cfg,
                    _es_fetcher,
                    occupied,
                    tagger=tagger,
                    metadata_fetcher=metadata_fetcher,
                    exclude_generated=exclude_generated,
                )
                logger.info("")
                logger.info(format_selection_report(report))
    finally:
        await dispose_corpus_engine()
        await dispose_elasticsearch_client()
        logger.info("🏁 資源已清理，腳本結束（未寫入任何資料）。")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
