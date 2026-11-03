# FEAT — 通用劇情流程圖模組（首發：魔女的夜宴）：用遊戲式流程圖定位卡片、看台詞、下載存檔

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-31 |
| **性質** | 新增機能設計 + 實作工作項 |
| **狀態** | 🔨 實作中：P1–P3 完成、P4 後端與存檔下載完成、P5 主題完成（2026-11-01）；P0 遊戲內讀檔驗證、MinIO 上傳未做。見 §12 |
| **範圍** | 前端：新增可共用模組 `frontend/src/features/story-flowchart/`；網站整合層 `pages/StoryGraph.tsx`、`components/story/`、`hooks/useStory*.ts`、`lib/storyParams.ts`；卡片瀏覽頁的卡面面板加一顆按鈕。後端：新增 `api/story.py`、`services/story/`（含遊戲適配器註冊表）、`schemas/story.py`、`infrastructure/krkr/`；`fluencytides_corpus` 新增 `story_builds`、`story_node_lines`（台詞相關的表由語料庫計畫建立）。建置：`backend/scripts/story/`（通用匯入，加上各遊戲的擷取器） |
| **不動** | 既有圖譜頁 `/graph`（`KnowledgeGraph.tsx`、`graphView.ts`）；建卡流程與 `card_gen_log` 的寫入邏輯；`scripts` 表的內容；校準程式 `SW_calib`（只讀它的產出）；遊戲本體與加密封存檔（不解 xp3） |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md`（新語料庫資料庫，本計畫的前置）；`game\senren_ref\flowchart_spec.md`（千戀＊萬花流程圖規格，版型來源）；`game\SW_flowchart.md`（魔女的夜宴流程圖，自動產生）；`game\SW_reverse_notes.md`（存檔格式與校準）；`game\SW_calib\README.md`；`game\krkr_savepack.py` |

> 2026-10-31 改版：初稿只針對魔女的夜宴。使用者要求「高度封裝，以後別的文字類遊戲共用同一套底層」，因此重寫為「通用平台＋遊戲適配器」，魔女的夜宴是第一個接上的遊戲。節點改用 HTML、連線改用 SVG 的雙層渲染（D9）也在這次定案。

---

## 1. 問題與動機

**現況**

- 「核心動詞」（`jp_core_verb`）與「自他動詞」（`jp_verb_pair`）兩種卡片的例句，全部取自《魔女的夜宴》（サノバウィッチ）。有效紀錄（`generated_sentences_log`，`is_deleted=0`，2026-10-31 查詢）：

  | project | 有效紀錄 | 不同台詞 | 不同動詞 |
  |---|---|---|---|
  | `jp_core_verb` | 3,190 | 3,040 | 281 |
  | `jp_verb_pair` | 3,060 | 2,861 | 326 |

- 卡片只知道自己的例句，看不到這句在劇情的哪裡、前後發生什麼事，也沒辦法回到遊戲裡那一段。
- 魔女的夜宴的遊戲端資料已經齊備：
  - 校準得到的 point 表 `SW_point_table.csv/json`：53,241 句、101 個場景檔、415 個場景標籤。每句有存檔位置（scene + point）、日文與繁中台詞、BGM、H 標記；縮圖在繁中輪補上。
  - 選項去向：`calib_choices.jsonl`，46 個選項、94 個選擇。
  - 選項設定的變數：`calib_choice_vars.jsonl`。
  - 路線分歧實測：`calib_route_probe.jsonl`。
  - 存檔編碼器 `krkr_savepack.py`：校準的五萬多次探測都是用它改寫快速存檔，再讓遊戲讀檔，已經驗證過。

**使用者要的**

1. 一張**幾乎還原遊戲內流程圖**的劇情圖，樣式可以和遊戲不同。
2. 在圖上**快速定位卡片**。
3. 點節點看**詳細台詞**，有卡的台詞可以**跳到對應卡片**。
4. 對任一句台詞**產生並下載存檔**。
5. **不做**左側預覽面板；背景、按鈕等外觀要能**靈活替換**。
6. **高度封裝**：以後其他文字類遊戲（其他 ゆずソフト 作品或其他引擎）要能共用同一套底層，只補遊戲專屬的部分。

### 1.1 千戀＊萬花的畫法能不能直接套用？

**結構規則大致可以直接套用**，所以把它當作平台的預設版型。魔女的夜宴要補的 5 個例外，正好說明平台需要哪些擴充點：

| 千戀＊萬花 | 魔女的夜宴 | 平台要提供的 |
|---|---|---|
| 分頁 = 共通 ＋ 各路線 ＋ 總覽 | 共通、寧々、めぐる、紬、憧子、和奏，加上總覽 | 分頁由資料定義，數量不寫死 |
| 膠囊、票券、矩形、菱形、角色卡片 | 全部對得上（D5） | 核心節點類型 |
| 「CHAPTER N」帶 | `chapter　X-Y` 取 X；`Epilogue`、`After Story` | 章節是節點屬性，標籤文字由資料決定 |
| 沒有 END 圖形 | **019 有ノーマルエンド** | `ending` 類型 |
| 共通的出口全部由好感決定 | **和奏要「任一路線通關」** | 條件文字、`inferred`（推測）邊 |
| 路線內單線 | **寧々通關後 RESTART 才進 110–116** | `unlock`（通關後）邊 |
| 沒有 AFTER | **各路線的 AFTER** | 章節標籤自由、`unlock` 邊 |
| 2、3 個選項 | **019 有 4 個選項** | 選項數不設上限 |
| 節點狀態依玩家進度 | 網站沒有玩家進度 | 狀態由外部決定（D10） |

---

## 2. 目標與非目標

**目標**
- G1 **通用平台**：流程圖模組、資料格式、後端 API、匯入流程都與遊戲無關。新增一款遊戲只需要：寫一個擷取器、填一份遊戲設定、在需要存檔時提供引擎的存檔寫入器（§8 清單）。
- G2 **前端模組可獨立使用**：`features/story-flowchart/` 不依賴網站的 API、路由、卡片；資料由 props 傳入，互動以 callback 回報。
- G3 魔女的夜宴的 `/story/SabbatOfTheWitch` 頁：
  - 以分頁呈現共通與 5 條路線，加上總覽。
  - 版面遵循 `flowchart_spec.md` 第一部分的規則，§1.1 的例外也要畫出來。
- G4 **卡片**：
  - 節點顯示卡片數徽章，可依卡片類型篩選、依動詞搜尋並高亮。
  - 抽屜列出台詞，有卡的台詞可以跳到 `/cards` 的那張卡。
  - 卡片頁可以反查回流程圖。
- G5 **存檔**：抽屜中每句有 `point` 的台詞都可以「下載存檔」，放進 `savedata` 後能在遊戲中讀到那一句（背景語音沒有 `point`，見 D12）。
- G6 **外觀可替換**：主題由一份設定檔（CSS 變數、尺寸、字型、素材網址、自訂 CSS）驅動；每個元素都有穩定的 class 名稱與 `data-*` 屬性可以套樣式。換主題不改元件程式碼。
- G7 頁面狀態都寫在網址，可以分享、重新整理後還原、用上一頁返回。
- G8 **資料可以不經部署更新**：重建劇情圖後匯入資料庫即可上線；匯入是原子切換，可以回滾。

**非目標**
- 左側預覽面板：使用者明確不做。
- 現在就接第二款遊戲：只做魔女的夜宴。通用性用一份手工的千戀＊萬花片段 fixture 驗證（P2），不做千戀＊萬花的資料擷取。
- 網頁播放 BGM：音檔還沒有，只預留欄位、端點與播放鍵（預設關閉，見 §12「BGM 預留」）。語音播放後來已加上（§12）。
- 在網站上編輯劇情圖：圖由建置流程產生，網站只讀。標題只透過覆寫檔修改。
- 修改全域存檔（例：魔女的夜宴的 `datasu.ksd`）：網站只產生欄位存檔。
- 橫向流程圖：排版先分成「邏輯格位」與「幾何投影」兩步（D8），但第一版只做直向。

---

## 3. 架構總覽

```
┌──────────────── 離線建置（本機） ─────────────────┐
│ 遊戲專屬擷取器               通用建置與匯入        │
│ （SW：讀校準產出）  ──▶  建置包（build package）  ──▶ import_story_build.py ──┐
│ （未來：其他遊戲）        graph.json + node_lines.jsonl                              │
│                          + manifest.json + 素材         （驗證 → 寫入 → 原子切換）  │
└────────────────────────────────────────────────────┘                          │
                                                                              ▼
