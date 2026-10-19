# FIX — `/sync` 刪光關聯、Anki 搜尋跳脫錯誤，以及 P0 的其他兩項

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-19 |
| **狀態** | ✅ 實作完成（分支 `fix/relation-sync-and-search-escape`，2026-10-19）；§8 尚待部署後確認的項目見該節。§9 的待決策 1、2 採建議值（超過一半且超過 20 列；TG 不提供強制），3、4、5 不在本次範圍 |
| **性質** | 調查結果 + 修正設計；2026-10-19 經一輪審查，修正了查證不足的敘述並補上並發情況（見各節「審查後補充」） |
| **範圍** | `backend/app/services/relation_service.py`、`backend/app/api/relations.py`、`backend/app/bot/handlers/commands.py`（`/sync`）；`backend/app/infrastructure/anki/`（新的搜尋跳脫模組）與所有組 Anki 查詢的地方；`backend/tests/test_jp_verb_reading_judgments.py`；`docs/bk/` 中與事實不符的 F002 紀錄（01、02、04、06、09、11、12、README） |
| **來源** | `docs/wip/backlog.md` 的 T1、T2（原出自 `docs/archive/tg_bot_copy_review_2026-10-18.md` §6）；`docs/wip/web_ui_parity_FEAT_2026-10-18.md` §6 第 1 點；`docs/archive/capability_routing_FEAT_2026-10-19.md` §11.3 |
| **不動** | 關聯資料表的結構；`card_browse_service` 的對外行為；Anki 筆記類型與欄位 |

---

## 1. 結論

| # | 項目 | 嚴重度 | 調查結果 | 處理 |
|---|---|---|---|---|
| 1 | `/sync` 收到空清單時刪光所有關聯 | **高**（會遺失資料） | 實測確認：`sync_with_anki([])` 把 5 筆關聯全部刪除。文件記載 2026-07-09 已修（F002），但程式與測試**從未存在**於 git 歷史 | 空清單一律拒絕；先算出要刪的列再刪；大比例刪除需明確確認 |
| 2 | 外語糾錯重複檢查的查詢跳脫錯誤 | **中** | 在正式 Anki 上實測：`_`、`*` 被當成萬用字元，會比對到別張卡；`"` 被寫成 `""`，查詢不報錯但永遠找不到，重複檢查形同失效 | 抽出共用的搜尋跳脫，所有組查詢的地方改用它 |
| 3 | 依賴開發者 `.env` 的測試 | 低 | 斷言寫在 `mock.patch` 區塊外，比對到 `.env` 的值；沒有 `.env` 的環境必定失敗 | 斷言移進區塊內 |
| 4 | TG 實機錄音評分確認 | 驗證 | 能力路由 R2 後還沒用真實語音走過一次 | 依 §5.2 的清單確認 |

另外，調查時發現 **`docs/bk/` 的修復紀錄大量與程式不符**（§6.1），並非只有 F002。這不在本計畫範圍，但會影響之後所有依賴這些文件的判斷，建議另開一項全面核對。

---

## 2. 調查：`/sync` 刪光關聯

### 2.1 程式現況

以下行號都是修正前（`3e93c6b`）的程式。

`RelationService.sync_with_anki()`（`backend/app/services/relation_service.py:154`）一次執行：

```python
delete(CardRelation).where(or_(
    and_(source_note_id IS NOT NULL, source_note_id NOT IN valid_note_ids),
    and_(target_note_id IS NOT NULL, target_note_id NOT IN valid_note_ids),
))
```

`valid_note_ids` 是空清單時，SQLAlchemy 把 `NOT IN ()` 展開成恆真條件，`WHERE` 等於「所有起點或終點不是 NULL 的列」，也就是整張表。

兩個入口都直接把 `anki_client.find_notes("deck:*")` 的結果傳進去，沒有任何檢查：

