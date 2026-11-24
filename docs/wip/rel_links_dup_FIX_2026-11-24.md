# FIX — 建卡管線重複寫入 `rel_links` 的 `has_context`／`has_cloze`

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-24 |
| **性質** | bug 修復（程式碼；正式庫目前沒有要清的資料） |
| **狀態** | 🚧 實作完成，待 PR |
| **嚴重度** | 🟡 卡片本身正確；關係圖譜每組卡多兩條重複的邊 |
| **影響範圍** | `backend/scripts/common/database/log_repository.py` 的 `_ensure_link`（建卡管線、刪卡工具的完整性修復會用到）；網站建卡不受影響 |
| **分支** | `fix/rel-links-dup`（從 `origin/main`） |
| **關聯文件** | `docs/wip/senren_banka_corpus_import_FEAT_2026-11-08.md` §9.14（發現經過）、`docs/wip/corpus_db_rebuild_FEAT_2026-10-31.md`（D8 的四種系統關係） |

---

## 1. 症狀

2026-11-24 用建卡管線替千戀＊萬花建了 21 組卡（驗完已刪除）。每組卡寫了 8 列 `rel_links`，其中 `core.has_context`、`core.has_cloze` 各兩列，21 組共 42 列重複。`core.example_from`、`core.teaches` 沒有重複。

- 第一列由網站的卡片 handler 寫入：`jp_verb_pair_handler.py`／`jp_core_verb_handler.py` 的 `relation_service.batch_create_relations`，`created_by` 是卡片類型。
- 第二列由建卡腳本寫 `card_gen_log` 時寫入：`log_repository.create_or_restore_record` → `_ensure_link`。

`_ensure_link` 本來就是為了這個情況設計的：先查有沒有現成的 active 列，有就沿用，沒有才新增（`rel_links` 沒有唯一鍵）。

## 2. 原因

是 MySQL 的交易快照，比對條件沒有問題。

1. 正式 MySQL 5.7.31 的隔離等級是 `REPEATABLE-READ`，連線 `autocommit=0`。同一個交易從第一次讀取開始，之後的一般 `SELECT` 都讀同一份快照。
2. 建卡腳本整個流程共用一個語料庫 session（`generate_child_cards.py` 的 `async with corpus_async_session_factory() as session`）。上一組卡 commit 之後，下一組卡在呼叫建卡 API 之前會先讀資料庫：
   - 自他動詞：`reading_filter.apply`、`get_lines`
   - 核心動詞：`get_lines`

   這時就開了新交易，快照也跟著定了。
3. 後端 handler 在另一條連線寫入並 commit `has_context`／`has_cloze`，然後才回應。
4. 腳本收到回應後呼叫 `create_or_restore_record`。它還在同一個交易裡，所以 `_ensure_link` 讀的是舊快照，查不到 handler 剛寫的列，就又新增一列。

比對條件已經核對過，兩邊一樣：`relation_type`、`src_type`／`dst_type`（`note`）、note id（都存字串）、`status`（`active`）。

**為什麼測試沒抓到**：`tests/test_log_repository_mysql.py` 的 `test_existing_handler_link_is_reused` 是在同一條連線、同一個 session 裡先寫 handler 的列再呼叫，看得到自己寫的資料。而且它用連線專屬的 `TEMPORARY` 表，模擬不了跨連線的情況。

**影響範圍**：語料庫重建後，這次是第一次跑建卡管線。正式庫沒有其他重複（2026-11-24 查：四種 active 關係的筆數都等於不重複的關係數），所以不需要清資料。

## 3. 修法

### D1 `_ensure_link` 在 MySQL 上改成鎖定讀取

查現成列的 `SELECT` 在 MySQL 上加 `FOR UPDATE`。InnoDB 的鎖定讀取讀的是最新已 commit 的資料，不受交易快照影響，所以看得到 handler 剛寫的列。SQLite（單元測試）不支援這個語法，也沒有快照問題，維持原本的查詢。

