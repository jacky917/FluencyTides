# FEAT — 同表層多讀的母卡歸屬:讀音判斷快取表 + 獨立判讀腳本

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-09-02 |
| **性質** | 追加功能(獨立判斷快取表 + 獨立判讀腳本 + 生成管線的查表過濾;移除 `search_keyword`) |
| **狀態** | 🚧 實作完成、本機驗證通過(2026-09-03);待部署新映像後跑判讀腳本與存量對帳 |
| **範圍** | 新表 `jp_verb_reading_judgments`;新的**日文專用、專案無關**判讀腳本 `JP_Common/judge_verb_readings.py --project …`;後端新增 `jp/` 命名空間下的判讀端點與模板;VerbPair 生成腳本加「查表過濾」(CoreVerb 同機制,待其出現同表層動詞時接);移除 `generated_sentences_log.search_keyword` |
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

### 2.1 新表 `jp_verb_reading_judgments`(判斷快取)

```sql
CREATE TABLE IF NOT EXISTS jp_verb_reading_judgments (
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
[獨立] judge_verb_readings.py ──寫入──▶ jp_verb_reading_judgments ◀──只讀── generate_child_cards.py [生卡]
            │                                                                    │
            └── POST /api/v1/jp/verb-readings/judge(後端,專案無關,新模板)        └── 表空 → 行為與現況完全相同
```

生卡流程**不呼叫 LLM 判讀音**;判斷全部由獨立腳本事先產生。兩者只靠那張表溝通,任一邊不存在都不影響另一邊。

### 3.1 獨立判讀腳本 `scripts/fastapi_client/JP_Common/judge_verb_readings.py`(日文專用、專案無關)

放在新目錄 `fastapi_client/JP_Common/`(與 `JP_VerbPair/`、`JP_CoreVerb/` 同級,承載日文跨專案的腳本;`query_backend_model.py` 這類語言無關的工具維持在頂層),以 `--project jp_verb_pair|jp_core_verb` 指定專案;母卡牌組、動詞欄位名等專案差異一律取自刪卡工具鏈既有的 `ProjectProfile` 註冊表(`scripts/local_anki/common/deletion/profiles.py`),該註冊表新增 `master_verb_fields`(VerbPair:`Intransitive_Word`/`Transitive_Word`;CoreVerb:`Word`)。不在腳本裡寫死任何專案。

1. 依 profile 掃該專案全部母卡,建同表層讀音表 `{表層: {讀音: 母卡id}}`,只保留讀音數 ≥ 2 的表層(VerbPair 目前 14 個、CoreVerb 目前 0 個;不落設定檔,母卡改動自動反映)。
2. 對每個表層收集待判 `script_id`:
   - ES 依表層搜尋的候選(與生卡相同的 `search_dialogue_by_verb`,`script_id` 游標分頁,`--limit` 控制上限);
   - **加上** `generated_sentences_log` 裡該專案、該表層已生成的紀錄(VerbPair 存量 117 筆),讓既有卡片一併受檢。
3. 排除表中已有判斷的;剩餘者每 20 句一批送後端端點,每句附:台詞原文、前後各 2 行(直接查 `scripts` 表,不走完整 ContextBuilder)、表層、候選讀音清單。
4. 回應寫入表(`reading` + 後端回傳的 `llm_model`)。
5. 結尾輸出**歸屬對帳報告**:對存量已生成紀錄,比對「判定讀音」與「所屬母卡讀音」,不一致者逐筆列出(id、句子、母卡讀音、判定讀音)——交人工複核與決策(改掛/刪除重生/保留)。

**以表層為單位,只搜漢字寫法**:けが/よご 兩張母卡共用「汚す」這個表層,ES 只搜一次、判斷只做一次,結果兩張母卡共用。假名寫法的句子(やめる、まくる)讀音本身就是明的、不存在歧義,不需要判——因此 ES 只用漢字表層搜尋,不帶母卡的假名擴展關鍵字;既有的漢字 `LIKE` 強校驗保證撈回的句子確實含該漢字。

