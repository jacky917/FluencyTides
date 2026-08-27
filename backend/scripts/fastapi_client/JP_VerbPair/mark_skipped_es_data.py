"""批量生成自動詞與他動詞的子卡片腳本 (Batch Child Cards Generator - ES Edition)

Batch child-card generator for intransitive/transitive verb pairs; this
variant additionally marks normally-deduped log rows with
``failure_count = 9`` so they are skipped permanently on future runs.

【行為差異註記 2026-08-27】主腳本 generate_child_cards.py 已接上
fugashi token 級驗證（lemma/讀音/複合動詞前後項/補助動詞四關，
詳見 docs/wip/verbpair_fugashi_validation_FEAT_2026-08-27.md），
本維運變體**刻意未接**：其 UPDATE 只影響既有 DB 紀錄、不會創造新卡，
未驗證的 ES 誤命中在此最多是對不存在的紀錄空更新。若日後把本腳本
當生成器使用，須先移植同款驗證。

此腳本負責從 Anki 中掃描 `日本語::自他動詞::Master` 牌組，針對每張母卡片中的動詞欄位，
向 Elasticsearch 檢索遊戲台詞，並呼叫 FastAPI 後端自動產生對應的 Context 與 Cloze 兩種類型的子卡片。

主要特色：
1. **ES 游標分頁檢索**：使用 Elasticsearch 的 `script_id > {last_script_id}` 進行極速的 O(1) 翻頁，徹底避免深層分頁的效能消耗。
2. **嚴格有序**：生成的例句會根據台詞在遊戲中的發生順序 (`script_id` 遞增) 嚴格排列。
3. **全域與區域上限**：
   - 區域上限：由 `.env` 的 `JP_VERB_PAIR_MAX_CARDS_PER_VERB` 控制，單一動詞在 Anki 中最高保留的卡片數。
   - 全域上限：由 CLI 參數 `--limit` 控制，避免單次執行耗費過多 LLM 資源。
4. **動態補給 (Top-up) 與防重複**：利用本地端資料庫紀錄與 Anki 的 JSON，跳過已經生成的舊台詞。

Example:
    無限制生成所有可生成的子卡片 (直到受限於 `.env` 中的 `JP_VERB_PAIR_MAX_CARDS_PER_VERB`)：
        $ python backend/scripts/fastapi_client/JP_VerbPair/generate_child_cards.py --limit 0

    限制本次執行最多只向 Anki 新增 50 張子卡片：
        $ python backend/scripts/fastapi_client/JP_VerbPair/generate_child_cards.py --limit 50

    測試模式 (Dry-Run)：模擬執行並計算預計生成的卡片數，但不實際調用 LLM 或寫入資料：
        $ python backend/scripts/fastapi_client/JP_VerbPair/generate_child_cards.py --limit 0 --dry-run
"""

import asyncio
import logging
import re
import sys
from pathlib import Path
import argparse
import httpx
from typing import Set

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.database.elasticsearch_client import search_dialogue_by_verb, dispose_elasticsearch_client
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager
from app.core.config import settings
from sqlalchemy import text
from scripts.common.database.log_repository import PROJECT_JP_VERB_PAIR
from scripts.common.llm_label import build_llm_model_label
from scripts.fastapi_client.JP_VerbPair.pipeline_components.dedup_manager import DedupManager
from scripts.fastapi_client.JP_VerbPair.pipeline_components.backend_api_client import BackendAPIClient
from scripts.fastapi_client.JP_VerbPair.pipeline_components.anki_media_uploader import AnkiMediaUploader

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

MASTER_DECK = "日本語::自他動詞::Master"

def _clean_verb_field(raw: str) -> list[str]:
    """從 Anki 欄位中的原始動詞字串，解析出可查詢的乾淨動詞列表。

    Parse the raw Anki verb field into a clean, queryable verb list,
    handling furigana brackets and multi-verb separators.

    Args:
        raw: Anki 欄位原始字串。Raw Anki field string.

    Returns:
        list[str]: 乾淨動詞列表。List of clean verb strings.
    """
    clean = re.sub(r'\[.*?\]', '', raw)
    parts = [v.strip() for v in re.split(r'[,、/・]', clean) if v.strip()]
    return parts if parts else [clean.strip()]

