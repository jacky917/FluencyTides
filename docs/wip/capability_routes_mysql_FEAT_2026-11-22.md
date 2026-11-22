# FEAT — 能力路由設定搬到 MySQL 語料庫，網站不再使用 SQLite

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-11-22 |
| **性質** | 資料儲存位置調整 + 移除 SQLite |
| **狀態** | 🚧 實作中（P1–P3 完成，待部署正式機） |
| **範圍** | 語料庫新增 `cfg_capability_routes`（`schema.sql`）；能力路由的讀寫（`routing.py`、`capability_route_service.py`）改用語料庫連線；啟動時建表；移除網站的 SQLite 引擎、Alembic、`DATABASE_URL`、docker 的 `/app/data` 掛載；測試改用語料庫連線的替身 |
| **不動** | 能力路由的解析規則、白名單、API 與 TG `/setconfig` 的行為；本機與正式機的 `fluencytides.db` 檔案本身（留著當備份，不刪）；測試與腳本用來模擬語料庫的 SQLite（`aiosqlite` 保留） |
| **PR / 進度** | 尚未開 PR |
| **關聯文件** | `capability_routing_FEAT_2026-10-19.md`（能力路由）、`data_stores_AUDIT_2026-11-01.md`（M8：當時決定不遷移）、`corpus_db_rebuild_FEAT_2026-10-31.md`（D0 命名規則） |

---

## 1. 問題與動機

- 網站同時用兩個資料庫：MySQL 語料庫（卡片關聯、劇情等全部資料）與 SQLite（`backend/fluencytides.db`）。SQLite 現在只剩 `capability_routes` 一張表還在用；`card_relations`、`relation_types` 已在 2026-11-01 停用（[models.py:48](../../backend/app/infrastructure/database/models.py)）。
- 為了這一張表，網站要維護第二個引擎、啟動建表、Alembic 遷移、`DATABASE_URL` 設定，正式機還要掛載 `/app/data` 才不會在重建容器時遺失。
- 2026-11-22 盤點：本機與正式機的 `capability_routes` 都是 **0 列**（正式機以 SSH 唯讀查詢容器內的 `/app/data/fluencytides.db`）。目前所有用途都用 `.env` 的預設，**沒有資料要搬**。

## 2. 目標與非目標

**目標**
- G1 能力路由設定存在語料庫的 `cfg_capability_routes`；網頁「工具設定」與 TG `/setconfig` 照常讀寫。
- G2 網站完全不再建立 SQLite 連線；`DATABASE_URL`、Alembic、`create_db_and_tables` 全部移除。
- G3 語料庫讀不到時，生成照常退回 `.env`（與現在讀不到 SQLite 時相同）；管理介面回 503。

**非目標**
- 不刪 `fluencytides.db` 與裡面的舊表：使用者 2026-11-01 決定保留，當作備份。
- 不改舊腳本 `migrate_child_cards.py`：它只適用舊庫、已經跑過、不會再用（檔頭已註明）。

## 3. 設計決策

### D1 表名 `cfg_capability_routes`（使用者決定）
語料庫的表依用途加前綴（D0）。網站設定不屬於任何既有系列，新增 `cfg_`（網站設定）系列。程式用常數 `CFG_CAPABILITY_ROUTES`，不寫死表名。

### D2 本機與正式機共用一份設定（使用者決定）
兩個後端連同一個 MySQL（192.168.50.172 的 `fluencytides_corpus`），所以讀到同一份設定，在任一邊改都會兩邊生效。不加區分環境的欄位。某一邊 `.env` 白名單沒有的組合，該邊照現有規則跳過、退回下一層並記 WARNING，不會壞。

