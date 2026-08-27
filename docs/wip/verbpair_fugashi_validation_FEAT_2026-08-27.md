# FEAT — VerbPair 管線接上 fugashi token 級驗證,杜絕偽命中與讀音錯配

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-27 |
| **性質** | 新增機能設計 + 實作工作項 |
| **狀態** | 🚧 實作中(P0–P2 完成 2026-08-27;待人工確認同表層多讀卡的取捨後收尾) |
| **範圍** | `scripts/fastapi_client/JP_CoreVerb/pipeline_components/candidate_validator.py`(擴充)、`scripts/fastapi_client/JP_VerbPair/generate_child_cards.py`(接入)、`scripts/fastapi_client/JP_VerbPair/extra_search_keywords.json`(格式擴充)、對應單元測試 |
| **不動** | ES 檢索本身(`elasticsearch_client.search_dialogue_by_verb`)、後端 API 與 handler、CoreVerb 管線的現有行為(驗證器擴充需回歸相容)、已生成的存量卡片(清查另案) |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | `docs/14_Core_Verb_Card_Plan.md` §6.1(CoreVerb 驗證器原始設計)、`docs/wip/child_card_deletion_toolkit_FEAT_2026-08-27.md`(刪除工具,清錯卡時配合使用) |

---

## 1. 問題與動機

2026-08-27 對 VerbPair 管線做全量 dry-run(253 母卡 → 4,474 張)並抽樣回查 MySQL 原句,
確認 VerbPair 是舊版「ES 命中即收」流程,**完全沒有** CoreVerb 已有的 token 級驗證
(`candidate_validator.py`),導致三類污染(均為**已定位**,附實測證據):

**① 偽命中(關鍵字是別的詞)**
- 撚る的假名擴展「よる」:20/20 全是「〜による/〜によって」(依る,文法連語)。
  已臨時移除該擴展止血,但這只是繞開,任何短假名關鍵字都有同樣風險。
- 「まくる」(捲る):18 張約 9 成是「弾き**まくる**/触れ**まくる**」接尾用法(狂做~)。
- 「切れる」:31 張混入「覚え**切れない**/使い**切れない**」(=無法~完,複合動詞後項)。

**② 讀音錯配(同表層異讀,ES 只認字形)**
- 埋まる:卡片原標うずまる,實測 40 句全是「心の穴が埋まる」(=うまる),
  已改卡止血——但「空く(あく)」混入「お腹が**空く**(すく)」同類問題無卡可改。
- 同表層卡共 13 組(止める=とめる/やめる、汚れる=けがれる/よごれる、
  退く=しりぞく/どく/のく、温める ×3…):撞句歸屬先搶先贏,搶錯=讀音教錯。

**③ 補助動詞/複合動詞前項**(CoreVerb 驗證器原本就處理的兩類):
  「食べて**みる**」的みる、「**見**送る」的見——VerbPair 同樣暴露。

根因:`generate_child_cards.py` 的 `process_keyword_up_to_target()`
(`generate_child_cards.py:204-269`)從 ES 拿到 `script_id` 後直接進 dedup → 生成,
中間沒有任何語言學驗證。而 CoreVerb 的 `candidate_validator.validate_candidate()`
已解決 ①後半+③,缺的只有「讀音驗證」與「複合動詞**後項**拒絕」兩條規則。

## 2. 目標與非目標

**目標**
- G1 `candidate_validator` 擴充兩條規則:讀音驗證(UniDic 語彙素読み vs 期待讀音)、
  複合動詞後項拒絕(前一 token 為動詞連用形 → 拒),CoreVerb 現有行為回歸不變。
- G2 VerbPair 管線在 ES 結果 → dedup 之間接入驗證:lemma、讀音、前後項、
  補助動詞四關全過才進入生成;拒絕原因分佈納入執行報告。
- G3 假名擴展關鍵字(ほぐす、めくる、どく…)經 UniDic lemma 正規化後仍能
  正確歸戶到母卡的標準表層(靠 UniDic 的 lemma 欄天然收斂:句中寫「ほぐす」的
  token,lemma 即為「解す」)。
- G4 以修正前的 dry-run 基線(4,297 張)做對照,量化驗證:
  「〜切れない」「〜まくる」「お腹が空く」等已知污染樣本全數被拒。

