"""以 LLM 建置詞彙索引（無上下文輕量版，文法受 Enum 嚴格限制）。

Build the vocabulary index with an LLM (lightweight, no chapter
context), with grammar extraction strictly constrained by an Enum
whitelist loaded from the N1-N5 grammar templates.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import logging
from enum import Enum
from pathlib import Path

# 確保 sys.path 包含 backend 根目錄並載入 .env
_backend_dir = Path(__file__).resolve().parents[4]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa

import aiomysql
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.infrastructure.llm.client import LLMClient


logger = logging.getLogger(__name__)

import re

# ==========================================
# 1. 預先讀取文法庫 (為了動態生成 Enum 限制)
# ==========================================
def load_allowed_grammar() -> list[str]:
    """讀取 N1~N5 的文法模板，作為 LLM 萃取文法的嚴格限制清單。

    Load the N1-N5 grammar templates as the strict whitelist for LLM
    grammar extraction.

    Returns:
        list[str]: 允許的文法項目清單。List of allowed grammar items.
    """
    # 確保不管從哪裡執行，都能正確指到 backend/app/templates/grammar
    current_script = Path(__file__).resolve()
    backend_root = current_script.parent.parent.parent.parent
    grammar_dir = backend_root / "app" / "templates" / "grammar"
    allowed_list = []
    for level in [1, 2, 3, 4, 5]:
        p = grammar_dir / f"n{level}.j2"
        if p.exists():
            content = p.read_text(encoding='utf-8')
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    # 移除括號內的中文解釋，只保留文法本身
                    line = re.sub(r'（.*?）', '', line)
                    line = re.sub(r'\(.*?\)', '', line)
                    line = line.strip()
                    if line:
                        allowed_list.append(line)
    return allowed_list

ALLOWED_GRAMMAR = load_allowed_grammar()
ALLOWED_GRAMMAR_SET = set(ALLOWED_GRAMMAR)

if not ALLOWED_GRAMMAR:
    logger.error("❌ 找不到任何 N1~N5 文法資料，請檢查 templates/grammar 目錄。")
    sys.exit(1)

# 動態建立 Enum 類別，這是為了讓 LLM 在底層 (API JSON Schema) 受到物理級別的限制，
# 它只能輸出這些列舉出來的值，絕對無法輸出清單以外的任何自創文字。
GrammarEnum = Enum('GrammarEnum', {f"G_{i}": item for i, item in enumerate(ALLOWED_GRAMMAR)})


# ==========================================
# 2. 結構化輸出 Schema (Pydantic V2)
# ==========================================

class GrammarDetail(BaseModel):
    """
    專門用於「文法/句型」的資料結構。
    擁有極度嚴格的 Enum 限制，LLM 只能選擇我們提供的文法項目。

    Data structure dedicated to grammar/sentence patterns, strictly
    limited so the LLM can only choose provided grammar items.
    """
    term: str = Field(
        description="【極度嚴格】萃取出的文法項目。必須一字不漏地符合提供的列表，絕對不可自行創造字串或截斷。"
    )
    score: int = Field(
        ge=0, le=99, 
        description="文法的使用頻率分數 (0~99)。分數越高代表在口語中越常用、越基礎。"
    )

class WordDetail(BaseModel):
    """
    專門用於一般單字（動詞、名詞、形容詞）的資料結構。
    允許 LLM 自由發揮萃取，但必須還原為辭書型（原型）。

    Data structure for regular words (verbs, nouns, adjectives); the
    LLM extracts freely but must normalize to dictionary form.
    """
    term: str = Field(
        description="詞彙原型 (辭書型)。請自由從句子中提取，不受文法清單限制。"
    )
    score: int = Field(
        ge=0, le=99, 
        description="單字的使用頻率分數 (0~99)。80~99 為每天講的口語極高頻字，低分則為書面語。"
    )

class SentenceExtraction(BaseModel):
    """單一句子的完整語意解析結果。

    Complete semantic extraction result for one sentence.
    """
    grammar: list[GrammarDetail] = Field(
        description="從台詞中提取出的文法/句型列表。如果沒有任何文法，請回傳空列表。"
    )
    verbs: list[WordDetail] = Field(
        description="動詞(辭書型)列表。如果沒有，請回傳空列表。"
    )
    adjectives_i: list[WordDetail] = Field(
        description="い形容詞(辭書型)列表。如果沒有，請回傳空列表。"
    )
    adjectives_na: list[WordDetail] = Field(
        description="な形容詞(語幹)列表。如果沒有，請回傳空列表。"
    )
    adverbs: list[WordDetail] = Field(
        description="副詞列表（例：ちょっと、やっぱり、もしかして、なんとなく、ちゃんと）。如果沒有，請回傳空列表。"
    )
    nouns: list[WordDetail] = Field(
        description="核心名詞列表。請只提取具有語意價值的重要名詞，忽略無意義的代名詞。如果沒有，請回傳空列表。"
    )

class ScriptTermsResult(BaseModel):
    """包含句子 ID 與其解析結果的映射對象。

    Mapping object pairing a script ID with its extraction result.
    """
    script_id: int = Field(
        description="對應的台詞 ID，必須與輸入的 ID 完全一致。"
    )
    extraction: SentenceExtraction = Field(
        description="該句台詞的詳細解析內容。"
    )

class BatchExtractionResponse(BaseModel):
    """批次解析的最終回傳結果陣列。

    Final response array for a batch extraction request.
    """
    results: list[ScriptTermsResult] = Field(
        description="批次解析的結果陣列，請確保涵蓋輸入中要求的所有台詞 ID。"
    )


# ==========================================
# 3. 核心處理邏輯
# ==========================================

async def get_db_pool():
    """建立 aiomysql 連線池。Create the aiomysql connection pool.

    Returns:
        aiomysql.Pool: 已設定的連線池。Configured connection pool.
    """
    return await aiomysql.create_pool(
        host=settings.MYSQL_HOST,
        port=settings.MYSQL_PORT,
        user=settings.MYSQL_USER,
        password=settings.MYSQL_PASSWORD,
        db=settings.MYSQL_DATABASE,
        charset='utf8mb4',
        autocommit=True
    )

async def process_chapter(chapter_name: str, pool, llm_client: LLMClient, limit: int, state: dict) -> bool:
    """處理單一章節：讀取台詞、跳過已索引句、分批解析並寫入索引。

    Process one chapter: fetch dialogue lines, skip already-indexed
    ones, request LLM parsing in batches, and insert extracted terms.

    Args:
        chapter_name: 章節名稱。Chapter name.
        pool: aiomysql 連線池。aiomysql connection pool.
        llm_client: LLM 客戶端。LLM client instance.
        limit: API 呼叫上限（0 為不限）。API call limit (0 = unlimited).
        state: 跨章節共享的呼叫次數狀態。Shared call-count state.

    Returns:
        bool: True 表示已達上限應提前停止。True when the limit was hit
        and processing should stop early.
    """
    print(f"\n📚 開始處理章節: {chapter_name}")
    
    # 1. 取得該章節的所有台詞
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, dialogue FROM scripts WHERE source = 'サノバウィッチ' AND chapter = %s AND status = '1' ORDER BY id ASC",
                (chapter_name,)
            )
            rows = await cur.fetchall()
            
    if not rows:
        print(f"  ⏭️ 章節 {chapter_name} 沒有有效台詞，跳過。")
        return False
    
    # 1.5 查詢已建索引的最大 script_id，直接從下一個 ID 開始（支援斷點續跑）
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT MAX(script_id) FROM dialogue_terms_index WHERE source = 'サノバウィッチ'"
            )
            result = await cur.fetchone()
            max_indexed_id = result[0] if result and result[0] else 0
    
    if max_indexed_id > 0:
        original_count = len(rows)
        rows = [row for row in rows if row[0] > max_indexed_id]
        skipped = original_count - len(rows)
        print(f"  📝 該章節共有 {original_count} 句，已索引至 ID {max_indexed_id}，跳過 {skipped} 句，剩餘 {len(rows)} 句待處理。")
    else:
        print(f"  📝 該章節共有 {len(rows)} 句有效台詞。")
    
    if not rows:
        print(f"  ✅ 章節 {chapter_name} 所有台詞皆已完成索引，跳過。")
        return False
    
    grammar_text = "\n".join([f"- {g}" for g in ALLOWED_GRAMMAR])
    
    system_prompt = (
        "你是一位精通日文語言學與詞彙分析的專家。\n"
        "你的任務是根據提供的遊戲台詞，精準拆解出台詞中的單字與文法，並給予頻率評分 (0~99)。\n"
        "【重要評分指標】：該詞彙在「日常口語對話」中的常用程度佔了極大權重！若是日本人口語天天用的字，請給予極高分（80~99）；若是書面語、古語或口語罕用，請給予低分。\n"
        "頻率評分標準：0代表極罕見/書面語，50代表一般詞彙，99代表超基礎、每天都會講到的口語單字(如 する, 私)。\n\n"

        "【🔴 動詞・形容詞的「辭書型還原」鐵則】\n"
        "所有動詞、形容詞必須徹底還原為「辭書型 (原型)」，包括但不限於：\n"
        "- 可能形 → 辭書型（例：外せる → 外す、思える → 思う、食べられる → 食べる）\n"
        "- 使役形 → 辭書型（例：食べさせる → 食べる）\n"
        "- 口語片假名/長音 → 漢字正規化（例：ホントー → 本当、ヘーキ → 平気、クスリ → 薬、きく → 聞く）\n"
        "- 一律使用最常見的漢字寫法（例：きく → 聞く、みる → 見る），不可留平假名\n\n"

        "【🔴 嚴禁提取「文法活用後的派生形」當作獨立詞彙】\n"
        "以下情況絕對不可以當作獨立的形容詞或動詞抽出來：\n"
        "- 「～そう」形（例：嬉しそう、大変そう、申し訳なさそう）→ 這是「～そうです（推量）」文法的應用，不是獨立詞彙。只需抽出底層的形容詞原型（嬉しい、大変、申し訳ない）\n"
        "- 「～てはいけない」中的「いける」→ 這是「～てはいけません」文法的一部分，不是獨立動詞\n"
        "- 避免同一句中同時抽出底層原型和派生形（例：不可同時抽出「大変」和「大変そう」）\n\n"

        "【🔴 副詞歸類規則】\n"
        "以下詞彙是副詞，必須放入 adverbs 欄位，絕對不可放入 nouns：\n"
        "ちょっと、やっぱり/やっぱ、もしかして、なんとなく、ちゃんと、まあ、結構、全然、たぶん、とりあえず 等。\n\n"

        "【🔴 強制規定：文法萃取限制（最嚴格）】\n"
        "你萃取出的所有文法/句型 (放入 grammar 欄位)，**必須完全一字不漏地符合以下【允許的文法列表】中的項目名稱**。\n"
        "⚠️ 特別注意：絕對不可自行截斷文法名稱！例如列表中是「～くなります・～になります」，你就不可以只寫「～になります」。\n"
        "⚠️ 列表中是「～ています」，你就不可以寫成「～ている」。一字之差都不行！\n"
        "絕對不可自行創造列表中沒有的文法名稱。如果台詞中沒有對應的文法，請保持為空列表。\n"
        "（註：一般單字如動詞、名詞、副詞則不受此列表限制，請自由提取原型）。\n\n"
        "【允許的文法列表】：\n"
        f"{grammar_text}\n"
    )

    batch_size = settings.LLM_PARSE_BATCH_SIZE
    total_processed = 0
    total_terms_inserted = 0
    
    # 3. 分批請求 LLM 萃取
    for i in range(0, len(rows), batch_size):
        if limit > 0 and state["api_calls"] >= limit:
            print(f"  🛑 已達到本次執行上限 ({limit} 次 API 呼叫)，停止處理。")
            return True
            
        chunk = rows[i : i + batch_size]
        start_id = chunk[0][0]
        end_id = chunk[-1][0]
        
        target_ids = [row[0] for row in chunk]
        
        # 僅傳遞目標 20 句
        chunk_context = ""
        for row in chunk:
            clean_dialogue = row[1].replace('\n', '') if row[1] else ""
            chunk_context += f"[{row[0]}] {clean_dialogue}\n"

        user_prompt = (
            f"請解析以下共 {len(chunk)} 句的台詞：\n\n"
            f"{chunk_context}\n"
            f"目標 ID 清單：{target_ids}\n\n"
            "請將這幾句的解析結果嚴格依照 JSON 格式輸出，不要遺漏任何一個 ID。"
        )
        
        # 估算 Token 數量（Gemini 日文約 1 token / 1.5 字元，加上 JSON Schema 開銷）
        schema_json = json.dumps(BatchExtractionResponse.model_json_schema(), ensure_ascii=False)
        total_input_chars = len(system_prompt) + len(user_prompt) + len(schema_json)
        estimated_tokens = int(total_input_chars / 1.5)
        print(f"  🔄 正在請求 LLM 解析 ID {start_id} ~ {end_id} (共 {len(chunk)} 句, 預估 Input ≈ {estimated_tokens:,} tokens)...", end="", flush=True)
        
        try:
            result = await llm_client.generate_structured_data(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_schema=BatchExtractionResponse.model_json_schema()
            )
            print(" ✅ 成功")
            
            # 4. 解析 JSON 並寫入 MySQL
            response_data = BatchExtractionResponse.model_validate(result.parsed_data)
            model_name = llm_client._formatted_model_name
            
            insert_data = []
            for item in response_data.results:
                s_id = item.script_id
                
                for term in item.extraction.grammar:
                    if term.term not in ALLOWED_GRAMMAR_SET:
                        print(f"    ⚠️ 丟棄自創文法 (幻覺): {term.term} (ID: {s_id})")
                        continue
                    insert_data.append((s_id, 'サノバウィッチ', term.term, 'grammar', term.score, model_name))
                    
                # 動詞等一般單字則是 WordDetail，term 直接是字串
                for term in item.extraction.verbs:
                    insert_data.append((s_id, 'サノバウィッチ', term.term, 'verb', term.score, model_name))
                for term in item.extraction.nouns:
                    insert_data.append((s_id, 'サノバウィッチ', term.term, 'noun', term.score, model_name))
                for term in item.extraction.adjectives_i:
                    insert_data.append((s_id, 'サノバウィッチ', term.term, 'adjective_i', term.score, model_name))
                for term in item.extraction.adjectives_na:
                    insert_data.append((s_id, 'サノバウィッチ', term.term, 'adjective_na', term.score, model_name))
                for term in item.extraction.adverbs:
                    insert_data.append((s_id, 'サノバウィッチ', term.term, 'adverb', term.score, model_name))
            
            if insert_data:
                async with pool.acquire() as conn:
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            "INSERT INTO dialogue_terms_index (script_id, source, term_lemma, term_type, frequency_score, model_name) VALUES (%s, %s, %s, %s, %s, %s)",
                            insert_data
                        )
                total_terms_inserted += len(insert_data)
                
            total_processed += len(chunk)
            state["api_calls"] += 1
            
        except Exception as e:
            print(f" ❌ 失敗: {str(e)}")
            logger.exception("LLM 解析或寫入失敗")
            continue
            
    print(f"  🏁 章節 {chapter_name} 完成。處理 {total_processed} 句，新增 {total_terms_inserted} 個詞彙索引。")
    return False

async def build_llm_index(limit: int = 0):
    """主流程：初始化 LLM、清理舊索引、逐章節建置索引。

    Main flow: initialize the LLM client, optionally clear the old
    index, then build the index chapter by chapter.

    Args:
        limit: API 呼叫上限（0 為不限）。API call limit (0 = unlimited).
    """
    print("🚀 啟動 LLM 語意解析索引建置腳本 (無上下文輕量版 + 物理級 Enum 嚴格限制)...")
    
    try:
        llm_client = LLMClient()
    except Exception as e:
        print(f"初始化 LLMClient 失敗: {e}")
        return

    print("-" * 50)
    print("📊 當前執行配置：")
    print(f"  🔹 LLM 模型：{llm_client._formatted_model_name}")
    print(f"  🔹 批次大小 (Batch Size)：{settings.LLM_PARSE_BATCH_SIZE}")
    print(f"  🔹 執行上限 (Limit)：{'無限制' if limit == 0 else f'{limit} 次請求'}")
    print(f"  🔹 物理級文法 Enum 限制：共載入 {len(ALLOWED_GRAMMAR)} 條嚴格句型")
    print("-" * 50)

    pool = await get_db_pool()
    
    start_time = time.time()
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if limit == 0:
                    print("🧹 清除舊有 [サノバウィッチ] 索引資料以防重複...")
                    await cur.execute("DELETE FROM dialogue_terms_index WHERE source = 'サノバウィッチ'")
                else:
                    print("⚠️ 啟用 --limit 模式，跳過清除舊有索引 (支援接續執行)。")
                
                print("🔍 掃描所有有效章節...")
                await cur.execute(
                    "SELECT chapter FROM scripts WHERE source = 'サノバウィッチ' AND status = '1' AND chapter IS NOT NULL GROUP BY chapter ORDER BY MIN(id) ASC"
                )
                chapters = await cur.fetchall()
        
        print(f"總共找到 {len(chapters)} 個章節。")
        
        state = {"api_calls": 0}
        for (chapter_name,) in chapters:
            stop_early = await process_chapter(chapter_name, pool, llm_client, limit, state)
            if stop_early:
                break
            
    finally:
        pool.close()
        await pool.wait_closed()
        
    elapsed = time.time() - start_time
    print(f"🎉 全部預處理完成！共花費 {elapsed:.2f} 秒。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM 語意解析索引建置 (無上下文輕量版 + 物理級 Enum 嚴格限制)")
    parser.add_argument("--limit", type=int, default=0, help="限制本次執行的 API 呼叫次數 (0 代表全部執行)")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.WARNING)
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(build_llm_index(args.limit))
