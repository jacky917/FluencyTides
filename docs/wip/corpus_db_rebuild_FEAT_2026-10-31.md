# FEAT — 重建語料庫資料庫 `fluencytides_corpus`：合併舊劇本與遊戲錨點、修復缺漏資料

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-31 |
| **性質** | 新增機能設計 + 實作工作項（資料庫遷移與資料修復） |
| **狀態** | 🔨 實作完成、待切換：M1（新庫已建並驗證）、M2b（D9 程式改寫，含審查修正）完成；M3 切換尚未進行（2026-11-01） |
| **範圍** | 同一台 MySQL（192.168.50.172）新增資料庫 `fluencytides_corpus`；表依用途分成 `game_`（遊戲內容）、`nlp_`（語言標注）、`card_`（卡片帳本）、`rel_`（通用關係）、`story_`（流程圖）五個系列（D0）；從 `yuzusoft` 遷移卡片紀錄、詞彙索引、讀音判定（保留原 id，欄位改成多遊戲）；遊戲設定從 `.env` 移到 `game_profiles`；遷移腳本 `backend/scripts/database/corpus_rebuild/`；切換 `MYSQL_DATABASE`（本機與正式機 `.env`） |
| **不動** | `yuzusoft` 資料庫與網站的 SQLite（兩者原樣保留，不刪、不改權限，程式不再讀寫）；Elasticsearch 索引的文件格式與 `_id`；Anki 筆記 |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/wip/story_flowchart_FEAT_2026-10-31.md`（流程圖，建在本計畫的新表上）；`game\SW_reverse_notes.md`、`game\SW_calib\README.md`（錨點來源）；`backend/scripts/common/database/init_db.py`（既有表的建表 SQL） |

---

## 1. 問題與動機

**使用者要求**：重新建立一個資料庫，把舊劇本（`yuzusoft.scripts`）和遊戲錨點（校準得到的 point 表，含繁中劇本）合併成新表，並修復之前缺少的資料。

**現況（已對照資料庫，2026-10-31 查詢）**

`yuzusoft` 有 5 張表，其中 4 張以外鍵指向 `scripts.id`：

| 表 | 列數 | 與 `scripts` 的關係 |
|---|---|---|
| `scripts` | 53,190 | 台詞本體，id 1–53196（中間有刪除） |
| `generated_sentences_log` | 6,265 | `script_id` FK，`ON DELETE CASCADE` |
| `dialogue_terms_index` | 9,184 | `script_id` FK，`ON DELETE CASCADE` |
| `jp_verb_reading_judgments` | 0 | `script_id` FK，`ON DELETE CASCADE` |
| `evaluation` | 0 | `script_id`（沒有外鍵） |

`scripts` 本身的問題：

| 問題 | 證據 | 影響 |
|---|---|---|
| **少了遊戲裡有的台詞** | 錨點有、`scripts` 沒有：6 句已被刪除（id 1722、8330、18023、36635、43959、45037）、46 句選項畫面 | 流程圖缺句；這 6 句原本在哪個位置也查不到 |
| **錨點少一句** | id 1043（`002…chapter14`，point 666–670 之間）在 `scripts` 有、錨點沒有。2026-11-01 確認：它是**背景語音**（`nen002_039_bgv`），在 point 670 的旁白顯示時於背景播放，不會出現在對話框，所以本來就沒有 point，也沒有繁中文字 | 不是缺漏；改用 `kind=bgv` 記錄（D2） |
| **錯字** | id 9516：`scripts` 是「が」，遊戲實際是「か」 | 用台詞比對會對不上 |
| **欄位內容錯位** | `dialogue_hiragana`、`dialogue_kanji`、`reserved` 有 37,818 列非空，但只有 2,604 種不同值，和 `dialogue` 相同的只有 57 列，顯然是匯入時錯位的殘留 | 不能用；目前沒有程式讀它們 |
| **無意義的欄位** | `illustration_info` 全部是 `1`、`unknown` 全部是 `0`、`audio_pan` 只有一種值 | 不需要遷移 |
| **來源名稱不一致** | `scripts.source` 是 `サノバウィッチ`，`generated_sentences_log.source` 是 `SabbatOfTheWitch` | 跨表查詢要另外對照 |
| **沒有繁中、沒有劇情位置** | `scripts` 只有日文；位置（場景標籤＋point）、BGM、H 標記、縮圖都在錨點檔案裡，不在資料庫 | 流程圖與存檔要另外讀檔案 |

錨點與 `scripts` 的比對（以「檔名 NFC、去掉振假名標記與空白的台詞」為鍵）：錨點 53,241 句中有 53,188 句對得上，`scripts` 有 2 筆沒被對到（1043、9516）。另外 **1,955 句在同一個檔案裡有一字不差的重複台詞**（例：「……」），只靠文字無法決定對應哪個 id，遷移時要依劇情順序對齊（D4）。

**錨點的現況（2026-11-01 繁中輪完成後）**：53,241 句都有日文、繁中與縮圖雜湊；19,175 句有語音代號、5,046 句是 H 場景、49,472 句有 BGM（逐句驗證後，曲名已補齊）。錨點檔案的 `chapter_cn`、`chapter_tw` 和 `chapter_jp` 完全相同，不另外存。

**程式碼對 `scripts` 的依賴**：建卡管線（JP_CoreVerb、JP_VerbPair）、ES 同步（`sync_mysql_to_es.py`，ES 文件的 `_id` 就是 `scripts.id`）、Anki 清理工具、`verb_search_config.json` 的 `exclude_script_ids`，總共數十個檔案。全部都只讀不寫（搜尋 `INSERT/UPDATE/DELETE … scripts` 沒有結果）。

## 2. 目標與非目標

**目標**
- G1 新資料庫 `fluencytides_corpus`，以「一句台詞一列」的 `game_lines` 為核心，合併舊劇本、遊戲錨點、各語言台詞。
- G2 **保留所有既有的 id**：`game_lines.id` = 舊 `scripts.id`；遷移過來的紀錄表也保留原 id。這樣 Anki、ES、`exclude_script_ids`、卡片紀錄都不用改。
- G3 修復 §1 列出的缺漏與錯誤，每一筆修復都記在 `game_line_revisions`，可以追查。
- G4 **一步到位**：所有程式（建卡管線、ES 同步、刪卡工具、讀音判定、網站的卡片關聯）都改讀寫新庫，不建相容層（D9）。
- G5 多遊戲、多語言：遊戲有自己的表與代號；多語欄位一律是「欄位＋語言」（`title_ja_jp`、`text_zh_hant`），語言代碼用 BCP 47（`ja-JP`、`zh-Hant`）。
- G6 劇情流程圖（`story_flowchart_FEAT`）直接建在這些表上。
- G7 **卡片紀錄與建卡管線支援多款遊戲**：紀錄以 `game_id` 指向遊戲，詞帶語言（`ja-JP:見る`）；遊戲設定（名稱、媒體前綴、語音與頭像資料夾）從 `.env` 移到 `game_profiles`，建卡管線用 `--game` 指定遊戲（D2、D9 的 H）。

**非目標**
- 重新產生 `dialogue_hiragana`、`dialogue_kanji`：目前沒人用，資料是錯的，只留在 `game_legacy_scripts`；之後需要讀音時再用分詞器重算。
- 刪除或改動 `yuzusoft` 與網站的 SQLite：使用者決定兩者都保留不動（2026-11-01）。
- 重建 Elasticsearch 索引：`_id` 不變、內容不變，不需要重建。修復新增的台詞要不要進 ES，見 D6。

## 3. 前置作業：建立資料庫與授權

✅ 2026-11-01 已完成：使用者以 root 建好 `fluencytides_corpus`（`utf8mb4_unicode_ci`），並授權給 `jacky917`（`SHOW GRANTS` 已確認）。當時執行的指令：

```sql
CREATE DATABASE fluencytides_corpus CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```sql
GRANT ALL PRIVILEGES ON fluencytides_corpus.* TO 'jacky917'@'192.168.%';
```

遷移時要同時讀 `yuzusoft`、寫 `fluencytides_corpus`。兩個資料庫在同一台 MySQL，現有權限加上這一條就夠了，可以直接用 `INSERT … SELECT` 跨資料庫複製。

`backend/.env.example` 的 `MYSQL_DATABASE` 本來就寫 `fluencytides_corpus`，名稱沿用。

## 4. 設計決策

### D0 重新檢視：新庫不沿用舊表的結構

> 2026-11-01 改版：這是全新的資料庫，表結構不受舊 `scripts` 拘束，只依「現在的資料與用途」設計。舊表的內容與舊程式的需求，改由兩個隔離層處理：**原樣封存表**（`game_legacy_scripts`）與**相容檢視表**（`scripts`）。新表裡不放任何為了相容而存在的欄位。

