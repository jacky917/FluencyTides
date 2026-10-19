# FEAT — 能力路由：用一張表管理各流程的 LLM、語音轉文字與語音評分

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-19 |
| **性質** | 設計計畫書；R1、R2 已實作（實作紀錄見 §11） |
| **狀態** | ✅ R1、R2 完成，PR #25；2026-10-19 歸檔。接下來的錄音評分網頁化屬 `docs/wip/web_ui_parity_FEAT_2026-10-18.md` 階段 C；各練法自己的 `recording_eval.<handler>` 隨 `docs/wip/speaking_card_types_PROPOSAL_2026-10-18.md` 的新卡一起宣告 |
| **範圍** | `backend/app/infrastructure/llm/routing.py`、`llm_route_service.py`、`api/llm_routes.py`、`bot/handlers/models_menu.py`、`/setconfig`（`commands.py`、`callbacks_config.py`、`core/dynamic_config.py`）、`infrastructure/audio_evaluator/`、`infrastructure/stt/`、`bot/handlers/voice.py`、資料表 `llm_routes` 與其 alembic 遷移、網頁的 LLM 模型設定頁、`scripts/common/llm_label.py`、`scripts/fastapi_client/query_backend_model.py` |
| **前置** | `feat/llm-model-routing`（P1～P4 的模型路由，`docs/archive/llm_model_routing_FEAT_2026-10-15.md`） |
| **相關** | `docs/wip/web_ui_parity_FEAT_2026-10-18.md`：本計畫取代其階段 B 的 `/setconfig` 部分，也是其階段 C（錄音評分）的前提。`docs/wip/speaking_card_types_PROPOSAL_2026-10-18.md`：各練法用自己的筆記類型與 handler |

---

## 1. 結論

- **一張表、一套解析規則，管理所有「用哪個工具」的設定。** 現在的 `llm_routes` 只管文字生成用哪個 LLM。這次擴充成「用途鍵 × 能力」：同一個用途鍵可以分別設定文字 LLM、聽音檔的模型、語音轉文字、評分策略。
- **用途鍵一律以流程與 handler 為基礎。** 需要依卡片類型區分時，用 `<流程>.<handler>`，例如 `recording_eval.speaking_trilingual`。解析時沿著 `.` 往上找：`recording_eval.speaking_trilingual` → `recording_eval` → 全域 `*` → `.env`。
- **練法由筆記類型與 handler 決定，設定表只決定工具。** 跟讀、情境回應等練法會同時影響評分策略、評分提示詞、參考答案的意義與卡面，所以每種練法各用一個筆記類型與 handler（§3.3）。設定表只回答「這個流程或 handler 用哪個工具、哪個模型」。
- **`/setconfig` 的暫時設定全部搬進來，舊功能移除。** `AUDIO_EVALUATOR_PROVIDER`、`AUDIO_MODEL_NAME`、`STT_LLM_MODEL_NAME` 改存資料庫、重啟後仍在；TG 的 `/models` 改名為 `/setconfig`，成為唯一的設定入口。
- **命名統一。** 表、service、API、網頁從「LLM 路由」改名為「能力路由」；欄位 `endpoint` 改名為 `provider`（§7）。用途鍵與能力的命名規範見 §4。

---

## 2. 現況

| 設定 | 存在哪 | 誰在讀 | 生效範圍 |
|---|---|---|---|
| 文字生成的 LLM（端點、模型、effort） | 資料表 `llm_routes`（`/models`、`/api/v1/llm-routes`） | 5 個以 `llm_route(...)` 宣告的用途鍵 | 依用途；重啟後仍在 |
| `AUDIO_EVALUATOR_PROVIDER` | 記憶體（`/setconfig`），預設 `.env` | `audio_evaluator/factory.py:51` 建 `app.state.audio_evaluator` | 全域；重啟後恢復 |
| `AUDIO_MODEL_NAME` | 同上 | `gemini_client.py:133`、`openai_client.py:172`、`proxy_client.py:172` 直接讀 settings | 全域；重啟後恢復 |
| `STT_LLM_MODEL_NAME` | 同上 | `stt_llm_evaluator.py:174`；客戶端用 `AUDIO_API_KEY` / `AUDIO_BASE_URL` 自建 | 全域；重啟後恢復 |
| `STT_MODEL_NAME` | 只有 `.env` | `stt/whisper_client.py` | 全域 |
| 評分提示詞 | 程式碼 | `voice.py:176-183`：三語卡用 `Speaking_Trilingual_{ZH,JA,EN}.j2`，其他卡用 `audio_evaluator.j2` | 依筆記類型 |