| 入口 | 位置 | 認證 |
|---|---|---|
| TG `/sync` | `backend/app/bot/handlers/commands.py:438-439` | TG 白名單 |
| `POST /api/v1/relations/sync` | `backend/app/api/relations.py:204-207` | `verify_api_key`；正式機 `API_SECRET_KEY` 為空，實際上不檢查（§6.2） |

### 2.2 實測

以記憶體 SQLite 建 5 筆關聯後呼叫 `sync_with_anki([])`：

```text
sync_with_anki([]) 刪除 5 筆，剩下 0 筆（原本 5 筆）
```

### 2.3 什麼情況會拿到空清單或不完整的清單

- **（推測，未實測）** AnkiConnect 有回應，但收藏還沒載入或是空的：例如 `anki-desktop` 容器剛重啟、設定檔還在載入，或 AnkiWeb 正在下載整個收藏。這些情況下 `findNotes` 是回 `[]` 還是回錯誤，沒有實際觀察過（要驗證得重啟正式的 Anki，不宜隨意做）。不論哪一種，防護都應該放在服務層，不依賴 AnkiConnect 的行為。
- 不完整的清單（例如只載入了一部分）不會觸發「空清單」，卻同樣會把大量關聯當成孤兒刪掉。所以只擋空清單不夠，見 §3.1 的比例防護。
- `find_notes()` 前會先同步一次 AnkiWeb（`client.py:551`）。同步若觸發「整個收藏覆蓋」，期間的結果也不可靠。

### 2.4 正式機現況（2026-10-19，唯讀查詢）

| 項目 | 數值 | 意義 |
|---|---|---|
| Anki 筆記數（`deck:*`） | 17,394 | `NOT IN` 一次帶 17,394 個參數。SQLite 3.46 的上限是 32,766，目前還在範圍內，超過後 `/sync` 會直接失敗 |
| `card_relations` 列數 | **0** | 目前沒有資料可被刪 |
| 近 5 天新增的 Anki 筆記（`added:5`） | **0** | 這段期間沒有生成過子卡，關聯表為 0 筆與此一致；不能據此斷定關聯寫入本身正常（要等下一次生成後確認關聯表有新列） |
| 部署前的備份 `fluencytides.db.bak-20261019-pre-capability-routes` | 0 | 同上 |

**結論：** 目前沒有資料處於危險中，但自他動詞與核心動詞的子卡生成（`jp_verb_pair_handler.py:351`、`jp_core_verb_handler.py:401`）每次都會寫入關聯；知識圖譜的關聯只存在資料庫、無法從 Anki 重建。開始生成之後，一次 `/sync` 就可能全部清空。

### 2.5 文件與程式不一致

`docs/bk/01_Project_Overview.md:383`、`docs/bk/11_Implementation_Log.md:38` 記載「2026-07-09 在 `sync_with_anki` 開頭加空列表防護」；`docs/bk/12_Implementation_Log.md:22` 記載回歸測試 `test_relation_sync`。實際查證：

```bash
git log --all --oneline -S "if not valid_note_ids"
```

```bash
git log --all --oneline -- "*test_relation_sync.py"
```

兩者都沒有任何結果。這些文件是在 `ceac06c`（2026-08-09，大批補文件的 commit）一起加入的，防護與測試則從未進入版本控制。

---

## 3. 方案：`/sync`

### 3.1 服務層

`sync_with_anki()` 改成「先算再刪」：

