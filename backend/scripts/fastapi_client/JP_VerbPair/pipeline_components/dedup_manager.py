"""去重與生成準備的控制器模組。

Deduplication and generation-preparation controller: coordinates
log_repository and context_builder behind a unified prepare/record API.

負責協調 log_repository 與 context_builder，
提供對外統一的 prepare_generation 與 record_success 介面。

去重分兩層（docs/wip/dedup_canonical_lemma_FIX_2026-09-02.md §3）：
1. **鍵層**：``(script_id, verb_lemma, project)``——``verb_lemma`` 必須是
   母卡標準表層去標音的正規表記（呼叫端傳 ``kd["target_lemma"]`` 而非
   搜尋關鍵字），否則同句會因拼寫不同被重複生成。
2. **文字層**：候選句正規化後與該動詞已記錄的台詞比對，擋住語料中
   「同一句台詞、不同 script_id」的分身。
Two dedup layers: the key layer (canonical verb lemma) and the text layer
(normalized dialogue vs. every logged line of the verb).
"""

import logging
from pathlib import Path
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.common.database.log_repository import GeneratedLogRepository
from scripts.common.sentence_normalize import normalize_sentence
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
        context_next: int = 5,
        *,
        project: str,
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
            project: generated_sentences_log 的專案識別
                （log_repository.KNOWN_PROJECTS）。Project identifier for
                generated_sentences_log.
        """
        self.session = session
        self.voice_dir = voice_dir
        self.avatar_dir = avatar_dir
        self.source_game = source_game
        self.context_prev = context_prev
        self.context_next = context_next
        self.project = project

        self.repo = GeneratedLogRepository()
        self.builder = ContextBuilder()
        # 文字層去重集合：verb_lemma -> 正規化台詞集合。每個動詞第一次
        # 用到時從 DB lazy 載入，本次執行中放行的句子即時加入（dry-run
        # 也一樣，否則同次執行內的分身擋不住）。
        # Text-dedup sets per verb, lazily loaded then extended in-run.
        self._seen_texts: dict[str, set[str]] = {}

    async def _is_text_duplicate(self, verb_lemma: str, dialogue: str) -> bool:
        """候選句是否為該動詞已記錄台詞的同文分身；不是則登記為已見。

        Whether the candidate is a text twin of a line already logged for
        this verb; if not, register it as seen.

        Args:
            verb_lemma: 動詞正規表記。Canonical verb lemma.
            dialogue: 候選句原文。Raw candidate dialogue.

        Returns:
            bool: True 表示重複應跳過。True when the sentence is a duplicate.
        """
        key = normalize_sentence(dialogue)
        if not key:
            return False
        seen = self._seen_texts.get(verb_lemma)
        if seen is None:
            logged = await self.repo.get_logged_dialogues(self.session, verb_lemma, project=self.project)
            seen = {normalize_sentence(d) for d in logged}
            seen.discard("")
            self._seen_texts[verb_lemma] = seen
        if key in seen:
            return True
        seen.add(key)
        return False

    async def prepare_generation(
        self, script_id: int, verb_lemma: str, chapter: str, *, dialogue: str | None = None,
    ) -> list[dict] | None:
        """準備生成卡片。檢查是否重複或已達到失敗上限，若允許則回傳上下文對話。

        Prepare a generation: check for duplicates or exhausted failures,
        returning the context dialogue when generation is allowed.

        Args:
            script_id: 目標台詞主鍵。Target line's primary key.
            verb_lemma: 動詞正規表記（母卡標準表層去標音，**非**搜尋
                關鍵字）。Canonical verb lemma, never the search keyword.
            chapter: 目標台詞章節。Target line's chapter.
            dialogue: 候選句原文；有給時啟用文字層去重（同文異 id 的分身
                視為重複）。Raw candidate dialogue; enables text-level
                dedup when provided.

        Returns:
            list[dict] | None: 允許生成時回傳上下文對話；重複/失敗達上限時
            回傳 ``None``。Context dialogue if allowed, else ``None``.
        """
        record = await self.repo.get_record(self.session, script_id, verb_lemma, project=self.project)

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

        # 文字層去重：鍵層放行後，再擋「同一句台詞、不同 script_id」的分身。
        # 有紀錄（含軟刪除復活）的句子本身已在集合中，不能誤擋自己，故只
        # 對「無紀錄」的候選檢查。
        # Text-level dedup runs only for candidates without a record: a
        # restored soft-deleted line is already in the set and must not
        # block itself.
        if dialogue is not None and record is None:
            if await self._is_text_duplicate(verb_lemma, dialogue):
                logger.info(
                    f"⏭️ 同文去重：與已記錄台詞文字相同（不同 script_id），跳過: "
                    f"script_id={script_id}, verb_lemma='{verb_lemma}'"
                )
                return None

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

    async def record_failure(
        self, script_id: int, verb_lemma: str, chapter: str, master_note_id: int, llm_model: str,
        *, search_keyword: str | None = None,
    ) -> None:
        """紀錄生成失敗。

        Record one generation failure.

        Args:
            script_id: 目標台詞主鍵。Target line's primary key.
            verb_lemma: 動詞正規表記。Canonical verb lemma.
            chapter: 章節。Chapter.
            master_note_id: 母卡 note id。Master note id.
            llm_model: 使用的 LLM 模型標籤。LLM model label used.
            search_keyword: 實際命中的搜尋關鍵字（與 verb_lemma 相同時
                自動存 NULL）。Matched search keyword; stored as NULL when
                identical to verb_lemma.
        """
        await self.repo.increment_failure_count(
            self.session, script_id, verb_lemma, self.source_game, chapter, master_note_id, llm_model,
            project=self.project, search_keyword=_keyword_or_none(search_keyword, verb_lemma),
        )
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
        llm_model: str,
        search_keyword: str | None = None,
    ) -> None:
        """生成成功後，寫入 MySQL 紀錄（或恢復軟刪除狀態）。

        After a successful generation, write the MySQL record (or restore a
        soft-deleted one).

        Args:
            script_id: 目標台詞主鍵。Target line's primary key.
            verb_lemma: 動詞正規表記。Canonical verb lemma.
            chapter: 章節。Chapter.
            master_note_id: 母卡 note id。Master note id.
            context_note_id: Context 子卡 note id。Context child note id.
            cloze_note_id: Cloze 子卡 note id。Cloze child note id.
            llm_model: 使用的 LLM 模型標籤。LLM model label used.
            search_keyword: 實際命中的搜尋關鍵字（與 verb_lemma 相同時
                自動存 NULL）。Matched search keyword; NULL when identical
                to verb_lemma.
        """
        record_data = {
            "script_id": script_id,
            "verb_lemma": verb_lemma,
            "source": self.source_game,
            "chapter": chapter,
            "master_note_id": master_note_id,
            "context_note_id": context_note_id,
            "cloze_note_id": cloze_note_id,
            "llm_model": llm_model,
            "search_keyword": _keyword_or_none(search_keyword, verb_lemma),
        }
        await self.repo.create_or_restore_record(self.session, record_data, project=self.project)
        logger.info(f"✅ 成功寫入去重紀錄: script_id={script_id}, verb_lemma='{verb_lemma}'")


def _keyword_or_none(search_keyword: str | None, verb_lemma: str) -> str | None:
    """關鍵字與正規表記相同時回 None（欄位只記「有差異」的命中）。

    Return None when the keyword equals the lemma; the column only records
    hits that differ from the canonical spelling.
    """
    if not search_keyword or search_keyword == verb_lemma:
        return None
    return search_keyword