`llm_routes` 目前的欄位：`id`、`route_key`（唯一，`[A-Za-z0-9._-]{1,64}`）、`endpoint`、`model`、`effort`、`updated_by`、`created_at`、`updated_at`。

---

## 3. 能力（capability）

### 3.1 兩類能力

這也是命名規範（§4.2）區分「模型」與「工具」的依據。

| 類別 | capability | 用途 | `provider` 是 | `model` / `effort` |
|---|---|---|---|---|
| **模型能力** | `llm` | 文字生成（建卡、讀音判斷、`stt_llm` 的評分） | LLM 端點 ID（`.env` 端點白名單） | 白名單內的模型；effort 依模型 |
| **模型能力** | `audio_llm` | 直接聽音檔評分的多模態模型 | 支援音訊輸入的端點 ID | 該端點的音訊模型白名單 |
| **工具能力** | `stt` | 語音轉文字 | STT 來源 ID（目前只有自架 Whisper：`whisper`） | Whisper 模型；無 effort |
| **工具能力** | `audio_eval` | 錄音評分的策略 | 策略名：`listen`、`stt_diff`、`stt_llm` | 留空 |

### 3.2 能力的組合

`audio_eval` 只記策略，需要的模型再用**同一個用途鍵**去查其他能力（各能力各自沿 §5 的順序往上找）：

| 策略 | 會用到的能力 | 對應現在的 |
|---|---|---|
| `listen` | `audio_llm` | `gemini_native`、`openai`、`proxy`（差別在端點類型，由 `audio_llm` 的 provider 決定） |
| `stt_diff` | `stt` | `stt_diff` |
| `stt_llm` | `stt`、`llm` | `stt_llm`（`STT_LLM_MODEL_NAME` 變成這個鍵的 `llm`） |

**流程決定用哪些能力，設定只決定由誰提供。** 錄音評分一定查 `audio_eval`，再依策略查其他能力；建卡只查 `llm`。每個用途鍵在宣告時列出會用到的能力，設定表不接受對其他能力的寫入，所以不會出現「設了沒人用」。網頁與 TG 選單也只列出該鍵會用到的能力。

### 3.3 練法、提示詞與能力的分工

| 由誰決定 | 內容 |
|---|---|
| **筆記類型＋handler**（程式碼與模板） | 卡面長什麼樣、參考答案的意義、評分提示詞（j2）、這個 handler 能用哪些評分策略 |
| **能力路由**（設定表） | 實際用哪個策略、哪個端點、哪個模型、多深的 effort |

提示詞留在程式碼裡：提示詞與輸出格式、欄位讀法綁在一起，放進設定容易組出壞掉的組合。新增一種練法（例如跟讀）＝新增一個筆記類型與 handler（含自己的提示詞），並宣告它的用途鍵。

---

## 4. 命名規範

### 4.1 用途鍵（route key）

用途鍵回答「**這是哪個流程（或哪個流程中的哪個 handler、哪個步驟）的設定**」。

| 種類 | 格式 | 宣告位置 | 範例 |
|---|---|---|---|
| 全域 | `*` | 系統保留 | `*` |
| 流程鍵 | `<流程>` | 該流程的 service 或 handler | `jp_verb_pair`、`jp_verb_reading`、`expression_correction`、`recording_eval` |
| 流程鍵的分支 | `<流程>.<handler 名>` 或 `<流程>.<步驟>` | 負責該分支的 handler 或 service | `recording_eval.speaking_coach`、`expression_correction.preview` |

