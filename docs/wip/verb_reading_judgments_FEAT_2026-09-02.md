# FEAT — 同表層多讀的母卡歸屬:讀音判斷快取表 + 獨立判讀腳本

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-09-02 |
| **性質** | 追加功能(獨立判斷快取表 + 獨立判讀腳本 + 生成管線的查表過濾;移除 `search_keyword`) |
| **狀態** | 📝 設計完成(2026-09-03 改版:不加讀音欄位、不動去重鍵),待實作 |
| **範圍** | 新表 `verb_reading_judgments`;新腳本 `judge_verb_readings.py`;後端新增判讀端點與模板;VerbPair 生成腳本加「查表過濾」;移除 `generated_sentences_log.search_keyword` |
| **不動** | `generated_sentences_log` 的唯一鍵與 `verb_lemma` 語意;兩層去重;**生成模板**;fugashi 四關與 `ignore_reading` 設定;CoreVerb 管線(無同表層動詞,端點日後可複用);非同表層動詞的一切行為 |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/archive/verb_lemma_backfill_FIX_2026-09-02.md`(**前置**:存量拼寫修復,必須先執行)、`docs/archive/dedup_canonical_lemma_FIX_2026-09-02.md`(去重鍵三個寫入點的教訓)、`docs/archive/verbpair_fugashi_validation_FEAT_2026-08-27.md`(§6.5 讀音關與 `ignore_reading` 的由來) |

---

## 1. 現況與需求

### 1.1 同表層多讀的實際規模(2026-09-02 全牌組掃描)

牌組 491 個不同表層中,**14 個表層、8 組動詞、18 張母卡**的漢字表記相同但讀音不同:

| 表層 | 讀音(母卡 note id) |
|---|---|
| 捲る / 捲れる | まくる(…540)/ めくる(…543) |
| 退く / 退ける | しりぞく(…654)/ どく(…657)/ のく(…661) **三讀** |
| 凝る / 凝らす | こる(…700)/ こごる(…702) |
| 止める | やめる(…711)/ とめる(…909032) |
| 開く / 開ける | あく(…748)/ ひらく(…750) |
| 温める | あたためる(…770)/ ぬるめる(…772)/ あたためる(…909007) |
| 解く / 解ける | とく(…830)/ ほどく(…832) |
| 汚す / 汚れる | けが(…921)/ よご(…802616341) |

### 1.2 兩個具體問題

**問題 A:去重鍵分不出母卡(資料層)**
唯一鍵 `(script_id, verb_lemma, project)` 對 `止める` 只認得一筆。目前 117 筆紀錄落在上述表層,尚無實際撞鍵(剛好挑到不同台詞),但**兩側正在互相擋**:任一張母卡用過的台詞,另一張就取不到。證據——`開く` 20 筆全來自 ひらく 側、`開ける` 20 筆全來自 あく 側、`汚す` けが 側只有 1 筆而 よご 側 6 筆。這不是未來風險,是現在進行式的配額餓死。

**問題 B:候選句歸屬需要上下文(內容層)**
ES 用表層搜,搜到的句子究竟讀哪個音,只有上下文知道。fugashi/UniDic 幫不上忙——它對同表層多讀是**上下文盲的固定選讀**(ドアが開く→ヒラク、お腹が空く→アク),這正是 `ignore_reading` 開關存在的原因。

已發現的實例:`[3332]`「うっ、うぅぅ……**汚された**、汚されちゃったよ……」(翻譯「我被玷汙了」)掛在 **よご** 母卡下,但該語境讀 **けがされた**,應屬 けが 母卡。

現有繞法只有 6/18 張母卡在 `extra_search_keywords.json` 設了假名關鍵字分流(まくる/めくる 等),且僅在語料剛好用假名書寫時有效——漢字寫法的句子仍會被兩側同時撈到。

### 1.3 設計原則:讀音不進資料層,只當選句依據

母卡上已經有讀音(`汚[けが]す`),它是母卡的身分;DB 的 `verb_lemma + master_note_id` 已足以表達「這筆屬於哪張母卡」。讀音真正需要出場的時刻只有一個:**選句時判斷「這句的 汚す 讀什麼」**,決定它該給哪張母卡。這是台詞本身的屬性,與母卡無關,所以獨立存放、與去重鍵解耦。

一旦分派正確,現有唯一鍵「同句只能一筆」就是**對的保護**:一句只讀一種音、只該屬一張母卡;第二張母卡要它,代表判斷錯了。因此本計畫**不加讀音欄位、不動唯一鍵**——2026-09-02 的初版設計(`verb_reading` 欄位 + 四欄鍵)已放棄,理由見 §4「考慮過但放棄」。

## 2. 資料模型

### 2.1 新表 `verb_reading_judgments`(判斷快取)

```sql
CREATE TABLE IF NOT EXISTS verb_reading_judgments (
    script_id    BIGINT UNSIGNED NOT NULL COMMENT '台詞 ID(對應 scripts.id)',
    verb_surface VARCHAR(32)     NOT NULL COMMENT '同表層多讀的表層,如 汚す',
    reading      VARCHAR(32)     NOT NULL COMMENT 'LLM 判定的讀音(平假名);無法判定為空字串',
    llm_model    VARCHAR(255)    DEFAULT NULL COMMENT '判讀所用模型標籤(取自後端回應)',
    created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (script_id, verb_surface),
    FOREIGN KEY (script_id) REFERENCES scripts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- 主鍵 `(script_id, verb_surface)`:同一句台詞、同一個表層只有一個判斷;けが/よご 兩張母卡查的是**同一筆**。
- `reading` 為空字串 = LLM 無法判定。空字串不等於任何母卡的讀音,所以所有母卡都會跳過該句(fail-closed)。
- 判斷永久有效(固定台詞的讀音不會變);要重判就刪那筆或用腳本 `--rejudge`。
- `VARCHAR(32)` 依 2026-09-02 實測(522 項讀音最長 5 字)取 6 倍餘裕。
- 與 `generated_sentences_log` **無關聯**、不進任何去重鍵、`prepare_generation` 不讀它。

### 2.2 移除 `generated_sentences_log.search_keyword`

它是 PR #16 存量修復期的安全網(保留被改寫的原值),任務已於 2026-09-03 完成;無任何讀取方、語意亦不精確(記的是 ES 查詢詞,不保證等於句中寫法)。一併移除:DDL、`_ensure_columns`、`log_repository` 兩個寫入方法的參數、`dedup_manager` 的傳遞與 `_keyword_or_none`、`generate_child_cards.py` 呼叫點、對應測試;`init_db.py` 加冪等的 `DROP COLUMN`(欄位存在才刪)。

## 3. 三個元件與它們的關係

```
[獨立] judge_verb_readings.py ──寫入──▶ verb_reading_judgments ◀──只讀── generate_child_cards.py [生卡]
            │                                                                    │
            └── POST /api/v1/verb-pair/judge-readings(後端,新模板)              └── 表空 → 行為與現況完全相同
```

生卡流程**不呼叫 LLM 判讀音**;判斷全部由獨立腳本事先產生。兩者只靠那張表溝通,任一邊不存在都不影響另一邊。

### 3.1 獨立判讀腳本 `scripts/fastapi_client/JP_VerbPair/judge_verb_readings.py`

1. 掃全部母卡,建同表層讀音表 `{表層: {讀音: 母卡id}}`,只保留讀音數 ≥ 2 的表層(目前 14 個;不落設定檔,母卡改動自動反映)。
2. 對每個表層收集待判 `script_id`:
   - ES 依表層搜尋的候選(與生卡相同的 `search_dialogue_by_verb`,`script_id` 游標分頁,`--limit` 控制上限);
   - **加上** `generated_sentences_log` 裡該表層已生成的紀錄(存量 117 筆),讓既有卡片一併受檢。
3. 排除表中已有判斷的;剩餘者每 20 句一批送後端端點,每句附:台詞原文、前後各 2 行(直接查 `scripts` 表,不走完整 ContextBuilder)、表層、候選讀音清單。
4. 回應寫入表(`reading` + 後端回傳的 `llm_model`)。
5. 結尾輸出**歸屬對帳報告**:對存量已生成紀錄,比對「判定讀音」與「所屬母卡讀音」,不一致者逐筆列出(id、句子、母卡讀音、判定讀音)——交人工複核與決策(改掛/刪除重生/保留)。

參數:`--surface 汚す`(只判一個表層)、`--limit N`(每表層 ES 上限)、`--dry-run`(只列待判數量,不呼叫 LLM)、`--rejudge`(刪除既有判斷重判,配 `--surface` 使用)。

### 3.2 後端端點 `POST /api/v1/verb-pair/judge-readings`

- 請求:`items: [{script_id, surface, candidates: [讀音…], line, context_before: [..], context_after: [..]}]`,單次 ≤ 20 筆。
- 回應:`{llm_model, results: [{script_id, reading}]}`,`reading` ∈ candidates 或 `""`。
- 新模板 `JP_VerbReading_Judge.j2`:給上下文與候選讀音,要求逐句判定;無法確定時明確回 `""`,**不猜**。模板只做這一件事,生成模板不動。
- 使用既有 LLM client(標籤來源與生成一致:後端回應的 `llm_model`)。

### 3.3 生卡腳本的改動(VerbPair)

只加一道**查表過濾**,位置在 ES 撈回候選之後、fugashi 驗證之前:

- 啟動時建同表層讀音表(與 3.1 同一段共用程式碼,放 `scripts/common/`)。
- 候選的表層若在多讀表內,查 `verb_reading_judgments`(每表層一次批量載入):
  - 有判斷且 = 本母卡讀音 → 放行;
  - 有判斷且 ≠ 本母卡讀音(含空字串)→ 跳過,log `讀音判斷:script_id=X 判為 よごす,非本母卡 けがす,跳過`,**不寫任何紀錄**;
  - **無判斷 → 放行**(與現況相同),log 一行 `未判讀` 供統計。
- 表層不在多讀表內 → 完全不查,零額外成本。
- 結尾統計:本輪「查表跳過」與「未判讀放行」各幾句,提醒使用者跑判讀腳本。
- fugashi 讀音關與 `ignore_reading` 設定**維持不變**——它是另一道獨立的、上下文盲的機械關,本計畫不改它的行為。

## 4. 為什麼是這個切法

| 疑慮 | 本設計 |
|---|---|
| 一張母卡的「讀音不符」寫成失敗紀錄,擋住另一張母卡 | 生卡流程根本不做讀音判斷、不寫任何相關紀錄;跳過的句子在 DB 沒有痕跡 |
| 判斷成本混進生卡流程,拖慢跑批 | 判斷在獨立腳本、離線批次、一句一生只判一次;生卡只查表 |
| 表空或後端未更新時生卡壞掉 | 表空 = 全部「未判讀 → 放行」,行為與現況一字不差,只是少了擋 |
| 讀音需要進去重鍵 | 不需要;讀音是選句依據,不是身分 |

### 考慮過但放棄

- **`verb_reading` 欄位 + 四欄唯一鍵**(2026-09-02 初版):要動鍵、要回填 3,144 筆、四個寫入點都得加參數,而且讀音本來就在母卡上——多存一份只是重複事實。放棄。
- **生成時由 LLM 拒絕(`READING_MISMATCH`)**:判斷發生在已花掉完整生成呼叫之後;拒絕若寫失敗紀錄會擋另一張母卡,不寫則每輪重試。放棄。
- **唯一鍵加 `master_note_id`**:鍵層與文字層都要改成按母卡,並失去「同句只一筆」對誤判的保護。放棄。
- **判斷內嵌在生卡流程、即時呼叫**:與獨立腳本功能相同,但生卡流程對後端端點產生硬依賴,表空時無法退回現況。放棄,改為解耦。
- **搭配語硬規則**:機械檢查與過擬合。放棄。

## 5. 改動清單

| 檔案 | 改動 |
|---|---|
| `scripts/common/database/init_db.py` | 新表 `verb_reading_judgments` DDL;`search_keyword` 冪等 DROP |
| `scripts/common/database/reading_judgment_repository.py` | 新增:`get_many(script_ids, surface)`、`upsert_many`、`delete_by_surface` |
| `scripts/common/homograph_table.py` | 新增:掃母卡建 `{表層: {讀音: 母卡id}}`(判讀腳本與生卡腳本共用) |
| `scripts/common/database/log_repository.py` | 移除 `search_keyword` 參數與 SQL 欄位 |
| `.../JP_VerbPair/pipeline_components/dedup_manager.py` | 移除 `search_keyword` 傳遞與 `_keyword_or_none` |
| `.../JP_VerbPair/generate_child_cards.py` | 啟動建多讀表;候選查表過濾;結尾統計;移除 `search_keyword=` |
| `.../JP_VerbPair/judge_verb_readings.py` | 新增獨立判讀腳本(§3.1) |
| `app/api/verb_pair.py`(或既有路由檔) | 新端點 `judge-readings` |
| `app/templates/prompts/anki/JP_VerbReading_Judge.j2` | 新模板 |
| `scripts/common/database/canonicalize_verb_lemma.py` | 移除寫 `search_keyword` 的邏輯(腳本保留為歷史工具) |
| `tests/test_reading_judgments.py` | 回歸測試 |

### 回歸測試

- 多讀表:只收讀音數 ≥ 2 的表層;同讀跨母卡(繋がる)不收;三讀(退く)正確收三個
- 查表過濾:有判斷且相符 → 放行;不符/空字串 → 跳過且不呼叫 `prepare_generation`;**無判斷 → 放行**;非多讀表層 → 不查表
- 判讀腳本:已判過的不重送;`--rejudge` 才重判;回應中 `reading` 不在候選內時視為空字串並警告;歸屬對帳報告正確列出不一致
- 端點:輸入 > 20 筆拒絕;回應結構;模板渲染含上下文與候選
- `search_keyword` 移除後全套件仍綠

## 6. 驗收

- [x] 前置:`verb_lemma` 拼寫修復已執行完畢(2026-09-03)
- [ ] `init_db.py` 重跑 → 新表就位、`search_keyword` 已移除、其餘欄位與唯一鍵不變
- [ ] 表為空時全牌組 dry-run:預計張數與改動前一致(只多「未判讀」統計行)
- [ ] 跑 `judge_verb_readings.py` 覆蓋 14 個表層(含存量 117 筆);對帳報告中的不一致逐筆人工複核後交決策
- [ ] 表填好後 dry-run:同表層動詞(止める / 開く / 汚す)兩側各自只拿到讀音相符的候選;log 出現「讀音判斷…跳過」
- [ ] 實際生成一輪,抽出新生成的同表層卡片逐張確認讀音歸屬正確
- [ ] 全套件通過

## 7. 後續(本計畫不做)

1. **同表層同讀跨母卡**:14 個表層是「同表層、同讀音、不同母卡」(如 `繋がる` 兩張母卡;`穢す`(557)與 `汚[けが]す`(921)更是同詞異漢字且前者以「汚す」為關鍵字),讀音判斷對它們無效,屬母卡設計問題,需另案盤點。
2. **孤兒紀錄**:`掛ける` 3 筆指向已刪除的母卡、`収まる` 2 筆是母卡改名前的舊紀錄。
3. **CoreVerb 接入查表過濾**:目前 3 張母卡無同表層,端點與共用模組已可直接複用,待需要時接。
4. **fugashi 讀音關與判斷表的關係**:兩者並存(前者機械、後者語境)。若日後判斷表覆蓋率高,可評估對多讀表層自動跳過 fugashi 讀音關;本計畫不動。
