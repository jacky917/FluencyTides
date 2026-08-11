# 15. 全項目 Bug 掃描報告（2026-08-09）

> 掃描方式：6 個並行審查代理分區掃描（app/core+schemas、app/api+bot、app/services、
> app/infrastructure、backend/scripts、frontend+部署/CI），只回報未修復。
> 範圍排除：`old/` 目錄、`.agent/` 工具目錄、config 中已知刻意硬編碼的 Windows 素材路徑。
> 同場作業：全項目補齊中英雙語 Google Style docstring（137 檔，已隨 PR #1 合入 main）。
>
> 統計：**High 11 / Med 28 / Low 23**，共 62 條（S001–S062，編號即引用單位；
> 部分條目涵蓋多個檔案位置，如 S005 含 6 檔）。

## 修復狀態總覽

### 🔴 High：**11/11 全數修復**（S001 於 2026-08-09；S002–S011 於 2026-08-10）

| 編號 | 狀態 | 修復摘要 |
|---|---|---|
| S001 | ✅ | `parse_field_string` 解析失敗改拋 `AnkiFieldCorruptedError`，不再靜默清空 |
| S002 | ✅ | 新增 `AnkiNoteTransaction` 補償式交易，母卡＋子卡整組回滾 |
| S003 | ✅ | VerbPair／CoreVerb 的 Context＋Cloze＋母卡回寫納入同一交易；圖譜關聯改為非致命 |
| S004 | ✅ | 補 `import re`；CI 加 Ruff F821 防線根治此類問題 |
| S005 | ✅ | 6 檔改用「向上尋找 `app/`」的深度無關 bootstrap，衍生路徑一併改由其推導 |
| S006 | ✅ | 2 檔移除硬編碼個人絕對路徑，改用同一 bootstrap |
| S007 | ✅ | DDL 補兩欄 ＋ 新增 `_ensure_columns()` 冪等補齊既有資料表 |
| S008 | ✅ | 讀寫改用兩條獨立 PyMySQL 連線，僅 commit 寫入端，兩條都關閉 |
| S009 | ✅ | 改由 nginx 樣板以 envsubst 注入 `X-API-Key`，金鑰不進前端 bundle |
| S010 | ✅ | 新增 `anki_field_to_tg_text()`；狀態改為訊息送達後才設定，並加降級重送 |
| S011 | ✅ | `_invoke` 支援 per-request `_timeout`，`sync()` 不再竄改共享 client 狀態 |

**驗證**：新增 `tests/test_high_severity_fixes.py`（20 個測試）；全套 38 個測試通過；
`ruff check app scripts tests` 通過（F821 歸零）；`compileall` app＋scripts 通過；
前端 `tsc -b` 通過；`app.main` 匯入煙霧測試通過（7 個 handler 正常註冊）。

### 🟡 Med：1/28 修復
- **S013** ✅ 隨 STT 雙模式實作：`voice.py` transcript 一律 escape、LLM feedback 先截斷再
  escape、stt_diff 安全標記不轉義不截斷。
- **S014** ⏳ 未修，但修復所需的共用工具（`app/bot/utils/formatting.py` 的
  `anki_field_to_tg_text` / `escape_tg`）已隨 S010 建好，替換各處呼叫即可。

### 🟢 Low：0/23 修復
其中 F401 未用匯入等 80 件整潔性問題已由 `backend/ruff.toml` 記錄在案（暫不阻擋 CI）。

### 修復過程中新發現
- **S063**（High，已一併修復）：`scripts/local_anki/JP_VerbPair/import_models.py`
  **檔案根本不存在**，但 `migrate_master_cards.py` 直接 `import` 它，
  且 `JP_CoreVerb/import_models.py` 的註解也宣告「`JP_Context_Dark` 由 VerbPair 側管理」。
  即使修好 S004／S005，該遷移腳本仍會 `ModuleNotFoundError`。已依 JP_CoreVerb 的既有慣例
  補回該模組（匯入 `JP_VerbPair_Master_Dark`／`JP_Context_Dark`／`JP_VerbPair_Cloze_Dark`）。
- **S064**（Low，未修）：`scripts/local_anki/Expression_Correction/20260620_migrate_and_update_expression.py`
  另有一處 `parent.parent.parent` 硬算路徑（在 `update_templates_and_css()` 內），
  已於本次一併改為由 `_BACKEND_DIR` 推導。

與 STT 計畫（[14_STT_Dual_Mode_Evaluator_Plan.md](14_STT_Dual_Mode_Evaluator_Plan.md)）
的交集見該文檔 §2.9（S001 為前置必修，已完成）。

---

## 🔴 High（11 條，涵蓋 17 處檔案位置）

> 本節每條含：現象 → 根因（代碼層級）→ 觸發條件 → 影響 → **修復方案**（含代碼草案）
> → 驗證方式 → 工作量估計。Med/Low 維持簡表，待排程時再展開。

### 資料遺失／損毀

#### S001 ✅ 已修復 · JSON 欄位解析失敗導致靜默清空
`backend/app/infrastructure/anki/json_modifier.py`

**現象**：`parse_field_string` 在 JSON 解析失敗時回傳 `[]`，而 `append_to_list` 的實作是
「讀取 → append → 整欄覆寫」，於是損毀的欄位會被**整段覆蓋為只含新元素的單元素陣列**，
原有的全部錄音歷史／參考範本永久消失，且過程零錯誤訊息。

**修復內容（2026-08-09 已實作）**：解析失敗或結果非 list 時改拋 `AnkiFieldCorruptedError`
（新增於 `core/exceptions/infrastructure.py`），中止整個寫入流程並回報使用者手動修復；
空欄位仍合法回傳 `[]`。已補 3 個單元測試（損毀 JSON、非陣列 JSON、空欄位）。

