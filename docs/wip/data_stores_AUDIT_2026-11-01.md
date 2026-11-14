# AUDIT — 資料儲存盤點與遷移風險評估

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-01 |
| **性質** | 稽核：盤點現有的資料庫與儲存體、彼此怎麼互相引用，並逐項評估遷移到 `fluencytides_corpus` 的風險 |
| **狀態** | 🔨 盤點完成；收尾進行中（2026-11-14，見 §6）：ES 已核對並補上千戀＊萬花、兩個資料庫已傾印到開發機、`rebuild.py` 改成檢查目標資料庫。**還沒做**：切換後實際建卡的驗收（試跑已通過）、定期備份與第二份備份的去處 |
| **範圍** | MySQL `yuzusoft`、`fluencytides_corpus`；網站 SQLite（本機與正式機各一份）；Elasticsearch `fluencytides_dialogue`；Anki；MinIO；本機的校準檔案與設定檔 |
| **不動** | §1–§5 只盤點與評估，不改任何資料；§6 的收尾另外記錄做了什麼 |
| **PR / 進度** | `rebuild.py` 的保護：分支 `fix/rebuild-guard` |
| **關聯文件** | `docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md`、`docs/wip/relation_graph_FEAT_draft_2026-11-01.md`、`docs/wip/story_flowchart_FEAT_2026-10-31.md` |

---

## 1. 現有的儲存體

> 本節與 §2 描述現況，用的是舊表名；§3 之後的新庫表名依 `corpus_db_rebuild_FEAT` D0 的前綴規則（`game_`、`nlp_`、`card_`、`rel_`、`story_`）。

| # | 儲存體 | 位置 | 內容 | 誰寫入 | 誰讀取 |
|---|---|---|---|---|---|
| S1 | MySQL `yuzusoft` | 192.168.50.172（MySQL 5.7.31），本機與正式機**共用同一個** | `scripts` 53,190、`generated_sentences_log` 6,265、`dialogue_terms_index` 9,184、`jp_verb_reading_judgments` 0、`evaluation` 0 | 建卡管線（本機的 `backend/scripts/`，透過後端 API 建卡）、詞彙索引腳本 | 建卡管線、ES 同步、Anki 工具、後端 |
| S2 | MySQL `fluencytides_corpus` | 同上 | 空的（2026-11-01 建立） | — | — |
| S3 | 網站 SQLite（本機） | `backend/fluencytides.db` | `card_relations` 172（只有 `has_context`、`has_cloze`）、`relation_types` 4、`capability_routes` 0 | 本機後端（建卡時寫關聯、手動建關聯、關聯同步） | 本機後端（知識圖譜、能力路由） |
| S4 | 網站 SQLite（正式機） | 正式機 `/DATA/AppData/FluencyTides/backend/data/fluencytides.db`（容器內 `/app/data`） | 同上的結構，**內容未知** | 正式機後端 | 正式機後端 |
| S5 | Elasticsearch | `fluencytides_dialogue` 索引 | 每句台詞一份文件：`script_id`、`source`、`dialogue`（Sudachi 分析） | `sync_mysql_to_es.py`（**先刪除整個索引再重建**） | 建卡管線的例句搜尋、後端 |
| S6 | Anki | 本機 AnkiConnect ＋ AnkiWeb 同步 | 卡片與筆記；例句、章節、說話者是**複製進欄位的文字**；語音以 `SabbatOfTheWitch_<檔名>.mp3` 存在 Anki 媒體 | 後端建卡 | 網站卡片頁、Anki 本身 |
| S7 | MinIO | `MINIO_DEFAULT_BUCKET` | 媒體檔（目前是獨立的媒體 API，尚未接到建卡）；之後放流程圖的縮圖與存檔樣板 | 後端 storage API | 後端 |
| S8 | 本機校準檔案 | `game\SW_point_table.*`、`game\SW_calib\` | 錨點、繁中、縮圖、BGM、選項、路線實測 | 校準程式 | 遷移腳本（之後） |
| S9 | 本機設定檔 | `backend/scripts/fastapi_client/JP_CoreVerb/verb_search_config.json`；語音、頭像資料夾（`JP_*_VOICE_DIR`、`JP_*_AVATAR_DIR`） | `exclude_script_ids` 等每個動詞的設定；語音與頭像原檔 | 人工 | 建卡管線 |
| S10 | 舊備份 | `sql/yuzusoft_backup.sql`（2026-06-21，24 MB） | `yuzusoft` 的舊傾印 | — | — |

## 2. 互相引用的關係

```
                         scripts.id（S1）── 全系統共用的台詞 ID
       ┌──────────────┬──────────────┼───────────────┬──────────────────┐
       ▼ FK           ▼ FK           ▼ FK            ▼ 文件 _id          ▼ 數值
 generated_sentences_log  dialogue_terms_index  jp_verb_reading_judgments  ES fluencytides_dialogue  verb_search_config.json
       │ master／context／cloze_note_id                                    （S5）             exclude_script_ids（S9）
       ▼
 Anki note id（S6）◄───── card_relations.source／target_note_id（S3、S4）
                         （has_context、has_cloze 和左邊的 master→子卡是同一件事）

 校準檔案（S8）── 只用「檔名＋台詞文字」對到 scripts，沒有存 id
 Anki 筆記（S6）── 沒有存 script_id，只存文字複本與語音檔名