| 初稿的做法 | 問題 | 改版 |
|---|---|---|
| `game_lines` 帶 `legacy_filename`、`legacy` JSON、`number`、`voice_role` 等舊欄位 | 新表被舊結構污染；這些欄位除了相容檢視表沒人用 | 舊表**原樣**複製到 `game_legacy_scripts`，新表一個舊欄位都不放 |
| 每句都重複存場景檔名與標籤 | 101 個檔、415 個場景標籤重複 5.3 萬次；檔案標題無處可放 | 拆出 `game_script_files`、`game_scenes` |
| 錨點獨立成 `line_anchors` | 一句只有一個位置，拆表只多一次 join；而且唯一性（同一場景不能有兩句同 point）跨表無法約束 | 位置（`point`）、縮圖、BGM 等直接放 `game_lines`，`UNIQUE (scene_id, point)` |
| 每句的 BGM 存成 JSON | 42 首曲子的曲名要寫進 4.6 萬列；「哪些台詞用了某首曲子」查不到 | `game_bgm_tracks` ＋ `game_line_bgm` |
| 語言拆成 `line_texts`（一句一語一列） | 使用者要求多語欄位一律寫成「欄位＋語言」；目前固定兩種語言，拆表只增加 join | 改成欄位：`text_ja_jp`、`text_zh_hant`；加語言就加欄位（5.3 萬列的 `ALTER TABLE` 很快） |
| 場景怎麼接、選項與變數只存在校準檔案（`calib_state.json`、`calib_choices.jsonl` 等） | 流程圖要依賴本機檔案；其他功能查不到 | 新增 `game_scene_links` |
| 說話者名字直接寫在每句台詞上 | 80 位說話者重複 3 萬多次；繁中名要寫 3 萬列 | 新增 `game_speakers`，台詞只存 `speaker_id` |
| 遷移 `evaluation` | 0 列，程式中沒有任何地方讀寫這張表（`evaluation` 字樣都是語音評分的程式） | 不遷移，留在 `yuzusoft` |
| `title`、`title_original` | 不知道哪個是哪種語言 | `title_ja_jp`、`title_zh_hant` |
| 卡片紀錄只為一款遊戲設計：遊戲靠字串區分（卡片紀錄是 `SabbatOfTheWitch`、詞彙索引是 `サノバウィッチ`），詞只存原形 | 加第二款遊戲就分不清；其他語言的詞會混在一起 | 改用 `game_id` 外鍵；詞改成帶語言的 `term_key`（D2） |
| 遊戲設定在 `.env` 只有一組，而且兩種卡片類型各抄一份（`JP_VERB_PAIR_*`、`JP_CORE_VERB_*` 的 `SOURCE_GAME`、`GAME_NAME_JP`、`VOICE_DIR`、`AVATAR_DIR`） | 換遊戲要改 `.env`；兩份可能不一致 | 移到 `game_profiles`，`.env` 只留素材根目錄 `GAME_ASSETS_ROOT`（D2、D9 的 H） |
| 表名沒有分類 | 表一多就看不出哪些屬於同一個系統 | 依用途加前綴（見下方命名規則） |

**命名規則**
- **語言代碼**（資料值）：BCP 47，`ja-JP`、`zh-Hant`。出現在 `game_profiles.languages`、API 回應、前端設定中。
- **多語欄位名**：`<欄位>_<語言代碼>`，語言代碼轉小寫、`-` 換成 `_`：`title_ja_jp`、`title_zh_hant`、`text_ja_jp`。
- 為什麼欄位名不用 `title_ja-JP`：MySQL 的欄位名含 `-` 時，每一條 SQL 都要加反引號；而且 Python（SQLModel）與 TypeScript 的屬性名不能有 `-`，對應時還要另外寫別名。用底線的話，規則是一對一的，程式可以自動由語言代碼產生欄位名。
- **表名前綴**：依用途分成五個系列，同一系列的表用同一個前綴，看表名就知道它屬於哪個系統。程式裡的表名集中定義成常數，不在各處寫死。

| 前綴 | 系列 | 表 |
|---|---|---|
| `game_` | 文字遊戲的內容與結構（不綁定單一遊戲） | `game_profiles`、`game_script_files`、`game_scenes`、`game_scene_links`、`game_speakers`、`game_lines`、`game_bgm_tracks`、`game_line_bgm`、`game_line_revisions`、`game_legacy_scripts` |
| `nlp_` | 對台詞做的語言標注 | `nlp_line_terms`、`nlp_line_readings`（之後的詞表 `nlp_terms`） |
| `card_` | 建卡管線的帳本 | `card_gen_log` |
| `rel_` | 通用關係圖：卡片、台詞、詞、場景之間的關係 | `rel_entity_types`、`rel_types`、`rel_links` |
| `story_` | 劇情流程圖的版本 | `story_builds`、`story_node_lines` |

- 卡片紀錄放 `card_` 而不是 `rel_`：它記的是生成流程（失敗次數、模型、刪除次數），只有建卡管線使用；給各種關係圖共用的關聯放在 `rel_links`。
- 根表叫 `game_profiles` 而不是 `game_games`：一列是一款遊戲的基本資料與設定。
- 外鍵與索引的名字不加前綴；外鍵名在整個資料庫內唯一即可。

### D1 表的總覽

一共 **16 張表**：新表 13 張（`game_` 10 張、`rel_` 3 張）、從 `yuzusoft` 遷移並改成多遊戲欄位的 3 張（相容檢視表依 D9 不建立），以及流程圖計畫的 2 張表（在 `story_flowchart_FEAT` 定義，這裡只列名字）。

| 表 | 一列代表 | 魔女的夜宴列數 | 來源 |
|---|---|---|---|
| `game_profiles` | 一款遊戲 | 1 | 新建 |
| `game_script_files` | 一個場景檔 | 101 | 錨點的場景名稱 |
| `game_scenes` | 一個場景標籤 | 415 | 錨點的場景名稱 |
| `game_speakers` | 一位說話者，含日繁名字 | 80 | 錨點的 `role`；繁中名由縮圖讀出（F11） |
| `rel_entity_types` | 一種可以當關係圖節點的東西 | 6 | 新建（D8） |
| `rel_types` | 一種關係 | 6 | 新建（D8） |
| `rel_links` | 一條關係（卡片 ↔ 卡片、卡片 ↔ 台詞、卡片 ↔ 詞…） | 約 3.7 萬 | 由 `card_gen_log` 回填（D8） |
| `game_scene_links` | 場景之間的一條連接（自然接續、選項、跳轉、通關後入口） | 約 500 | 校準檔案 |
| `game_lines` | 一句台詞（核心） | 53,242 | 舊表 53,190 ＋ 還原 6 ＋ 選項 46 |
| `game_bgm_tracks` | 一首 BGM | 50（台詞用到 42 首，另有標題 BGM 與 7 首歌曲） | `file_titles.json` |
| `game_line_bgm` | 某句台詞播放的某首 BGM | 約 5 萬（49,472 句有 BGM，少數同時有 2 首） | 錨點 |
| `game_line_revisions` | 一筆資料修復或變更紀錄 | 數十筆 | 遷移腳本與之後的修正 |
| `game_legacy_scripts` | 舊 `scripts` 的一列，**原樣封存** | 53,190 | `yuzusoft.scripts` 逐欄複製 |
| `card_gen_log` | 一筆卡片生成紀錄 | 6,265 | `yuzusoft` 的 `generated_sentences_log`，id 不變，欄位改成多遊戲（D2） |
| `nlp_line_terms` | 一筆詞彙索引 | 9,184 | `yuzusoft` 的 `dialogue_terms_index`，id 不變，欄位改成多遊戲（D2） |
| `nlp_line_readings` | 一筆讀音判定 | 0 | `yuzusoft` 的 `jp_verb_reading_judgments`（只有結構） |
| `story_builds`、`story_node_lines` | 劇情流程圖的版本與節點成員 | — | 見 `story_flowchart_FEAT` D2 |

```
game_profiles
 ├─ game_script_files ─── game_scenes
 │                         ├─ game_scene_links（場景 → 場景：接續、選項、跳轉、通關後入口）
 │                         └─ game_lines ─── game_speakers（說話者）
 │                              ├─ game_line_bgm ─── game_bgm_tracks
 │                              ├─ game_line_revisions
 │                              ├─ card_gen_log、nlp_line_terms、nlp_line_readings
 │                              └─ story_node_lines（流程圖）
 ├─ game_bgm_tracks
 └─ story_builds（流程圖）
rel_entity_types ─── rel_types ─── rel_links（兩端都是「類型＋ID」，可以指向筆記、台詞、場景、說話者、BGM、詞）
game_legacy_scripts（封存，id 與 game_lines 相同，不建外鍵）
```

**共同規則**
- 字元集 `utf8mb4`。鍵欄位與日文比對欄位用 `utf8mb4_bin`；`unicode_ci` 會把濁音、全半形視為相同。
- 指向台詞的外鍵一律是 `game_lines(id)`；卡片相關表維持舊表 `ON DELETE CASCADE` 的行為。
- **台詞不實際刪除**，只把 `status` 改成 `inactive`。這次會出現 6 句被刪掉的台詞，就是因為以前是真的刪除。
- 檔名一律存 NFC。

### D2 各表欄位

#### `game_profiles` —— 遊戲

一款遊戲一列：名稱、引擎、語言，以及建卡管線需要的遊戲設定（原本在 `.env`，見 D9 的 H）。