**dry-run 行為**:與正式執行走完全相同的收集流程(掃母卡建多讀表 → ES 搜尋 → 併入存量紀錄 → 扣除已判),只是不呼叫後端端點、不花 LLM。輸出每個表層的「ES 候選 + 存量 − 已判 = 待判 → 分批數」與合計,例如:

```
汚す    ES 候選 143 + 存量 12 − 已判 0  = 待判 155 → 8 批(batch 20)
止める  ES 候選 200 + 存量 40 − 已判 40 = 待判 200 → 10 批
合計 14 個表層、待判 1,180 句、59 次呼叫
```

用途是在花錢前看清楚規模,並可搭配 `--surface` / `--max-surfaces` / `--limit` 調整到可接受的批次再正式跑。

**參數**(全部可選;模型與深度不給時沿用後端 .env 的設定,回應仍帶實際使用的 `llm_model`):

| 參數 | 說明 | 預設 |
|---|---|---|
| `--surface 汚す [止める …]` | 只處理指定表層 | 全部多讀表層 |
| `--max-surfaces N` | 本次最多處理幾個表層(順序固定,便於分次跑) | 不限 |
| `--limit N` | 每表層 ES 候選上限 | 200 |
| `--batch-size N` | 每次 LLM 請求送幾句 | **20**(見下) |
| `--model NAME` | 覆寫後端模型(claude-code:`opus-5` / `sonnet-5` / `haiku-4-5`…;傳給後端,不在腳本解析) | 後端設定 |
| `--effort LEVEL` | 覆寫思考深度(`low` / `medium` / `high`) | 後端設定 |
| `--dry-run` | 只列待判數量與分批計畫,不呼叫 LLM | — |
| `--rejudge` | 整個表層砍掉重判(須配 `--surface`,避免誤清整表) | — |
| `--rejudge-empty` | 只重判 `reading=''`(上次判不出來)的紀錄;典型用法:第一輪便宜模型跑完,把判不出的交給 `--model opus-5 --effort high` 再試 | — |
| `--rejudge-model LABEL` | 只重判 `llm_model` 等於該標籤的紀錄(某模型的判斷不可信、整批換掉) | — |
| `--yes` | 跳過正式執行前的規模確認 | — |

**快取跳過與重判規則**:是否跳過只看 `(script_id, 表層)` 是否已有紀錄,**不比對 `llm_model`**——一句固定台詞的讀音是客觀事實,不因換模型而改變;會變的只有「判對沒」。所以判斷預設是永久快取,重判必須是明確動作,否則每次改 `--model` 就整表重跑、快取形同虛設。三個 rejudge 參數互斥;`--rejudge-empty` 與 `--rejudge-model` 不需要 `--surface`(範圍已由條件限定,不會誤清整表)。重判以 upsert 覆寫該筆(`reading`、`llm_model`、`created_at` 一併更新),不留歷史版本——需要追溯時看 git 紀錄的執行報告即可。

**正式執行前的規模確認**:印出與 dry-run 相同的摘要(每表層「ES 候選 + 存量 − 已判 + 重判納入 = 待判 → 分批數」與合計)後暫停等待輸入,避免手滑對上千句開跑;`--yes` 跳過。dry-run 摘要中「重判納入」的筆數與「新句」分開列,例如 `待判 155(新 140 + 重判空值 15)`。

**`--batch-size` 的建議值寫在腳本註解裡**,理由要一起寫:每句附前後各 2 行,一批 20 句約 100 行對話、數千 token,模型仍能逐句對照;超過 40 句後逐項注意力下降、遺漏或串位的機率上升;低於 10 句則呼叫次數翻倍、省不到什麼。**推薦 20,上限硬性 40**(端點拒收 > 40)。判讀是「看上下文選讀音」的分類任務,不需要最深的思考:推薦 `--effort medium`;真的難判的句子模型應回空字串而不是硬猜,深度加大不會讓它更誠實。