**後續注意**：此修復把「靜默資料遺失」轉為「明確拋錯」，因此原本被掩蓋的失敗路徑會浮現
到 S002/S003 的流程中——這是 S003 必須一併處理的直接原因（見下）。

---

#### S002 ✅ 已修復 · 外語糾錯多卡建立無交易性，失敗留下孤兒母卡
`backend/app/services/task_handlers/expression_handler.py:242-298`

**現象**：`execute_create` 先建立母卡（`Expression_Master_Dark`），再以 for 迴圈逐張建立
子卡（`Expression_Micro_Dark`）。任一子卡 `create_note` 拋例外時，例外直接向上傳播，
**已建立的母卡與前幾張子卡不會被清除，也不會回報給呼叫端**（`created_ids` 隨堆疊消失）。

**根因**：Anki 沒有交易機制，而現有代碼把「一組原子性的知識群組」寫成多次獨立的
`create_note` 呼叫，中間沒有任何補償邏輯。子卡呼叫（第 290 行）未傳 `allow_duplicate`，
預設為 `False`，因此**內容重複就會拋 `DuplicateCardError`**——這是最常見的觸發點。

**觸發條件**：使用者在 TG 送出糾錯預覽並確認建卡，其中第 2 張微技能子卡的
`Target_Phrase` 與既有卡片重複 → 母卡＋第 1 張子卡已寫入 Anki，流程中止並回報失敗 →
使用者以為沒建成、再按一次 → 又一張母卡誕生。

**影響**：Anki 出現無子卡的孤兒母卡與內容重複的半套群組；因母卡的 `Child_Cards_Data`
永遠是空陣列、子卡靠 `Master_Note_ID` 反查，孤兒不易被察覺，需人工比對清理。

**修復方案**：新增可重複使用的「補償式交易」輔助類別，把多卡建立包成一個單位——

```python
# 新檔：backend/app/services/task_handlers/shared/anki_transaction.py
class AnkiNoteTransaction:
    """補償式交易：離開時若有例外，反序刪除本次已建立的所有筆記。

    Anki 無原生交易，改以「記錄已建 ID → 失敗時反向刪除」達成近似原子性。
    """

    def __init__(self, card_service: "CardService") -> None:
        self._card_service = card_service
        self.created_ids: list[int] = []

    async def create_note(self, **kwargs: object) -> int:
        note_id = await self._card_service.create_note(**kwargs)  # type: ignore[arg-type]
        self.created_ids.append(note_id)
        return note_id

    async def __aenter__(self) -> "AnkiNoteTransaction":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            return False
        for note_id in reversed(self.created_ids):
            try:
                await self._card_service.delete_note(note_id)
            except Exception:  # 回滾失敗必須大聲記錄，供人工清理
                logger.error("回滾失敗，殘留孤兒筆記 note_id=%s（請手動刪除）", note_id)
        return False  # 不吞例外，讓上層照常收到原始錯誤
```

`card_service.delete_note()` 已存在（`card_service.py:249`），不需新增基礎設施。
`expression_handler.execute_create` 改為：

```python
async with AnkiNoteTransaction(card_service) as tx:
    master_note_id = await tx.create_note(deck_name=master_deck, ...)
    for idx, (card_type, sub_deck_name, mp) in enumerate(all_micro_entries):
        ...
        await tx.create_note(deck_name=deck_name_full, ...)
    return tx.created_ids
```

**驗證**：單元測試以 mock `card_service`，令第 2 次 `create_note` 拋 `DuplicateCardError`，
斷言 `delete_note` 被以「子卡 → 母卡」的反序呼叫、且原例外仍向上拋出；另測回滾本身失敗
時不遮蔽原始例外且有 ERROR 日誌。

**工作量**：新檔約 40 行 ＋ handler 改寫約 10 行 ＋ 測試約 60 行。半天內可完成。

---

#### S003 ✅ 已修復 · 語料卡片群組建立無交易性（VerbPair / CoreVerb）
`backend/app/services/task_handlers/jp_verb_pair_handler.py:231-346`
（`jp_core_verb_handler.py` 同型）

**現象**：流程為 ①`position_cloze` 驗證 → ②建 Context 卡 → ③建 Cloze 卡 →
④`append_to_list` 回寫母卡 `*_Data_JSON` → ⑤兩次 `create_relation` 寫圖譜。
②之後任一步失敗，前面已建立的卡片都不會回收。

**根因**：現有代碼**只對挖空定位做了 fail-fast**（第 198-217 行的註解明確說明「先確認挖空
成功再建卡，避免孤兒 Context 卡」），但這個保護只涵蓋 LLM 產出問題，**不涵蓋建卡與寫回
本身的 IO 失敗**。三種殘留樣態：

| 失敗點 | 殘留狀態 | 可見性 |
|---|---|---|
| ③ Cloze 建立失敗 | 孤兒 Context 卡（母卡無引用） | 低，需查 deck 才發現 |
| ④ 回寫母卡失敗 | Context＋Cloze 皆成孤兒 | 低 |
| ⑤ 關聯建立失敗 | 卡片與母卡 JSON 都在，僅圖譜缺邊 | 極低，知識圖譜靜默缺角 |

**觸發條件（已升高）**：④ 的 `append_to_list` 在 **S001 修復後會對損毀欄位主動拋
`AnkiFieldCorruptedError`**——原本會靜默「成功」的路徑，現在變成真實的失敗路徑。加上
AnkiConnect 於兩次建卡之間斷線、Anki 使用者手動關閉程式等情境，機率不低。

**影響**：批次生成腳本（`scripts/fastapi_client/JP_VerbPair/generate_child_cards.py`）
一次跑數十張卡，中途失敗會累積孤兒；孤兒卡片又會被 `dedup_manager` 記為已生成，導致該
句永久缺卡（與 S034 同源的資料一致性問題）。