0. （實作審查後補充）先明確與 AnkiWeb 同步一次（`sync(raise_errors=True)`）。失敗不中止，以本機收藏為準，結果標在回傳的 `ankiweb_synced` 並記 WARNING，事後能把刪除與同步失敗連在一起。
1. **掃描開始時間**：在取 Anki 筆記（`find_notes("deck:*")`）**之前**，以**資料庫的時鐘**記下時間；只有 `created_at` 早於這個時間 2 秒以上的關聯才可能被當成孤兒（`created_at` 由資料庫填入、SQLite 只到秒，所以用同一個時鐘並留餘裕）。實作時介面改為 `sync_with_anki(anki_client, force=...)`，由服務層自己依序記時間、取筆記，呼叫端不會弄錯順序。（審查後補充：否則 `/sync` 掃描期間剛生成的子卡，其筆記不在掃描結果裡，新建的關聯會立刻被刪掉。`card_relations.created_at` 已存在，為 UTC；比較時兩邊都用 UTC。）
2. `valid_note_ids` 為空：拋 `RelationSyncEmptyAnkiError`（`error_code` 為 `RELATION_SYNC_EMPTY_ANKI`），訊息說明「Anki 回傳 0 張筆記，為避免誤刪已停止清理（強制清理也不會執行）」，並附下一步：AnkiWeb 同步也失敗時請先在 Anki 處理同步（同步失敗很可能就是原因），否則請確認 Anki 已開啟並載入收藏。不刪任何資料。兩種拒絕都是 `RelationSyncRefusedError`（`FluencyTidesError` 子類，HTTP 409）的子類；實作審查後拆成兩個 `error_code`，網頁才分得出「能不能強制」。
3. 讀出關聯表裡考慮範圍內（`created_at` 早於掃描開始 2 秒以上，即 `created_at < cutoff`）的列出現過的所有筆記 ID（`source_note_id`、`target_note_id` 的聯集，去掉 NULL），在 Python 端與 `valid_note_ids` 做差集，得到孤兒 ID。
   - 這樣也避開了 `NOT IN` 帶上萬個參數：參數只剩孤兒 ID，並分批（每批 400 個）以 `IN` 刪除。每條 DELETE 帶 2 × 批次 + 1 個參數（起點、終點各展開一次，加上截止時間），400 → 801 個，在舊版 SQLite（3.32 之前）的 999 上限內。所有批次在同一個 transaction 裡，最後才提交：中途任何失敗都不會留下刪到一半的結果。
4. 比例防護（在刪除之前檢查）：要刪的關聯超過考慮範圍內關聯的 50%，且超過 20 列時，除非呼叫端帶 `force=True`，否則拋 `RelationSyncNeedsForceError`（`error_code` 為 `RELATION_SYNC_NEEDS_FORCE`），訊息附上「將刪除 N / 總數 M 筆」。剛好 20 列或剛好一半都不擋。
5. 刪除條件同樣限定 `created_at < cutoff`。
6. 回傳值改為小型結果物件：刪除數、掃描的筆記數、孤兒筆記 ID 數、`ankiweb_synced`，讓各入口都能顯示。資料庫沒回報刪除筆數（`rowcount` 為 None 或負數）時記 WARNING，改用同一個 transaction 裡算出的預估刪除數（資料已刪除並提交，顯示「移除 0 筆」會誤導）；實際刪除數與預估不同時也記 WARNING。`ankiweb_synced` 為必填，忘了傳不會被當成同步成功。

服務層的訊息陳述事實（發生了什麼、刪了幾筆）與兩邊都適用的下一步；只有某個入口能做的事（例如網頁的強制清理）由該入口補上。

### 3.2 入口