判讀腳本與生卡腳本**不共用**模型設定——生卡用 .env 的預設,判讀可用較便宜的模型跑大量,兩者互不影響。

### 3.2 後端端點 `POST /api/v1/jp/verb-readings/judge`(專案無關)

獨立路由檔 `app/api/jp_verb_readings.py`(`APIRouter(prefix="/jp/verb-readings")`),不掛在 `verb_pair` 或 `core_verb` 之下——請求裡沒有任何專案概念(只有台詞、上下文、表層、候選讀音),日文的任何母卡驅動專案都能呼叫;路徑與檔名帶 `jp` 是因為「同表層多讀」是日文特有的問題,本服務同時承載 TOEIC/英語等其他語言的模組,不能讓語言專屬能力看起來像通用的。

- 請求:`{items: [{script_id, surface, candidates: [讀音…], line, context_before: [..], context_after: [..]}], model?: str, effort?: str}`,`items` 單次 ≤ 40 筆(超過回 422)。
- 回應:`{llm_model, results: [{script_id, reading}]}`,`reading` ∈ candidates 或 `""`;`llm_model` 為**實際使用**的模型標籤(含覆寫後的值),腳本寫入表時以此為準。
- **模型/深度覆寫**:`model` / `effort` 任一有給時,後端以該組合建立**請求範圍**的 LLM client(不動 `app.state.llm_client`、不寫回設定);兩者皆缺則用既有 client。覆寫值的合法性由後端驗證(不在白名單 → 422 並列出可選值),腳本只負責傳遞。
- **模型白名單的耦合**:`model` 覆寫值以後端 .env 的 `MODIFY_LLM_MODEL_NAME` 為白名單(有設才限制)。該清單是 Telegram 動態設定的既有機制,內容隨 provider 而異——provider 為 claude-code 時須在其中加入 claude 模型名,否則覆寫一律 422(錯誤訊息已附此提示;2026-09-03 本機實測即因清單只列 Gemini 模型而被擋)。`effort` 由 client 自身的白名單驗證,不經此清單。
- 新模板 `JP_VerbReading_Judge.j2`:給上下文與候選讀音,要求逐句判定;無法確定時明確回 `""`,**不猜**。模板只做這一件事,生成模板不動。
- 為什麼不讓腳本直連 LLM:專案原則是腳本不自組 LLM client、不讀 LLM 的 .env、標籤以後端回應為準(2026-08-28 曾因腳本自行推導標籤錯標 190 筆);claude-code 的認證也只在後端/容器配置。覆寫參數走端點,既保留單一事實來源,又給了每次執行指定模型的自由。

### 3.3 生卡腳本的改動(VerbPair)

只加一道**查表過濾**,位置在 ES 撈回候選之後、fugashi 驗證之前:

- 啟動時建同表層讀音表(與 3.1 同一段共用程式碼 `scripts/common/jp_homograph_table.py`,以 profile 為參數)。
- 候選的表層若在多讀表內,查 `jp_verb_reading_judgments`(每表層一次批量載入):
  - 有判斷且 = 本母卡讀音 → 放行;
  - 有判斷且 ≠ 本母卡讀音(含空字串)→ 跳過,log `讀音判斷:script_id=X 判為 よごす,非本母卡 けがす,跳過`,**不寫任何紀錄**;
  - **無判斷 → 放行**(與現況相同),log 一行 `未判讀` 供統計。
- 表層不在多讀表內 → 完全不查,零額外成本。
- 結尾統計:本輪「查表跳過」與「未判讀放行」各幾句,提醒使用者跑判讀腳本。
- fugashi 讀音關與 `ignore_reading` 設定**維持不變**——它是另一道獨立的、上下文盲的機械關,本計畫不改它的行為。

### 3.4 命名約定:日文專用能力一律帶 `jp`

