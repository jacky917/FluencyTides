"""劇情流程圖測試共用的假資料：一份縮小版的「魔女的夜宴」場景結構。

Shared fixtures for story tests: a miniature Sabbat-like scene structure
covering choices (2 and 4 options), virtual options, a variable relay, a
route split, an unlock entrance, an AFTER story, a back link, and a link
into an empty scene that must be redirected.
"""

from __future__ import annotations

import re

from scripts.story.common.scene_graph import ExtraLink, FileRow, GameRules, LineRow, LinkRow, SceneRow

FILES = [
    # file_id, file_name, file_no, title
    (1, "001.共通－始まり.ks", "001", "共通－始まり"),
    (2, "009.共通－休日デート.ks", "009", "共通－休日デート"),
    (3, "019.共通－パーティ.ks", "019", "共通－パーティ"),
    (4, "020.共通－パーティ寧々.ks", "020", "共通－パーティ寧々"),
    (5, "020.共通－パーティめぐる.ks", "020", "共通－パーティめぐる"),
    (6, "101.寧々－ラーメン.ks", "101", "寧々－ラーメン"),
    (7, "102.寧々－エピローグ.ks", "102", "寧々－エピローグ"),
    (8, "117.寧々－アフター.ks", "117", "寧々－アフター"),
    (9, "201.めぐる－記念撮影.ks", "201", "めぐる－記念撮影"),
    (10, "500.和奏－分岐.ks", "500", "和奏－分岐"),
]

# scene_id, file_id, label, line count, chapter label
SCENES = [
    (10, 1, "chapter11", 5, "chapter　1-1"),
    (11, 1, "chapter12", 3, "chapter　1-2"),
    (12, 1, "chapter21", 4, "chapter　2-1"),   # 章節不同：不合併
    (20, 2, "chapter32", 6, "chapter　3-2"),
    (21, 2, "dummyselect1", 1, "chapter　3-2"),
    (22, 2, "009_01A", 3, ""),
    (23, 2, "009_01B", 2, ""),
    (24, 2, "009_01com", 4, ""),
    (25, 2, "dummy1", 0, None),                    # 純中繼：依 sel_flag 跳
    (26, 2, "009_02A", 2, ""),
    (27, 2, "009_02B", 2, ""),
    (28, 2, "009_02com", 3, ""),
    (29, 2, "chapter99", 0, None),                 # 沒有台詞：指向它的連接要改指下一個檔案的同名場景
    (30, 3, "chapter47", 5, "chapter　4-7"),
    (31, 3, "dummyselect1", 1, "chapter　4-7"),
    (32, 3, "019_01A", 1, ""),
    (33, 3, "019_01B", 1, ""),
    (34, 3, "019_01C", 1, ""),
    (35, 3, "019_01D", 1, ""),
    (36, 3, "019_01com", 4, ""),
    (37, 3, "normal_end", 2, ""),
    (40, 4, "chapter48", 2, ""),
    (41, 5, "chapter48", 2, ""),
    (50, 6, "chapter51", 4, "chapter　5-1"),
    (51, 6, "ne_H_1", 3, ""),
    (55, 7, "chapter99", 3, "寧々_エピローグ"),
    (60, 8, "chapter after", 3, "寧々_アフターストーリー"),
    (70, 9, "chapter48", 3, "chapter　5-1"),
    (80, 10, "chapter45", 3, "chapter　4-5"),
]


def _link(src, dst, kind="next", **kw) -> LinkRow:
    return LinkRow(from_scene_id=src, to_scene_id=dst, kind=kind, **kw)