**修復方案**：沿用 S002 的 `AnkiNoteTransaction`，將 ②③④ 納入同一交易；⑤ 的圖譜寫入
本身有 DB 交易保護，維持最後執行。關鍵是**把 ④ 也納入回滾範圍**：

```python
async with AnkiNoteTransaction(card_service) as tx:
    new_context_id = await tx.create_note(deck_name=context_deck_name, ...)
    new_cloze_id = await tx.create_note(deck_name=cloze_deck_name, ...)
    await AnkiJsonFieldManager.append_to_list(
        card_service, master_note_id, target_field, new_example_item
    )  # 失敗 → __aexit__ 刪除上面兩張卡，母卡回到未被汙染的狀態
# 交易成功後才寫圖譜關聯
await relation_service.create_relation(...)
```

⑤ 失敗時卡片與母卡 JSON 已提交，屬「可事後補救」等級：改為捕獲例外後記 WARNING 並把
`relation_failed: True` 放進回傳值，由 `/sync` 既有的孤兒清理機制後續修正，不觸發回滾
（避免為了圖譜一致性刪掉使用者已看得到的卡片）。

**驗證**：以 mock 令 `append_to_list` 拋 `AnkiFieldCorruptedError`，斷言兩張子卡被刪除、
母卡欄位未被寫入；另測 `create_relation` 失敗時卡片保留且回傳含失敗旗標。

**工作量**：兩個 handler 各改約 15 行（共用 S002 的類別）＋測試約 80 行。半天。

### 啟動／執行必炸（腳本類）

> S004–S006 屬同一類「搬家後沒同步」的低級錯誤，但後果是腳本 100% 無法啟動。
> 除逐檔修正外，**必須加上系統性防線**（見本節末「共通防線」），否則下次搬檔會重演。

#### S004 ✅ 已修復 · 缺少 `import re`，遷移腳本必定崩潰
`backend/scripts/local_anki/JP_VerbPair/migrate_master_cards.py:26`

**現象**：`clean_html()` 內使用 `re.compile` / `re.sub`，但整個檔案的 import 區塊
（第 1-23 行）沒有 `import re`。

**觸發條件**：只要 `find_notes` 找到任何一張 M-Both 母卡，處理第一張時就
`NameError: name 're' is not defined`——即腳本從未被成功執行過。

**修復方案**：於 import 區塊補 `import re`。

**驗證與系統性防線**：單純補 import 無法防止再犯，應在 CI 加入 **Ruff 的 `F821`
（undefined-name）規則**掃描整個 `backend/`，這類「用了沒 import」的錯誤會在
lint 階段就被擋下：

```toml
# backend/pyproject.toml（或 ruff.toml）
[tool.ruff.lint]
select = ["F"]   # 含 F821 undefined-name、F401 unused-import
```

**工作量**：修正 1 行；CI 規則約 10 分鐘（順帶會掃出其他潛在同類問題）。

---

#### S005 ✅ 已修復 · `sys.path` 深度算錯，6 個腳本無法匯入
**現象**：各腳本以 `Path(__file__).resolve().parents[N]` 硬算 backend 根目錄，
檔案搬家後 N 沒跟著改，導致 `sys.path` 指向錯誤層級，`import scripts.common.env`
或 `from app.core.config import settings` 立即 `ModuleNotFoundError`。

| 檔案 | 現值 | 應為 | 附帶影響 |
|---|---|---|---|
| `local_anki/update_tg_bot_links.py:26` | `parents[3]` | `parents[2]` | — |
| `local_anki/JP_VerbPair/migrate_master_cards.py:15` | `parents[4]` | `parents[3]` | — |
| `local_anki/Expression_Correction/20260620_migrate_and_update_expression.py:16` | `parents[4]` | `parents[3]` | — |
| `local_anki/Expression_Correction/generate_expression_cards.py:21` | `parents[4]` | `parents[3]` | 同檔 49 行已用正確的 `parents[3]`，自相矛盾 |
| `common/template_validators/speaking_coach_dark_validator.py:27,76` | `parent×3` | `parent×4` | `model_dir` 一併指向不存在的 `scripts/app/anki_models` |
| `database/MySQL/import_sql_dumps.py:14` | `parent×3` | `parent×4` | `sql_dir` 一併指錯 |

**根因**：用「相對層數」表達「找專案根目錄」這個語意，層數與檔案位置強耦合。

**修復方案**：**不要修數字**，改用與深度無關的「向上尋找特徵目錄」寫法。因為此段
本身就是 bootstrap（尚未能 import 專案模組），必須是自足的 3 行片段：

```python
# 統一 bootstrap：向上尋找含 app/ 的目錄即為 backend 根，與檔案深度無關
_BACKEND_DIR = next(
    p for p in Path(__file__).resolve().parents if (p / "app").is_dir()
)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))
```

同時把 `model_dir` / `sql_dir` 等衍生路徑一律改為由 `_BACKEND_DIR` 推導，消除第二處硬編碼。

**驗證**：新增測試 `tests/test_scripts_bootstrap.py`，掃描 `backend/scripts/**/*.py`，
斷言**沒有任何檔案**再出現 `parents[` 或 `parent.parent.parent` 形式的 bootstrap
（正則比對），從機制上防止回歸。

**工作量**：6 檔各改 3-5 行 ＋ 防回歸測試約 30 行。約 1 小時。

---

#### S006 ✅ 已修復 · 硬編碼個人絕對路徑
`backend/scripts/database/Elasticsearch/test_analyze.py:13`、`test_esql_search.py:12`

**現象**：`sys.path.insert(0, r'c:\Users\forip\Desktop\...')` 直接寫死開發者本機路徑。

**影響**：任何其他機器（含伺服器、CI、同事電腦）執行即 `ModuleNotFoundError`；
即使在原機器上，把專案搬到別的資料夾也會壞。