```sql
CREATE TABLE game_profiles (
  game_id        VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  title_ja_jp    VARCHAR(191) NOT NULL,
  title_zh_hant  VARCHAR(191) NULL,
  engine         VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  languages      JSON         NOT NULL,
  voice_dir      VARCHAR(191) COLLATE utf8mb4_bin NULL,
  avatar_dir     VARCHAR(191) COLLATE utf8mb4_bin NULL,
  legacy_source  VARCHAR(255) COLLATE utf8mb4_bin NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (game_id),
  UNIQUE KEY uk_legacy_source (legacy_source)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 魔女的夜宴 |
|---|---|---|
| `game_id` | 代號，網址、API、卡片紀錄都用它，建立後不改。**它同時是 Anki 媒體檔的前綴**（`SabbatOfTheWitch_<檔名>.mp3`），刪卡工具也靠這個前綴判斷哪些媒體檔可以刪，所以必須沿用舊設定 `JP_*_SOURCE_GAME` 的值 | `SabbatOfTheWitch` |
| `title_ja_jp` | 日文標題；卡片上顯示的遊戲名稱（原 `JP_*_GAME_NAME_JP`） | サノバウィッチ |
| `title_zh_hant` | 繁中標題 | 魔女的夜宴 |
| `engine` | 遊戲引擎與存檔格式，決定 `point` 的意義與存檔寫入器 | `kirikiri_packinone` |
| `languages` | 這款遊戲有哪些語言的台詞（BCP 47） | `["ja-JP", "zh-Hant"]` |
| `voice_dir` | 語音原檔的資料夾，相對於 `.env` 的 `GAME_ASSETS_ROOT`（原 `JP_*_VOICE_DIR`）；也取代舊表每一列重複的 `audio_path` | `voice/yuzusoft/SabbatOfTheWitch` |
| `avatar_dir` | 頭像資料夾，同樣相對於 `GAME_ASSETS_ROOT`（原 `JP_*_AVATAR_DIR`）；檔名見 `game_speakers.avatar_file` | `avatar/yuzusoft/SabbatOfTheWitch` |
| `legacy_source` | 舊 `scripts.source` 的值；遷移與驗證時對照舊表用 | `サノバウィッチ` |

**為什麼資料夾存相對路徑**：新庫是本機與正式機共用的，絕對路徑（`C:\Users\…\material\voice\…`）換一台電腦就失效。每台電腦不同的只有素材根目錄，放在 `.env` 的 `GAME_ASSETS_ROOT`（本機是 `C:\Users\forip\Desktop\WorkSpace\material`）。新增遊戲只要在這張表加一列，`.env` 不用動。

> 初稿的 `log_source`（卡片紀錄用的 `SabbatOfTheWitch`）拿掉：卡片紀錄改用 `game_id`，而 `game_id` 就是這個值。

#### `game_script_files` —— 場景檔

```sql
CREATE TABLE game_script_files (
  file_id        INT          NOT NULL AUTO_INCREMENT,
  game_id        VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  file_name      VARCHAR(191) COLLATE utf8mb4_bin NOT NULL,
  file_no        VARCHAR(16)  COLLATE utf8mb4_bin NOT NULL,
  title_ja_jp    VARCHAR(191) NOT NULL,
  title_zh_hant  VARCHAR(191) NULL,
  file_order     INT          NOT NULL,
  PRIMARY KEY (file_id),
  UNIQUE KEY uk_game_file (game_id, file_name),
  UNIQUE KEY uk_game_order (game_id, file_order),
  CONSTRAINT fk_files_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 例 |
|---|---|---|
| `file_name` | 場景檔名（NFC） | `002.共通－欠片吸収.ks` |
| `file_no` | 檔名前面的編號 | `002`、`109_β` |
| `title_ja_jp` | 檔名去掉編號與副檔名；流程圖的節點標題預設用它 | `共通－欠片吸収` |
| `title_zh_hant` | 繁中標題；遊戲沒有提供，先留空，之後人工補 | NULL |
| `file_order` | 檔案的劇情順序 | 2 |

#### `game_scenes` —— 場景標籤

```sql
CREATE TABLE game_scenes (
  scene_id     INT          NOT NULL AUTO_INCREMENT,
  file_id      INT          NOT NULL,
  label        VARCHAR(191) COLLATE utf8mb4_bin NOT NULL,
  scene_order  INT          NOT NULL,
  PRIMARY KEY (scene_id),
  UNIQUE KEY uk_file_label (file_id, label),
  CONSTRAINT fk_scenes_file FOREIGN KEY (file_id) REFERENCES game_script_files (file_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 例 |
|---|---|---|
| `label` | 場景標籤（存檔的 `scene` 是 `<file_name>*<label>`） | `chapter14`、`009_01A`、`dummyselect1`、`chapter after` |
| `scene_order` | 檔案內的劇情順序 | |

> 場景的「類型」（本線、選項分支、合流、條件跳轉）屬於流程圖的解讀，放在流程圖的建置包，不放這裡。

#### `game_scene_links` —— 場景之間的連接與選項

```sql
CREATE TABLE game_scene_links (
  link_id               INT          NOT NULL AUTO_INCREMENT,
  game_id               VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  from_scene_id         INT          NOT NULL,
  to_scene_id           INT          NULL,
  to_point              INT          NULL,
  kind                  ENUM('next','choice','jump','unlock') NOT NULL,
  option_index          TINYINT      NULL,
  option_text_ja_jp     VARCHAR(255) COLLATE utf8mb4_bin NULL,
  option_text_zh_hant   VARCHAR(255) NULL,
  condition_text_ja_jp  VARCHAR(255) NULL,
  condition_text_zh_hant VARCHAR(255) NULL,
  condition_vars        JSON         NULL,
  effects               JSON         NULL,
  raw_expression        VARCHAR(255) COLLATE utf8mb4_bin NULL,
  via                   VARCHAR(32)  COLLATE utf8mb4_bin NULL,
  evidence              ENUM('observed','probed','inferred') NOT NULL,
  note                  VARCHAR(255) NULL,
  PRIMARY KEY (link_id),
  UNIQUE KEY uk_link (from_scene_id, kind, option_index, to_scene_id),
  KEY idx_to (to_scene_id),
  CONSTRAINT fk_links_game FOREIGN KEY (game_id)       REFERENCES game_profiles (game_id),
  CONSTRAINT fk_links_from FOREIGN KEY (from_scene_id) REFERENCES game_scenes (scene_id),
  CONSTRAINT fk_links_to   FOREIGN KEY (to_scene_id)   REFERENCES game_scenes (scene_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 魔女的夜宴的來源 |
|---|---|---|
| `from_scene_id`、`to_scene_id` | 從哪個場景接到哪個場景；`to_scene_id` 為 NULL 表示結束（例：ノーマルエンド之後） | `calib_state.json` 的 `next_scene`、`calib_choices.jsonl` 的 `to_scene` |
| `to_point` | 接過去時落在目標場景的哪個 point（通常是 1 或 2） | `next_point`、`to_point` |
| `kind` | `next`：場景結束自然接下一個。`choice`：玩家選項。`jump`：依變數自動跳轉（`dummyN` 依 `sel_flag`、019 結尾依好感決定路線）。`unlock`：通關後才出現的入口（寧々 RESTART、各路線 AFTER、和奏） | 依來源判斷 |
| `option_index` | 選項的順序（0 起算，等於遊戲選單由上到下） | `calib_choices.jsonl` 的 `index` |
| `option_text_ja_jp`、`option_text_zh_hant` | 選項文字 | 日文來自 `option`；繁中由選項畫面那一句（`kind=choice`）的繁中台詞依「 / 」拆開 |
| `condition_text_ja_jp`、`condition_text_zh_hant` | 給人看的條件說明，例「任一路線通關後」「好感旗標不足」 | 人工填寫（只有 `jump`、`unlock`） |
| `condition_vars` | 實測可以走這條連接的一組變數，例 `{"nen_flag": 4}`。注意這是「實測的例子」，不是門檻 | `calib_route_probe.jsonl`、`calib_results_vars.jsonl` |
| `effects` | 走這條連接會怎麼改變數，例 `[{"var": "sel_flag", "op": "set", "value": 0}, {"var": "wak_flag", "op": "add", "value": 1}]`；流程圖的存檔變數就是沿路徑累加它 | `calib_choice_vars.jsonl` 的 `exp` 解析 |
| `raw_expression` | 遊戲內的原始運算式，例 `f.sel_flag = 0 , f.wak_flag ++`，保留以便查證 | 同上 |
| `via` | 連接途中經過的演出，例 `movie`（播放 OP 動畫） | `calib_state.json` 的 `via` |
| `evidence` | `observed`：校準時實際走到。`probed`：帶變數探測確認。`inferred`：依章節編號或攻略推測（和奏入口、寧々 RESTART） | |
| `note` | 補充說明 | |

**為什麼放進語料庫**：場景怎麼接、選項有哪些、選了會改什麼變數，是遊戲本身的結構，不是流程圖的畫法。放在資料庫之後：
- 流程圖的擷取器只讀資料庫，不必依賴本機的校準檔案。
- 以後其他功能（例如「這句台詞要走哪些選項才看得到」）也能直接查。

魔女的夜宴約 500 筆：
- 自然接續 371 筆。
- 選項 94 筆。
- `dummyN` 跳轉與路線分歧數十筆。
- 通關後入口約 7 筆。

#### `game_speakers` —— 說話者

```sql
CREATE TABLE game_speakers (
  speaker_id       INT          NOT NULL AUTO_INCREMENT,
  game_id          VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  name_ja_jp       VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  name_zh_hant     VARCHAR(64)  NULL,
  name_zh_hant_src ENUM('game','manual') NULL,
  voice_prefix     VARCHAR(32)  COLLATE utf8mb4_bin NULL,
  avatar_file      VARCHAR(64)  NULL,
  sort_order       INT          NOT NULL DEFAULT 0,
  PRIMARY KEY (speaker_id),
  UNIQUE KEY uk_game_name (game_id, name_ja_jp),
  CONSTRAINT fk_speakers_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 例 |
|---|---|---|
| `speaker_id` | 說話者 ID；`game_lines.speaker_id` 指向它，旁白是 NULL | |
| `name_ja_jp` | 日文名，一款遊戲內唯一 | 柊史、寧々、女子クラスメイト |
| `name_zh_hant` | 繁中名 | 柊史、寧寧 |
| `name_zh_hant_src` | 繁中名的來源：`game`（從遊戲畫面讀出）、`manual`（人工填寫） | |
| `voice_prefix` | 語音檔名的前綴，方便由語音反查角色 | `nen`（`nen002_039`） |
| `avatar_file` | 頭像檔名，在 `game_profiles.avatar_dir` 底下；NULL 時用現行規則 `<name_ja_jp>.jpg` | NULL（寧々 → `寧々.jpg`） |
| `sort_order` | 顯示順序（主角、女主角在前） | |

魔女的夜宴有 80 位說話者（另有 21,867 句是旁白），其中台詞最多的是：柊史 12,194 句、寧々 4,135 句、めぐる 3,236 句、紬 2,736 句、憧子 2,509 句、和奏 1,600 句。

**繁中名從哪裡來**：中文輪只記錄台詞文字，沒有記錄名字。但中文輪的縮圖是遊戲在繁中模式下的畫面截圖，名字欄就在畫面上。所以每位說話者挑 1～2 張縮圖讀出名字欄（F11），不需要再跑遊戲。

> 遊戲在角色登場前可能顯示「？？？」之類的暫時名稱，這屬於「這一句顯示的名字」，不是角色本身的名字。這次不另外記錄，如果之後需要，放在 `game_lines.attrs.display_name_<語言>`。

#### `game_lines` —— 台詞（核心）

```sql
CREATE TABLE game_lines (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  game_id            VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  scene_id           INT          NOT NULL,
  seq                INT          NOT NULL,
  story_order        INT          NOT NULL,
  kind               ENUM('line','choice','bgv') NOT NULL DEFAULT 'line',
  point              INT          NULL,
  chapter_label      VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  speaker_id         INT          NULL,
  text_ja_jp         VARCHAR(1024) COLLATE utf8mb4_bin NOT NULL,
  text_zh_hant       VARCHAR(1024) NULL,
  ruby_ja_jp         VARCHAR(1024) COLLATE utf8mb4_bin NULL,
  voice_file         VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  is_h               TINYINT(1)   NOT NULL DEFAULT 0,
  h_label            VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  thumb              CHAR(16)     COLLATE utf8mb4_bin NULL,
  bgm_detect         VARCHAR(16)  NULL,
  bgm_may_change     TINYINT(1)   NOT NULL DEFAULT 0,
  attrs              JSON         NULL,
  status             ENUM('active','inactive') NOT NULL DEFAULT 'active',
  in_corpus          TINYINT(1)   NOT NULL DEFAULT 1,
  origin             ENUM('legacy','restored','game_only') NOT NULL,
  anchor_method      VARCHAR(16)  COLLATE utf8mb4_bin NULL,
  anchor_verified_at DATETIME     NULL,
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_scene_seq (scene_id, seq),
  UNIQUE KEY uk_scene_point (scene_id, point),
  UNIQUE KEY uk_game_order (game_id, story_order),
  KEY idx_voice (voice_file),
  KEY idx_thumb (thumb),
  CONSTRAINT fk_lines_game  FOREIGN KEY (game_id)  REFERENCES game_profiles (game_id),
  CONSTRAINT fk_lines_scene FOREIGN KEY (scene_id) REFERENCES game_scenes (scene_id),
  CONSTRAINT fk_lines_speaker FOREIGN KEY (speaker_id) REFERENCES game_speakers (speaker_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 魔女的夜宴的來源 |
|---|---|---|
| `id` | **沿用舊 `scripts.id`**（卡片紀錄、ES 的 `_id`、Anki 工具、`exclude_script_ids` 都用它）；新增的台詞從 53197 起編 | 舊表 |
| `game_id` | | |
| `scene_id` | 所屬場景 | 錨點 `scene` |
| `seq` | 場景內的順序，從 0 起算 | 依 point 排序 |
| `story_order` | 全作的劇情順序，流程圖與「前後文」都用它 | 對齊時產生（D4） |
| `kind` | `line` 一般台詞；`choice` 選項畫面（文字是「選択肢：選項1 / 選項2」）；`bgv` 背景語音：不顯示在對話框、只在其他台詞背後播放（沒有 `point`，播放位置記在 `attrs.plays_at`） | 46 句選項列；1043 是 `bgv` |
| `point` | **存檔位置**：場景內的 point；NULL 表示還沒校準 | 錨點 `point` |
| `chapter_label` | 舊資料庫的章節名，例 `chapter　1-4`、`Epilogue`、`After Story`；流程圖的章節帶依它分組 | 舊表 `chapter`（舊表沒有的列沿用前一句）。錨點的 `chapter_jp` 在エピローグ、アフター用了不同的名字，不採用 |
| `speaker_id` | 說話者，指向 `game_speakers`；旁白是 NULL | 舊表 `role_name`；舊表沒有的列用錨點 `role`。錨點有 2 千多句台詞缺名字，不能當主要來源 |
| `text_ja_jp` | 日文台詞，以**遊戲實際顯示**的文字為準 | 舊表 `dialogue` 去掉振假名（保留舊表的換行）；和錨點去掉空白後相同才採用，否則用錨點 `text_jp`（9516） |
| `text_zh_hant` | 繁中台詞 | 錨點 `text_tw`（中文輪） |
| `ruby_ja_jp` | 帶振假名標記的日文 `[よみ]漢字`，只有舊表有標記的 119 句才有值 | 舊表 `dialogue` |
| `voice_file` | 語音檔名；完整路徑是 `.env` 的 `GAME_ASSETS_ROOT` ＋ `game_profiles.voice_dir` ＋ 檔名 | 舊表 `audio_file` |
| `is_h`、`h_label` | H 場景與它的段落標籤，例 `ne_H_1` | 錨點 `is_h`、`h_label` |
| `thumb` | 縮圖雜湊（MinIO 檔名） | 錨點 `thumb` |
| `bgm_detect` | BGM 的偵測方式：`A`（直接監看到，40,708 句）、`實測`（逐句驗證確認，12,529 句，包含確認沒有 BGM 的句子）、`B推定`（由曲名浮窗推得，2 句）、NULL（2 句，選項畫面） | 錨點 `bgm_source` |
| `bgm_may_change` | 這句播放中 BGM 可能切換 | 錨點 `bgm_may_change` |
| `attrs` | 引擎或遊戲專屬、不常查詢的資料：`segment`、`replay_unlock`、`cg_unlock`、`cg_unlock_vars`、`vars`、`flags_new` | 錨點對應欄位 |
| `status` | `inactive` 等同以前的「刪除」 | 全部 `active` |
| `in_corpus` | 是否給建卡與搜尋用；0 時建卡管線與 ES 同步都不讀，但流程圖照樣顯示 | 見 D6 |
| `origin` | `legacy`：舊表就有。`restored`：舊表刪掉、從遊戲還原。`game_only`：只有遊戲裡有（選項畫面） | |
| `anchor_method`、`anchor_verified_at` | `point` 的取得方式（`probe` 直接探測、`fill` 補掃、`manual` 人工）與確認時間 | 校準紀錄 |
| `created_at`、`updated_at` | | |

**`attrs` 的範圍**：只放「依遊戲而不同、而且不會拿來篩選」的資料。需要篩選或 join 的，一律做成正式欄位或獨立的表（例：H 是 `is_h`，BGM 是 `game_line_bgm`）。

#### `game_bgm_tracks` —— BGM 曲目

```sql
CREATE TABLE game_bgm_tracks (
  track_id       INT          NOT NULL AUTO_INCREMENT,
  game_id        VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  code           VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  archive_index  INT          NULL,
  gallery_no     INT          NULL,
  title_ja_jp    VARCHAR(191) NULL,
  title_zh_hant  VARCHAR(191) NULL,
  PRIMARY KEY (track_id),
  UNIQUE KEY uk_game_code (game_id, code),
  CONSTRAINT fk_bgm_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 例 |
|---|---|---|
| `code` | 遊戲內的曲目代號 | `BGM02`、`#15` |
| `archive_index` | `bgm.xp3` 中的檔案序號 | 3 |
| `gallery_no` | EXTRA 音樂鑑賞畫面的編號；不在鑑賞畫面的（BGM20I、標題 BGM51）是 NULL | 4 |
| `title_ja_jp`、`title_zh_hant` | 曲名，一首只寫一次。日文取自曲名浮窗與日文介面的鑑賞畫面，繁中取自繁中介面的鑑賞畫面 | 気の合う仲間たち／意氣相投的朋友們 |

#### `game_line_bgm` —— 台詞播放的 BGM

```sql
CREATE TABLE game_line_bgm (
  line_id   BIGINT UNSIGNED NOT NULL,
  ord       TINYINT      NOT NULL,
  track_id  INT          NOT NULL,
  PRIMARY KEY (line_id, ord),
  KEY idx_track (track_id),
  CONSTRAINT fk_lbgm_line  FOREIGN KEY (line_id)  REFERENCES game_lines (id) ON DELETE CASCADE,
  CONSTRAINT fk_lbgm_track FOREIGN KEY (track_id) REFERENCES game_bgm_tracks (track_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 |
|---|---|
| `line_id` | 台詞 |
| `ord` | 0 起算；376 句在播放中切換曲子，有 0 和 1 兩筆 |
| `track_id` | 曲目 |

#### `game_line_revisions` —— 修復與變更紀錄

```sql
CREATE TABLE game_line_revisions (
  id          BIGINT       NOT NULL AUTO_INCREMENT,
  line_id     BIGINT UNSIGNED NOT NULL,
  field       VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  old_value   TEXT         NULL,
  new_value   TEXT         NULL,
  reason      VARCHAR(255) NOT NULL,
  evidence    VARCHAR(255) NULL,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_line (line_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 |
|---|---|
| `line_id` | 被修改的台詞；不建外鍵，台詞停用後紀錄仍保留 |
| `field` | 改了什麼，例 `text_ja_jp`、`status`、`point`、`row`（整列新增） |
| `old_value`、`new_value` | 改前與改後 |
| `reason` | 例「舊資料庫錯字（が→か），依遊戲顯示修正」 |
| `evidence` | 佐證，例 `002.共通－欠片吸収.ks*chapter14@668` |

#### `game_legacy_scripts` —— 舊劇本原樣封存

- 結構與 `yuzusoft.scripts` **完全相同**（同樣的欄位、型別、排序規則），資料用 `INSERT … SELECT` 逐欄複製 53,190 列，一個字都不改。
- 用途：遷移驗證時對照舊欄位，以及日後查證「舊資料原本長什麼樣」。
- 新程式不得讀寫這張表；不建外鍵，也不建索引以外的約束。
- 舊表的錯位欄位（`dialogue_hiragana`、`dialogue_kanji`、`reserved`）、固定值欄位（`illustration_info`、`unknown`、`audio_pan`、`status`）都完整留在這裡，新表不再處理它們。

有了這張表，新庫裡也有一份完整的舊資料，不必回 `yuzusoft` 查。

#### 從 `yuzusoft` 遷移的 3 張表（欄位改成多遊戲）

舊的卡片紀錄、詞彙索引、讀音判定都只為一款遊戲設計：遊戲靠字串區分（`SabbatOfTheWitch`、`サノバウィッチ`），詞只存原形、沒有語言。搬到新庫時一併改成多遊戲的欄位。`id` 用 `INSERT … SELECT` 原樣帶過來，`AUTO_INCREMENT` 接續舊值。

##### `card_gen_log` —— 卡片生成紀錄（原 `generated_sentences_log`）

```sql
CREATE TABLE card_gen_log (
  id               INT             NOT NULL AUTO_INCREMENT,
  game_id          VARCHAR(64)     COLLATE utf8mb4_bin NOT NULL,
  line_id          BIGINT UNSIGNED NOT NULL,
  term_key         VARCHAR(255)    COLLATE utf8mb4_bin NOT NULL,
  card_type        VARCHAR(32)     COLLATE utf8mb4_bin NOT NULL,
  master_note_id   BIGINT          NOT NULL,
  context_note_id  BIGINT          NULL,
  cloze_note_id    BIGINT          NULL,
  is_deleted       TINYINT(1)      NOT NULL DEFAULT 0,
  delete_count     INT             NOT NULL DEFAULT 0,
  failure_count    INT             NOT NULL DEFAULT 0,
  llm_model        VARCHAR(255)    NULL,
  created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_line_term_type (line_id, term_key, card_type),
  KEY idx_term (term_key),
  KEY idx_master (master_note_id),
  KEY idx_game_type (game_id, card_type),
  CONSTRAINT fk_cgl_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id),
  CONSTRAINT fk_cgl_line FOREIGN KEY (line_id) REFERENCES game_lines (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 舊欄位 | 新欄位 | 遷移時怎麼轉 |
|---|---|---|
| `script_id` | `line_id` | 值不變（`game_lines.id` 沿用舊 id） |
| `source` | `game_id` | 原值 `SabbatOfTheWitch`；驗證每一筆都和台詞所屬的遊戲相同 |
| `chapter` | （拿掉） | 需要時由台詞的 `chapter_label` 查；驗證每一筆舊值都等於台詞的 `chapter_label` |
| `verb_lemma` | `term_key` | 前面加上語言：`見る` → `ja-JP:見る`，和 `rel_links` 的詞用同一個鍵 |
| `project` | `card_type` | 原值（`jp_verb_pair`、`jp_core_verb`），和卡片的 handler、`rel_links.attrs.card_type` 一致 |
| 其他（3 個 note id、`is_deleted`、`delete_count`、`failure_count`、`llm_model`、時間） | 同名 | 原值 |

- **唯一鍵**：維持舊的「台詞＋詞＋卡片類型」。台詞 id 全庫唯一，跨遊戲也不會撞。
- **`game_id` 是重複的資訊**（台詞本身就決定了遊戲）：留著是為了依遊戲查詢與統計時不必 join，由 `verify.py` 檢查它和台詞一致。
- **詞改成精確比對**：舊的 `verb_lemma` 是 `utf8mb4_unicode_ci`，會把「が」和「か」、全形和半形視為相同；`term_key` 改用 `utf8mb4_bin`。舊資料在寬鬆的規則下已經唯一，改成精確比對不會產生衝突。
- **每個詞的卡數上限**（`*_MAX_CARDS_PER_VERB`）維持「所有遊戲合計」，和現在一樣；需要依遊戲分開時再加。
- **note id 和 `rel_links` 的母卡 → 子卡是同一份資訊，刻意保留**：建卡管線靠 note id 去重與刪卡。兩邊在同一個交易寫入，由 `verify.py` 檢查一致（D9 的 B）。

##### `nlp_line_terms` —— 詞彙索引（原 `dialogue_terms_index`）

結構照舊表的 `SHOW CREATE TABLE`，只改這幾欄：

| 舊欄位 | 新欄位 | 遷移時怎麼轉 |
|---|---|---|
| `script_id` | `line_id` | 值不變；外鍵指向 `game_lines(id)`，`ON DELETE CASCADE` |
| `source`（`サノバウィッチ`） | `game_id` | 用 `game_profiles.legacy_source` 對照成 `SabbatOfTheWitch` |
| `term_lemma` | `term_key` | 前面加 `ja-JP:` |
| `term_type`、`frequency_score`、`model_name` | 同名 | 原值 |

##### `nlp_line_readings` —— 讀音判定（原 `jp_verb_reading_judgments`）

目前 0 列。`script_id` 改名 `line_id`，外鍵指向 `game_lines(id)`；其他欄位（`verb_surface`、`reading`、`llm_model`、`created_at`）不變，主鍵是 `(line_id, verb_surface)`。

#### 檢視表 `scripts` —— 相容舊程式（**不建立**，見 D9；保留作為新舊欄位的對照）

```sql
CREATE VIEW scripts AS
SELECT l.id,
       g.legacy_source                                    AS source,
       l.chapter_label                                    AS chapter,
       COALESCE(ls.filename, CONCAT(f.file_name, '.json')) AS filename,
       ls.number,
       sp.name_ja_jp                                      AS role_name,
       ls.additional_info,
       COALESCE(l.ruby_ja_jp, l.text_ja_jp)               AS dialogue,
       ls.audio_role, ls.audio_pan, ls.audio_type, ls.audio_path,
       l.voice_file                                       AS audio_file,
       ls.`unknown`, ls.illustration_info, ls.reserved,
       ls.dialogue_hiragana, ls.dialogue_kanji,
       '1'                                                AS status,
       COALESCE(ls.created_time, l.created_at)            AS created_time,
       COALESCE(ls.modified_time, l.updated_at)           AS modified_time
FROM game_lines l
JOIN game_profiles g          ON g.game_id = l.game_id
JOIN game_scenes s         ON s.scene_id = l.scene_id
JOIN game_script_files f   ON f.file_id = s.file_id
LEFT JOIN game_speakers sp      ON sp.speaker_id = l.speaker_id
LEFT JOIN game_legacy_scripts ls ON ls.id = l.id
WHERE l.status = 'active' AND l.in_corpus = 1 AND l.kind IN ('line', 'bgv');
```

- 欄位名稱、順序、意義都和舊表一致；舊程式實際用到的是 `id`、`source`、`chapter`、`role_name`、`dialogue`、`status`、`audio_file`（已查過全部 SELECT）。
- `bgv`（1043）也要輸出：它原本就在舊表裡，建卡與 ES 都看得到，不能因為改了類型就消失。
- 會改變的值只有修復過的地方，例如 9516 的 `dialogue` 會變成遊戲裡的正確文字。
- 新增的台詞（目前 `in_corpus` 全是 0）如果之後開放，沒有舊值的欄位會是 NULL。
- **這是過渡用的**：舊程式逐步改成讀新表後就可以移除。新程式一律不讀這個檢視表。

### D3 文字以哪個來源為準

| 情況 | `text_ja_jp` | `ruby_ja_jp` |
|---|---|---|
| 錨點與舊表都有，去掉振假名後相同（53,188 句） | 錨點（遊戲顯示的文字） | 舊表有標記時才有值 |
| 錨點與舊表不同（9516） | 錨點，並寫一筆 `game_line_revisions` | NULL |
| 只有舊表（1043，背景語音） | 舊表去掉振假名 | 舊表有標記時才有值 |
| 只有錨點（6 句還原、46 句選項） | 錨點 | NULL |

`text_zh_hant` 一律來自中文輪（`text_tw`）。中文輪已在 2026-11-01 04:43 完成，錨點的 53,241 句全部有繁中；1043 是背景語音，遊戲沒有顯示它的文字，所以沒有繁中（`text_zh_hant` 為 NULL）。

**實作時的調整（2026-11-01，依實際資料）**
- 日文台詞：錨點和舊表去掉空白後完全相同（53,188 句），差別只在換行：錨點沒有換行，2,441 句的舊表有。所以 `text_ja_jp` 用舊表的文字（去掉振假名、保留換行），舊格式的 `dialogue` 可以原樣組回。
- 說話者：錨點有 2 千多句台詞沒有名字（例：柊史 2,744 句），所以舊台詞以舊表 `role_name` 為準，只有新增的台詞用錨點的 `role`。兩邊都有、但不同的 34 句寫在對齊報表。
- 語音：舊台詞只用舊表的 `audio_file`。錨點在無聲台詞（主角的「………」）上會沿用前一句的語音，有 22 句是錯的。
- 章節：舊台詞用舊表的 `chapter`，卡片紀錄的 `chapter` 因此可以完全對上。

### D4 舊 id 與錨點的對齊

同一個檔案裡有一字不差的重複台詞（1,955 句），只比對文字會對錯 id。

**實際資料的結構**（2026-11-01 驗證）：每個場景標籤在舊表裡是一段連續的 id，而且只屬於一個舊章節；同一個標籤內，舊 id 的順序和錨點一致，但標籤之間的先後兩邊不同。所以整個檔案做一次序列比對行不通（只對上 52,150 句），改成分四步（`align.py`）：

1. 「台詞＋語音」在檔案內唯一的配對（19,084 句）。
2. 「台詞」在檔案內唯一、語音不衝突的配對（32,942 句）。
3. 由前兩步定出每個標籤在舊表的 id 範圍，標籤內做加權的序列比對：文字相同才能配對，語音相同、說話者相同的分數較高（1,099 句）。
4. 剩下的舊列配給同檔案、同文字、位置最近的錨點，標成**低信心**（63 句，多半是沒有語音的「………」）。

**結果**：53,188 句一一對應；舊表只剩 1043（背景語音）與 9516（錯字），錨點只剩 6 句還原、46 句選項與 9516 那一句。6 句還原的空缺位置全部吻合，**都用原 id 還原**。63 句低信心的台詞都**沒有卡片紀錄**，不影響卡片。對齊報表在 `corpus_rebuild/report/rebuild_report.json`（不進版控）。

> 校準檔案裡的 `db_number` 是之前另一次比對的結果，有 160 句重複台詞和這次不同，而且同一標籤內有 94 處順序顛倒，所以不採用。

`story_order` 依錨點順序產生，1043 插在 1042 之後。

### D5 修復清單

| # | 項目 | 做法 | 依賴 |
|---|---|---|---|
| F1 | 6 句被刪除的台詞 | **用原本的 id 還原**（1722、8330、18023、36635、43959、45037）。對齊結果確認它們在舊 id 序列中的空缺位置，和錨點的位置相符；不相符就改用新 id。`origin=restored`，各寫一筆 `game_line_revisions` | D4 |
| F11 | 繁中說話者名 | ✅ 2026-11-01：縮圖不含對話框，改在繁中模式下讀檔看名字欄（校準 session，約 35 分鐘，結果 `SW_calib\speaker_names_tw.json`，截圖 `SW_calib\speaker_names\`）。81 位中 76 位高信心，匯入 `name_zh_hant`（`name_zh_hant_src=game`），例 寧々→寧寧、めぐる→巡、アカギ→赤木。另 5 位遊戲只顯示場景稱呼（女子クラスメイト「秋田」、映画ヒロイン／映画主人公「電影」、子供Ｇ「大家一起」、子供Ｅ「男生」），先留空、畫面沿用日文名。名字欄是依場景顯示的稱呼，前期常是「女生」「？？？」，所以讀的是該角色後段的台詞 | — |
| F2 | 46 句選項畫面 | 新增 `kind=choice` 的台詞，id 從 53197 起；`in_corpus=0`（不進建卡與搜尋） | — |
| F3 | id 9516 錯字 | 日文改成遊戲顯示的文字，寫 `game_line_revisions` | — |
| F4 | id 1043 | ✅ 已確認（2026-11-01，`voicewatch.py`，日文與繁中各一次）：讀檔到 667 時遊戲停在 670 的旁白，同時開啟 `voice.xp3` 的第 392 個檔案，緊接在 1042（`nen002_039`，第 390 個）之後。匯入時 `kind=bgv`、`point=NULL`、`attrs.plays_at={"scene": "002.共通－欠片吸収.ks*chapter14", "point": 670}`，寫一筆 `game_line_revisions` | — |
| F5 | 繁中台詞 | ✅ 資料已齊：錨點檔案已重產（11/01 04:48），53,241 句都有繁中；遷移時直接匯入 | — |
| F6 | 縮圖 | ✅ 資料已齊：53,241 句都有縮圖雜湊（共 18,069 種），檔案已打包成 `thumbs.tar.xz`（64.5 MB）；壞掉的 2 張已重拍 | — |
| F7 | 來源名稱不一致 | `game_profiles.game_id` 用卡片紀錄的 `SabbatOfTheWitch`，`legacy_source` 記舊劇本的 `サノバウィッチ`；卡片紀錄與詞彙索引遷移時都換成 `game_id` | — |
| F8 | 錯位與無用的欄位 | 不做成新表的欄位，原值完整留在 `game_legacy_scripts`（D2） | — |
| F9 | BGM 曲名 | ✅ 已補並核對（2026-11-01）：以 EXTRA 的音樂鑑賞畫面逐首播放、比對開啟的檔案序號，原有 40 首曲名全部一致，補上 BGM19＝「友達」（86 句）；BGM20I 是 BGM20「優しい風」的另一個版本。另有 8 個沒在台詞播放過的檔案（標題 BGM51、7 首歌曲的 Short Ver.）也一併建檔。對照表存在 `SW_calibgm_titlesile_titles.json`（50 個檔案，含鑑賞編號），匯入 `game_bgm_tracks.title_ja_jp`。繁中曲名取自介面切成繁中的鑑賞畫面（48 首同順序），匯入 `game_bgm_tracks.title_zh_hant`；鑑賞第 21 首的繁中原文是「朋友友達」，照畫面原樣保存。只剩 BGM51（標題畫面 BGM，不顯示曲名）兩種語言都不明 | — |
| F10 | 每句的 BGM | ✅ 已驗證（2026-11-01 12:20 完成，錨點檔案 13:11 重產）：監控 session 對可疑的 11,531 句加上隨機抽樣 1,000 句，逐句觀察播放位置確認。直接偵測的隨機抽樣 1,000 句只錯 1 句；推定的句子錯得較多，其中「沿用上一句」的 4,242 句有 652 句不一致（641 句其實已經沒有 BGM），全部已改正。重產後 49,472 句有 BGM、3,769 句沒有。**遷移以重產後的 `SW_point_table.json` 為準** | — |

### D6 還原的台詞要不要進建卡與搜尋（`in_corpus`）

**已決定（2026-11-01，使用者）**：要修復，當作一般台詞，**`in_corpus=1`**。這 6 句在 Anki 沒有對應的卡片也沒關係，不補卡。

- 那 6 句都含「見」，也都沒有卡片紀錄，當初可能是建「見る」卡時刻意排除的。恢復後，之後建卡時可能被選中，使用者已接受。
- 切換後要跑一次 ES 同步（`sync_mysql_to_es.py`，以 `script_id` 當 `_id` 寫入，可以重跑），把這 6 句加進索引。
- 46 句選項畫面維持 `in_corpus=0`：它們是選單，不是台詞。

### D7 切換方式

1. 建新資料庫與授權（§3）。
2. 執行遷移腳本：建表、`game_profiles`、`game_legacy_scripts`、`game_script_files`、`game_scenes`、對齊與 `game_lines`、`game_bgm_tracks`、`game_line_bgm`、修復、卡片相關表、檢視表。腳本可以重跑，但分兩類（`data_stores_AUDIT_2026-11-01` §4）：**內容表**（`game_profiles`、`game_script_files`、`game_scenes`、`game_scene_links`、`game_speakers`、`game_lines`、`game_bgm_tracks`、`game_line_bgm`、`game_legacy_scripts`）切換前可以清空重建，切換後改為依 id 更新、不刪除既有列；**紀錄表**（卡片相關 3 張、`rel_links`、`game_line_revisions`）只在切換時遷移一次，之後任何腳本都不得清空。舊資料庫只讀。
3. **驗證**（全部通過才切換）：
   - 依本節的欄位對照，把新表組成舊格式後和舊表逐列比較（只在驗證腳本裡組，不建檢視表）：`id`、`source`、`chapter`、`filename`、`number`、`role_name`、`dialogue`、`audio_*` 完全相同（F3 的 9516 除外，差異只有那一處）。
   - `game_legacy_scripts` 與舊表逐欄完全相同。
   - 3 張遷移的表列數與 id 和舊表相同；所有 `line_id` 都在 `game_lines` 中；欄位轉換符合 D2 的對照（`term_key` 去掉 `ja-JP:` 等於舊的 `verb_lemma`，舊的 `chapter` 等於台詞的 `chapter_label`，`game_id` 等於台詞所屬的遊戲）。
   - 用新資料庫跑後端測試，以及建卡管線的乾跑（不寫 Anki）。
   - ES 同步乾跑：產生的文件和現在的索引相同。
4. **凍結**：切換期間不建卡、不刪卡（舊庫的 `generated_sentences_log` 不能有新的寫入），以免兩邊不一致。
5. 切換：本機 `backend/.env` 的 `MYSQL_DATABASE` 改為 `fluencytides_corpus`（先備份），正式機 `.env` 用 sudo 指令修改後重啟（實作時附指令）。
6. 跑一次 ES 同步（改寫後的版本讀新庫），把還原的 6 句加進索引（D6）。
7. `yuzusoft` **保留不動**（2026-11-01 使用者決定：不刪除、不改權限）；網站的 SQLite 也一樣保留不動。
8. **回滾**：把 `MYSQL_DATABASE` 改回 `yuzusoft`。如果切換後已經有新的卡片紀錄，要先把那些列寫回舊表，所以切換後一週內要保留回滾腳本。

### D8 卡片與內容的通用關聯：採用 `rel_links`

> **已決定（2026-11-01，使用者）**：這次遷移就導入通用關係表，取代初稿的 `card_links`。整體設計（實體、關係、圖提供者、排版引擎四層）見 `docs/wip/relation_graph_FEAT_draft_2026-11-01.md`；本計畫負責其中的 **R0**：建表、登記類型、從既有資料回填。

**為什麼不只用 `card_gen_log`**：它混了兩件事，「生成流程的帳本」（失敗、刪除次數、模型）和「關聯本身」（哪張筆記取自哪句台詞）。而且帳本的欄位是為「母卡加兩張子卡」這種形態設計的，其他形態的關係放不進去。帳本留在 `card_gen_log`，關聯本身抽到 `rel_links`。

**新增的 3 張表**（DDL 以草案 D1–D3 為準，`schema.sql` 照抄）：

| 表 | 一列代表 | 初始列數 |
|---|---|---|
| `rel_entity_types` | 一種可以當節點的東西 | 6 |
| `rel_types` | 一種關係，由系統或卡片類型註冊 | 6（`core.*` 4 種＋網站上使用者建立的 `user.synonym`、`user.conversation`） |
| `rel_links` | 一條關係（兩端都是「類型＋ID」） | 約 3.7 萬（回填：母卡→子卡 12,054、子卡→台詞 12,054、卡→詞 12,540） |

**`rel_entity_types` 初始值**

| `entity_type` | `source` | `id_kind` | ID 的寫法 |
|---|---|---|---|
| `note` | `anki` | int | Anki note id |
| `script_line` | `game_lines` | int | `game_lines.id` |
| `scene` | `game_scenes` | int | `game_scenes.scene_id` |
| `speaker` | `game_speakers` | int | `game_speakers.speaker_id` |
| `bgm_track` | `game_bgm_tracks` | int | `game_bgm_tracks.track_id` |
| `term` | `nlp_terms`（之後才建表） | string | `<語言代碼>:<原形>`，例 `ja-JP:見る` |

> **詞（`term`）先不建表**：ID 直接用「語言＋原形」的字串，`rel_links` 就能先指向它；之後要建 `nlp_terms` 表放多語說明時，用同一個字串當主鍵，已經寫入的關係不用改。

**`rel_types` 初始值**

| `relation_type` | `owner` | 端點 | 說明 | 回填來源 |
|---|---|---|---|---|
| `core.has_context` | `system` | `note` → `note` | 母卡 → 情境子卡 | `card_gen_log.master_note_id` → `context_note_id` |
| `core.has_cloze` | `system` | `note` → `note` | 母卡 → 填空子卡 | `master_note_id` → `cloze_note_id` |
| `core.example_from` | `system` | `note` → `script_line` | 子卡的例句取自這句台詞 | `context_note_id`、`cloze_note_id` → `line_id` |
| `core.teaches` | `system` | `note` → `term` | 這張卡學的詞 | 母卡與子卡 → `term_key` |

回填時的 `rel_links.attrs` 記 `{"card_type": "<card_type>"}`，`created_by = migration`；`is_deleted=1` 的紀錄回填成 `status=deleted`。

**和網站 SQLite 的 `card_relations` 的關係**
- `card_relations` 的 `has_context`、`has_cloze`（本機 172 列）和上表前兩種是同一件事。
- 網站的知識圖譜也在這次改讀寫 `rel_links`，見 D9 的 C。

**切換後的同步**（已由 D9 取代：建卡與刪卡直接寫 `rel_links`，不需要同步腳本）：原本規劃建卡管線照舊只寫 `card_gen_log`，新增 `sync_relations.py`，依 `card_gen_log.updated_at` 增量補進 `rel_links`，可以重跑。改建卡管線直接寫 `rel_links` 是草案 R3 之後的事。

**對流程圖的好處**：註記提供者查 `rel_links WHERE relation_type='core.example_from' AND dst_type='script_line'`。以後任何從遊戲台詞做的卡片類型，只要寫入這種關係，就會自動出現在流程圖上。

### D9 程式一步到位改讀新庫（不建相容檢視表）

> **已決定（2026-11-01，使用者）**：這次遷移一步到位。`yuzusoft` 與網站的 SQLite 原樣保留不動，但**所有程式都改讀寫新庫**，包含網站的卡片關聯（C）。

**為什麼不建相容檢視表**：舊表留在 `yuzusoft`，程式連的是 `fluencytides_corpus`，新庫裡沒有 `scripts` 這張表。漏改的程式會直接報「表不存在」，不會在不知情的情況下讀到舊資料或過時的資料。所以 D2 的檢視表 `scripts` **不建立**，該節保留當作欄位對照參考。

**要改的程式**（2026-11-01 盤點）

| # | 範圍 | 檔案 | 做法 |
|---|---|---|---|
| A | 讀舊劇本 `scripts` | 13 個檔案、約 20 處 SQL：建卡管線（JP_CoreVerb、JP_VerbPair 的 `generate_child_cards.py`、`context_builder.py`）、讀音判定、詞彙索引、ES 同步、刪卡工具、`log_repository.py`、`init_db.py` | 新增共用模組 `backend/scripts/common/database/corpus_repository.py`：`get_lines(ids)`、`get_context(line_id, prev, next)`、`iter_lines(game_id, chapter=None)`。各處改呼叫它，不再自己寫 SQL |
| B | 卡片紀錄的寫入 | `log_repository.py`（核心）、刪卡工具 `deletion/`（`id_deleter.py`、`integrity.py`、`cleanup.py`）、`mark_skipped_es_data.py` | `card_gen_log` 依 D2 改成多遊戲的欄位。`log_repository.py` 的函式名稱不變，呼叫端不再傳 `source`、`chapter`，詞照舊傳原形，由 repository 組成 `term_key`、由台詞查出 `game_id`。建立紀錄時同時寫 `rel_links`（`core.has_context`、`core.has_cloze`、`core.example_from`、`core.teaches`）；刪卡時把對應的關係標成 `deleted`。D8 的 `sync_relations.py` 因此不需要 |
| C | 網站的卡片關聯 | `app/services/relation_service.py`、`app/api/relations.py`、`app/schemas/relation.py`、3 個 handler（`jp_core_verb`、`jp_verb_pair`、example）、`scripts/local_anki/import_cards_from_json.py`、`tests/test_relation_sync.py` | `CardRelation`／`RelationType`（SQLite）改成讀寫新庫的 `rel_links`／`rel_types`。對外的 API（`/relations/graph` 等）回應格式不變，前端不用改。SQLite 原樣留著，程式不再讀寫 |
| D | ES 同步 | `sync_mysql_to_es.py` | 改讀 `game_lines`（`in_corpus=1`、`status=active`、`kind IN ('line','bgv')`）；ES 文件格式不變（`script_id`、`source`、`dialogue`），搜尋的程式不用改 |
| E | 讀音判定、詞彙索引 | `reading_judgment_repository.py`、`jp_reading_filter.py`、`build_*_index.py` | 表依 D2 改名與改欄位（`line_id`、`game_id`、`term_key`）；有 join `scripts` 的地方改用 A 的模組；詞彙索引原本以 `source = 'サノバウィッチ'` 刪除與篩選，改成 `game_id` |
| F | 測試 | `test_deletion_toolkit.py`、`test_relation_sync.py` 等約 5 個 | 測試資料改成新表的結構 |
| G | 一次性的維護腳本 | `backfill_project.py`、`canonicalize_verb_lemma.py`、`migrate_child_cards.py` 等 | 已經跑過、不會再用的，標成「舊庫專用」，不改寫 |
| H | 遊戲設定移出 `.env` | `app/core/config.py`、`.env`、`.env.example`；讀這些設定的 6 個檔案：兩種卡片類型的 `generate_child_cards.py`、`mark_skipped_es_data.py`、刪卡工具 `deletion/profiles.py`、兩支 `cleanup_script.py` | 拿掉 8 個設定（`JP_VERB_PAIR_`、`JP_CORE_VERB_` 各一組 `SOURCE_GAME`、`GAME_NAME_JP`、`VOICE_DIR`、`AVATAR_DIR`），新增 `GAME_ASSETS_ROOT`。建卡管線加 `--game` 參數（預設 `SabbatOfTheWitch`），從 `game_profiles` 讀名稱與資料夾、從 `game_speakers` 讀頭像檔名；媒體清理改成檢查 `game_profiles` 裡每款遊戲的前綴。卡片類型的參數（前後文句數、卡數上限、過濾條件、母卡牌組）留在 `.env` |

**C 的細節：舊的知識圖譜關聯怎麼放進 `rel_links`**

| `card_relations`（SQLite） | `rel_links`（新庫） |
|---|---|
| `source_note_id`、`target_note_id` | `src_type='note'`、`src_id`；`dst_type='note'`、`dst_id` |
| `source_label`、`target_label` | `src_label`、`dst_label`（懸空節點照舊靠標籤） |
| `relation_type`（`has_context`、`has_cloze`、`synonym`、`conversation`） | `core.has_context`、`core.has_cloze`、`user.synonym`、`user.conversation` |
| `relation_types`（SQLite） | `rel_types`（`owner=user` 的就是使用者在網頁上建立的類型） |

- **資料不用從 SQLite 搬**：本機的 172 列全部是建卡時自動寫的 `has_context`、`has_cloze`，D8 已經從卡片紀錄回填；使用者也確認正式機沒有重要資料。
- 正式機切換前，仍先唯讀看一眼正式機 SQLite 有沒有 `has_*` 以外的關聯；有的話一併匯入，標 `created_by=migration`。

**行為上會變的地方**
- 建卡時抓的前後文：原本依舊 id 在章節內排序，改成依 `story_order`。還原的 6 句會出現在前後文裡，選項畫面不會。只影響之後新建的卡。
- 讀音判定、建卡的來源篩選：原本寫死 `source = 'サノバウィッチ'`，改成 `game_id`。
- 詞改成精確比對：`term_key` 是 `utf8mb4_bin`，舊的 `verb_lemma` 是 `utf8mb4_unicode_ci`（「が」「か」視為相同）。
- 建卡管線一次處理一款遊戲（`--game`）；遊戲名稱、語音與頭像資料夾從 `game_profiles` 讀。

**卡片類型與圖的共用**
- 每種卡片類型可以註冊自己的關係類型（例：`jp_verb_pair.transitive_pair`），表現方式各自不同。共通的關係（`core.*`）由系統定義，大家共用。
- 圖也可以跨遊戲共用：劇情流程圖的提供者以 `game_id` 為參數，任何有 `game_scenes`、`game_scene_links`、`game_lines` 資料的遊戲都能直接使用，不必為每款遊戲另寫。

## 5. 改動清單

### 遷移腳本（`backend/scripts/database/corpus_rebuild/`）

| 檔案 | 改動 |
|---|---|
| `schema.sql` | 新增：D2 的建表 SQL（不含檢視表） |
| `rebuild.py` | 新增：§D7 第 2 步的主程式（`--dry-run` 只輸出報表） |
| `align.py` | 新增：D4 的序列對齊 |
| `fixes.py` | 新增：D5 的 F1–F3、F7 |
| `verify.py` | 新增：D7 第 3 步的驗證；另外檢查 `rel_links` 的每個端點都存在（依 `rel_entity_types`） |
| `backfill_relations.py` | 新增：D8 的類型登記與回填 |
| `report/` | 輸出：對齊報表、修復清單、驗證結果 |

**遷移的輸入**（全部唯讀）：

| 來源 | 用在 |
|---|---|
| `yuzusoft` 的 `scripts`、`generated_sentences_log`、`dialogue_terms_index`、`jp_verb_reading_judgments` | `game_legacy_scripts`、卡片相關表、舊 id 與振假名 |
| `game\SW_point_table.json` | `game_script_files`、`game_scenes`、`game_lines`（日文、繁中、point、縮圖、H、BGM） |
| `game\SW_calib\bgm_titles\file_titles.json` | `game_bgm_tracks`（代號、檔案序號、日繁曲名、鑑賞編號） |
| `game\SW_calib\work\calib_state.json` | `game_scene_links` 的自然接續、`game_scenes.scene_order` |
| `game\SW_calib\work\calib_choices.jsonl`、`calib_choice_vars.jsonl` | `game_scene_links` 的選項與 `effects` |
| `game\SW_calib\work\calib_route_probe.jsonl`、`calib_results_vars.jsonl` | `game_scene_links` 的 `jump` 與 `condition_vars` |
| `game\SW_calib\work\calib_results_tw.jsonl` | `anchor_method`、`anchor_verified_at` |

執行方式：

```bash
python backend/scripts/database/corpus_rebuild/rebuild.py --anchors "C:/Users/forip/Desktop/WorkSpace/game/SW_point_table.json" --calib "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/work" --dry-run
```

```bash
python backend/scripts/database/corpus_rebuild/rebuild.py --anchors "C:/Users/forip/Desktop/WorkSpace/game/SW_point_table.json" --calib "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/work"
```

```bash
python backend/scripts/database/corpus_rebuild/verify.py --old yuzusoft --new fluencytides_corpus
```

### Backend

| 檔案 | 改動 |
|---|---|
| `backend/.env` | `MYSQL_DATABASE=fluencytides_corpus`（切換時，先備份） |
| `backend/.env.example` | 已經是 `fluencytides_corpus`，不用改；補一行註解說明舊名 `yuzusoft` |
| `backend/app/core/config.py` | D9 的 H：拿掉 8 個遊戲設定，新增 `GAME_ASSETS_ROOT` |
| `backend/.env`、`backend/.env.example` | 同上（本機 `.env` 先備份）。正式機不跑建卡管線，不需要 `GAME_ASSETS_ROOT`；舊的 8 個設定留著也不會出錯（設定類別是 `extra="ignore"`），切換時附上刪除它們的 sudo 指令 |
| `backend/scripts/common/database/init_db.py` | 改成建 D2 的 `card_gen_log`、`nlp_line_readings`，讓新環境可以從頭建表；舊的欄位補齊與 unique key 遷移邏輯移除 |
| 讀寫舊表的程式 | 依 D9 的 A–G 全部改讀寫新庫 |

### 其他

- `game\SW_calib\work\make_csv.py`：不用改。錨點檔案已在繁中輪結束後重產；和資料庫的對齊改在遷移腳本中做（取代流程圖計畫書原本的 D1.1 修正）。

### 測試

- `align.py`：用重複台詞、插入、刪除、錯字的小型案例測試序列對齊。
- `verify.py` 本身就是驗收工具；在乾跑模式對真實資料跑一次。
- 後端：既有測試全部用新資料庫跑過。

## 6. 實作順序

| 階段 | 內容 | 為何先做 |
|---|---|---|
| **M0** | ✅ 建資料庫與授權（§3）。遷移腳本對 `yuzusoft` 只做 `SELECT`，舊庫在切換後停在切換前的狀態，本身就是遷移前的快照，所以**遷移不另外備份**（2026-11-01 使用者確認）；SQLite 不動、不複製 | 沒有權限什麼都不能做 |
| **M1** | 建表、封存舊劇本、場景檔與場景、對齊並匯入 `game_lines`（日文＋繁中＋位置＋縮圖）、BGM、修復 F1–F3、卡片相關表、檢視表；乾跑與驗證 | 繁中輪已完成，所有資料都齊了，可以一次做完 |
| **M2** | （已完成）F4 與 F9 都在 2026-11-01 處理完；只剩 BGM51 曲名不明 | — |
| **M2b** | D9 的程式改寫：先做 A 的共用模組，再依 A → B → C → D → E 逐組改；每組改完用乾跑（不寫 Anki）比對新舊結果 | 程式改寫和資料遷移可以並行，但切換前必須全部完成 |
| **M3** | 凍結建卡 → 本機與正式機同時切換 `.env` → 重建 ES → 解凍、觀察一週 | 確認建卡管線、刪卡、知識圖譜、ES 都正常 |
| **M4** | 劇情流程圖（`story_flowchart_FEAT`）開始實作 | 建在新表上 |

## 7. 風險與未知

- **R1 對齊錯誤**：重複台詞如果對錯 id，卡片會掛在錯的劇情位置上。
  - 應對：用劇情順序做序列比對；報表抽查重複台詞的前後文；驗證「每筆卡片紀錄的台詞文字和 Anki 卡片上的例句一致」（抽查 50 筆）。
- **R2 （已不適用）舊程式依賴 NFD 檔名**：一步到位後不建檢視表，舊程式全部改寫；查過也沒有程式讀 `filename`。
  - 應對：檢視表從 `game_legacy_scripts` 輸出原值，舊程式看到的值完全不變（而且查過沒有程式讀 `filename`）；新表一律存 NFC。
- **R3 （已不適用）檢視表效能**：舊程式對 `scripts` 的查詢多半是 `WHERE id IN (…)` 或依 `source`、`chapter` 篩選，經過檢視表後是五表 join（`game_profiles`、`game_scenes`、`game_script_files` 都很小，`game_legacy_scripts` 依主鍵）。
  - 應對：M1 用建卡管線實際跑一次比較耗時；依 `chapter` 篩選的查詢若變慢，在 `game_lines` 加 `(game_id, chapter_label)` 索引。
- **R4 F1 還原的 id 不在原本的位置**：如果當初刪除後又插入過其他資料，原 id 的位置可能已經不對。
  - 應對：D5 F1 先檢查空缺位置；不相符就改用新 id 並記錄。
- **R5 切換期間有寫入**：建卡會寫 `card_gen_log`。
  - 應對：D7 的凍結步驟；切換前再比一次列數。
- **R6 正式機設定**：正式機也讀這個資料庫，`.env` 要同步修改；ES 同步若在正式機上排程執行，也要確認它讀的是新資料庫。
- **R7 還原的 6 句進入建卡**：D6 已決定進入；ES 同步後才會被搜尋到，切換步驟要記得跑。

## 8. 驗收標準

- [ ] M0：`jacky917` 可以在 `fluencytides_corpus` 建表，也能讀 `yuzusoft`。
- [x] M1：對齊報表：53,188 句一一對應，增刪清單和 §1 一致（還原 6、選項 46、錯字 1、錨點缺 1），重複台詞抽查 20 組全部正確。（2026-11-01：低信心 63 句，都沒有卡片紀錄）
- [x] M1：`verify.py` 通過：`game_legacy_scripts` 與舊表完全相同；舊欄位（`chapter`、`role_name`、`dialogue`、`audio_file`）逐列一致；3 張遷移的表列數、id 與欄位轉換都正確。
- [ ] M1：後端測試在新資料庫全部通過；建卡管線乾跑結果和舊資料庫相同。
- [x] M1：`game_lines` 除了 `kind=bgv`（1043）以外，`point` 與 `text_zh_hant` 全部非 NULL。
- [x] M2：`game_line_revisions` 記錄了 F1–F4 的每一筆修復。（54 筆：還原 6、選項 46、錯字 1、背景語音 1）
- [ ] M3：切換後建一張核心動詞卡、一張自他動詞卡成功，ES 搜尋正常；正式機也正常。
- [ ] M3：建卡管線用 `--game SabbatOfTheWitch` 建卡，語音與頭像由 `GAME_ASSETS_ROOT` ＋ `game_profiles` 的資料夾讀到；本機 `.env` 已沒有 `JP_*_SOURCE_GAME`、`GAME_NAME_JP`、`VOICE_DIR`、`AVATAR_DIR` 這 8 個設定。
