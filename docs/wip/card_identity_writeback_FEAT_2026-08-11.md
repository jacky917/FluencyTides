# FEAT — 卡片身分證:建卡後把 Card_ID 與 Anki nid 寫回 JSON, 查重改以身分為準

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-11 |
| **性質** | 新增機能設計 + 實作工作項(含同工具鏈的一項 bug 修復) |
| **狀態** | 🚧 P0–P5 全數完成，實機驗證通過；**僅餘 `clear_recordings` 的正式執行未做**（該操作會刪除錄音，留待實際需要時） |
| **範圍** | `scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py`、`scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py`(新檔)、`scripts/local_anki/Speaking_Trilingual_Dark/card_identity.py`(新檔, 見 §3.5)、`tests/test_card_identity.py`(新檔)、`scripts/local_anki/common/card_identity.py`(P5 由本目錄上移)、`scripts/local_anki/common/clear_audio_fields.py`、`scripts/local_anki/Speaking_Trilingual_Dark/jsons/**.json`(新增身分欄位)、`jsons/README.md` |
| **不動** | Anki note model 的 11 欄位定義(不新增欄位)、`app/` 下的 bot/handler/service 任何代碼、卡片模板 HTML/CSS、`Recordings_*` 的既有內容與格式、**`Speaking_Coach_Dark` 的匯入腳本與其卡片**(屬 [speaking_coach_identity_FEAT](speaking_coach_identity_FEAT_2026-08-11.md) 的職責, 見 §2 職責邊界) |
| **PR / 進度** | 尚未開始 |
| **關聯文件** | [`jsons/README.md`](../../backend/scripts/local_anki/Speaking_Trilingual_Dark/jsons/README.md)(卡片 JSON 生成準則, 本案需同步更新 §2/§7)、PR [#4](https://github.com/jacky917/FluencyTides/pull/4)(S065 前兩個呼叫點的修復)、[`speaking_coach_identity_FEAT_2026-08-11.md`](speaking_coach_identity_FEAT_2026-08-11.md)(下游案, 消費本案 P5 產出的共用模組) |

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
- G2 存在判斷**只看身分, 不看 `Prompt`**:
  - **兩者皆有且與 Anki 一致** → 更新
  - **兩者皆有但對不上**(含只有其一) → 印診斷並跳過, **不回退、不建卡、不更新**(理由見 §3.2)
  - **兩者皆無** → 視為卡片不存在, ⚠️ 印警告後正常建卡並寫回身分

  改 `Prompt` 不再產生任何影響 —— 存在判斷根本不看它。
- G3 既有 29 張卡可**無損接管** —— 以一次性旗標 `--adopt-by-prompt` 收編現存卡的
  `nid`/`Card_ID`, 不重建、不動錄音(見 §3.2)。
- G4 能列出**孤兒卡**(Anki 有、JSON 無)供人工裁決;腳本本身不刪卡。
- G5 `clear_recordings.py` 能實際清除 `Recordings_*`(S065 第三個呼叫點修復)。
- G7 身分層上移至 `scripts/local_anki/common/card_identity.py`, 成為跨 model 可重用的模組
  —— 本案**負責搬移與確保 Trilingual 側不受影響**;由誰消費不在本案範圍(見 §2 職責邊界)。
- G6 提供 `clear_identity.py`:清除 JSON 內的身分欄位, 使該卡回到「無身分」狀態
  —— 這是 G2 判定失敗後**唯一**的復原手段, 也是複製 JSON 檔開新牌組時的必要步驟(見 §3.6)。

**非目標**

- **不新增 Anki note model 欄位** —— `noteId` 是 Anki 自身的主鍵, 存進欄位會產生兩份真相;
  只寫在 JSON 側。
- **不自動刪除任何 Anki 卡片** —— 孤兒卡可能含使用者錄音, 刪除必須人工確認(G4 只做報告)。
- **不回頭統一存量 87 個未轉義欄位** —— 讀取端(`parse_field_string`)兩種都吃, 批次改寫
  只為整齊而承擔全量寫入風險, 不划算。僅統一**新寫入**的格式(見 §3.4)。
- **不處理其他 model 的匯入腳本** —— `clear_audio_fields.py` 為共用檔, 其修復對所有呼叫者
  同時生效, 但本案不逐一驗證其他 model。

### 職責邊界

本案的範圍是 **`Speaking_Trilingual_Dark` 這條線, 加上身分層本身**:

| 屬於本案 | 屬於 [speaking_coach_identity_FEAT](speaking_coach_identity_FEAT_2026-08-11.md) |
|---|---|
| `Speaking_Trilingual_Dark/` 下的所有腳本與 `jsons/` | `Speaking_Coach_Dark/` 下的所有腳本與 `jsons/` |
| `card_identity.py` 的實作、測試, 以及**上移至 `common/`**(P5) | 消費 `common/card_identity.py`;不修改其內容 |
| `clear_audio_fields.py` 的 S065 修復(共用檔, 修復對所有呼叫者生效) | 不再處理該檔 |
| `SPEAKING_TRILINGUAL_ROOT_DECK` 設定 | `SPEAKING_COACH_ROOT_DECK` 設定 |

**分界的判準是「改動落在哪個檔案」**, 而非「誰受益」。共用模組的搬移會動到本目錄下的
檔案與既有 import, 因此由本案負責;下游案只承接結果。這樣兩案的 PR 不會互相踩到同一批檔案。

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

### 3.2 存在判斷:身分優先且嚴格, 對不上就停手

身分是**成對**的 —— `cardId` 與 `noteId` 必須同時存在, 且必須指向 Anki 中的同一張卡。
只要進入「身分已存在」這條路, 就沒有任何自動回退。

| JSON 身分狀態 | Anki 查驗 | 動作 |
|---|---|---|
| 兩者皆無 | — | ⚠️ **視為卡片不存在** —— 印警告後**正常建卡**, 並把新身分寫回 JSON |
| 兩者皆有 | `noteId` 查得到 **且** `modelName` 相符 **且** 該 note 的 `Card_ID` 欄位 == `cardId` | 正常更新 |
| 兩者皆有 | 上述任一條件不成立 | ❌ **印出診斷並跳過該卡** —— 不建卡、不更新、不改檔 |
| 只有其一 | — | ❌ 視為損壞的身分, 同上處理 |

**`Prompt` 不再參與存在判斷。** 身分是唯一依據 —— 有就嚴格查驗, 沒有就當新卡。
這讓「改 `Prompt` 會發生什麼」有了單一答案:什麼都不會發生, 因為根本不看它。

「兩者皆無」仍印警告(而非靜默建卡), 是因為**正常情況下不該出現** ——
第一次匯入後每張卡都會有身分。之後再看到無身分的卡, 通常代表:手動新增了卡片(預期內)、
或身分被 `clear_identity.py` 清掉了(預期內)、或 JSON 被複製而忘了處理(**非預期**)。
警告讓第三種情況不會無聲無息地多出一批卡。

#### 為什麼身分「有但對不上」時不回退到 `Prompt`

「兩者皆無」當新卡處理是安全的 —— 沒有身分就沒有指涉對象, 建一張新的不會動到任何既有資料。
但「有身分卻對不上」完全是另一回事:那代表**這張卡曾經綁定過某張 note, 而現在綁不上了**。
此時回退比對 `Prompt` 看似貼心, 實際上會**重新製造本計劃要消滅的問題**。
身分對不上有幾種可能, 每種都需要人的判斷:

- **卡片在 Anki 被刪了** —— 你可能是故意刪的。自動以 `Prompt` 重建, 等於推翻使用者的決定。
- **JSON 檔被複製去開新牌組** —— 身分跟著被複製。若回退比對 `Prompt`, 新檔會**接管原檔的卡**,
  兩個 JSON 指向同一批 note, 之後互相覆寫(見 §3.6 的複製情境)。
- **`Card_ID` 在 Anki 端被手動改過** —— 需要確認是誤改還是有意為之。
- **JSON 被手動編輯而弄壞了身分**(只剩一個欄位) —— 靜默修復會掩蓋編輯錯誤。

這幾種情境的正確處理各不相同, 腳本無從分辨。**停下來讓人決定**比猜一個看起來合理的行為安全。
復原手段是 `clear_identity.py`(§3.6):清掉身分後該卡回到「無身分」狀態 ——
重跑會建一張新卡, 或加 `--adopt-by-prompt` 重新接管既有卡, 由使用者選擇。

#### 診斷輸出要能直接行動

跳過時不能只說「失敗」, 必須包含足以判斷的資訊與下一步:

```
❌ [逆質問.json #2] 身分與 Anki 不一致, 已跳過
   JSON  : noteId=1786354356170  cardId=st-1786354356089-3ba92049
   Anki  : noteId 查無此 note(可能已被刪除)
   Prompt: 你在面試前實際下載並使用了這家公司的應用程式, 察覺有幾個語言…
   處理  : 確認該卡是否為刻意刪除。若要重新建立, 先執行
           clear_identity.py --name "日本語面接/Queen Bee Capital株式会社/逆質問" --index 2
```

`modelName` 也要驗:Anki 的 nid 是建立時的毫秒時戳且不重用, 但使用者可能手動改過 note 類型,
沿用會寫進錯誤的欄位集合。

#### 既有 29 張卡的收編:一次性的顯式旗標 `--adopt-by-prompt`

上面的決策表有一個必然後果:**現行五個 JSON 檔一張卡都沒有身分**, 照表操作就是全部當新卡建立
—— Anki 會多出 29 張重複卡, 其中數張既有卡還帶著已錄好的 `Recordings_*`(實測至少 3 張)。
G3 要求無損接管, 因此需要一條一次性的通道。

做法是把 `Prompt` 比對從**預設行為**降級為**顯式旗標**:

```bash
# 僅遷移時使用一次;跑完所有卡都有身分, 此後永不再用
python scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py --adopt-by-prompt --dry-run
```

| 行為 | 不加旗標(預設) | 加 `--adopt-by-prompt` |
|---|---|---|
| 身分皆無時 | ⚠️ 警告 → 建新卡 | 先以 `牌組 + Prompt` 查找;命中 → **接管**(寫回該 note 的身分, 不建卡);未命中 → 建新卡 |
| 其餘三種狀態 | 同決策表 | 同決策表(旗標不影響) |

**為什麼是旗標而不是預設**:`Prompt` 比對正是產生 8 張殘留卡的元凶, 讓它留在預設路徑上,
等於把已知會出錯的行為留在每天都會走的路上。設成旗標後, 它只在使用者明確知道自己在做遷移時生效。

**為什麼不乾脆手動填**:29 張卡逐一到 Anki 查 nid 再貼進 JSON, 既慢又容易貼錯,
而貼錯的後果是覆寫別張卡的內容。

旗標在 P2 完成後即可視為歷史遺留;不特別移除(未來若有人從舊備份還原 JSON 仍用得上),
但 `--help` 標明「一次性遷移用」。

### 3.3 寫回時機與檔案安全

- **只在有實際變更時改寫檔案**(新增身分、或 nid 修正), 內容不變則不碰, 避免無謂 diff。
- `--dry-run` **絕不寫檔**, 只印出「將寫入什麼身分」。
- 寫檔沿用現行格式:`json.dumps(..., ensure_ascii=False, indent=2)` + 結尾換行,
  與現有五個檔一致, 讓 diff 只出現在身分欄位。
- 寫檔採**先寫暫存檔再 `os.replace`** 的原子替換, 避免中途中斷造成半截 JSON
  —— 這些檔案是手寫內容的唯一副本(`jsons/` 已列入 `.gitignore`, 無版控保護)。

#### 〔2026-08-11 實作時調整〕身分寫回改置於 ``finally``

原設計把 ``save_cards`` 放在主迴圈之後的正常路徑上。實作時發現這會產生一個
**製造重複卡的漏洞**:若迴圈中途拋出未被接住的例外(``create_deck`` /
``get_notes_info`` / ``find_notes`` 皆會往外拋), 先前已在 Anki 建好的卡片其身分
就不會落地 —— 那些卡在 Anki 存在、在 JSON 卻無身分, 下次重跑會被當成新卡再建一次,
正是本計劃要消滅的問題。

改為置於 ``finally``, 並把 ``save_cards`` 本身包在 ``try/except OSError`` 中,
避免寫檔失敗掩蓋原始例外。已補測試
``test_identity_survives_midway_exception`` 覆蓋此路徑。

註:``add_note`` / ``update_note_fields`` 的例外在迴圈內即被接住並計為 ``failed``,
不會中斷整批, 因此該測試的注入點選在 ``create_deck``。

### 3.4 寫入格式統一(順帶, 但範圍受限)

`_normalize_fields` 改用 `AnkiJsonFieldManager` 的轉義規則寫入 JSON 欄位, 使
「bot 寫入」與「腳本寫入」格式一致, 消除 §1.3 那類「第 N 個呼叫點」的再發土壤。
存量不回頭改寫(見 §2 非目標)。

### 3.5 〔2026-08-11 實作時追加〕``card_identity.py``:共用的身分讀寫模組

原改動清單只列了兩支腳本, 但 ``import_cards.py`` 與 ``clear_identity.py`` 都需要
「讀身分 / 寫身分 / 原子存檔 / 維持 JSON 格式」這組操作。若各寫一份, 兩邊的存檔格式
遲早分歧 —— 而格式分歧正是 §1.3 那個 bug 的成因。因此抽成單一模組。

模組同時吸收了幾個實作時才浮現的細節:

- **``noteId`` 接受字串**:使用者從 Anki 複製 nid 貼進 JSON 時會是字串, 一律正規化為
  ``int`` 再比對, 否則 ``"111" != 111`` 會讓有效身分被誤判成不一致。
- **明確排除 ``bool``**:Python 的 ``bool`` 是 ``int`` 的子類, ``noteId: true`` 會
  意外通過型別檢查並被當成 nid ``1``。
- **鍵序正規化**:寫入身分後把鍵排成 ``deckName → modelName → cardId → noteId →
  tags → fields``, 讓所有檔案長得一樣;未列於標準順序的鍵保留在最後, 不丟棄使用者
  自行加入的欄位。

### 3.6 `clear_identity.py`:身分清除工具

因為 §3.2 取消了自動回退, 必須有一個明確的手段讓卡片回到「無身分」狀態。
放在 `Speaking_Trilingual_Dark/` 下, 與 `clear_recordings.py` 同層、命名同系列。

**它只改 JSON, 完全不碰 Anki** —— 這是刻意的:清除身分不應該有刪卡的副作用。
使用者若也想刪 Anki 那張卡, 自行在 Anki 操作。

```bash
# 清除單一檔案全部卡片的身分
python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py \
    --name "日本語面接/Queen Bee Capital株式会社/逆質問" --dry-run

# 只清第 2 張(對應 §3.2 診斷訊息給的建議)
python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py \
    --name "日本語面接/…/逆質問" --index 2

# 清除整個 jsons/ 目錄(複製整包去開新公司牌組時用)
python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py --all
```

| 參數 | 說明 |
|---|---|
| `--name` | `jsons/` 下的相對路徑(不含 `.json`), 與 `import_cards.py` 一致 |
| `--index` | 卡片序號(1-based, 對齊診斷訊息的編號);省略則清該檔全部 |
| `--all` | 遞迴處理整個 `jsons/`;與 `--name` 互斥 |
| `--dry-run` | 只列出將被清除的身分, 不寫檔 |

沿用 §3.3 的原子寫檔與格式規則(`indent=2`、`ensure_ascii=False`、結尾換行), 讓 diff 只出現在身分欄位。

#### 主要使用情境:複製 JSON 開新牌組

這是最容易出事、也最需要這支工具的地方。換一家公司面試時, 直覺做法是整包複製資料夾:

```
cp -r "jsons/日本語面接/Queen Bee Capital株式会社" "jsons/日本語面接/新公司株式会社"
```

複製出來的檔案**帶著原公司卡片的身分**。若直接匯入, 新牌組的卡會沿著 `noteId` 去
**更新原公司的卡片** —— 內容被覆寫、而新牌組一張卡都不會建立。

因此複製後必須先清身分:

```bash
python scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py --name "日本語面接/新公司株式会社/志望動機"
```

這個約束要寫進 `jsons/README.md` 的目錄慣例段, 否則遲早會踩到。

> 有考慮過讓 `import_cards.py` 自動偵測「JSON 檔路徑與該 note 實際所在牌組不符」並警告,
> 但牌組是可以被使用者在 Anki 端合法搬動的, 不符不必然代表錯誤, 會產生假警報。
> 先靠文件與工具約束, 待實際踩到再評估。

### 3.7 `clear_audio_fields.py` 的修法

改走 `AnkiJsonFieldManager.parse_field_string` / `update_field`,
並把 `except: pass` 改為**會出聲**的處理(`AnkiFieldCorruptedError` → 記 error 並跳過該欄位,
不中斷整批)。靜默失敗正是這個 bug 潛伏至今的原因。

### 3.8 〔2026-08-11 追加〕身分層上移至 `common/`

`card_identity.py` 不含任何 model 專屬邏輯 —— 欄位名、模型名、牌組規則皆不出現於其中,
只處理「讀身分 / 寫身分 / 原子存檔 / 維持 JSON 格式」。它從一開始就是可重用的,
放在 `Speaking_Trilingual_Dark/` 下只是因為當時只有一個消費者。

現在有了第二個消費者, 上移至 `scripts/local_anki/common/card_identity.py`。

**為什麼不讓下游案自己複製一份**:身分格式一旦分岔, 就會重演 §1.3 那種「同一份資料
兩種格式、某個呼叫點靜默失效」的問題 —— 那正是本案要根治的病。

**為什麼由本案負責搬移**:搬移會動到本目錄下的檔案與兩支腳本的 import, 屬於本案的
檔案範圍。下游案只 import 新位置, 不碰模組內容。這樣兩案的 PR 不會爭奪同一批檔案。

**搬移的驗收要求**:必須是純 rename(git 判定 R100), 且搬移後 Trilingual 的匯入
`--dry-run` 行為與搬移前逐字一致 —— 這是「行為零變更」的證據, 不能只靠測試綠燈。

> 注:`clear_identity.py` **不一併上移**。它除了 `JSONS_DIR` 常數外雖也無 model 專屬
> 邏輯, 但只有約 60 行, 過早抽象(多一層目錄參數、兩個 model 的錯誤訊息要共用措辭)的
> 成本高於重複的成本。待第三個 model 出現時, 憑三個實例再決定。

## 4. 改動清單

### Backend

| 檔案 | 改動 |
|---|---|
| `scripts/local_anki/Speaking_Trilingual_Dark/import_cards.py` | 新增 `resolve_existing_note()`(§3.2 決策表, 回傳 `(note_id \| None, 診斷訊息 \| None)`)、`--adopt-by-prompt`(一次性遷移旗標)、`write_back_identity()`(原子寫回);`_normalize_fields` 改為「JSON 有 `cardId` 就沿用, 無則生成」並改用轉義寫入;新增 `--report-orphans`;摘要行增列「已寫回身分 N 筆／身分不符跳過 N 筆」, 且**有跳過時以非零 exit code 結束**(避免批次腳本誤判成功) |
| `scripts/local_anki/Speaking_Trilingual_Dark/card_identity.py` | **新檔（實作時追加，見 §3.5）** —— 身分讀寫與原子存檔的單一事實來源，供匯入與清除兩支腳本共用 |
| `tests/test_card_identity.py` | **新檔** —— 26 個測試涵蓋 §3.2 決策表四狀態、旗標兩路徑、寫檔閘門與中斷安全 |
| `scripts/local_anki/Speaking_Trilingual_Dark/clear_identity.py` | **新檔** —— 清除 JSON 的 `cardId`/`noteId`;`--name` / `--index` / `--all` / `--dry-run`;只改 JSON 不碰 Anki;沿用原子寫檔 |
| `scripts/local_anki/common/clear_audio_fields.py` | JSON 解析改 `AnkiJsonFieldManager.parse_field_string`;寫回改 `update_field`;`except: pass` 改為記錄 error 後跳過 |
| `scripts/local_anki/Speaking_Trilingual_Dark/jsons/**/*.json` | 各卡新增 `cardId` / `noteId`(由遷移執行產生, 非手寫) |
| `scripts/local_anki/common/card_identity.py` | **P5 由 `Speaking_Trilingual_Dark/` 上移**（純 rename，內容不變）；本目錄下的兩支腳本與測試同步調整 import 路徑 |
| `scripts/local_anki/Speaking_Trilingual_Dark/jsons/README.md` | §2 頂層結構補 `cardId`/`noteId` 說明;§7 改寫「`Prompt` 是實質主鍵」為身分機制與 §3.2 決策表;目錄慣例段補「複製資料夾後必須先清身分」;新增孤兒卡與 `clear_identity.py` 使用段 |

### Frontend

不涉及。

### 測試

- backend(以假 AnkiClient 驅動, 不連 Anki):
  - `resolve_existing_note()` 的 §3.2 決策表**四種狀態逐一覆蓋** —— 尤其「兩者皆有但 nid 查無」
    與「只有其一」必須回傳跳過而非回退, 這是本設計最容易被日後改壞的地方
  - 「兩者皆無」在**未加旗標**時建新卡(不查 `Prompt`), 在**加了 `--adopt-by-prompt`** 時
    才以 `Prompt` 接管 —— 兩條路徑都要有案例, 確保旗標真的是開關而非裝飾
  - `write_back_identity()` 的冪等性(同內容重跑不改檔)與 `--dry-run` 不寫檔
  - `clear_identity.py` 的 `--index` 只清指定卡、`--dry-run` 不寫檔、清除後檔案其餘內容逐字不變
  - `clear_audio_fields` 對「轉義」與「未轉義」兩種格式皆能取出 audio 檔名
- frontend: 不涉及。

## 5. 實作順序

| 階段 | 目標 | 為何先做 |
|---|---|---|
| **P0** ✅ | `clear_audio_fields.py` S065 修復 | 與身分機制無耦合, 可獨立上線;且錄音清不掉會妨礙後續反覆重測 |
| **P1** ✅ | 身分寫回 + §3.2 決策表 + `--adopt-by-prompt` + `--dry-run` 不寫檔 | 核心機能。此時 JSON 尚無身分, 收編要等 P2 加旗標執行 |
| **P1.5** ✅ | `clear_identity.py` | 必須**早於** P2 —— P2 的 dry-run 一旦發現配對錯誤, 沒有這支工具就無法退回重來 |
| **P2** ⏳ | 一次性遷移:`--adopt-by-prompt --dry-run` 檢視配對表 → 正式跑一次寫回身分 | 需人工核對「哪張 Anki 卡對應哪張 JSON 卡」, 是 go-no-go 閘門。**未加旗標直接跑會建立 29 張重複卡** |
| **P3** ✅ | `--report-orphans` + README 同步 | 遷移後才知道誰是孤兒;文件最後補, 避免寫了又改 |
| **P4** ✅ | 寫入格式統一(§3.4) | 影響所有新寫入, 放最後降低前面階段的變數 |
| **P5** ⏳ | `card_identity.py` 上移至 `common/`(§3.8) | 純搬移, 行為零變更。放在最後是因為它只有在**下游案要開工時**才有必要 —— 提早搬只是製造一次無收益的路徑變動 |

P0 與 P1–P4 可拆成兩個 PR;P2 是**人工操作**而非代碼, 但必須在 P3 之前完成。

### 3.9 〔2026-08-11 審查後修正〕獨立審查發現的缺陷

實作完成後由獨立審查者逐項對照本文件, 找出下列問題, 均已修正並補測試:

| 嚴重度 | 問題 | 修正 |
|---|---|---|
| **資料遺失** | ``_process_media_paths`` 就地改寫傳入的 dict, 而該 dict 就是 ``cards_data`` 內的物件 —— 身分寫回時會把使用者手寫的**絕對素材路徑覆寫成純檔名**。``jsons/`` 未進版控, 那是唯一一份 | 正規化前先 ``copy.deepcopy``;新增 ``test_absolute_media_paths_survive_import`` |
| 高 | ``--adopt-by-prompt`` 命中多張時靜默取 ``adopted[0]``。§1.2 已載明逆質問有 3 批同 ``Prompt`` 的重複卡, 任選一張會讓另一張的錄音變孤兒 —— 正是 G3 要防止的損失 | 命中 >1 時改為印診斷並跳過, 列出所有候選 nid |
| 高 | 接管查詢未限定模型。``Prompt`` 也是 ``Speaking_Coach_Dark`` 等模型的欄位, Anki 欄位搜尋跨模型 | 查詢加 ``"note:Speaking_Trilingual_Dark"`` |
| 高 | ``report_orphans`` 只掃 ``jsons/``, 但 ``--name`` 支援腳本同層作為後備位置(``sabbat_of_the_witch.json`` 即在此) —— 那些卡會被永遠誤報為孤兒 | 併入後備位置;單檔解析失敗改為略過並警告 |
| 高 | ``load_cards`` 的 ``ValueError`` 未被接住, 一個格式損毀的檔案會讓整批以 raw traceback 中止 | ``main()`` 逐檔 ``try/except``, 計入 ``failed`` 並繼續 |
| 中 | ``clear_audio_fields`` 只看項目頂層的 ``audio``, 但 ``References_*`` 的音檔在巢狀的 ``audios`` 子陣列 —— docstring 聲稱已處理, 實際完全取不到, **正是它要修的那類「靜默失敗藏在自信的訊息背後」** | 改為遞迴收集;新增三個測試 |
| 中 | 已宣告「損毀、已跳過」的欄位仍會被 ``.strip()`` 後寫回並印「音訊已清除」, 與上一行的錯誤訊息自相矛盾 | 回傳值增加 ``must_skip``, 與「不是 JSON 陣列」區分開 |
| 中 | 診斷訊息未附可直接複製的復原指令(§3.2 明文要求), 使用者仍須自行推算相對路徑 | 新增 ``_recovery_hint()``, 由 ``file_path`` 與序號組出完整指令 |
| 低 | ``save_cards`` 缺 ``fsync`` —— ``os.replace`` 只保證無半截檔案, 不保證內容已落盤 | ``flush()`` + ``fsync()`` |
| 低 | ``"tags": null`` 會 ``TypeError`` 中止整檔;缺 ``Prompt`` 被計入 ``blocked`` 而非 ``failed``;``--report-orphans`` 恆回傳 0 | 逐項修正 |

審查亦確認:§3.2 決策表四種狀態的行為與規格一致、exit code 串接正確、``identity_dirty``
與 ``finally`` 寫回的原子性無誤、所有公開函式具備中英雙語 docstring。

**已知但未處理**:``Speaking_Trilingual_Dark_back.html`` 的 ``status`` 切換與
``speaking_trilingual_dark_validator.py`` 仍以未轉義格式直寫 AnkiConnect, 因此 §3.4
「消除再發土壤」的說法僅涵蓋 Python 側的寫入路徑, 卡面 JS 與驗證器仍是第四、第五個
未轉義寫入者。讀取端 ``parse_field_string`` 兩種皆相容, 故不影響正確性, 但該說法應理解為
範圍受限。

## 6. 風險與未知

| 風險 | 應對 |
|---|---|
| **P2 遷移誤配對** —— Prompt 已被改過的卡, 第 3 段查不到, 會被當新卡建立, 使孤兒再增 | P2 一律先 `--dry-run` 並人工核對配對表;對配不上的卡, 手動在 JSON 填入正確 `noteId` 後再跑。**不得在未檢視 dry-run 的情況下正式執行** |
| **寫回破壞手寫 JSON** —— `jsons/` 已被 `.gitignore`, 無版控可回復 | 原子替換(§3.3);P2 執行前手動備份整個 `jsons/` 目錄 —— 列為驗收條目 |
| **nid 在多機器 Anki 間不一致** —— 同一副牌在另一台機器同步後 nid 可能不同 | 第 2 段 `cardId` 即為此設計;`Card_ID` 隨 note 同步, 跨機器穩定 |
| **孤兒卡含使用者錄音** | 只報告不刪除(G4);報告需標示各孤兒卡的 `Recordings_*` 筆數, 讓人工判斷 |
| **複製 JSON 檔開新牌組時忘記清身分** —— 新檔會沿 `noteId` 更新到原公司的卡, 內容被覆寫且新牌組一張卡都不會建 | 這是**唯一會造成資料被覆寫**的操作路徑, 且 §3.2 的嚴格檢查攔不住(身分完全有效, 只是指錯卡)。對策:`jsons/README.md` 的目錄慣例段列為必要步驟, 並在 §3.6 的工具說明中以此為首要情境 |
| **P2 忘記加 `--adopt-by-prompt`** —— 29 張卡全部被當新卡建立, Anki 瞬間多一倍且原卡的錄音留在舊卡上 | 現行 JSON 皆無身分, 這是 P2 當下最可能的誤操作。對策:P2 一律先 `--dry-run`, 摘要行會顯示「將新增 29 筆」而非「將接管 29 筆」, 數字即為閘門 |
| **嚴格判定造成批次中斷** —— 一張卡身分壞掉就跳過, 使用者可能沒注意到 | 摘要行明列跳過筆數, 且有跳過時以非零 exit code 結束;診斷訊息附可直接複製的 `clear_identity.py` 指令(§3.2) |
| **未知:Anki 端手動改過 Card_ID** | 此時 `noteId` 查得到但 `Card_ID` 不符 → 依 §3.2 跳過並診斷, 由人決定是改 JSON 還是改回 Anki |

## 6.5 實機驗證紀錄（2026-08-11）

P2 遷移與全套工具已對實機 Anki 執行完畢。

**備份**:`C:\Users\forip\Desktop\fluencytides_jsons_backup_20260811.zip`(8 檔, 33 KB)

**P2 閘門**:兩個數字都對上才執行 ——

```
不加旗標   --dry-run → 新增 18   （來源檔可解析、身分確實不存在）
--adopt-by-prompt   → 接管 18   （全數對上既有卡，被攔 0 筆）
```

**正式執行結果**:18/18 接管, 身分寫回 5 個 JSON 檔。與備份逐鍵比對確認
**除新增 `cardId`/`noteId` 外, 所有欄位逐字未動**。

**端對端場景**(以自建的臨時卡片進行, 全程未觸及使用者內容, 測後已清除):

| 場景 | 結果 |
|---|---|
| 首次匯入 | 建卡 + 身分寫回 |
| **大幅改寫 `Prompt` 後 `--update-existing`** | 更新同一張 note, Anki 總數 31→31 **未產生重複卡** |
| 刪除 Anki 那張卡後重跑 | 診斷 + 跳過, JSON md5 未變, exit 1 |
| 照診斷指令 `clear_identity --index 1` | 身分清除成功 |
| 清除後重跑 | 建立新卡 + 寫回身分 |
| 二次執行(冪等) | 全部跳過, 五檔 md5 完全相同 |

**孤兒報告**:Anki 30 張 / JSON 持有 18 張 → 正確列出 12 張孤兒(含各語言錄音筆數)。
這 12 張是 §1.1 所述、`Prompt` 查重時代遺留的重複卡, **本工具只報告不刪除**,
待使用者自行裁決。

### 驗證過程中另外修掉的兩個問題

1. **`--dry-run` 會上傳媒體** —— `_normalize_fields` 在 dry_run 判斷**之前**執行, 而它會
   `storeMediaFile`。與 `--help` 宣稱的「不寫入 Anki」牴觸。已加閘門, 改為只檢查存在性
   並印「將上傳」。現行 JSON 因 `Prompt_Audios` 皆空未觸發, 但帶絕對路徑的檔案會踩到。
2. **診斷的復原指令未接上** —— `recovery_hint` 佔位符只在四個 blocked 分支中的一個生效,
   其餘仍是無法直接執行的通用文字。實機測試才暴露出來:測試原本只斷言字串
   `clear_identity.py` 存在, 而那段文字在通用說明裡本來就有, 佔位符沒接上照樣通過。
   已改為斷言完整指令(含 `--name` / `--index`), 並以變異驗證確認能攔截。

## 7. 驗收標準

> **勾選狀態(2026-08-11)**:已勾選者由 `tests/test_card_identity.py` 的自動測試涵蓋
> (73 tests passed)**或已對實機 Anki 驗證**, 並額外做過**變異驗證** —— 逐一破壞決策表的四項判斷、寫檔閘門、
> 中斷保護與更新排除清單, 確認每項都會讓對應測試失敗, 而非恆真的斷言。
> 未勾選者需連線實機 Anki 或屬人工操作(P2), 待使用者執行。

- [x] P2 執行前已手動備份 `jsons/` 目錄(壓縮檔留存路徑記於本文件)
- [x] `--dry-run` 對五個 JSON 檔執行後, **檔案 mtime 未變**(證明未寫檔)
- [x] 正式執行後, 五個 JSON 檔的每張卡都有非空的 `cardId` 與 `noteId`
- [x] 對照 Anki:每個寫回的 `noteId` 都查得到, 且 `modelName == "Speaking_Trilingual_Dark"`
- [x] **改 `Prompt` 不再產生新卡**:任選一張卡改動 `Prompt` 文字 → 重跑 `--update-existing`
      → Anki 卡片總數不變, 且該 note 的 `Prompt` 已更新
- [x] 連續執行兩次匯入, 第二次的 JSON 檔內容與第一次完全相同(冪等)
- [x] **無身分時會建卡並警告**:新增一張沒有 `cardId`/`noteId` 的卡 → 匯入時印出警告、
      正常建立、且身分被寫回 JSON
- [x] **無身分時不查 `Prompt`**:把某張已有身分的卡「身分清掉但 `Prompt` 保持不變」→
      不加旗標匯入會**新建一張**(證明預設路徑確實不比對 `Prompt`)
- [x] **`--adopt-by-prompt` 能接管**:承上, 改用旗標重跑 → 接回原本那張 note 而非新建
- [x] **身分失效會跳過而非重建**:任選一張卡, 手動把 JSON 的 `noteId` 改成不存在的數字
      → 匯入時該卡被跳過並印出 §3.2 格式的診斷, Anki 卡片總數不變, JSON 檔未被改寫,
      且 exit code 非零
- [x] **半截身分會跳過**:手動刪掉某張卡的 `cardId`(保留 `noteId`)→ 同樣跳過並診斷
- [x] `clear_identity.py --dry-run` 不改檔(mtime 未變);正式執行後目標卡的
      `cardId`/`noteId` 消失, 而該檔其餘內容**逐字未變**
- [x] `clear_identity.py --index N` 只清第 N 張, 同檔其他卡的身分保留
- [x] **清身分後可重新接管**:對上一條清掉身分的卡以 `--adopt-by-prompt` 重跑 →
      接回原本那張 Anki 卡(`noteId` 與清除前相同), 而非新建一張
- [x] `--report-orphans` 正確列出目前已知的 8 張殘留卡(志望動機 4 + 逆質問 4), 並標示各自錄音筆數
- [x] `clear_recordings.py --dry-run` 能列出實際存在的 `.ogg` 檔名(不再回報「沒有找到音訊記錄」)
- [ ] `clear_recordings.py` 正式執行後, 目標卡的 `Recordings_*` 變為 `[]` 且媒體檔已刪除
- [x] `pytest backend/tests/` 全數通過（72 tests）
- [x] **P5 搬移後**：`git` 將 `card_identity.py` 判定為 rename（非 delete + add）
- [x] **P5 搬移後**：Trilingual 的 `import_cards.py --dry-run` 輸出與搬移前逐字一致
- [x] **P5 搬移後**：`pytest backend/tests/` 全綠（測試內容不變，僅 import 路徑調整）
- [x] **手寫的絕對素材路徑在匯入後保持不變**（`test_absolute_media_paths_survive_import`）
- [x] **接管命中多張時跳過並列出候選**，不任選一張
- [x] **接管查詢限定模型**，不會接管到別種 model 的 note
- [x] `clear_audio_fields` 對轉義／未轉義／巢狀 `audios` 三種形態皆能取出檔名