規則：
1. **字元與長度不變**：英數字與 `.` `_` `-`，最長 64，與現在的格式檢查相同。名稱一律小寫 snake_case，`.` 只用來分層。
2. **流程名**用「領域＿對象」或「動作＿對象」：`jp_verb_pair`（自他動詞子卡生成）、`recording_eval`（錄音評分）、`speaking_reference`（口說提案的參考回答生成）。現有 5 個鍵都符合，不改名。
3. **分支名**：
   - 同一流程依卡片類型用不同工具時，用 **handler 名**（與 `handler_registry` 中的 `handler_name` 完全相同），例如 `recording_eval.speaking_trilingual`。
   - 同一流程有兩個步驟需要不同工具時，用**步驟名**（動詞），例如 `expression_correction.preview`、`expression_correction.create`。
   - 只有確實需要不同設定時才分支；設定相同就只用流程鍵。
4. **層數最多兩層**（`<流程>.<分支>`）。真的需要更細時再修改這份規範。
5. **每個鍵都要在程式中宣告**，宣告時列出會用到的能力（§8.1）。未宣告的鍵不能寫入；程式中已刪除的鍵在資料表中成為孤兒鍵，沿用現有規則標示、可以刪除。

### 4.2 能力（capability）

能力回答「**要哪一種工具**」，一律小寫 snake_case 名詞：

| 規則 | 說明 |
|---|---|
| **模型能力以 `llm` 結尾** | `llm`（文字）、`audio_llm`（音訊輸入）。採「`<輸入模態>_llm`」的形式；日後如有影像輸入，就是 `image_llm` |
| **工具能力以功能命名** | `stt`（語音轉文字）、`audio_eval`（評分策略）；日後如有語音合成，就是 `tts` |
| 能力清單寫在程式裡 | 新增能力要改程式（加白名單與檢查），不能由設定新增 |

### 4.3 provider 與 model

| 能力類別 | provider 命名 | model 命名 |
|---|---|---|
| 模型能力 | `.env` 端點 ID（小寫，例如 `claude-code`、`yinli`、`gemini`） | 端點白名單中的模型名，原樣（區分大小寫） |
| 工具能力 | 小寫 snake_case 的工具或策略名（`whisper`、`listen`、`stt_diff`、`stt_llm`） | 工具自己的模型名（例如 Whisper 模型）；策略類留空 |

### 4.4 實例

**例 1：自他動詞子卡生成（只用 LLM，不分支）**

```python
# jp_verb_pair_handler.py
CHILD_CARD = route("jp_verb_pair", "自他動詞子卡生成", capabilities={"llm"})
```

| route_key | capability | provider | model | effort |
|---|---|---|---|---|
| `jp_verb_pair` | `llm` | `claude-code` | `claude-opus-5` | `high` |
| `*` | `llm` | `yinli` | `gemini-3.5-flash` | |

建子卡時 `llm` 查到 `jp_verb_pair`，用 Claude Code；刪掉那一列就退到 `*`。

**例 2：錄音評分（同一流程，依 handler 用不同工具）**

```python
# recording_service.py（網頁版計畫階段 C 新增）
RECORDING_EVAL = route("recording_eval", "錄音評分", capabilities={"audio_eval", "audio_llm", "stt", "llm"})

# speaking_trilingual_handler.py
RECORDING_EVAL_TRILINGUAL = route(
    "recording_eval.speaking_trilingual", "三語卡錄音評分", capabilities={"audio_eval", "audio_llm", "stt", "llm"}
)

# shadowing_handler.py（口說提案卡 1，新的筆記類型與 handler）
RECORDING_EVAL_SHADOWING = route("recording_eval.shadowing", "跟讀卡錄音評分", capabilities={"audio_eval", "stt", "llm"})
```

| route_key | capability | provider | model |
|---|---|---|---|
| `recording_eval` | `audio_eval` | `listen` | |
| `recording_eval` | `audio_llm` | `gemini` | `gemini-3.5-flash` |
| `recording_eval.speaking_trilingual` | `audio_eval` | `stt_llm` | |
| `recording_eval.speaking_trilingual` | `llm` | `yinli` | `gemini-3.1-flash-lite` |
| `recording_eval.shadowing` | `audio_eval` | `stt_diff` | |
| `*` | `stt` | `whisper` | `large-v3` |

