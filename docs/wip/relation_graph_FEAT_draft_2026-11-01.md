# FEAT（草稿）— 通用關係圖：讓每種卡片類型都能定義自己的關係圖

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-01 |
| **性質** | 新增機能設計（方向草稿，尚未承諾實作） |
| **狀態** | 📝 草稿（需求討論中）；R0 已決定併入語料庫重建（2026-11-01） |
| **範圍** | 語料庫 MySQL `fluencytides_corpus` 新增 `rel_entity_types`、`rel_types`、`rel_links`；網站 SQLite 的 `card_relations`、`relation_types` 遷到 MySQL；後端新增「圖提供者」註冊表；前端把 `story-flowchart` 模組放進通用的 `graph-views`，與現有知識圖譜共用資料格式 |
| **不動** | 語料庫的內容表（`game_lines`、`game_scenes`、`game_scene_links`…）；各卡片類型的生成帳本（`card_gen_log`）；網站 SQLite 的 `capability_routes` |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md`（本草稿取代其 D8 `card_links`）；`docs/wip/story_flowchart_FEAT_2026-10-31.md`（第一個具體的關係圖） |

---

## 1. 問題與動機

使用者希望**每種卡片類型都能自訂複雜的關係圖**，劇情流程圖只是其中一種，以後還會有別的圖。

**現況**有三套各自為政的關係：

| 關係 | 存放 | 端點 | 問題 |
|---|---|---|---|
| 卡片 ↔ 卡片（知識圖譜） | 網站 SQLite `card_relations`（本機 172 列，只有 `has_context`、`has_cloze` 兩種）＋ `relation_types`（4 種） | 只能是 Anki 筆記 | 不能連到台詞、詞、場景 |
| 卡片 ↔ 台詞 | 語料庫 `generated_sentences_log` | 寫死成日文動詞的三個 note 欄位 | 其他卡片類型放不進去 |
| 場景 ↔ 場景 | 語料庫 `game_scene_links`（新） | 只有場景 | 屬於遊戲結構，本來就該獨立 |

而且 `card_relations` 的 `has_context`、`has_cloze` 其實和 `generated_sentences_log` 的 master → context／cloze 是同一件事，存了兩份。

## 2. 目標與非目標

**目標**
- G1 任何東西都能當節點：Anki 筆記、台詞、場景、說話者、詞（動詞原形）、BGM…，以後再加新種類不改表。
- G2 任何卡片類型都能**註冊自己的關係類型**（例：自他動詞的「自動詞 ↔ 他動詞」、核心動詞的「例句取自」），不改表。
- G3 一張「圖」= 選哪些節點、走哪些關係、怎麼分組、用哪種排版；新增一種圖只寫一個圖提供者加設定，不改既有的圖。
- G4 前端的排版引擎可以替換：力導向（現有知識圖譜）、劇情流程圖（新）、以後的樹狀、時間軸…，都吃同一種資料格式。

**非目標**
- 把所有資料都變成通用的節點與邊（EAV）：內容本身（台詞、場景）和遊戲結構（`game_scene_links`）有豐富的專屬欄位，維持原本的表。
- 讓使用者在網頁上用拖拉的方式定義新的圖：先做到「寫設定就能加圖」，視覺化編輯器以後再說。

## 3. 設計：分四層

```
④ 前端排版引擎   force（知識圖譜）│ story-flowchart（劇情流程圖）│ 以後：tree、timeline…
                         ▲ 同一種資料格式 GraphPayload
③ 圖提供者       KnowledgeGraph │ StoryFlowchart │ VerbPairGraph │ …（後端註冊表，每種圖一個）
                         ▲ 讀
② 關係           通用 rel_links（使用者或卡片類型建立的關係）＋ 原生結構表（game_scene_links 等，不複製）
                         ▲ 端點