**修復方案**：換成 S005 的同一段 bootstrap 片段，並納入同一條防回歸測試
（正則另加 `sys.path.insert(0, r'` 開頭為絕對路徑的樣式）。

**工作量**：2 檔各 3 行，10 分鐘。

---

#### S007 ✅ 已修復 · DDL 與 Repository 欄位不一致，新建表即不可用
`backend/scripts/common/database/init_db.py:40-69` vs
`backend/scripts/common/database/log_repository.py:37,162-235`

**現象**：`init_db.py` 的 `CREATE TABLE generated_sentences_log` 只定義到
`delete_count` 為止，**缺少 `failure_count` 與 `llm_model` 兩欄**；但
`log_repository.py` 三個方法都在讀寫它們：
- `get_record`（第 37 行）：`SELECT ... failure_count ...`
- `increment_failure_count`（第 179-193 行）：`INSERT ... (llm_model, failure_count)`
- `create_or_restore_record`（第 212-235 行）：同上並 `failure_count = 0`

**觸發條件**：在乾淨環境（新機器、新資料庫、CI）跑 `init_db.py` 建表後，任何一次生成
流程呼叫 `get_record` 就 `Unknown column 'failure_count' in field list`。現有開發機能運作，
是因為那張表是更早以其他方式（手動 ALTER 或舊版腳本）建立的——**schema 只存在於某台機器上**。

**修復方案（兩段式，缺一不可）**：

1. **補 DDL**（給全新環境）——依 repository 用法推得型別：
```sql
failure_count INT NOT NULL DEFAULT 0 COMMENT 'LLM 生成連續失敗次數，達門檻則跳過',
llm_model VARCHAR(255) DEFAULT NULL COMMENT '最後一次生成使用的 LLM 模型名稱',
```

2. **補冪等遷移**（給既有環境）——關鍵：DDL 用的是 `CREATE TABLE IF NOT EXISTS`，
   **既有資料庫不會因為改了 DDL 就長出新欄位**。MySQL 8 不支援
   `ADD COLUMN IF NOT EXISTS`，需先查 `information_schema`：

```python
async def _ensure_columns(session) -> None:
    """為既有資料表補上缺少的欄位（冪等）。"""
    existing = {
        r[0] for r in (await session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'generated_sentences_log'"
        ))).all()
    }
    for col, ddl in (
        ("failure_count", "ADD COLUMN failure_count INT NOT NULL DEFAULT 0"),
        ("llm_model", "ADD COLUMN llm_model VARCHAR(255) DEFAULT NULL"),
    ):
        if col not in existing:
            await session.execute(text(f"ALTER TABLE generated_sentences_log {ddl}"))
```

**驗證**：對空資料庫跑 `init_db.py` 後，執行一次 `get_record` 與
`increment_failure_count` 應成功；重複執行 `init_db.py` 兩次不應報錯（冪等）。

**工作量**：約 30 行，1 小時（含在測試 DB 上實跑驗證）。

---

#### S008 ✅ 已修復 · SSCursor 與寫入共用連線，大量資料必中斷
`backend/scripts/database/MySQL/JP_VerbPair/build_nlp_index.py:88-135`

**現象**：同一條 PyMySQL 連線上同時開了 `SSCursor`（server-side streaming 讀）與一般
`cursor`（批次寫），且在讀取尚未結束時就 `conn.commit()`（第 123 行）。

**根因**：第 89 行的註解自己寫著「SSCursor 必須單獨佔用一個連線，因此我們需要兩個
cursor」——**結論下對了但實作錯了**：兩個 cursor 仍來自同一個 `conn`。MySQL 協定在
streaming 結果集未讀完前，同一連線不得發送其他語句，否則觸發
`Commands out of sync; you can't run this command now`，或結果集被截斷。

**觸發條件**：腳本以 `BATCH_SIZE = 5000` 累積後寫入。只要 `サノバウィッチ` 的動詞索引
超過 5000 筆（實際遠超），第一次批次寫入就會在讀取流中途插入 INSERT ＋ COMMIT。

**影響**：索引建到一半就中斷或靜默截斷 → `dialogue_terms_index` 不完整 → 下游
`generate_child_cards.py` 檢索不到句子，症狀表現為「這個動詞找不到語料」，難以聯想到根因。

**修復方案**：讀寫各用獨立連線，寫入端獨立 commit：

```python
read_conn = pymysql.connect(**db_config)
write_conn = pymysql.connect(**db_config)
try:
    with read_conn.cursor(SSCursor) as read_cursor, write_conn.cursor() as write_cursor:
        read_cursor.execute(read_sql)
        for row in read_cursor:
            ...
            if len(batch_data) >= BATCH_SIZE:
                write_cursor.executemany(insert_sql, batch_data)
                write_conn.commit()      # 只 commit 寫入連線，讀取流不受影響
                batch_data.clear()
        if batch_data:
            write_cursor.executemany(insert_sql, batch_data)
            write_conn.commit()
finally:
    read_conn.close()
    write_conn.close()
```

**驗證**：在完整語料上實跑一次，比對 `SELECT COUNT(*) FROM dialogue_terms_index` 與腳本
輸出的 `total_indexed` 是否一致（目前應會不一致或中途報錯）。

**工作量**：約 15 行，30 分鐘 ＋ 一次完整重建索引的執行時間。

---

#### 共通防線（S004–S006 的根治）
1. CI 加入 `ruff check backend/ --select F`（擋 undefined-name / unused-import）。
2. 新增 `tests/test_scripts_bootstrap.py`：正則掃描所有腳本，禁止 `parents[數字]`、
   `parent.parent.parent`、絕對路徑 `sys.path.insert` 三種寫法。
