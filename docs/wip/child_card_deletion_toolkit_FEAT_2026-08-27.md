# FEAT — 子卡片刪除工具鏈:抽出共用核心、補齊 JP_CoreVerb、修正跨專案誤刪

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-27 |
| **性質** | 新增機能設計 + 實作工作項(含既有 JP_VerbPair 工具的 review 結論與修正) |
| **狀態** | 🚧 實作中（P0–P2 程式碼與單元測試完成 2026-08-27；待跑 DB 遷移/回填與 P3 端到端驗證） |
| **範圍** | `backend/scripts/local_anki/`(JP_VerbPair / JP_CoreVerb / 新增 common/deletion)、`backend/scripts/common/database/`(log_repository、init_db)、MySQL `generated_sentences_log` 資料表結構 |
| **不動** | 生成管線的選句/LLM 邏輯(`fastapi_client/` 兩專案的 generate_child_cards 僅在 DB 介面簽名變更處跟著改參數,不改行為)、Anki 模板與模型定義、`app/` 後端 API(handlers 不寫 dedup log,不受影響)、Speaking_Coach / Speaking_Trilingual 系列腳本 |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/14_Core_Verb_Card_Plan.md`(CoreVerb 生成設計)、`backend/scripts/local_anki/JP_VerbPair/` 現有三腳本 |

---

## 1. 問題與動機

2026-08-27 對兩專案刪除工具的調查結論(以下均為**已定位**,附證據):

### 1.1 JP_CoreVerb 完全沒有刪除工具

`backend/scripts/local_anki/JP_CoreVerb/` 只有 `create_master_card.py`、`import_models.py`、`update_templates.py`。
但 CoreVerb 的生成足跡與 JP_VerbPair 完全同構,刪一張卡需要同步清四個地方:

| 資源 | CoreVerb 寫入位置 | 證據 |
|---|---|---|
| 母卡 JSON | 單欄 `Word_Data_JSON`(item 含 `cloze_note_id`/`context_note_id`/`audio`/`avatar`) | `app/services/task_handlers/jp_core_verb_handler.py:376-386` |
| 子卡 | `JP_Context_Dark`(與 VerbPair **共用模型**)+ `JP_CoreVerb_Cloze_Dark` | `jp_core_verb_handler.py:80` |
| MySQL 去重 | 與 VerbPair **同一張** `generated_sentences_log`(經 `GeneratedLogRepository`) | `scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py:64,534` |
| 媒體 | 與 VerbPair **同一個**前綴(`JP_CORE_VERB_SOURCE_GAME` 預設 fallback 到 `JP_VERB_PAIR_SOURCE_GAME`) | `generate_child_cards.py:504-505` |

目前刪錯卡只能手動清,而且沒有任何完整性檢查能發現 CoreVerb 側的殘留。

### 1.2 JP_VerbPair 工具 review:三腳本的體質與缺陷

**`delete_child_cards.py`** — 整體設計良好(Dry Run 預設、單筆 try/except、母卡欄位備份回滾、
事後自動完整性檢查),母卡 JSON 移除邏輯正確(掃描兩欄比對 `cloze_note_id`)。缺陷:

- **R1(語意矛盾)**:MySQL 用硬刪除(`DELETE FROM`,`delete_child_cards.py:271-276`),
  但 `log_repository.py:136` 的設計註解明說「軟刪除代表使用者不想再生成該句,
  `get_logged_keys` 刻意不看 `is_deleted`」。硬刪除後同一句會被重新生成,
  違反自身的去重設計。repo 已有為此寫好的 `smart_delete_by_note_id`
  (`log_repository.py:281`,首刪保留紀錄、已軟刪才硬刪),**但無任何腳本使用,是死碼**。
- **R2(回滾邊界)**:步驟順序「改母卡 JSON → 刪子卡 → 刪 MySQL」
  (`delete_child_cards.py:230-306`),若 MySQL 步驟失敗,子卡已刪不可逆,
  回滾只能還原母卡 JSON,留下 DB 斷鏈(靠事後 integrity check 收拾,屬被動補救)。

**`check_integrity.py`** — 四維度檢查完整,但有**跨專案誤刪**問題(見 1.3)。

**`cleanup_script.py`** — 媒體前綴硬編碼 `"SabbatOfTheWitch_"`(`cleanup_script.py:74`,
未讀 `settings.JP_VERB_PAIR_SOURCE_GAME`),且刪除該前綴**所有**媒體,
會把 CoreVerb 引用的音檔/頭像一併刪光。

### 1.3 跨專案誤刪(最嚴重,一旦 CoreVerb 產卡即觸發)

`check_integrity.py:195-198` 讀取 `generated_sentences_log` **全部**活躍紀錄,
但 Anki 側只載入 `JP_VerbPair_Master_Dark` / `JP_Context_Dark` / `JP_VerbPair_Cloze_Dark`
(`check_integrity.py:216-218`)。CoreVerb 產卡後,以 `--execute` 執行會發生連鎖誤刪:

1. **第一次執行**:CoreVerb 的 DB 紀錄其 `master_note_id` 不在 VerbPair 母卡集合中
   → 被判為斷鏈 → **全部軟刪除**(step 4)。CoreVerb 的去重防線消失,之後會重複生成。
2. **第二次執行**:CoreVerb 的 Context 卡(共用 `JP_Context_Dark`,會被載入)
   因 DB 已無活躍紀錄、且找不到指向它的 cloze(`JP_CoreVerb_Cloze_Dark` 未載入)
   → 被判為不可修復孤兒 → **直接刪卡**(step 5,`check_integrity.py:559-570`)。
3. Context 卡被刪後,其 `Dialog_JSON` 引用的媒體不再列入 required
   → 後續執行被判為多餘媒體 → **刪除媒體**(step 7)。

而 `delete_child_cards.py:365-369` 在真實執行後會自動帶 `--execute` 呼叫 check_integrity,
等於**刪一張 VerbPair 卡就可能連鎖破壞 CoreVerb 的資料**。

**根因**:`generated_sentences_log` 無法區分紀錄屬於哪個專案。
`source` 欄存的是遊戲名(兩專案相同),unique key `uk_script_verb (script_id, verb_lemma)`
(`init_db.py:67`)也讓兩專案在同句+同動詞時互相衝突。
斷鏈紀錄的 `master_note_id` 指向已死的卡,執行期無從反查歸屬 → 必須落欄位。

## 2. 目標與非目標

**目標**

- G1 `generated_sentences_log` 增加 `project` 欄位,兩專案的 DB 紀錄可明確區分,
  unique key 改為 `(script_id, verb_lemma, project)`,既有資料完成歸屬回填。
- G2 刪除/完整性/清理三種工具的核心邏輯抽到共用模組,以「專案描述子(ProjectProfile)」
  參數化;JP_VerbPair 三腳本改為薄包裝,行為對齊修正後語意。
- G3 JP_CoreVerb 補齊 `delete_child_cards.py` / `check_integrity.py` / `cleanup_script.py`
  三個薄包裝,能力與 VerbPair 對等。
- G4 修正 R1(刪卡預設軟刪除、`--allow-regen` 才硬刪)、R2(步驟重排,不可逆操作最後做)、
  媒體保護(required media 取**所有已註冊專案**的聯集)、cleanup 前綴改讀 settings。

**非目標**

- 不做跨專案去重(同一句台詞允許同時出現在兩個牌組 —— 兩者是獨立學習目標,
  合併去重反而會讓後開的專案選不到好句)。
- 不改生成管線的選句與 LLM 邏輯(只跟著 repo 簽名加 `project` 參數)。
- 不處理 `docs/bk/` 時代的舊資料表或其他專案(Speaking 系列不用此 DB)。
- 不做 GUI/互動式刪除介面(維持 CLI + JSON 設定檔)。

## 3. 設計決策

### D1:DB 加 `project` 欄位(而非執行期反查歸屬)

**選擇**:`ALTER TABLE generated_sentences_log ADD COLUMN project VARCHAR(32) NOT NULL DEFAULT 'jp_verb_pair'`,
值域 `jp_verb_pair` / `jp_core_verb`;unique key 由 `uk_script_verb(script_id, verb_lemma)`
改為 `uk_script_verb_project(script_id, verb_lemma, project)`;加 `INDEX idx_project (project)`。

**放棄的方案**:執行期以 `master_note_id` 反查 Anki 卡片模型來歸屬。
理由:完整性檢查要處理的正是「母卡已不存在」的斷鏈紀錄,死卡無從反查,歸屬永遠有殘缺。

**存量資料回填**(migration 腳本,Dry Run 預設):

1. 撈出所有紀錄的 `master_note_id`,到 Anki 分別查 `JP_VerbPair_Master_Dark` 與
   `JP_CoreVerb_Master_Dark` 的 note id 集合,能匹配者按模型歸屬。
2. 匹配不到(母卡已死)者:歸 `jp_verb_pair`(CoreVerb 為新專案;若回填時
   CoreVerb 尚未產過卡,全部存量必屬 VerbPair)。回填報告需列出這批「按預設歸屬」
   的筆數供人工抽查。

**連帶修改**:`GeneratedLogRepository` 全部查詢方法加必填 `project` 參數
(`get_record` / `get_generated_script_ids` / `get_generated_records` / `get_logged_keys` /
`increment_failure_count` / `create_or_restore_record` / `soft_delete_record` /
`smart_delete_by_note_id`;`clear_all_records` 改為必填 `project`,
**TRUNCATE 改為 `DELETE FROM ... WHERE project = :project`**,不再允許整表清空)。
兩個 `generate_child_cards.py` 呼叫端跟著傳入各自的 project 常數。

### D2:共用核心放 `scripts/local_anki/common/deletion/`,以 ProjectProfile 參數化

**選擇**:新增套件 `backend/scripts/local_anki/common/deletion/`:

```
common/deletion/
├── __init__.py
├── profiles.py        # ProjectProfile dataclass + JP_VERB_PAIR / JP_CORE_VERB 兩個實例 + REGISTRY
├── child_deleter.py   # 單組子卡刪除核心(原 delete_child_cards 主流程)
├── integrity.py       # 完整性檢查核心(原 check_integrity 四維度,參數化)
├── cleanup.py         # 全量清理核心(原 cleanup_script 主流程)
└── media_scan.py      # collect_required_media(profiles): 跨專案掃描所有引用中的媒體
```

`ProjectProfile` 欄位(dataclass,全部由 settings 或常數組成):

| 欄位 | JP_VerbPair 值 | JP_CoreVerb 值 |
|---|---|---|
| `project_key`(DB project 值) | `jp_verb_pair` | `jp_core_verb` |
| `master_model` | `JP_VerbPair_Master_Dark` | `JP_CoreVerb_Master_Dark` |
| `cloze_model` | `JP_VerbPair_Cloze_Dark` | `JP_CoreVerb_Cloze_Dark` |
| `context_model` | `JP_Context_Dark`(共用) | `JP_Context_Dark`(共用) |
| `master_json_fields` | `["Intransitive_Data_JSON", "Transitive_Data_JSON"]` | `["Word_Data_JSON"]` |
| `root_deck` | `settings.JP_VERB_PAIR_*`(現行預設 `日本語::自他動詞`) | `settings.JP_CORE_VERB_MASTER_DECK` 的父牌組(`日本語::核心動詞`) |
| `source_game`(媒體前綴) | `settings.JP_VERB_PAIR_SOURCE_GAME` | `settings.JP_CORE_VERB_SOURCE_GAME`(fallback 同生成腳本) |

母卡 JSON 欄位差異(雙欄 vs 單欄)以 `master_json_fields` 清單吸收,
核心邏輯一律「逐欄掃描比對 `cloze_note_id`」,VerbPair 掃兩欄、CoreVerb 掃一欄,無需分支。

**Context 卡歸屬**:`JP_Context_Dark` 兩專案共用,完整性檢查載入後必須先分流 ——
以卡上的 `Master_Note_ID` 欄位反查母卡模型歸屬;母卡已死的 Context 孤兒,
再以「本專案 DB 紀錄的 `context_note_id`」兜底;兩者皆無法歸屬的,
**只回報不刪除**(避免誤刪他專案或手動建立的卡)。

**放棄的方案**:繼續兩專案各養一份腳本(複製 modify)。理由:check_integrity 近 800 行,
複製後任何修補都要做兩次,且本次要修的跨專案媒體保護本質上就需要一個「知道所有專案」的註冊表。

### D3:刪卡的去重語意 —— 預設軟刪除,`--allow-regen` 才硬刪

**選擇**:`child_deleter` 對 DB 改用既有的 `smart_delete_by_note_id` 語意:

- 預設:軟刪除(`is_deleted=TRUE`、`delete_count+1`)。因 `get_logged_keys` 刻意不看
  `is_deleted`,該句**不會**再被生成 —— 符合「刪卡 = 這句不好,不要再來」的預設直覺。
- `--allow-regen` 旗標:硬刪除紀錄,讓該句回到候選池 —— 用於「卡片內容生成壞了,
  想重生成同一句」的場景。

死碼 `smart_delete_by_note_id` 就此啟用(依 D1 加 `project` 參數)。

### D4:刪除步驟重排 —— 不可逆操作放最後

**選擇**:單組刪除流程改為:

1. 驗證三卡存在 + 備份母卡欄位(不變)。
2. 母卡 JSON 移除紀錄(Anki,可還原:有備份)。
3. DB 軟刪除/硬刪除並 commit(可還原:軟刪 UPDATE 回去、硬刪按備份 re-insert)。
4. **最後**才 `deleteNotes([cloze, context])`(不可逆)。

任一步失敗:還原前面已做的可逆步驟,跳過此任務。原順序(先刪卡後刪 DB)的
「子卡已刪、DB 失敗」殘局從此不會出現;deleteNotes 本身失敗則前面全部還原,狀態乾淨。

### D5:媒體刪除的跨專案保護

**選擇**:`media_scan.collect_required_media(profiles)` 掃描**REGISTRY 中所有專案**的
母卡 JSON(audio/avatar)、Cloze 卡(Audio/Avatar 欄)、Context 卡(`Dialog_JSON` 逐 turn),
回傳引用中媒體的聯集。`integrity` 的多餘媒體判定與 `cleanup` 的媒體刪除,
一律用「該前綴媒體 − 全專案聯集」計算,兩專案共用同一前綴也不會互相誤刪。

`cleanup` 的前綴改讀 profile 的 `source_game`(即 settings),移除硬編碼字串。

### D6:integrity 核心的專案過濾

- DB 讀取加 `WHERE project = :project`(D1 後可行),斷鏈判定只對本專案紀錄做。
- 孤兒判定:cloze 用本專案 `cloze_model`;context 依 D2 歸屬分流後只處理本專案的。
- 孤兒媒體:依 D5 全專案聯集,`--execute` 才刪。
- CoreVerb 的孤兒修復(反查 `scripts` 表重建 DB 紀錄)與 VerbPair 共用同一套邏輯:
  cloze 卡上同樣有 `Master_Note_ID` / `Context_Note_ID` / `Audio` 欄
  (`jp_core_verb_handler.py:355-357`),verb_lemma 改從 CoreVerb cloze 的欄位取
  (實作時依 `JP_CoreVerb_Cloze_Dark` 模型實際欄位定案,profile 提供
  `extract_verb_lemma(cloze_fields)` 掛鉤)。

### D7:薄包裝 CLI 介面(兩專案一致)

```
JP_VerbPair/delete_child_cards.py   →  common.deletion.child_deleter + JP_VERB_PAIR profile
JP_VerbPair/check_integrity.py      →  common.deletion.integrity     + JP_VERB_PAIR profile
JP_VerbPair/cleanup_script.py       →  common.deletion.cleanup       + JP_VERB_PAIR profile
JP_CoreVerb/delete_child_cards.py   →  同上,換 JP_CORE_VERB profile
JP_CoreVerb/check_integrity.py      →  同上
JP_CoreVerb/cleanup_script.py       →  同上
JP_CoreVerb/configs/delete_child_cards.json  →  新增(格式同 VerbPair 版)
```

CLI 旗標統一:`--execute`(預設 Dry Run)、`--master-nid`(delete 專用)、
`--allow-regen`(delete 專用,D3)。JSON 設定檔格式沿用 VerbPair 現行版
(`master_nid` 必填,`cloze_nid`/`context_nid` 選填 = 全刪)。

## 4. 改動清單

### DB / Repository

| 檔案 | 改動 |
|---|---|
| `scripts/common/database/init_db.py` | DDL 加 `project` 欄 + 新 unique key;`_ensure_columns` 補 `project`;新增冪等的 key 遷移(drop `uk_script_verb` → add `uk_script_verb_project`) |
| `scripts/common/database/backfill_project.py` | **新增**:存量歸屬回填腳本(D1,Dry Run 預設) |
| `scripts/common/database/log_repository.py` | 全方法加 `project` 參數;`clear_all_records` 改按 project 刪除;`smart_delete_by_note_id` 同步 |

### 共用核心(新增)

| 檔案 | 改動 |
|---|---|
| `scripts/local_anki/common/deletion/profiles.py` | ProjectProfile + 兩實例 + REGISTRY |
| `scripts/local_anki/common/deletion/child_deleter.py` | 單組刪除核心(D3/D4 語意) |
| `scripts/local_anki/common/deletion/integrity.py` | 四維度檢查核心(D5/D6) |
| `scripts/local_anki/common/deletion/cleanup.py` | 全量清理核心(D5) |
| `scripts/local_anki/common/deletion/media_scan.py` | 跨專案 required media 聯集 |

### 專案薄包裝

| 檔案 | 改動 |
|---|---|
| `scripts/local_anki/JP_VerbPair/delete_child_cards.py` | 改為薄包裝(參數解析 + profile 注入) |
| `scripts/local_anki/JP_VerbPair/check_integrity.py` | 同上 |
| `scripts/local_anki/JP_VerbPair/cleanup_script.py` | 同上,前綴改 settings |
| `scripts/local_anki/JP_CoreVerb/delete_child_cards.py` | **新增**薄包裝 |
| `scripts/local_anki/JP_CoreVerb/check_integrity.py` | **新增**薄包裝 |
| `scripts/local_anki/JP_CoreVerb/cleanup_script.py` | **新增**薄包裝 |
| `scripts/local_anki/JP_CoreVerb/configs/delete_child_cards.json` | **新增**空清單範本 |

### 生成管線(僅簽名跟改)

| 檔案 | 改動 |
|---|---|
| `scripts/fastapi_client/JP_VerbPair/generate_child_cards.py` 及 pipeline_components | repo 呼叫處傳 `project='jp_verb_pair'` |
| `scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py` | repo 呼叫處傳 `project='jp_core_verb'` |

### 測試

- `log_repository`:project 隔離(A 專案的 get/clear/smart_delete 不碰 B 專案紀錄)。
- `child_deleter`:步驟順序與回滾(mock AnkiClient/session,模擬 deleteNotes 失敗
  → 驗證 JSON 與 DB 均還原);`--allow-regen` 硬刪 vs 預設軟刪。
- `media_scan`:兩 profile 交叉引用同一檔案時不列入可刪集合。
- `integrity`:CoreVerb 紀錄存在時,VerbPair 檢查不將其判為斷鏈/孤兒(1.3 情境回歸測試)。

## 5. 實作順序

- **P0 — DB 遷移與 repo 改造**(先做:後續一切依賴 project 欄):
  init_db 加欄+換 key → backfill 腳本 → log_repository 加參數 → 生成管線跟改簽名。
  驗收閘門:backfill Dry Run 報告人工確認後才 `--execute`。
- **P1 — 共用核心 + VerbPair 切換**:建 `common/deletion/` 五模組,
  VerbPair 三腳本改薄包裝,行為對齊 D3–D6。用現有 VerbPair 資料實跑
  Dry Run 對照舊版輸出(結果集應一致,僅語意修正處有差異)。
- **P2 — CoreVerb 三工具**:純新增薄包裝 + configs,幾乎零新邏輯。
- **P3 — 端到端驗證**:兩專案各生成測試卡 → 交叉跑 check_integrity `--execute`
  確認互不誤刪 → delete_child_cards 單卡刪除 → cleanup Dry Run 確認媒體保護。

每階段可獨立驗收、獨立 PR。

## 6. 風險與未知

- **unique key 遷移失敗**(存量資料若已有跨專案同 `(script_id, verb_lemma)` 衝突,
  舊 key 下不可能存在,故 drop→add 理論安全)。應對:遷移前 `SELECT COUNT(*)` 驗證
  無重複,遷移腳本整體包在報告式 Dry Run 內。
- **backfill 誤歸屬**:母卡已死的紀錄按預設歸 `jp_verb_pair`,若 CoreVerb 在回填前
  已產卡又刪母卡,會歸錯。應對:回填報告列出全部「預設歸屬」筆數;
  若執行時 CoreVerb 尚未產卡(可用 `note:"JP_CoreVerb_Master_Dark"` 查證),風險為零。
- **`JP_CoreVerb_Cloze_Dark` 欄位假設**:D6 的孤兒修復假設 CoreVerb cloze 具備
  `Master_Note_ID`/`Context_Note_ID`/`Audio` 欄(handler 寫入處已證實),
  但 verb_lemma 的取得欄位待實作時對模型定義核對 —— 若無對應欄,
  CoreVerb 的孤兒修復降級為「只回報不自動重建」,不影響其他維度。
- **薄包裝重構造成行為漂移**:P1 以 Dry Run 新舊對照把關;
  舊腳本在切換 PR 中直接替換(git 歷史可回溯,不留 `_old` 副本)。

## 6.5 實作紀錄與計劃出入（2026-08-27）

P0–P2 一次完成，與計劃的出入如下：

- **不建 `__init__.py`**：`scripts/` 全樹沿用 namespace package 慣例（原有目錄皆無
  `__init__.py`），`common/deletion/` 跟隨，計劃 §D2 檔案樹中的 `__init__.py` 不建。
- **`smart_delete_by_note_id` 直接刪除**（驗收標準的第二分支）：刪卡工具需要的是
  「三 ID 全匹配 + 軟/硬明確可控 + commit 可延遲」，故在 repo 新增
  `delete_record_by_note_ids` / `count_record_by_note_ids` 取代；單一 note id 比對的
  smart_delete 語意（首刪軟、再刪硬）與 `--allow-regen` 旗標式控制重疊且較不精準，
  維持死碼沒有意義，予以刪除。
- **D4 的回滾實作比計劃更乾淨**：DB 標記改為「不 commit → deleteNotes 成功後才
  commit」，deleteNotes 失敗時直接 `rollback()`，不需要計劃中「commit 後再
  un-soft-delete」的補償邏輯。
- **事後完整性檢查改為同進程函式調用**：原 `delete_child_cards.py` 用 subprocess
  呼叫 check_integrity.py（5 分鐘超時保護）；共用核心後直接 `await
  run_integrity_check(...)`，共用同一個 AnkiClient 連線。
- **順手修復**：`old/` 遺留腳本（generate_child_cards / test_single_generate）的
  DedupManager 呼叫一併補上 project 參數，避免樹上留下必 crash 的呼叫；
  `pipeline_components/README.md` 範例同步更新。
- **媒體刪除加最後防線（2026-08-28 補）**：孤兒判定的保護集合只掃已註冊筆記類型，
  未註冊筆記類型引用同前綴檔案時會有盲區。`media_scan.guard_unreferenced` 在
  integrity / cleanup 實際刪除每個媒體檔前，對**整個 Anki 集合**（不限筆記類型）
  做全文搜尋（Anki 欄位搜尋比對原始文字，JSON 內裸檔名也命中），仍被任何卡片
  引用即攔下並以 `media_guard_blocked` 回報。
- **刪卡前加筆記類型驗證（2026-08-28 補）**：JSON 精確三元組模式原本只驗證卡片
  存在；現在步驟 0 同時比對三張卡的 modelName 與 profile 鎖定的類型，
  誤把他專案的 nid 填進清單會被直接拒絕（防止用錯腳本刪錯筆記類型）。
- **新增以 DB id 為入口的通用刪除工具（2026-08-28 補）**：
  `common/deletion/id_deleter.py` + 包裝
  `scripts/local_anki/delete_by_generated_sentences_log_id.py`
  （檔名直接寫上表名，一眼可知輸入的是哪張表的 id）。
  輸入 generated_sentences_log 的 id（支援單一/逗號分隔/範圍混用），
  以每筆紀錄的 `project` 欄自動選擇 profile 後按專案分組調用 child_deleter
  （`tasks` 參數為此新增），不需手動指定卡片類型；純失敗紀錄（無子卡 note id）
  與查無 id 者跳過並回報。
  **2026-08-28 語意補完**：「無卡可刪」的紀錄（純失敗紀錄，或子卡已不存在於
  Anki）在 `--allow-regen` 下直接硬刪 DB 列（否則該句永遠回不到候選池）；
  預設軟刪除語意下維持跳過（紀錄留著本來就擋得住重新生成）。
- **單元測試**：`backend/tests/test_deletion_toolkit.py` 15 例，涵蓋 project 驗證、
  lemma 抽取、媒體聯集保護、integrity 跨專案回歸（§1.3 情境：CoreVerb 的卡片/
  媒體/DB 紀錄在 VerbPair `--execute` 下全數無恙）、child_deleter 成功/失敗回滾/
  dry-run/`--allow-regen` 四路徑。全套件 143 passed。

## 7. 驗收標準

- [ ] `generated_sentences_log` 有 `project` 欄與 `(script_id, verb_lemma, project)` unique key;存量資料回填完成且報告已人工確認。（程式碼已備:`init_db.py` + `backfill_project.py`,**尚未對真實 DB 執行**）
- [x] `GeneratedLogRepository` 所有方法帶 project 過濾;單元測試證明專案隔離。
- [ ] JP_VerbPair 三腳本改為薄包裝後,Dry Run 輸出與舊版對照無非預期差異。（程式碼完成,待實機 Dry Run 對照）
- [x] `delete_child_cards`(兩專案)預設軟刪除、`--allow-regen` 硬刪;deleteNotes 為最後一步,模擬失敗時 JSON 與 DB 完整還原（單元測試覆蓋）。
- [ ] JP_CoreVerb 三工具可執行:單卡刪除(母卡 `Word_Data_JSON` 正確移除該筆)、完整性檢查、全量清理。（程式碼完成,待實機驗證）
- [x] 交叉驗證:兩專案各有測試卡時,任一專案 `check_integrity --execute` 不軟刪對方 DB 紀錄、不刪對方 Context 卡、不刪對方引用中的媒體（單元測試以 fake Anki/DB 覆蓋;實機交叉驗證待 P3）。
- [x] `cleanup_script` 不再硬編碼前綴;Dry Run 清單排除他專案引用中的媒體。
- [x] 死碼 `smart_delete_by_note_id`:語意不合(單 ID 比對、無法延遲 commit),已刪除並以 `delete_record_by_note_ids` 取代,詳見 §6.5。