### D3 建表：`schema.sql` 是唯一來源，網站啟動時 `CREATE TABLE IF NOT EXISTS`
- DDL 寫進 `scripts/database/corpus_rebuild/schema.sql` 的 `cfg_` 區段，欄位與現在的 SQLModel 一致（`route_key` 64、`capability` 32、`provider` 64、`model` 128 可空、`effort` 16 可空、`updated_by` 64、`created_at`／`updated_at`），`UNIQUE (route_key, capability)`。
- 這張表是網站自己的設定，網站一定要有它，所以啟動時從 `schema.sql` 取出這張表的 DDL、加上 `IF NOT EXISTS` 執行（與 `init_db.py` 同一個取 DDL 的函式，移到 `app/` 共用）。建表失敗只記 ERROR、不中斷啟動：路由會退回 `.env`，管理介面回 503。
- 映像檔有包含 `scripts/`（`.dockerignore` 沒排除），容器內讀得到 `schema.sql`。
- 語料庫重建（`rebuild.py`）：加進 `DROP_ORDER` 與 `PROTECTED_TABLES`。重建只能對一個**新的空庫**執行（`.env` 的 `MYSQL_DATABASE` 已是目標庫時直接拒絕），而 `PROTECTED_TABLES` 只檢查目標庫，所以它擋不住「設定留在舊庫」這件事。因此重建寫完新庫後，`carry_site_settings` 會從 `.env` 的 `MYSQL_DATABASE`（目前使用中的語料庫）把 `SITE_TABLES` 以 `INSERT … SELECT *` 整張複製過去（保留 id；兩邊都由 `schema.sql` 建立，結構相同，不同時報錯、整次重建回復）。來源還沒有這張表時略過，主程式印警告。切換 `MYSQL_DATABASE` 後設定照舊。
- 時間戳：MySQL 的 `DATETIME` 不存時區，`CURRENT_TIMESTAMP` 又依伺服器時區，所以 `created_at`／`updated_at` 由網站寫入 UTC（`models.utc_now`，模型的 `default`／`onupdate`）；欄位的 `DEFAULT CURRENT_TIMESTAMP` 只給手動 INSERT 的列用。

### D4 讀寫改走語料庫連線
- `CapabilityRouteSetting` 仍是 SQLModel，只改 `__tablename__`。讀取端 `routing._fetch_choices` 與寫入端 `capability_route_service._session_factory` 改用 `corpus_async_session_factory`，同樣在呼叫時才讀（測試可以換掉）。
- 語料庫引擎沒建起來（`corpus_async_session_factory is None`）時拋 `ConnectionError`（`OSError` 的子類別）。它已在 `DATABASE_ERRORS` 內，所以生成退回 `.env`，管理介面回 503，不必新增錯誤處理路徑。

### D5 移除 SQLite（使用者決定）
- 刪除 `app/infrastructure/database/database.py`（SQLite 引擎、`create_db_and_tables`、舊表偵測、`get_async_session`），以及 `dependencies.get_db_session`（沒有任何 API 使用）。
- 刪除停用的 `CardRelation`、`RelationType` 模型（程式不再讀寫）。
- 刪除 `alembic/`、`alembic.ini`、`requirements` 的 `alembic`，以及 Alembic 遷移的測試。
- 刪除 `Settings.DATABASE_URL` 與它的路徑解析，`.env`／`.env.example` 拿掉 `DATABASE_URL`。`extra="ignore"`，所以舊 `.env` 留著這一行也不會出錯。
- `docker-compose.yml` 拿掉 `DATABASE_URL` 與 `/app/data` 掛載。主機上的 `/DATA/AppData/FluencyTides/backend/data/fluencytides.db` 留著不動。
- `aiosqlite` 保留：測試與 `import_cards_*.py --db-url` 用 SQLite 模擬語料庫。

## 4. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `scripts/database/corpus_rebuild/schema.sql` | 新增 `cfg_` 區段與 `cfg_capability_routes` |
| `app/infrastructure/database/corpus_tables.py` | `CFG_CAPABILITY_ROUTES` 常數；前綴說明加 `cfg_` |
| `app/infrastructure/database/corpus_schema.py`（新） | 從 `schema.sql` 取單表 DDL（`table_ddl`）、啟動建網站自己的表（`ensure_site_tables`） |
| `app/infrastructure/database/corpus_database.py` | 新增 `require_corpus_session_factory()`（沒有連線時拋 `ConnectionError`）；說明文字更新 |
| `app/infrastructure/database/models.py` | 只留 `CapabilityRouteSetting`，表名改成常數；時間戳改由網站寫入 UTC（`utc_now`） |
| `app/infrastructure/database/database.py` | 刪除 |
| `app/infrastructure/llm/routing.py`、`app/services/capability_route_service.py` | 改用語料庫連線；說明文字的表名 |
| `app/main.py` | 拿掉 SQLite 建表與釋放；啟動時建 `cfg_` 表 |
| `app/core/dependencies.py` | 拿掉 `get_db_session` |
| `app/core/config.py` | 拿掉 `DATABASE_URL` 與路徑解析 |
| `scripts/common/database/init_db.py` | 改用共用的 `table_ddl` |
| `scripts/database/corpus_rebuild/rebuild.py` | `DROP_ORDER`、`PROTECTED_TABLES` 加入新表；重建後從使用中的語料庫複製網站設定（`carry_site_settings`） |
| `alembic/`、`alembic.ini`、`requirements.txt`、`requirements.lock` | 移除 Alembic |
| `.env.example`、`docker-compose.yml` | 拿掉 `DATABASE_URL` 與 `/app/data` |
| `README.md` | 資料庫說明改成「只用 MySQL 語料庫」 |

