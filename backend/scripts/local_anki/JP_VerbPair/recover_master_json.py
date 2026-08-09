"""從 Cloze 子卡片重建母卡片 JSON 資料的救援腳本。

Recovery script that rebuilds master-card Intransitive/Transitive_Data_JSON
fields by scanning all JP_VerbPair_Cloze_Dark child notes.
"""

import os
import sys
from pathlib import Path
import asyncio
import logging
import json
import html
from collections import defaultdict

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

backend_dir = str(Path(__file__).resolve().parents[3])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
import scripts.common.env  # noqa

from app.infrastructure.anki.client import AnkiClient
from app.services.card_service import CardService
from app.infrastructure.anki.json_modifier import AnkiJsonFieldManager

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

async def main():
    """腳本主入口：掃描 Cloze 卡片、重建並寫回母卡片 JSON。

    Script entry point: scan cloze notes, rebuild the per-master JSON data
    structures and write them back to Anki.
    """
    client = AnkiClient()
    card_service = CardService(client, None)

    try:
        query = 'note:"JP_VerbPair_Cloze_Dark"'
        logger.info(f"🔍 開始搜尋所有 Cloze 子卡片進行資料重建 (Query: {query})")
        
        cloze_notes = await client._invoke('findNotes', query=query)
        if not cloze_notes:
            logger.info("❌ 找不到任何 Cloze 卡片！")
            return
            
        logger.info(f"✅ 共找到 {len(cloze_notes)} 張 Cloze 筆記，開始分析資料...\n")
        
        # 由於筆記可能很多，分批取得 info 避免負載過大
        chunk_size = 500
        all_info = []
        for i in range(0, len(cloze_notes), chunk_size):
            chunk = cloze_notes[i:i+chunk_size]
            info = await client._invoke('notesInfo', notes=chunk)
            all_info.extend(info)
            
        # master_note_id -> {"intransitive": [], "transitive": []}
        master_map = defaultdict(lambda: {"intransitive": [], "transitive": []})
        
        for info in all_info:
            try:
                fields = info['fields']
                cloze_note_id = info['noteId']
                
                audio = fields.get('Audio', {}).get('value', '')
                avatar = fields.get('Avatar', {}).get('value', '')
                speaker = fields.get('Speaker', {}).get('value', '')
                text = fields.get('Full_Sentence_HTML', {}).get('value', '')
                
                context_str = fields.get('Context_Note_ID', {}).get('value', '0')
                master_str = fields.get('Master_Note_ID', {}).get('value', '0')
                
                # 安全轉型
                if not context_str.strip(): context_str = '0'
                if not master_str.strip(): master_str = '0'
                context_note_id = int(context_str)
                master_note_id = int(master_str)
                
                if master_note_id == 0:
                    continue
                    
                # 解析 Verb_Pair_JSON 決定是 intransitive 還是 transitive
                verb_pair_str = fields.get('Verb_Pair_JSON', {}).get('value', '')
                used_type = "intransitive"
                if verb_pair_str:
                    try:
                        # 處理 Anki 產生的 HTML 雜訊
                        clean_str = html.unescape(verb_pair_str).replace('<div>', '').replace('</div>', '').replace('<br>', '').strip()
                        verb_obj = json.loads(clean_str)
                        used_type = verb_obj.get("used", "intransitive").lower()
                    except:
                        pass
                
                item = {
                    "audio": audio,
                    "avatar": avatar,
                    "speaker": speaker,
                    "text": text,
                    "context_note_id": context_note_id,
                    "cloze_note_id": cloze_note_id
                }
                
                if used_type == "transitive":
                    master_map[master_note_id]["transitive"].append(item)
                else:
                    master_map[master_note_id]["intransitive"].append(item)
                    
            except Exception as e:
                logger.error(f"解析 Cloze 筆記 {info.get('noteId')} 時出錯: {e}")
                
        logger.info(f"📌 成功重建 {len(master_map)} 張母卡片的資料結構，準備寫入 Anki...")
        
        success_count = 0
        error_count = 0
        
        # 將重建的資料寫回 Anki，這會經過我們修復過的 update_field (html.escape)，徹底解決紅字失效問題
        for idx, (m_id, data_dict) in enumerate(master_map.items(), 1):
            try:
                # 按照 cloze_note_id 排序以維持順序
                in_list = sorted(data_dict["intransitive"], key=lambda x: x["cloze_note_id"])
                tr_list = sorted(data_dict["transitive"], key=lambda x: x["cloze_note_id"])
                
                await AnkiJsonFieldManager.update_field(card_service, m_id, "Intransitive_Data_JSON", in_list)
                await AnkiJsonFieldManager.update_field(card_service, m_id, "Transitive_Data_JSON", tr_list)
                
                success_count += 1
                if idx % 10 == 0:
                    logger.info(f"⏳ 寫入進度: {idx}/{len(master_map)}")
            except Exception as e:
                logger.error(f"寫入母卡片 {m_id} 時出錯: {e}")
                error_count += 1
                
        logger.info("\n🎉 救援與修復任務完成！")
        logger.info(f"   ✅ 成功覆寫: {success_count} 張母卡片")
        if error_count > 0:
            logger.info(f"   ❌ 失敗: {error_count} 張母卡片")
            
    finally:
        await client.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
