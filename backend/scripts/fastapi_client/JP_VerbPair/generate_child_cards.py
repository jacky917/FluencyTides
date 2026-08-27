"""批量生成自動詞與他動詞的子卡片腳本 (Batch Child Cards Generator - ES Edition)

Batch child-card generator for intransitive/transitive verb pairs: scans
master cards in Anki, searches Elasticsearch for game dialogue lines, and
calls the FastAPI backend to create Context and Cloze child cards.

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
import json
import os
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
    skip_narrator: bool = False,
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
        skip_narrator: 跳過旁白/無角色台詞。Skip narrator/no-role lines.
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
        
        target_keywords = [normalized_verb]
        str_nid = str(master_note_id)
        
        config_path = os.path.join(os.path.dirname(__file__), "extra_search_keywords.json")
        extra_keywords_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    extra_keywords_config = json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ 無法讀取 extra_search_keywords.json: {e}")

        if str_nid in extra_keywords_config and normalized_verb in extra_keywords_config[str_nid]:
            extra_kw = extra_keywords_config[str_nid][normalized_verb]
            if isinstance(extra_kw, list):
                target_keywords.extend(extra_kw)
                logger.info(f"   ➕ 偵測到額外搜尋關鍵字，擴展為: {target_keywords}")

        total_needed = max_cards - success_count
        if total_needed <= 0:
            break
            
        keyword_data = []
        num_kw = len(target_keywords)
        # 計算基本目標配額：將總配額平均分給所有關鍵字，多的分給前面的
        base_quota = total_needed // num_kw
        remainder = total_needed % num_kw
        
        # 開局只查兩次 ES (Fetch All Upfront)，抓取所有備用候選名單
        for idx, kw in enumerate(target_keywords):
            logger.info(f"   🔍 正在預先獲取 '{kw}' 的 ES 候選對話...")
            # 取出 100 筆備用彈藥，確保足夠應對後端生成失敗
            es_results = await search_dialogue_by_verb(
                target_verb=kw,
                game_name_jp=game_name_jp,
                limit=100,
                last_script_id=0
            )
            target = base_quota + (1 if idx < remainder else 0)
            keyword_data.append({
                "keyword": kw,
                "es_results": es_results,
                "cursor": 0,
                "target": target,
                "successes": 0
            })
            
        # 輔助函式：處理單一關鍵字的生成，直到達成目標數量或名單耗盡
        async def process_keyword_up_to_target(kd: dict) -> bool:
            """對單一關鍵字生成直到達標或候選名單耗盡。

            Generate for one keyword until its target is met or its
            candidate list is exhausted.

            Args:
                kd: 關鍵字狀態 dict（keyword/es_results/cursor/target/
                    successes）。Keyword state dict.

            Returns:
                bool: ``False`` 表示觸及全域上限須停止；否則 ``True``。
                ``False`` when the global limit is hit; otherwise ``True``.
            """
            nonlocal success_count, new_generated
            while kd["successes"] < kd["target"] and kd["cursor"] < len(kd["es_results"]):
                row = kd["es_results"][kd["cursor"]]
                kd["cursor"] += 1
                script_id = row["script_id"]
                
                if global_limit > 0 and global_total + new_generated >= global_limit:
                    return False
                    
                target_query = text("SELECT chapter FROM scripts WHERE id = :script_id")
                target_result = await session.execute(target_query, {"script_id": script_id})
                target_row = target_result.fetchone()
                chapter = target_row[0] if target_row else ""
                
                if dry_run and (kd["keyword"], script_id) in dry_run_generated:
                    continue
                    
                context_dialogue = await dedup_manager.prepare_generation(
                    script_id=script_id, 
                    verb_lemma=kd["keyword"], 
                    chapter=chapter
                )
                
                if not context_dialogue:
                    continue
                    
                if skip_narrator:
                    target_line = next((line for line in context_dialogue if line.get("is_target")), None)
                    if target_line:
                        t_speaker = target_line.get("speaker", "-")
                        t_avatar = target_line.get("avatar", "none")
                        if t_speaker in ("-", "", "none") and t_avatar == "none":
                            logger.info(f"⏭️ 由於啟用了 --skip-narrator，跳過此旁白/無角色台詞: script_id={script_id}")
                            continue
                            
                payload = {
                    "master_note_id": master_note_id,
                    "deck_name": "日本語::自他動詞",
                    "target_verb": kd["keyword"],  # 使用當下的關鍵字
                    "source_game": game_name_jp,
                    "context_dialogue": context_dialogue
                }
                
                if dry_run:
                    logger.info(f"🧪 [DRY-RUN] 預計生成卡片: script_id={script_id}, 單字='{kd['keyword']}', 章節={chapter}")
                    success_count += 1
                    new_generated += 1
                    kd["successes"] += 1
                    stat_key = f"{kd['keyword']}（{verb_category}）"
                    verb_stats[stat_key] = verb_stats.get(stat_key, 0) + 1
                    dry_run_generated.add((kd["keyword"], script_id))
                    continue
                    
                logger.info(f"🚀 發送生成請求 (script_id: {script_id})...")
                
                llm_model_name = settings.LLM_MODEL_NAME
                if settings.LLM_PROVIDER and settings.LLM_PROVIDER.lower() not in ("google", "openai", ""):
                    llm_model_name = f"({settings.LLM_PROVIDER}){llm_model_name}"
                # claude-code provider 的推理力度會影響生成品質，
                # 需一併記錄才能事後區分同模型不同力度產出的卡片。
                if settings.LLM_PROVIDER and settings.LLM_PROVIDER.lower() == "claude-code":
                    llm_model_name = f"{llm_model_name}@{settings.LLM_CLAUDE_CODE_EFFORT}"

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
                        verb_lemma=kd["keyword"],
                        chapter=chapter,
                        master_note_id=master_note_id,
                        context_note_id=data.get("context_note_id"),
                        cloze_note_id=data.get("cloze_note_id"),
                        llm_model=llm_model_name
                    )
                    success_count += 1
                    new_generated += 1
                    kd["successes"] += 1
                    stat_key = f"{kd['keyword']}（{verb_category}）"
                    verb_stats[stat_key] = verb_stats.get(stat_key, 0) + 1
                    logger.info(f"✅ 生成成功！({kd['keyword']})，總進度: {success_count}/{max_cards} (本字進度: {kd['successes']}/{kd['target']})")
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 422:
                        try:
                            error_data = e.response.json()
                            error_code = error_data.get("error_code")
                        except Exception:
                            error_code = None

                        if error_code == "CLOZE_POSITIONING_FAILED":
                            logger.warning(f"⚠️ 該句子挖空定位失敗被後端拒絕，自動跳過並嘗試下一句: {e.response.text}")
                            await dedup_manager.record_failure(script_id, kd["keyword"], chapter, master_note_id, llm_model_name)
                            continue
                        
                        logger.error(f"❌ 發生預期外的業務邏輯錯誤 (HTTP 422): {e.response.text}")
                        logger.error("🛑 將中止腳本，觸發安全退出。")
                        raise
                    else:
                        if "429" in e.response.text or "Quota" in e.response.text:
                            logger.warning(f"⏳ 遇到 LLM API 速率限制 (Rate Limit)，自動暫停 60 秒後跳過此句並繼續: {e.response.text[:200]}...")
                            await asyncio.sleep(60)
                            continue
                            
                        if "LLM API 在所有重試後仍回傳空內容" in e.response.text or "PROHIBITED_CONTENT" in e.response.text:
                            logger.warning("⚠️ 遭遇 LLM 安全審查攔截，自動跳過此句並繼續...")
                            await dedup_manager.record_failure(script_id, kd["keyword"], chapter, master_note_id, llm_model_name)
                            continue

                        if e.response.status_code >= 500:
                            logger.warning(f"⚠️ 遭遇伺服器錯誤 ({e.response.status_code})，文字內容: '{e.response.text[:100]}'。將紀錄失敗並跳過...")
                            await dedup_manager.record_failure(script_id, kd["keyword"], chapter, master_note_id, llm_model_name)
                            await asyncio.sleep(3)
                            continue
                            
                        logger.error(f"❌ 生成過程中發生伺服器錯誤: {e.response.status_code} - {e.response.text}")
                        logger.error("🛑 將中止腳本，觸發安全退出。")
                        raise
                except Exception as e:
                    logger.error(f"❌ 生成過程中發生非預期錯誤: {type(e).__name__} - {str(e)}")
                    logger.error("🛑 將中止腳本，觸發安全退出。")
                    raise
            return True

        # =====================================================================
        # 第一階段 (Pass 1)：各自達標
        # 系統會將剩餘配額平均分配（例如 17 張配額 -> 捲くる 目標 9 張，まくる 目標 8 張）。
        # 接著針對兩份獨立的 List 進行 API 請求，只有生成成功才會消耗配額。
        # =====================================================================
        logger.info(f"   🔄 開始第一階段分配 (Pass 1)...")
        stop_all = False
        for kd in keyword_data:
            if not await process_keyword_up_to_target(kd):
                stop_all = True
                break
                
        if not stop_all:
            # =====================================================================
            # 第二階段 (Pass 2)：互相補足
            # 如果第一階段執行完，因為語料不足或 API 失敗，導致還有剩餘配額沒有用完，
            # 系統會自動啟動 Pass 2。程式會把剩下的配額轉交給「備用清單還沒用完」的關鍵字，
            # 直到補滿所需卡片數量為止！
            # =====================================================================
            current_total_success = sum(kd["successes"] for kd in keyword_data)
            if current_total_success < total_needed:
                logger.info(f"   🔄 開始第二階段互補 (Pass 2)...")
                for kd in keyword_data:
                    # 如果該關鍵字的備用名單還沒用完，代表它可以幫忙消化別人沒用完的配額
                    if kd["cursor"] < len(kd["es_results"]):
                        shortfall = total_needed - sum(k["successes"] for k in keyword_data)
                        if shortfall <= 0:
                            break
                        kd["target"] += shortfall
                        logger.info(f"      將 {shortfall} 個剩餘額度轉移給 '{kd['keyword']}'...")
                        if not await process_keyword_up_to_target(kd):
                            stop_all = True
                            break
                    if stop_all:
                        break

    return new_generated

async def main() -> None:
    """腳本進入點：掃描母卡並逐一處理自/他動詞欄位。

    Script entry point: scan master cards and process each
    intransitive/transitive verb field in turn.
    """
    parser = argparse.ArgumentParser(description="批量生成子卡片腳本")
    parser.add_argument("--limit", type=int, required=True, help="本次執行一共添加幾張卡片 (0 代表無限制全部添加)")
    parser.add_argument("--dry-run", action="store_true", help="測試執行，僅計算預計生成的卡片數量，不調用後端 API 且不寫入資料庫")
    parser.add_argument("--skip-narrator", action="store_true", help="跳過所有旁白/無角色台詞的生成")
    args = parser.parse_args()
    
    global_limit = args.limit
    dry_run = args.dry_run
    skip_narrator = args.skip_narrator
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
                        skip_narrator=skip_narrator,
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
                        skip_narrator=skip_narrator,
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