### 測試
- `tests/conftest.py`：`llm_route_db` 改成替換 `corpus_async_session_factory`（暫存 SQLite 只建這張表）。
- 刪除 `tests/test_capability_routes_migration.py`（Alembic 遷移）。
- 新增：`table_ddl` 取得 `cfg_capability_routes`；`schema.sql` 與模型的欄位名稱、型別、字串長度、可否為空一致；`ensure_site_tables` 執行的是 `IF NOT EXISTS`、失敗不中斷啟動；語料庫沒有連線時路由退回 `.env`、管理介面回 503；重建時複製網站設定（來源沒有表時略過、來源與目標相同時不動）。

## 5. 實作順序

1. P1 建表與讀寫改向（D1、D3、D4），跑能力路由相關測試。
2. P2 移除 SQLite 與 Alembic（D5），跑全部測試。
3. P3 本機實測：啟動後端，確認 MySQL 建出表；網頁工具設定寫一筆、讀回、恢復預設。
4. P4 部署正式機，確認啟動 log 與「工具設定」頁。

## 6. 風險與未知

- **共用設定會互相影響**（D2）：在本機試模型會同時改到正式機。應對：這是使用者選擇的行為；試模型時記得改回，或改用 `.env`。
- **另一台要重開才生效**（已知限制，2026-11-22 使用者決定先不修）：路由設定讀出後快取在記憶體、不會過期，只有同一台後端寫入時才清快取。所以在正式機（TG `/setconfig`）改的設定，本機要重開後端才會讀到，反之亦然。要修的話：快取設 30–60 秒的有效期，或每次先比對 `MAX(updated_at)` 與列數。
- **語料庫斷線時**：以前 SQLite 是本機檔案，幾乎不會讀不到；改成 MySQL 後，MySQL 斷線期間路由退回 `.env`，管理介面回 503。既有的「故障開始記 WARNING、恢復記 INFO」照常運作。
- **啟動時建表失敗**（例如 MySQL 沒開）：只記 ERROR，下次啟動再建；這段期間路由退回 `.env`。
- **權限**：網站用的 MySQL 帳號需要 `CREATE` 權限。應對：本機實測先確認；正式機與本機用同一個帳號。
- **重建不會帶走網站寫進語料庫的其他資料**（既有問題，不在這次範圍）：`rebuild.py` 的內容來自舊庫 `yuzusoft`，2026-11-01 之後網站寫進 `fluencytides_corpus` 的卡片關聯 `rel_links` 等，重建出的新庫不會有。這次只補上網站設定（`carry_site_settings`）；其他表要沿用時另外設計合併方式。

## 7. 驗收標準

- [x] 本機建表後，MySQL 有 `cfg_capability_routes`，可重複執行；`backend/fluencytides.db` 沒被動到（2026-11-22，以 `ensure_site_tables` 實測，未啟動整個後端，避免本機綁走正式機的 Telegram webhook）
- [x] 經服務層（網頁與 TG 共用的 `capability_route_service`）寫一筆 `jp_verb_pair` 路由，MySQL 看得到、解析變成 `route`；刪除後該列消失、退回 `.env`（2026-11-22 實測）
- [x] 全部測試通過（除了已知與本機 `.env` 有關的兩個：`test_llm_endpoints`、`test_models_menu`）
- [ ] 正式機部署後健康檢查正常，「工具設定」頁能列出路由