| 卡片（筆記類型 → handler） | `audio_eval` | 其他能力 | 結果 |
|---|---|---|---|
| Speaking_Coach_Dark → `speaking_coach` | `recording_eval.speaking_coach` 沒設，退到 `recording_eval`：`listen` | `audio_llm` → `recording_eval`：gemini-3.5-flash | Gemini 直接聽，用 `audio_evaluator.j2` |
| Speaking_Trilingual_Dark → `speaking_trilingual` | `recording_eval.speaking_trilingual`：`stt_llm` | `stt` → `*`：whisper large-v3；`llm` → `recording_eval.speaking_trilingual`：gemini-3.1-flash-lite | Whisper 轉文字，再用便宜模型與三語提示詞評分 |
| Shadowing_Dark → `shadowing` | `recording_eval.shadowing`：`stt_diff` | `stt` → `*`：whisper large-v3 | 與原文逐字比對 |

`recording_eval.speaking_coach` 不必宣告也不必設定：沒有分支時就用流程鍵。只有確實要和流程預設不同的 handler 才宣告分支。

**例 3：一個流程的兩個步驟用不同模型**

```python
PREVIEW = route("expression_correction.preview", "外語糾錯：預覽", capabilities={"llm"})
CREATE = route("expression_correction.create", "外語糾錯：寫入子卡", capabilities={"llm"})
```

| route_key | capability | provider | model |
|---|---|---|---|
| `expression_correction.preview` | `llm` | `claude-code` | `claude-opus-5` |
| `expression_correction.create` | `llm` | `yinli` | `gemini-3.5-flash` |

兩步都用同一個模型的話，維持單一的 `expression_correction`，不要拆。

### 4.5 常見的寫錯

| 寫法 | 問題 |
|---|---|
| `recording_eval.Speaking_Trilingual_Dark` | 分支用的是 handler 名（`speaking_trilingual`），不是筆記類型名 |
| `recording_eval.speaking_trilingual.ja` | 超過兩層；三語卡依語言換提示詞是 handler 的事，不是工具設定 |
| 在 `jp_verb_pair` 寫入 `audio_eval` | 這個鍵宣告時沒有 `audio_eval`，寫入時拒絕 |
| `audio_eval` 的 provider 填 `gemini` | `audio_eval` 是策略，只能填 `listen` / `stt_diff` / `stt_llm`；模型設在 `audio_llm` |
| 想讓同一種筆記類型在不同牌組用不同策略 | 練法不同就應該是不同的筆記類型與 handler（§3.3），因為提示詞與卡面也要跟著不同 |

---

## 5. 解析順序

每個能力**各自**沿著用途鍵往上找，找到第一層有效（在白名單內）的設定就用；無效的層照現在的做法記進 `rejected`，顯示在管理介面上。

1. `<流程>.<分支>`（有分支時）
2. `<流程>`
3. 全域 `*`
4. `.env`

例：`recording_eval.speaking_trilingual` 只設了 `audio_eval` 與 `llm`；`stt` 會往上找到 `recording_eval`，那裡沒設，再到 `*`。

例外（R2 實作時決定，見 §11.1）：用途鍵為某個能力宣告了自己的預設（`env_defaults`）時，該預設排在 `*` 之前。目前只有錄音評分的 `llm`（audio 端點＋`STT_LLM_MODEL_NAME`）。

---

## 6. 資料表與遷移

### 6.1 新表 `capability_routes`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | int PK | 自增（與現在相同，MySQL 相容） |
| `route_key` | varchar(64) | 用途鍵（§4.1），長度與格式不變 |
| `capability` | varchar(32) | 能力（§4.2） |
| `provider` | varchar(64) | 由 `endpoint` 改名（§4.3） |
| `model` | varchar(128)，可為 NULL | 策略類能力為 NULL |
| `effort` | varchar(16)，可為 NULL | |
| `updated_by` | varchar(64) | 不變 |
| `created_at`、`updated_at` | datetime | 不變 |

唯一鍵：`(route_key, capability)`。

不加 JSON 的 `options` 欄位：每個欄位都有固定意義、可以檢查。日後某個能力真的需要額外參數，再加欄位。