| 入口 | 改動 |
|---|---|
| TG（全域） | 新增全域錯誤處理（`bot/handlers/errors.py`）：handler 沒攔下的例外記下 traceback，並通知使用者這次操作沒有完成、結束按鈕轉圈。原本只有 log，使用者端完全沒有反應 |
| TG `/sync` | 被拒絕時回覆原因與數字（例如「⚠️ 沒有清理：Anki 回傳 0 張筆記…」）。TG 不提供 `force`：要刪超過一半而被擋時，指出到網頁「Knowledge Graph」按同步並確認強制清理。連不上 Anki、資料庫錯誤也都更新狀態訊息並說明「未刪除任何關聯」（原本資料庫錯誤會讓訊息停在「⏳ 清理中」）。AnkiWeb 同步失敗時在完成訊息後補一行說明 |
| `POST /api/v1/relations/sync` | 新增 `?force=true` 查詢參數（只放寬比例防護，空清單永遠拒絕）；被拒絕時回 409 與 `RELATION_SYNC_EMPTY_ANKI` 或 `RELATION_SYNC_NEEDS_FORCE`；回應改為 Pydantic 模型 `RelationSyncResponse`，多了 `scanned_notes`、`orphan_notes`、`ankiweb_synced` |
| 網頁知識圖譜「Sync」 | 成功時顯示掃描張數與移除筆數；收到 `RELATION_SYNC_NEEDS_FORCE` 時跳確認框，確認後帶 `force=true` 重送；`RELATION_SYNC_EMPTY_ANKI` 只說明原因；非後端錯誤另記 `console.error` |

### 3.3 文件

更正 `docs/bk/01`、`02`、`04`、`06`、`09`、`11`、`12` 與 `docs/bk/README.md` 中 F002 的狀態（審查後補充：原本只列了其中四份）：註明「2026-10-19 查證程式中沒有這個防護，於本計畫實作」，不刪原紀錄。

---

## 4. 調查與方案：Anki 搜尋跳脫

### 4.1 在正式 Anki 上的實測（唯讀，只用 `findNotes` / `notesInfo`）

對象：`日本語::外語糾錯::母卡片` 裡的 `Expression_Master_Dark`（6 張）。

| 查詢（`"My_Original:<值>"`） | 結果 | 說明 |
|---|---|---|
| 原值，不跳脫 | 1 | 正常 |
| 原值第 4 個字換成 `_` | **1** | `_` 是單一字元萬用字元：查詢比對到不完全相同的卡 |
| 原值前 3 個字 + `*` | **1** | `*` 是任意長度萬用字元 |
| 原值第 4 個字換成 `\_` | 0 | `\_` 照字面比對，跳脫有效 |
| `a""b`（現在的引號寫法） | 0，**不報錯** | `""` 不是 Anki 的跳脫寫法，查詢被解析成別的條件，靜默地找不到 |
| `a\"b` | 0，不報錯 | `\"` 才是正確寫法 |
| 含換行的原值，原樣查詢 | 1 | 換行照字面比對即可 |
| 含換行的原值，換行換成 `<br>` | 0 | 欄位搜尋比對的是儲存的原始內容 |
| 含 HTML 的欄位（`Error_Comparison`），帶標籤的原值 | 1 | 欄位搜尋**不去掉 HTML**，比對儲存的原始字串 |
| 同一欄位，去掉標籤後的文字 | 0 | 同上 |

審查後補充的**正向驗證**（上表只證明了跳脫後不再誤配，沒證明跳脫後仍配得到）。以「依序跳脫 `\`、`"`、`*`、`_`」處理後查詢真實的值：

| 對象（全收藏） | 跳脫後精確查詢 | 說明 |
|---|---|---|
| 含 `_` 的 `Card_ID` | 1 | `\_` 照字面比對得到 `_` |
| 含 `"` 的 `Context` 欄位 | 1 | `\"` 照字面比對得到 `"` |
| 含 `:` 的 `Context` 欄位（冒號不處理） | 1 | 指定欄位時，值中間的冒號不跳脫也比對得到（後來改為一律跳脫，見下表） |

實作審查後再補的**冒號**驗證（同樣唯讀）：

| 查詢 | 結果 | 說明 |
|---|---|---|
| 不指定欄位的 `"值"`，值含冒號 | 0 | 第一個冒號被當成「欄位名:值」的分隔 |
| 同上，冒號跳脫為 `\:` | 與預期相同 | 照字面比對 |
| 指定欄位，值以 `re:`、`nc:` 開頭 | 被當成正規表示式／忽略重音搜尋 | 跳脫成 `re\:` 後照字面比對 |
| `deck:"日本語\:\:外語糾錯"` | 29，與不跳脫相同 | 跳脫後的 `::` 照常比對子牌組 |
| `note:"Expression\_Master\_Dark"`、`note:"Speaking\_Coach\_Dark"` | 6、63，與不跳脫相同 | |

