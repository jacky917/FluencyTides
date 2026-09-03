"""JP_CoreVerb 批量生成子卡片腳本（多樣性選句版）。

Batch child-card generator for JP_CoreVerb (diversity-selection edition):
scans master cards, runs the full selection funnel per verb, then calls the
FastAPI endpoint to generate context/cloze child cards.

掃描 ``JP_CORE_VERB_MASTER_DECK`` 牌組中的核心動詞母卡（單欄 ``Word``），
對每個動詞執行完整選句漏斗（docs/14_Core_Verb_Card_Plan.md §6）：

    ES 全量游標分頁 → §3.2 過濾 → fugashi token 級驗證 →
    搭配×活用形分桶 → zigzag 兩段式配額（含 §6.5 增量平衡）→
    對選中句呼叫 FastAPI ``/core-verb/generate-child-cards``
    （payload 含 ``target_verb_span`` 供後端挖空交叉驗證）。

與 JP_VerbPair 版的差異：
    1. 選句不再「照 script_id 順序取前 N」——改由 ``funnel.run_selection_funnel``
       在配額內最大化覆蓋搭配與活用形的變化空間。
    2. 全量游標分頁（每頁 500 直到空頁），避免 Fetch-100 的頭部偏差
       （§6.1 必修項 2）。
    3. per-verb 搜尋設定由同目錄的 ``verb_search_config.json`` 提供（§3.2）。

Example:
    不限制本次總量（受限於每動詞配額）::

        $ python backend/scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py --limit 0

    限制本次最多新增 10 張::

        $ python backend/scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py --limit 10

    測試模式（跑到選句為止，列印四段報告，不呼叫 API、零寫入）::

        $ python backend/scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py --limit 0 --dry-run
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import httpx

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from sqlalchemy import bindparam, text

from app.core.config import settings
from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import (
    corpus_async_session_factory,
    dispose_corpus_engine,
)
from app.infrastructure.database.elasticsearch_client import (
    dispose_elasticsearch_client,
    search_dialogue_by_verb,
)
from scripts.common.database.log_repository import (
    PROJECT_JP_CORE_VERB,
    GeneratedLogRepository,
)
from scripts.common.jp_reading_filter import ReadingFilter
from scripts.common.verb_lemma import canonical_verb_lemma
from scripts.common.llm_label import build_llm_model_label
from scripts.local_anki.common.deletion.profiles import get_profile
from scripts.fastapi_client.JP_VerbPair.pipeline_components.anki_media_uploader import (
    AnkiMediaUploader,
)
from scripts.fastapi_client.JP_VerbPair.pipeline_components.backend_api_client import (
    BackendAPIClient,
)
from scripts.fastapi_client.JP_VerbPair.pipeline_components.dedup_manager import DedupManager
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
    derive_target_lemmas,
)
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.funnel import (
    SelectionReport,
    VerbSearchConfig,
    format_selection_report,
    run_selection_funnel,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent / "verb_search_config.json"


def _load_search_config() -> dict[str, dict]:
    """讀取 per-verb 搜尋設定檔（§3.2），鍵統一轉為去標音表記。

    Load the per-verb search config file (§3.2), normalizing keys to
    furigana-stripped form.

    Returns:
        dict[str, dict]: ``{去標音動詞: 覆寫設定}``；檔案不存在或格式錯誤時
        回傳空 dict（全部動詞走預設值）。Mapping of stripped verb to override
        settings; empty dict if the file is missing or malformed.
    """
    if not _CONFIG_PATH.exists():
        logger.warning(f"⚠️ 找不到 {_CONFIG_PATH.name}，全部動詞使用預設搜尋設定。")
        return {}
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ 無法讀取 {_CONFIG_PATH.name}: {e}，全部動詞使用預設搜尋設定。")
        return {}
    # 設定檔鍵以母卡表記書寫（見[み]る），正規化後才對得上 verb_lemma
    return {canonical_verb_lemma(key): value for key, value in raw.items()}


def _build_verb_cfg(
    word_display: str,
    verb_lemma: str,
    overrides: dict,
    game_name_jp: str,
    *,
    skip_narrator: bool = False,
    compound_seqs: tuple = (),
) -> VerbSearchConfig:
    """由全域 settings 疊加 per-verb 覆寫組出 ``VerbSearchConfig``。

    Compose a ``VerbSearchConfig`` from global settings plus per-verb
    overrides.

    Args:
        word_display: 母卡 Word 欄位的原始表記（帶標音）。Raw Word field text
            with furigana.
        verb_lemma: 去標音字典形。Furigana-stripped dictionary form.
        overrides: ``verb_search_config.json`` 中該動詞的覆寫（可為空 dict）。
            Per-verb overrides; may be empty.
        game_name_jp: 遊戲來源名稱。Source game name.
        compound_seqs: 本專案全部多 token 目標動詞的 lemma 序列（讓位給更長的
            複合動詞用）。All multi-token target sequences of the project.
        skip_narrator: ``--skip-narrator`` 全域旗標；True 時強制排除旁白句，
            覆蓋 per-verb 的 ``exclude_narration``（只能加嚴、不能放寬）。
            Global flag forcing narration exclusion on top of the per-verb
            setting (tightens only).

    Returns:
        VerbSearchConfig: 漏斗設定。The funnel configuration.
    """
    return VerbSearchConfig(
        verb_display=word_display,
        verb_lemma=verb_lemma,
        include_keywords=list(overrides.get("include_keywords", [])),
        exclude_keywords=list(overrides.get("exclude_keywords", [])),
        exclude_speakers=list(overrides.get("exclude_speakers", [])),
        exclude_narration=skip_narrator or bool(overrides.get("exclude_narration", False)),
        exclude_script_ids=[int(x) for x in overrides.get("exclude_script_ids", [])],
        max_cards=int(
            overrides.get(
                "max_cards",
                getattr(settings, "JP_CORE_VERB_MAX_CARDS_PER_VERB", 15),
            )
        ),
        max_per_chapter=int(getattr(settings, "JP_CORE_VERB_MAX_PER_CHAPTER", 2)),
        min_sentence_length=int(
            getattr(settings, "JP_CORE_VERB_MIN_SENTENCE_LENGTH", 8)
        ),
        filter_moan=bool(getattr(settings, "JP_CORE_VERB_FILTER_MOAN_SENTENCES", True)),
        compound_seqs=compound_seqs,
        allow_auxiliary=bool(overrides.get("allow_auxiliary", False)),
        priority_collocations=list(overrides.get("priority_collocations", [])),
        page_size=500,
        game_name_jp=game_name_jp,
    )


async def _fetch_occupied(
    session,
    log_repo: GeneratedLogRepository,
    verb_lemma: str,
    anki_client: AnkiClient,
    master_note_id: int,
) -> list[dict]:
    """撈取該動詞已生成的句子（含原文/章節/說話者），供增量平衡分桶。

    Fetch the verb's already-generated sentences (text/chapter/speaker) for
    incremental-balancing bucket occupancy, reconciled against Anki.

    以 **Anki 實際存在的子卡為準**（而非僅信任 DB log）：

        1. ``generated_sentences_log`` 撈已生成紀錄（含子卡 note id）。
        2. ``find_notes`` 按 ``Master_Note_ID`` 欄位查 Anki 實存的
           context / cloze 子卡 note id 集合。
        3. 只有子卡仍存在於 Anki 的 log 紀錄才計入桶佔用——
           Anki 端已手動刪除的卡片自動釋放配額。
        4. Anki 有卡但 log 無對應紀錄（未追蹤卡）→ 以佔位項計入總量
           （佔配額、不佔桶），避免超生。

    Args:
        session: 語料庫 async session。Corpus async session.
        log_repo: 生成紀錄 repository。Generation-log repository.
        verb_lemma: 動詞字典形。Dictionary form of the verb.
        anki_client: Anki 連線客戶端（查實存子卡）。Anki client used to query
            existing child notes.
        master_note_id: 母卡 note id（子卡以 ``Master_Note_ID`` 欄位回鏈）。
            Master note id child cards link back to.

    Returns:
        list[dict]: 每項含 ``script_id / sentence / chapter / speaker``。
        Each item contains ``script_id / sentence / chapter / speaker``.
    """
    records = await log_repo.get_generated_records(
        session, verb_lemma, project=PROJECT_JP_CORE_VERB
    )

    # Anki 實存子卡 note id 集合（context / cloze 各查一次）
    anki_context_ids = set(
        await anki_client.find_notes(
            f'note:JP_Context_Dark "Master_Note_ID:{master_note_id}"'
        )
    )
    anki_cloze_ids = set(
        await anki_client.find_notes(
            f'note:JP_CoreVerb_Cloze_Dark "Master_Note_ID:{master_note_id}"'
        )
    )

    alive_records = []
    matched_context_ids: set[int] = set()
    matched_cloze_ids: set[int] = set()
    for rec in records:
        ctx_alive = rec["context_note_id"] in anki_context_ids
        clz_alive = rec["cloze_note_id"] in anki_cloze_ids
        if ctx_alive and rec["context_note_id"] is not None:
            matched_context_ids.add(rec["context_note_id"])
        if clz_alive and rec["cloze_note_id"] is not None:
            matched_cloze_ids.add(rec["cloze_note_id"])
        if ctx_alive or clz_alive:
            alive_records.append(rec)

    dropped = len(records) - len(alive_records)
    if dropped:
        logger.info(
            f"🧹 Anki 對帳：{dropped} 筆 log 紀錄的子卡已不存在於 Anki，"
            f"不計入佔用（釋放配額）。"
        )

    # 未追蹤卡：Anki 有 cloze 子卡但 log 無對應 → 佔量不佔桶
    untracked = len(anki_cloze_ids - matched_cloze_ids)
    placeholders = [
        {"script_id": -1, "sentence": "", "chapter": "", "speaker": ""}
        for _ in range(untracked)
    ]
    if untracked:
        logger.warning(
            f"⚠️ Anki 對帳：發現 {untracked} 張未追蹤的 cloze 子卡"
            f"（Anki 存在但 log 無紀錄），以佔位計入配額。"
        )

    script_ids = [rec["script_id"] for rec in alive_records]
    if not script_ids:
        return placeholders
    query = text(
        "SELECT id, dialogue, chapter, role_name FROM scripts WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    result = await session.execute(query, {"ids": list(script_ids)})
    occupied = []
    for row in result.fetchall():
        occupied.append(
            {
                "script_id": int(row[0]),
                "sentence": row[1] or "",
                "chapter": row[2] or "",
                "speaker": row[3] or "",
            }
        )
    return occupied + placeholders


def _make_metadata_fetcher(session):
    """建立注入漏斗的章節/說話者查詢器。

    Build the chapter/speaker metadata fetcher injected into the funnel.

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