本服務不只日文(另有 TOEIC / 英語口說等模板),而「同表層多讀」是日文特有的問題。因此本計畫新增的每個構件都在名稱上標明語言,與既有慣例對齊(腳本目錄 `JP_VerbPair`、模板 `JP_VerbPair_Child.j2`、卡片模型 `JP_*_Dark`):

| 構件 | 名稱 |
|---|---|
| 資料表 | `jp_verb_reading_judgments` |
| 端點 | `POST /api/v1/jp/verb-readings/judge`,路由檔 `app/api/jp_verb_readings.py` |
| 判讀腳本 | `scripts/fastapi_client/JP_Common/judge_verb_readings.py` |
| 共用模組 | `scripts/common/jp_homograph_table.py` |
| 模板 | `app/templates/prompts/anki/JP_VerbReading_Judge.j2` |

`generated_sentences_log`、`scripts` 等既有資料表沒有語言前綴,是歷史包袱,本計畫不回改。

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
| `scripts/common/database/init_db.py` | 新表 `jp_verb_reading_judgments` DDL;`search_keyword` 冪等 DROP |
| `scripts/common/database/reading_judgment_repository.py` | 新增:`get_many(script_ids, surface)`、`upsert_many`、`delete_by_surface` |
| `scripts/common/jp_homograph_table.py` | 新增:依 `ProjectProfile` 掃母卡建 `{表層: {讀音: 母卡id}}`(判讀腳本與兩條生卡腳本共用) |
| `scripts/common/jp_reading_filter.py` | 新增:生卡側查表過濾(`verdict` + `ReadingFilter`),只讀判斷表、不呼叫 LLM |
| `app/schemas/llm/jp_verb_reading.py`、`app/services/jp_verb_reading_service.py` | 新增:請求/回應/LLM 輸出 schema;服務層(渲染模板、請求範圍 client、fail-closed 正規化) |
| `scripts/common/jp_reading_filter.py` | 新增:生卡側查表過濾(`verdict` + `ReadingFilter`),只讀判斷表、不呼叫 LLM |
| `app/schemas/llm/jp_verb_reading.py`、`app/services/jp_verb_reading_service.py` | 新增:請求/回應/LLM 輸出 schema;服務層(渲染模板、請求範圍 client、fail-closed 正規化) |
| `scripts/local_anki/common/deletion/profiles.py` | `ProjectProfile` 新增 `master_verb_fields`(專案差異收斂於此,不散落各腳本) |
| `scripts/common/database/log_repository.py` | 移除 `search_keyword` 參數與 SQL 欄位 |
| `.../JP_VerbPair/pipeline_components/dedup_manager.py` | 移除 `search_keyword` 傳遞與 `_keyword_or_none` |
| `.../JP_VerbPair/generate_child_cards.py` | 啟動建多讀表;候選查表過濾;結尾統計;移除 `search_keyword=` |
| `scripts/fastapi_client/JP_Common/judge_verb_readings.py` | 新增日文專用、專案無關的獨立判讀腳本(`--project`,§3.1);`JP_Common/` 為新目錄 |
| `app/api/jp_verb_readings.py` | 新增專案無關路由 `POST /verb-readings/judge`(含 `model`/`effort` 請求範圍覆寫與白名單驗證、`items` ≤ 40);`app/main.py` 掛載 |
| `app/infrastructure/llm/factory.py`、`claude_code_client.py` | `create_llm_client(model=None, effort=None)` 與 client 建構子接受可選覆寫(缺省沿用 settings),供請求範圍 client 使用 |
| `app/templates/prompts/anki/JP_VerbReading_Judge.j2` | 新模板 |
| `scripts/common/database/canonicalize_verb_lemma.py` | 移除寫 `search_keyword` 的邏輯(腳本保留為歷史工具) |
| `tests/test_reading_judgments.py` | 回歸測試 |

### 回歸測試