3. 在 `docs/` 或 `CLAUDE.md` 記錄「腳本 bootstrap 一律使用向上尋找 `app/` 的片段」。

### 運行時缺陷

#### S009 ✅ 已修復 · 前端不帶 API Key，啟用認證後 Web UI 全面 401
`frontend/src/api/client.ts:34-39`

**現象**：`apiClient` 只設定了 `Content-Type`，**沒有任何認證 header**；而後端
`app/api/handlers.py` 與 `relations.py` 兩個 router 都掛了
`Depends(verify_api_key)`，該依賴讀取 `X-API-Key`（`core/auth.py:39-43`）。

**為何現在沒發作**：`verify_api_key` 在 `API_SECRET_KEY` 未設定時會「跳過認證」
（`auth.py:70-74`，fail-open）。所以本機與目前部署能用，**只是因為金鑰沒設**——
一旦按 `.env.example` 的指示填上 `API_SECRET_KEY`，整個 Web UI 立刻全滅
（`/api/health` 除外，它沒掛認證）。

**影響**：安全性與可用性二選一的死結：設金鑰 → 前端全壞；不設 → API 對內網全開放。

**修復方案（推薦：nginx 注入，金鑰不進瀏覽器）**

前端已由 nginx 反代 `/api/`（`frontend/nginx.conf` 第 22 行），因此正解是**在反向代理層
補上 header**，瀏覽器端完全不接觸金鑰——避免把密鑰打包進 JS bundle（任何人按 F12 都看得到）：

```nginx
location /api/ {
    proxy_pass http://fluencytides-backend:8000;
    proxy_set_header X-API-Key "${API_SECRET_KEY}";   # 由 envsubst 於容器啟動時代入
    ...
}
```
`nginx:alpine` 內建 envsubst 樣板機制：把檔案改名為
`/etc/nginx/templates/default.conf.template`，並在 compose 的 frontend 服務加上
`environment: [API_SECRET_KEY]`，容器啟動時自動生成實際設定檔。

**替代方案（僅限本機 dev，Vite proxy 場景）**：在 axios 加 request 攔截器讀
`import.meta.env.VITE_API_KEY`。**須明確接受**金鑰會被打包進前端資源的風險，因此僅適用於
不對外的開發環境。

**驗證**：`.env` 設定 `API_SECRET_KEY` 後，Dashboard／CardGenerator／KnowledgeGraph
三頁皆能正常載入；用瀏覽器直接打 `http://<host>:8000/api/v1/handlers`（繞過 nginx）
應回 401——證明保護生效且僅由代理層放行。

**工作量**：nginx 樣板化 ＋ compose 環境變數約 20 行，1 小時（含實測）。

---

#### S010 ✅ 已修復 · 卡片內容未轉義注入 Telegram HTML，使用者卡在錄音狀態
`backend/app/bot/handlers/commands.py:143,156-164`

**現象**：從 Anki 讀出的第一個欄位值 `display_text` 未經 `html.escape`，直接以 f-string
插入 `<blockquote>{display_text}</blockquote>`，而 Bot 預設 `parse_mode=HTML`。

**根因**：**Anki 欄位本身就是 HTML**（`<div>`、`<br>`、furigana 的 `<ruby>`、樣式
`<span>` 等），與 Telegram 只允許 `<b>/<i>/<s>/<u>/<code>/<pre>/<a>` 等少數標籤的白名單
天然衝突；含 `<` 或未閉合標籤時 `message.answer` 直接拋 `TelegramBadRequest`。

**觸發條件的惡性順序**：狀態在第 125-132 行**先**被設為 `recording`，提示訊息在第 156 行
**後**發送。因此訊息發送失敗時：狀態已進入錄音模式、使用者卻沒收到任何提示 →
使用者不知道要發語音，或發了語音卻困惑（此時反而能正常運作）。體感是「點了按鈕沒反應」。

**同族問題**：S014（`commands.py:80,90` / `messages.py:100-102` /
`fsm/vocabulary_fsm.py:132`）是同一個根因的不同位置，應一併處理。

**修復方案**：新增共用格式化工具，先剝 HTML 標籤再轉義，最後才截斷：

```python
# 新檔：backend/app/bot/utils/formatting.py
_TAG_RE = re.compile(r"<[^>]+>")

def anki_field_to_tg_text(raw: str, limit: int = 300) -> str:
    """把 Anki 欄位（含 HTML）轉為可安全嵌入 Telegram HTML 訊息的純文字。"""
    text = _TAG_RE.sub("", html.unescape(raw)).strip()   # 去標籤、還原實體
    if len(text) > limit:
        text = text[:limit] + "..."
    return html.escape(text)                              # 最後統一轉義
```
順序很關鍵：**先截斷再轉義**，避免截斷點切在 `&amp;` 這類實體中間產生半截實體。
`commands.py` 改為 `display_text = anki_field_to_tg_text(raw_value)`，S014 各處同樣替換。

**防禦性補強**：在 `commands.py` 把「設定狀態」移到「訊息發送成功之後」，或以
try/except 包住 `answer`，失敗時清除狀態並回覆純文字的降級訊息——避免任何未來的
格式問題再度造成「狀態已設、使用者無感知」。

**驗證**：單元測試餵入 `<div>あ&nbsp;<b>い</b></div>`、`3 < 5 & 7`、超長字串，斷言輸出不含
`<`/`&` 未轉義字元且長度受限；整合面在 TG 用一張含 HTML 的卡片實測按鈕。

**工作量**：工具檔約 25 行 ＋ 4 處替換 ＋ 測試約 40 行。半天（含 S014 一併修）。

---

#### S011 ✅ 已修復 · `sync()` 竄改共享 timeout，併發下污染其他請求
`backend/app/infrastructure/anki/client.py:1184-1195`