async def process_verb_group(
    anki_client: AnkiClient,
    session,
    dedup_manager: DedupManager,
    api_client: BackendAPIClient,
    uploader: AnkiMediaUploader,
    master_note_id: int,
    raw_verb_str: str,
    max_cards: int,
    game_name_jp: str,
    current_count: int,
    global_limit: int,
    global_total: int,
    dry_run: bool = False,
    verb_category: str = "",
    verb_stats: dict[str, int] = None,
    dry_run_generated: Set[tuple[str, int]] = None
) -> int:
    """處理自動詞或他動詞欄位中的所有同義動詞。回傳本次新增的卡片數量。

    Process every synonym verb in an intransitive/transitive field and
    return the number of newly added cards.

    Args:
        anki_client: Anki 連線客戶端。Anki client.
        session: 語料庫 async session。Corpus async session.
        dedup_manager: 去重與上下文準備控制器。Dedup/context-prep controller.
        api_client: 後端 API 呼叫器。Backend API client.
        uploader: Anki 媒體延遲上傳器。Deferred Anki media uploader.
        master_note_id: 母卡 note id。Master note id.
        raw_verb_str: 動詞欄位原始字串。Raw verb field string.
        max_cards: 單一動詞欄位生成上限。Per-field generation cap.
        game_name_jp: 遊戲來源名稱。Source game name.
        current_count: 既有紀錄數（起算值）。Existing record count.
        global_limit: 本次執行全域上限（0 為不限）。Global cap (0 = none).
        global_total: 進入本函數前的全域生成數。Global count so far.
        dry_run: 測試模式，不實際寫入。Dry-run mode, no writes.
        verb_category: 統計用類別標籤（自/他動詞）。Category label for stats.
        verb_stats: 各動詞生成統計 dict。Per-verb stats accumulator.
        dry_run_generated: dry-run 模擬去重集合。Dry-run dedup set.

    Returns:
        int: 本次新增的卡片數。Number of cards added this call.
    """
    if verb_stats is None:
        verb_stats = {}
    if dry_run_generated is None:
        dry_run_generated = set()
    if not raw_verb_str.strip():
        return 0
        
    clean_verbs = _clean_verb_field(raw_verb_str)
    processed_lemmas: Set[str] = set()
    success_count = current_count
    new_generated = 0
    
    for raw_verb in clean_verbs:
        if success_count >= max_cards:
            logger.info(f"✅ 動詞欄位 [{raw_verb_str}] 已達到生成上限 ({max_cards})，跳過後續同義詞。")
            break
            
        if global_limit > 0 and global_total + new_generated >= global_limit:
            logger.info(f"🛑 已達到本次執行的全局生成上限 ({global_limit})，停止處理該動詞。")
            break
            
        normalized_verb = raw_verb
        
        # 利用正規化後的 lemma 去重，避免同欄位內同義詞（如 替わる, 代わる）重複生成
        if normalized_verb in processed_lemmas:
            logger.info(f"⏭️ 單字 '{raw_verb}' (正規化: '{normalized_verb}') 已處理過，跳過。")
            continue
            
        processed_lemmas.add(normalized_verb)
        
        logger.info(f"🎯 開始處理單字: '{raw_verb}' (正規化為 '{normalized_verb}')...")
        
        last_script_id = 0
        limit_per_batch = 100
        has_more_data = True
        
        while has_more_data and success_count < max_cards and (global_limit == 0 or global_total + new_generated < global_limit):
            es_results = await search_dialogue_by_verb(
                target_verb=normalized_verb,
                game_name_jp=game_name_jp,
                limit=limit_per_batch,
                last_script_id=last_script_id
            )
            
            if not es_results:
                has_more_data = False
                break
                
            logger.info(f"   🔍 從游標 {last_script_id} 取得 {len(es_results)} 筆候選對話。")
            
            for row in es_results:
                script_id = row["script_id"]
                last_script_id = script_id  # 更新游標
                
                if success_count >= max_cards:
                    break
                    
                if global_limit > 0 and global_total + new_generated >= global_limit:
                    break
                    
                # 取得台詞的章節
                target_query = text("SELECT chapter FROM scripts WHERE id = :script_id")
                target_result = await session.execute(target_query, {"script_id": script_id})
                target_row = target_result.fetchone()
                chapter = target_row[0] if target_row else ""
                
                # Dry-Run 模式下，模擬真實寫入的全域去重
                if dry_run and (normalized_verb, script_id) in dry_run_generated:
                    continue
                    
                # 去重檢查 + 上下文組裝
                context_dialogue = await dedup_manager.prepare_generation(
                    script_id=script_id, 
                    verb_lemma=normalized_verb, 
                    chapter=chapter
                )
                
                # 如果 context_dialogue 是 None，代表 dedup_manager 發現已經有紀錄（成功或失敗），正常跳過
                # 依照使用者要求，將這些「正常的去重資料」標記為 failure_count = 9
                if context_dialogue is None:
                    mark_query = text("""
                        UPDATE generated_sentences_log 
                        SET failure_count = 9
                        WHERE script_id = :script_id AND verb_lemma = :verb_lemma
                    """)
                    await session.execute(mark_query, {
                        "script_id": script_id,
                        "verb_lemma": normalized_verb
                    })
                    await session.commit()
                    continue
                    
                # 如果 context_dialogue 是空陣列 []，代表 context_builder 發生異常 (找不到台詞或無法組裝)
                if context_dialogue == []:
                    continue
                    
                payload = {
                    "master_note_id": master_note_id,
                    "deck_name": "日本語::自他動詞",
                    "target_verb": raw_verb,  # 保留原始字形顯示在卡片上
                    "source_game": game_name_jp,
                    "context_dialogue": context_dialogue
                }
                
                if dry_run:
                    logger.info(f"🧪 [DRY-RUN] 預計生成卡片: script_id={script_id}, 單字='{raw_verb}', 章節={chapter}")
                    success_count += 1
                    new_generated += 1
                    stat_key = f"{normalized_verb}（{verb_category}）"
                    verb_stats[stat_key] = verb_stats.get(stat_key, 0) + 1
                    dry_run_generated.add((normalized_verb, script_id))
                    continue
                    
                logger.info(f"🚀 發送生成請求 (script_id: {script_id})...")
                
                # 若 API 發生嚴重錯誤 (例如無法連線、LLM多重失敗等)，BackendAPIClient 內部如果拋出 Exception 會中斷這層，
                # 我們應該讓它向上拋出，以便觸發安全退出。
                # 解析 model 標籤（統一規則見 scripts/common/llm_label.py）
                llm_model_name = build_llm_model_label()
                    
                try:
                    response_json = await api_client.invoke_generation_pipeline(payload)
                    data = response_json.get("data")
                    if not data:
                        logger.warning("⚠️ API 回應中沒有 'data' 欄位，無法進行後續處理。")
                        continue
                    
                    if "kept_dialog" in data:
                        await uploader.upload_media(data["kept_dialog"])
                        
                    # 寫入去重紀錄
                    await dedup_manager.record_success(
                        script_id=script_id,
                        verb_lemma=normalized_verb,
                        chapter=chapter,
                        master_note_id=master_note_id,
                        context_note_id=data.get("context_note_id"),
                        cloze_note_id=data.get("cloze_note_id"),
                        llm_model=llm_model_name
                    )
                    success_count += 1
                    new_generated += 1
                    stat_key = f"{normalized_verb}（{verb_category}）"
                    verb_stats[stat_key] = verb_stats.get(stat_key, 0) + 1
                    logger.info(f"✅ 生成成功！({raw_verb})，目前進度: {success_count}/{max_cards}")
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 422:
                        # 嘗試解析回傳的 ErrorResponse JSON
                        try:
                            error_data = e.response.json()
                            error_code = error_data.get("error_code")
                        except Exception:
                            error_code = None

                        if error_code == "CLOZE_POSITIONING_FAILED":
                            logger.warning(f"⚠️ 該句子挖空定位失敗被後端拒絕，自動跳過並嘗試下一句: {e.response.text}")
                            await dedup_manager.record_failure(script_id, normalized_verb, chapter, master_note_id, llm_model_name)
                            continue
                        
                        # 其他的 422 錯誤 (例如參數錯誤、未知的業務邏輯錯誤)，則當作嚴重錯誤拋出
                        logger.error(f"❌ 發生預期外的業務邏輯錯誤 (HTTP 422): {e.response.text}")
                        logger.error("🛑 將中止腳本，觸發安全退出。")
                        raise  # 向上拋出以終止腳本
                    else:
                        if "429" in e.response.text or "Quota" in e.response.text:
                            logger.warning(f"⏳ 遇到 LLM API 速率限制 (Rate Limit)，自動暫停 60 秒後跳過此句並繼續: {e.response.text[:200]}...")
                            import asyncio
                            await asyncio.sleep(60)
                            continue
                            
                        if "LLM API 在所有重試後仍回傳空內容" in e.response.text or "PROHIBITED_CONTENT" in e.response.text:
                            logger.warning("⚠️ 遭遇 LLM 安全審查攔截，自動跳過此句並繼續...")
                            await dedup_manager.record_failure(script_id, normalized_verb, chapter, master_note_id, llm_model_name)
                            continue

                        # 處理 Reverse Proxy (Nginx/Cloudflare) 導致的無回應 500/502/504 錯誤，或是其它不可控錯誤
                        if e.response.status_code >= 500:
                            logger.warning(f"⚠️ 遭遇伺服器錯誤 ({e.response.status_code})，文字內容: '{e.response.text[:100]}'。將紀錄失敗並跳過...")
                            await dedup_manager.record_failure(script_id, normalized_verb, chapter, master_note_id, llm_model_name)
                            import asyncio
                            await asyncio.sleep(3)  # 稍微等待一下避免連續撞擊代理伺服器
                            continue
                            
                        logger.error(f"❌ 生成過程中發生伺服器錯誤: {e.response.status_code} - {e.response.text}")
                        logger.error("🛑 將中止腳本，觸發安全退出。")
                        raise  # 向上拋出以終止腳本
                except Exception as e:
                    logger.error(f"❌ 生成過程中發生非預期錯誤: {type(e).__name__} - {str(e)}")
                    logger.error("🛑 將中止腳本，觸發安全退出。")
                    raise  # 向上拋出以終止腳本

    return new_generated