- 多讀表:只收讀音數 ≥ 2 的表層;同讀跨母卡(繋がる)不收;三讀(退く)正確收三個
- 查表過濾:有判斷且相符 → 放行;不符/空字串 → 跳過且不呼叫 `prepare_generation`;**無判斷 → 放行**;非多讀表層 → 不查表
- 判讀腳本:已判過的不重送(即使 `llm_model` 不同);`--rejudge` 未配 `--surface` 拒絕;`--rejudge-empty` 只選空字串紀錄、`--rejudge-model` 只選該標籤紀錄、三者互斥;重判為 upsert 覆寫;回應中 `reading` 不在候選內時視為空字串並警告;歸屬對帳報告正確列出不一致;正式執行前有規模確認、`--yes` 可跳過
- 端點:輸入 > 40 筆拒絕;`model`/`effort` 覆寫建立請求範圍 client 且不動全域 client;不合法的覆寫值 422;回應 `llm_model` 反映覆寫後的實際模型;模板渲染含上下文與候選
- 腳本:`--batch-size` 上限 40;`--rejudge` 未配 `--surface` 時拒絕執行;`--max-surfaces` 順序穩定
- `search_keyword` 移除後全套件仍綠

## 6. 驗收

- [x] 前置:`verb_lemma` 拼寫修復已執行完畢(2026-09-03)
- [x] `init_db.py` 重跑(2026-09-03 本機)→ `jp_verb_reading_judgments` 就位、`search_keyword` 已移除、其餘欄位與唯一鍵 `uk_script_verb_project` 不變
- [x] 表為空時全牌組 dry-run(2026-09-03):預計 **1,438 張,與改動前完全一致**;多讀表 14 個表層正確建出;結尾統計「讀音不符跳過 0 句;未判讀放行 415 句」並提示跑判讀腳本;零例外
- [ ] 跑 `judge_verb_readings.py` 覆蓋 14 個表層(含存量 117 筆);對帳報告中的不一致逐筆人工複核後交決策
- [ ] 表填好後 dry-run:同表層動詞(止める / 開く / 汚す)兩側各自只拿到讀音相符的候選;log 出現「讀音判斷…跳過」
- [ ] 實際生成一輪,抽出新生成的同表層卡片逐張確認讀音歸屬正確
- [x] 全套件通過:221 passed(新增 `test_jp_verb_reading_judgments.py` 18 項;`test_dedup_canonical_lemma.py` 隨 `search_keyword` 移除同步更新)
- [x] 判讀服務端到端實測(2026-09-03,本機 claude CLI,`effort=low` 覆寫):4 句同表層多讀全部判對——口元汚れてます→よごす、看板を汚す→けがす、ドアが開いた→あく、もう止める→やめる;標籤 `(claude-code)opus-5@low` 正確反映覆寫;4.8 秒
- [x] 判讀腳本 dry-run(2026-09-03):14 個表層、待判 432 句、30 次呼叫;單一表層 `--surface 汚す`:ES 29 + 存量 7 → 待判 29、2 批;資源正常釋放

## 7. 後續(本計畫不做)

1. **同表層同讀跨母卡**:14 個表層是「同表層、同讀音、不同母卡」(如 `繋がる` 兩張母卡;`穢す`(557)與 `汚[けが]す`(921)更是同詞異漢字且前者以「汚す」為關鍵字),讀音判斷對它們無效,屬母卡設計問題,需另案盤點。
2. **孤兒紀錄**:`掛ける` 3 筆指向已刪除的母卡、`収まる` 2 筆是母卡改名前的舊紀錄。
3. **CoreVerb 接入查表過濾**:端點、判讀腳本(`--project jp_core_verb`)、多讀表模組皆已專案無關,CoreVerb 出現同表層動詞時只需在其生成腳本加同一段查表過濾。
4. **fugashi 讀音關與判斷表的關係**:兩者並存(前者機械、後者語境)。若日後判斷表覆蓋率高,可評估對多讀表層自動跳過 fugashi 讀音關;本計畫不動。