**現象**：
```python
old_timeout = self._client.timeout
try:
    self._client.timeout = httpx.Timeout(self.SYNC_TIMEOUT)   # 改動共享狀態
    await self._invoke("sync")
finally:
    self._client.timeout = old_timeout                        # 還原
```
`self._client` 是整個應用共用的 `httpx.AsyncClient` singleton，`timeout` 是**實例層級**
屬性；在 `await` 期間其他協程照常使用同一個 client。

**根因**：把「單一請求的超時需求」實作成「全域狀態的暫時修改」，在非同步併發下必然競態。

**具體競態序列**：
1. 協程 A 呼叫 `sync()`，把 timeout 改成 `SYNC_TIMEOUT`（較長）。
2. 協程 B 此時發出一般請求（如 `find_notes`）→ 繼承了過長的 timeout，Anki 沒回應時
   B 會卡住遠超預期的時間。
3. A 完成，`finally` 把 timeout 還原成 30s；若此時 A 觸發的其他慢請求仍在途中，反而被縮短。

**觸發條件**：Webhook 模式下 `BackgroundTasks` 併發處理多個 Update（見 S012），
而 `sync()` 被 `create_model`、`modelFieldReposition`、voice handler 等多處呼叫，重疊窗口大。

**修復方案**：改為 per-request timeout，完全不碰共享狀態。`_invoke` 增加私有關鍵字參數
（前綴底線避免與 AnkiConnect 的 API 參數命名衝突）：

```python
async def _invoke(self, action: str, *, _timeout: float | None = None, **params: object) -> object:
    ...
    response = await self._client.post(
        self._url,
        json=req.model_dump(exclude_none=True),
        timeout=_timeout if _timeout is not None else self._timeout,   # 單次請求覆寫
    )
```
`sync()` 則簡化為：
```python
await self._invoke("sync", _timeout=self.SYNC_TIMEOUT)
```
並刪除 `old_timeout` / `finally` 還原邏輯。順帶修正超時錯誤訊息（第 216 行）使用實際生效
的 timeout 值而非永遠印 `self._timeout`。

**注意**：`_invoke` 的 `**params` 會原樣送進 AnkiConnect 的 `params`，因此新參數**必須**是
keyword-only 且被簽名顯式接住，不可落入 `**params`（否則會被當成 API 參數送出）。

**驗證**：單元測試以 mock `AsyncClient.post` 斷言 `timeout` kwarg 為 `SYNC_TIMEOUT`、
且呼叫前後 `client.timeout` 屬性未被更動；併發測試同時跑 `sync()` 與 `find_notes()`，
斷言後者的 timeout 始終是預設值。

**工作量**：約 15 行改動 ＋ 測試約 40 行。1 小時。

---

### High 修復建議順序

| 順位 | 項目 | 理由 |
|---|---|---|
| 1 | S003（＋S002 的共用交易類別） | S001 修復後失敗路徑浮現，孤兒卡風險現正升高 |
| 2 | S002 | 與 1 共用 `AnkiNoteTransaction`，一次做完 |
| 3 | S011 | 改動小、影響全域穩定性，且與 S012 併發問題同源 |
| 4 | S010（＋S014） | 使用者直接可感知的「按鈕沒反應」 |
| 5 | S004–S006 ＋共通防線 | 改動極小，一次清掉並加 CI 防線 |
| 6 | S007、S008 | 需在真實 DB 上驗證，安排在有語料庫存取的時段 |
| 7 | S009 | 需搭配部署設定調整，與下次部署一起上 |

---

## High 實作紀錄（2026-08-10）

上方各條的「修復方案」為實作前草案，以下記錄**實際落地內容與偏離之處**。

### 新增檔案

| 檔案 | 用途 |
|---|---|
| `app/services/task_handlers/shared/anki_transaction.py` | `AnkiNoteTransaction` 補償式交易（S002／S003 共用） |
| `app/bot/utils/formatting.py` | `anki_field_to_tg_text()`／`escape_tg()` TG 文字安全化（S010、可供 S014 重用） |
| `scripts/local_anki/JP_VerbPair/import_models.py` | 補回遺失模組（S063） |
| `backend/ruff.toml` | 靜態檢查規則集（S004 共通防線） |
| `tests/test_high_severity_fixes.py` | 20 個回歸測試，涵蓋 S002–S011 |
| `frontend/nginx.conf.template` | 由 `nginx.conf` 更名，供 envsubst 注入金鑰（S009） |

### 與計畫的偏離

1. **`AnkiNoteTransaction.create_note` 採顯式簽名**而非草案的 `**kwargs`——保留型別檢查
   能力，避免呼叫端打錯參數名時要到執行期才發現。
2. **Ruff 規則集收斂**：草案建議 `select = ["F"]`，但實跑發現專案有 80 件既有整潔性問題
   （F401 未用匯入 54、F541 無佔位符 f-string 17、F841 未用變數 6、F811 重複定義 3）。
   若直接開啟，CI 會立刻全紅且與本次修復無關。因此收斂為「會在執行期真的爆炸」的子集
   `["E9", "F821", "F823", "F502", "F522", "F701"]`，**S004 要防的 F821 已完全覆蓋**且目前
   歸零；整潔性問題留待獨立工作處理後再擴大規則。CI 的 lint 範圍也從 `backend/app`
   擴大為 `app scripts tests`——S004 正是發生在 `scripts/`，只掃 `app/` 擋不住。
3. **S008 額外發現**：`finally` 區塊原本只 `conn.close()` 一條連線，改為兩條連線都關閉，
   否則分離連線後會洩漏一條。
4. **S010 追加防禦**：除了轉義工具，另把「設定 recording 狀態」移到訊息成功送出之後，
   並在 `TelegramAPIError` 時以 `parse_mode=None` 降級重送純文字提示——確保任何未來的
   格式問題都不會再造成「狀態已設、使用者無感知」。
