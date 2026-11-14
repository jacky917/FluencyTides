# FEAT — 千戀＊萬花的素材結構：從解包素材只取 Anki 建卡、劇情流程圖、語料庫會用到的內容

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-08 |
| **性質** | 素材結構設計（還不實作） |
| **狀態** | ✅ 成品已產生並通過驗證（2026-11-08；成品在 `Y:\material\yuzusoft\SenrenBanka`，原始解包不動）。後端匯入（語料庫、流程圖）已由下一份計畫 `docs/wip/senren_banka_corpus_import_FEAT_2026-11-08.md` 完成（PR [#40](https://github.com/jacky917/FluencyTides/pull/40)，2026-11-12 合併）。本計畫沒有剩餘工作，2026-11-12 歸檔 |
| **範圍** | 成品素材 `Y:\material\yuzusoft\SenrenBanka\`（NAS `\192.168.50.250\sn640`）的結構與命名；產生腳本 `game\千戀＊萬花\SB_calib\work\build_materials.py`、`build_avatars.py`；`game_profiles` 的 `voice_dir`／`avatar_dir` 值 |
| **不動** | 校準產出（`SB_point_table`、`SB_flowchart_nodes`、`SB_bgm_titles`，只讀）；FluencyTides 的程式碼與資料庫（語料庫匯入、流程圖擷取器是下一份計畫）；魔女的夜宴的素材；遊戲本體與加密封包 |
| **PR / 進度** | 素材已產生（2026-11-08）。本計畫不改 repo 的程式，產生腳本在 `game\千戀＊萬花\SB_calib\work\`；計畫書本身隨 PR [#40](https://github.com/jacky917/FluencyTides/pull/40) 進 main |
| **關聯文件** | `docs/wip/story_flowchart_FEAT_2026-10-31.md`（流程圖建置包與素材物件鍵）；`docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md`（`game_*` 資料表）；`game\千戀＊萬花\SB_calib\README.md`（校準，§5.15～5.16 素材核對）；`game\千戀＊萬花\SB_material_check.json` |

> **2026-11-08 定案**（使用者：「就照這個方案，複製一份放到 Y:\material\yuzusoft\SenrenBanka，不要更動原文件」）：
> - 成品輸出到 NAS 的 `Y:\material\yuzusoft\SenrenBanka\`。C 槽的原始解包 `material\yuzusoft\SenrenBanka\` **留在原地、不搬也不改**，取代 D1 的「移到 `raw\`」；`check_material.py`、`make_table.py` 的路徑因此不用改。
> - Q2：魔女的夜宴的規格從 Anki 媒體資料夾（`SabbatOfTheWitch_*.mp3`／`.jpg`）取得：語音 mp3、48 kHz、單聲道、128 kbps；頭像 jpg、約 165～255 px 的正方形臉部裁切（13 人）。
> - Q3 保留 496×279；Q4 自動合成（樣張先給使用者確認）；Q5 不收；Q6 成品不另備份（腳本可重產）。
> - D7 更正：存檔樣板是「縮圖後面接存檔資料」的合體檔（魔女的夜宴的 jpg、千戀＊萬花的 bmp 都是），不能轉檔，改為原樣複製成 `templates\quick.bmp`。

---

## 1. 問題與動機

**現況**

- 使用者在 `material\yuzusoft\SenrenBanka\` 放了完整解包：57,515 個檔、約 18 GB。內容包括劇本、語音、BGM、CG、立繪、UI、影片。
- 2026-11-08 已用它逐句核對錨點表：劇本 55,726 句全部對上，繁中與日文逐句一致，說話者、語音零差異（校準 README §5.15～5.16）。
- 三個下游會用到千戀＊萬花的素材，各自要求固定的格式與檔名：

| 下游 | 需要的素材 | 現有規格（程式碼位置） |
|---|---|---|
| Anki 建卡（JP_VerbPair、JP_CoreVerb） | 台詞文字（含注音）、語音、說話者頭像 | 語音 `<voice_dir>/<voice_file>.mp3`；頭像 `<avatar_dir>/<game_speakers.avatar_file>`，沒有時用 `<說話者>.jpg`；注音 `[よみ]漢字` 存在資料庫、顯示時去掉（`scripts/fastapi_client/JP_VerbPair/pipeline_components/context_builder.py`） |
| 劇情流程圖 | 縮圖、語音、BGM、存檔樣板 | 建置包 manifest 的 `thumbs_dir`（`<雜湊>.jpg`）、`voices_dir`（`<voice_file>.mp3`）、`bgm_dir`（`<代號>.ogg`，沒有時找 `.mp3`）、`templates`（`scripts/story/common/package.py`、`import_story_build.py`） |
| 語料庫（`fluencytides_corpus`） | 台詞、說話者、BGM 曲目 | `game_lines.ruby_ja_jp`／`voice_file`／`thumb`、`game_speakers`、`game_bgm_tracks`（`scripts/database/corpus_rebuild/schema.sql`） |

**問題**

- 解包裡大部分用不到：CG 7 GB、影片 3.4 GB、UI 素材 0.9 GB、立繪圖層、系統語音、H 循環聲。
- 用得到的格式也不對：語音和 BGM 是 flac，下游要 mp3／ogg；劇本是 92 個、每個約 4 MB 的 json，下游只要其中的台詞欄位。
- 頭像沒有現成檔：立繪是分層素材，要合成才有臉。
- 縮圖不在解包裡：它是校準時從存檔取的，在 NAS `SB_thumbs\`，是無損 WebP，下游要 jpg。
- 解包和成品放在同一個資料夾，`GAME_ASSETS_ROOT` 指過來時分不清哪些是可用的成品。

## 2. 目標與非目標

**目標**

- G1：訂出 `material\yuzusoft\SenrenBanka\` 的成品結構。每個下游要的檔都有固定位置、檔名和格式，可以直接設成 `voice_dir`、`avatar_dir`、`voices_dir`、`thumbs_dir`、`bgm_dir`。
- G2：成品只放用得到的內容，數量和錨點表、劇本一一對得上（例：語音＝台詞引用的 24,982 個）。
- G3：原始解包和成品分開，原始解包整包保留、不修改。
- G4：有一份清單（`MANIFEST.json`）記錄每一類的數量、來源、轉檔參數與雜湊，產生後可以自動驗證。

**非目標**

- 這份計畫不寫產生腳本、不轉檔。等結構定案後再開工。
- 不做語料庫匯入與流程圖擷取器。千戀＊萬花的 `content.py`／擷取器是下一份計畫，這裡只保證它們要的素材到位。
- 不收 CG、立繪全身圖、背景、影片、UI 素材、英文與簡中。目前三個下游都沒用到，要用時再加一類，原始解包仍在。
- 不收 H 場景循環聲（`playvoice` 的 268 個）和系統語音。它們不是台詞，沒有 `voice_file` 會指到它們。
- 不動魔女的夜宴的素材搬遷。`backend/.env` 仍指向舊的 `material\voice\yuzusoft\SabbatOfTheWitch`，要另外處理。

## 3. 設計決策

### D1 成品結構

```text
material\
  raw\yuzusoft\SenrenBanka\        原始解包（現在 material\yuzusoft\SenrenBanka\ 的內容整包搬來，唯讀）
  yuzusoft\SenrenBanka\            成品（GAME_ASSETS_ROOT 底下）
    MANIFEST.json                  清單：各類數量、來源、轉檔參數、產生時間、雜湊
    voice\<voice_file>.mp3         台詞語音 24,982 個
    bgm\<代號>.ogg                 BGM 55 首：劇情 47、標題 BGM61、歌曲 7
    bgm\<代號>.sli                 BGM 循環點（只有 48 首 BGM 有）
    avatar\<說話者日文名>.png      說話者頭像，透明背景（有立繪的 12 人）
    avatar_sky\<說話者日文名>.jpg  帶背景版：青空
    avatar_shrine\<…>.jpg          帶背景版：神社境內
    avatar_place\<…>.jpg           帶背景版：各角色常出現的地點
    thumbs\<雜湊>.jpg              存檔縮圖 20,527 張（錨點表引用的）
    templates\quick.bmp            存檔樣板（流程圖「下載存檔」用；原樣複製）
    text\script_lines.jsonl        劇本每句的精簡版（含注音）
    text\speakers.json             說話者對照
    calib\                         跑遊戲校準的成果，原樣複製（D10）
```

- **選了**：原始解包移到 `material\raw\yuzusoft\SenrenBanka\`，成品放回 `material\yuzusoft\SenrenBanka\`。
  - 和使用者 11/7 建的 `material\yuzusoft\SabbatOfTheWitch\{avatar,voice}` 同一個慣例。
  - `GAME_ASSETS_ROOT`（`material\`）底下只有成品；`game_profiles.voice_dir`＝`yuzusoft/SenrenBanka/voice`、`avatar_dir`＝`yuzusoft/SenrenBanka/avatar`。
- **放棄**：原始解包留在原地、成品放在 `_out\` 子資料夾。這樣 `voice_dir` 會變成 `yuzusoft/SenrenBanka/_out/voice`，以後每款遊戲都要多一層。
- **放棄**：原始解包刪掉只留成品。之後要加 CG、影片等類別時就得重新解包。

### D2 台詞文字：`text\script_lines.jsonl`

- 從 `raw\…\adult\scn\*.json` 取出每一句，一句一行。92 個檔（約 290 MB）縮成一個檔（預估約 30 MB）。
- 用成人版 `adult\scn`：錨點表的 92 個場景檔都在這裡；全年齡版 `scn` 只有 89 個。
- 欄位：

| 欄位 | 內容 | 例 |
|---|---|---|
| `file` | 場景檔名 | `001・アーサー王ver1.07.ks` |
| `label` | 標籤 | `com_part_1:2` |
| `idx` | 標籤內句序（0 起算） | `11` |
| `point` | 存檔點（沒有存檔點的句子為 null） | `15` |
| `speaker_jp`／`speaker_tw` | 說話者 | `運転手`／`司機` |
| `ruby_ja_jp` | 日文原文，保留注音 `[よみ]` 和樣式碼 | `「[し]志[な]那[つ]都荘まで行って欲しいんですけど」` |
| `text_ja_jp` | 日文，去掉注音、換行碼 `\n`、樣式碼 `%數字;`、`%;` | `「志那都荘まで行って欲しいんですけど」` |
| `text_zh_hant` | 繁中，去掉換行碼 | |
| `voices` | 語音檔名（不含副檔名） | `["uts001_001"]` |

- **錨點表仍是主要資料**：位置、劇情順序、縮圖、BGM、旗標、流程圖節點都以錨點表為準。這個檔只補兩樣錨點表沒有的東西：
  - 帶注音的日文，對應 `game_lines.ruby_ja_jp`；Anki 卡片會用到。
  - 以後核對要用的完整劇本文字，不必再打開 290 MB 的原始 json。
- 對應方式沿用 `check_material.py`：存檔點 `p` 後面第一個整數 `t` 就是第 `t` 句。所以 `(file, label, point)` 可以直接對到錨點表。

### D3 語音：`voice\<voice_file>.mp3`

- **範圍**：劇本台詞引用的 24,982 個（＝錨點表的不重複語音數），來源 `raw\…\voice\`、`raw\…\adult\voice\`。
- **格式**：flac 轉 mp3，檔名用劇本裡的語音名稱、不帶原副檔名，例 `uts001_001.mp3`。
  - 錨點表的 `voice` 欄是 `uts001_001.ogg`；`game_lines.voice_file` 存主幹 `uts001_001`，所以一致。
  - 檔名都符合 `^[A-Za-z0-9_]{1,64}$`（`import_story_build.py` 的 `VOICE_NAME`）；產生時要再檢查一次。
- **規格**：沿用魔女的夜宴的語音 mp3（位元率、聲道、取樣率）。魔女的夜宴的語音目前不在 `material` 底下（新資料夾是空的），開工前要先找到一個檔確認規格（§6 待決 Q2）。
- **大小**：flac 5.3 GB；mp3 實測 1.92 GB（預估 1 GB 以下偏低），已寫進 `MANIFEST.json`。

### D4 BGM：`bgm\<代號>.ogg`

- **範圍**：解包 `bgm\` 的 55 首全部收：
  - 劇情 47 首（BGM01～BGM56）
  - 標題 BGM61
  - 歌曲 7 首（SONGED1～6、SONGOP，音樂鑑賞用）
- **格式**：flac 轉 ogg（Vorbis）。流程圖 API 先找 `.ogg` 再找 `.mp3`，所以只需要 ogg。檔名用代號，和 `game_bgm_tracks.code`、`SB_bgm_titles.json` 的 `code` 一致。
- **循環點**：48 個 `.sli` 照原樣複製成 `bgm\<代號>.sli`。使用者決定保留 `.sli`（2026-11-08）；目前播放端不用，但要無縫循環時只能靠它。
  - 轉檔不可以改變取樣率，否則 `.sli` 的取樣位置會失效。
- **曲名**：用 `SB_bgm_titles.json`（日／繁曲名、音樂鑑賞序號、`bgm.xp3` 序號），已複製到 `calib\`（D10）。
- **序號已驗證**：解包檔名排序和 `bgm.xp3` 序號完全吻合（校準 README §5.15）。

### D5 縮圖：`thumbs\<雜湊>.jpg`

- **範圍**：錨點表 `thumb` 欄引用的 20,527 張（補錄 `dummy9` 後多 1 張），來源是 NAS `SB_thumbs\<前 2 字>\<雜湊>.webp`。NAS 上另有 712 張沒被引用，不收。
- **格式**：WebP 轉 jpg，檔名是雜湊加 `.jpg`，和流程圖 `thumbs_dir`、`game_lines.thumb`（CHAR(16)）一致。
- **尺寸**：保留原尺寸 496×279（存檔縮圖原尺寸）。魔女的夜宴是 230×129，因為它的存檔縮圖本來就這麼大；流程圖前端要確認能接受不同尺寸（§6 待決 Q3）。
- **畫質**：jpg 品質 85，預估約 600 MB；實測後寫進清單。

### D6 頭像：`avatar\<說話者日文名>.png`（另有三套帶背景 jpg）

- **範圍**：有立繪的角色 13 個，加上白狛／祟り神。立繪來源 `raw\…\fgimage\<角色>\`：分層 png、圖層位置表 `<角色><姿勢>.txt`、`.stand` 立繪定義。
- **做法（定案）**：自動合成，不手工截圖（`build_avatars.py`）。
  1. 依圖層位置表，用姿勢 a 的基本衣裝加預設表情合成立繪。
  2. 以表情圖層的位置為中心裁出臉部（原本打算用 `data\others\facezoom.csv`，實作時改用表情圖層，見下方實作細節）。
  3. 存成正方形 png，透明背景（使用者 2026-11-08 要求：原版要透明）。
- **檔名**：日文說話者名，例 `芳乃.png`。
  - `text\speakers.json` 的 `avatar_file` 填 `芳乃.png`。
  - **注意**：Anki 建卡沒有 `game_speakers.avatar_file` 時找的是 `<說話者>.jpg`（`context_builder.py` 寫死 `.jpg`）。原版是 png，所以匯入時要填 `avatar_file`；或是 `avatar_dir` 改指帶背景的資料夾（jpg，檔名同規則）。
  - 一人多種顯示名（例：`？？？`）不另做頭像，由 `text\speakers.json` 指到同一個檔。
- **沒有立繪的說話者**（路人、運転手等）不做頭像，建卡時顯示「none」，和魔女的夜宴相同。
- **實作細節**（2026-11-08）：
  - 立繪有兩套圖：`<角色>a_<id>.png` 是原尺寸，對應 `<角色>a.txt`（畫布 3600×5100）；`<角色>a_0_<id>.png` 是半尺寸，對應 `<角色>a_0.txt`。用原尺寸那套。
  - 表情用 `01`（基本表情、睜眼）。`笑顔1` 常是閉眼，辨識度差。
  - 衣裝用各角色的代表衣裝：芳乃巫女服、茉子忍者、ムラサメ私服、レナ・小春・廉太郎制服、芦花・心子仕事着、みづは白衣、安晴神主、玄十郎・比奈実私服。
  - 以表情圖層為中心裁正方形（表情寬 × 2.3、中心上移 12%），輸出 256×256。魔女的夜宴約 165～255 px。使用者看過圓形遮罩預覽，確認置中（2026-11-08）。
  - 原版 `avatar\`：透明背景 png。
  - 帶背景三套（使用者 2026-11-08 要求）：背景取 `bgimage` 的中央正方形，輕微高斯模糊（256 px 下半徑 2），jpg 品質 90。
    - `avatar_sky\`：全員 `空_青空`。
    - `avatar_shrine\`：全員 `神社_境内A`。
    - `avatar_place\`：依角色：芳乃 `朝武_芳乃の部屋A`、茉子 `ヒロイン_茉子の部屋A`、ムラサメ `神社_神社内（刀）A`、レナ `旅館_レナの部屋A`、小春・廉太郎・比奈実 `学院_教室A`、芦花 `街_甘味処A`、みづは `街_診療所A`、玄十郎 `旅館_旅館前A`、安晴 `神社_境内A`、心子 `旅館_廊下A`（對應是推定，可改 `build_avatars.py` 的 `BG_SETS`）。
  - 產生：`python build_avatars.py --all`（原版）、`--all --bg sky|shrine|place`（帶背景版）。
  - 12 位有立繪的角色。白狛、祟り神沒有編號立繪資料夾，主角將臣沒有立繪，不做頭像。

### D7 存檔樣板：`templates\quick.jpg`

- 來源：校準用的 `SB_calib\work\quick_template.bmp`（424 KB：496×279 的縮圖，後面接存檔資料）。
- **原樣複製**成 `templates\quick.bmp`，對應建置包的 `story/SenrenBanka/templates/quick.bmp`。不能轉 jpg：存檔資料接在圖片後面，轉檔會遺失（魔女的夜宴的 `SW_calib\quick_template.jpg` 也是 jpg 加存檔資料）。
- 流程圖的「下載存檔」用它當底，寫入器依 `game_profiles.engine`＝`kirikiri_packinone` 選擇，和魔女的夜宴共用。

### D8 說話者：`text\speakers.json`

- 錨點表有 181 組（日文名、繁中名）。每個日文名一筆：

```json
{"name_ja_jp": "芳乃", "name_zh_hant": "芳乃", "voice_prefix": "yos", "avatar_file": "芳乃.png", "line_count": 0}
```

- `voice_prefix` 取語音檔名的英文前綴（例 `yos112_246` → `yos`），對應 `game_speakers.voice_prefix`。
- 繁中名用遊戲畫面的顯示名（`name_zh_hant_src`＝`game`）。

### D9 清單與驗證：`MANIFEST.json`

```json
{
  "game_id": "SenrenBanka",
  "generated_at": "2026-11-xx",
  "sources": {"raw": "material/raw/yuzusoft/SenrenBanka", "anchor_table": "game/千戀＊萬花/SB_point_table.json", "thumbs": "//192.168.50.250/…/SB_thumbs"},
  "voice":  {"count": 24982, "format": "mp3 …", "bytes": 0, "sha256_list": "voice.sha256"},
  "bgm":    {"count": 55, "format": "ogg q…", "sli": 48},
  "thumbs": {"count": 20527, "format": "jpg q85 496x279"},
  "avatar": {"count": 12, "format": "png 256x256 透明背景", "missing": ["運転手", "…"]},
  "avatar_variants": {"sky": {"count": 12}, "shrine": {"count": 12}, "place": {"count": 12}},
  "text":   {"script_lines": 55726, "speakers": 0}
}
```

產生腳本最後跑驗證，任何一項不符就失敗：

- 錨點表每個語音都有 `voice\*.mp3`。
- 錨點表每個縮圖都有 `thumbs\*.jpg`。
- `SB_bgm_titles.json` 每個代號都有 `bgm\*.ogg`。
- `script_lines.jsonl` 有 55,726 句，和錨點表逐句對得上（沿用 `check_material.py`）。
- 每個說話者不是有頭像，就是列在 `avatar.missing`。
- `calib\` 每個檔都在，大小和來源相同。

### D10 校準成果：`calib\`

- 使用者 2026-11-08 要求：跑遊戲得到的成果也放進素材資料夾，讓素材資料夾自成一套。
- 原樣複製，檔名不變；來源是 `game\千戀＊萬花\`（校準 README 來自 `SB_calib\README.md`）：

| 檔案 | 內容 |
|---|---|
| `SB_point_table.json`、`.csv` | 錨點表：劇情順序、存檔點、旗標、選項、流程圖節點、BGM 代號、CG 解鎖、縮圖雜湊 |
| `SB_bgm_titles.json`、`.csv` | BGM 代號與日／繁曲名、音樂鑑賞序號 |
| `SB_flowchart_nodes.json`、`.csv` | 334 個流程圖節點與官方標題 |
| `SB_flowchart.md`、`.html` | 流程圖（實測與推定的連線分開標示） |
| `SB_reverse_notes.md` | 逆向筆記 |
| `SB_material_check.json` | 和解包素材逐句核對的結果 |
| `README_calib.md` | 校準方法、錨點表欄位說明 |

- 校準成果改過後，要重跑 `python build_materials.py --only calib`；每次都會整份覆蓋。
- `MANIFEST.json` 的 `calib.files` 記每個檔的大小和來源。

## 4. 改動清單（開工時）

| 項目 | 位置 | 內容 |
|---|---|---|
| 搬移原始解包 | `material\yuzusoft\SenrenBanka\` → `material\raw\yuzusoft\SenrenBanka\` | 整包搬，不改內容。搬之前先確認沒有程式指向舊路徑（目前只有 `check_material.py` 的 `MAT`） |
| 產生腳本 | `game\千戀＊萬花\SB_calib\work\build_materials.py`（新） | 依 D2～D9 產生成品；可重跑，已存在且雜湊相同就跳過；`--only voice,bgm,…` 分類執行 |
| 頭像合成 | 同上，或獨立的 `build_avatars.py` | D6；先做芳乃一個人給使用者確認再全做 |
| 核對腳本 | `check_material.py` | `MAT` 改指 `material\raw\…`；`make_table.py` 透過它讀劇本，也一起跟著改 |
| 後端設定 | `game_profiles`（語料庫匯入時寫入） | `voice_dir`＝`yuzusoft/SenrenBanka/voice`、`avatar_dir`＝`yuzusoft/SenrenBanka/avatar` |

建議的執行順序（每一步都可以單獨驗收）：

1. 搬移原始解包，`check_material.py`、`make_table.py` 改路徑後重跑，結果不變。
2. `text\`（D2、D8）：不轉檔、最快，先讓下一份語料庫匯入計畫可以開始。
3. `bgm\`、`templates\`（D4、D7）：數量少，順便確認轉檔參數。
4. `voice\`（D3）：最大量，背景執行。
5. `thumbs\`（D5）：讀 NAS，背景執行。
6. `avatar\`（D6）：先做一張給使用者看。
7. `MANIFEST.json` 與驗證（D9）。

執行範例（之後的腳本，目前不存在）：

```bash
python build_materials.py --only text
```

```bash
python build_materials.py --only voice
```

```bash
python build_materials.py --verify
```

## 5. 驗收

- [x] ~~原始解包搬到 `material\raw\…`~~：改為成品輸出到 `Y:\material\yuzusoft\SenrenBanka\`，原始解包留在原地不動（使用者 2026-11-08 決定）。
- [x] `voice\` 24,982 個 mp3（1.92 GB），錨點表每個語音都找得到；轉檔錯誤 0。
- [x] `bgm\` 55 個 ogg（251 MB）、48 個 sli；`SB_bgm_titles.json` 每個代號都找得到。
- [x] `thumbs\` 20,527 張 jpg（675 MB），錨點表每個縮圖都找得到。
- [x] `text\script_lines.jsonl` 55,726 句；`--verify` 逐句對錨點表（繁中、注音日文），零差異。
- [x] 頭像：使用者確認樣張與圓形預覽後全做（原版透明 png＋三套帶背景 jpg，各 12 張）；沒有頭像的說話者列在清單。
- [x] `MANIFEST.json` 的數量與驗證全部通過（2026-11-08，`verify.ok`＝true；沒有頭像的說話者 104 人列在 `avatar.missing`）。

## 6. 風險與待決

| # | 問題 | 建議 | 需要誰決定 |
|---|---|---|---|
| Q1 | 原始解包要搬到 `material\raw\…`，還是留在原地、成品另放？ | 搬（D1） | 使用者 |
| Q2 | 魔女的夜宴的語音 mp3 和頭像在哪裡？要照它的規格（位元率、頭像尺寸） | 找到後照抄規格 | 使用者告知位置 |
| Q3 | 縮圖保留 496×279，還是縮成和魔女的夜宴一樣的 230×129？ | 保留原尺寸，流程圖前端確認可接受 | 使用者 |
| Q4 | 頭像用自動合成（D6），還是使用者自己準備？ | 自動合成，先出一張樣張 | 使用者 |
| Q5 | 要不要收 H 循環聲、系統語音、CG、影片？ | 先不收（非目標），原始解包還在 | 使用者 |
| Q6 | 成品要不要也放 NAS 備份？ | 成品可由腳本重產，只備份 `MANIFEST.json` 和頭像（頭像有人工確認） | 使用者 |

- **風險**：原始解包是使用者提供的。校準流程原本只透過存檔與遊戲畫面取資料，這份設計中只有 D2 注音、D3 語音、D4 BGM、D6 頭像、15 句結局最後一句的說話者與語音取自解包。`MANIFEST.json` 的 `sources` 要寫清楚每一類的來源。
- **風險**：`check_material.py` 和 `make_table.py` 目前寫死素材路徑；搬移時兩邊要一起改，否則重產錨點表時會略過素材修正（找不到素材時會自動略過，不會報錯）。