┌──────────────── 儲存 ─────────────────────────────────────────────────────┐
│ fluencytides_corpus：story_builds（含 graph JSON） ─ story_node_lines           │
│   ╲ join ╱ game_profiles、game_lines 等 game_* 系列（語料庫計畫）               │
│   ╲ join ╱ rel_links（卡片 ↔ 台詞，core.example_from）                          │
│ MinIO：story/<game>/thumbs/*、story/<game>/templates/*                        │
└──────────────────────────────────────────────────────────────────────────┘
                                                                              │
┌──────────────── 後端 ─────────────────────────────────────────────────────┐
│ api/story.py（通用端點）→ services/story/（通用服務）                         │
│        ├─ 遊戲適配器註冊表：@register_story_game("SabbatOfTheWitch")      │
│        ├─ 註記提供者：cards（讀 rel_links 的 core.example_from）              │
│        └─ 存檔寫入器：kirikiri_packinone（infrastructure/krkr/）              │
└──────────────────────────────────────────────────────────────────────────┘
                                                                              │ JSON（D11）
┌──────────────── 前端 ─────────────────────────────────────────────────────┐
│ 網站整合層：pages/StoryGraph.tsx、hooks/useStory*、components/story/（抽屜）  │
│        │ props / callbacks                                                   │
│ 可共用模組：features/story-flowchart/                                         │
│        core/（型別、排版、路徑、主題，純 TS）  render/（React：節點層、連線層、 │
│        小地圖、分頁、捲動）  index.ts（唯一的公開入口）                         │
└──────────────────────────────────────────────────────────────────────────┘
```

**共用與遊戲專屬的切分**

| 共用（寫一次） | 遊戲專屬（每款遊戲各自提供） |
|---|---|
| 建置包格式與驗證（pydantic schema） | 擷取器：從該遊戲的資料來源產生建置包 |
| 節點合併、類型判定的通用規則、標題覆寫、Mermaid 匯出 | 遊戲設定：分頁、路線、跨頁出口、推測邊、章節解析規則 |
| 匯入、版本切換、回滾 | 標題覆寫檔 |
| 後端端點、卡片註記、路徑變數累加、快取 | 遊戲適配器（設定值、路線旗標預設） |
| 存檔寫入器介面；同一引擎的寫入器跨遊戲共用 | 存檔樣板、欄位檔名規則（屬於設定） |
| 前端流程圖模組、排版、主題系統 | 預設主題與素材（選配） |

---

## 4. 資料如何儲存

### D1 儲存位置

> 2026-10-31 改版：使用者決定重建語料庫資料庫，台詞、錨點、繁中改由 `docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md` 建立的新資料庫 `fluencytides_corpus` 提供。本計畫**不再自己存台詞**，只存流程圖的結構。

| 資料 | 存放 | 由誰負責 |
|---|---|---|
| 遊戲清單 | `fluencytides_corpus.games` | 語料庫計畫 |
| 場景檔、場景標籤 | `game_script_files`、`game_scenes` | 語料庫計畫 |
| 台詞、各語言文字、存檔位置（point）、縮圖雜湊、H 標記 | `game_lines`（多語欄位 `text_ja_jp`、`text_zh_hant`） | 語料庫計畫 |
| BGM | `game_bgm_tracks`、`game_line_bgm` | 語料庫計畫 |
| 卡片 ↔ 台詞的關聯 | `rel_links`（`core.example_from`，由 `card_gen_log` 回填與同步） | 語料庫計畫 D8 |
| **流程圖版本**：劇情圖＋流程圖設定 | `story_builds` | 本計畫 |
| **節點包含哪些台詞** | `story_node_lines` | 本計畫 |
| 縮圖檔案 | MinIO `story/<game>/thumbs/<雜湊>.jpg` | 本計畫（上傳） |
| 存檔樣板 | MinIO `story/<game>/templates/<名稱>` | 本計畫 |
| 建置包原檔 | MinIO `story/<game>/builds/<build_id>/`（壓縮） | 本計畫 |
| 引擎程式（存檔寫入器） | repo `backend/app/infrastructure/<engine>/` | 本計畫 |
| 主題 | repo `frontend/public/story-themes/<id>/`；之後也可以由網址載入主題 manifest | 本計畫 |

**為什麼流程圖只存結構**：台詞本身不會因為流程圖重建而改變，放在語料庫由所有功能共用（建卡、搜尋、流程圖）。流程圖每個版本只需要記「哪個節點有哪些台詞、依什麼順序」，每個版本約 5.3 萬列的小整數，約 3 MB；初稿把台詞文字複製進每個版本，約 30 MB。

**為什麼劇情圖不進 repo**（初稿原本放 `app/assets`）：放 repo 的話，每次重建都要等 CI 部署（4–12 分鐘）。改放資料庫，匯入時圖和節點成員在同一個 `build_id` 下原子切換。可審閱性改由匯入腳本的「差異摘要」提供（新增、刪除、改名的節點清單），也保留建置包原檔。

### D2 資料表

本計畫在 `fluencytides_corpus` 新增 **2 張表**。台詞相關的表見語料庫計畫書 D2。

| 表 | 一列代表 | 魔女的夜宴的列數 |
|---|---|---|
| `story_builds` | 一次匯入的流程圖版本 | 每次重建 +1，保留 1 個上線＋3 個舊版本 |
| `story_node_lines` | 某個版本中，某個節點的一句台詞 | 每個版本 53,242 列 |

#### `story_builds` —— 流程圖版本

```sql
CREATE TABLE story_builds (
  build_id        BIGINT       NOT NULL AUTO_INCREMENT,
  game_id         VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  status          ENUM('importing','ready','active','retired') NOT NULL,
  schema_version  INT          NOT NULL,
  extractor       VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  source_hash     CHAR(64)     COLLATE utf8mb4_bin NOT NULL,
  config          JSON         NOT NULL,
  graph           JSON         NOT NULL,
  stats           JSON         NOT NULL,
  note            VARCHAR(500) NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activated_at    DATETIME     NULL,
  PRIMARY KEY (build_id),
  UNIQUE KEY uk_game_hash (game_id, source_hash),
  KEY idx_game_status (game_id, status),
  CONSTRAINT fk_builds_game FOREIGN KEY (game_id) REFERENCES game_profiles (game_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 |
|---|---|
| `build_id` | 版本號，也用在前端快取的鍵與 `ETag` |
| `game_id` | 對應 `game_profiles.game_id` |
| `status` | `importing`：寫入中，API 不讀。`ready`：寫完且驗證通過。`active`：上線中（每款遊戲最多一個，由匯入腳本在交易中保證）。`retired`：被換下的舊版本，可以回滾 |
| `schema_version` | 建置包格式版本，目前是 1；後端遇到不認得的版本就拒絕匯入 |
| `extractor` | 產生這份資料的擷取器與版本，例 `sabbat_of_the_witch@1` |
| `source_hash` | 建置包內容的 SHA-256；同一份資料重複匯入時直接略過 |
| `config` | 流程圖設定（D3）。跟著版本走：回滾時存檔欄位、卡片類型、主題也一起回到舊版本 |
| `graph` | 劇情圖（D4），約百 KB |
| `stats` | 節點數、台詞數、各分頁統計；總覽與差異摘要用 |
| `note` | 匯入時的備註 |
| `created_at`、`activated_at` | 匯入時間、上線時間 |

> 初稿的 `story_games` 取消：遊戲本身改由語料庫的 `game_profiles` 描述；「目前上線的版本」用 `status='active'` 查詢即可，不另外存指標，也就不會有兩邊不一致的問題。

#### `story_node_lines` —— 節點的台詞成員

```sql
CREATE TABLE story_node_lines (
  build_id  BIGINT          NOT NULL,
  node_key  VARCHAR(191)    COLLATE utf8mb4_bin NOT NULL,
  seq       INT             NOT NULL,
  line_id   BIGINT UNSIGNED NOT NULL,
  PRIMARY KEY (build_id, node_key, seq),
  UNIQUE KEY uk_build_line (build_id, line_id),
  CONSTRAINT fk_nodelines_build FOREIGN KEY (build_id) REFERENCES story_builds (build_id) ON DELETE CASCADE,
  CONSTRAINT fk_nodelines_line  FOREIGN KEY (line_id)  REFERENCES game_lines (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 |
|---|---|
| `build_id` | 屬於哪個版本；刪掉版本時跟著刪 |
| `node_key` | 所屬節點，對應 `graph.nodes[].key` |
| `seq` | 節點內的順序，從 0 起算 |
| `line_id` | 對應 `game_lines.id`（= 舊 `scripts.id`，永遠不重新編號）；一句台詞在一個版本中只屬於一個節點 |

**索引對應的查詢**

| 查詢 | 做法 |
|---|---|
| 抽屜：某個節點的台詞，依 `seq` 分頁 | 主鍵範圍查詢，join `game_lines`（BGM 曲名再 join `game_line_bgm`、`game_bgm_tracks`） |
| 網址的 `line=`、存檔 API、卡片反查：這句在哪個節點 | `uk_build_line` |
| 卡片統計：依 `node_key` 分組計數 | `story_node_lines` join `rel_links`（`dst_type='script_line'`、`dst_id = line_id`，用 `idx_dst`），依 `attrs.card_type` 分組 |

**刻意不建的表**
- 節點表、連線表：前端一律整份讀取劇情圖，存成 `graph` JSON 就夠了；後端要用時在記憶體裡建索引（每個版本只做一次）。
- 卡片對應表：直接用通用的 `rel_links`。
- 使用者狀態表（主題、捲動位置）：放瀏覽器。

**版本生命週期**（匯入腳本負責）：
1. 新增一筆 `story_builds`，狀態 `importing`，寫入所有 `story_node_lines`。
2. 驗證：
   - 列數和建置包一致。
   - 每個 `node_key` 都在 `graph` 裡。
   - 每個 `line_id` 都屬於這款遊戲。
   - 這款遊戲 `status='active'` 的台詞全部都有歸屬（沒歸屬的列在差異摘要）。

   通過後改 `ready`，失敗就刪掉這筆。
3. 在一個交易中：舊的 `active` 改 `retired`，新的改 `active` 並填 `activated_at`。
4. 回滾（`--rollback <build_id>`）只是對某個 `retired` 版本重做第 3 步。

### D3 遊戲設定（`story_builds.config`）

```json
{
  "languages": [{"code": "ja-JP", "label": "日本語"}, {"code": "zh-Hant", "label": "繁中"}],
  "default_theme": "sabbat-night",
  "line_attrs": [{"key": "is_h", "label": "H", "kind": "flag"}],
  "annotations": {
    "cards": {"card_types": [{"card_type": "jp_core_verb", "label": "核心動詞"}, {"card_type": "jp_verb_pair", "label": "自他動詞"}]}
  },
  "saves": {
    "template": "story/SabbatOfTheWitch/templates/quick.jpg",
    "slots": [{"id": "quick", "label": "快速存檔", "filename": "data_quick_01.jpg"},
              {"id": "1", "label": "第 1 格", "filename": "data_0001_01.jpg"}],
    "install_hint": "放到 SabbatOfTheWitch\\savedata\\，遊戲中按 Q.Load"
  },
  "route_presets": {"nene": {"nen_flag": 4}, "meguru": {"meg_flag": 4}, "tsumugi": {"tsu_flag": 2}, "touko": {"tou_flag": 3}}
}
```

BGM 不在 `line_attrs`：它是台詞列的正式欄位（D12 的 `bgm`）。設定另有 `bgm_audio`（預設 `false`，上面省略），表示這款遊戲有沒有提供 BGM 音檔，見 §12「BGM 預留」。

設定由擷取器寫在建置包的 `manifest.json`，匯入時存進該版本的 `story_builds.config`。驗證規則屬於後端 schema，但每款遊戲各自有一份 pydantic 模型。

### D4 劇情圖格式（`graph`，schema v1）

```json
{
  "schema_version": 1,
  "tabs": [
    {"key": "common", "title": "共通", "order": 1, "start": "sw:001", "avatar": null},
    {"key": "nene", "title": "寧々", "order": 2, "start": "sw:101", "avatar": "nene"}
  ],
  "nodes": [
    {"key": "sw:009", "tab": "common", "type": "choice", "title": "休日デート",
     "chapter": "3", "chapter_label": "CHAPTER 3", "line_count": 140,
     "meta": {"scenes": ["009.共通－休日デート.ks*chapter32"]}},
    {"key": "sw:009_01A", "tab": "common", "type": "option", "title": "……悩む", "line_count": 86},
    {"key": "sw:019#cond", "tab": "common", "type": "condition", "title": null,
     "condition": "依好感旗標決定路線", "line_count": 0}
  ],
  "edges": [
    {"from": "sw:009", "to": "sw:009_01A", "kind": "branch", "order": 0, "label": "……悩む",
     "effects": [{"var": "sel_flag", "op": "set", "value": 0}]},
    {"from": "sw:018#cond", "to": "sw:route:wakana", "kind": "branch", "order": 1,
     "label": "任一路線通關後", "flags": ["inferred", "unlock"]}
  ]
}
```

- **節點類型**（核心詞彙，前端各有預設外觀）：`scene`、`choice`（結尾出現選項）、`option`、`merge`、`condition`、`ending`、`route_exit`。另外允許 `x-<名稱>` 的自訂類型，前端找不到渲染器時退回 `scene` 的外觀。
- **邊種類**：`normal`、`branch`（從分歧點出去，`order` 是選項順序）、`merge`、`cross_tab`（指向別的分頁）、`back`（往回跳的迴圈，不參與分層，見 D8）。
- **邊旗標**：`inferred`（推測，畫虛線）、`unlock`（通關後，畫虛線＋鎖頭圖示）。
- **`effects`**：選這條邊會怎麼改變數。後端把起點到節點的路徑上所有 `effects` 累加起來，就是存檔的變數（D13）。格式通用，`op` 有 `set`、`add`。
- `meta` 是擷取器的自由欄位，前端不讀，只在除錯面板顯示。

### D5 魔女的夜宴擷取器的規則（遊戲專屬）

**粒度**：一個場景檔裡沒有分歧、依序串接的標籤，合成一個節點。例：`018` 的 `chapter43 → 44 → dummy1 → 45 → 46` 合成一個節點「パーティー準備」。全作約 300 個節點。

**類型判定**（依序，先符合者優先）

| 類型 | 判定 | 標題 |
|---|---|---|
| `condition` | `*dummyN` 跳轉點、019 結尾的路線分歧、018 → 500（和奏）、109_β → 110（RESTART） | 不顯示；`condition` 欄寫條件文字，例「wak_flag ≥ 1」「任一路線通關後」 |
| `choice` | 節點結尾接 `*dummyselectN` | 場景檔標題 |
| `option` | 選項直接通往的分支標籤；選項直接跳到合流標籤時，補一個 0 句的虛擬節點 | 選項文字 |
| `merge` | 有兩條以上入邊的分支後標籤（多半是 `*xxx_com`） | 場景檔標題＋「（續）」 |
| `ending` | 沒有出邊，而且不是路線最後的 Epilogue／AFTER（目前只有 019 的 `normal_end`） | 「ノーマルエンド」 |
| `route_exit` | 共通分頁指向角色路線的出口 | 角色名 |
| `scene` | 其他 | 場景檔標題；同一個檔拆成多個節點時加 ①②③ |

**有台詞的條件分支**：例如 `009` 的 `dummy1` 有 63 句，是和奏的段落，只有 `wak_flag=1` 才走得到。這種情況畫成菱形加一個 `scene` 節點，不把台詞放進菱形。

**分頁**

| 分頁 | 內容 |
|---|---|
| 共通 | 001–020；019 的菱形分出 4 個「パーティー○○」與ノーマルエンド，各接一張 `route_exit` |
| 寧々 | 101–109_β → 菱形「通關後 RESTART」（`unlock`）→ 110–116（章節標成「RESTART · CHAPTER N」）→ After Story 117 |
| めぐる、紬、憧子 | 各路線 → After Story（`unlock`） |
| 和奏 | 500–507 → After Story 508；入口是共通 018 後面的菱形（`inferred` + `unlock`） |

**章節**：台詞的 `chapter　X-Y` 取 X；`Epilogue`、`After Story` 照原文；`408` 這種空白的沿用前一個。

**`node_key` 規則**：`sw:<檔號>`，同一個檔拆開時用 `sw:<檔號>_<標籤>`，虛擬節點用 `sw:<檔號>#opt<n>`。只用檔號和標籤，**不用流水號**，重建時才不會變（D6）。

**資料來源**：擷取器**只讀 `fluencytides_corpus`**，不再讀本機的校準檔案或 point 表：
- 台詞：`game_lines` 依 `story_order` 排序；`game_scenes`、`game_script_files` 提供場景標籤、檔名與標題；`point` 在 `game_lines`。
- 結構：`game_scene_links` 提供自然接續（`next`）、選項（`choice`，含選項文字與 `effects`）、依變數跳轉（`jump`，含 `condition_vars`）、通關後入口（`unlock`）。D5 的類型判定直接依 `kind` 與入邊數決定。
- 缺漏資料（6 句被刪除的台詞、46 句選項、9516 錯字）由語料庫計畫書 D5 修復；1043 是背景語音（`kind=bgv`），見 D12。

### D6 跨版本穩定的鍵

網址、書籤、卡片反查都只用**穩定的鍵**：

- `game_id`。
- `tab.key`。
- `node_key`：由擷取器依遊戲內的識別（檔名、標籤）產生。
- `line_id`：語料庫的 `game_lines.id`。語料庫保證不重新編號（沿用舊 `scripts.id`，台詞只停用、不刪除），所以可以直接當作穩定的鍵；卡片紀錄的 `line_id` 也是同一個值。

`build_id` 只在同一版本內使用。重建後某個 `node_key` 消失時：
- 匯入腳本的差異摘要會列出來。
- 前端打開舊網址時退回該分頁的起點，並提示「這個節點在新版本中已移除」。

---

## 5. 後端如何給前端資料

### D11 API（全部在 `/api/v1/story`，受 `verify_api_key` 保護）

| 方法與路徑 | 回傳 | 快取 |
|---|---|---|
| `GET /games` | 遊戲清單：`game_id`、`title`、`capabilities` | 短 |
| `GET /games/{game}` | manifest：`titles`（`{"ja-JP": …, "zh-Hant": …}`）、`build_id`、`capabilities`、`languages`、`line_attrs`、`annotation_kinds`、`save_slots`、`default_theme` | 短；`build_id` 改變就代表資料更新 |
| `GET /games/{game}/graph?build=` | D4 的整份劇情圖 | `build` 不變就不會變：`ETag = build_id`，前端 `staleTime: Infinity` |
| `GET /games/{game}/annotations?kinds=cards` | 各節點的註記統計 `{node_key: {cards: {jp_core_verb: 3, jp_verb_pair: 1}}}`，以及搜尋索引 `{cards: {verbs: {"見る": ["sw:009", …]}}}` | 後端記憶體快取 60 秒；建卡、刪卡後最慢 60 秒反映 |
| `GET /games/{game}/nodes/{node_key}/lines?offset=&limit=` | 台詞列（D12），可分頁 | 同 `build_id` 不變；只有註記部分會變 |
| `GET /games/{game}/locate?note_id=` 或 `?line_id=` | `{tab, node_key, line_id, seq}` | 不快取 |
| `POST /games/{game}/saves` | 存檔檔案（D13） | — |
| `GET /games/{game}/thumbs/{hash}` | 302 導向 MinIO 預簽名網址（實作改成後端代讀，見 §12） | 瀏覽器快取 |
| `GET /games/{game}/voices/{voice}` | 台詞語音 MP3（後端代讀，§12） | 一年 `immutable` |
| `GET /games/{game}/bgm/{code}` | BGM 音檔（**預留**，`bgm_audio` 關閉時一律 404，§12） | 一年 `immutable` |

**`capabilities`** 讓前端依遊戲決定要顯示哪些功能，而不是寫死遊戲名稱：

```json
{"saves": true, "thumbs": true, "voices": true, "bgm": false, "annotations": ["cards"], "route_presets": true, "languages": ["ja-JP", "zh-Hant"]}
```

`voices`、`thumbs` 在有素材來源（MinIO 或 `STORY_ASSETS_DIR`）時為 true；`bgm` 還要遊戲設定的 `bgm_audio` 打開才是 true（目前一律 false）。

**為什麼把圖、註記、台詞拆成三個端點**：三者變動頻率不同。圖只在重建時變；註記每次建卡都會變；台詞量大，要依節點分頁讀取。拆開後，圖可以永久快取，建卡後只要重抓很小的註記。

### D12 台詞列的格式

```json
{
  "node_key": "sw:009", "total": 140, "offset": 0,
  "lines": [
    {"line_id": 9021, "anchor_key": "009.共通－休日デート.ks*chapter32@12", "seq": 3,
     "speaker": "めぐる", "texts": {"ja-JP": "「……」", "zh-Hant": "「……」"},
     "voice": "meg009_001", "thumb": "001d81975a2c91f1", "attrs": {"is_h": false},
     "bgm": [{"code": "BGM02", "ord": 0, "titles": {"ja-JP": "…", "zh-Hant": "…"}, "gallery_no": 4},
             {"code": "BGM51", "ord": 1, "titles": {}, "gallery_no": null}],
     "bgm_detect": "實測", "bgm_may_change": true,
     "annotations": {"cards": [{"card_type": "jp_core_verb", "label": "核心動詞", "verb": "見る",
                                "note_ids": [1717000000001, 1717000000002]}]}}
  ]
}
```

- 每句帶 `kind`，抽屜依類型顯示：
  - `line`：一般顯示。
  - `choice`：顯示成選項清單，每個選項標出它通往哪個節點。
  - `bgv`：背景語音，以較淡的樣式顯示在它播放位置那句的下方，標「背景語音」。沒有 `point`，所以不提供下載存檔；需要的話，改成下載它播放位置（`attrs.plays_at`）那句的存檔。
- `texts` 以語言代碼（BCP 47）為鍵，由後端把資料庫的 `text_<語言>` 欄位轉過來（`ja-JP` ↔ `text_ja_jp`）；前端不需要知道欄位名。
- **BGM 是正式欄位**（不放 `attrs`）：`bgm` 是這句播放的曲子，依 `ord` 排列（`game_line_bgm` join `game_bgm_tracks`，整頁一次查詢）；播放中切換曲子的台詞有兩首，沒有 BGM 時是空陣列。`titles` 和 `texts` 一樣以語言代碼為鍵、沒有曲名的語言省略（標題畫面的 BGM51 兩種都沒有，前端退回顯示 `code`）；`gallery_no` 是 EXTRA 音樂鑑賞的編號。`bgm_detect` 是偵測方式（`A`／`實測`／`B推定`，選項畫面為 null），`bgm_may_change` 表示這句播放中 BGM 可能切換。`code` 也是 BGM 音檔的名稱（`GET …/bgm/{code}`）。
- 註記是通用結構：`annotations.<kind>` 是陣列，前端依 kind 找對應的徽章渲染器（D10）。
- **跳卡片**：後端只給 note id，**不呼叫 AnkiConnect**，讓流程圖 API 不依賴 Anki 有沒有開。前端先用既有的 `GET /cards?q=nid:<ids>` 取得 card id，再導向 `/cards?q=nid:<ids>&card=<card_id>`。Anki 沒開時，這一步才顯示錯誤。
- 句數上千的節點（例：103 有 1,542 句），用 `offset`、`limit` 分頁，前端用虛擬捲動。另外提供「下一句有卡的台詞」：`GET …/lines?annotated=cards&after=<seq>`。

### D13 存檔產生

**介面**（`services/story/saves.py`）：

```python
class SaveWriter(Protocol):
    engine: str                                    # 'kirikiri_packinone'
    def write(self, template: bytes, position: dict, variables: dict,
              meta: SaveMeta) -> bytes: ...        # meta：台詞文字、縮圖、時間
```

- 寫入器依 `game_profiles.engine` 選擇，同一引擎的遊戲共用。千戀＊萬花、魔女的夜宴等 ゆずソフト 作品，只要存檔格式相同就共用 `kirikiri_packinone`。
- 魔女的夜宴的實作，移植自 `krkr_savepack.py` 與 `calib_run.py` 的 `write_quick`：
  1. 解碼樣板。
  2. 設 `core.scenePlayer = position`、`user = variables`、`core.bookMarkInfo.lastText = 台詞`。
  3. 把縮圖換成該句的縮圖。
  4. 依原檔頭編碼。

**`POST /games/{game}/saves`** 的 body：

```json
{"line_id": 1043, "slot": "quick", "route_preset": null}
```

- 變數由後端算：沿著 `graph` 從分頁起點走到該節點的路徑，累加每條邊的 `effects`；有 `route_preset` 時再疊上 `config.route_presets`。前端只送鍵，不送變數。
- 檔名取自 `config.saves.slots[].filename`，放在 `Content-Disposition`。
- 前端用 API client 帶金鑰取得 blob 再觸發下載，不能用單純的 `<a href>`。

### D14 後端程式結構

```
app/api/story.py                    通用端點，只做請求與回應
app/schemas/story.py                DTO 與建置包 schema（pydantic，匯入腳本也用同一份）
app/services/story/
    registry.py                     @register_story_game、依 game_id 取適配器（比照 task_handlers/registry.py）
    base.py                         StoryGame 基底：預設行為（讀設定、算路徑變數）
    repository.py                   story_builds / story_node_lines 的查詢，以及 join 語料庫表
    graph_service.py                manifest、graph、locate、路徑與變數累加
    annotations/
        base.py                     AnnotationProvider 介面：統計、搜尋索引、每句註記
        cards.py                    讀 rel_links（core.example_from、core.teaches）
    saves.py                        SaveWriter 介面與依 engine 的註冊
    games/
        sabbat_of_the_witch.py      魔女的夜宴的適配器（只放和預設不同的部分）
app/infrastructure/krkr/packinone.py  KiriKiri PackinOne 編解碼（移植）
```

**遊戲適配器**的預設行為都由 `base.py` 提供；遊戲專屬的只有不同的部分。例如：路線旗標要另外驗證，或某款遊戲的錨點要特別解析。

**註記提供者**和遊戲無關。目前只有 `cards`，以後可以加，例如「使用者筆記」或「Elasticsearch 詞彙命中」，前端以同一個 `annotations` 結構接收。

---

## 6. 前端

### D7 模組邊界

> 2026-11-01：採用通用關係圖（`relation_graph_FEAT_draft`）後，本模組的位置改為 `frontend/src/features/graph-views/engines/story-flowchart/`，主題系統提到 `graph-views/core/` 給所有排版引擎共用；下文的 `features/story-flowchart/` 一律指這個新位置。

```
src/features/story-flowchart/        可共用模組（不依賴網站）
    index.ts                         唯一公開入口：元件、型別、主題工具
    core/                            純 TS，不依賴 React
        types.ts                     Graph、Node、Edge、Tab 型別（對應 schema v1）
        layout.ts                    排版（D8）
        path.ts                      起點到節點的路徑
        theme.ts                     StoryTheme 型別、預設值、轉成 CSS 變數
    render/                          React 元件
        StoryFlowchart.tsx           主元件
        NodeLayer.tsx、EdgeLayer.tsx、Minimap.tsx、ScrollControls.tsx、TabBar.tsx、Overview.tsx
        nodeRenderers.tsx            預設節點渲染器，以類型為鍵
    fixtures/                        測試與預覽用的劇情圖（魔女的夜宴片段、千戀＊萬花 CH3 片段）
src/pages/StoryGraph.tsx             網站整合：取資料、網址狀態、抽屜、卡片、存檔
src/components/story/StoryLineDrawer.tsx
src/hooks/useStoryGame.ts、useStoryGraph.ts、useStoryLines.ts
src/lib/storyParams.ts
```

- **規則**：`features/story-flowchart/` 內不得 import `@/api`、`@/pages`、`@/hooks`、`react-router-dom`。用 ESLint `no-restricted-imports` 對該資料夾強制，確保模組可以整包搬走。
- **公開 API**：

```tsx
<StoryFlowchart
  graph={graph}                      // schema v1
  tab="common" onTabChange={…}
  theme={theme}                      // StoryTheme
  selectedNode="sw:009" onNodeClick={…} onNodeHover={…}
  nodeState={(node) => 'normal' | 'dim' | 'highlight'}   // 外部決定狀態（D10）
  pathTo="sw:009_01A"                // 要畫成「選取路徑」的終點
  decorations={(node) => <Badge…/>}  // 徽章等疊加物
  renderers={{ condition: MyDiamond }}  // 覆寫某類節點的外觀
  layoutOptions={{ /* 覆寫主題尺寸，例如窄版 */ }}