**非目標**
- 不清查/重生成**已存在**的錯誤卡片——先擋住新增,存量清理配合刪除工具鏈另案。
- 不改 ES 查詢與索引(Sudachi 正規化維持現狀;驗證是 ES 之後的第二道濾網)。
- 不處理「同表層多卡搶句的配額公平性」——驗證後各卡只拿讀音相符的句子,
  搶句問題自然消解為「先到先得但不會拿錯」,配額再平衡不在本案。

## 3. 設計決策

### D1:擴充既有驗證器,不另起爐灶;VerbPair 跨包引用

`candidate_validator.py` 留在 `JP_CoreVerb/pipeline_components/`,VerbPair 直接 import
——倒向依賴已有先例(CoreVerb 的 generate 引用 `JP_VerbPair.pipeline_components` 的
DedupManager/uploader),兩包互引是既成慣例,搬到 common/ 的收益不抵 CoreVerb
呼叫端全改的成本。**放棄**:複製一份到 VerbPair(修補要做兩次,本次要加的規則
CoreVerb 同樣受益)。

### D2:讀音驗證用 UniDic 語彙素読み(feature[6]),期待讀音從母卡 furigana 導出

- token 側:UniDic `feature[6]`(lForm,片假名);`feature[7]`(lemma)取法
  已對齊 `build_nlp_index.py`,同一 feature 序列往前一格即是。
- 期待值側:母卡欄位本來就帶 furigana(`埋[う]まる`),以
  `jp_core_verb_handler.to_pure_kana` 同款正規式導出純假名(うまる),
  平→片後與 lForm 比對。**這要求 `_clean_verb_field` 之後保留 raw(帶標音)版本**
  ——現行 `generate_child_cards.py` 只留去標音表層,需把 (表層, 期待讀音) 成對傳遞。
- 邊界:lForm 缺值(`*`)時**放行讀音關**只驗 lemma(寧可漏擋不誤殺);
  期待讀音為空(母卡無標音)同樣跳過讀音關。
- 驗證器介面新增可選參數 `expected_reading: str | None = None`,
  預設 None = 不驗讀音 → **CoreVerb 呼叫端零改動、行為回歸不變**(G1)。

### D3:新規則「複合動詞後項拒絕」+ per-verb 白名單

- 規則:目標 token 的**前一** token 詞性為「動詞」→ 拒(對稱於現有規則②的
  前項拒絕)。擋下「使い**切れ**ない」「弾き**まくる**」——連用形直接接續
  的後項在自他動詞教學語境下必然不是獨立用法。
- 「食べて**みる**」由現有規則③(て/で)繼續負責,不動。
- per-verb 白名單:沿用現有 `allow_auxiliary` 機制,新增 `allow_compound_suffix`
  ——預設 False;若某動詞(如 込める的「〜込める」?)確有需要再逐詞放行。
  兩旗標由 VerbPair 側的設定檔提供(見 D4)。

### D4:`extra_search_keywords.json` 擴充為 per-verb 驗證設定

現行格式 `{nid: {表層: [擴展關鍵字]}}` 升級為向下相容的:

```json
{
  "1782042908548": {
    "縒れる": {"extra_keywords": ["よれる"]},
    "撚る":   {"extra_keywords": ["よる"], "allow_auxiliary": false}
  }
}
```

- 讀取端相容舊格式(值為 list 時視為 `{"extra_keywords": [...]}`)。
- 有了讀音+lemma 驗證後,先前止血移除的「よる」擴展**可以安全加回**
  (によって 的 lemma=因る/依る 會被 lemma 關擋掉)——這是本案完成的直接紅利。

### D5:接入點與失敗語意

- 接入點:`process_keyword_up_to_target()` 內,拿到 ES row 之後、
  `dedup_manager.prepare_generation()` 之前。ES 結果自帶 `dialogue` 文本
  (`elasticsearch_client.py:168`),**零額外查詢**;驗證失敗 `continue` 換下一句,
  游標邏輯不變。