① 實體           (entity_type, entity_id)：note、script_line、scene、speaker、term、bgm_track…
```

### D1 實體：用「類型＋ID」指向，不建總表

任何節點都用 `(entity_type, entity_id)` 表示，指向它自己的表。不另外建一張「所有實體」的總表，避免和原本的表重複、還要保持同步。

```sql
CREATE TABLE rel_entity_types (
  entity_type    VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  name_ja_jp     VARCHAR(64)  NOT NULL,
  name_zh_hant   VARCHAR(64)  NOT NULL,
  source         VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  id_kind        ENUM('int','string') NOT NULL DEFAULT 'int',
  PRIMARY KEY (entity_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| `entity_type` | `source` | 說明 |
|---|---|---|
| `note` | `anki` | Anki 筆記，ID 是 note id |
| `script_line` | `game_lines` | 遊戲台詞 |
| `scene` | `game_scenes` | 場景標籤 |
| `speaker` | `game_speakers` | 說話者 |
| `bgm_track` | `game_bgm_tracks` | BGM |
| `term` | `nlp_terms`（見下） | 詞或句型 |

**`term` 要不要建表**：目前動詞原形只是字串（`verb_lemma`）。要讓「詞」成為節點（例：「見る」連到它的所有例句與卡片），建議建一張小表 `terms (term_id, lang, lemma, term_type)`，由現有的 `verb_lemma` 回填。這樣詞有穩定的 ID，也能放多語說明。

### D2 關係類型：由卡片類型或系統註冊

```sql
CREATE TABLE rel_types (
  relation_type   VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  owner           VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  name_ja_jp      VARCHAR(64)  NOT NULL,
  name_zh_hant    VARCHAR(64)  NOT NULL,
  src_types       JSON         NOT NULL,
  dst_types       JSON         NOT NULL,
  directed        TINYINT(1)   NOT NULL DEFAULT 1,
  inverse_type    VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  style           JSON         NULL,
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (relation_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 | 例 |
|---|---|---|
| `relation_type` | 代號，建議 `<owner>.<名稱>` | `core.has_context`、`jp_verb_pair.transitive_pair`、`user.synonym` |
| `owner` | 誰定義的：卡片類型代號、`system` 或 `user`（網頁上手動建立） | `jp_core_verb` |
| `name_ja_jp`、`name_zh_hant` | 顯示名稱 | 例句取自／例文の出典 |
| `src_types`、`dst_types` | 允許的端點類型，寫入時檢查 | `["note"]` → `["script_line"]` |
| `directed`、`inverse_type` | 有沒有方向；反向關係的名稱（圖上可以從另一端看） | |
| `style` | 預設畫法（顏色、虛線、箭頭），主題可以覆寫 | |

卡片類型的 handler（`services/task_handlers/`）可以宣告自己的關係類型。後端啟動時同步進這張表，比照 `@register_handler` 的做法。

### D3 關係：一張通用表

```sql
CREATE TABLE rel_links (
  relation_id    BIGINT       NOT NULL AUTO_INCREMENT,
  relation_type  VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  src_type       VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  src_id         VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  src_label      VARCHAR(200) NULL,
  dst_type       VARCHAR(32)  COLLATE utf8mb4_bin NOT NULL,
  dst_id         VARCHAR(64)  COLLATE utf8mb4_bin NULL,
  dst_label      VARCHAR(200) NULL,
  attrs          JSON         NULL,
  status         ENUM('active','deleted') NOT NULL DEFAULT 'active',
  created_by     VARCHAR(64)  COLLATE utf8mb4_bin NOT NULL,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (relation_id),
  KEY idx_src (src_type, src_id, relation_type, status),
  KEY idx_dst (dst_type, dst_id, relation_type, status),
  KEY idx_type (relation_type),
  CONSTRAINT fk_rel_type FOREIGN KEY (relation_type) REFERENCES rel_types (relation_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 欄位 | 說明 |
|---|---|
| `src_*`、`dst_*` | 兩端的類型、ID、可讀標籤。ID 可以是 NULL，此時靠標籤表示「還沒建立的卡片」（沿用 `card_relations` 的懸空節點設計） |
| ID 為什麼用字串 | 端點可能是整數 ID（筆記、台詞），也可能是字串鍵；統一用字串，查詢時依 `rel_entity_types.id_kind` 轉型 |
| `attrs` | 這條關係的附加資料，例：例句在卡片中的角色、權重 |
| `created_by` | 由誰建立：卡片類型代號、`user`、`migration` |

**它取代的東西**
- 網站 SQLite 的 `card_relations`：172 列遷過來，`has_context` → `core.has_context`，以此類推。
- `corpus_db_rebuild` 草擬的 `card_links`：改成這張表的一種關係，`note → script_line`，類型 `core.example_from`。
- 由 `card_gen_log` 回填：master → context／cloze（筆記 ↔ 筆記）、子卡 → 台詞（筆記 ↔ 台詞）、子卡 → 詞（筆記 ↔ 詞）。

**它不取代的東西**：`game_scene_links`、台詞屬於哪個場景、台詞的說話者、台詞的 BGM。這些是內容本身的結構，欄位很多，留在原本的表；圖提供者直接讀它們，不複製一份到 `rel_links`。

### D4 圖提供者：一種圖一個

後端新增註冊表 `services/graphs/`，每種圖一個提供者：

```python
@register_graph("story_flowchart")
class StoryFlowchartGraph(GraphProvider):
    engine = "story-flowchart"                    # 前端用哪種排版引擎
    def build(self, params) -> GraphPayload: ...  # 從 story_builds、rel_links 組出節點與邊
```

| 圖 | 資料來源 | 排版引擎 |
|---|---|---|
| `knowledge` | `rel_links`（筆記 ↔ 筆記） | `force`（現有的知識圖譜） |
| `story_flowchart` | `story_builds`、`game_scene_links`、`rel_links`（筆記 ↔ 台詞） | `story-flowchart` |
| 以後：`verb_family` | `rel_links`（自動詞 ↔ 他動詞、詞 ↔ 例句） | `force` 或 `tree` |
| 以後：`speaker_map` | `game_speakers`、`game_lines` | `force` |

卡片類型可以**貢獻**圖：在 handler 宣告「我的筆記可以出現在哪些圖、怎麼附加上去」。例：核心動詞的筆記，以註記的形式附加在 `story_flowchart` 的台詞上。

**共用的資料格式 `GraphPayload`**（前後端共用的契約）：

```json
{
  "graph": "story_flowchart", "engine": "story-flowchart", "version": "build:12",
  "groups": [{"key": "common", "title": {"ja-JP": "共通", "zh-Hant": "共通"}}],
  "nodes": [{"key": "sw:009", "entity": {"type": "scene", "id": "123"}, "kind": "choice",
             "label": {"ja-JP": "休日デート"}, "group": "common", "attrs": {}}],
  "edges": [{"from": "sw:009", "to": "sw:009_01A", "type": "story.branch", "attrs": {}}],
  "annotations": {"sw:009": {"cards": {"jp_core_verb": 3}}}
}
```

劇情流程圖計畫書的 schema v1，就是這個格式的一個特例：`kind`、`attrs` 放流程圖專屬的資料。

### D5 前端：排版引擎註冊表

```
src/features/graph-views/
    core/          GraphPayload 型別、主題系統、共用工具
    engines/
        force/            現有知識圖譜的排版搬過來
        story-flowchart/  劇情流程圖計畫書的模組
    GraphView.tsx  依 payload.engine 選引擎
```

- 劇情流程圖計畫書的 `features/story-flowchart/` 改放到 `engines/story-flowchart/`，主題系統往上提到 `core/`，所有引擎共用。
- 現有的 `/graph`（知識圖譜）改成 `GraphView` 加 `force` 引擎。行為不變，只是資料改走 `GraphPayload`。
- 新增一種排版只要加一個引擎資料夾並註冊。

## 4. 網站 SQLite 怎麼辦

| SQLite 的表 | 建議 | 理由 |
|---|---|---|
| `card_relations`（172 列） | 遷到 `fluencytides_corpus.relations`，之後停用 | 關係圖要和台詞、詞 join，必須和語料庫在同一個資料庫 |
| `rel_types`（4 列） | 遷到 `fluencytides_corpus.relation_types` | 同上 |
| `capability_routes` | **留在 SQLite** | 是每個部署各自的執行設定，和語料、關係無關 |

所以 SQLite 不會完全廢除，只剩執行設定。要不要連設定也搬到 MySQL、完全不用 SQLite，是另一個決定，本草稿不處理。

> **正式機要注意**：正式機的 SQLite 是另一個檔案，`card_relations` 的內容可能和本機不同。遷移時要從正式機匯出，不能只用本機的 172 列。

## 5. 實作順序（建議）

| 階段 | 內容 |
|---|---|
| R0 | 語料庫重建（`corpus_db_rebuild_FEAT`）照原計畫做，但 D8 的 `card_links` 改成本草稿的 `rel_links`；`rel_entity_types`、`rel_types`、`rel_links` 一起建表，從 `card_gen_log` 回填 |
| R1 | 劇情流程圖照原計畫做，註記改讀 `rel_links`；模組放在 `features/graph-views/engines/story-flowchart/` |
| R2 | 知識圖譜改成讀 `rel_links`（SQLite 的 `card_relations` 遷過來），前端改用 `GraphView` 加 `force` 引擎 |
| R3 | 卡片類型可以宣告關係類型與圖貢獻；加第一個新圖（例：`verb_family`）驗證擴充性 |

## 6. 風險與未知

- **R1 多型關聯沒有外鍵**：`rel_links` 的端點不能用外鍵約束。
  - 應對：寫入時依 `rel_types.src_types`、`dst_types` 檢查；定期跑檢查腳本，找出指向不存在實體的關係；內容表一律只停用、不刪除。
- **R2 過度通用**：什麼都塞進 `rel_links`，最後很難查。
  - 應對：遵守 D3 的界線，原生結構留在原本的表；`rel_links` 只放「跨種類」或「使用者、卡片類型自己定義」的關係。
- **R3 Anki 筆記被刪**：`rel_links` 裡的 note 端點會失效。
  - 應對：沿用現有刪卡流程，同步把關係標成 `deleted`。
- **R4 知識圖譜改資料來源**：會影響現有的 `/graph` 頁。
  - 應對：R2 階段才做，並先用遷移後的資料比對節點數與邊數一致。

## 7. 決定紀錄

- ✅ 2026-11-01：R0 在這次語料庫重建時就導入（使用者決定），細節見 `corpus_db_rebuild_FEAT` D8。
- ✅ `term` 先不建表：ID 用 `<語言代碼>:<原形>` 字串（例 `ja-JP:見る`），之後建 `nlp_terms` 表時用同一個字串當主鍵。
- ✅ 2026-11-01：R2 的後端部分（知識圖譜改讀寫 `rel_links`）併入這次的語料庫遷移（`corpus_db_rebuild_FEAT` D9 的 C）。SQLite 原樣保留、資料不搬（全是可由卡片紀錄回填的 `has_*`）。前端改用 `GraphView` 的部分仍在之後。
