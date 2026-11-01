"""以 LLM 批次解析遊戲台詞並建置詞彙索引 (nlp_line_terms，原 dialogue_terms_index)。

Build the vocabulary index (nlp_line_terms, formerly dialogue_terms_index)
by batch-parsing game dialogue with an LLM, using full-chapter context in
the system prompt.

台詞依劇情順序從 ``game_lines`` 讀（``--game`` 指定遊戲，預設 SabbatOfTheWitch），讀寫集中在
``terms_index_store.py``。Lines come from ``game_lines`` in story order.

模型：在本機以本機 ``.env`` 的 ``LLM_PROVIDER`` / ``LLM_MODEL_NAME`` 建立客戶端，後端 TG ``/setconfig``
（DB）的用途設定對這支腳本無效（模型路由計畫 llm_model_routing_FEAT_2026-10-15.md D9）。
Model: built locally from this machine's ``.env``; the backend's ``/setconfig``
route settings do not apply to this script.
"""

import argparse
import asyncio
import sys
import time
import logging
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[4]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.infrastructure.llm.base import BaseLLMClient
from app.infrastructure.database.corpus_database import corpus_async_session_factory, dispose_corpus_engine
from app.infrastructure.llm.factory import create_llm_client
from scripts.common.database.corpus_repository import DEFAULT_GAME_ID, get_game_profile, list_chapters
from scripts.database.MySQL.JP_VerbPair import terms_index_store

# ==========================================
# 結構化輸出 Schema (Pydantic V2)
# ==========================================
class TermDetail(BaseModel):
    """單一詞彙與其頻率分數。A single term with its frequency score."""

    term: str = Field(description="詞彙或文法原型 (辭書型)")
    score: int = Field(ge=0, le=99, description="使用頻率分數 (0~99)，分數越高說明越常使用、越基礎")

class SentenceExtraction(BaseModel):
    """單一句子的語意解析結果。Extraction result for one sentence."""

    grammar: list[TermDetail] = Field(description="文法/句型列表")
    verbs: list[TermDetail] = Field(description="動詞(辭書型)列表")
    adjectives_i: list[TermDetail] = Field(description="い形容詞列表")
    adjectives_na: list[TermDetail] = Field(description="な形容詞列表")
    nouns: list[TermDetail] = Field(description="名詞列表")

class ScriptTermsResult(BaseModel):
    """台詞 ID 與解析結果的映射。Maps a script ID to its extraction."""

    script_id: int = Field(description="對應的台詞 ID")
    extraction: SentenceExtraction

class BatchExtractionResponse(BaseModel):
    """批次解析的最終回傳結果。Final batch extraction response."""

    results: list[ScriptTermsResult]


logger = logging.getLogger(__name__)

