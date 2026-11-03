"""驗證新庫與舊庫一致（計畫書 D7 第 3 步、§8 驗收）。

Verify the rebuilt corpus DB against the legacy DB. Read-only on both.

用法 / usage（在 backend/ 底下）::

    python -m scripts.database.corpus_rebuild.verify
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[3]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))
import scripts.common.env  # noqa: E402,F401

from scripts.database.corpus_rebuild.rebuild import connect  # noqa: E402

# 已知、允許的差異 / known, accepted differences
ALLOWED_DIALOGUE_DIFF = {9516}  # F3 錯字修正


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", default="yuzusoft")
    ap.add_argument("--new", default="fluencytides_corpus")
    args = ap.parse_args()
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    conn = connect(args.new)
    cur = conn.cursor()
    o = f"`{args.old}`"
    failures: list[str] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(f"{'✅' if ok else '❌'} {name} {detail}")
        if not ok:
            failures.append(name)

    def one(sql: str, params: tuple = ()) -> object:
        cur.execute(sql, params)
        return cur.fetchone()[0]

    # 1. 封存表與舊表完全相同 / archive identical
    cur.execute(f"CHECKSUM TABLE game_legacy_scripts, {o}.scripts")
    sums = [r[1] for r in cur.fetchall()]
    check("game_legacy_scripts 與舊 scripts 完全相同", sums[0] == sums[1], sums)

    # 2. 每一句舊台詞都在，值一致 / every legacy row present with equal values
    cur.execute(f"""
        SELECT s.id,
               s.chapter <=> l.chapter_label,
               COALESCE(s.role_name, '') = COALESCE(sp.name_ja_jp, ''),
               s.dialogue = COALESCE(l.ruby_ja_jp, l.text_ja_jp),
               COALESCE(s.audio_file, '') = COALESCE(l.voice_file, '')
        FROM {o}.scripts s
        LEFT JOIN game_lines l ON l.id = s.id
        LEFT JOIN game_speakers sp ON sp.speaker_id = l.speaker_id""")
    rows = cur.fetchall()
    missing = [r[0] for r in rows if r[1] is None and r[3] is None]
    check("舊台詞全部都在 game_lines", not missing, missing[:10])
    for k, name in ((1, "chapter"), (2, "role_name"), (3, "dialogue"), (4, "audio_file")):
        bad = [r[0] for r in rows if not r[k]]
        if k == 3:
            bad = [x for x in bad if x not in ALLOWED_DIALOGUE_DIFF]
        check(f"舊欄位 {name} 一致", not bad, f"不一致 {len(bad)}：{bad[:10]}")

    # 3. 新增的台詞 / new rows
    cur.execute("SELECT origin, kind, COUNT(*) FROM game_lines GROUP BY origin, kind ORDER BY 1, 2")
    print("   origin/kind:", cur.fetchall())
    check("除了 bgv 以外 point 與 text_zh_hant 都有值",
          one("SELECT COUNT(*) FROM game_lines WHERE kind <> 'bgv' AND (point IS NULL OR text_zh_hant IS NULL)") == 0)
    check("每一筆修復都有 game_line_revisions",
          one("SELECT COUNT(*) FROM game_lines l WHERE (l.origin <> 'legacy' OR l.kind = 'bgv' OR l.id = 9516) "
              "AND NOT EXISTS (SELECT 1 FROM game_line_revisions r WHERE r.line_id = l.id)") == 0)

    # 4. 卡片紀錄 / card log
    check("card_gen_log 列數與 id 和舊表相同",
          one(f"SELECT COUNT(*) FROM {o}.generated_sentences_log g LEFT JOIN card_gen_log c ON c.id = g.id "
              "WHERE c.id IS NULL") == 0
          and one("SELECT COUNT(*) FROM card_gen_log") == one(f"SELECT COUNT(*) FROM {o}.generated_sentences_log"))
    bad = one(f"""SELECT COUNT(*) FROM {o}.generated_sentences_log g JOIN card_gen_log c ON c.id = g.id
                  JOIN game_lines l ON l.id = c.line_id
                  WHERE c.line_id <> g.script_id
                     OR SUBSTRING(c.term_key, 7) <> g.verb_lemma COLLATE utf8mb4_bin
                     OR c.card_type <> g.project COLLATE utf8mb4_bin
                     OR g.source COLLATE utf8mb4_bin <> c.game_id OR c.game_id <> l.game_id
                     OR g.chapter COLLATE utf8mb4_bin <> l.chapter_label
                     OR NOT (c.master_note_id <=> g.master_note_id AND c.context_note_id <=> g.context_note_id
                             AND c.cloze_note_id <=> g.cloze_note_id AND c.is_deleted = g.is_deleted)""")
    check("card_gen_log 欄位轉換正確（term_key、card_type、game_id、chapter、note id）", bad == 0, f"不一致 {bad}")
    check("nlp_line_terms 列數與舊表相同",
          one("SELECT COUNT(*) FROM nlp_line_terms") == one(f"SELECT COUNT(*) FROM {o}.dialogue_terms_index"))
    check("nlp_line_readings 列數與舊表相同",
          one("SELECT COUNT(*) FROM nlp_line_readings") == one(f"SELECT COUNT(*) FROM {o}.jp_verb_reading_judgments"))

    # 5. 關係 / relations
    cur.execute("SELECT relation_type, status, COUNT(*) FROM rel_links GROUP BY 1, 2 ORDER BY 1, 2")
    print("   rel_links:", cur.fetchall())
    check("rel_links 的台詞端點都存在",
          one("SELECT COUNT(*) FROM rel_links r LEFT JOIN game_lines l ON l.id = CAST(r.dst_id AS UNSIGNED) "
              "WHERE r.dst_type = 'script_line' AND l.id IS NULL") == 0)
    check("rel_links 的類型都已登記",
          one("SELECT COUNT(*) FROM rel_links r LEFT JOIN rel_entity_types a ON a.entity_type = r.src_type "
              "LEFT JOIN rel_entity_types b ON b.entity_type = r.dst_type WHERE a.entity_type IS NULL "
              "OR b.entity_type IS NULL") == 0)
    n_child = one("SELECT COUNT(context_note_id) + COUNT(cloze_note_id) FROM card_gen_log")
    check("母卡→子卡、子卡→台詞的關係數等於子卡數",
          one("SELECT COUNT(*) FROM rel_links WHERE relation_type IN ('core.has_context','core.has_cloze')") == n_child
          and one("SELECT COUNT(*) FROM rel_links WHERE relation_type = 'core.example_from'") == n_child)

    # 6. 建卡設定的排除清單 / exclude_script_ids still exist
    cfg = _backend_dir / "scripts/fastapi_client/JP_CoreVerb/verb_search_config.json"
    if cfg.exists():
        ids = sorted({int(x) for v in json.loads(cfg.read_text(encoding="utf-8")).values()
                      if isinstance(v, dict) for x in v.get("exclude_script_ids", [])})
        if ids:
            cur.execute(f"SELECT COUNT(*) FROM game_lines WHERE id IN ({','.join(map(str, ids))})")
            check("verb_search_config 的 exclude_script_ids 都存在", cur.fetchone()[0] == len(ids), len(ids))

    conn.close()
    print("\n全部通過" if not failures else f"\n❌ {len(failures)} 項未通過：{failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