```

**關鍵事實**
- **`scripts.id` 是所有東西的共同鍵**：3 張表的外鍵、ES 的文件 ID、`exclude_script_ids` 都用它。新庫沿用舊 id，所以這些都不用改。
- **Anki 不認得 `script_id`**：筆記和台詞之間的連結，全部在 `generated_sentences_log`。這張表如果遺失，卡片就再也找不回出處。
- **同一件事存了兩份**：母卡 → 子卡的關係同時存在 `generated_sentences_log`（S1）和 `card_relations`（S3、S4）。
- **SQLite 有兩份**：本機和正式機各自的後端寫各自的檔案，`card_relations` 可能不一致。
- **ES 完全由 MySQL 推導**：沒有只存在 ES 的資料（`mark_skipped_es_data.py` 寫的是 MySQL 的 `failure_count`），可以隨時重建。

## 3. 逐項的遷移風險

評等：🟢 低　🟡 中　🔴 高

| # | 遷移項目 | 風險 | 可能出什麼事 | 應對 |
|---|---|---|---|---|
| M1 | `scripts` → `game_lines`、`game_legacy_scripts`、檢視表 `scripts` | 🟡 | ① 1,955 句同檔重複台詞對錯 id，卡片掛到錯的劇情位置。② 檢視表的值和舊表有差（9516 改字、6 句還原），影響建卡的前後文。③ 檢視表多表 join 變慢 | ① 依劇情順序做序列對齊＋抽查前後文＋比對 Anki 例句（50 筆）。② `verify.py` 逐列比較，差異只允許清單上的幾處。③ 用建卡管線實測耗時 |
| M2 | 卡片相關 3 張表（保留 id） | 🟡 | ① 切換期間有新的建卡寫入，兩邊不一致。② `AUTO_INCREMENT` 沒接續，新紀錄撞到舊 id。③ **遷移腳本重跑時清掉已經切換後寫入的紀錄** | ① 切換時凍結建卡，切換前再比一次列數。② 建表後設定 `AUTO_INCREMENT` 為舊值。③ 見 §4：內容表可以重建，卡片表只遷移一次 |
| M3 | `rel_links` 回填（約 3.7 萬列） | 🟢 | 回填規則錯（漏掉已刪除的紀錄、角色對錯） | 回填後依 project 比對列數；抽查 20 張卡的關係 |
| M4 | ES 重建 | 🟢 | ① 同步腳本會**先刪索引再重建**，期間搜尋失敗。② 內容會有小變動（9516、6 句還原） | ① 在凍結時段內做，不和建卡同時跑。② 已知差異，可以接受（6 句還原是使用者決定的） |
| M5 | 切換 `MYSQL_DATABASE`（本機與正式機 `.env`） | 🟡 | ① 只改了一邊，本機與正式機讀不同的資料庫，卡片紀錄分裂。② 有腳本寫死資料庫名 | ① 兩邊在同一個凍結時段內改，改完各自確認連到新庫。② 已搜尋：程式裡只有 `.env` 寫 `yuzusoft`，都經由 `settings.MYSQL_DATABASE` |
| M6 | 本機 SQLite `card_relations` → `rel_links`（關係圖草案的 R2，不在這次） | 🟡 | ① 本機與正式機兩份 SQLite 內容不同，只遷一份會漏。② 和 M3 回填的關係重複 | ① 兩份都匯出、合併。② 依 `(類型, 起點, 終點)` 去重 |
| M7 | 正式機 SQLite（同 M6） | 🟡 | 內容未知；正式機在容器裡，要從 volume 取出 | R2 前先唯讀匯出，盤點內容 |
| M8 | SQLite `capability_routes` | 🟢 | 不遷移 | — |
| M9 | Anki | 🟢 | 不遷移；筆記內的文字複本和新庫不同步（例：9516 的卡片欄位仍是舊字） | 不處理，屬於已存在的卡片內容；需要時另案更新 |
| M10 | 校準檔案 → 新庫 | 🟡 | ① 匯入後兩邊都有資料，之後誰是正本不清楚。② 校準若再更新，檔案改了資料庫沒改 | ① 匯入後**資料庫是正本**，校準檔案改為來源紀錄。② 校準更新後必須重跑遷移腳本的內容部分，並在 `game_line_revisions` 留紀錄 |
| M11 | `verb_search_config.json` 的 `exclude_script_ids` | 🟢 | id 沒變，不受影響 | 驗證時抽查清單內的 id 都存在 |
| M12 | `yuzusoft` 退役 | — | 不適用：使用者決定 `yuzusoft` 與兩份 SQLite 都保留不動（2026-11-01） | — |
| M13 | 備份 | 🟡（與遷移無關） | 兩個資料庫在同一台 MySQL；那台機器的硬碟壞了會一起遺失。最新的異機傾印是 6 月 21 日的 | 遷移本身不需要備份：只讀舊庫，切換後舊庫就是遷移前的快照（2026-11-01 使用者確認）。異機備份另案處理，重點是切換後持續寫入的 `card_gen_log`、`rel_links` |

## 4. 從盤點發現、要回頭修正計畫書的地方

1. **遷移腳本不能整庫清空重建**（M2-③）：`corpus_db_rebuild_FEAT` D7 原本寫「每次都先清空新資料庫的表，再從頭建立」，但切換後，卡片相關表與 `rel_links` 會持續有新的寫入。改成兩類：
   - **內容表**（`game_profiles`、`game_script_files`、`game_scenes`、`game_scene_links`、`game_speakers`、`game_lines`、`game_bgm_tracks`、`game_line_bgm`、`game_legacy_scripts`）：切換前可以反覆重建；切換後改為依 id 更新（`INSERT … ON DUPLICATE KEY UPDATE`），不刪除既有列。
   - **紀錄表**（卡片相關 3 張、`rel_links`、`game_line_revisions`）：只在切換時遷移一次；之後只能新增或更新，任何腳本都不得清空。
   - 2026-11-14：`rebuild.py` 已照這條改成檢查目標資料庫（§6.3）。
2. **備份**（M13）：不列為遷移的前置步驟（遷移只讀舊庫）；異機備份另案處理。
3. **正式機 SQLite 先盤點**（M7）：關係圖草案的 R2 之前，先唯讀匯出正式機的 `card_relations`，比對和本機的差異。

## 5. 建議的順序

1. （不需要）遷移前的備份：見 M13。
2. 語料庫重建 M1：建表、匯入、驗證；這段期間舊庫照常使用，不影響任何功能。
3. 凍結建卡 → 最後一次同步卡片紀錄 → 切換本機與正式機的 `.env` → 重建 ES → 解凍（M2、M4、M5）。
4. `yuzusoft` 與 SQLite 保留不動（使用者決定），只是程式不再讀寫它們。
5. 關係圖 R2：盤點並合併兩份 SQLite 的 `card_relations`（M6、M7）。

## 6. 收尾紀錄（2026-11-14）

### 6.1 Elasticsearch（M4）

切換後「重建 ES」一直沒有紀錄，千戀＊萬花匯入後有沒有同步也沒查過。用同步腳本自己的讀法與清洗（`iter_lines`＋`build_action`）算出應有的文件，和索引逐筆比對：

| 遊戲 | 新庫應有 | 同步前 ES | 同步後 ES | 缺／多／內容不同 |
|---|---|---|---|---|
| 魔女的夜宴（`サノバウィッチ`） | 53,196 | 53,196 | 53,196 | 0／0／0 |
| 千戀＊萬花（`千恋＊万花`） | 55,726 | 0 | 55,726 | 0／0／0 |

- 魔女的夜宴在同步前就和新庫完全一致（含還原的 6 句與 9516 的改字），所以**不需要重建整個索引**。
- 千戀＊萬花用單一遊戲的同步補上，約 11 秒，魔女的夜宴的文件沒有動，搜尋沒有中斷：

```bash
python scripts/database/Elasticsearch/sync_mysql_to_es.py --game SenrenBanka
```

- 同步後各搜一次「食べる」：千戀＊萬花 434 筆、魔女的夜宴 371 筆。

### 6.2 備份（M13）

兩個資料庫各傾印一份到開發機（NAS 以外），放在 repo 的 `sql/`（被 git 忽略，6 月那份 `yuzusoft_backup.sql` 也在這裡）：

| 檔案 | 壓縮後 | 解壓後 | 表 | 列數 |
|---|---|---|---|---|
| `sql/fluencytides_corpus_20261114_2026.sql.gz` | 12.7 MB | 95.1 MB | 18 | 486,642 |
| `sql/yuzusoft_20261114_2027.sql.gz` | 4.4 MB | 44.8 MB | 5 | 124,365 |

- 做法：在 NAS 上用 `mysql:5.7.31` 映像開一個用完即刪的臨時容器、走主機網路連線跑 `mysqldump`，gzip 後經 SSH 傳回開發機。只讀資料庫（`--single-transaction`）。帳密放在一個 `[client]` 設定檔，經標準輸入送進臨時容器，不出現在指令列。

```bash
ssh jacky917@192.168.50.172 "docker run --rm -i --network host --entrypoint bash mysql:5.7.31 -c 'set -o pipefail; umask 077; cat > /tmp/c.cnf; mysqldump --defaults-extra-file=/tmp/c.cnf --single-transaction --no-tablespaces --triggers --hex-blob --skip-extended-insert --default-character-set=utf8mb4 --set-gtid-purged=OFF --databases fluencytides_corpus | gzip -c'" < client.cnf > sql/fluencytides_corpus_YYYYMMDD.sql.gz
```

- `client.cnf` 的內容（不要放進 repo）：

```ini
[client]
user=<MYSQL_USER>
password="<MYSQL_PASSWORD>"
host=192.168.50.172
port=3306
protocol=tcp
```

- 為什麼不直接在 MySQL 容器裡跑：從容器內連線時來源是 Docker 內部網段（`172.16.0.0/255.240.0.0`），那個帳號來源只有新庫的權限，傾印 `yuzusoft` 會被拒絕（1044）。走主機網路時來源是 `192.168.%`，和 app 平常連線的帳號來源相同，兩個資料庫都能讀。
- `--skip-extended-insert`：一列一個 INSERT。檔案大一點、還原慢一點，但能逐表核對列數。
- 檢查：壓縮檔讀得到檔尾、結尾有 `-- Dump completed`、每張表都在、每張表的 INSERT 列數和線上的 `COUNT(*)` 相同（兩個資料庫都通過）。
- **沒有做的檢查**：實際還原一次。開發機沒有 MySQL 也沒有 docker，app 的帳號也只能動這兩個資料庫，沒有地方可以還原。
- 還原（會覆寫同名的表，還原前先確認目標）：

```bash
gunzip -c sql/fluencytides_corpus_20261114_2026.sql.gz | mysql --defaults-extra-file=client.cnf
```

- 傾印當下，`card_gen_log` 最後一次寫入是 2026-09-07，和舊庫的 `generated_sentences_log` 相同（6,265 列）：切換後還沒有新的卡片紀錄。
- **還沒決定**：要不要再放一份到別處（例如雲端硬碟）、要不要定期做。

### 6.3 `rebuild.py` 不再能清掉紀錄表（§4 第 1 點）

- 原本只看本機 `.env` 的 `MYSQL_DATABASE` 是不是新庫；換一份 `.env` 或在別台機器執行就擋不住。
- 改成刪表前查目標資料庫（`unrecoverable_data`）：`card_gen_log`、`rel_links`、`game_line_revisions`、`nlp_line_terms`、`nlp_line_readings`、`story_builds` 有任何一列，或 `game_profiles` 有其他遊戲，就結束程式、什麼都不刪，沒有略過的選項。開始對齊前查一次，`write` 刪表前在同一個連線再查一次；原本的 `.env` 檢查保留為第一道。
- 所以 `rebuild.py` 之後只能對空的資料庫執行；要在測試用的資料庫重跑，先自己刪掉那個資料庫再建一個空的。
- 用真實的 `fluencytides_corpus` 驗證（只查不刪）：回報 `card_gen_log` 6,265、`rel_links` 36,761、`game_line_revisions` 54、`nlp_line_terms` 9,184、`story_builds` 3、其他遊戲 1，拒絕執行。
- 其他會刪資料的腳本：`import_senren_banka.py` 在紀錄表引用到這款遊戲時本來就拒絕；刪卡工具的「清除整個卡片類型」是使用者明確下指令才執行的正常操作，不在這條的範圍。
- 測試：`backend/tests/test_corpus_rebuild_guard.py`。

### 6.4 盤點時不知道、這次查到的事

- `yuzusoft.scripts` 現在有 108,916 列：原本的 53,190 列，加上 2026-11-08 誤匯入的千戀＊萬花 55,726 列（`senren_banka_corpus_import_FEAT_2026-11-08.md` §3 有移除用的 SQL）。程式已經不讀寫舊庫，這些列沒有影響，要不要移除另外決定；這次的 `yuzusoft` 備份也包含它們。
- MySQL 帳號 `jacky917` 有兩個來源：`192.168.%`（兩個資料庫都有全部權限）與 `172.16.0.0/255.240.0.0`（Docker 內部網段，只有新庫）。

### 6.5 還沒做

- 切換後的建卡驗收（`corpus_db_rebuild_FEAT_2026-10-31.md` §8 的兩項 M3）：實際建一張核心動詞卡、一張自他動詞卡（會寫進 Anki 與卡片紀錄）。2026-11-14 已做的部分：兩條建卡管線不寫入的試跑（`--limit 1 --dry-run --game SabbatOfTheWitch`）都選得到句子；語音（23,491 個檔）與頭像（13 個檔）資料夾由 `GAME_ASSETS_ROOT`＋`game_profiles` 找得到；本機 `backend/.env` 的 8 個舊 `JP_*` 設定已刪（備份 `.env.bak_20261114_remove_legacy_jp`）。
- 備份的第二份去處與定期備份（§6.2）。
- 兩份 SQLite 的 `card_relations` 合併（M6、M7）：屬於關係圖計畫的 R2。