/>
```

模組本身不知道「卡片」「存檔」是什麼；網站整合層把卡片統計轉成 `nodeState` 和 `decorations` 傳進去。

### D8 排版引擎（`core/layout.ts`）

分兩步，兩步都是純函式，可以單元測試：

1. **邏輯格位**：`assign(graph, tab) → { rank, lane }`，以及章節帶要插在哪一層。
   - 最長路徑分層；合流節點的層 = 所有入邊分支中最長的那條 + 1。
   - `back` 邊不參與分層（避免迴圈）。畫成從節點側邊出去、標註目標的短箭頭，點了捲到目標節點。
   - 有 k 個出邊的分歧點，第 i 個子節點的欄位偏移是 `(i − (k−1)/2)`，單位是 1 欄；k 不設上限（019 是 4）。
   - 巢狀分歧以所在欄為中心展開；碰撞時把整組往外推，並標記這組改用窄版節點。
   - 合流節點回到分歧點所在的欄。
2. **幾何投影**：`project(grid, theme.size, orientation) → { nodes: [{key,x,y,w,h}], edges: [{d, kind}], bands: [{y,label}], width, height }`。
   - 直向：x = 欄 × `colGap / 2` 的倍數，y = 層 × `rowGap`，加上章節帶的累計高度。
   - 連線路徑：
     - 同欄直線。
     - 分歧邊從節點側邊出發、圓角轉向下。
     - 合流用 S 形三次貝茲曲線，可以跨好幾層。
     - 菱形從左、右、下三個頂點出發。
   - 橫向只要多寫一個投影函式，格位那一步不必改。

**效能**：每個分頁的排版結果以 `(build_id, tab, size)` memo。節點超過 500 個時，節點層只渲染可視範圍上下各一屏的節點；連線層同樣依可視範圍裁切。魔女的夜宴每頁只有幾十個節點，第一版就把這個門檻做進去，以後接大型遊戲不用重寫。

### D9 渲染：節點用 HTML、連線用 SVG

| 圖層 | 做法 | 客製化程度 |
|---|---|---|
| 連線層 | 一張 `<svg>`，畫線、箭頭、S 曲線 | 顏色、粗細、虛線、描邊都用 CSS 變數；箭頭可以換成主題的 `<marker>` 圖 |
| 節點層 | `<div>` 依排版結果絕對定位 | CSS 能做的都能用：背景圖、`border-image`、漸層、陰影、字型、hover 與選取動畫。票券缺口、菱形用 `mask` 或 `clip-path`，遮罩圖也可以由主題替換 |
| 其他介面 | 分頁、捲動條、小地圖、總覽都是一般元件 | 有素材圖就用圖，沒有就退回 CSS |

**兩層座標一致**由 `project` 統一產生，兩層共用同一個座標原點；有測試確保節點框的頂部中點就是入邊的終點。

**每個元素都有穩定的掛勾**，讓主題可以只用 CSS 改樣式：

```html
<div class="sf-node sf-node--choice" data-type="choice" data-state="highlight" data-selected="true" data-on-path="true">
  <span class="sf-node__title">休日デート</span>
