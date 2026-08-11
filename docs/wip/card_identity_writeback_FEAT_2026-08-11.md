# FEAT — 卡片身分證:建卡後把 Card_ID 與 Anki nid 寫回 JSON, 查重改以身分為準

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-11 |
| **性質** | 新增機能設計 + 實作工作項(含同工具鏈的一項 bug 修復) |
| **狀態** | 📝 未實作 |
| **範圍** | `scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py`、`scripts/local_anki/common/clear_audio_fields.py`、`scripts/local_anki/Speaking_Trilingual_Dark/jsons/**.json`(新增身分欄位)、`jsons/README.md` |
| **不動** | Anki note model 的 11 欄位定義(不新增欄位)、`app/` 下的 bot/handler/service 任何代碼、卡片模板 HTML/CSS、`Recordings_*` 的既有內容與格式、其他 model 的匯入腳本 |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | [`jsons/README.md`](../../backend/scripts/local_anki/Speaking_Trilingual_Dark/jsons/README.md)(卡片 JSON 生成準則, 本案需同步更新 §2/§7)、PR [#4](https://github.com/jacky917/FluencyTides/pull/4)(S065 前兩個呼叫點的修復) |

---

## 1. 問題與動機

### 1.1 根因:JSON 沒有穩定身分, 只能用內容當鍵(已定位)

匯入腳本以 **`牌組 + Prompt`** 判斷卡片是否已存在
([`import_cards.py:236-237`](../../backend/scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py)):

```python
query = f'deck:"{escaped_deck}" Prompt:"{escaped_prompt}"'
existing_notes = await client.find_notes(query)
```

`Prompt` 是**卡片內容**, 卻同時被當成主鍵。改一個字就配不上舊卡, 於是被判為新卡建立,
舊卡原地留存 —— 這正是 2026-08-10 實際發生的事:

| 牌組 | Anki 實有 | JSON 定義 | 殘留 |
|---|---|---|---|
| `…::Queen Bee Capital株式会社::志望動機` | 8 | 4 | 4 張(Context 仍是已廢棄的【句型骨架】格式) |
| `…::Queen Bee Capital株式会社::逆質問` | 7 | 3 | 4 張(仍含已刪除的「マレー語」「約 20 秒」) |

使用者觀察到的症狀是「匯入好像沒效果」——實際上更新成功了, 但複習時抽到的是舊卡。

### 1.2 為什麼現有的兩個識別碼都不能用(已定位)

**`Card_ID` — 每次執行都被丟棄重生**
([`import_cards.py:194`](../../backend/scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py)):

```python
out["Card_ID"] = generate_unique_card_id(prefix="st")   # 無條件覆寫
```

`generate_unique_card_id` = `{prefix}-{毫秒時戳}-{uuid4[:8]}`, 每次呼叫必然不同。
JSON 即使寫了 `Card_ID` 也會被覆蓋, 因此 README 現行版本明文要求「不要寫」。
實測五個 JSON 檔均無 `Card_ID`;Anki 內逆質問 7 張卡的 `Card_ID` 分屬三組時戳,
對應三次匯入。

**`nid` — 只存在 Anki 一側**
建卡後才由 Anki 產生, 腳本從未寫回 JSON, 下次執行時本地檔案無從得知自己對應哪張卡。

### 1.3 同工具鏈的既有缺陷(已定位, 併入本案處理)

[`clear_audio_fields.py:79-89`](../../backend/scripts/local_anki/common/clear_audio_fields.py) 以裸
`json.loads` 解析欄位, 失敗時 `except: pass` 靜默吞掉:

```python
json_data = json.loads(original_val)
...
except json.JSONDecodeError:
    pass
```

`Recordings_*` 由語音流程經 `AnkiJsonFieldManager` 寫入, 內容是 HTML 轉義過的, 必定解析失敗。
自 Anki 實機唯讀驗證(2026-08-10):

```
note 1784357183736 / Recordings_JA
  '[{&quot;date&quot;: &quot;2026-07-18&quot;, &quot;audio&quot;: …'
  裸 json.loads      : 失敗 (Expecting property name enclosed in double quotes)
  parse_field_string : 成功, 2 筆
```

結論:`clear_recordings.py` 對 `Recordings_*` 是 **100% 失效**, 非偶發。
這是 S065 的**第三個呼叫點**, PR [#4](https://github.com/jacky917/FluencyTides/pull/4) 只修了
`commands.py` 與 `voice.py`。

全牌組儲存格式普查(29 張卡):

| 欄位群 | 寫入者 | 格式 | 統計 |
|---|---|---|---|
| `Recordings_*` | bot(經 `AnkiJsonFieldManager`) | HTML 轉義 | 全部 |
| `References_*` / `Prompt_Audios` | `import_cards` 直寫 `json.dumps` | 未轉義 | 87 個欄位 |

## 2. 目標與非目標

**目標**

- G1 卡片在 JSON 內具備**永久身分**(`cardId` + `noteId`), 建卡後由腳本自動寫回。
- G2 存在判斷改以身分為準:`noteId` → `cardId` → (僅遷移期)`Prompt`。改 `Prompt` 不再產生新卡。
- G3 既有 29 張卡可**無損接管** —— 一次性遷移把現存卡的 `nid`/`Card_ID` 收編進 JSON, 不重建、不動錄音。
- G4 能列出**孤兒卡**(Anki 有、JSON 無)供人工裁決;腳本本身不刪卡。
- G5 `clear_recordings.py` 能實際清除 `Recordings_*`(S065 第三個呼叫點修復)。

**非目標**

- **不新增 Anki note model 欄位** —— `noteId` 是 Anki 自身的主鍵, 存進欄位會產生兩份真相;
  只寫在 JSON 側。
- **不自動刪除任何 Anki 卡片** —— 孤兒卡可能含使用者錄音, 刪除必須人工確認(G4 只做報告)。
- **不回頭統一存量 87 個未轉義欄位** —— 讀取端(`parse_field_string`)兩種都吃, 批次改寫
  只為整齊而承擔全量寫入風險, 不划算。僅統一**新寫入**的格式(見 §3.4)。
- **不處理其他 model 的匯入腳本** —— `clear_audio_fields.py` 為共用檔, 其修復對所有呼叫者
  同時生效, 但本案不逐一驗證其他 model。

## 3. 設計決策

### 3.1 身分放在卡片物件頂層, 不放進 `fields`

```json
{
  "deckName": "",
  "modelName": "Speaking_Trilingual_Dark",
  "cardId": "st-1786354356058-a387dd13",
  "noteId": 1786354356058,
  "tags": ["TelegramBot", "Interview", "逆質問"],
  "fields": { "Prompt": "…", "…": "…" }
}
```

**選這個**:`fields` 維持「與 Anki note model 一一對應的內容」語意, 身分是**檔案層級的中繼資料**。
`noteId` 本來就不是 model 欄位, 硬塞進 `fields` 會破壞
[`speaking_trilingual_dark_validator.py`](../../backend/scripts/common/template_validators/speaking_trilingual_dark_validator.py)
的 11 欄位順序斷言。

**放棄 `fields.Card_ID`**:雖然 `Card_ID` 確實是 model 欄位, 但把兩個身分拆到兩個層級會讓
「身分證」這件事變得難找;統一放頂層, 匯入時再由腳本注入 `fields["Card_ID"]`。

**放棄決定性 ID(檔案路徑 + 索引的 hash)**:不需寫回檔案是優點, 但在 JSON 中插入或重排卡片
會導致身分整體位移 —— 而重排正是編輯卡片時的常見操作。

### 3.2 查找順序:三段式, 第三段僅供遷移

| 順序 | 依據 | 用途 | 驗證 |
|---|---|---|---|
| 1 | `noteId` | 常態路徑 | 查得到 **且** `modelName` 相符;否則視為失效往下走 |
| 2 | `cardId` | nid 失效時的回復(卡片被刪後重建、跨機器同步) | `Card_ID:{cardId}` 命中唯一一筆 |
| 3 | `Prompt` | **僅遷移期** —— 收編既有卡 | 命中後立刻把 nid/cardId 寫回 JSON, 之後不再走此路 |

第 1 段必須驗 `modelName`:Anki 的 nid 是建立時的毫秒時戳、不重用, 但**卡片被刪除後**該 nid
就查不到;若使用者手動改過 note 類型, 沿用會寫錯欄位。

### 3.3 寫回時機與檔案安全

- **只在有實際變更時改寫檔案**(新增身分、或 nid 修正), 內容不變則不碰, 避免無謂 diff。
- `--dry-run` **絕不寫檔**, 只印出「將寫入什麼身分」。
- 寫檔沿用現行格式:`json.dumps(..., ensure_ascii=False, indent=2)` + 結尾換行,
  與現有五個檔一致, 讓 diff 只出現在身分欄位。
- 寫檔採**先寫暫存檔再 `os.replace`** 的原子替換, 避免中途中斷造成半截 JSON
  —— 這些檔案是手寫內容的唯一副本(`jsons/` 已列入 `.gitignore`, 無版控保護)。

### 3.4 寫入格式統一(順帶, 但範圍受限)

`_normalize_fields` 改用 `AnkiJsonFieldManager` 的轉義規則寫入 JSON 欄位, 使
「bot 寫入」與「腳本寫入」格式一致, 消除 §1.3 那類「第 N 個呼叫點」的再發土壤。
存量不回頭改寫(見 §2 非目標)。

### 3.5 `clear_audio_fields.py` 的修法

改走 `AnkiJsonFieldManager.parse_field_string` / `update_field`,
並把 `except: pass` 改為**會出聲**的處理(`AnkiFieldCorruptedError` → 記 error 並跳過該欄位,
不中斷整批)。靜默失敗正是這個 bug 潛伏至今的原因。

## 4. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py` | 新增 `resolve_existing_note()`(三段式查找)、`write_back_identity()`(原子寫回);`_normalize_fields` 改為「JSON 有 `cardId` 就沿用, 無則生成」並改用轉義寫入;新增 `--report-orphans`;摘要行增列「已寫回身分 N 筆」 |
| `scripts/local_anki/common/clear_audio_fields.py` | JSON 解析改 `AnkiJsonFieldManager.parse_field_string`;寫回改 `update_field`;`except: pass` 改為記錄 error 後跳過 |
| `scripts/local_anki/Speaking_Trilingual_Dark/jsons/**/*.json` | 各卡新增 `cardId` / `noteId`(由遷移執行產生, 非手寫) |
| `scripts/local_anki/Speaking_Trilingual_Dark/jsons/README.md` | §2 頂層結構補 `cardId`/`noteId` 說明;§7 改寫「`Prompt` 是實質主鍵」為身分機制;新增孤兒卡處理段 |

### Frontend

不涉及。

### 測試

- backend: `resolve_existing_note()` 的三段式優先序與各段失效回退(以假 AnkiClient 驅動,
  不連 Anki);`write_back_identity()` 的冪等性(同內容重跑不改檔)與 `--dry-run` 不寫檔;
  `clear_audio_fields` 對「轉義」與「未轉義」兩種格式皆能取出 audio 檔名。
- frontend: 不涉及。

## 5. 實作順序

| 階段 | 目標 | 為何先做 |
|---|---|---|
| **P0** | `clear_audio_fields.py` S065 修復 | 與身分機制無耦合, 可獨立上線;且錄音清不掉會妨礙後續反覆重測 |
| **P1** | 身分寫回 + 三段式查找 + `--dry-run` 不寫檔 | 核心機能;此時 JSON 尚無身分, 全部走第 3 段(Prompt)並完成收編 |
| **P2** | 一次性遷移:對現有 29 張卡執行 `--dry-run` 檢視 → 正式跑一次寫回身分 | 需人工核對「哪張 Anki 卡對應哪張 JSON 卡」, 是 go-no-go 閘門 |
| **P3** | `--report-orphans` + README 同步 | 遷移後才知道誰是孤兒;文件最後補, 避免寫了又改 |
| **P4** | 寫入格式統一(§3.4) | 影響所有新寫入, 放最後降低前面階段的變數 |

P0 與 P1–P4 可拆成兩個 PR;P2 是**人工操作**而非代碼, 但必須在 P3 之前完成。

## 6. 風險與未知

| 風險 | 應對 |
|---|---|
| **P2 遷移誤配對** —— Prompt 已被改過的卡, 第 3 段查不到, 會被當新卡建立, 使孤兒再增 | P2 一律先 `--dry-run` 並人工核對配對表;對配不上的卡, 手動在 JSON 填入正確 `noteId` 後再跑。**不得在未檢視 dry-run 的情況下正式執行** |
| **寫回破壞手寫 JSON** —— `jsons/` 已被 `.gitignore`, 無版控可回復 | 原子替換(§3.3);P2 執行前手動備份整個 `jsons/` 目錄 —— 列為驗收條目 |
| **nid 在多機器 Anki 間不一致** —— 同一副牌在另一台機器同步後 nid 可能不同 | 第 2 段 `cardId` 即為此設計;`Card_ID` 隨 note 同步, 跨機器穩定 |
| **孤兒卡含使用者錄音** | 只報告不刪除(G4);報告需標示各孤兒卡的 `Recordings_*` 筆數, 讓人工判斷 |
| **未知:Anki 端手動改過 Card_ID** | 第 2 段查詢命中多筆時視為衝突, 記 warning 並降級至第 3 段, 不擅自挑一筆 |

## 7. 驗收標準

- [ ] P2 執行前已手動備份 `jsons/` 目錄(壓縮檔留存路徑記於本文件)
- [ ] `--dry-run` 對五個 JSON 檔執行後, **檔案 mtime 未變**(證明未寫檔)
- [ ] 正式執行後, 五個 JSON 檔的每張卡都有非空的 `cardId` 與 `noteId`
- [ ] 對照 Anki:每個寫回的 `noteId` 都查得到, 且 `modelName == "Speaking_Trilingual_Dark"`
- [ ] **改 `Prompt` 不再產生新卡**:任選一張卡改動 `Prompt` 文字 → 重跑 `--update-existing`
      → Anki 卡片總數不變, 且該 note 的 `Prompt` 已更新
- [ ] 連續執行兩次匯入, 第二次的 JSON 檔內容與第一次完全相同(冪等)
- [ ] `--report-orphans` 正確列出目前已知的 8 張殘留卡(志望動機 4 + 逆質問 4), 並標示各自錄音筆數
- [ ] `clear_recordings.py --dry-run` 能列出實際存在的 `.ogg` 檔名(不再回報「沒有找到音訊記錄」)
- [ ] `clear_recordings.py` 正式執行後, 目標卡的 `Recordings_*` 變為 `[]` 且媒體檔已刪除
- [ ] `pytest backend/tests/` 全數通過
