"""去重與生成準備的控制器模組。

Deduplication and generation-preparation controller: coordinates
log_repository and context_builder behind a unified prepare/record API.

負責協調 log_repository 與 context_builder，
提供對外統一的 prepare_generation 與 record_success 介面。
"""

import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.common.database.log_repository import GeneratedLogRepository
from scripts.fastapi_client.JP_VerbPair.pipeline_components.context_builder import ContextBuilder

logger = logging.getLogger(__name__)

class DedupManager:
    """去重與上下文準備控制器。Dedup and context-preparation controller."""

    def __init__(
        self,
        session: AsyncSession,
        voice_dir: Path,
        avatar_dir: Path,
        source_game: str,
        context_prev: int = 5,
        context_next: int = 5
    ):
        """初始化 DedupManager。

        Initialize the DedupManager.

        Args:
            session: 語料庫 async session。Corpus async session.
            voice_dir: 音檔本地目錄。Local voice-file directory.
            avatar_dir: 頭像本地目錄。Local avatar directory.
            source_game: 遊戲來源代號。Source game id.
            context_prev: 上下文往前行數。Context lines before the target.
            context_next: 上下文往後行數。Context lines after the target.
        """
        self.session = session
        self.voice_dir = voice_dir
        self.avatar_dir = avatar_dir
        self.source_game = source_game
        self.context_prev = context_prev
        self.context_next = context_next
        
        self.repo = GeneratedLogRepository()
        self.builder = ContextBuilder()

    async def prepare_generation(self, script_id: int, verb_lemma: str, chapter: str) -> list[dict] | None:
        """準備生成卡片。檢查是否重複或已達到失敗上限，若允許則回傳上下文對話。

        Prepare a generation: check for duplicates or exhausted failures,
        returning the context dialogue when generation is allowed.

        Args:
            script_id: 目標台詞主鍵。Target line's primary key.
            verb_lemma: 動詞字典形。Verb lemma.
            chapter: 目標台詞章節。Target line's chapter.

        Returns:
            list[dict] | None: 允許生成時回傳上下文對話；重複/失敗達上限時
            回傳 ``None``。Context dialogue if allowed, else ``None``.
        """
        record = await self.repo.get_record(self.session, script_id, verb_lemma)
        
        if record:
            if record.get("failure_count", 0) >= 1:
                logger.warning(f"🚫 該句子生成失敗次數已達 {record['failure_count']} 次，自動跳過: script_id={script_id}, verb_lemma='{verb_lemma}'")
                return None
            
            if record.get("has_been_generated"):
                if not record["is_deleted"]:
                    logger.info(f"⏭️ 已存在有效紀錄，拒絕重複生成: script_id={script_id}, verb_lemma='{verb_lemma}'")
                    return None
                else:
                    logger.info(f"♻️ 發現被軟刪除的紀錄，允許重新生成: script_id={script_id}, verb_lemma='{verb_lemma}' (已反覆刪除 {record['delete_count']} 次)")
            else:
                logger.info(f"✨ 發現曾失敗過的新組合 (失敗 {record.get('failure_count', 0)} 次)，允許生成: script_id={script_id}, verb_lemma='{verb_lemma}'")
        else:
            logger.info(f"✨ 發現新組合，允許生成: script_id={script_id}, verb_lemma='{verb_lemma}'")
            
        context_dialogue = await self.builder.build(
            session=self.session,
            script_id=script_id,
            chapter=chapter,
            voice_dir=self.voice_dir,
            avatar_dir=self.avatar_dir,
            source_game=self.source_game,
            context_prev=self.context_prev,
            context_next=self.context_next
        )
        
        return context_dialogue

    async def record_failure(self, script_id: int, verb_lemma: str, chapter: str, master_note_id: int, llm_model: str) -> None:
        """紀錄生成失敗。

        Record one generation failure.

        Args:
            script_id: 目標台詞主鍵。Target line's primary key.
            verb_lemma: 動詞字典形。Verb lemma.
            chapter: 章節。Chapter.
            master_note_id: 母卡 note id。Master note id.
            llm_model: 使用的 LLM 模型標籤。LLM model label used.
        """
        await self.repo.increment_failure_count(self.session, script_id, verb_lemma, self.source_game, chapter, master_note_id, llm_model)
        logger.warning(f"⚠️ 紀錄一次生成失敗: script_id={script_id}, verb_lemma='{verb_lemma}'")

    async def record_success(
        self,
        script_id: int,
        verb_lemma: str,
        chapter: str,
        master_note_id: int,
        context_note_id: int | None = None,
        cloze_note_id: int | None = None,
        *,
        llm_model: str
    ) -> None:
        """生成成功後，寫入 MySQL 紀錄（或恢復軟刪除狀態）。

        After a successful generation, write the MySQL record (or restore a
        soft-deleted one).

        Args:
            script_id: 目標台詞主鍵。Target line's primary key.
            verb_lemma: 動詞字典形。Verb lemma.
            chapter: 章節。Chapter.
            master_note_id: 母卡 note id。Master note id.
            context_note_id: Context 子卡 note id。Context child note id.
            cloze_note_id: Cloze 子卡 note id。Cloze child note id.
            llm_model: 使用的 LLM 模型標籤。LLM model label used.
        """
        record_data = {
            "script_id": script_id,
            "verb_lemma": verb_lemma,
            "source": self.source_game,
            "chapter": chapter,
            "master_note_id": master_note_id,
            "context_note_id": context_note_id,
            "cloze_note_id": cloze_note_id,
            "llm_model": llm_model
        }
        await self.repo.create_or_restore_record(self.session, record_data)
        logger.info(f"✅ 成功寫入去重紀錄: script_id={script_id}, verb_lemma='{verb_lemma}'")