</div>
<!-- 疊加物（徽章）在所有節點之後的獨立圖層，對齊節點右上角，不受節點框的 mask／filter／opacity 影響 -->
<div class="sf-decorations">
  <span class="sf-node__decorations" data-key="sw:009" data-state="highlight">…</span>
</div>
```

### D10 狀態與疊加

遊戲的節點狀態（未到達、灰色、粉紅、黃色）依玩家進度決定。模組不假設有玩家進度，改由外部提供：

| 模組提供的狀態 | 魔女的夜宴頁的用法 |
|---|---|
| `nodeState = 'dim'` | 沒有符合目前篩選的卡 |
| `nodeState = 'highlight'` | 搜尋命中 |
| `selectedNode` | 抽屜打開的節點 |
| `pathTo` | 從分頁起點到選取節點的路徑（黃色路徑＋粉紅分歧邊）；也是存檔變數所走的路徑 |
| `decorations` | 卡片數徽章；`unlock`、`inferred` 的提示圖示由模組自己畫 |

以後要做「玩家進度」（例如上傳全域存檔、標記已讀），只是另一種 `nodeState` 的來源，模組不用改。

### D15 主題系統

```ts
interface StoryTheme {
  id: string
  extends?: string                 // 繼承另一個主題，只覆寫差異
  colors: Record<string, string>   // 轉成 --sf-color-<key>，鍵名見預設主題
  size: { nodeW: number; nodeWNarrow: number; nodeH: number; rowGap: number;
          colGap: number; bandHeight: number; edgeWidth: number; arrowSize: number }
  fonts: { node: string; band: string; title: string }
  assets: {                        // 全部選填，相對於主題資料夾或絕對網址
    background?: string; backgroundOverlay?: string
    nodeTexture?: string; nodeMasks?: Partial<Record<NodeType, string>>
    chapterIcon?: string; tabButton?: string; tabAvatars?: Record<string, string>
    scrollThumb?: string; scrollButtons?: Partial<Record<'top'|'up'|'down'|'bottom', string>>
    arrow?: string; titleBadge?: string
  }
  css?: string                     // 主題附加的 CSS 檔，可以針對 .sf-* 任意覆寫
}
```

- **主題來源**：內建主題在 `public/story-themes/<id>/theme.json`。格式就是上面的 JSON，所以之後也可以從 MinIO 或任意網址載入，不用部署。
- **選用順序**：使用者在頁面上選的（localStorage）→ 遊戲 manifest 的 `default_theme` → 平台預設。localStorage 失敗時直接用後兩者。
- **兩個內建主題**：
  - `default`：中性灰階，用來驗證「沒有任何素材時」也能正常顯示。
  - `sabbat-night`：魔女的夜宴用，夜色深紫底、銀月色節點與金色選取路徑，和千戀＊萬花的和風紅白區隔。具體色值在 P2 定稿。
- **預覽頁**：開發模式下有 `/story/_playground`，用 `fixtures/` 的劇情圖套任意主題，方便做主題時不必連後端。

### D16 網址狀態（`lib/storyParams.ts`，比照 `cardBrowse.ts`）

`/story/:game?tab=&node=&line=&kind=&verb=`

| 參數 | 意義 |
|---|---|
| `tab` | 分頁 `key`；省略時是總覽 |
| `node` | 選取的 `node_key`（抽屜打開） |
| `line` | 抽屜中選取的 `line_id` |
| `kind` | 註記篩選（例：`jp_core_verb`） |
| `verb` | 動詞搜尋 |

- 選節點會新增一筆歷史，所以上一頁可以關閉抽屜。
- 篩選與搜尋用取代，不新增歷史。
- 捲動位置每個分頁各自記在 sessionStorage；網址有 `node` 時一律捲到那個節點。

### D17 卡片頁反查

- `CardViewPanel` 的筆記類型屬於某個遊戲 `annotations.cards.card_types` 時，顯示「在流程圖中顯示」。
- 點下去呼叫 `GET /story/games/{game}/locate?note_id=`，再導向 `/story/{game}?tab=&node=&line=`。
- 筆記類型和遊戲的對應，由 `GET /story/games` 的回應提供，前端不寫死。

---

## 7. 改動清單

### 建置（離線）

| 檔案 | 改動 |
|---|---|
| `backend/scripts/story/common/` | 新增：建置包的寫出與驗證（共用 `app/schemas/story.py`）、節點合併工具、標題覆寫、Mermaid 匯出 |
| `backend/scripts/story/import_story_build.py` | 新增：通用匯入（驗證 → 差異摘要 → 寫入 `importing` → `ready` → 原子切換 `active`；`--rollback <build_id>`；上傳縮圖與樣板到 MinIO；保存建置包原檔） |
| `backend/scripts/story/extractors/sabbat_of_the_witch.py` | 新增：從 `fluencytides_corpus` 讀台詞、場景與 `game_scene_links`，套用遊戲設定（分頁、路線、標題覆寫），產生建置包（D5） |
| `backend/scripts/story/extractors/sabbat_of_the_witch_titles.json` | 新增：標題覆寫 |
| `sql/` | 新增兩張表的建表 SQL |

重建與匯入魔女的夜宴：

```bash
python backend/scripts/story/extractors/sabbat_of_the_witch.py --out build/sabbat_of_the_witch --template "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/quick_template.jpg" --thumbs "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/thumbs" --voices "C:/Users/forip/Desktop/WorkSpace/material/voice/yuzusoft/SabbatOfTheWitch"
```

```bash
python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --dry-run
```

```bash
python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch
```

縮圖、語音、BGM 的來源資料夾不算進 `source_hash`。內容沒變、只是補上 `--voices`（或 `--bgm`）的建置包重跑匯入會被略過，改用 `--assets-only`：不建新版本，只替上線版本上傳（補齊）樣板、縮圖、語音、BGM；建置包必須和上線版本的 `source_hash` 相同，已存在的檔案跳過。本機資料夾（後端以 `STORY_ASSETS_DIR` 指向它）：

```bash
python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --assets-only --assets local:<資料夾>
```

### Backend

依 D14 的結構新增。另外在 `app/main.py` 註冊 `story_router`（`/api/v1`），並在 `app/infrastructure/database/` 加兩張表的 SQLModel，以及讀取語料庫表的查詢（語料庫引擎）。

### Frontend

依 D7 的結構新增。另外：
- `src/App.tsx`：新增路由 `/story/:game` 與 `/story`（遊戲清單，只有一款時直接導向）。
- 側邊欄新增入口。
- `src/components/cards/CardViewPanel.tsx`：新增「在流程圖中顯示」。
- `src/types/api.ts`：新增 story 的 API 型別。
- ESLint 設定：新增 `features/story-flowchart/` 的 import 限制。

### 測試

- **backend**
  - PackinOne 往返，以及改寫 scene、point、`user` 後的往返。
  - 路徑與 `effects` 累加：以 009 的四個選項、019 的兩個選項為案例。
  - 匯入：驗證失敗不切換版本、原子切換、回滾、重複匯入略過。
  - 註記統計與 `locate` 的 SQL。
  - 建置包 schema 檢查：
    - 每條邊的兩端都存在。
    - 除了 `back` 邊以外，每個分頁都是 DAG。
    - 每個 `option` 的入邊都來自 `choice` 或 `condition`。
    - `node_key` 唯一、每個 `line_id` 只屬於一個節點。
  - 存檔端點回傳的檔名與 MIME。
- **frontend**
  - `layout`：2、3、4 個選項的欄位偏移；巢狀分歧不重疊；合流的層數；章節帶插入位置；`back` 邊不影響分層；投影後節點頂部中點等於入邊終點。
  - 用兩份 fixture（魔女的夜宴片段、千戀＊萬花 CH3 片段）跑同一套排版測試，證明不依賴特定遊戲。
  - `storyParams` 的解析與預設值。
  - 主題：繼承、缺素材時的退回、CSS 變數輸出。
- **實機**：見 §11。

---

## 8. 新增一款遊戲要做什麼

1. 台詞先匯入語料庫：`game_profiles` 一列、`game_script_files`、`game_scenes`、`game_lines`；有校準資料的話 `point` 才有值（沒有 `point` 就不能產生存檔）。這一步屬於語料庫，建卡、搜尋也會用到。
2. 寫擷取器，產生建置包：
   - `graph.json`（schema v1）。
   - `node_lines.jsonl`：每個節點包含哪些 `line_id`、依什麼順序。
   - `manifest.json`：遊戲設定。
   - 素材：存檔樣板，以及 `game_lines.thumb` 指到的縮圖檔（匯入腳本上傳到 MinIO，已存在就跳過）。

   擷取方式不限：校準、解包、手工整理都可以。
3. 存檔：
   - 引擎已經有寫入器：只要在設定填樣板與欄位檔名。
   - 新引擎：在 `infrastructure/<engine>/` 加寫入器並註冊。
   - 不需要存檔時，`capabilities.saves = false`，前端自動隱藏下載按鈕。
4. 和預設行為不同的地方（例如特殊的路線旗標），在 `services/story/games/<game_id>.py` 寫適配器；沒有就不用寫。
5. 選配：在 `public/story-themes/<id>/` 加主題，並在設定指定 `default_theme`。
6. 執行 `import_story_build.py`。不需要改前端程式，也不需要部署（除非有第 3、4、5 步的程式碼）。

---

## 9. 實作順序

| 階段 | 內容 | 為何先做 |
|---|---|---|
| **P0 資料與存檔驗證** | ① 語料庫計畫完成 M1–M3（新資料庫、修復、切換）② 繁中輪結束後實機驗證 R1 ③ 確認欄位存檔的檔名、`lastText` 與時間欄位 | 台詞、錨點、卡片對應都來自新資料庫；存檔能不能用決定 D13 的範圍 |
| **P1 平台資料層** | 建置包 schema、兩張表、通用匯入（含原子切換與回滾）、魔女的夜宴擷取器、Mermaid 核對 | 格式是共用與專屬之間的契約，最先定 |
| **P2 流程圖模組** | `features/story-flowchart/` 的排版、渲染、小地圖、捲動、分頁、總覽；兩份 fixture；`default` 主題；`/story/_playground` | 不依賴後端，可以先用 fixture 對照遊戲截圖驗收版型 |
| **P3 網站整合與卡片** | 後端 manifest、graph、annotations、lines、locate；`/story/:game` 頁、抽屜、篩選與搜尋、跳卡片、卡片頁反查 | 使用者最常用的功能 |
| **P4 存檔** | 移植 PackinOne、`SaveWriter`、存檔端點、下載按鈕、目標路線選單 | 依賴 P0 的驗證結果 |
| **P5 主題** | `sabbat-night` 主題與素材、主題選單、主題繼承與遠端載入 | 確認 D15 真的不用改元件 |

---

## 10. 風險與未知

- **R1 特殊位置的合成存檔可能讀不了**：和奏（500–508）、寧々 RESTART（110–116）、AFTER 都要在通關後才會出現在選單上。直接讀欄位存檔時，遊戲有沒有檢查全域通關紀錄，還沒驗證過。校準時這些位置都是在已經通關的全域狀態下探測的。
  - 應對：P0 用另一份全新的遊戲資料夾（沒有通關紀錄）測試。
  - 讀不了時，在節點的 `meta` 標註，抽屜註明「需先通關任一路線」或「需先通關寧々」。不提供修改全域存檔的功能。
- **R2 共通路線的路線旗標預設**：用的是實測可以進路線的旗標組，但不保證之後再選其他選項時，結果還是同一條路線。
  - 應對：只當作輔助，tooltip 說明「之後的選項仍會影響路線」。
- **R3 和奏入口的條件不明**：018 → 500 目前是推測。
  - 應對：標 `inferred`；之後驗證出實際條件，再更新擷取器。
- **R4 通用化過度**：為了尚未出現的遊戲做太多抽象。
  - 應對：抽象只做到「魔女的夜宴已經需要」加「千戀＊萬花 fixture 已經證明需要」的程度。D4 的 `x-<名稱>` 自訂類型、D8 的橫向投影、D15 的遠端主題只定介面、不實作。
- **R5 擷取器規則的邊界情況**：同檔多個主標籤之間有條件跳轉，或跨檔的分支。
  - 應對：P1 輸出 Mermaid 版，逐檔對照 `SW_flowchart.md`；不合理的地方用標題覆寫或擷取器規則處理。
- **R6 註記統計的效能**：魔女的夜宴是 53k 列 `story_node_lines` join 約 6k 筆紀錄，有索引就不是問題，而且有 60 秒快取。遊戲多了以後，改成在建卡、刪卡時主動讓快取失效。
- **R7 MySQL 的 JSON 欄位**：`graph` 約百 KB、`config` 更小；都只整筆讀寫，不用 JSON 函式查詢，MySQL 5.7 的 JSON 型別就夠用。
- **R8 正式機**：兩張表在 `fluencytides_corpus`、素材在 MinIO，正式機都已經能存取。匯入腳本在本機對正式資料庫執行，要確認連線設定（`.env`）指向正確的資料庫。

---

## 11. 驗收標準

- [ ] P0：語料庫計畫的驗收全部通過；`rel_links` 中有效的 `core.example_from`，`dst_id` 不在 `story_node_lines`（上線版本）的筆數為 0，或逐筆列出原因。
- [ ] P0：產生的快速存檔放進 `savedata`、按 Q.Load 後，遊戲停在那一句；抽查 共通、各路線、選項分支內、H 場景各 1 句。
- [ ] P0：R1 的三種特殊位置在無通關紀錄的資料夾中測試過，結果記在本文件。
- [ ] P1：建置包通過 schema 檢查；`--dry-run` 輸出差異摘要；匯入、切換、回滾都實際操作過；Mermaid 版經人工核對無誤。
- [ ] P2：用千戀＊萬花 CH3 fixture 畫出的圖，和截圖 `sb_49`～`sb_51` 的欄位、分歧、合流、章節帶一致。
- [ ] P2：魔女的夜宴共通、寧々、和奏三個分頁的版面：
  - 019 的 4 個選項、路線分歧菱形、ノーマルエンド、和奏的虛線入口、寧々 RESTART 都畫出來。
  - 截圖附在 PR。
- [ ] P2：`features/story-flowchart/` 通過 import 限制檢查，不依賴網站其他部分。
- [ ] P3：
  - 搜尋一個動詞（例：`見る`），命中節點高亮，分頁按鈕顯示命中數，可以在命中節點之間切換。
  - 點台詞的卡片標籤，跳到 `/cards` 的那張卡。
  - 從那張卡按「在流程圖中顯示」，回到同一句。
- [ ] P3：重新整理、上一頁都能還原分頁、抽屜與選取的台詞；重新匯入同一份資料後，舊網址仍然有效。
- [ ] P4：在抽屜下載的檔名與格子正確；選項分支內的台詞，解碼存檔後 `user` 變數和選取路徑一致。
- [ ] P5：只改主題設定檔與素材資料夾，就能把背景、節點底紋與形狀遮罩、分頁按鈕、捲動按鈕全部換掉，元件程式碼不用動。

## 12. 實作紀錄（2026-11-01）

**已完成並驗證**
- 擷取器只讀語料庫（`game_lines`、`game_scenes`、`game_scene_links`），建置包經不變式檢查後匯入；版本 1 已上線（`story_builds.status=active`）。匯入可以重跑：`source_hash` 相同就略過。
- 後端 story API、遊戲適配器註冊表、`kirikiri_packinone` 存檔寫入器：產出的存檔和 `krkr_savepack.py` 位元組完全相同（約 25 ms）。
- 前端模組 `features/graph-views/engines/story-flowchart/`、`/story/:gameId` 頁、兩個主題（`default`、`sabbat-night`）、卡片頁的「在流程圖中顯示」按鈕。
- 測試：後端 story 測試 62 個、前端 vitest（排版、路徑、主題、網址參數）；`tsc`、`npm run build` 通過。實機在瀏覽器確認共通、寧々、和奏分頁、選項、路線分歧、搜尋、抽屜、路徑高亮、手機寬度。

**和設計不同的地方**
- 縮圖由後端代讀（一年 immutable 快取），不回 302：MinIO 位址在內網，瀏覽器連不到。
- repository 用原生 SQL（全是唯讀的多表 join），沒有建 SQLModel。
- 節點徽章的數字是「有卡的台詞句數」：情境卡和填空卡指向同一句，算筆記數會重複。
- 「下一句有卡」的參數改成 `annotated_after=`。
- schema 多了節點的 `target_tab`、`titles`，邊的 `condition_vars`，`card_types` 的 `note_types`（卡片頁反查不用寫死筆記類型）。
- 抽屜加了語音播放（存檔鍵左邊）：`GET /story/games/{game}/voices/{voice}` 由後端代讀 `story/<game>/voices/<voice_file>.mp3`（名稱只收 `[A-Za-z0-9_]{1,64}`，不進記憶體快取），`capabilities.voices` 有素材來源時為 true。沒有語音的台詞顯示停用的靜音喇叭圖示。匯入只上傳 `game_lines.voice_file` 用到的檔案（SW 23,493 個、約 2.0 GiB；`local:` 模式同一顆磁碟用硬連結）。正式機的語音從哪裡來還沒決定，目前只放進本機素材資料夾。
- 節點徽章改畫在節點層之後的獨立圖層 `.sf-decorations`（總覽則在節點框外）：原本畫在節點框裡，票券形節點的 `mask` 會把它裁掉，`opacity`／`filter` 也會影響它，後面才畫的相鄰節點還可能蓋住它。渲染器的 `decorations` 參數因此移除。
- 020 的四個檔案檔號相同，節點鍵改用檔名主幹（例 `sw:020.共通－パーティ寧々`）。
- 資料庫和其他功能一樣讀 `MYSQL_DATABASE`，共用 `corpus_database.py` 的引擎（整個分支和語料庫切換一起上線）。本機的 `MYSQL_DATABASE` 還沒切換時，可用 `STORY_CORPUS_DATABASE` 明確覆寫（只供本機測試，會另建一個連線池）；擷取器與匯入腳本則用 `--database fluencytides_corpus`。本機素材可用 `--assets local:<資料夾>` 與 `STORY_ASSETS_DIR`（兩個 `STORY_*` 都可以寫在 `backend/.env`）。

**BGM 預留（2026-11-01）**

BGM 音檔在遊戲的加密封包（`bgm.xp3`）裡，不解包，所以目前沒有音檔。程式先把位置留好，播放一律預設關閉：

- **台詞列**：`StoryLine` 多了正式欄位 `bgm`（曲目清單：`code`、`ord`、`titles`、`gallery_no`）、`bgm_detect`、`bgm_may_change`（D12）。原本 `attrs.bgm`／`attrs.bgm_title` 的複本拿掉，SW 擷取器設定的 `line_attrs` 也拿掉 `bgm_title`（下次擷取的 `source_hash` 會變，正常匯入成新版本即可；在那之前上線版本的設定還留著這一項，但前端不依 `line_attrs` 渲染，沒有影響）。
- **抽屜**：說話者旁顯示曲名（`♪ 曲名`，兩首時以「→」連接），跟著台詞語言：「繁」用繁中曲名、沒有時退回日文；「日」與「雙語」用日文、沒有時退回繁中；兩種都沒有顯示代號。提示框列出代號、鑑賞編號、兩種曲名、判定方式與「會切換」。規則在 `frontend/src/lib/storyBgm.ts`（有 vitest）。
- **播放鍵**：只在 `capabilities.bgm=true` 時，每首曲名變成播放鍵；目前不顯示。BGM 用另一個 `useVoicePlayer` 實例（循環、音量 0.5），**和語音可以同時播**（遊戲裡語音本來就疊在 BGM 上；同一個播放器會讓按語音時停掉 BGM），BGM 自己一次只播一首。播放中的鍵是曲目代號，同一首 BGM 的台詞都顯示播放中。換節點、關閉抽屜時停止。
- **端點**：`GET /story/games/{game}/bgm/{code}` 讀物件鍵 `story/{game}/bgm/{code}.<副檔名>`。安全規則和語音相同：`game_id` 白名單、代號只收 `[A-Za-z0-9_]{1,32}`（不帶副檔名；SW 的 50 個代號都符合）、不合或找不到一律 404、素材來源出錯 503、一年 `immutable`、不進記憶體快取。副檔名依序找 `.ogg`（`audio/ogg`）、`.mp3`（`audio/mpeg`），`Content-Type` 跟著找到的檔案：遊戲原檔多半是 Ogg Vorbis，要支援不能播 Ogg 的瀏覽器時改上傳 MP3。設定的 `bgm_audio` 沒打開時，即使檔案在也回 404。
- **`capabilities.bgm`**：遊戲設定 `bgm_audio: true` **而且**有素材來源才是 true。
- **上傳**：擷取器 `--bgm <資料夾>`（`<代號>.ogg` 或 `.mp3`）只記在 manifest，不算進 `source_hash`；匯入（含 `--assets-only`）只上傳 `game_bgm_tracks` 裡這款遊戲有的代號，同一個代號 `.ogg` 優先，目的地已有任一種副檔名就跳過，缺檔計數並警告。

之後有 BGM 音檔時：

1. 把音檔整理成 `<game_bgm_tracks.code>.ogg`（或 `.mp3`）放進一個資料夾。`game_bgm_tracks.archive_index` 是曲子在 `bgm.xp3` 裡的檔案序號，可以拿來對照。
2. 先替上線版本預先上傳（不改設定，所以 `source_hash` 不變；這時還不會播放）：

```bash
python backend/scripts/story/extractors/sabbat_of_the_witch.py --out build/sabbat_of_the_witch --template "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/quick_template.jpg" --thumbs "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/thumbs" --voices "C:/Users/forip/Desktop/WorkSpace/material/voice/yuzusoft/SabbatOfTheWitch" --bgm <BGM資料夾>
```

```bash
python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --assets-only --assets local:<資料夾>
```

3. 確認上傳結果沒有缺檔後，加上 `--enable-bgm-audio` 重新擷取（設定寫入 `bgm_audio: true`，`source_hash` 會變），再正常匯入成新版本；新版本上線後 `capabilities.bgm` 變成 true，抽屜出現 BGM 播放鍵：

```bash
python backend/scripts/story/extractors/sabbat_of_the_witch.py --out build/sabbat_of_the_witch --template "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/quick_template.jpg" --thumbs "C:/Users/forip/Desktop/WorkSpace/game/SW_calib/thumbs" --voices "C:/Users/forip/Desktop/WorkSpace/material/voice/yuzusoft/SabbatOfTheWitch" --bgm <BGM資料夾> --enable-bgm-audio
```

```bash
python backend/scripts/story/import_story_build.py build/sabbat_of_the_witch --assets local:<資料夾>
```

要關掉時回滾到前一版（`--rollback <build_id>`），或不加 `--enable-bgm-audio` 重新擷取匯入。語料庫腳本讀到的台詞也帶 BGM（`CorpusLine.bgm`，見語料庫計畫書 D9-A）。

**語料庫連接資料的問題**
- `calib_state.json` 的「下一個場景」是整個章節跑完後的位置，記在章節起點的標籤上，所以會接錯。遷移腳本已濾掉兩種：選項標籤出發的自然接續（例 019 `dummyselect1` → 015 `015_01B`）與自迴圈。
- 仍有 7 條往回跳的連接，畫成 back 邊的小圖示，待 P0 在遊戲裡確認：019 `019_01A` → `chapter47`、`105_01A` → `ne_H_4`、`113_01A` → `ne_H_7`、115 `dummy1` → `ne_H_8`、112 `dummy1` → `chapter23`、`219_01A` → `me_H_4`、`508_02A` → `508_01com`。
- 語料庫缺少的連接由 SW 擷取器的 `EXTRA_LINKS` 補上：019 結尾依好感的路線分歧、015 `chapter38` → `015_01B`、009 `009_03B` → `dummy1`，以及全部通關後入口（和奏入口、寧々 RESTART、5 條 AFTER）。之後可考慮把它們移進 `game_scene_links`（`evidence=inferred`）。

**還沒做**
- P0：在遊戲裡讀下載的存檔，確認位置與變數（含 R1 的三種特殊位置）。
- MinIO 實際上傳（程式已寫，本機 MinIO 沒開，只測過 local 模式）。
- Mermaid 匯出的人工核對、分頁頭像素材、遠端主題的頁面入口。
- 卡片頁「流程圖」按鈕只通過型別檢查，還沒實機點過（要開 Anki 與完整後端）。