def _make_es_fetcher(game_name_jp: str):
    """建立注入漏斗的 ES 游標分頁抓取器。

    Build the ES cursor-pagination fetcher injected into the funnel.

    Args:
        game_name_jp: 過濾的遊戲來源名稱。Source game name used as filter.

    Returns:
        Callable: ``(keyword, last_script_id, page_size) -> list[dict]``。
        Async callable performing one cursor-paged ES fetch.
    """

    async def es_fetcher(keyword: str, last_script_id: int, page_size: int) -> list[dict]:
        return await search_dialogue_by_verb(
            target_verb=keyword,
            game_name_jp=game_name_jp,
            limit=page_size,
            last_script_id=last_script_id,
        )

    return es_fetcher


async def _generate_from_report(
    report: SelectionReport,
    *,
    master_note_id: int,
    deck_name: str,
    game_name_jp: str,
    dedup_manager: DedupManager,
    api_client: BackendAPIClient,
    uploader: AnkiMediaUploader,
    global_limit: int,
    global_total: int,
) -> int:
    """對漏斗選中的句子逐一走生成管線（dedup → API → 媒體上傳 → 落庫）。

    Run each funnel-selected sentence through the generation pipeline
    (dedup, API call, media upload, DB logging).

    Args:
        report: 漏斗輸出（選中清單含 span 與章節）。Funnel output with spans
            and chapters.
        master_note_id: 母卡 note id。Master note id.
        deck_name: 子卡片目標牌組（Master 的上一層）。Target deck for child
            cards (parent of Master).
        game_name_jp: 遊戲來源名稱。Source game name.
        dedup_manager: 去重與上下文準備控制器。Dedup/context-prep controller.
        api_client: 後端 API 呼叫器。Backend API client.
        uploader: Anki 媒體延遲上傳器。Deferred Anki media uploader.
        global_limit: 本次執行全域上限（0 為不限）。Global cap (0 = no limit).
        global_total: 進入本函數前已生成的全域總數。Global count generated
            before entering this function.

    Returns:
        int: 本動詞實際新增的卡片數。Number of cards actually added.
    """
    # 本機推導的標籤只作兩用:①失敗紀錄(無回應可取)②回應缺欄時的
    # fallback。成功紀錄一律取後端回應的 llm_model(單一事實來源),
    # 詳見 docs/wip/runtime_config_service_FEAT_2026-08-29.md §3.5。
    llm_model_name = build_llm_model_label()

    new_generated = 0
    for item in report.selected:
        if global_limit > 0 and global_total + new_generated >= global_limit:
            logger.info(f"🛑 已達到本次執行的全局生成上限 ({global_limit})，停止處理該動詞。")
            break

        candidate = item.candidate
        script_id = candidate.script_id
        chapter = candidate.chapter

        # 傳入候選句原文以啟用文字層去重（同一句台詞、不同 script_id 的
        # 分身跳過），詳見 docs/archive/dedup_canonical_lemma_FIX_2026-09-02.md §3.2
        context_dialogue = await dedup_manager.prepare_generation(
            script_id=script_id,
            verb_lemma=report.verb_lemma,
            chapter=chapter,
            dialogue=candidate.sentence,
        )
        if not context_dialogue:
            continue

        payload = {
            "master_note_id": master_note_id,
            "deck_name": deck_name,
            "target_verb": report.verb_lemma,
            "source_game": game_name_jp,
            "context_dialogue": context_dialogue,
            "target_verb_span": list(candidate.span),
        }

        logger.info(
            f"🚀 發送生成請求 (script_id={script_id}, 搭配={candidate.collocation},"
            f" 活用={candidate.conjugation}, {item.pass_label})..."
        )
        try:
            response_json = await api_client.invoke_generation_pipeline(payload)
            data = response_json.get("data")
            if not data:
                logger.warning("⚠️ API 回應中沒有 'data' 欄位，無法進行後續處理。")
                continue

            if "kept_dialog" in data:
                await uploader.upload_media(data["kept_dialog"])

            await dedup_manager.record_success(
                script_id=script_id,
                verb_lemma=report.verb_lemma,
                chapter=chapter,
                master_note_id=master_note_id,
                context_note_id=data.get("context_note_id"),
                cloze_note_id=data.get("cloze_note_id"),
                llm_model=data.get("llm_model") or llm_model_name,
            )
            new_generated += 1
            logger.info(
                f"✅ 生成成功！({report.verb_lemma}) 本動詞進度:"
                f" {new_generated}/{report.quota}"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                try:
                    error_code = e.response.json().get("error_code")
                except Exception:
                    error_code = None
                if error_code == "CLOZE_POSITIONING_FAILED":
                    logger.warning(
                        f"⚠️ 該句挖空定位失敗被後端拒絕，自動跳過: {e.response.text}"
                    )
                    await dedup_manager.record_failure(
                        script_id, report.verb_lemma, chapter, master_note_id, llm_model_name
                    )
                    continue
                logger.error(f"❌ 發生預期外的業務邏輯錯誤 (HTTP 422): {e.response.text}")
                logger.error("🛑 將中止腳本，觸發安全退出。")
                raise
            if "429" in e.response.text or "Quota" in e.response.text:
                logger.warning(
                    f"⏳ 遇到 LLM 速率限制，暫停 60 秒後跳過此句: {e.response.text[:200]}..."
                )
                await asyncio.sleep(60)
                continue
            if (
                "LLM API 在所有重試後仍回傳空內容" in e.response.text
                or "PROHIBITED_CONTENT" in e.response.text
            ):
                logger.warning("⚠️ 遭遇 LLM 安全審查攔截，自動跳過此句並繼續...")
                await dedup_manager.record_failure(
                    script_id, report.verb_lemma, chapter, master_note_id, llm_model_name
                )
                continue
            if e.response.status_code >= 500:
                logger.warning(
                    f"⚠️ 遭遇伺服器錯誤 ({e.response.status_code})，紀錄失敗並跳過..."
                )
                await dedup_manager.record_failure(
                    script_id, report.verb_lemma, chapter, master_note_id, llm_model_name
                )
                await asyncio.sleep(3)
                continue
            logger.error(f"❌ 生成過程中發生伺服器錯誤: {e.response.status_code} - {e.response.text}")
            logger.error("🛑 將中止腳本，觸發安全退出。")
            raise
        except Exception as e:
            logger.error(f"❌ 生成過程中發生非預期錯誤: {type(e).__name__} - {str(e)}")
            logger.error("🛑 將中止腳本，觸發安全退出。")
            raise

    return new_generated


async def main() -> None:
    """腳本進入點：掃描母卡 → per-verb 漏斗選句 → 呼叫 API 生成子卡片。

    Script entry point: scan master cards, run the per-verb selection funnel,
    then call the API to generate child cards.
    """
    parser = argparse.ArgumentParser(description="JP_CoreVerb 批量生成子卡片腳本（多樣性選句版）")
    parser.add_argument(
        "--limit",
        type=int,
        required=True,
        help="本次執行一共添加幾張卡片 (0 代表無限制全部添加)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="測試執行：跑到選句為止列印四段報告，不呼叫後端 API 且不寫入資料庫",
    )
    parser.add_argument(
        "--skip-narrator",
        action="store_true",
        help="跳過所有旁白/無角色台詞（全域，覆蓋 per-verb 的 exclude_narration）",
    )
    args = parser.parse_args()

    global_limit = args.limit
    dry_run = args.dry_run
    skip_narrator = args.skip_narrator
    global_total = 0
    verb_stats: dict[str, int] = {}

    logger.info("=== JP_CoreVerb 批量生成子卡片腳本（多樣性選句版） ===")
    if dry_run:
        logger.info("🧪 測試模式 (DRY-RUN)：只跑到選句為止，零寫入。")
    if global_limit > 0:
        logger.info(f"⚙️ 本次執行將限制最多生成 {global_limit} 張卡片。")
    else:
        logger.info("⚙️ 本次執行不限制總生成卡片數。")

    import fugashi  # 延後 import：--help 等場景不需分詞器

    logger.info("🧠 初始化 Fugashi NLP Tagger (UniDic)...")
    tagger = fugashi.Tagger()

    master_deck = getattr(settings, "JP_CORE_VERB_MASTER_DECK", "日本語::核心動詞::Master")
    deck_name = re.sub(r"::Master$", "", master_deck)
    voice_dir = Path(
        getattr(settings, "JP_CORE_VERB_VOICE_DIR", settings.JP_VERB_PAIR_VOICE_DIR)
    )
    avatar_dir = Path(
        getattr(settings, "JP_CORE_VERB_AVATAR_DIR", settings.JP_VERB_PAIR_AVATAR_DIR)
    )
    source_game = getattr(
        settings, "JP_CORE_VERB_SOURCE_GAME", settings.JP_VERB_PAIR_SOURCE_GAME
    )
    game_name_jp = getattr(
        settings, "JP_CORE_VERB_GAME_NAME_JP", settings.JP_VERB_PAIR_GAME_NAME_JP
    )

    base_url = getattr(settings, "SCRIPTS_API_BASE_URL", "http://127.0.0.1:8000")
    api_url = f"{base_url.rstrip('/')}/api/v1/core-verb/generate-child-cards"

    headers: dict[str, str] = {}
    cf_id = settings.CF_ACCESS_CLIENT_ID
    cf_secret = settings.CF_ACCESS_CLIENT_SECRET
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret

    search_config = _load_search_config()
    anki_client = AnkiClient()

    try:
        logger.info(f"🔍 正在從 {master_deck} 抓取所有母卡片...")
        note_ids = await anki_client.find_notes(f'"deck:{master_deck}"')
        if not note_ids:
            logger.error(f"❌ 找不到任何母卡片！請確認 {master_deck} 中至少有一張卡片。")
            return
        logger.info(f"📦 總共找到 {len(note_ids)} 張母卡片。")
        note_ids.sort()

        # 同表層多讀表：掃母卡建構（不落設定檔）。判斷表由
        # JP_Common/judge_verb_readings.py 離線產生；表空或本專案沒有多讀
        # 表層時整段為 no-op，行為與現況相同（計畫 §3.3）。
        reading_filter = await ReadingFilter.create(anki_client, get_profile(PROJECT_JP_CORE_VERB))

        # 複合動詞序列清單：UniDic 把 走り出す 切成 走る＋出す、気に入る 切成
        # 気＋に＋入る，這些多 token 目標既要能被自己命中，也必須防止較短的
        # 單 token 母卡（走る／入る）把它們的句子收走。清單一次建好傳給漏斗。
        # Multi-token target sequences: needed both to match compounds and to
        # stop shorter single-token verbs from stealing their sentences.
        all_notes = await anki_client.get_notes_info(note_ids)
        # 迴圈內直接複用這批 note，不再逐一往返 Anki（344 張少 344 次呼叫）
        notes_by_id = {int(n.noteId): n for n in all_notes if getattr(n, "fields", None)}
        all_lemmas = []
        for note in all_notes:
            field = note.fields.get("Word", {})
            raw = field.get("value", "") if isinstance(field, dict) else getattr(field, "value", "")
            lemma = canonical_verb_lemma(raw)
            if lemma:
                all_lemmas.append(lemma)
        compound_seqs = tuple(
            seq for seq in (derive_target_lemmas(lemma, tagger) for lemma in all_lemmas)
            if len(seq) > 1
        )
        logger.info(f"🔗 多 token 複合動詞: {len(compound_seqs)} 個（單 token 母卡將讓位給它們）")

        async with corpus_async_session_factory() as session:
            log_repo = GeneratedLogRepository()
            dedup_manager = DedupManager(
                session=session,
                voice_dir=voice_dir,
                avatar_dir=avatar_dir,
                source_game=source_game,
                context_prev=getattr(settings, "JP_CORE_VERB_CONTEXT_PREV", 20),
                context_next=getattr(settings, "JP_CORE_VERB_CONTEXT_NEXT", 10),
                project=PROJECT_JP_CORE_VERB,
            )
            api_client = BackendAPIClient(api_url, headers)
            uploader = AnkiMediaUploader(anki_client, voice_dir, avatar_dir, source_game)
            es_fetcher = _make_es_fetcher(game_name_jp)
            metadata_fetcher = _make_metadata_fetcher(session)

            for idx, master_note_id in enumerate(note_ids, 1):
                logger.info("\n==================================================")
                logger.info(f"📝 處理母卡片 [{idx}/{len(note_ids)}] (ID: {master_note_id})")

                note = notes_by_id.get(int(master_note_id))
                if note is None:
                    logger.warning(f"⚠️ 無法讀取母卡片 {master_note_id} 的資訊，跳過。")
                    continue

                fields = note.fields
                word_field = fields.get("Word", {})
                word_display = (
                    word_field.get("value", "")
                    if isinstance(word_field, dict)
                    else getattr(word_field, "value", "")
                )
                # 去標音並去掉 Anki 的 ruby 分隔空白（聞[き]き 返[かえ]す →
                # 聞き返す）——留空白會讓 ES 與 UniDic 都對不上（2026-09-03
                # 實測 95/344 個動詞因此生不出卡）。
                verb_lemma = canonical_verb_lemma(word_display)
                if not verb_lemma:
                    logger.warning("⚠️ 此母卡片沒有 Word 欄位內容，跳過。")
                    continue

                logger.info(f"🎯 開始處理核心動詞: '{word_display}'（lemma: '{verb_lemma}'）")
                verb_cfg = _build_verb_cfg(
                    word_display, verb_lemma, search_config.get(verb_lemma, {}), game_name_jp,
                    skip_narrator=skip_narrator,
                    compound_seqs=compound_seqs,
                )

                # §6.5 增量平衡：以 Anki 實存子卡對帳後計入桶佔用
                occupied = await _fetch_occupied(
                    session, log_repo, verb_lemma, anki_client, master_note_id
                )
                if occupied:
                    logger.info(
                        f"♻️ 增量平衡：Anki 對帳後 {len(occupied)} 筆計入桶佔用。"
                    )

                # 已有生成紀錄（含軟刪除/失敗）的句子在過濾層直接篩掉
                exclude_generated = await log_repo.get_logged_keys(
                    session, verb_lemma, source_game, project=PROJECT_JP_CORE_VERB
                )

                # 同表層多讀（開く＝あく/ひらく）：把「已知讀作其他音」的 script_id
                # 交給漏斗在抓取階段排除，配額才不會浪費在不屬於本母卡的句子上。
                # 判斷表為空時集合為空 → 行為與現況完全相同（計畫 §3.3）。
                verb_cfg.exclude_script_ids = list(
                    set(verb_cfg.exclude_script_ids)
                    | await reading_filter.excluded_ids(
                        session,
                        verb_lemma,
                        reading_filter.reading_for_master(verb_lemma, master_note_id),
                    )
                )

                report = await run_selection_funnel(
                    verb_cfg,
                    es_fetcher,
                    occupied,
                    tagger=tagger,
                    metadata_fetcher=metadata_fetcher,
                    exclude_generated=exclude_generated,
                )
                logger.info(format_selection_report(report))

                if dry_run:
                    verb_stats[verb_lemma] = len(report.selected)
                    global_total += len(report.selected)
                    if global_limit > 0 and global_total >= global_limit:
                        logger.info(f"🛑 [DRY-RUN] 預計已達全局上限 ({global_limit})，結束。")
                        break
                    continue

                new_cards = await _generate_from_report(
                    report,
                    master_note_id=master_note_id,
                    deck_name=deck_name,
                    game_name_jp=game_name_jp,
                    dedup_manager=dedup_manager,
                    api_client=api_client,
                    uploader=uploader,
                    global_limit=global_limit,
                    global_total=global_total,
                )
                verb_stats[verb_lemma] = new_cards
                global_total += new_cards
                if global_limit > 0 and global_total >= global_limit:
                    logger.info(f"🛑 已達到本次執行的全局生成上限 ({global_limit})，結束腳本。")
                    break

            logger.info("\n==================================================")
            mode_str = "DRY-RUN 預計" if dry_run else "實際"
            logger.info(f"📊 [{mode_str}統計] 本次執行新增的子卡片總數為: {global_total} 張")
            reading_filter.log_summary()
            if verb_stats:
                logger.info("   [各動詞生成明細]")
                for verb_key, count in sorted(verb_stats.items(), key=lambda x: -x[1]):
                    logger.info(f"   - {verb_key} : {count:>3} 張")

    except Exception as e:
        logger.error(f"💥 發生非預期嚴重錯誤，腳本提前終止: {e}")
        sys.exit(1)
    finally:
        await anki_client.close()
        await dispose_corpus_engine()
        await dispose_elasticsearch_client()
        logger.info("🏁 資源已清理，腳本結束。")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