`My_Original` 寫入時沒有任何轉換（`expression_handler.py:227` 直接存使用者輸入的原文），所以重複檢查只要把同一段原文正確跳脫後查詢即可。需要跳脫的字元是 `\`、`"`、`*`、`_`、`:`（`\` 必須最先處理，否則會重複跳脫）。冒號一律跳脫：不跳脫時，不指定欄位的查詢會壞掉，以 `re:`、`nc:` 開頭的值會被當成特殊搜尋；跳脫後各種情況都照字面比對（上表）。

### 4.2 現在的行為與後果

修正前（`3e93c6b`）的 `expression_fsm.py:453-458`：

```python
original_text = data.get("original_text", "").replace('"', '""')
query = f'deck:"{deck_name}" note:"{model_name}" "My_Original:{original_text}"'
```

| 原文含有 | 後果 |
|---|---|
| `"` | 查詢靜默地找不到 → 判定「沒有重複」→ **建出重複的母卡與子卡** |
| `_`、`*` | 可能比對到**不同原文**的舊卡 → 跳出「發現重複」。使用者若選「覆蓋」，新卡寫入成功後會**刪掉那張不相干的舊卡**及其子卡（畫面會顯示舊卡的修正內容，使用者有機會發現，但沒有任何阻擋） |
| `\` | 可能把後面的字元變成跳脫，結果不可預期 |

### 4.3 其他組查詢的地方

位置的行號與「現況」都是修正前（`3e93c6b`）的程式。

| 位置 | 插入的值 | 現況 | 風險 |
|---|---|---|---|
| `bot/handlers/fsm/expression_fsm.py:458` | 使用者輸入的原文 | 跳脫錯誤 | 中：見 §4.2 |
| `services/anki_model_manager.py:356`（`_lookup_duplicate_location`） | 新筆記第一個欄位的內容 | 只加外層引號，沒跳脫；而且是全收藏搜尋 | 中：內容含 `"` 時查詢壞掉、含 `_`/`*` 時回報錯的「重複位置」。只影響錯誤訊息，不刪資料 |
| `api/relations.py:58`（知識圖譜依牌組篩選） | 前端傳來的牌組名 | 只加外層引號 | 低：牌組名含 `_` 時多撈到名稱相近的牌組 |
| `services/task_handlers/{speaking_coach,speaking_trilingual,vocabulary_mining}_handler.py`（共 4 處） | 牌組名 | `"deck:{deck}"`，沒跳脫 | 低：同上 |
| `bot/handlers/{commands,callbacks,messages,voice}.py`（共 6 處 `Card_ID:{card_id}`） | Deep Link 裡的卡片 ID | 沒加引號、沒跳脫 | 低：Telegram 的 start 參數只允許 `A-Za-z0-9_-`，最壞是 `_` 當萬用字元；現有 ID 格式下實際撞不到 |
| `bot/handlers/fsm/expression_fsm.py:941` | 母卡 note ID（整數） | — | 無（仍一併改用 `note_type_term` + `field_term`，寫法一致） |
| `services/card_browse_service.py`（`deck_search_term`） | 牌組名 | ✅ 已正確跳脫（`_SEARCH_SPECIALS`），有測試 | 無；本計畫把它的實作搬去共用 |

### 4.4 方案