### 6.2 遷移（alembic）

1. 建 `capability_routes`。
2. 從 `llm_routes` 逐列複製：`capability = 'llm'`，`provider = endpoint`，其餘欄位照搬。
3. 刪除 `llm_routes`。downgrade 反向：只搬回 `capability = 'llm'` 的列。
4. 現有 `/models` 的設定全部保留；遷移後 `GET` 的結果應與遷移前相同（測試驗證）。

### 6.3 `.env` 那一層（預設值）

| 能力 | `.env` 預設來源 |
|---|---|
| `llm` | 不變（`LLM_PROVIDER` / `LLM_MODEL_NAME` 等） |
| `audio_llm` | `AUDIO_EVALUATOR_PROVIDER` 是 `gemini_native` / `openai` / `proxy` 時，對應的端點類型，加上 `AUDIO_MODEL_NAME`、`AUDIO_API_KEY`、`AUDIO_BASE_URL` |
| `stt` | `STT_SERVER_URL`、`STT_MODEL_NAME`、`STT_API_KEY` |
| `audio_eval` | `AUDIO_EVALUATOR_PROVIDER` 換成策略：`gemini_native`、`openai`、`proxy` → `listen`；其餘照名 |

### 6.4 白名單

| 能力 | 白名單來源 |
|---|---|
| `llm` | 不變：`LLM_ENDPOINTS__<ID>__MODELS` 等 |
| `audio_llm` | 新增：`LLM_ENDPOINTS__<ID>__AUDIO_MODELS`（只有標明支援音訊的模型能選，避免把純文字模型拿去聽音檔） |
| `stt` | 新增：`STT_MODELS`（JSON 陣列）；未設定時只能用 `STT_MODEL_NAME` |
| `audio_eval` | 程式中的固定清單（`listen`、`stt_diff`、`stt_llm`）。用途鍵宣告時可以再限縮，例如跟讀卡只允許 `stt_diff`、`stt_llm` |

---

## 7. 改名

| 層 | 現在 | 改為 |
|---|---|---|
| TG 指令 | `/models`、`/setconfig`（暫時設定） | 合併為 **`/setconfig`**（存資料庫）；`/models` 移除，不留別名。舊 `/setconfig` 在 R2 才移除，所以指令改名也在 R2 做 |
| 資料表 | `llm_routes` | `capability_routes` |
| 宣告函式 | `llm_route(key, description)` | `route(key, description, capabilities=...)`，宣告這個鍵會用到的能力 |
| service | `llm_route_service` | `capability_route_service` |
| REST API | `/api/v1/llm-routes` | `/api/v1/capability-routes` |
| 錯誤碼 | `LLM_ROUTE_NOT_FOUND`、`INVALID_LLM_ROUTE`、`LLM_ROUTE_STORE_UNAVAILABLE` | `ROUTE_NOT_FOUND`、`INVALID_ROUTE`、`ROUTE_STORE_UNAVAILABLE` |
| 網頁 | 「LLM 模型設定」 | 「工具設定」 |
| 欄位 | `endpoint` | `provider`（API 的請求與回應也一樣），另加 `capability` |

改名要跟著更新的呼叫端：`scripts/common/llm_label.py`（讀標籤）、`scripts/fastapi_client/query_backend_model.py`、`/api/v1/config` 診斷輸出中的 `llm_routes` 區塊、網頁的 `client.ts` / `types/api.ts` / 設定頁、TG `/help` 與 `texts.py`。

**說明文字要改的意義：** 舊 `/setconfig` 是「暫時、重啟恢復」，新的是「存資料庫、重啟後仍在」，`/help` 要寫清楚。

---

## 8. 程式改動

1. **宣告與解析**（`routing.py`）：
   - `route(key, description, capabilities, allowed_strategies=None)` 登記用途鍵與它會用到的能力；格式檢查沿用現在的規則，另檢查最多兩層。
   - `resolve(key, capability)` 依 §5 沿 `.` 往上找；快取與無效層的處理沿用現在的做法。
   - 錄音評分依卡片的筆記類型找到 handler（`handler_registry`），組出 `recording_eval.<handler>`：有宣告分支就從分支開始，沒有就從 `recording_eval` 開始。
