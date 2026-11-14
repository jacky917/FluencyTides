# FEAT — 《千恋＊万花》加入新語料庫 `fluencytides_corpus` 與劇情流程圖

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-08 |
| **性質** | 新增機能設計 + 實作工作項（第二款遊戲） |
| **狀態** | 🚀 已合併進 main（PR [#40](https://github.com/jacky917/FluencyTides/pull/40)，2026-11-12，commit `09f6f92`），之後由 CI 部署 main。合併前先用 SSH 部署在正式機執行（2026-11-08，`deploy_remote.sh all --allow-dirty`，版本 `3858f03`＝當時正式機版本 `db22e35`＋本分支未 commit 的改動，見 §9.13）；素材放正式機的本機資料夾。ES 同步 2026-11-14 完成（`sync_mysql_to_es.py --game SenrenBanka`，55,726 筆，與新庫逐筆一致；魔女的夜宴 53,196 筆不變） |
| **範圍** | 語料庫：`fluencytides_corpus` 的 `game_*` 表加入 `SenrenBanka`（不動魔女的夜宴的列）。匯入腳本：`backend/scripts/database/corpus_rebuild/` 新增遊戲匯入。流程圖：`backend/scripts/story/extractors/senren_banka.py`、`backend/app/services/story/games/`、`backend/app/infrastructure/krkr/`（新存檔格式）、`frontend/public/story-themes/`。素材：`Y:\material\yuzusoft\SenrenBanka\` |
| **不動** | 魔女的夜宴在新庫的所有列；舊庫 `yuzusoft`（只讀；P0 的刪除除外）；遊戲本體與加密封包；`SB_calib`（只讀它的產出） |
| **基準分支** | `main`（`4b99f04`）。**不要在 `feat/cards-create-panel` 上做**：那個分支比 main 落後 80 個 commit，沒有語料庫與流程圖的程式 |
| **關聯文件** | main 的 `docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md`（語料庫，🔨 待切換）、`docs/wip/story_flowchart_FEAT_2026-10-31.md`（流程圖）；`docs/archive/senren_banka_materials_FEAT_2026-11-08.md`（素材）；`docs/wip/senren_banka_scripts_import_FEAT_2026-11-08.md`（誤匯入舊表，本計畫取代它） |

## 1. 現況（2026-11-08 對 main 與資料庫查證）

### 語料庫

- 新庫 `fluencytides_corpus` 已建好（2026-11-01），只有魔女的夜宴一款：
  - `game_profiles` 1 列（`SabbatOfTheWitch`）
  - `game_lines` 53,242 列（id 1～53242；53197～53242 是還原句與選項畫面）
  - 卡片紀錄 `card_gen_log` 6,265 列，和舊庫相同，表示切換後還沒有新的建卡
  - 流程圖 `story_builds`：建置 2 版，第 2 版上線中
- **2026-11-14 更新**：本機與正式機都已切換，資料庫只由 `.env` 的 `MYSQL_DATABASE` 決定；`STORY_CORPUS_DATABASE` 與腳本的 `--database` 參數已移除，下面提到它們的段落是當時的紀錄。
- **切換（M3）還沒做**：本機 `backend/.env` 的 `MYSQL_DATABASE` 仍是 `yuzusoft`。main 的程式都改讀新庫，切換前用 `--database fluencytides_corpus` 或 `STORY_CORPUS_DATABASE` 指定。
  - **更正（2026-11-08 部署時查證）**：正式機 2026-11-01 就已經切換，正式機 `.env` 是 `MYSQL_DATABASE=fluencytides_corpus`（備份 `.env.bak-2026-11-01-cutover`）。沒切換的只有本機 `.env`。
  - 本機和正式機連的是**同一台 MySQL**（`192.168.50.172:3306`）。所以本計畫在「本機」匯入的語料與流程圖版本 3，一寫進去就是正式機的資料。
- **重建腳本只認得一款遊戲**：`corpus_rebuild/rebuild.py` 每次都**刪掉新庫所有表**再從舊庫重建，而且讀的是舊表 `scripts` 的**全部列，不分 source**（`load_legacy`：`SELECT … FROM scripts ORDER BY id`）。切換後它拒絕執行。

### 建卡（main）

- 用 `--game <game_id>` 選遊戲，設定從 `game_profiles` 讀（語音、頭像、日文標題），不再用 `.env` 的 `JP_*` 設定。
- 頭像取 `game_speakers.avatar_file`，沒有才用 `<說話者>.jpg`，所以透明 png 可以直接用。
- 素材路徑是 `GAME_ASSETS_ROOT`（`.env`）接上 `game_profiles.voice_dir`／`avatar_dir`。
- ES 同步 `sync_mysql_to_es.py` 逐款遊戲讀 `game_lines`，文件 `_id`＝`game_lines.id`，`source`＝`legacy_source`，沒有時用日文標題。

### 流程圖（main）

- 通用匯入 `scripts/story/import_story_build.py`，加上每款遊戲一個擷取器（`extractors/sabbat_of_the_witch.py`）。擷取器從語料庫的場景與場景連接組出劇情圖，再寫成建置包。
- 「下載存檔」的寫入器 `infrastructure/krkr/packinone.py` 只支援 PackinOne：JPEG 縮圖裡嵌入封包。

### 千戀＊萬花和魔女的夜宴的差異

| 項目 | 魔女的夜宴 | 千戀＊萬花 |
|---|---|---|
| 舊庫 | 有（`scripts`，要對齊舊 id） | 沒有：全部是遊戲實測加上解包劇本 |
| 錨點表 | `SW_point_table.json`（list） | `SB_point_table.json`（`columns`／`rows`），欄位不同（見 D3） |
| 選項 | 錨點 `text_jp` 是 `選択肢：…`；分歧變數 `f.*_flag` | `kind='choice'`、`text_jp` 為空、`choice_options`；分歧用 `SetBranchFlags("s001*…_sel",1)` |
| 場景連接紀錄 | `calib_state.json`、`calib_choices.jsonl`、`calib_choice_vars.jsonl`、`calib_results_vars.jsonl` | `adv_state.json`、`adv_choices.jsonl`、`adv_links_manual.jsonl`、`calib_route_*.jsonl`；`SB_calib\work\flowchart.py` 已經整理成實測／推定的連線 |
| 流程圖標題 | 擷取器內的標題表 | 遊戲內官方流程圖：334 個節點（`SB_flowchart_nodes.json`），每句有 `fc_node` |
| 存檔 | PackinOne：JPEG 加封包 | 吉里吉里 saveStruct：BMP 加 zlib 的 TJS 文字（`SB_reverse_notes.md` §1，工具 `SB_calib\work\sbsave.py`） |
| 素材位置 | 本機 `material\voice\yuzusoft\SabbatOfTheWitch` 等 | NAS `Y:\material\yuzusoft\SenrenBanka\` |

## 2. 目標與非目標

- G1：千戀＊萬花的台詞、說話者、BGM、場景與場景連接進入 `fluencytides_corpus`，可以用 `--game SenrenBanka` 建卡。
- G2：ES 同步後可以依動詞搜尋千戀＊萬花的台詞。
- G3：劇情流程圖頁可以切換到千戀＊萬花，節點標題用遊戲內的官方標題。
- G4：匯入可以重跑，而且不會動到魔女的夜宴的任何列。
- 非目標：
  - 改動魔女的夜宴的資料；
  - 新庫的切換（M3）本身，那是語料庫計畫的步驟；
  - 千戀＊萬花的英文、簡中文字（語料庫目前只存日文和繁中）。

## 3. 前置條件

- **P0 刪掉舊表裡誤匯入的千戀＊萬花列**（使用者 11/8 決定先留著，但**重跑 `rebuild.py` 之前一定要刪**）：
  - `rebuild.py` 讀舊表 `scripts` 不分 source。這 55,726 列會被當成魔女的夜宴的舊台詞拿去對齊，複製進 `game_legacy_scripts`，`verify.py` 的核對也會失敗。
  - 它們的 id 是 53197～108922，和新庫魔女的夜宴還原句的 id（53197～53242）重疊，容易混淆。

```sql
DELETE FROM scripts WHERE source = '千恋＊万花';
```

- **P1 在 main 開新分支（worktree）**，不在目前這個資料夾的分支上做。

## 4. 設計決策

### D1 匯入方式：新增「加一款遊戲」的匯入，不改 `rebuild.py`

- **選了**：新增 `corpus_rebuild/import_senren_banka.py`（或是通用的 `import_game.py` 加上各遊戲的讀取器）。
  - 只寫 `game_id='SenrenBanka'` 的列。
  - 可以重跑：先依外鍵順序刪掉這款遊戲的內容表列，再寫入。
  - 如果這款遊戲的台詞已經被 `card_gen_log`、`rel_links`、`nlp_*` 引用，就拒絕重跑（紀錄表不能被清掉；比照語料庫計畫 D7 的內容表與紀錄表規則）。
  - 切換前、切換後都能跑，用 `--database` 指定資料庫。
- **放棄**：把千戀＊萬花寫進 `rebuild.py` 的 `build()`。它是「刪光重建、讀舊庫、對齊舊 id」的流程，千戀＊萬花三樣都不需要；而且切換後 `rebuild.py` 就不能再跑了。
- **`rebuild.py` 的配套**：切換前如果又重跑 `rebuild.py`（會刪光所有表），之後要再跑一次千戀＊萬花的匯入。`rebuild.py` 結束時印一行提醒。

### D2 `game_profiles`

| 欄位 | 值 |
|---|---|
| `game_id` | `SenrenBanka`（也是 Anki 媒體前綴 `SenrenBanka_`，和素材資料夾同名） |
| `title_ja_jp`／`title_zh_hant` | `千恋＊万花`／`千戀＊萬花` |
| `engine` | 新值 `kirikiri_savestruct`（存檔格式和 PackinOne 不同，見 D7） |
| `languages` | `["ja-JP","zh-Hant"]` |
| `voice_dir`／`avatar_dir` | `yuzusoft/SenrenBanka/voice`／`yuzusoft/SenrenBanka/avatar`（接在 `GAME_ASSETS_ROOT`＝`Y:\material` 之後，Q1） |
| `legacy_source` | NULL：沒有舊庫，ES 的 `source` 會用 `title_ja_jp`＝`千恋＊万花` |

### D3 台詞 `game_lines`：每列錨點一列

- 錨點表 55,770 列，全部進 `game_lines`：
  - `line` 55,712 列
  - `auto` 14 列（沒有存檔點，`point` 為 NULL）
  - `choice` 44 列（選項畫面）
- **id**：自動遞增，接在現有最大值之後（目前會從 53243 開始）。ES 文件 `_id`＝`game_lines.id`，不會和魔女的夜宴撞號。
- 欄位對應：

| `game_lines` | 千戀＊萬花的來源 |
|---|---|
| `scene_id`、`seq` | `scene`（`file*label`）；同場景依 `story_order` 編 `seq` |
| `story_order` | 錨點表 `story_order`（0～55,769，遊戲內連續） |
| `kind` | `line`／`auto` → `line`；`choice` → `choice` |
| `point` | `point`（`auto` 為 NULL） |
| `chapter_label` | 錨點表 `chapter`（例 `chapter　1-1`）；空的沿用前一句（比照魔女的夜宴） |
| `speaker_id` | `speaker_jp` → `game_speakers` |
| `text_ja_jp` | `text_jp`。選項畫面沒有日文，組成 `選択肢：正直に言う / 誤魔化す`，比照魔女的夜宴 |
| `text_zh_hant` | `text_tw` |
| `ruby_ja_jp` | 有注音的句子取素材 `script_lines.jsonl` 的 `ruby_ja_jp`（用 file、label、point 對上；137 句） |
| `voice_file` | `voice` 去掉 `.ogg`（例 `uts001_004`）；多人同時說話以 `|` 分隔的，取第一個，全部存進 `attrs.voices` |
| `is_h`、`h_label`、`thumb` | 同名欄位 |
| `bgm_detect`、`bgm_may_change` | NULL、0：千戀＊萬花的 BGM 是存檔裡讀到的，不是偵測的 |
| `attrs` | `segment`、`cg_unlock`、`replay_unlock`、`bgm_unlock`、`flags_new`、`flags_after_line`、`fc_node`、`fc_flags`（有值才放） |
| `origin` | `game_only`（全部來自遊戲，沒有舊庫） |
| `in_corpus` | 選項畫面為 0，其他為 1 |
| `anchor_method` | 依 `hist_matched`（校準 README 的定義）：1 → `backlog`（下一步的回想紀錄配對）；2 → `backlog_other`（同位置其他紀錄補）；4 → `fill`（日文由遊戲讀檔取得，結局最後一句的說話者與語音取自解包劇本，15 句）；`auto`／`choice`（0）為 NULL |

- **BGM**：`bgm_codes` → `game_line_bgm`（`ord` 依清單順序）。

### D4 說話者、檔案、場景、BGM 曲目

- `game_speakers`：取自素材 `text\speakers.json`（116 人）。
  - `name_zh_hant` 是遊戲畫面上的繁中名，`name_zh_hant_src='game'`；`voice_prefix` 照抄。
  - `avatar_file`：有頭像的 12 人填 `<名>.png`。
  - `sort_order` 依台詞數。
- `game_script_files`：92 個檔案。
  - `file_no` 是檔名開頭的編號（`001`、`508a`）。
  - `title_ja_jp` 是檔名中段（例 `アーサー王`，比照魔女的夜宴用檔名）。
  - `file_order` 依劇情順序第一次出現的位置。
  - 官方流程圖標題放在流程圖擷取器（D6），不放這裡。
- `game_scenes`：每個 `file*label` 一列，檔案內依第一次出現的順序排。
- `game_bgm_tracks`：取自 `SB_bgm_titles.json`。
  - `code`、`archive_index`（`bgm.xp3` 序號）、`gallery_no`（音樂鑑賞序號）、日文與繁中曲名。
  - 55 首：劇情 47、標題 1、歌曲 7。

### D5 場景連接 `game_scene_links`

- 來源是 `SB_calib\work\flowchart.py` 已經整理好的連線（流程圖 md／html 用的那一份）。新增一個輸出：`calib\SB_scene_links.json`。
  - 每筆一條連線：`from_scene`、`to_scene`、`to_point`、`kind`（`next`／`choice`／`jump`／`unlock`）、`option_index`、日文與繁中選項文字、`raw_expression`、`via`、`evidence`（`observed`／`probed`／`inferred`）、`note`。
  - 匯入腳本只讀這個檔，不在 FluencyTides 裡重寫一次 SB 的校準邏輯。
- **選項的效果**：
  - 千戀＊萬花的運算式是 `SetBranchFlags("s001*com_part_1_sel",1)`。
  - 魔女的夜宴的 `parse_effects` 只認 `f.x = n`，所以加一個千戀＊萬花的解析，轉成 `[{"var": "s001*com_part_1_sel", "op": "set", "value": 1}]`。
  - `condition_vars` 用同樣的旗標名。
- **路線條件**：
  - 結局與路線旗標的實測結果在 `calib_endflags.jsonl`、`calib_route_*.jsonl`，`flowchart.py` 已經用它們判斷路線分歧。
  - 有條件的連接寫進 `condition_vars`，`evidence='probed'`。

### D6 流程圖擷取器 `extractors/senren_banka.py`

- **分頁（tab）**：取自 `SB_flowchart_nodes.json` 的 `tab`，例如 `common`、`yos`、`mak` 等，繁中名用 `tab_tw`。
- **標題**：
  - 節點標題用官方標題（`title_jp`／`title_tw`），依 `fc_node`（`s<檔案編號>*<標籤>`）對到場景。
  - 合併成一個單元時，取單元第一個場景的官方標題。
- **章節**：網頁流程圖照魔女的夜宴的方式，顯示官方流程圖節點的 `chapter`（`CHAPTER 1`～、`EPILOGUE`）；語料庫的 `chapter_label` 仍是細分章節（Q3）。
- **補充連接**：`flowchart.py` 推定的連線已經在 D5 進了資料庫，這裡不另外加；只有通關後入口這類還沒有實測的，才用 `ExtraLink`。
- **主題**：新增 `frontend/public/story-themes/<主題名>/`（例 `senren-hoori`），比照 `sabbat-night` 的檔案組成（`theme.json`、`theme.css`、背景與裝飾 svg），風格對應千戀＊萬花：和風、神社與穗織的町、櫻與紅白（Q4）。配色與裝飾先出樣張給使用者確認。
- **後端適配器**：`services/story/games/senren_banka.py`。路線預設的旗標是官方流程圖的 `s…_sel` 旗標，值是整數。
- **建置包素材**：
  - 縮圖 `Y:\…\thumbs`
  - 語音 `Y:\…\voice`
  - BGM `Y:\…\bgm`（`.ogg`；擷取器加 `--enable-bgm-audio` 打開播放，Q5）
  - 存檔樣板 `Y:\…\templates\quick.bmp`

### D7 「下載存檔」：新的存檔寫入器

- 新增 `infrastructure/krkr/savestruct.py`，移植 `SB_calib\work\sbsave.py`。
  - 格式：BMP 縮圖，後面接 `FE FE 02 FF FE`、壓縮長度 u64、原始長度 u64，再接 zlib 壓縮的 UTF-16LE TJS 文字。
  - 寫回時要完全照遊戲的寫法：每個陣列、字典前加 `(const)`，空容器也換行，小數保留原寫法與 `/* 註解 */`。否則遊戲讀不了。
- 依 `game_profiles.engine` 選寫入器：`kirikiri_packinone` 用原本的，`kirikiri_savestruct` 用新的。
- 要比照魔女的夜宴的 P0，用遊戲實際讀檔驗證。這一步要跑遊戲，驗證通過才上線（Q6：全部做好一起上線）。

### D8 建卡與 ES

- 匯入後跑一次 ES 同步。main 的 `sync_mysql_to_es.py` 會逐款遊戲寫入，`_id` 是台詞 id，可以重跑。要確認它是不是還會先重建整個索引；如果會，加上只同步單一遊戲的選項。
- 建卡用 `--game SenrenBanka`。
  - 語音 `<voice_dir>/<voice_file>.mp3`；頭像 `<avatar_dir>/<avatar_file>`，也就是透明 png。
  - Anki 媒體前綴是 `SenrenBanka_`。

## 5. 改動清單

| 項目 | 位置 | 內容 |
|---|---|---|
| 舊表清理 | MySQL `yuzusoft.scripts` | P0 |
| 場景連接輸出 | `SB_calib\work\flowchart.py`（FluencyTides 之外） | 多寫 `SB_scene_links.json`，並由 `build_materials.py --only calib` 複製到素材 `calib\` |
| 遊戲匯入 | `backend/scripts/database/corpus_rebuild/import_senren_banka.py`（新） | D1～D5；`--dry-run`、`--database` |
| 重建提醒 | `corpus_rebuild/rebuild.py` | 結束時提醒要重跑其他遊戲的匯入 |
| 存檔寫入器 | `backend/app/infrastructure/krkr/savestruct.py`（新）、存檔服務依 `engine` 分派 | D7 |
| 流程圖擷取器 | `backend/scripts/story/extractors/senren_banka.py`（新） | D6 |
| 遊戲適配器 | `backend/app/services/story/games/senren_banka.py`（新） | 路線預設的驗證 |
| 主題 | `frontend/public/story-themes/<新主題>/`（新） | Q4：千戀＊萬花風格，先出樣張 |
| 設定 | 本機 `backend/.env`、`.env.example` | `GAME_ASSETS_ROOT=Y:\material`（本機 `.env` 先備份；`.env.example` 同步說明）。正式機不跑建卡管線，不需要 |
| 魔女的夜宴的素材路徑 | `game_profiles`（新庫）、`corpus_rebuild/content.py` 的 `GAME_PROFILE` | `voice_dir`／`avatar_dir` 改成 `yuzusoft/SabbatOfTheWitch/voice`／`avatar`（兩處一起改，否則重跑 `rebuild.py` 會改回舊值） |
| 測試 | `backend/tests/` | 匯入的對應與重跑規則、`SetBranchFlags` 解析、savestruct 讀寫來回一致 |

## 6. 實作順序（每一步都可以單獨驗收）

1. P0、P1。
2. `SB_scene_links.json`（D5 的來源檔）。
3. 匯入語料庫（D1～D5），先 `--dry-run`，再寫入新庫並驗證（§8）。
4. ES 同步；用 `--game SenrenBanka --dry-run` 試建卡，再實際建幾張確認語音、頭像。
5. 存檔寫入器，加上遊戲內讀檔驗證。
6. 主題：先出樣張給使用者確認。
7. 流程圖擷取器與匯入（BGM 播放打開），全部一起驗收後上線。

## 7. 決定（使用者 2026-11-08）

| # | 問題 | 決定 |
|---|---|---|
| Q1 | 素材路徑 | `GAME_ASSETS_ROOT`＝`Y:\material`。兩款遊戲的素材都已在 NAS `Y:\material\yuzusoft\<game_id>\`（`voice`、`avatar`、`thumbs`、`templates`、`text`、`calib`）。`game_profiles` 改成：魔女的夜宴 `yuzusoft/SabbatOfTheWitch/voice`、`yuzusoft/SabbatOfTheWitch/avatar`（原本是 `voice/yuzusoft/SabbatOfTheWitch`）；千戀＊萬花 `yuzusoft/SenrenBanka/voice`、`yuzusoft/SenrenBanka/avatar` |
| Q2 | 切換前或切換後加入 | 「同上」，理解為照建議：切換前就加，匯入腳本用 `--database fluencytides_corpus`，切換驗證時兩款一起驗。切換前重跑 `rebuild.py` 會清掉千戀＊萬花，要再匯一次（D1） |
| Q3 | 章節 | 語料庫 `chapter_label` 用錨點表的細分章節（`chapter　1-1`），建卡依它限制；網頁流程圖照魔女的夜宴的方式顯示官方的 `CHAPTER 1`（D6） |
| Q4 | 流程圖主題 | 另做一份符合千戀＊萬花風格的主題（D6） |
| Q5 | 流程圖 BGM | 打開播放 |
| Q6 | 上線方式 | 全部做好一起上線：存檔寫入器與遊戲內讀檔驗證完成前不上線，也不做「下載存檔關閉」的過渡版本 |

- **注意（Q1）**：NAS 上魔女的夜宴的 `MANIFEST.json`（2026-11-08 18:53）記錄語音需要 19,127 個、已有 15,544 個、缺 3,583 個。（2026-11-08 晚上再確認：已補到只缺 2 個，見 §9.12。）

- **風險**：千戀＊萬花的場景連接有一部分是推定的（流程圖 md 有標示）。匯入時 `evidence='inferred'` 照實標註，流程圖要能區分顯示（確認 main 的前端有沒有依 `evidence` 改樣式；沒有的話列為後續）。
- **風險**：舊表那 55,726 列沒刪的話，任何人重跑 `rebuild.py` 都會把它們當成魔女的夜宴的資料（見 P0）。

## 8. 驗收

- [ ] `yuzusoft.scripts` 只剩魔女的夜宴的 53,190 列。
- [ ] 新庫魔女的夜宴的列數與內容沒有變動：匯入前後各表依 `game_id` 做 CHECKSUM 比對。
- [ ] `game_lines` 中 `SenrenBanka` 有 55,770 列，`story_order` 0～55,769 連續，`(scene, point)` 不重複。
- [ ] 每句台詞的日文、繁中、說話者、語音和素材 `script_lines.jsonl` 一致（沿用 `check_material.py` 的比對）。
- [ ] 每個有語音的列，`Y:\…\voice\<voice_file>.mp3` 都存在；有頭像的說話者，`avatar_file` 都存在。
- [ ] 匯入重跑結果相同；已有卡片紀錄引用時拒絕重跑。
- [x] ES 中 `千恋＊万花` 的文件數＝`in_corpus=1` 的台詞數，魔女的夜宴的文件數不變。（2026-11-14：55,726／53,196，逐筆比對沒有缺、多或內容不同）
- [ ] `--game SenrenBanka` 建出的卡片語音、頭像、前後文正確。
- [ ] 流程圖：分頁、官方標題、選項分歧和遊戲內流程圖一致；推定的連線有標示。
- [ ] 下載的存檔在遊戲內讀得到，停在指定的句子。

## 9. 實作紀錄（2026-11-08）

### 9.1 場景連接來源

- `SB_calib\work\flowchart.py` 新增 `write_links()`，和流程圖 md／html 同一份連線，輸出 `SB_scene_links.json`：381 條連線、3 個標題選單入口（`menu_entries`）。
  - 自然接續 285 條（含 13 個結局：`to_scene` 為 null）
  - OP 動畫 1 條（`via=movie`）
  - 選項 87 條（附 `SetBranchFlags` 運算式）
  - 路線判定 8 條（`jump`，`evidence=probed`）
- 改之前已備份 `flowchart.py` 與 `SB_flowchart.md`；重跑後 `SB_flowchart.md` 一字不變。
- `build_materials.py` 的 `CALIB_FILES` 加入 `SB_scene_links.json`，所以素材 `calib\` 也有這份。

### 9.2 語料庫匯入（D1～D5）

- 腳本：`backend/scripts/database/corpus_rebuild/import_senren_banka.py`。

```bash
python -m scripts.database.corpus_rebuild.import_senren_banka --dry-run
```

```bash
python -m scripts.database.corpus_rebuild.import_senren_banka
```

- 結果：
  - 寫入 `fluencytides_corpus`，`game_lines` id 53243～109012。
  - 台詞 55,770 列（`line` 55,726、`choice` 44）。
  - 檔案 92、場景 327、說話者 116、BGM 54 首、台詞 BGM 58,008 筆、連接 381 條（next 286、choice 87、jump 8）、注音 137 句。
- 驗證：
  - 魔女的夜宴各表在匯入前後做 CRC 指紋比對（列數與內容），完全相同。
  - 千戀＊萬花：`story_order` 連號；每句台詞的（檔名、標籤、日文、繁中、說話者、語音）和素材 `script_lines.jsonl` 完全一致。
  - 語音 mp3 全部存在；頭像 12 個全部存在；選項連接都有 `effects`。
  - 重跑一次，指紋相同。

### 9.3 素材路徑（Q1）

- `game_profiles`：魔女的夜宴改為 `yuzusoft/SabbatOfTheWitch/voice`／`avatar`。`corpus_rebuild/content.py` 的 `GAME_PROFILE` 同步改。
- `config.py`、`corpus_repository.py` 的說明、`.env.example` 同步改。
- 本機 `backend/.env` 加 `GAME_ASSETS_ROOT=Y:\material`；改之前已備份成 `backend/.env.bak_20261108_game_assets_root`。
- 魔女的夜宴的語音在 NAS 上：需要 23,493 個、有 19,127 個、缺 4,366 個（例 `AV405_001`、`aka206_011`）。本機舊資料夾 `material\voice\yuzusoft\SabbatOfTheWitch` 已經不存在，所以改路徑沒有讓情況變差，但缺的語音要另外補（不在本計畫範圍）。
- 建卡實測（`--game SenrenBanka` 的前後文組裝，`MYSQL_DATABASE=fluencytides_corpus`）：
  - 語音 `[sound:SenrenBanka_yos101_010.mp3]`、頭像 `SenrenBanka_芳乃.png` 都對到 `Y:\material\yuzusoft\SenrenBanka\…`。
  - 前後文依劇情順序。

### 9.4 ES 同步（D8）

- `sync_mysql_to_es.py` 加 `--game <game_id>`：不重建索引，只刪掉並重寫這款遊戲（`source`＝`es_source`）的文件。
- **還沒有執行**。當初以為正式機仍讀舊庫 `yuzusoft`，怕新舊庫的 id 對不上（新庫千戀＊萬花是 53243～，舊庫誤匯入的列是 53197～108922），所以沒跑。實際上正式機已經切換到新庫（見 §1 的更正），現在跑不會有這個問題，指令：

```bash
python scripts/database/Elasticsearch/sync_mysql_to_es.py --game SenrenBanka
```

### 9.5 存檔寫入器（D7）

- `backend/app/infrastructure/krkr/savestruct.py`：移植 `sbsave.py`，引擎 `kirikiri_savestruct`，在 `services/story/saves.py` 註冊。
- 做法同校準時的 `advance_run.write_save`：
  - 改 `scenePlayer` 的 scene／point，清空回想紀錄。
  - `user` 的劇情旗標換成路徑變數；選項畫面狀態保留。
  - `histories[].flags` 一起換。
- 對真實樣板「解析 → 序列化」和遊戲原文逐字相同。
- **遊戲內驗證**：`SB_calib\work\verify_story_saves.py`，多開 SB_1，混音器靜音，不碰使用者自己的遊戲。
  - 7 個不同路線的位置，包含共通、芳乃、茉子、叢雨、蕾娜、小春，從標題「前回のつづきから」與遊戲中 F12 兩種讀法都試過。
  - 全部停在指定的句子，`user` 變數讀檔後原樣存在。
  - 紀錄在 `story_save_verify.jsonl`。
- **限制**：後端沒有 Pillow，JPEG 縮圖不能轉成 BMP，所以下載的存檔沿用樣板的縮圖。這只影響遊戲存檔清單的預覽圖；之後要換，需要加 Pillow 依賴或預先轉好 BMP 縮圖。

### 9.6 主題（Q4）

- `frontend/public/story-themes/senren-hoori/`（穗織之櫻）：和紙底、墨色字、朱紅選取路徑與分頁、櫻色分歧、櫻花章節圖示、淡青海波與鳥居剪影背景。
- 在 `/story/_playground` 用手工千戀＊萬花片段預覽過；使用者照樣張定案（2026-11-08）。

### 9.7 流程圖擷取器與匯入（D6）

- 擷取器：`backend/scripts/story/extractors/senren_banka.py`。
  - 分頁依檔號百位數。
  - 場景節點標題用官方流程圖標題：建兩次圖，第二次用成員第一句的 `fc_node` 覆寫。
  - 章節由 `chapter_label` 轉成 `CHAPTER X`／Epilogue／After Story。
  - after story 6 條 `unlock`：3 條標題選單入口實測，其餘推定。
  - 路線預設取 route_grid 實測；レナ依計數規則推定。
  - BGM 播放打開。
- 存檔欄位：快速存檔 `data_quick_01.bmp`、前回のつづきから `data_continue.bmp`。
- 遊戲適配器：`backend/app/services/story/games/senren_banka.py`，路線預設只能用 `s…_sel` 整數旗標。
- 產生結果：255 個節點、297 條邊、55,770 句，沒有警告。

```bash
python backend/scripts/story/extractors/senren_banka.py --out build/senren_banka --database fluencytides_corpus --template "Y:/material/yuzusoft/SenrenBanka/templates/quick.bmp" --thumbs "Y:/material/yuzusoft/SenrenBanka/thumbs" --voices "Y:/material/yuzusoft/SenrenBanka/voice" --bgm "Y:/material/yuzusoft/SenrenBanka/bgm" --enable-bgm-audio
```

### 9.8 測試

- 新增 `tests/test_story_savestruct.py`、`tests/test_corpus_import_senren_banka.py`，15 個全過。
- 全部 `tests`：1,630 過、2 個失敗。兩個失敗都來自本機 `.env` 的 LLM 設定（`test_llm_endpoints`、`test_models_menu`）：拿掉 `.env` 後全過，和本次改動無關。

### 9.9 流程圖匯入（2026-11-08）

- 新庫 `story_builds` 版本 3（`senren_banka@1`），已設為上線版本：255 個節點、55,770 句。
- 素材寫到本機 `build/story-assets`，全部到齊、沒有缺漏：縮圖 20,527、語音 24,861、BGM 54、樣板 1。
- 上線時改上傳 MinIO：照 §6 用 `import_story_build.py`（預設 `--assets minio`），或用 `--assets-only` 替上線版本補上傳。

### 9.10 多面向審查與修正

審查分三個面向：一般程式碼、錯誤處理、測試。一般程式碼沒有高信心問題；錯誤處理與測試的建議全部照做：

- **存檔解析與寫入**（`savestruct.py`）：
  - 所有格式錯誤一律拋 `SaveStructError` 並附脈絡，包括：截斷的文字、缺逗號、根值後多餘內容、非字串的鍵、BMP 檔頭大小不合理、BMP 後沒有封包、封包截斷、UTF-16 錯誤、NaN／無限大、超出 64 位元的整數。
  - 樣板缺 `core.scenePlayer` 或 `core.historyData` 時拒絕，不默默補上。
  - 自我比對失敗時指出第一個不同的路徑。
  - 控制字元一律寫成 4 位數 `\x0001`，來回一致。
  - 縮圖只接受單獨的 BMP，其他格式記 WARNING 後沿用樣板縮圖。
- **存檔服務**（`service.py`）：
  - 寫入器的格式錯誤轉成 `StorySaveTemplateError`（`STORY_SAVE_TEMPLATE`，500），log 附遊戲、台詞與樣板物件鍵。
  - 寫入器宣告 `thumb_format`，千戀＊萬花（`bmp`）不去讀用不上的 JPEG 縮圖。
- **ES `--game`**：
  - 索引不存在時拒絕，提示先全量重建。
  - 順序改成先覆寫、再刪掉這款遊戲已不在語料庫的舊文件，過程中搜尋沒有空窗。
  - 刪除失敗會報錯；失敗訊息附重跑指令；log 依模式顯示。
- **語料庫匯入**：
  - BGM 代號對不到、場景連接被略過（依原因計數）、`hist_matched` 不認得，都會列進報表與 `check()`。
  - 錨點表的場景名稱不合法時，附列號中止。
  - 素材檔不在或格式錯時，附檔名與行號。
  - 連線強制 `STRICT_TRANS_TABLES`，超長的值不會被默默截斷。
- **擷取器**：
  - 連資料庫前先驗證存檔樣板（格式與必要結構）與官方節點檔。
  - 分頁落回「共通」、`fc_node` 不在官方節點表、檔案沒有官方場景節點，都寫進 `warnings.txt`。
  - JSON 欄位壞掉時附 `link_id`。
  - 路線預設改成校準實測的優先。
- **其他**：
  - `.env.example` 的 `GAME_ASSETS_ROOT` 恢復成佔位值。
  - 魔女的夜宴擷取器的用法範例改成 NAS 路徑。

測試：

- 新增與改寫 5 個測試檔，共 118 個：
  - `test_story_savestruct.py`：含寫入器輸出的 golden 文字，以及每一種格式錯誤。
  - `test_corpus_import_senren_banka.py`：含用假連線測 `write()` 的重跑保護、刪除只限這款遊戲、id 位移。
  - `test_story_extractor_senren_banka.py`：分頁、章節、官方標題、警告、樣板檢查、設定驗證。
  - `test_story_api_savestruct.py`：走 API 下載 saveStruct 存檔、壞樣板的錯誤碼。
  - `test_es_sync_game.py`：`--game` 先寫後刪、索引不存在、中途失敗時舊文件仍在。
- 真實樣板測試改由環境變數 `SENREN_MATERIAL` 開啟。
- 全部 `tests`：1,705 過、7 略過、2 失敗。2 個失敗是本機 `.env` 的 LLM 設定造成，和本次無關。

### 9.11 整體驗收（實際服務 → 遊戲）

- 用實際的 `StoryService.make_save`（新庫＋本機素材）產生 5 個存檔：
  - 共通 010 選項之後（路徑變數 4 個）
  - 芳乃路線
  - 小春 508a（標籤帶 `:2`）
  - 015 結尾＋茉子預設
  - 015 結尾＋芳乃預設
- 在多開 SB_1 讀檔（混音器靜音），5 個都停在指定的句子，`user` 變數一致。
- 兩個 015 的存檔接著自動推進：茉子預設進到 `mak_part_0:2`，芳乃預設進到 `yos_part_0:2`。路線預設確實能讓遊戲走進對的路線。
- 紀錄在 `SB_calib\work\story_save_verify.jsonl`；`verify_story_saves.py` 新增 `until`（推進到指定標籤）。

### 9.12 本機上線（不用 MinIO，2026-11-08）

使用者決定先只在本機上線，素材用本機資料夾（後端以 `STORY_ASSETS_DIR` 讀取），不動正式機、不跑 ES 同步。

- **素材資料夾**：`FluencyTides\build\story-assets`（`build/` 已在 `.gitignore`），兩款遊戲共用。
  - 千戀＊萬花：從 worktree 的 `build\story-assets` 搬過來。縮圖 20,527、語音 24,861、BGM 54、樣板 1。
  - 魔女的夜宴：用 NAS 素材重新擷取。建置包的 `source_hash` 和上線版本 2 相同（`d2029e…`），所以用 `--assets-only` 只補素材，不建新版本：

```bash
python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --assets-only --database fluencytides_corpus --assets "local:C:/Users/forip/Desktop/WorkSpace/Python/FluencyTides/build/story-assets"
```

- **本機 `backend/.env`**（改之前已備份成 `backend/.env.bak_20261108_story_assets`）：

```text
STORY_ASSETS_DIR=C:\Users\forip\Desktop\WorkSpace\Python\FluencyTides\build\story-assets
STORY_CORPUS_DATABASE=fluencytides_corpus
```

- **啟動**：`.claude/launch.json` 新增 `backend-senren-banka-no-bot`（跑 worktree 的後端，port 8000）與 `frontend-senren-banka`（port 5176）。
- **驗證**：
  - `/api/v1/story/games` 列出兩款遊戲；千戀＊萬花的 `bgm` 能力為 true。
  - 千戀＊萬花流程圖頁：總覽 6 個路線、穗織之櫻主題、節點抽屜顯示縮圖與日／繁台詞。
  - 語音 `uts001_001` 回 `audio/mpeg`；BGM `BGM01`（今昔の街／小鎮今昔）回 `audio/ogg`；存檔下載回 `data_quick_01.bmp`（`application/octet-stream`）。
- **魔女的夜宴的素材**：
  - 魔女的夜宴：縮圖 18,069、語音 23,491（NAS 只缺 2 個：`nen002_039_bgv`、`nen020_112`，先前記錄的 4,366 個已補上）、樣板 1；流程圖頁的縮圖、語音、存檔下載（`data_quick_01.jpg`）都正常。
- **正式機上線**：見 §9.13。

### 9.13 正式機 SSH 部署（2026-11-08）

使用者要求先用 SSH 部署（`deploy_remote.sh`，不經 CI）。

**部署前的現況**

- 正式機已經切換到新庫，而且和本機共用同一台 MySQL（§1 的更正）。所以部署前正式機的 `/story/games` 就已經列出千戀＊萬花（版本 3）。當時的舊程式不認得 `kirikiri_savestruct` 引擎，存檔功能顯示為關閉；正式機上也還沒有素材。
- 正式機跑的是 `db22e35`：main（`4b99f04`）再加 14 個還沒合併的 commit（人脈卡登錄日、建卡草稿 `server_drafts`）。直接部署本分支會拿掉這些功能，所以改成疊在 `db22e35` 上部署。
- 正式機 `.env` 已經有 `STORY_ASSETS_DIR=/app/data/story-assets`（容器內路徑，主機是 `/DATA/AppData/FluencyTides/backend/data/story-assets`），`MYSQL_DATABASE` 也已經是新庫，**這次不用改 `.env`**。

**步驟**

1. 在 `db22e35` 開臨時 worktree `.claude/worktrees/deploy-senren-banka`（detached），套上本分支的 diff 和未追蹤檔。只有 `config.py`、`.env.example` 兩個檔和那 14 個 commit 重疊，而且都只改說明文字，3-way 套用沒有衝突。
2. 在組合後的版本跑檢查：
   - 後端：ruff 通過；pytest 1,883 通過、7 略過、1 失敗。失敗的是 `test_json_field_editor.py::test_writes_whole_list_and_keeps_unknown_keys`（人脈卡那批的測試），原因是測試用 UTC 日期（11/9）比本機日期（11/8），過午夜才會發生，和本次改動無關。
   - 前端：lint 0 錯誤、356 個測試通過、build 成功。
3. 上傳千戀＊萬花素材到正式機（tar 經 SSH，2.8 GB）：縮圖 20,527、語音 24,861、BGM 54、樣板 1。魔女的夜宴在正式機已有素材，沒有動。

```bash
tar -C build/story-assets -cf - story/SenrenBanka | ssh jacky917@192.168.50.172 'tar -C /DATA/AppData/FluencyTides/backend/data/story-assets -xf -'
```

4. 從臨時 worktree 部署：

```bash
./deploy_remote.sh all --allow-dirty
```

- 版本 `3858f03`（`db22e35`＋未 commit 的改動），82 秒完成。
- 回滾點：`fluencytides-backend:pre-db22e35`、`fluencytides-frontend:pre-db22e35`。

**驗證**（在正式機上經前端 nginx 打 API）

- `/story/games`：兩款遊戲的存檔功能都打開；千戀＊萬花的 BGM 打開。
- 千戀＊萬花：劇情圖 255 個節點、297 條邊；縮圖回 `image/jpeg`；語音 `uts001_001` 回 `audio/mpeg`；BGM `BGM01` 回 `audio/ogg`；存檔下載回 `data_quick_01.bmp`（`BM` 開頭）；主題 `story-themes/senren-hoori/theme.json` 回 200。
- 魔女的夜宴：存檔下載回 `data_quick_01.jpg`（JPEG 開頭）；語音正常。
- 後端 log 沒有 5xx 或例外。

**要記得**

- **還沒合併進 main**。下一次 CI 部署 main，或在 Portainer 按「Pull and redeploy」，都會蓋回 main 的版本。到時千戀＊萬花的存檔會再變成關閉，主題也會不見。要長期留在正式站，得把本分支和那 14 個 commit 都合併進 main。
- 回滾：

```bash
./deploy_remote.sh rollback backend pre-db22e35
```

```bash
./deploy_remote.sh rollback frontend pre-db22e35
```

- ~~**ES 同步還沒跑**：千戀＊萬花的台詞還搜尋不到。跑法見 §9.4。~~ 2026-11-14 已同步，搜尋得到。
