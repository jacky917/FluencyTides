"""選句漏斗編排（計劃 §6.6，雙入口共用的單一事實來源）。

Selection-funnel orchestration (plan §6.6), the single source of truth
shared by both entry scripts: ES paging, filtering, validation, bucketing,
and quota allocation, fully dependency-injected.

``generate_child_cards.py``（正式生成）與 ``test_search_verb.py``（分桶驗證）
都只呼叫本模組的 ``run_selection_funnel``——兩者唯一的差異是「設定來源」
（settings 組 cfg vs 檔內寫死的 map）與「出口」（呼叫 API vs 列印報告），
保證測試看到的分桶行為與正式生成完全一致、杜絕複製貼上分岔。

漏斗流程：
    ES 全量游標分頁（§6.1 必修項 2）→ §3.2 過濾（exclude_keywords /
    exclude_speakers / exclude_script_ids / min_sentence_length）→
    token 級驗證（candidate_validator）→ 兩維度分桶 → zigzag 兩段配額
    （diversity_selector）→ ``SelectionReport``。

依賴注入原則：
    - 設定以 ``VerbSearchConfig`` dataclass 注入——漏斗內不 import settings、
      不讀 .env，代碼零分支。
    - ES 抓取器 ``es_fetcher``、fugashi ``tagger``、章節/說話者
      ``metadata_fetcher`` 全部由呼叫端注入，本模組不建立任何連線。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from scripts.common.jp_moan_filter import REJECTION_MOAN, is_moan_sentence
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.candidate_validator import (
    validate_candidate,
)
from scripts.fastapi_client.JP_CoreVerb.pipeline_components.diversity_selector import (
    BucketOccupancy,
    SelectionCandidate,
    SelectedItem,
    classify_collocation,
    classify_conjugation,
    select_diverse,
)

# 過濾層淘汰原因鍵（報告用）
FILTER_EXCLUDE_SCRIPT_ID = "exclude_script_ids"
FILTER_EXCLUDE_KEYWORD = "exclude_keywords"
FILTER_EXCLUDE_SPEAKER = "exclude_speakers"
FILTER_NARRATION = "exclude_narration"
FILTER_ALREADY_GENERATED = "already_generated"
FILTER_MIN_LENGTH = "min_sentence_length"
# 純呻吟句（與 JP_VerbPair 共用同一套判定，見 scripts/common/jp_moan_filter.py）
FILTER_MOAN = REJECTION_MOAN

_FURIGANA_PATTERN = re.compile(r"\[.*?\]")

#: 游標分頁的保險上限（頁數），防止異常 fetcher 造成無窮迴圈。
_MAX_PAGES_PER_KEYWORD = 1000


def strip_furigana(text: str) -> str:
    """去除 furigana 標音括號（``見[み]る`` → ``見る``）。

    Strip furigana bracket annotations (``見[み]る`` → ``見る``).

    與 ``core_verbs.json`` / ``verb_search_config.json`` 的鍵表記共用同一條
    去標音規則（計劃 §3.2）。

    Args:
        text: 帶標音的原始字串。Raw string with furigana annotations.

    Returns:
        str: 去標音後的字串。The furigana-stripped string.
    """
    return _FURIGANA_PATTERN.sub("", text or "").strip()


@dataclass
class VerbSearchConfig:
    """單一動詞的選句設定（§3.2 搜尋設定檔 + §3.1 全域配額的合成結果）。

    Per-verb selection config, the merge of the §3.2 search-config file and
    §3.1 global quotas.

    正式腳本由 settings（``JP_CORE_VERB_*``）疊加 ``verb_search_config.json``
    的 per-verb 覆寫組出本結構；測試腳本由檔內寫死的 ``TEST_CONFIG`` 組出。

    Attributes:
        verb_display: 帶標音的顯示表記（如「見[み]る」，報告用）。
        verb_lemma: 去標音的字典形（如「見る」，ES 檢索與驗證用）。
        include_keywords: 額外 ES 檢索關鍵字（寫法變體）。
        exclude_keywords: 命中即排除的字面（人工排雷）。
        exclude_speakers: 排除的說話者清單。
        exclude_narration: 是否排除旁白句（role_name 為空/NULL/"None"/"-"/
            "none"），獨立於 ``exclude_speakers``。
        exclude_script_ids: 排除的 script_id 清單。
        max_cards: 該動詞的生成上限（含既有已生成張數）。
        max_per_chapter: 同一章節最多取句數。
        min_sentence_length: 目標句最短長度（去標音後字元數）。
        filter_moan: 是否過濾純呻吟句（擬態音節密度過高的 R18 台詞）。
        allow_auxiliary: 是否放行補助動詞用法（てみる／かける類）。
        priority_collocations: 優先保證 Pass 1 席位的搭配桶鍵（如「電話を」）
            ——只要有候選就必收，順序即優先序。
        page_size: 游標分頁每頁筆數。
        game_name_jp: 過濾的遊戲來源名稱（僅供呼叫端組 es_fetcher 參考）。
    """

    verb_display: str
    verb_lemma: str
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_speakers: list[str] = field(default_factory=list)
    exclude_narration: bool = False
    exclude_script_ids: list[int] = field(default_factory=list)
    max_cards: int = 15
    max_per_chapter: int = 2
    min_sentence_length: int = 8
    filter_moan: bool = True
    allow_auxiliary: bool = False
    priority_collocations: list[str] = field(default_factory=list)
    page_size: int = 500
    game_name_jp: str | None = None


@dataclass
class SelectionReport:
    """``run_selection_funnel`` 的完整輸出（計劃 §6.7 四種報告的資料來源）。

    Complete output of ``run_selection_funnel``, the data source for the
    four §6.7 reports.

    Attributes:
        verb_display: 帶標音的顯示表記。
        verb_lemma: 去標音的字典形。
        selected: 選中清單（含 span/桶標籤/Pass 標記，順序即選取順序）。
        funnel_counts: 漏斗各層計數（es_hits → after_filter → validated →
            selected）。
        filter_drops: §3.2 過濾層各原因的淘汰數。
        rejection_reasons: 驗證器拒絕原因分佈。
        bucket_matrix: 分桶矩陣 ``dict[搭配桶][活用形桶] = 候選數``。
        uncovered_collocations: 有候選但配額內未覆蓋的搭配桶。
        uncovered_conjugations: 有候選但配額內未覆蓋的活用形桶。
        occupied_count: 增量平衡計入的已生成張數。
        quota: 本次實際可選張數（``max_cards - occupied_count``，下限 0）。
    """

    verb_display: str
    verb_lemma: str
    selected: list[SelectedItem] = field(default_factory=list)
    funnel_counts: dict[str, int] = field(default_factory=dict)
    filter_drops: dict[str, int] = field(default_factory=dict)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    bucket_matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    uncovered_collocations: list[str] = field(default_factory=list)
    uncovered_conjugations: list[str] = field(default_factory=list)
    occupied_count: int = 0
    quota: int = 0


async def _fetch_all_pages(
    verb_cfg: VerbSearchConfig,
    es_fetcher: Callable[[str, int, int], Awaitable[list[dict]]],
) -> dict[int, str]:
    """對全部關鍵字做全量游標分頁抓取，合併去重（§6.1 必修項 2）。

    Full cursor-paged fetch across all keywords, merged and deduplicated
    (§6.1 mandatory item 2).

    每個關鍵字以 ``script_id > last_script_id`` 游標推進，每頁
    ``page_size`` 筆直到空頁——徹底避免 Fetch-100 的頭部偏差。

    Args:
        verb_cfg: 動詞選句設定。Per-verb selection config.
        es_fetcher: 注入的 ES 抓取器
            ``(keyword, last_script_id, page_size) -> list[dict]``，
            回傳列須含 ``script_id`` 與 ``dialogue``。Injected ES fetcher;
            rows must contain ``script_id`` and ``dialogue``.

    Returns:
        dict[int, str]: ``script_id -> dialogue``（跨關鍵字去重後）。
        Mapping deduplicated across keywords.
    """
    keywords = [verb_cfg.verb_lemma] + list(verb_cfg.include_keywords)
    rows: dict[int, str] = {}
    for keyword in keywords:
        last_script_id = 0
        for _ in range(_MAX_PAGES_PER_KEYWORD):
            page = await es_fetcher(keyword, last_script_id, verb_cfg.page_size)
            if not page:
                break
            for row in page:
                rows[int(row["script_id"])] = row.get("dialogue") or ""
            last_script_id = int(page[-1]["script_id"])
            if len(page) < verb_cfg.page_size:
                break
    return rows


def _build_occupancy(
    occupied: Iterable[dict],
    verb_cfg: VerbSearchConfig,
    tagger: Callable[[str], Iterable[Any]],
) -> BucketOccupancy:
    """把已生成句同樣走驗證＋分桶，換算成桶佔用（§6.5 增量平衡）。

    Run already-generated sentences through validation and bucketing to
    compute bucket occupancy (§6.5 incremental balancing).

    已生成句即使驗證失敗（例如當年規則較鬆），其章節計數仍照算——
    ``max_per_chapter`` 是硬約束，必須含歷史佔用一體檢查。

    Args:
        occupied: 已生成句清單，每項為
            ``{"script_id", "sentence", "chapter", "speaker"}``。List of
            generated sentences with those keys.
        verb_cfg: 動詞選句設定。Per-verb selection config.
        tagger: 注入的分詞器。Injected tokenizer.

    Returns:
        BucketOccupancy: 桶佔用統計。Bucket occupancy statistics.
    """
    collocations: Counter[str] = Counter()
    conjugations: Counter[str] = Counter()
    chapters: Counter[str] = Counter()
    total = 0
    for item in occupied:
        total += 1
        chapters[item.get("chapter") or ""] += 1
        sentence = strip_furigana(item.get("sentence") or "")
        if not sentence:
            continue
        result = validate_candidate(
            sentence, verb_cfg.verb_lemma, verb_cfg.allow_auxiliary, tagger
        )
        if not result.accepted or result.candidate is None:
            continue
        cand = result.candidate
        collocations[classify_collocation(cand.tokens, cand.span_token_index)] += 1
        conjugations[classify_conjugation(cand.tokens, cand.span_token_index)] += 1
    return BucketOccupancy(
        collocations=dict(collocations),
        conjugations=dict(conjugations),
        chapters=dict(chapters),
        total=total,
    )


async def run_selection_funnel(
    verb_cfg: VerbSearchConfig,
    es_fetcher: Callable[[str, int, int], Awaitable[list[dict]]],
    occupied: list[dict] | None = None,
    *,
    tagger: Callable[[str], Iterable[Any]],
    metadata_fetcher: Callable[[list[int]], Awaitable[dict[int, dict]]] | None = None,
    exclude_generated: set[tuple[int, str]] | None = None,
) -> SelectionReport:
    """執行整條選句漏斗（計劃 §6.6，雙入口唯一呼叫點）。

    Run the entire selection funnel (plan §6.6), the sole call point shared
    by both entry scripts.

    Args:
        verb_cfg: 動詞選句設定（dataclass 注入，漏斗內不讀 .env/settings）。
            Injected per-verb config; the funnel reads no settings itself.
        es_fetcher: ES 抓取器 ``(keyword, last_script_id, page_size) ->
            list[dict]``；漏斗內以 while 迴圈游標分頁拉全量候選。Injected ES
            fetcher used for full cursor-paged retrieval.
        occupied: §6.5 增量平衡的已生成句清單（每項含
            ``script_id / sentence / chapter / speaker``）；``None`` 或空
            清單視為首次生成。Generated sentences for incremental balancing;
            ``None``/empty means first run.
        tagger: 注入的 fugashi 分詞器（或測試假 tagger）。Injected fugashi
            tagger or a test fake.
        exclude_generated: 已有生成紀錄的 ``(script_id, chapter)`` 鍵集合
            （呼叫端以 verb_lemma＋source 撈取，**含軟刪除與失敗紀錄**——
            軟刪除代表使用者不想再生成該句）；過濾層全等即排除。Keys of
            previously logged sentences, excluded at the filter layer.
        metadata_fetcher: 可選的章節/說話者查詢器
            ``(script_ids) -> {script_id: {"chapter": ..., "speaker": ...}}``；
            未注入時章節/說話者以空字串處理（exclude_speakers 將無從過濾）。
            Optional chapter/speaker fetcher; blanks are used when omitted.

    Returns:
        SelectionReport: 選中清單、漏斗各層計數、拒絕原因分佈、
        分桶矩陣與未覆蓋桶清單。Selected items, per-layer counts, rejection
        distribution, bucket matrix, and uncovered-bucket lists.
    """
    occupied = occupied or []

    # --- 第 1 層：ES 全量游標分頁 ---
    rows = await _fetch_all_pages(verb_cfg, es_fetcher)
    es_hits = len(rows)

    # 章節 / 說話者中繼資料
    metadata: dict[int, dict] = {}
    if metadata_fetcher is not None and rows:
        metadata = await metadata_fetcher(sorted(rows))

    # --- 第 2 層：§3.2 過濾 ---
    filter_drops: Counter[str] = Counter()
    exclude_script_ids = set(verb_cfg.exclude_script_ids)
    filtered: list[tuple[int, str, str, str]] = []  # (script_id, 句, 章節, 說話者)
    for script_id in sorted(rows):
        dialogue = rows[script_id]
        meta = metadata.get(script_id, {})
        chapter = meta.get("chapter") or ""
        speaker = meta.get("speaker") or ""
        if script_id in exclude_script_ids:
            filter_drops[FILTER_EXCLUDE_SCRIPT_ID] += 1
            continue
        # 已有生成紀錄（script_id + verb_lemma + source + chapter 全等，
        # 含軟刪除/失敗紀錄）→ 直接篩掉，不進候選池
        if exclude_generated and (script_id, chapter) in exclude_generated:
            filter_drops[FILTER_ALREADY_GENERATED] += 1
            continue
        if any(bad and bad in dialogue for bad in verb_cfg.exclude_keywords):
            filter_drops[FILTER_EXCLUDE_KEYWORD] += 1
            continue
        # 旁白判定：role_name 為空/NULL/"None"/"-"/"none"
        # （對齊 JP_VerbPair context_builder / --skip-narrator 的判定）
        is_narration = speaker in ("", "-", "None", "none")
        if verb_cfg.exclude_narration and is_narration:
            filter_drops[FILTER_NARRATION] += 1
            continue
        # exclude_speakers 為字面比對；「ナレーション」字面亦視為旁白排除
        if verb_cfg.exclude_speakers and (
            speaker in verb_cfg.exclude_speakers
            or (is_narration and "ナレーション" in verb_cfg.exclude_speakers)
        ):
            filter_drops[FILTER_EXCLUDE_SPEAKER] += 1
            continue
        sentence = strip_furigana(dialogue)
        if len(sentence) < verb_cfg.min_sentence_length:
            filter_drops[FILTER_MIN_LENGTH] += 1
            continue
        # 純呻吟句：動詞用法合法但教學價值近零，字面樣式擋下
        if verb_cfg.filter_moan and is_moan_sentence(sentence):
            filter_drops[FILTER_MOAN] += 1
            continue
        filtered.append((script_id, sentence, chapter, speaker))

    # --- 第 3 層：token 級驗證 + 第 4 層：兩維度分桶 ---
    rejection_reasons: Counter[str] = Counter()
    bucket_matrix: dict[str, dict[str, int]] = {}
    candidates: list[SelectionCandidate] = []
    for script_id, sentence, chapter, speaker in filtered:
        result = validate_candidate(
            sentence, verb_cfg.verb_lemma, verb_cfg.allow_auxiliary, tagger
        )
        if not result.accepted or result.candidate is None:
            rejection_reasons[result.reason or "未知"] += 1
            continue
        cand = result.candidate
        collocation = classify_collocation(cand.tokens, cand.span_token_index)
        conjugation = classify_conjugation(cand.tokens, cand.span_token_index)
        bucket_matrix.setdefault(collocation, {})
        bucket_matrix[collocation][conjugation] = (
            bucket_matrix[collocation].get(conjugation, 0) + 1
        )
        candidates.append(
            SelectionCandidate(
                script_id=script_id,
                sentence=sentence,
                span=cand.span,
                collocation=collocation,
                conjugation=conjugation,
                chapter=chapter,
                speaker=speaker,
            )
        )

    # --- 第 5 層：增量平衡佔用 + zigzag 兩段配額 ---
    occupancy = _build_occupancy(occupied, verb_cfg, tagger)
    quota = max(0, verb_cfg.max_cards - occupancy.total)
    selection = select_diverse(
        candidates=candidates,
        quota=quota,
        max_per_chapter=verb_cfg.max_per_chapter,
        occupied_buckets=occupancy,
        priority_collocations=verb_cfg.priority_collocations,
    )

    return SelectionReport(
        verb_display=verb_cfg.verb_display,
        verb_lemma=verb_cfg.verb_lemma,
        selected=selection.selected,
        funnel_counts={
            "es_hits": es_hits,
            "after_filter": len(filtered),
            "validated": len(candidates),
            "selected": len(selection.selected),
        },
        filter_drops=dict(filter_drops),
        rejection_reasons=dict(rejection_reasons),
        bucket_matrix=bucket_matrix,
        uncovered_collocations=selection.uncovered_collocations,
        uncovered_conjugations=selection.uncovered_conjugations,
        occupied_count=occupancy.total,
        quota=quota,
    )


def format_selection_report(report: SelectionReport) -> str:
    """把 ``SelectionReport`` 格式化為計劃 §6.7 的四段人讀報告。

    Format a ``SelectionReport`` into the four-section human-readable
    report of plan §6.7.

    四段內容：漏斗各層統計（含拒絕原因分佈）、搭配×活用形分桶矩陣、
    zigzag 選取軌跡（每句標注桶/章節/Pass）、未覆蓋桶清單。

    Args:
        report: 漏斗輸出。The funnel output.

    Returns:
        str: 多行報告文字（供 logger 或 print 輸出）。Multi-line report text
        for logger or print output.
    """
    lines: list[str] = []
    lines.append(f"━━━ 選句報告：{report.verb_display}（{report.verb_lemma}） ━━━")

    # 報告 1：漏斗各層統計
    fc = report.funnel_counts
    lines.append("【1. 漏斗各層統計】")
    lines.append(
        f"  ES 命中 {fc.get('es_hits', 0)} → 過濾後 {fc.get('after_filter', 0)}"
        f" → 驗證通過 {fc.get('validated', 0)} → 選中 {fc.get('selected', 0)}"
        f"（已生成佔用 {report.occupied_count}，本次配額 {report.quota}）"
    )
    if report.filter_drops:
        drops = "、".join(f"{k}: {v}" for k, v in sorted(report.filter_drops.items()))
        lines.append(f"  過濾層淘汰：{drops}")
    if report.rejection_reasons:
        rejects = "、".join(
            f"{k}: {v}" for k, v in sorted(report.rejection_reasons.items())
        )
        lines.append(f"  驗證器拒絕原因分佈：{rejects}")

    # 報告 2：搭配 × 活用形 分桶矩陣
    lines.append("【2. 分桶矩陣（搭配桶 × 活用形桶 = 候選數）】")
    if report.bucket_matrix:
        conj_keys = sorted({c for row in report.bucket_matrix.values() for c in row})
        header = "  搭配桶＼活用形 | " + " | ".join(conj_keys)
        lines.append(header)
        for colloc in sorted(
            report.bucket_matrix,
            key=lambda k: -sum(report.bucket_matrix[k].values()),
        ):
            row = report.bucket_matrix[colloc]
            cells = " | ".join(str(row.get(c, 0)) for c in conj_keys)
            lines.append(f"  {colloc}（計 {sum(row.values())}） | {cells}")
    else:
        lines.append("  （無驗證通過的候選）")

    # 報告 3：zigzag 選取軌跡
    lines.append("【3. 選取軌跡】")
    if report.selected:
        for index, item in enumerate(report.selected, 1):
            c = item.candidate
            lines.append(
                f"  {index:>2}. [{item.pass_label}] 搭配={c.collocation}"
                f" / 活用={c.conjugation} / 章節={c.chapter or '-'}"
                f" / 話者={c.speaker or '-'} / script_id={c.script_id}"
                f" / span={c.span}"
            )
            lines.append(f"      {c.sentence}")
    else:
        lines.append("  （配額為 0 或無可選候選）")

    # 報告 4：未覆蓋桶清單
    lines.append("【4. 未覆蓋桶清單（有候選但配額內未選中）】")
    lines.append(
        "  搭配桶："
        + ("、".join(report.uncovered_collocations) or "（全數覆蓋）")
    )
    lines.append(
        "  活用形桶："
        + ("、".join(report.uncovered_conjugations) or "（全數覆蓋）")
    )
    return "\n".join(lines)
