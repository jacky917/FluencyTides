"""Cloze 挖空定位與對話組裝的共用純函數模組。

本模組自 ``jp_verb_pair_handler.py`` 抽出，供 JP_VerbPair 與 JP_CoreVerb
兩個 Handler 共用，維持單一事實來源：

1. ``assemble_dialog_turns``：依 LLM 回傳的翻譯映射，
   在 Python 端確定性地組裝 LINE 風格對話結構（含截斷與氣泡對齊）。
2. ``position_cloze``：以「從右往左定位」策略在原文中執行挖空，
   內建助詞剝除與 ``target_particle_verb`` 整體匹配兩層 Fallback，
   全數失敗時 fail-fast 拋出 ``ClozePositioningError``；
   另支援可選的 verified span 交叉驗證（防止 LLM 挖到污染位置）。

Shared pure functions for cloze positioning and dialog assembly.

Extracted from ``jp_verb_pair_handler.py`` and shared by the JP_VerbPair
and JP_CoreVerb handlers as a single source of truth:
``assemble_dialog_turns`` deterministically assembles the LINE-style
dialog structure from the LLM translation map (with truncation and
bubble alignment); ``position_cloze`` performs right-to-left cloze
positioning with particle-stripping and whole-phrase fallbacks,
fail-fasting with ``ClozePositioningError`` when everything fails, plus
optional verified-span cross-validation.
"""

import logging
import re

from app.core.exceptions import ClozePositioningError
from app.infrastructure.utils.llm_logger import log_llm_failure

logger = logging.getLogger(__name__)


def assemble_dialog_turns(
    context_dialogue: list[dict],
    translation_map: dict[int, str],
) -> list[dict]:
    """在 Python 中組裝確定性的對話結構 (Deterministic Dialog)。

    Assemble the deterministic dialog structure in Python.

    依據 LLM 決定保留的句子範圍（``translation_map`` 的 id 集合）截斷上下文，
    並為每一句配置說話者氣泡對齊方向、抽取音檔純檔名、對齊翻譯。

    Truncates the context to the sentence range the LLM kept (the id set
    of ``translation_map``), assigns bubble alignment per speaker,
    extracts bare audio filenames, and aligns translations.

    Args:
        context_dialogue: 原始上下文對話陣列，每個元素為包含
            speaker / text / is_target / audio / avatar 的 dict。Raw
            dialog list; each item has speaker / text / is_target /
            audio / avatar.
        translation_map: LLM 回傳的翻譯映射，key 為原始對話索引 (0-based)、
            value 為該句的繁體中文翻譯。LLM translation map keyed by the
            0-based dialog index, valued with the Traditional Chinese
            translation.

    Returns:
        list[dict]: 組裝完成的對話回合清單，每個元素包含
            speaker / avatar / text / translation / align / is_target /
            audio。Assembled dialog turns with speaker / avatar / text /
            translation / align / is_target / audio.
    """
    dialog_turns = []

    if not translation_map:
        # 如果 LLM 什麼都沒回傳，只好印出全部 (雖然理論上不該發生)
        truncated_dialogue = context_dialogue
        start_offset = 0
    else:
        # 找出 LLM 決定保留的句子範圍
        kept_ids = sorted(list(translation_map.keys()))
        min_id = kept_ids[0]
        max_id = kept_ids[-1]

        # 確保目標句一定被保留
        target_id = next((i for i, b in enumerate(context_dialogue) if b.get("is_target")), -1)
        if target_id != -1:
            min_id = min(min_id, target_id)
            max_id = max(max_id, target_id)

        # 安全邊界檢查
        min_id = max(0, min_id)
        max_id = min(len(context_dialogue) - 1, max_id)

        # 截斷多餘的上下文，只保留 min_id 到 max_id 之間的句子
        truncated_dialogue = context_dialogue[min_id:max_id + 1]
        start_offset = min_id

    # 找出目標句的說話者
    target_speaker = None
    for block in truncated_dialogue:
        if block.get("is_target"):
            target_speaker = block.get("speaker", "-")
            break

    for i_offset, block in enumerate(truncated_dialogue):
        original_id = start_offset + i_offset
        speaker = block.get("speaker", "-")
        # 將換行替換為空格，避免 JSON 中出現控制字元
        # 不可使用 <br>，因為 Anki 的 {{Field}} 會將其渲染為真實的 HTML 換行，
        # 導致 textContent 讀取時重新產生 \n，使 JSON.parse 失敗
        text = block.get("text", "").replace("\n", " ")
        is_target = block.get("is_target", False)
        raw_audio = block.get("audio", "")
        avatar = block.get("avatar", "none")

        # 從 [sound:xxx.mp3] 格式中提取純檔名
        # Anki 的 {{Field}} 渲染會自動處理 [sound:...] 標記並將其轉換為
        # HTML <audio> 元素，這會破壞 JSON 結構。因此只存檔名，
        # 由前端 JS 自行組裝 audio 元素
        audio_filename = ""
        if raw_audio:
            sound_match = re.search(r'\[sound:(.+?)\]', raw_audio)
            if sound_match:
                audio_filename = sound_match.group(1)

        # 配置對齊方向：目標句的說話人在右邊，其他人皆在左邊。旁白 ("-") 永遠置中。
        if speaker == "-":
            align = "center"
        elif speaker == target_speaker:
            align = "right"
        else:
            align = "left"

        # 組合原文與翻譯
        translation = translation_map.get(original_id, "(翻譯遺失)").replace("\n", " ")

        dialog_turns.append({
            "speaker": speaker,
            "avatar": avatar,
            "text": text,
            "translation": translation,
            "align": align,
            "is_target": is_target,
            "audio": audio_filename
        })

    return dialog_turns


