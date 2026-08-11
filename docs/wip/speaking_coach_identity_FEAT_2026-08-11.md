# FEAT — 把卡片身分機制推廣到 Speaking_Coach_Dark, 並補齊該匯入路徑的三項缺陷

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-11 |
| **性質** | 新增機能設計 + 實作工作項(含既有匯入路徑的缺陷修復) |
| **狀態** | 📝 未實作 |
| **範圍** | `scripts/local_anki/Speaking_Coach_Dark/import_cards.py`、`scripts/local_anki/Speaking_Coach_Dark/clear_identity.py`(新檔)、`scripts/local_anki/Speaking_Coach_Dark/jsons/`(新目錄)、`app/core/config.py`(新增根牌組設定)、`.env.example` |
| **不動** | Anki 內既有 63 張卡片的**內容與錄音**、`Speaking_Coach_Dark` note model 的 8 欄位定義、**`common/card_identity.py` 的內容**(只 import 不修改)、**`Speaking_Trilingual_Dark/` 下的任何檔案**(屬 [card_identity_writeback_FEAT](../archive/card_identity_writeback_FEAT_2026-08-11.md) 的職責)、`app/bot/` 下任何 handler、`generate_interview_cards.py`(見 §2 非目標) |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | [`card_identity_writeback_FEAT_2026-08-11.md`](../archive/card_identity_writeback_FEAT_2026-08-11.md)(**前置案** —— 本案沿用其決策表與清除工具設計, 並依賴**該案 P5** 產出的 `common/card_identity.py`)、[`jsons/README.md`](../../backend/scripts/local_anki/Speaking_Trilingual_Dark/jsons/README.md)(Trilingual 的卡片撰寫準則, 本案需產出對應版本) |

---

## 1. 問題與動機

全專案掃描 39 個 note 查詢點後, **只有 `Speaking_Coach_Dark` 的匯入路徑仍以內容判斷卡片存在**
(bot 端一律用 `Card_ID:` 查詢, 其餘皆為列舉或錯誤回報用途)。而它的狀況比
`Speaking_Trilingual_Dark` 修復前更差 —— 以下四項均為**已定位**, 非推測。

### 1.1 完全沒有查重, 只靠 Anki 的第一欄位判定(已定位)

[`import_cards.py`](../../backend/scripts/local_anki/Speaking_Coach_Dark/import_cards.py) 全檔
**沒有任何 `find_notes` 呼叫**(grep 計數為 0), 唯一的重複防線是建卡選項:

```python
options=AnkiNoteOptions(allowDuplicate=False, duplicateScope="deck")   # :141-144
```

Anki 的重複判定只看 note 的**第一個欄位**, 而
[`Speaking_Coach_Dark.json`](../../backend/app/anki_models/Speaking_Coach_Dark.json) 的
`inOrderFields[0]` 正是 `Prompt`。因此:

- 改 `Prompt` 一個字 → Anki 不認為重複 → **建出重複卡**(與 Trilingual 修復前同一種病)
- 更嚴重的是**它連更新既有卡的能力都沒有** —— 只能建, 不能改。想修正既有卡片的
  `Context` 或 `References`, 只能手動在 Anki 編輯

### 1.2 卡片資料硬編在原始碼裡, 沒有可寫回身分的地方(已定位)

卡片內容是腳本內的一個 Python list([`:212 CARDS_TO_IMPORT`](../../backend/scripts/local_anki/Speaking_Coach_Dark/import_cards.py)),
目標牌組是常數([`:193 TARGET_DECK`](../../backend/scripts/local_anki/Speaking_Coach_Dark/import_cards.py)),
`DRY_RUN` 也是常數([`:196`](../../backend/scripts/local_anki/Speaking_Coach_Dark/import_cards.py))。
沒有 `jsons/` 目錄, 也沒有命令列參數。

這造成兩個後果:

- **身分無處可寫回** —— 本案的核心機制需要一個持久的檔案來承載 `cardId` / `noteId`
- 每次匯入新卡都要**編輯原始碼**, 而卡片內容(含個人面試答案)因此進入版控;
  對照 Trilingual 的 `jsons/` 是刻意 gitignore 的

### 1.3 `Card_ID` 不持久(已定位)

[`:102-104`](../../backend/scripts/local_anki/Speaking_Coach_Dark/import_cards.py) 雖然支援從資料
指定 `Card_ID`, 但沒有寫回機制 —— 硬編的 list 每次執行都重新求值, 未指定者
一律 `_generate_card_id()` 產生新值。實務上與 Trilingual 修復前相同:每次執行換一組 ID。

### 1.4 `Target_Language` 從未被寫入, 導致評分失去語言基準(已定位, 實機驗證)