def links() -> list[LinkRow]:
    return [
        _link(10, 11), _link(11, 12), _link(12, 20),
        _link(20, 21),
        _link(21, 22, "choice", option_index=0, option_text_ja="悩む", option_text_zh="糾結",
              effects=[{"var": "sel_flag", "op": "set", "value": 0}]),
        _link(21, 23, "choice", option_index=1, option_text_ja="変更する",
              effects=[{"var": "sel_flag", "op": "set", "value": 1}, {"var": "meg_flag", "op": "add", "value": 1}]),
        _link(21, 21, "next"),                      # 自迴圈雜訊
        _link(21, 50, "next"),                      # 選項場景的 next 雜訊
        _link(22, 24), _link(23, 24),
        _link(24, 25),
        _link(25, 26, "jump", condition_vars={"sel_flag": 0}, evidence="probed"),
        _link(25, 27, "jump", condition_vars={"sel_flag": 1}, evidence="probed"),
        _link(26, 28), _link(27, 28),
        _link(28, 29),                              # → 沒有台詞的 009 chapter99：丟棄
        _link(28, 30),
        _link(28, None),                            # 另有去向時的「結束」
        _link(30, 31),
        *[_link(31, 32 + i, "choice", option_index=i, option_text_ja=f"選{i}",
                effects=[{"var": "sel_flag", "op": "set", "value": i}]) for i in range(4)],
        _link(32, 36), _link(33, 36), _link(34, 36), _link(35, 36),
        _link(32, 30),                              # 往回跳
        _link(36, 37),
        _link(37, None),
        _link(40, 50), _link(41, 70),
        _link(50, 51), _link(51, 55),
        _link(55, None),
    ]


EXTRAS = [
    ExtraLink(("019", "019_01com"), ("019", "normal_end"), kind="next", evidence="probed",
              condition_text_zh="好感旗標不足"),
    ExtraLink(("019", "019_01com"), ("020.共通－パーティ寧々", None), condition_vars={"nen_flag": 4}, evidence="probed"),
    ExtraLink(("019", "019_01com"), ("020.共通－パーティめぐる", None), condition_vars={"meg_flag": 4}, evidence="probed"),
    ExtraLink(("009", "009_02com"), ("500", None), kind="unlock", evidence="inferred", condition_text_zh="任一路線通關後"),
    ExtraLink(("102", None), ("117", None), kind="unlock", evidence="observed", flags=("unlock",)),
]

TAB_BY_DIGIT = {"0": "common", "1": "nene", "2": "meguru", "5": "wakana"}


def rows():
    files = [FileRow(fid, name, no, title, None, fid) for fid, name, no, title in FILES]
    scenes, lines = [], []
    order = 0
    for i, (sid, fid, label, count, chapter) in enumerate(SCENES):
        scenes.append(SceneRow(sid, fid, label, i))
        for j in range(count):
            order += 1
            kind = "choice" if label.startswith("dummyselect") else "line"
            lines.append(LineRow(sid * 100 + j, sid, order, kind, chapter if j == 0 else ""))
    return files, scenes, lines, links()


def parse_chapter(label: str):
    m = re.search(r"chapter[\s　]*(\d+)-(\d+)", label)
    if m:
        return m.group(1), f"CHAPTER {m.group(1)}"
    if "アフター" in label:
        return "after", "After Story"
    if "エピローグ" in label:
        return "epilogue", "Epilogue"
    return None


def rules(**overrides) -> GameRules:
    kwargs = dict(
        prefix="sw", main_tab="common",
        tabs=[{"key": "common", "title": "共通", "order": 1}, {"key": "nene", "title": "寧々", "order": 2},
              {"key": "meguru", "title": "めぐる", "order": 3}, {"key": "wakana", "title": "和奏", "order": 4}],
        tab_of_file=lambda f: TAB_BY_DIGIT[f.file_no[0]],
        is_after_file=lambda f: "アフター" in f.file_name,
        file_title=lambda f: (f.title_ja.split("－", 1)[1], None),
        parse_chapter=parse_chapter,
        extra_links=list(EXTRAS),
        titles={"sw:019_normal_end": {"ja-JP": "ノーマルエンド"}},
    )
    kwargs.update(overrides)
    return GameRules(**kwargs)