5. **S007 型別確認**：`failure_count INT NOT NULL DEFAULT 0`、
   `llm_model VARCHAR(255) DEFAULT NULL`，依 `log_repository.py` 的實際 SQL 用法推得。
6. **測試策略**：純邏輯部分（交易回滾、文字轉義、timeout 傳遞）以 mock 做行為斷言；
   涉及外部資源的部分（DDL、MySQL 連線、nginx 樣板、前端）改以**原始碼結構斷言**防回歸，
   不需要真實 DB／容器即可在 CI 執行。

### 尚待真實環境驗證

以下修復的正確性已由測試與靜態檢查覆蓋，但**尚未在真實環境實跑**：

- **S007**：需對真實語料庫執行 `init_db.py`（含既有表的欄位補齊路徑）。
- **S008**：需完整重建一次索引，比對 `SELECT COUNT(*)` 與腳本輸出的 `total_indexed`。
- **S009**：需重新 build 前端映像並以設定了 `API_SECRET_KEY` 的環境部署，確認 Web UI 正常
  且直接打後端 8000 埠會 401。
- **S002／S003**：建議在 Anki 實測一次「刻意觸發子卡重複」的情境，確認回滾後無殘留。

---

## 🟡 Med（28）

### Bot / API

- **S012** 全域（`webhook.py:49` + `state.py`/`voice.py` 等）：Webhook 以 BackgroundTasks
  併發執行 Update，`UserStateManager` 純記憶體先讀後寫無鎖，`voice.py:284-287` 且直接改
  共享 state 物件 → 同一使用者連發兩則語音會重複評分/重複寫 Anki/狀態交錯覆寫。
- **S013** `voice.py:274-276`：LLM 產生的 transcript/feedback 未 escape 直接入 HTML 訊息，
  `[:300]`/`[:500]` 截斷可能切斷標籤 → 評分已寫入 Anki 但 TG 回覆發送失敗。
  （STT 計畫 §2.5/§3.3 一併處理。）
- **S014** `commands.py:80,90` / `messages.py:100-102` / `fsm/vocabulary_fsm.py:132`：
  使用者輸入（deep link payload、card_id、speaker_name、word）未 escape 進 HTML f-string。
- **S015** `callbacks.py:145,182,225,243`、`newcard_menu.py`、`callbacks_config.py` 多個 handler：
  未判 `callback.message` 為 None（>48h 舊按鈕 → AttributeError、callback 轉圈無回應）。
- **S016** `fsm/speaking_fsm.py:146,293` 等：`if callback_query.message:` 保護不含緊接的
  `.answer(...)` 呼叫（保護區塊外）。

### Services

- **S017** `speaking_coach_handler.py:206-219`（trilingual 同型）：`add_audio` 寫 References
  時 index 解析失敗/越界僅 warning 後回報成功 → 音檔上傳成功但無欄位引用（靜默失聯）。
- **S018** `relation_service.py:56-58,212-224`：RelationType 表存 `strip().lower()` 名稱、
  CardRelation 存原始大小寫 → 依名稱過濾/刪除漏刪。
- **S019** `anki_model_manager.py:392-399`：`can_add_note` 以本地 JSON 定義補欄位而非查
  Anki 實際欄位 → 模型欄位不同步時誤判「重複」。

### Infrastructure

- **S020** `audio_evaluator/*` 三 client 重試不一致：gemini 對 timeout/503/429 指數退避
  ×3，openai/proxy 完全不重試；gemini 以 `"503" in str(e)` 字串匹配分類（脆弱）。
- **S021** `openai_client.py:124`：`input_audio.format` 傳 `"ogg"`，官方 OpenAI 僅收
  wav/mp3 → 官方端點＋TG 語音必 400。
- **S022** `llm/client.py:282-314`：`$ref` 展開無循環偵測（自參照 schema → RecursionError）；
  巢狀 `$defs` 解析為 `{}` 靜默丟失約束。
- **S023** `storage/minio_client.py`（全檔）：僅捕 `S3Error`，urllib3 連線層例外裸穿，
  違反「統一包成 MinioStorageError」的錯誤邊界。
- **S024** `storage_service.py:117-124`：上傳成功後 presigned URL 失敗整體拋錯 →
  呼叫端誤判上傳失敗而重傳（孤兒物件/重複上傳）。
- **S025** `anki/client.py:117+171-208`：傳輸層 `retries=3` 與 `_invoke` 手動重試疊加，
  ConnectError 實際最多 12 次嘗試＋每次 0.5s → Anki 未啟動時 TG 長時間無回應。

### Scripts

- **S026** `fastapi_client/JP_VerbPair/mark_skipped_es_data.py:162-171`：標記 failure_count=9
  的 UPDATE 缺 `source`（與 chapter）條件 → 誤封其他遊戲來源的正常紀錄。
- **S027** `fastapi_client/JP_VerbPair/generate_child_cards.py:146-154`：與 docstring 宣稱的
  游標分頁不符，每關鍵字僅 Fetch-100 → 超過 100 句命中時永遠只取頭部（頭部偏差）。
- **S028** `local_anki/migrations/migrate_geekly_to_speaking_coach.py:74,143,157,160`：
  Card_ID 僅 8 hex 無去重（生日碰撞）；每次重生成 ID 使查重形同虛設（重跑重複建卡）；
  `TG_Bot` 硬編碼 `"Jacky917_bot"` 不讀 settings。
- **S029** `local_anki/migrations/migrate_old_speaking_coach.py:199`：`allowDuplicate=True`
  且 ID 重生成，重跑即整批複製。
- **S030** `local_anki/JP_VerbPair/migrate_master_cards.py:74-86`：同上冪等缺陷。
- **S031** `local_anki/Speaking_Trilingual_Dark/import_cards.py:~160`：查重 query 只跳脫
  雙引號，Prompt 含 Anki 查詢特殊字元時查不到既有卡（重複建卡）或誤匹配。
