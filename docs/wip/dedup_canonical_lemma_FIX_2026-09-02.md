# FIX — 同一句台詞被同一動詞重複生成子卡(去重鍵漏洞)

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-09-02 |
| **性質** | bug 修復(根因分析 + 修正工作項 + 存量資料修復) |
| **狀態** | 🚧 修復中 |
| **嚴重度** | 🟡 功能異常有繞路(冗餘卡各自內容正確,只是學習上重複;3,106 張活卡中 23 張冗餘,0.7%) |
| **影響範圍** | JP_VerbPair 生成管線的去重判定、`generated_sentences_log.verb_lemma` 的語意、刪卡工具鏈的完整性修復寫入;存量資料**已被污染**(同母卡下 `verb_lemma` 拼寫漂移 126 組,其中 7 組已造成重複卡) |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/archive/child_card_deletion_toolkit_FEAT_2026-08-27.md`(刪卡工具鏈,§2 verb_lemma 抽取掛鉤)、`docs/archive/verbpair_fugashi_validation_FEAT_2026-08-27.md`(假名擴展關鍵字的來源)、2026-09-02 全庫抽查對話 |

---

## 1. 症狀與復現

- **症狀**:全庫 3,106 張活卡做句子文字比對,發現 **24 組「同母卡・同句」重複**,扣除 3 組「同句兩側各一張」(出る/出す、直る/直す、焦れる/焦らす,屬正當對照)後,**21 組冗餘、涉及 23 張多餘卡片**。分兩型:

  | 型 | 組數 | 冗餘卡 | 例 |
  |---|---|---|---|
  | A. 同一行台詞(script_id 相同) | 7 | 8 | [125] 纏まる / [555] まとまる;[128] 纏める / [560] まとめる / [745] 纏[まと]める;[157] 無くなる / [618] なくなる |
  | B. 語料重複台詞(script_id 不同,VN 分支場景重複) | 14 | 15 | [1015]/[1016] 縮まる;[3281]/[3290]/[3291] 代わる(同一句三張) |

- **復現步驟**(A 型):
  1. 母卡 纏める 的 `extra_search_keywords.json` 設有假名擴展 `まとめる`
  2. 同一行台詞「話をまとめると…」被關鍵字 `まとめる` 命中並生成 → DB 寫入 `verb_lemma='まとめる'`
  3. 之後母卡欄位改為帶標音 `纏[まと]める` 或改跑另一關鍵字 → 同一行再被命中,`get_record(script_id, '纏める')` 查無紀錄 → **再生成一張**
  → 預期:同一句在同一動詞側只生成一次;實際:每種拼寫各一張。
- **復現步驟**(B 型):語料中同一句台詞出現在兩個 script_id(分支劇情),兩者都被 ES 命中 → 唯一鍵按 script_id 看是兩句 → 各生成一張。
- **首次出現**:A 型自 2026-08 假名擴展關鍵字上線起;B 型自始存在(語料特性)。

## 2. 根因(皆為**已定位**)

### R1. 管線把「命中的搜尋關鍵字」當成 `verb_lemma` 寫入

`backend/scripts/fastapi_client/JP_VerbPair/generate_child_cards.py`:

- L400–403 `prepare_generation(verb_lemma=kd["keyword"])`
- L457–460 `record_success(verb_lemma=kd["keyword"])`
- L483/497/502 `record_failure(script_id, kd["keyword"], …)`
- L397 dry-run 集合亦以 `kd["keyword"]` 為鍵

`kd["keyword"]` 是 ES 搜尋用的表層(母卡標準表層 **或** `extra_search_keywords.json` 的假名/異體擴展),同一個動詞側會有多個值(纏める/まとめる)。唯一鍵 `uk_script_verb_project (script_id, verb_lemma, project)` 因此把同一句的不同拼寫視為不同紀錄。**同一 `kd` 內其實已備有正確的值**:`kd["target_lemma"] = normalized_verb`(母卡標準表層去標音,L318–321),只是去重呼叫沒用它。

### R2. 刪卡工具鏈的完整性修復寫入「帶標音」的表層

`backend/scripts/local_anki/common/deletion/profiles.py:55–82 _verb_pair_lemma()` 直接回傳 `Verb_Pair_JSON` 的 `intransitive`/`transitive` 值——該欄位保留母卡標音格式(`纏[まと]める`)。`integrity.py:412–416` 以此查 DB、`:486–494` 以此 INSERT 修復紀錄,於是產生第三種拼寫(存量 [745] 即此來源)。CoreVerb 側的 `_core_verb_lemma` 有去標音,VerbPair 側漏了。

### R3. 沒有句子文字層的去重

去重鍵完全依賴 `script_id`;語料裡「同一句台詞出現在不同 script_id」是 VN 的常態(分支、回想、重複場景),現行機制無法識別。

### 存量污染盤點(2026-09-02 查詢)

- 同一母卡下 `verb_lemma` 出現多種拼寫:126 個母卡(多數只是自他兩側,正常;實際拼寫漂移者為假名/標音變體,如 纏まる/まとまる/纏[まと]める、浮かべる/浮[う]かべる、捲る/まくる、無くなる/なくなる、じらす/焦らす、そろう/揃う、かぶる/被る、かかる/掛かる、ゆるむ/緩む、こぼす/零れる)
- A 型重複 7 組(id:125/555、126/556、128/560/745、139/320、140/321、155/478、157/618)
- B 型重複 14 組(冗餘 id:330、658、1016、1431、1765、2318、2470、2723、3055、3290、3291、3292、3293、3294、3295)

## 3. 修法

### 3.1 `verb_lemma` 語意收斂為「母卡標準表層去標音」(修 R1、R2)

- **不改唯一鍵**,改「寫進去的值」:管線改用 `kd["target_lemma"]`;完整性修復改用共用的 `canonical_verb_lemma()`(去 `[…]` 標音)。
- 新增欄位 `search_keyword VARCHAR(255) NULL`,保留「實際命中的關鍵字」供追溯(原本混在 `verb_lemma` 裡的資訊不丟失)。`init_db.py` 的冪等欄位補齊自動加上。
- 共用 helper 放 `backend/scripts/common/verb_lemma.py`,CoreVerb 現有的 `funnel.strip_furigana` 規則相同,本次不動它(範圍防蔓延),於 docstring 互相指認。

### 3.2 句子文字層去重(修 R3)

- `GeneratedLogRepository.get_logged_dialogues(session, verb_lemma, project)`:JOIN `scripts` 取該動詞**全部**紀錄(含軟刪除、失敗)的台詞原文——語意同 `get_logged_keys`:使用者軟刪除代表「這句不要」,同文異 id 的分身同樣不要。
- `DedupManager.prepare_generation(..., dialogue=None)`:新增可選參數;有給時以 `normalize_sentence()` 正規化後比對該動詞的已記錄文字集合(每動詞 lazy 載入一次 + 本次執行中允許的句子即時加入,dry-run 同樣生效)。命中 → 記 log 並回 `None`(與既有去重同一路徑)。
- 兩條管線都傳入:VerbPair 傳 ES 命中列的 `row["dialogue"]`,CoreVerb 傳 `candidate.sentence`。
- `normalize_sentence()`(`backend/scripts/common/sentence_normalize.py`):NFKC → 去 `[…]` 標音 → 去 HTML 標籤 → 只保留字母/數字類字元(Unicode category L*/N*,長音「ー」「々」屬 Lm 自然保留),標點、空白、符號全去。刻意**不做**假名/漢字層的等價(那是語意層,交給 LLM 的範疇不在此)。

### 3.3 存量資料修復

`backend/scripts/common/database/canonicalize_verb_lemma.py`(預設 dry-run,`--execute` 才寫):

1. 對每筆紀錄算 canonical:去標音;若值落在該母卡 `extra_search_keywords.json` 的 `extra_keywords` 中,映射回其標準表層。`verb_lemma` 改寫為 canonical,原值存進 `search_keyword`(僅當原值 ≠ canonical)。
2. 改寫後若與同 `(script_id, project)` 的另一筆撞鍵:
   - 至多一筆是「活的」(未軟刪除且有子卡)→ 自動合併:保留活的那筆,硬刪另一筆,`delete_count`/`failure_count` 取兩者最大值;
   - 兩筆都活 → **不動**,列出 id 對,要求先用 `delete_by_generated_sentences_log_id.py` 刪掉冗餘卡(§1 A 型 7 組正是這種),再重跑。
3. 執行完呼叫既有的 `reset_auto_increment`。

B 型 15 張冗餘卡不涉及 DB 修復,直接用 id 刪除工具軟刪除即可(軟刪除 → 該 script_id 永不再生成;其文字分身則靠 §3.2 擋住)。

### 考慮過但放棄

- **唯一鍵改成 `(script_id, master_note_id, project)`**:會擋掉「同句兩側各一張」的正當卡(出る/出す 同句對照),而且存量已有 3 組合法衝突,遷移要先毀資料。放棄。
- **新增 `sentence_norm` 欄位存正規化文字**:要回填 3,000+ 筆且與 `scripts.dialogue` 重複存;JOIN 即可取得原文,每動詞紀錄數 ≤ 數十筆,成本可忽略。放棄。
- **在 LLM 提示詞層處理重複**:重複判定是純資料層問題,與內容品質無關,不屬「提示詞優先」原則的範圍;交給 LLM 反而無法跨執行記憶。

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `scripts/common/verb_lemma.py` | 新增 `canonical_verb_lemma()`(去標音、去空白) |
| `scripts/common/sentence_normalize.py` | 新增 `normalize_sentence()` |
| `scripts/common/database/log_repository.py` | 新增 `get_logged_dialogues()`;`increment_failure_count` / `create_or_restore_record` 接受 `search_keyword`;docstring 標明 `verb_lemma` 語意 |
| `scripts/common/database/init_db.py` | DDL 加 `search_keyword` 欄位與 `verb_lemma` 註解;`_ensure_columns` 補齊 |
| `scripts/common/database/canonicalize_verb_lemma.py` | 新增存量修復腳本(dry-run 預設) |
| `scripts/fastapi_client/JP_VerbPair/pipeline_components/dedup_manager.py` | `prepare_generation` 加 `dialogue` 文字去重;`record_*` 傳遞 `search_keyword` |
| `scripts/fastapi_client/JP_VerbPair/generate_child_cards.py` | 去重呼叫改用 `kd["target_lemma"]`,關鍵字改走 `search_keyword`;傳入 `dialogue` |
| `scripts/fastapi_client/JP_CoreVerb/generate_child_cards.py` | `prepare_generation` 傳入 `candidate.sentence` |
| `scripts/local_anki/common/deletion/profiles.py` | `_verb_pair_lemma` 回傳值去標音 |
| `tests/test_dedup_canonical_lemma.py` | 釘住 R1/R2/R3 的回歸測試 |

### 回歸測試

- `normalize_sentence`:標點/空白/全半形/標音差異視為同句;假名 vs 漢字**不**視為同句。
- `canonical_verb_lemma`:`纏[まと]める` → `纏める`;純假名不變。
- `DedupManager.prepare_generation`:同動詞已記錄的同文異 id 句 → 回 `None`;本次執行中放行的句子其分身 → 回 `None`;不同動詞不互相影響;未傳 `dialogue` 時行為與舊版完全相同。
- `_verb_pair_lemma`:帶標音的 `Verb_Pair_JSON` → 去標音。
- `canonicalize` 的合併規則(純函式):活/死撞鍵 → 合併並取最大計數;活/活撞鍵 → 列為衝突不動。
- 既有 `test_deletion_toolkit.py` 全綠(profiles 掛鉤行為改變處同步更新斷言)。

## 5. 驗證

- [x] 新測試 26 項通過(2026-09-02),全套件 198 passed、無新增失敗
- [x] `canonicalize_verb_lemma.py` dry-run(2026-09-02):單純改寫 147 筆、自動合併 1 組([156]/[479] 捲る,後者為死紀錄)、衝突 7 組——**與 §1 A 型 7 組完全一致**
- [x] `init_db.py` 重跑 → 只補上 `search_keyword` 欄位,unique key 無變更
- [x] 全牌組 dry-run 生成(`--limit 0 --dry-run`,1,473 張候選):log 出現 3 次「同文去重」跳過(明ける 17111、戻す 17815、伸ばす 17395),皆為已記錄台詞的同文異 id 分身;無例外
- [ ] **存量清理(合併後由使用者執行,涉及不可逆的 Anki 刪卡)**:
  1. 軟刪 23 張冗餘卡(A 型保留較新那張):
     ```
     python scripts/local_anki/delete_by_generated_sentences_log_id.py 125,126,128,560,139,140,155,157,330,658,1016,1431,1765,2318,2470,2723,3055,3290,3291,3292,3293,3294,3295 --execute
     ```
     dry-run 已確認 23 筆全部可解析、0 筆失敗。⚠️ CLI 的事後完整性檢查會**順帶修復** 199 個既有問題(孤兒媒體等);若要維持「不動既有髒資料」的先前決定,改走 report-only 的驅動方式(child_deleter.run_integrity_check 以 is_execute=False 包裝)。
  2. 再跑 `python scripts/common/database/canonicalize_verb_lemma.py --execute` → 預期零衝突(軟刪後 7 組各剩一筆活紀錄,死紀錄自動合併)
  3. 複查:`SELECT verb_lemma, COUNT(*) FROM generated_sentences_log WHERE verb_lemma LIKE '%[%' GROUP BY 1` 應為空