2. **service 與 API**：讀寫以 `(route_key, capability)` 為單位。PUT 依能力檢查白名單並試建：`llm`、`audio_llm` 試建客戶端，`stt` 檢查伺服器設定，`audio_eval` 檢查策略名與該鍵允許的策略。GET 列出每個鍵的各能力狀態與來源。
3. **語音評分**：`audio_evaluator/factory.py` 不再讀全域 settings，改依解析出的 `audio_eval`、`audio_llm`、`stt`、`llm` 建立評分器（依設定組合快取，設定變更時清除）。`app.state.audio_evaluator` 移除。`stt_llm_evaluator` 改用 `llm` 能力的客戶端，不再自建 `AUDIO_*` 客戶端。評分提示詞的選擇不變，仍由程式依 handler 決定（`voice.py:176-183`，階段 C 移到 `RecordingService`）。
4. **移除** `/setconfig` 的舊實作：`callbacks_config.py`、`core/dynamic_config.py`、`MODIFY_*` 的讀取，以及 `.env.example` 的 `MODIFY_*` 區塊。
5. **TG `/setconfig` 選單**：選用途鍵 → 選能力（只列該鍵宣告的）→ 選 provider → 選模型 → 選 effort。沿用 `models_menu.py` 的畫面流程，多一層「能力」。
6. **網頁設定頁**：由現在的 LLM 模型設定頁擴充，每個鍵顯示各能力的生效設定與來源，分支鍵縮排在流程鍵之下。

---

## 9. 分階段

| 階段 | 內容 | 驗收 |
|---|---|---|
| **R1 改名與資料表**（行為不變） | §6 的新表與遷移、§7 的改名（TG 指令除外）、`route(...)` 宣告；只有 `llm` 能力 | 現有 `/models` 設定遷移後結果相同；全部既有測試通過；TG `/models` 與網頁「Tool Settings」頁的功能與原本相同 |
| **R2 語音能力** | `audio_llm`、`stt`、`audio_eval` 的白名單與解析；宣告 `recording_eval`（三語卡另宣告分支）；TG 錄音評分改依設定建立評分器；移除舊 `/setconfig` 與 `MODIFY_*`；TG `/models` 改名為 `/setconfig` | 三種策略都能經設定切換、重啟後仍在；三語卡與對話卡可以設不同策略；TG 錄音評分照常運作 |

R2 完成後再進行網頁版計畫的**階段 C（錄音評分）**：`RecordingService` 依卡片的 handler 解析能力，TG 與網頁共用。口說提案的新練法（例如跟讀卡）各自新增筆記類型與 handler 時，順帶宣告自己的 `recording_eval.<handler>`。

每階段都要通過下列檢查，並在瀏覽器與 TG 走過流程：

```bash
cd backend && python -m pytest tests -q
```

```bash
cd frontend && npm run lint && npm test && npm run build
```

---

## 10. 待決策

1. ~~**`proxy` 評分器**要不要保留~~ → 保留，成為 openai 類端點的送音檔方式 `AUDIO_TRANSPORT=image_url`（§11）。
2. ~~**評分器快取**~~ → 依設定組合快取，任何寫入或刪除（路由快取世代改變）時清空。
3. ~~**R1 的改名是否一次到位**~~ → 一次改完，不留舊 API 與 `/models` 別名。

---

## 11. 實作紀錄

### 11.1 R2（2026-10-19）

依 §3–§9 實作，以下是計畫書沒寫死、實作時決定的部分：