- **S032** `local_anki/Speaking_Coach_Dark/import_cards.py`、`generate_interview_cards.py`：
  無 sys.path bootstrap 且 docstring 模組路徑過時；後者 JSON 路徑相對 cwd。
- **S033** `database/MySQL/JP_VerbPair/build_llm_index.py:199-203`：`--limit` 模式不清空
  舊索引、無去重鍵、無斷點 → 中斷重跑重複寫入。
- **S034** `database/MySQL/JP_VerbPair/build_llm_index_no_context.py:163-176,300-303`：
  以全域 MAX(script_id) 續跑，失敗批次被後續成功批次「越過」後永久跳過。

### 前端 / 部署

- **S035** `frontend/src/pages/CardGenerator.tsx:50-54`：model 自動選取 effect 依賴整個
  handlers 陣列參考 → React Query refetch 後悄悄重設使用者手選的 model。
- **S036** `frontend/src/components/CardDetailModal.tsx:30,44`：`cardDetail!.note_id`
  非空斷言 + 關閉/切換 race → 對 `/cards/undefined` 發請求。
- **S037** Docker：前後端容器與 compose 均無 HEALTHCHECK → hung 進程不會被偵測/重啟。
- **S038** `deploy_local.sh:65`：`hostname -I` 僅 Linux 存在 → Windows/macOS 顯示錯誤網址。
- **S039** `deploy_local.sh:86`：`read -p` 在非互動環境 + `set -e` 下直接以錯誤碼終止。

## 🟢 Low（23）

- **S040** `main.py:288`：崩潰警報 `asyncio.create_task` 未保留參照 → 可能被 GC 靜默吞掉。
- **S041** `core/dependencies.py:74-101`：lifespan 失敗時 client 為 None，依賴函數宣告
  非 Optional 直接回傳 → 下游 AttributeError 500 而非明確 503。
- **S042** `main.py:177-182`：shutdown 時 `await polling_task` 重拋既有例外 → 跳過後續清理。
- **S043** `main.py:128-144`：webhook URL 相同但 secret 變更時不重新 set_webhook。
- **S044** `core/auth.py:66`：API Key 用 `!=` 而非 `secrets.compare_digest`（時序側信道）。
- **S045** `core/dynamic_config.py:36-40`：.env 覆蓋 os.environ，優先序與 pydantic-settings 相反。
- **S046** `voice.py:126-127`（`messages.py:68-69` 同型）：`notes_info[0]` 未防空列表 IndexError。
- **S047** `fsm/expression_fsm.py:253`：Anki 搜尋跳脫用 `""` 而非 `\"` → 查重失效。
- **S048** `callbacks_config.py:52-56`：callback_data 超 64 bytes 截斷後按鈕形同壞掉。
- **S049** `api/webhook.py:56`：解析失敗回傳 `str(e)` 洩漏內部細節。
- **S050** `anki/client.py:211`：非 JSON 回應（CF 登入頁 200）拋裸 JSONDecodeError，未包裝。
- **S051** `audio_evaluator/proxy_client.py:94-99`：非 ogg 一律當 wav；`audio/mp3` 非標準 MIME。
- **S052** `ffmpeg/client.py:91`、`voice/voicepeak_runner.py:173`：`communicate()` 無 timeout。
- **S053** `database/elasticsearch_client.py:179`：ES|QL 跳脫未處理反斜線。
- **S054** `anki_model_manager.py:286-289`：`_lookup_duplicate_location` 查詢未跳脫/未限定欄位。
- **S055** `task_handlers` 兩處 `@override` 標註的方法不在 BaseHandler 介面（型別檢查報錯）。
- **S056** `speaking_coach_handler.py:163-165`：add_recording 路徑重複讀卡（多一次往返＋競態窗口）。
- **S057** `local_anki/JP_VerbPair/cleanup_script.py:70`、`add_narrator_tag.py:20`、
  `add_llm_tag.py:22`：媒體前綴/AnkiConnect URL 硬編碼不讀 settings。
- **S058** `local_anki/Expression_Correction/import_expression_models.py:44-71`：client 建構
  失敗時 finally 引用未定義變數（UnboundLocalError 掩蓋原錯）。
- **S059** `local_anki/test_llm.py:20`：AsyncOpenAI client 未 close。
- **S060** `common/template_validators/speaking_coach_dark_validator.py:204-205`：docstring
  宣稱 `--overwrite` 參數，實際寫死 True 且無 argparse。
- **S061** 前端雜項：`KnowledgeGraph.tsx` 硬編碼 handler `'vocabulary_mining'`（新 handler
  卡片不入圖譜）、`as any` 泛濫、下拉無 outside-click 關閉；`useLocalStorage.ts` 閉包舊值
  ＋跨分頁 JSON.parse 無防護；`VITE_DEFAULT_DECK` build 期打包 CI 未注入。
- **S062** 部署/CI 雜項：後端 8000 直接對外映射（nginx 已反代）；CI lint 用 Py3.11 而
  runtime 3.13；Portainer webhook curl 不驗 http_code 失敗仍綠燈；`deploy_local.sh`
  的 `docker port` 解析在 IPv6 輸出取錯欄位、compose env_file 絕對路徑本機不存在。

---

## 附註

- `git status` 中 `backend/app/core/exceptions.py` 顯示刪除為誤導：實已重構為
  `core/exceptions/` package 且 `__init__.py` 有 re-export，import 正常（已驗證）。
- 測試基線（48+11）不在 main：位於未合併的本地分支 `claude/distracted-borg-2cfed8`
  （342 檔差異），處置決策見 [14 號計畫 §2.9](14_STT_Dual_Mode_Evaluator_Plan.md)。