model 有 `Target_Language` 欄位, 但建卡時的 `fields` 只填了 7 個
([`:126-137`](../../backend/scripts/local_anki/Speaking_Coach_Dark/import_cards.py)), **獨缺這一個**。
實機統計 63 張卡:

| `Target_Language` | 張數 |
|---|---|
| （空） | **62** |
| `ja-JP` | 1 |

而語音評分的舊卡路徑正是讀這個欄位
([`voice.py:181`](../../backend/app/bot/handlers/voice.py)):

```python
target_language = str(fields.get("Target_Language", {}).get("value", ""))
```

空字串往下傳到 [`whisper_client.to_whisper_language('')`](../../backend/app/infrastructure/stt/whisper_client.py)
回傳 `None`(實測), 即**退化為自動語言偵測**;評分樣板中「目標語言未使用則 score ≤ 20」
的硬性門檻也因此形同虛設。

這是三項問題中**唯一已經在影響日常使用**的 —— 那 62 張卡目前每次錄音評分都缺少語言基準,
其中 14 張已有錄音。

### 1.5 現況快照(實機, 2026-08-11)

| 項目 | 數值 |
|---|---|
| Anki 內 `Speaking_Coach_Dark` 卡片 | 63 張 |
| 目前 `Prompt` 重複的組數 | 0（尚未踩到 §1.1，但機制上隨時會） |
| 已有錄音的卡片 | 14 張 |
| 分布牌組 | `封存::日本語::AI點評::` 下三個子牌組（19 / 2 / 42） |

卡片已全數移入「封存」樹, 而腳本的 `TARGET_DECK` 仍指向未封存的舊路徑 —— 現在執行會在
錯誤的位置建卡。

## 2. 目標與非目標

**目標**

- G2 `Speaking_Coach_Dark` 改為**由 `jsons/` 目錄驅動**, 與 Trilingual 同構(路徑決定牌組、
  根牌組來自設定、內容不進版控)。
- G3 存在判斷改用[身分決策表](../archive/card_identity_writeback_FEAT_2026-08-11.md)的同一套四狀態;
  支援 `--update-existing`(現在完全沒有更新能力)。
- G4 匯入時填入 `Target_Language`, 並讓既有 62 張空值卡片可被一次補齊。
- G5 提供 `clear_identity.py`(與 Trilingual 同介面), 並可用 `--adopt-by-prompt` 接管既有 63 張卡。

**非目標**

- **不改 note model 的 8 欄位定義** —— `Target_Language` 已存在, 只是沒被填。
- **不動既有 63 張卡的內容與錄音** —— 接管只寫身分, `Recordings` / `References` 一律不碰。
- **不重構 `generate_interview_cards.py`** —— 它讀死路徑 `./2026.06/08_interview.json`
  ([`:25`](../../backend/scripts/local_anki/Speaking_Coach_Dark/generate_interview_cards.py)),
  該檔已不存在, 屬一次性歷史腳本。本案不修不刪, 但會在 README 標明它已停用。
- **不抽取共用的 importer 主體** —— 理由見 §3.5。
- **完全不動 `Speaking_Trilingual_Dark/` 下的任何檔案** —— 含共用模組的搬移在內, 皆屬
  [card_identity_writeback_FEAT](../archive/card_identity_writeback_FEAT_2026-08-11.md) 的職責(見 §3.1)。
  兩案因此不會爭奪同一批檔案, PR 可獨立審閱。

## 3. 設計決策

### 3.1 前置條件:`common/card_identity.py` 必須先就位

本案**直接 import** `scripts/local_anki/common/card_identity.py`, 不複製、不修改其內容。

該模組的搬移由前置案
[card_identity_writeback_FEAT §3.8 / **該案 P5**](../archive/card_identity_writeback_FEAT_2026-08-11.md) 負責 ——
搬移會動到 `Speaking_Trilingual_Dark/` 下的檔案與既有 import, 屬於那份計劃的檔案範圍。
本案只承接結果。

**開工前的檢查**:確認 `scripts/local_anki/common/card_identity.py` 存在且
`pytest backend/tests/` 全綠。