- 驗證只看**目標句本身**(ES 命中句),不看上下文對話——與 CoreVerb 相同。
- tagger 生命週期:`fugashi.Tagger()` 在 main 建一次注入(CoreVerb 同款),
  dry-run 與真實模式都走驗證(dry-run 的預估數才有意義)。
- 拒絕統計:沿用 `REJECTION_*` 常數,執行總結報告新增
  「各拒絕原因 × 動詞」分佈,長期可觀察哪些動詞該調整搜尋設定。

### D6:依賴與環境

fugashi + unidic-lite 已是 CoreVerb 管線的既有依賴(`requirements.txt` 於
PR #10 補上 fugashi),VerbPair 接入**不新增依賴**。Windows 下 Tagger 初始化
約 1–2 秒,單次執行一次,4,300 句級驗證耗時預估 < 10 秒,可忽略。

## 4. 改動清單

### 驗證器(CoreVerb 側,回歸相容)

| 檔案 | 改動 |
|---|---|
| `JP_CoreVerb/pipeline_components/candidate_validator.py` | 新增 `expected_reading` 參數(lForm 讀音關)、規則②'複合動詞後項拒絕、`allow_compound_suffix` 旗標、`REJECTION_READING_MISMATCH`/`REJECTION_COMPOUND_SUFFIX` 常數 |
| `JP_CoreVerb/tests/test_pipeline_units.py` | 補讀音關與後項拒絕的假 token 測試;既有測試不改(回歸保證) |

### VerbPair 接入

| 檔案 | 改動 |
|---|---|
| `JP_VerbPair/generate_child_cards.py` | ①`_clean_verb_field` 保留 (表層, 期待讀音) 對;②`process_keyword_up_to_target` 接入 `validate_candidate`;③main 建 Tagger 注入;④總結報告加拒絕原因分佈 |
| `JP_VerbPair/extra_search_keywords.json` | 升級 D4 新格式;加回「よる」擴展 |
| `JP_VerbPair/mark_skipped_es_data.py` | 同樣接入驗證(它共用同一條選句邏輯,避免兩邊行為分岔)——實作時若確認其用途不需驗證,降級為只加註記 |

### 測試

- 驗證器單元測試(假 tagger):讀音不符拒(うずまる卡 vs うまる句)、
  lemma 收斂(ほぐす句 → 解す)、〜切れない/〜まくる 後項拒、
  によって lemma 拒、CoreVerb 舊行為回歸。
- VerbPair 端:以 2026-08-27 dry-run 抓到的實際污染句(script_id 1207/347/7660/10525 等)
  做整合測試素材,斷言全數被拒。

## 5. 實作順序

- **P0 — 驗證器擴充 + 單元測試**:讀音關、後項拒絕、旗標,CoreVerb 回歸綠燈。
  先做:這是純函式層,無 I/O,測試最便宜。
- **P1 — VerbPair 接入**:generate_child_cards 四處改動 + 設定檔升級。
- **P2 — 對照驗證(go/no-go 閘門)**:重跑全量 dry-run,與基線(4,297 張)對照:
  已知污染樣本(§1 清單)應全數消失;正常動詞(めくる、どく、済む、決まる…)
  數量不應異常下降(容忍 ±10%,超出即回頭查誤殺)。通過後才視為完成。
- **P3 —(可選)加回「よる」擴展並驗證**:P2 通過後的紅利驗收。

## 6. 風險與未知

- **UniDic lemma/lForm 欄位位置假設**(feature[6]/[7]):與 `build_nlp_index.py`
  同源,但 unidic-lite 與完整版 UniDic 欄位序不同的傳聞需在 P0 以真實 Tagger
  抽測 10 個已知詞確認;不符則改用 fugashi 的 named feature(`token.feature.lForm`)。
- **誤殺風險**:讀音關對 lForm 缺值放行(D2)、後項拒絕有 per-verb 白名單(D3)、
  P2 設 ±10% 數量閘門——三層保險。仍誤殺的極端詞以 `allow_*` 旗標逐詞放行。
- **mark_skipped_es_data.py 行為確認**:其與 generate 共用選句邏輯的耦合程度
  待實作時確認,若接入成本高則列後續項並在檔頭註記行為差異。
- **與 PR #11 的合併順序**:本分支基於 main,PR #11(刪除工具鏈)也改了
  `generate_child_cards.py`(僅 import 與 DedupManager 參數,3 行)。
  後合併者需 rebase,衝突面極小。

## 6.5 實作紀錄與計劃出入(2026-08-27)

- **UniDic 語彙素統合造成的誤殺與修正**:實測 lemma 精確比對會整批誤殺字形變體
  (帰る lemma=返る、治る→直る、代わる→変わる、刺す→**差す-他動詞**、
  混ぜる→交ぜる、現れる→現われる、貯める→溜める、釣る→吊る、閉める→締める)。
  修正:lemma 比對放寬為「lemma(去 `-他動詞` 類細分後綴)或 **orthBase**
  (feature[10],書字形基本形)任一相符」;「による」(lemma=因る、orthBase=よる)
  對撚る兩關皆不符,防線不破。此修正對 CoreVerb 同步生效(其 帰る/治る 類
  動詞先前同樣被全拒)。
- **規則②'對 CoreVerb 同步生效**:計劃原寫「CoreVerb 行為回歸不變」,實作時
  確認 CoreVerb 既有測試無任何依賴「複合動詞後項放行」的斷言,且後項污染
  (思い**出す**型)CoreVerb 同樣存在——規則②'預設對兩管線生效,屬有意識的
  行為改進而非回歸破壞。
- **同表層多讀的先天限制與 `ignore_reading` 逃生口**:unidic-lite 對同漢字
  表層是上下文盲的固定選讀(ドアが開く→ヒラク、タバコを止める→トメル、
  お腹が空く→アク),讀音關在這類卡上退化為「贏者全拿」:ひらく卡拿走全部
  開く句,**あく/やめる/けがれる 卡歸零**。這是 MeCab unigram 的能力邊界,
  無法在本層解決。已加 per-verb `ignore_reading` 設定(關讀音關、其餘三關
  照常)供逐卡取捨;贏者全拿(自洽但輸家卡餓死)vs 關讀音關(兩卡隨機分句)
  的決策留給人工,見驗收清單末項。
- **mark_skipped_es_data.py 降級為註記**:確認其為一次性維運腳本(只 UPDATE
  既有 DB 紀錄、不創新卡),未接驗證,檔頭已加行為差異註記(計劃預留選項)。
- **對照驗證結果**(基線 4,297 → 驗證後 4,031 張,擋下 1,017 句):
  已知污染全滅——による 84 句(lemma 關)、〜まくる 12 句(後項關)、
  〜切れない 14 句(後項關)、開く讀音分流 93 句;弾く 20→0(はじく卡先前
  拿的全是ピアノを弾く=ひく錯讀句)、捻る 5→0(全是ひねる)、降る 3→0
  (全是ふる,卡標くだる)——三者歸零皆屬正確清洗。下降超過 10% 的 56 個
  動詞逐一核對,除同表層多讀的贏者全拿情形外均為預期清洗;唯一上升項
  治す 0→7 為 orthBase 修正的正向收益。

## 7. 驗收標準

- [x] 驗證器:讀音關、後項拒絕上線,CoreVerb 既有單元測試全綠(回歸;新增 12 例,select_diverse 的 5 個 fail 為 main 上既存問題,與本案無關)。
- [x] VerbPair dry-run:による 84 句 lemma 關全拒、〜まくる 後項關拒 12 句、〜切れない 後項關拒 14 句,拒絕原因正確分類。(お腹が空く=すく 為 unidic-lite 能力邊界,見 §6.5)
- [x] 正常動詞對照:下降 >10% 的 56 動詞逐一核對均屬預期清洗或多讀限制,無誤殺;めくる、どく、済む、決まる等正常。
- [ ] 同表層異讀卡:靠 lForm 分流已生效(開く 93 句分流),但 unidic-lite 上下文盲導致贏者全拿——**待人工決策**:餓死卡(あく/やめる/けがれる)刪卡、合併、或設 `ignore_reading`。
- [x] 「よる」擴展已加回,撚る 命中 0(語料無真撚る句,84 句による全拒)。
- [x] 執行總結報告輸出拒絕原因 × 動詞分佈。
- [x] `extra_search_keywords.json` 新舊格式皆可讀(loader 正規化,舊 list 格式視為 extra_keywords)。