def _validate_span_overlap(
    positions: list[tuple[int, int]],
    verified_span: tuple[int, int] | None,
    target_sentence: str,
) -> None:
    """檢查挖空位置與腳本側 verified span 是否有重疊（交叉驗證）。

    Cross-validate that cloze positions overlap the verified span.

    腳本側以形態素分析器驗證過的目標動詞字元 span，
    與 LLM 決定的挖空位置若完全不重疊，代表 LLM 挖到了
    同形污染詞（如複合動詞前項或補助動詞），必須 fail-fast 拒絕建卡。

    If the morphologically verified target-verb span does not overlap
    any LLM cloze position, the LLM blanked a homographic contaminant
    (e.g. a compound-verb prefix or auxiliary verb), so card creation is
    rejected fail-fast.

    Args:
        positions: 已定位完成的挖空位置清單，每個元素為 (start, end)。
            Located cloze positions as (start, end) tuples.
        verified_span: 腳本側傳入的目標動詞字元 span (start, end)；為 None
            時跳過檢查。Script-side verified char span; None skips the
            check.
        target_sentence: 目標句原文（僅用於錯誤訊息）。Original sentence
            (error message only).

    Raises:
        ClozePositioningError: 挖空位置與 verified span 完全不重疊時拋出。
            When no cloze position overlaps the verified span.
    """
    if verified_span is None:
        return

    span_start, span_end = verified_span
    if any(start < span_end and end > span_start for start, end in positions):
        return

    logger.warning(
        "Cloze 挖空位置與 verified span 交叉驗證失敗: positions=%s, span=%s。原文: '%s'",
        positions, verified_span, target_sentence
    )
    raise ClozePositioningError(
        f"Cloze 挖空位置與目標動詞 span 交叉驗證失敗，拒絕建立卡片。"
        f"LLM 挖空位置 {positions} 與腳本側驗證的目標動詞 span "
        f"({span_start}, {span_end}) 完全不重疊，疑似挖到同形污染詞。"
        f"原文='{target_sentence}'"
    )