async def process_chapter(chapter_name: str, game_id: str, llm_client: BaseLLMClient, limit: int, state: dict) -> bool:
    """處理單一章節：讀取台詞、跳過已索引句、分批請求 LLM 解析並寫入索引。

    Process one chapter: fetch its dialogue lines, skip already-indexed
    ones, request LLM parsing in batches, and insert the extracted terms.

    系統提示詞裡的章節上下文一律是整章；只有要解析的目標句會跳過已有索引的句子
    （``--limit`` 中斷後重跑不會重複寫入）。
    The chapter context is always the whole chapter; only the target lines
    skip those already indexed, so re-running after ``--limit`` does not
    duplicate rows.

    Args:
        chapter_name: 章節名稱。Chapter name.
        game_id: 遊戲代號。Game id.
        llm_client: LLM 客戶端。LLM client instance.
        limit: API 呼叫上限（0 為不限）。API call limit (0 = unlimited).
        state: 跨章節共享的呼叫次數狀態。Shared call-count state.

    Returns:
        bool: True 表示已達上限應提前停止。True when the limit was hit
        and processing should stop early.
    """
    print(f"\n📚 開始處理章節: {chapter_name}")
    
    # 1. 取得該章節的所有台詞
    async with corpus_async_session_factory() as session:
        rows = await terms_index_store.chapter_lines(session, game_id, chapter_name)
            
    if not rows:
        print(f"  ⏭️ 章節 {chapter_name} 沒有有效台詞，跳過。")
        return False

    # 1.5 已有索引列的台詞不再送 LLM（支援斷點續跑，與 build_llm_index_no_context.py 相同）
    # Lines that already have index rows are not sent again.
    async with corpus_async_session_factory() as session:
        indexed = await terms_index_store.indexed_line_ids(session, game_id)
    targets = [row for row in rows if row[0] not in indexed]
    if len(targets) < len(rows):
        print(f"  📝 該章節共有 {len(rows)} 句，已有索引 {len(rows) - len(targets)} 句（跳過），剩餘 {len(targets)} 句待處理。")
    else:
        print(f"  📝 該章節共有 {len(rows)} 句有效台詞。")
    if not targets:
        print(f"  ✅ 章節 {chapter_name} 所有台詞皆已完成索引，跳過。")
        return False
    
    # 2. 構建該章節的完整靜態上下文區塊
    context_lines = []
    for script_id, dialogue in rows:
        if dialogue:
            clean_dialogue = dialogue.replace('\n', '')
            context_lines.append(f"[{script_id}] {clean_dialogue}")
            
    static_context = "\n".join(context_lines)
    
    system_prompt = (
        "你是一位精通日文語言學與詞彙分析的專家。\n"
        "你的任務是根據提供的遊戲劇本章節上下文，精準拆解出台詞中的詞彙，並給予頻率評分 (0~99)。\n"
        "【重要評分指標】：該詞彙在「日常口語對話」中的常用程度佔了極大權重！若是日本人口語天天用的字，請給予極高分（80~99）；若是書面語、古語或口語罕用，請給予低分。\n"
        "頻率評分標準：0代表極罕見/書面語，50代表一般詞彙，99代表超基礎、每天都會講到的口語單字(如 する, 私)。\n"
        "所有動詞、形容詞必須還原為「辭書型 (原型)」。\n\n"
        f"【以下是完整章節上下文，請作為語意理解的參考】\n"
        f"----------------------------------------\n"
        f"{static_context}\n"
        f"----------------------------------------\n"
    )

    batch_size = settings.LLM_PARSE_BATCH_SIZE
    total_processed = 0
    total_terms_inserted = 0
    
    # 3. 分批 (Sliding Window) 請求 LLM 萃取（只送未索引的目標句）
    for i in range(0, len(targets), batch_size):
        if limit > 0 and state["api_calls"] >= limit:
            print(f"  🛑 已達到本次執行上限 ({limit} 次 API 呼叫)，停止處理。")
            return True
            
        chunk = targets[i : i + batch_size]
        start_id = chunk[0][0]
        end_id = chunk[-1][0]
        
        target_ids = [row[0] for row in chunk]
        
        user_prompt = (
            f"請解析以上章節中，ID 介於 {start_id} 到 {end_id} 之間，共 {len(chunk)} 句的台詞。\n"
            f"目標 ID 清單：{target_ids}\n\n"
            "請將這幾句的解析結果嚴格依照 JSON 格式輸出，不要遺漏任何一個 ID。"
        )
        
        print(f"  🔄 正在請求 LLM 解析 ID {start_id} ~ {end_id} (共 {len(chunk)} 句)...", end="", flush=True)
        
        try:
            result = await llm_client.generate_structured_data(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=BatchExtractionResponse.model_json_schema()
            )
            print(" ✅ 成功")
            
            # 4. 解析 JSON 並寫入 MySQL
            response_data = BatchExtractionResponse.model_validate(result.parsed_data)
            model_name = llm_client.model_label
            
            insert_data = []
            for item in response_data.results:
                s_id = item.script_id
                # Grammar
                for term in item.extraction.grammar:
                    insert_data.append((s_id, term.term, 'grammar', term.score, model_name))
                # Verbs
                for term in item.extraction.verbs:
                    insert_data.append((s_id, term.term, 'verb', term.score, model_name))
                # Nouns
                for term in item.extraction.nouns:
                    insert_data.append((s_id, term.term, 'noun', term.score, model_name))
                # Adjective I
                for term in item.extraction.adjectives_i:
                    insert_data.append((s_id, term.term, 'adjective_i', term.score, model_name))
                # Adjective Na
                for term in item.extraction.adjectives_na:
                    insert_data.append((s_id, term.term, 'adjective_na', term.score, model_name))
            
            if insert_data:
                async with corpus_async_session_factory() as session:
                    await terms_index_store.insert_terms(session, game_id, insert_data)
                total_terms_inserted += len(insert_data)
                
            total_processed += len(chunk)
            state["api_calls"] += 1
            
        except SQLAlchemyError:
            # 資料庫寫不進去時繼續跑只會白花 LLM 呼叫：記錄後中止整個腳本
            # A DB failure would waste every further LLM call: stop.
            print(" ❌ 寫入索引失敗，中止")
            logger.exception("寫入 nlp_line_terms 失敗（台詞 %s），中止建置", target_ids)
            raise
        except Exception as e:
            # LLM 呼叫或回應解析失敗：記錄這一批的台詞後跳過，下次重跑會補上（已索引的句子會跳過）
            # LLM or parse failure: log this chunk's line ids and move on.
            print(f" ❌ 失敗: {str(e)}")
            logger.exception("LLM 解析失敗，跳過這一批（台詞 %s）", target_ids)
            # LLMClient 已有內建重試，這裡不再重試
            continue
            
    print(f"  🏁 章節 {chapter_name} 完成。處理 {total_processed} 句，新增 {total_terms_inserted} 個詞彙索引。")
    return False