| 項目 | 做法 | 理由 |
|---|---|---|
| `AUDIO_*` 的憑證放哪 | 合成一個內建端點，ID 固定為 `audio`（保留字，自訂端點不能用）。類型：`AUDIO_EVALUATOR_PROVIDER=gemini_native` → gemini-native；其他（`openai` / `proxy` / `stt_diff` / `stt_llm`）→ openai，沒有 `AUDIO_BASE_URL` 時用 OpenAI 官方網址；`proxy` 以 `image_url` 送音檔，其餘以 `input_audio`。啟動 log 記一行選定的類型 | `audio_llm` 與 `stt_llm` 的 `llm` 都要能選到「現在用的那把金鑰」，現有 `.env` 不必改，行為與能力路由之前相同 |
| audio 端點的白名單 | 音訊模型＝`AUDIO_MODELS`＋`AUDIO_MODEL_NAME`；文字模型＝同一份清單＋`STT_LLM_MODEL_NAME` | 取代 `MODIFY_AUDIO_MODEL_NAME` 與 `MODIFY_STT_LLM_MODEL_NAME`；能聽音檔的模型也能做文字評分 |
| 自訂端點的音訊模型 | `LLM_ENDPOINTS__<ID>__AUDIO_MODELS`、`LLM_ENDPOINTS__<ID>__AUDIO_TRANSPORT`（`input_audio` 預設 / `image_url`） | §6.4；`image_url` 就是原本的 proxy 評分器 |
| `stt_llm` 的 `llm` 預設 | `recording_eval` 宣告時給 `env_defaults`：audio 端點＋`STT_LLM_MODEL_NAME`，分支沿用。**這種用途鍵宣告的預設排在全域 `*` 之前**：錄音評分的 `llm` 解析為「分支 → 流程 → 宣告的預設」，不看 `*` | `*` 的 `llm` 是建卡的預設（例如 Claude Code）；否則設了 `*` 就會讓 stt_llm 改用 Claude Code（慢、吃訂閱）。要換錄音評分用的模型就直接設 `recording_eval` |
| `stt_llm` 評分器 | 改用 `llm` 能力的 `BaseLLMClient.generate_structured_data`，不再自建 `AsyncOpenAI`；LLM 客戶端隨評分器快取 | §8.3。行為差異：temperature 改為客戶端的設定（原本 0.3），重試與逾時改用客戶端的策略（原本 90 秒、不重試） |
| 三語卡分支宣告位置 | 與流程鍵一起放在 `services/recording_eval.py`（`capability_route_service` 會 import 它，確保清單一定有） | 階段 C 的 `RecordingService` 出現後再搬 |
| 錯誤 | 建不出評分器時拋 `AudioEvaluatorUnavailableError`（503），訊息說明哪個能力、哪一層、哪個設定；TG 照實回覆並保留錄音模式 | 改好設定後可在時限內直接重傳 |
| `/api/v1/config` | 移除 `configs` 與 `GET /config/{key}`（來自 `MODIFY_*`），只留 `runtime` | §8.4 |
| TG 清單與網頁卡片的 ⚠️ | 有評分策略的鍵只看策略本身與策略用到的能力（全域另算文字 LLM）；各能力自己的警示照常顯示 | 策略是 listen 時，STT 沒設定不影響評分，不必標 |
| 評分器組成的讀取 | 一則語音的策略與各能力由同一份設定快照解析 | 讀取期間有寫入時，不會把新策略配上舊模型 |
| 舊 `/setconfig` 的按鈕 | 聊天裡殘留的 `setconfig_*` / `setcfg_val:*` 按鈕回覆「選單已過期」 | 不讓按鈕一直轉圈 |

### 11.2 部署注意

正式機的 `.env` 若要保留原本 `/setconfig` 可選的模型，需加上（值沿用原本 `MODIFY_*` 的清單）：

```text
AUDIO_MODELS=["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"]
```

`MODIFY_*` 可以刪掉（留著也不再有作用）。沒有任何 DB 設定時，錄音評分照 `.env` 運作，與部署前相同。

### 11.3 驗證

```bash
cd backend && python -m pytest tests -q
```

```bash
cd frontend && npm run lint && npm test && npm run build
```

另以暫存 SQLite 啟動後端與網頁實測：網頁把全域 `*` 的評分策略改成 `stt_diff`、三語卡分支設 `stt_llm` 後，`recording_eval` 沿用全域（來源「全域」）、三語卡用自己的設定；重啟後端後設定仍在，啟動 log 顯示依設定建立的評分器（`recording_eval → stt_diff`）。TG 實機錄音評分尚未在正式機走過（需部署後確認）。