def position_cloze(
    target_sentence: str,
    cloze_blanks: list[str],
    target_particle_verb: str,
    *,
    task_name: str,
    model_name: str,
    prompt_text: str,
    raw_response: str,
    verified_span: tuple[int, int] | None = None,
) -> tuple[str, str]:
    """在目標句原文中定位並執行 Cloze 挖空（fail-fast）。

    Locate and apply cloze blanks in the original sentence (fail-fast).

    LLM 負責「決定挖空哪裡」(提供要挖空的子字串清單)，
    Python 負責「精準執行挖空」(在原文中定位並替換)，
    徹底消除 LLM 自行改寫原文的風險。

    採用「從右往左定位」策略處理挖空：日文的動詞位於句尾，
    搭配的助詞是離動詞最近的那個。因此先找動詞位置（最右邊的 blank），
    再往左找最近的助詞，即可正確處理助詞重複出現的情況。

    定位失敗時依序嘗試兩層 Fallback：
    1. 剝除 LLM 腦補的開頭助詞後重新定位。
    2. 以 ``target_particle_verb`` 整體匹配。
    全數失敗即記錄 JSONL 失敗日誌並拋出 ``ClozePositioningError``。

    The LLM decides where to blank (a list of substrings); Python
    executes the blanking precisely, eliminating the risk of the LLM
    rewriting the sentence. Uses a right-to-left strategy (Japanese
    verbs sit at the sentence end; the relevant particle is the nearest
    one to the left). On failure, two fallbacks are tried in order:
    (1) strip LLM-hallucinated leading particles and retry; (2) match
    ``target_particle_verb`` as a whole. If all fail, a JSONL failure
    log is written and ``ClozePositioningError`` is raised.

    Args:
        target_sentence: 目標句原文。Original target sentence.
        cloze_blanks: LLM 回傳的挖空子字串清單。Substrings to blank,
            returned by the LLM.
        target_particle_verb: 完整的目標搭配詞（助詞+動詞），作為最終
            Fallback。Full target collocation (particle + verb), used as
            the final fallback.
        task_name: 失敗日誌 (JSONL) 使用的任務名稱，例如
            "JP_VerbPair_Cloze"。Task name for the JSONL failure log.
        model_name: 本次 LLM 呼叫實際使用的模型名稱（記錄失敗日誌用）。
            LLM model name actually used (for the failure log).
        prompt_text: 本次 LLM 呼叫的完整提示詞（記錄失敗日誌用）。Full
            prompt of this LLM call (for the failure log).
        raw_response: LLM 的原始回應內容（記錄失敗日誌用）。Raw LLM
            response (for the failure log).
        verified_span: 可選。腳本側形態素驗證過的目標動詞字元 span
            (start, end)。提供時會在定位完成後檢查挖空位置與該 span 有
            重疊，不重疊即拋出 ``ClozePositioningError``。Optional
            script-side verified char span; when given, cloze positions
            must overlap it or ``ClozePositioningError`` is raised.

    Returns:
        tuple[str, str]: ``(cloze_sentence, full_sentence_html)``——
            前者為挖空後（____）的句子，後者為以底線標記解答的完整句
            HTML。The blanked (____) sentence and the full-sentence HTML
            with the answer underlined.

    Raises:
        ClozePositioningError: 所有定位策略均失敗，或 span 交叉驗證失敗時
            拋出。When all strategies fail or span cross-validation fails.
    """
    # 縮約形截斷防線（提示詞規則 6 的機械執行）。
    # 判定特徵刻意做成位置感知：不是「以促音結尾」就攔——句尾強調促音
    # （「終わったっ」）與中斷語（「鳴っ——」）都是合法的句尾促音，
    # 忠於原文挖入是對的。真正的截斷特徵是「っ 後面緊跟著縮約的延續假名
    # ち/て/と」（〜っちゃう/〜ってる/〜っとく 被從中切開）。
    # Position-aware guard: a trailing sokuon is only a truncation when the
    # character right AFTER the blank in the sentence continues a
    # contraction (chi/te/to); sentence-final emphatic or interrupted
    # sokuon is legitimate and passes.
    for blank in cloze_blanks:
        if not blank.endswith(("っ", "ッ")):
            continue
        # 與定位主策略一致採 rfind 取最右出現位置
        idx = target_sentence.rfind(blank)
        if idx == -1:
            continue  # 原文找不到 → 交給後續定位流程報錯
        following = target_sentence[idx + len(blank): idx + len(blank) + 1]
        if following in ("ち", "て", "と"):
            error_detail = (
                f"cloze_blank 以促音結尾（縮約形被截斷）: {blank!r}, "
                f"blanks={cloze_blanks}, 原文={target_sentence}"
            )
            log_llm_failure(
                task_name=task_name,
                model_name=model_name,
                prompt=prompt_text,
                raw_response=raw_response,
                error_detail=error_detail,
            )
            raise ClozePositioningError(
                f"挖空片段 {blank!r} 以促音「っ」結尾——縮約形（ちゃう/てる等）"
                "被從中截斷，將產出殘缺的填空形（提示詞規則 6）。"
            )

    # 使用「從右往左定位」策略處理挖空。
    # 例：「彼女は私を見つけて、扉をゆっくり開けた」
    #   blanks = ["を", "開けた"]
    #   先找「開けた」→ pos=14，再往左找「を」→ pos=11（扉を），不會誤挖 pos=4（私を）
    positions: list[tuple[int, int]] = []  # 每個 blank 的 (start, end) 位置
    search_boundary = len(target_sentence)  # 每次搜尋的右邊界
    all_found = True

    for i, blank in enumerate(reversed(cloze_blanks)):
        pos = -1
        
        # 若是第一個處理的 blank（即目標動詞本體），且有提供 verified_span，
        # 則優先尋找與 verified_span 重疊的出現位置，解決同一動詞在句中重複出現的問題。
        if i == 0 and verified_span is not None:
            span_start, span_end = verified_span
            start_idx = 0
            while True:
                idx = target_sentence.find(blank, start_idx, search_boundary)
                if idx == -1:
                    break
                # 檢查這個 match 是否與 verified_span 重疊
                if idx < span_end and (idx + len(blank)) > span_start:
                    pos = idx
                    break
                start_idx = idx + 1
                
        # 若沒有 verified_span，或上方未找到重疊者，退回預設的 rfind 策略
        if pos == -1:
            pos = target_sentence.rfind(blank, 0, search_boundary)
            
        if pos == -1:
            all_found = False
            break
            
        positions.append((pos, pos + len(blank)))
        # 下一個 blank 必須在此位置的左邊
        search_boundary = pos

    positions.reverse()  # 恢復為原始順序（左到右）

    if cloze_blanks and all_found:
        # 從右往左替換，以避免替換後的偏移影響前面的位置
        cloze_sentence = target_sentence
        full_sentence_html = target_sentence
        for start, end in reversed(positions):
            original_text = target_sentence[start:end]
            cloze_sentence = (
                cloze_sentence[:start] + "____" + cloze_sentence[end:]
            )
            full_sentence_html = (
                full_sentence_html[:start]
                + f'<u class="error-line">{original_text}</u>'
                + full_sentence_html[end:]
            )
        _validate_span_overlap(positions, verified_span, target_sentence)
    else:
        # 有子字串在原文中找不到，記錄警告
        failed = [b for b in cloze_blanks if b not in target_sentence]
        logger.warning(
            "LLM 回傳的 cloze_blanks 無法在原文中正確定位: blanks=%s, failed=%s。原文: '%s'",
            cloze_blanks, failed, target_sentence
        )

        # Fallback 1: 嘗試剝除 LLM 腦補的助詞後重新匹配。
        # 口語日文常省略助詞（助詞省略），但 LLM 會按語法規則自動補回，
        # 導致 'を閉める' 在原文 '閉める' 中找不到。
        # 策略：將每個失敗的 blank 去掉開頭的常見助詞後重試。
        common_particles = ("を", "が", "に", "は", "と", "で", "へ", "から", "まで", "より")
        stripped_blanks: list[str] = []
        for blank in cloze_blanks:
            stripped = blank
            for particle in common_particles:
                if blank.startswith(particle) and blank != particle:
                    candidate = blank[len(particle):]
                    if candidate in target_sentence:
                        logger.info(
                            "助詞剝除修復成功: '%s' -> '%s' (剝除了 LLM 腦補的助詞 '%s')",
                            blank, candidate, particle
                        )
                        stripped = candidate
                        break
            stripped_blanks.append(stripped)

        # 用修復後的 blanks 重新執行右往左定位
        retry_positions: list[tuple[int, int]] = []
        retry_boundary = len(target_sentence)
        retry_found = True
        for blank in reversed(stripped_blanks):
            pos = target_sentence.rfind(blank, 0, retry_boundary)
            if pos == -1:
                retry_found = False
                break
            retry_positions.append((pos, pos + len(blank)))
            retry_boundary = pos
        retry_positions.reverse()

        if retry_found and stripped_blanks:
            cloze_sentence = target_sentence
            full_sentence_html = target_sentence
            for start, end in reversed(retry_positions):
                original_text = target_sentence[start:end]
                cloze_sentence = (
                    cloze_sentence[:start] + "____" + cloze_sentence[end:]
                )
                full_sentence_html = (
                    full_sentence_html[:start]
                    + f'<u class="error-line">{original_text}</u>'
                    + full_sentence_html[end:]
                )
            _validate_span_overlap(retry_positions, verified_span, target_sentence)
        # Fallback 2: 嘗試用 target_particle_verb 整體匹配
        elif target_particle_verb in target_sentence:
            cloze_sentence = target_sentence.replace(target_particle_verb, "____", 1)
            full_sentence_html = target_sentence.replace(
                target_particle_verb,
                f'<u class="error-line">{target_particle_verb}</u>',
                1
            )
            fallback_start = target_sentence.index(target_particle_verb)
            _validate_span_overlap(
                [(fallback_start, fallback_start + len(target_particle_verb))],
                verified_span,
                target_sentence,
            )
        else:
            # 紀錄失敗到 JSONL
            error_detail = f"Cloze 挖空定位完全失敗: blanks={cloze_blanks}, target_particle_verb='{target_particle_verb}', 原文={target_sentence}"
            log_llm_failure(
                task_name=task_name,
                model_name=model_name,
                prompt=prompt_text,
                raw_response=raw_response,
                error_detail=error_detail
            )

            # 所有 Fallback 均失敗：拒絕建立卡片，回傳錯誤給調用端。
            # 這樣可以避免產生無法正確挖空的壞卡片，調用端可以選擇跳過或重試。
            raise ClozePositioningError(
                f"Cloze 挖空定位完全失敗，拒絕建立卡片。"
                f"blanks={cloze_blanks}, target_particle_verb='{target_particle_verb}', "
                f"原文='{target_sentence}'"
            )

    return cloze_sentence, full_sentence_html