async def build_llm_index(limit: int = 0, game_id: str = DEFAULT_GAME_ID):
    """主流程：初始化 LLM、清理舊索引、逐章節建置索引。

    Main flow: initialize the LLM client, optionally clear the old
    index, then build the index chapter by chapter.

    Args:
        limit: API 呼叫上限（0 為不限）。API call limit (0 = unlimited).
        game_id: 遊戲代號。Game id.
    """
    print("🚀 啟動 LLM 語意解析索引建置腳本...")
    
    # 確保使用的模型與 API KEY 存在
    try:
        llm_client = create_llm_client()
    except Exception as e:
        print(f"初始化 LLM 客戶端失敗: {e}")
        return

    print("-" * 50)
    print("📊 當前執行配置：")
    print(f"  🔹 LLM 模型：{llm_client.model_label}")
    print(f"  🔹 批次大小 (Batch Size)：{settings.LLM_PARSE_BATCH_SIZE}")
    print(f"  🔹 執行上限 (Limit)：{'無限制' if limit == 0 else f'{limit} 次請求'}")
    print("-" * 50)

    start_time = time.time()
    try:
        # 先確認遊戲存在再清任何資料（打錯 --game 不能清掉別的東西）；LookupError 會列出可用的代號
        # Validate --game before clearing anything; the error lists valid ids.
        try:
            async with corpus_async_session_factory() as session:
                await get_game_profile(session, game_id)
        except LookupError as error:
            print(f"❌ {error}")
            sys.exit(1)

        if limit == 0:
            print(f"🧹 清除舊有 [{game_id}] 索引資料以防重複...")
            async with corpus_async_session_factory() as session:
                await terms_index_store.clear_game(session, game_id)
        else:
            print("⚠️ 啟用 --limit 模式，跳過清除舊有索引（已有索引的台詞會跳過，可接續執行）。")

        print("🔍 掃描所有有效章節...")
        async with corpus_async_session_factory() as session:
            chapters = await list_chapters(session, game_id)
        
        print(f"總共找到 {len(chapters)} 個章節。")
        
        state = {"api_calls": 0}
        for chapter_name in chapters:
            stop_early = await process_chapter(chapter_name, game_id, llm_client, limit, state)
            if stop_early:
                break
            
    finally:
        await dispose_corpus_engine()
        
    elapsed = time.time() - start_time
    print(f"🎉 全部預處理完成！共花費 {elapsed:.2f} 秒。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM 語意解析索引建置")
    parser.add_argument("--limit", type=int, default=0, help="限制本次執行的 API 呼叫次數 (0 代表全部執行)")
    parser.add_argument("--game", default=DEFAULT_GAME_ID, help=f"要建索引的遊戲（game_profiles.game_id，預設 {DEFAULT_GAME_ID}）")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.WARNING)
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(build_llm_index(args.limit, args.game))