1. 新模組 `backend/app/infrastructure/anki/search.py`：
   - `escape_search_value(value: str) -> str`：依序跳脫 `\`、`"`、`*`、`_`、`:`。
   - `field_term(field: str, value: str) -> str`：回傳 `"欄位:跳脫後的值"`，整段加引號（值裡有空白也安全）。
   - `deck_term(deck: str) -> str`：由 `card_browse_service.deck_search_term` 搬過來；`card_browse_service` 改成匯入它，對外名稱不變。
   - `note_type_term(name)`、`note_types_query(names, deck=None)`：筆記類型條件（實作時補上，三個 handler 共用）。
2. §4.3 表中「現況」不是 ✅ 的地方全部改用這些函式。`Card_ID` 查詢改為 `field_term("Card_ID", card_id)`。
3. 測試：
   - `search.py` 的單元測試：五種特殊字元、反斜線不重複跳脫、空字串、含換行與 HTML 的值原樣保留。
   - 外語糾錯重複檢查：原文含 `"`、`_`、`*`、`\`、`:` 時，傳給 `find_notes` 的查詢字串正確。
   - 既有的 `deck_search_term` 測試照常通過。

---

## 5. 其他兩項

### 5.1 依賴 `.env` 的測試

`backend/tests/test_jp_verb_reading_judgments.py:231`：

```python
        assert settings.LLM_MODEL_NAME == "claude-opus-5"  # 設定未被改動