async def main() -> None:
    """腳本進入點：掃描母卡並逐一處理自/他動詞欄位。

    Script entry point: scan master cards and process each
    intransitive/transitive verb field in turn.
    """
    parser = argparse.ArgumentParser(description="批量生成子卡片腳本")
    parser.add_argument("--limit", type=int, required=True, help="本次執行一共添加幾張卡片 (0 代表無限制全部添加)")
    parser.add_argument("--dry-run", action="store_true", help="測試執行，僅計算預計生成的卡片數量，不調用後端 API 且不寫入資料庫")
    args = parser.parse_args()
    
    global_limit = args.limit
    dry_run = args.dry_run
    global_total = 0
    verb_stats: dict[str, int] = {}
    dry_run_generated: Set[tuple[str, int]] = set()

    logger.info("=== 批量生成子卡片腳本 (ES 版) ===")
    if dry_run:
        logger.info("🧪 測試模式 (DRY-RUN) 已開啟：不會有任何實際寫入，僅作計數")
    if global_limit > 0:
        logger.info(f"⚙️ 本次執行將限制最多生成 {global_limit} 張卡片。")
    else:
        logger.info("⚙️ 本次執行不限制總生成卡片數 (生成全部)。")
    
    # 載入 API 與網路設定
    base_url = getattr(settings, "SCRIPTS_API_BASE_URL", "http://127.0.0.1:8000")
    api_url = f"{base_url.rstrip('/')}/api/v1/verb-pair/generate-child-cards"
    
    headers: dict[str, str] = {}
    cf_id = settings.CF_ACCESS_CLIENT_ID
    cf_secret = settings.CF_ACCESS_CLIENT_SECRET
    if cf_id and cf_secret:
        headers["CF-Access-Client-Id"] = cf_id
        headers["CF-Access-Client-Secret"] = cf_secret
        
    anki_client = AnkiClient()
    
    try:
        logger.info(f"🔍 正在從 {MASTER_DECK} 抓取所有母卡片...")
        note_ids = await anki_client.find_notes(f'"deck:{MASTER_DECK}"')
        
        if not note_ids:
            logger.error(f"❌ 找不到任何母卡片！請確認 {MASTER_DECK} 中至少有一張卡片。")
            return
            
        logger.info(f"📦 總共找到 {len(note_ids)} 張母卡片。")
        
        # 為了保證每次執行結果的一致性，強制對 ID 進行排序
        note_ids.sort()
        
        # 準備音檔和頭像的路徑
        voice_dir = Path(settings.JP_VERB_PAIR_VOICE_DIR)
        avatar_dir = Path(settings.JP_VERB_PAIR_AVATAR_DIR)
        source_game = settings.JP_VERB_PAIR_SOURCE_GAME
        game_name_jp = settings.JP_VERB_PAIR_GAME_NAME_JP
        
        # 從 .env 讀取每個動詞最大生成張數
        max_cards_per_verb = settings.JP_VERB_PAIR_MAX_CARDS_PER_VERB
        logger.info(f"⚙️ 每個動詞（自/他獨立計算）最大生成上限：{max_cards_per_verb} 張")
        
        async with corpus_async_session_factory() as session:
            # 初始化生成管線模組
            dedup_manager = DedupManager(
                session=session,
                voice_dir=voice_dir,
                avatar_dir=avatar_dir,
                source_game=source_game,
                context_prev=settings.JP_VERB_PAIR_CONTEXT_PREV,
                context_next=settings.JP_VERB_PAIR_CONTEXT_NEXT,
                project=PROJECT_JP_VERB_PAIR,
            )
            api_client = BackendAPIClient(api_url, headers)
            uploader = AnkiMediaUploader(anki_client, voice_dir, avatar_dir, source_game)
            
            for idx, master_note_id in enumerate(note_ids, 1):
                logger.info(f"\n==================================================")
                logger.info(f"📝 處理母卡片 [{idx}/{len(note_ids)}] (ID: {master_note_id})")
                
                notes_info = await anki_client.get_notes_info([master_note_id])
                if not notes_info:
                    logger.warning(f"⚠️ 無法讀取母卡片 {master_note_id} 的資訊，跳過。")
                    continue
                    
                fields = notes_info[0].fields
                
                # 兼容不同格式的欄位值
                intransitive_field = fields.get("Intransitive_Word", fields.get("Intransitive", {}))
                intransitive = (
                    intransitive_field.get("value", "")
                    if isinstance(intransitive_field, dict)
                    else getattr(intransitive_field, 'value', '')
                )
                
                transitive_field = fields.get("Transitive_Word", fields.get("Transitive", {}))
                transitive = (
                    transitive_field.get("value", "")
                    if isinstance(transitive_field, dict)
                    else getattr(transitive_field, 'value', '')
                )
                
                if not intransitive and not transitive:
                    logger.warning("⚠️ 此母卡片沒有自動詞與他動詞，跳過。")
                    continue
                    
                # 分別處理自動詞與他動詞，獨立計算上限
                if intransitive:
                    # 取得目前 JSON 中已有的紀錄數量
                    intransitive_json_field = fields.get("Intransitive_Data_JSON", {})
                    intransitive_json_str = (
                        intransitive_json_field.get("value", "[]")
                        if isinstance(intransitive_json_field, dict)
                        else getattr(intransitive_json_field, 'value', '[]')
                    )
                    intransitive_list = AnkiJsonFieldManager.parse_field_string(intransitive_json_str)
                    intransitive_current_count = len(intransitive_list)
                    
                    logger.info(f"▶️ 開始處理 [自動詞] 欄位: {intransitive} (目前 JSON 中已有 {intransitive_current_count} 筆紀錄)")
                    new_cards = await process_verb_group(
                        anki_client=anki_client,
                        session=session,
                        dedup_manager=dedup_manager,
                        api_client=api_client,
                        uploader=uploader,
                        master_note_id=master_note_id,
                        raw_verb_str=intransitive,
                        max_cards=max_cards_per_verb,
                        game_name_jp=game_name_jp,
                        current_count=intransitive_current_count,
                        global_limit=global_limit,
                        global_total=global_total,
                        dry_run=dry_run,
                        verb_category="自動詞",
                        verb_stats=verb_stats,
                        dry_run_generated=dry_run_generated
                    )
                    global_total += new_cards
                    if global_limit > 0 and global_total >= global_limit:
                        logger.info(f"🛑 已達到本次執行的全局生成上限 ({global_limit})，結束腳本。")
                        break
                    
                if transitive:
                    # 取得目前 JSON 中已有的紀錄數量
                    transitive_json_field = fields.get("Transitive_Data_JSON", {})
                    transitive_json_str = (
                        transitive_json_field.get("value", "[]")
                        if isinstance(transitive_json_field, dict)
                        else getattr(transitive_json_field, 'value', '[]')
                    )
                    transitive_list = AnkiJsonFieldManager.parse_field_string(transitive_json_str)
                    transitive_current_count = len(transitive_list)
                    
                    logger.info(f"▶️ 開始處理 [他動詞] 欄位: {transitive} (目前 JSON 中已有 {transitive_current_count} 筆紀錄)")
                    new_cards = await process_verb_group(
                        anki_client=anki_client,
                        session=session,
                        dedup_manager=dedup_manager,
                        api_client=api_client,
                        uploader=uploader,
                        master_note_id=master_note_id,
                        raw_verb_str=transitive,
                        max_cards=max_cards_per_verb,
                        game_name_jp=game_name_jp,
                        current_count=transitive_current_count,
                        global_limit=global_limit,
                        global_total=global_total,
                        dry_run=dry_run,
                        verb_category="他動詞",
                        verb_stats=verb_stats,
                        dry_run_generated=dry_run_generated
                    )
                    global_total += new_cards
                    if global_limit > 0 and global_total >= global_limit:
                        logger.info(f"🛑 已達到本次執行的全局生成上限 ({global_limit})，結束腳本。")
                        break
                        
            logger.info("\n==================================================")
            mode_str = "DRY-RUN 預計" if dry_run else "實際"
            logger.info(f"📊 [{mode_str}統計] 本次執行新增的子卡片總數為: {global_total} 張")
            if verb_stats:
                logger.info("   [各動詞生成明細]")
                sorted_stats = sorted(verb_stats.items(), key=lambda item: item[1], reverse=True)
                max_key_len = max(len(k) for k in verb_stats.keys())
                for verb_key, count in sorted_stats:
                    padding = '　' * (max_key_len - len(verb_key))
                    logger.info(f"   - {verb_key}{padding} : {count:>3} 張")

    except Exception as e:
        logger.error(f"💥 發生非預期嚴重錯誤，腳本提前終止: {e}")
        # sys.exit() 會自動觸發 finally 區塊
        sys.exit(1)
    finally:
        await anki_client.close()
        await dispose_corpus_engine()
        await dispose_elasticsearch_client()
        logger.info("🏁 資源已清理，腳本結束。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