> **前置條件已滿足(2026-08-11)**:前置案已由 PR
> [#7](https://github.com/jacky917/FluencyTides/pull/7) 合併並歸檔至 `docs/archive/`,
> `common/card_identity.py` 已就位。本案可隨時開工。

**為什麼不自己複製一份**:身分格式一旦分岔, 就會重演
[S065](../archive/card_identity_writeback_FEAT_2026-08-11.md) 那種「同一份資料兩種格式、
某個呼叫點靜默失效」的問題。

### 3.2 建立 `Speaking_Coach_Dark/jsons/` 與根牌組設定

比照 Trilingual:牌組 = `<根牌組(設定)>::<相對路徑>::<檔名>::<deckName(選填)>`。
新增設定 `SPEAKING_COACH_ROOT_DECK`, 預設沿用現況的 `封存::日本語::AI點評`。

**為什麼不共用 `SPEAKING_TRILINGUAL_ROOT_DECK`**:兩者是不同的學習軌道, 使用者可能想把
其中一個整批搬家而不動另一個。共用會讓「改一處全套用」變成「改一處全都動」。

### 3.3 存在判斷沿用同一套決策表

四種身分狀態的處理與
[§3.2 決策表](../archive/card_identity_writeback_FEAT_2026-08-11.md)完全一致, 不另立規則:
兩者皆無 → 警告後建卡並寫回;兩者皆有且一致 → 依 `--update-existing` 更新或跳過;
對不上或只有其一 → 印診斷並跳過。

更新模式的**保護欄位清單**需按本 model 調整 —— Trilingual 排除
`Recordings_×3`, 本 model 對應的是單數的 `Recordings`:

```
排除: Prompt_Audios, Recordings, Card_ID, TG_Bot
```

`Target_Language` **不列入排除** —— 見 §3.4。

### 3.4 `Target_Language` 的填入與既有卡補值

- 匯入時由 JSON 提供;未提供則預設 `ja-JP`(現有 63 張卡全部是日文面試題)。
- **刻意不列入更新排除清單**:那 62 張空值卡需要靠 `--update-existing` 一次補齊,
  若排除就補不了。這是本案唯一會寫入既有卡片內容的欄位, 且只把「空」補成「有值」。
- 補值前需人工確認語言正確 —— 63 張中有 1 張已是 `ja-JP`, 其餘為空但**內容未必都是日文**。
  P4 的 dry-run 需列出將被寫入的卡片與值。

### 3.5 不抽取共用的 importer 主體

兩支腳本除了身分層之外差異很大:欄位數(8 vs 11)、語言解析方式
(`Target_Language` 欄位 vs 欄位名後綴)、`References` 單複數、媒體上傳範圍。
硬抽成一支帶 model 設定的通用 importer, 會把兩條演化路徑綁死 —— 日後任一方要調整
都得考慮另一方。

**共用身分層(已驗證 model-agnostic), 不共用業務邏輯。** 若第三個 model 也需要,
屆時再以三個實例為依據抽取, 比現在憑兩個猜要可靠。

### 3.6 `clear_identity.py` 比照複製

Trilingual 版本除了 `JSONS_DIR` 常數外無 model 專屬邏輯。可考慮一併上移至 `common/`
並以參數指定目錄, 但**本案先複製一份**:它只有約 60 行, 而過早抽象的成本(多一層參數、
兩個 model 的錯誤訊息要共用措辭)高於重複的成本。待 §3.5 那個「第三個 model」出現時一併處理。

> 這是刻意的重複, 不是疏漏。若日後修改其中一支, 記得同步另一支。

## 4. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `tests/test_speaking_coach_identity.py` | **新檔** —— Coach 版的決策表四狀態、`Target_Language` 預設與覆寫、更新模式對 `Recordings`(單數)的保護 |
| `scripts/local_anki/Speaking_Coach_Dark/import_cards.py` | 改為 `jsons/` 驅動:`--name` / `--dry-run` / `--update-existing` / `--adopt-by-prompt` / `--report-orphans`;接上決策表與身分寫回;填入 `Target_Language`;移除硬編的 `CARDS_TO_IMPORT` / `TARGET_DECK` / `DRY_RUN` |
| `scripts/local_anki/Speaking_Coach_Dark/clear_identity.py` | **新檔**(比照 Trilingual, 見 §3.6) |
| `scripts/local_anki/Speaking_Coach_Dark/jsons/` | **新目錄** + `README.md`(比照 Trilingual 版, 依 8 欄位調整) |
| `app/core/config.py`、`.env.example` | 新增 `SPEAKING_COACH_ROOT_DECK`, 預設 `封存::日本語::AI點評` |
| `.gitignore` | 新增 `backend/scripts/local_anki/Speaking_Coach_Dark/jsons/` |

### Frontend

不涉及。

### 測試

- backend:Coach 版匯入的決策表四狀態、`Target_Language` 的預設與覆寫、更新模式對
  `Recordings`(**單數**, 與 Trilingual 的複數欄位名不同)的保護。共用模組 `card_identity`
  的測試屬前置案, 本案不重複。
- frontend: 不涉及。

## 5. 實作順序

| 階段 | 目標 | 為何這個順序 |
|---|---|---|
| **P0** | Coach 版 `jsons/` + README + 根牌組設定 + `.gitignore` | 先有承載身分的地方, 才談得上寫回 |
| **P1** | **把現有 63 張卡的內容匯出成 JSON** | 卡片內容目前只存在 Anki 與硬編的 list 中;不先匯出就沒有可接管的來源檔 |
| **P2** | Coach 版 `import_cards.py` 改寫 + `clear_identity.py` | 核心;此時 P1 產出的 JSON 尚無身分 |
| **P3** | 一次性接管:`--adopt-by-prompt --dry-run` 核對 → 正式執行 | **人工閘門**, 同前案 |
| **P4** | `Target_Language` 補值:`--update-existing --dry-run` 核對 → 正式執行 | 必須在 P3 之後 —— 沒有身分就無從更新 |

**開工前置**:前置案的 P5(共用模組上移)必須已完成, 見 §3.1。

P1 是**前案沒有對應階段的新工作項**, 因為 Coach 的卡片內容不像 Trilingual 那樣本來就在
JSON 裡 —— 它散在 Anki 與硬編的 list 中, 必須先匯出成可接管的來源檔。

## 6. 風險與未知

| 風險 | 應對 |
|---|---|
| **P1 匯出遺漏欄位或格式不符** —— 匯出的 JSON 若與匯入端期待的結構不一致, P3 接管時會大量落入「無身分 → 建新卡」, 憑空多出 63 張 | P1 完成後先以 `--dry-run`(不加接管旗標)確認**摘要顯示「將新增 63」**, 證明來源檔可被正確解析;再改用 `--adopt-by-prompt` 確認變成「接管 63」。兩個數字都對才進正式執行 |
| **P3 接管命中多張** —— 63 張中若有同牌組同 `Prompt` 者, 依前案設計會跳過並要求人工裁決 | 現況實測重複組數為 0, 風險低;若 P1 匯出後產生重複則會被攔下, 不會靜默選錯 |
| **P4 補錯語言** —— 62 張空值卡的內容未必全是日文 | dry-run 需列出每張卡的 `Prompt` 前 40 字與將寫入的值, 人工逐筆確認後才執行 |
| **`Recordings` 被覆寫** —— 14 張卡有錄音, 是不可復原的使用者資料 | 更新模式的排除清單需含 `Recordings`(單數!), 並以測試鎖定 —— Trilingual 用的是複數欄位名, 直接複製會漏掉 |
| **前置案的共用模組上移未完成就開工** —— `common/card_identity.py` 不存在, 或存在但 Trilingual 側尚未驗證 | §3.1 已列為開工檢查;P0 第一步即確認該檔存在且 `pytest backend/tests/` 全綠。**不得為了趕進度而自行複製一份模組** |
| **未知:Coach 卡片是否有 Trilingual 沒有的欄位用法** | P1 匯出時逐欄位比對 model 定義, 遇到未預期的內容(如 `Prompt_Audios` 非空)先停下確認 |

## 7. 驗收標準

- [ ] **開工前置**:`scripts/local_anki/common/card_identity.py` 存在, 且 `pytest backend/tests/` 全綠
- [ ] 本案的改動**未觸及** `Speaking_Trilingual_Dark/` 下任何檔案(以 `git diff --stat` 佐證)
- [ ] P1 執行前已備份 Anki(`.apkg` 匯出), 備份路徑記於本文件
- [ ] P1 匯出的 JSON 通過結構檢查:8 欄位齊全、`Recordings` / `References` 可解析為 JSON 陣列
- [ ] **不加旗標的 dry-run 顯示「將新增 63」** —— 證明來源檔可被解析且身分確實不存在
- [ ] **加 `--adopt-by-prompt` 的 dry-run 顯示「接管 63」** —— 數字不符即中止, 不得正式執行
- [ ] P3 正式執行後, 每張卡的 JSON 都有非空的 `cardId` / `noteId`, 且 Anki 卡片總數**仍為 63**
- [ ] **改 `Prompt` 不再產生新卡**:任選一張改動 `Prompt` → `--update-existing` → 總數不變且內容已更新
- [ ] **`Recordings` 未被動到**:對有錄音的 14 張卡執行更新模式後, 錄音筆數與執行前相同
- [ ] P4 後 `Target_Language` 為空的卡片數為 **0**(目前 62)
- [ ] 隨機抽一張補值後的卡實際錄音, 確認評分結果的語言基準正確(不再退化為自動偵測)
- [ ] `clear_identity.py` 對 Coach 的 `jsons/` 可正常運作(`--dry-run` 不改檔、`--index` 只清一張)
- [ ] `--report-orphans` 在接管完成後回報 0 張孤兒