```

這行在 `with mock.patch.object(settings, "LLM_MODEL_NAME", "claude-opus-5")` 區塊**之外**，比對的是離開區塊後的真實設定（開發者 `.env` 的值）。本意是「建立覆寫的客戶端不會改到設定」，所以應該移進區塊內，比對被 patch 的值仍是 `claude-opus-5`。全套測試中沒有其他同類寫法（已以 `grep` 檢查）。

### 5.2 TG 實機錄音評分確認

R2 之後評分器每則語音依卡片的 handler 從能力路由建立，並在上傳音檔**之前**取得。自動測試與 NAS 啟動 log（`✅ 錄音評分器可用（recording_eval → listen）`）都正常，但真實語音的整段流程還沒走過。確認方式：

1. 對話卡（Speaking_Coach_Dark）錄一則：回覆有分數、逐字稿、評語，「模式」顯示 `gemini-3.5-flash`；Anki 卡片的 Recordings 多一筆，音檔可播放。
2. 三語卡（Speaking_Trilingual_Dark）任一語言錄一則：同上，且評語依該語言的提示詞。
3. 後端 log 出現 `錄音評分器已建立：recording_eval… → listen`，沒有 `錄音評分器無法建立`。

```bash
ssh jacky917@192.168.50.172 'docker logs fluencytides-backend 2>&1 | grep -aE "錄音評分器|AI 評分失敗" | tail -5'
```

---

## 6. 盤點時發現（不在本計畫範圍）

### 6.1 `docs/bk/` 的修復紀錄與程式不符

`docs/bk/11`、`docs/bk/12` 記載 2026-07 共四輪修復、「134 / 141 已修復」，並列出 48 個後端測試。抽查結果：

| 編號 | 文件記載 | 程式現況 |
|---|---|---|
| F002 | `sync_with_anki` 空列表防護 | ❌ 不存在（本計畫處理） |
| F004 | 生產模式 `API_SECRET_KEY` 為空時拒絕啟動 | ❌ 不存在：為空時直接跳過認證（`core/auth.py`）。README「API 認證」記載正式機刻意不設金鑰、改由外層控管，所以文件的「已修復」與 README 的部署決定互相矛盾 |
| F005 | Webhook 沒有 secret 時一律 403 | ❌ 不存在：只有設了 secret 才檢查（`api/webhook.py:44`） |
| F009 | 基線遷移 `7f3d1a2b4c5e` | ❌ 不存在 |
| F010 | 移除已提交的 `vite.config.js` / `.d.ts` | ❌ 兩個檔案仍在版本控制中 |
| F011 | 刪除後重置 `isDeleting` | ➖ 不適用：前端已沒有 `isDeleting` 這個狀態（元件改寫過），原問題不會發生 |
| F019 | CORS 改由設定管理 | ❌ 仍寫死 `localhost:5173`（`main.py:349`） |
| F029 | 合併重複的刪除函式 | ❌ 兩個函式都還在 |
| F071 / F085 / F086 / F112 | 新增 `escape_anki_search_value()` 統一跳脫 | ❌ 不存在（本計畫的 §4 處理同一件事） |
| 測試 | `test_api_smoke`、`test_config`、`test_anki_escape`、`test_relation_sync`、`test_llm_client`… | ❌ 都不存在 |
| F013 | 部署腳本建立共用網路 | ✅ 存在 |
| F018 | Bot 啟動失敗不阻止後端 | ✅ 存在 |

推測：那幾輪修復是在另一份工作副本上進行的，文件被提交了，程式沒有。**建議另開一項**：逐條核對 `docs/bk/06` 的 141 條，把文件狀態改回事實，真正還需要修的列入 backlog。

### 6.2 正式機的 API 沒有認證

正式機 `API_SECRET_KEY` 為空，`verify_api_key` 直接放行（即 F004 描述的 fail-open）。這是**已記載的部署決定**：README「API 認證」寫明存取控制交給外層——對外經 Cloudflare Tunnel，網頁與後端的 hostname 以 Cloudflare Access 登入保護，webhook 只放行 `/api/webhook`；家用區網視為可信。

實際狀態（審查後查證）：後端容器的 8000 埠綁在 NAS 的 `0.0.0.0`（區網內任何裝置都能直接呼叫，包括 `POST /api/v1/relations/sync` 與各種刪除端點）；前端的 nginx 在設定金鑰時會代為注入 `X-API-Key`，目前注入的是空值。要不要設定 `API_SECRET_KEY`（區網也要驗證）是部署決定，不在本計畫範圍。Webhook 的 secret 有設定，F005 在正式機不構成問題。

### 6.3 Anki 筆記超過 32,766 張時 `/sync` 會失敗

目前 17,394 張。§3.1 的「先算再刪」只用孤兒 ID 當參數、分批刪除，實作後這個問題一併消失。

---

## 7. 改動清單

| 檔案 | 改動 |
|---|---|
| `backend/app/infrastructure/anki/search.py`（新） | `escape_search_value`、`field_term`、`deck_term`、`note_type_term`、`note_types_query` |
| `backend/app/services/card_browse_service.py` | `deck_search_term` 改用 `search.deck_term` |
| `backend/app/bot/handlers/fsm/expression_fsm.py` | 重複檢查改用 `deck_term` / `note_type_term` / `field_term`；覆蓋時的子卡查詢（`Master_Note_ID`）改用 `note_type_term` + `field_term`；只有 Anki 查詢失敗才退回直接生成，並先告訴使用者跳過了檢查；流程狀態沒有原文時當成流程失效 |
| `backend/app/services/anki_model_manager.py` | 重複位置查詢改為「同筆記類型＋模型定義的第一個欄位完全相同」（Anki 的重複判定條件），失敗時記下查詢與 traceback、查無結果時記 INFO，仍退回原本的重複錯誤 |
| `backend/app/api/relations.py`、`backend/app/schemas/relation.py` | 圖譜牌組篩選改用 `deck_term`；`/relations/sync` 加 `force`、處理拒絕，回應改為 `RelationSyncResponse` |
| `backend/app/services/task_handlers/` 三個 handler | 牌組與筆記類型篩選改用 `note_types_query` |
| `backend/app/bot/handlers/{commands,callbacks,messages,voice}.py` | `Card_ID` 查詢改用 `field_term`；`/sync` 顯示拒絕原因 |
| `backend/app/bot/handlers/errors.py`（新）、`backend/app/bot/dispatcher.py` | 全域錯誤處理 |
| `backend/app/services/relation_service.py` | `sync_with_anki` 改為先算再刪、只處理掃描開始前建立的關聯、空清單與比例防護、回傳結果物件 |
| `backend/app/core/exceptions/services.py` | 新增 `RelationSyncRefusedError`（409）與子類 `RelationSyncEmptyAnkiError`、`RelationSyncNeedsForceError` |
| `frontend/src/api/client.ts`、`frontend/src/types/api.ts`、`frontend/src/pages/KnowledgeGraph.tsx` | `syncRelations(force)`、回應型別 `RelationSyncResponse`；確認後強制清理、顯示掃描張數 |
| `backend/tests/` | `test_anki_search.py`（新）、`test_relation_sync.py`（新）、外語糾錯重複檢查與 `/sync` 回覆的測試；修 `test_jp_verb_reading_judgments.py:231` |
| `docs/bk/01`、`02`、`04`、`06`、`09`、`11`、`12`、`docs/bk/README.md` | 更正 F002 狀態 |
| `docs/wip/backlog.md` | 實作後移除 T1、T2 |

---

## 8. 驗收

- [x] Anki 回傳 0 張筆記時拋 `RelationSyncEmptyAnkiError`（`RelationSyncRefusedError` 的子類），資料表筆數不變（實作後介面改為傳入 Anki 客戶端，由服務層自己取筆記，才能在取之前記下掃描時間）
- [x] 要刪的超過 50% 且超過 20 列時拒絕；帶 `force=True` 才刪
- [x] `valid_note_ids` 超過 32,766 個時照常運作（測試以 SQLite 建 40,000 個 ID）
- [x] 掃描開始之後才建立的關聯不會被刪（即使它的筆記不在掃描結果裡）
- [x] TG `/sync` 被拒絕時回覆原因與數字；API 回 409 與 `RELATION_SYNC_EMPTY_ANKI` / `RELATION_SYNC_NEEDS_FORCE`；網頁知識圖譜的同步按鈕顯示後端的原因，要刪超過一半時可確認後強制
- [x] 分批刪除跨多批時全部刪除；中途失敗時一筆都不留下刪除（測試）
- [x] 掃描時間在取 Anki 筆記之前取（測試：把順序反過來會失敗）
- [x] 連不上 Anki、資料庫錯誤時 TG 狀態訊息都會更新；handler 沒攔下的例外由全域錯誤處理通知使用者
- [x] 外語糾錯重複檢查：原文含 `"`、`_`、`*`、`\`、`:` 時查詢字串正確（單元測試）；查不到 Anki 時明講跳過檢查；正式 Anki 上已確認跳脫後的 `note:`、欄位條件結果正確
- [ ] 部署後在 TG 實際走一次外語糾錯，原文含 `_` 或 `"`
- [x] `test_jp_verb_reading_judgments.py` 在沒有 `backend/.env` 的 worktree 通過
- [x] 全套測試、ruff 通過
- [ ] §5.2 的 TG 實機錄音確認完成

```bash
cd backend && python -m pytest tests -q
```

```bash
cd backend && python -m ruff check app tests
```

---

## 9. 待決策

1. **比例防護的門檻**：建議「超過 50% 且超過 20 列」。門檻太低會讓正常的清理也被擋，太高則擋不住不完整的清單。
2. **TG `/sync` 要不要提供強制清理**：建議不提供，強制只走 API。
3. **§6.1 的文件全面核對**：要不要另開一項（建議要，否則之後的判斷都可能建立在錯的紀錄上）。
4. **§6.2 正式機要不要設定 `API_SECRET_KEY`**。
5. **（實作審查後）強制清理的上限**：網頁確認的是當下的「將刪除 N / M 筆」，帶 `force=true` 重送時服務層會重算、不再比對比例。若確認後 Anki 剛好換成另一個較小的收藏，強制清理可能刪得比確認時多（空收藏仍一律拒絕）。可行做法：重送時帶 `expected=N`，重算後超過就再拒絕。風險低，未在本次實作。
