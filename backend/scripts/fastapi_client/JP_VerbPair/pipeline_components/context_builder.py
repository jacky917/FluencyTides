"""上下文對話萃取與建構模組。

Context-dialogue extraction and construction: fetches lines from MySQL,
merges unclosed Visual Novel dialogue, and maps audio/avatar paths into
the context_dialogue array consumed by the API.

負責從 MySQL 中撈取台詞，進行 Visual Novel (VN) 的未閉合對話合併，
並映射對應的音檔與頭像路徑，產生供 API 使用的 context_dialogue 陣列。
"""

import re
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

class ContextBuilder:
    """上下文對話組裝器。Builder of context_dialogue arrays."""

    async def build(
        self,
        session: AsyncSession,
        script_id: int,
        chapter: str,
        voice_dir: Path,
        avatar_dir: Path,
        source_game: str,
        context_prev: int,
        context_next: int
    ) -> list[dict]:
        """撈取上下文，進行 VN 對話合併，並映射本地媒體路徑（不進行上傳）。

        Fetch surrounding lines, merge unclosed VN dialogue, and map local
        media paths (no uploading). Returns the context_dialogue array for
        the API.

        Args:
            session: 語料庫 async session。Corpus async session.
            script_id: 目標台詞主鍵。Target line's primary key.
            chapter: 目標台詞章節。Target line's chapter.
            voice_dir: 音檔本地目錄。Local voice-file directory.
            avatar_dir: 頭像本地目錄。Local avatar directory.
            source_game: 檔名前綴用的遊戲代號。Game id for filename prefixes.
            context_prev: 往前撈取的行數。Lines fetched before the target.
            context_next: 往後撈取的行數。Lines fetched after the target.

        Returns:
            list[dict]: 供 API 使用的 context_dialogue 陣列。The
            context_dialogue array consumed by the API.
        """
        
        # 1. 撈取上下文對話
        context_query = text("""
            SELECT id, role_name, dialogue, audio_file
            FROM scripts
            WHERE id BETWEEN :start_id AND :end_id
            AND chapter = :chapter
            ORDER BY id ASC
        """)
        context_result = await session.execute(context_query, {
            "start_id": script_id - context_prev,
            "end_id": script_id + context_next,
            "chapter": chapter
        })
        
        context_lines = context_result.fetchall()
        
        # 2. 初步映射欄位與媒體標籤
        raw_turns = []
        for row in context_lines:
            line_id, speaker, raw_dialogue, audio_file = row[0], row[1], row[2], row[3]
            display_speaker = "-" if not speaker or speaker == "None" else speaker
            
            # 移除文字中 [] 內的注音標記 (如: [かい]海[どう]道 -> 海道)
            dialogue = re.sub(r'\[.*?\]', '', raw_dialogue) if raw_dialogue else ""
            
            audio_tag = ""
            if audio_file:
                audio_path = voice_dir / f"{audio_file}.mp3"
                if audio_path.exists():
                    audio_tag = f"[sound:{source_game}_{audio_path.name}]"

            avatar_tag = "none"
            if display_speaker != "-":
                avatar_path = avatar_dir / f"{display_speaker}.jpg"
                if avatar_path.exists():
                    avatar_tag = f"{source_game}_{avatar_path.name}"

            raw_turns.append({
                "line_id": line_id,
                "speaker": display_speaker,
                "text": dialogue,
                "audio": audio_tag,
                "avatar": avatar_tag,
                "is_target": (line_id == script_id)
            })

        # 3. 合併未閉合的對話 (Visual Novel Script Merging)
        context_dialogue = []
        for turn in raw_turns:
            if not context_dialogue:
                context_dialogue.append(turn)
                continue
                
            last_block = context_dialogue[-1]
            last_text = last_block["text"].strip()
            
            is_unclosed_dialogue = (
                (last_text.startswith("「") and not last_text.endswith("」")) or
                (last_text.startswith("『") and not last_text.endswith("』"))
            )
            
            if turn["speaker"] == "-" and is_unclosed_dialogue:
                last_block["text"] += turn["text"]
                last_block["is_target"] = last_block["is_target"] or turn["is_target"]
                if turn["audio"]:
                    last_block["audio"] = turn["audio"]
            else:
                context_dialogue.append(turn)
        
        # 4. 移除內部使用的 line_id，產出純淨的 payload 陣列
        for block in context_dialogue:
            block.pop("line_id", None)
            
        return context_dialogue