- 用 `FOR UPDATE` 而不是 `LOCK IN SHARE MODE`：找到列之後可能要 `UPDATE attrs`，先拿排他鎖可以避免共享鎖升級成排他鎖時的死結。
- 鎖會持有到 `create_or_restore_record` 結尾的 commit。查詢走 `idx_src (src_type, src_id, relation_type, status)`，只鎖這張母卡或子卡的範圍。
- 5.7 和 8.0 都支援 `FOR UPDATE`。

### 沒有採用的做法

| 做法 | 不採用的原因 |
|---|---|
| 建卡腳本在呼叫 API 前先 commit／rollback | 兩支腳本都要改，以後新的呼叫端也得記得；`_ensure_link` 本身還是會讀到舊資料 |
| `create_or_restore_record` 開頭先結束交易 | 呼叫端可能還有沒 commit 的寫入（例如完整性修復），在 repository 方法裡偷偷 commit 會出乎意料 |
| 語料庫引擎改成 `READ COMMITTED` | 影響所有用語料庫的程式，範圍太大 |
| `rel_links` 加唯一鍵 | 刪除是改 `status`，同一條關係會有多列 deleted，加不了單純的唯一鍵 |

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `backend/scripts/common/database/log_repository.py` | `_ensure_link`：MySQL 用 `FOR UPDATE` 查現成列；docstring 說明原因 |
| `backend/tests/test_corpus_repository.py` | MySQL 方言時查詢帶 `FOR UPDATE`、其他方言時不帶（CI 會跑） |
| `backend/tests/test_log_repository_mysql.py` | 跨連線的整合測試（`CORPUS_DB_TESTS=1` 才跑）：另一條連線先 commit handler 的列，原本的 session 已經有舊快照，呼叫後仍只有一列 |

## 5. 驗收

- [x] 新測試在修正前失敗、修正後通過。
- [x] `pytest tests -q` 通過（本機 `backend/.env` 多一個 `audio` 端點造成的 2 個既有失敗除外）。
- [x] `ruff check` 沒有新問題。

## 6. 實作紀錄（2026-11-24）

**程式碼**

- `log_repository.py`：新增 `_locking_read_suffix(session)`，MySQL 回 `" FOR UPDATE"`，其他方言（SQLite）和沒有 `get_bind` 的測試替身回空字串。`_ensure_link` 查現成列時接上它。
- 其他會呼叫 `_ensure_link` 的地方（完整性修復的 `repair_record`、`insert_repaired_record`）也一起受惠，不用另外改。
- 建卡腳本在本機跑，不需要重新部署後端。

**測試**

- `tests/test_corpus_repository.py`：
  - `test_link_lookup_is_a_locking_read_on_mysql`：MySQL 方言時，7 個關係查詢都以 `LIMIT 1 FOR UPDATE` 結尾。
  - `test_link_lookup_has_no_lock_clause_elsewhere`：SQLite 方言時維持 `LIMIT 1`。
- `tests/test_log_repository_mysql.py` 的 `test_handler_link_committed_after_the_snapshot_is_reused`（`CORPUS_DB_TESTS=1` 才跑）：
  - 在語料庫建一張名稱不重複的一般表 `zz_test_rel_links_<亂數>`，把 `log_repository.REL_LINKS` 換成它，測完就 `DROP`。暫存表只有建立它的連線看得到，所以這裡不能用暫存表。
  - 「腳本」連線先讀一次定下快照，「handler」連線寫入並 commit 一列 `has_context`，接著腳本呼叫 `_ensure_link`。
  - 結果：關掉修正（把 `_locking_read_suffix` 換成回空字串）時表裡有 **2 列**，測試失敗；修正後只有 **1 列**，`attrs` 補上了 `card_type`。這也實際證實了 §2 的原因。
  - 跑完查過語料庫沒有留下 `zz_test_%` 表。

跑法：

```powershell
cd backend
```

```powershell
$env:CORPUS_DB_TESTS = '1'; python -m pytest tests/test_log_repository_mysql.py -q
```

```powershell
python -m pytest tests -q
```

結果：MySQL 整合測試 5 passed；全部測試 2618 passed、8 skipped、2 failed，2 個失敗是本機 `.env` 多一個 `audio` 端點造成的（`test_llm_endpoints::test_list_endpoints_only_selectable_ones`、`test_models_menu::…[effort-token-mismatch]`），和這次無關。
