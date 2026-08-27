# FEAT — Claude CLI 環境標準化與 headless 參數全量實測

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-27 |
| **性質** | 環境配置 + 測試驗證(claude-code LLM Provider 的前置依賴) |
| **狀態** | ✅ 完成(2026-08-27,含第二輪值域補測):CLI 2.1.247 已裝、`claude auth login` 認證就緒、A 組 17 項 + B 組 8 項全數實測,定案組合與值域表見 §3/§5 |
| **範圍** | 本機 CLI 安裝與 PATH、headless 認證、參數實測矩陣與結論 |
| **不動** | 專案代碼零改動;API key 計費模式不涉及 |
| **PR / 進度** | 隨 [#9](https://github.com/jacky917/FluencyTides/pull/9) 一併提交(commit `911bdc7`) |
| **關聯文件** | `docs/wip/claude_code_llm_provider_FEAT_2026-08-27.md`(消費本文件的定案組合與基準) |

---

## 1. 目的

claude-code LLM Provider 以 subprocess 呼叫 `claude -p` 完成結構化生成。動工前
需要:①穩定可用的 CLI 環境(非版本化路徑、headless 認證);②provider 呼叫
路徑上每個旗標與行為假設的實測結論,避免基於猜測寫代碼。

## 2. 環境配置(最終形態,可供重建環境時照做)

### 2.1 安裝

原生安裝,裝至穩定路徑並註冊 User PATH:

```powershell
irm https://claude.ai/install.ps1 | iex
```

驗證(新開終端):

```powershell
where claude
claude --version
```

現況:`%USERPROFILE%\.local\bin\claude.exe`,版本 2.1.247,`claude doctor` 無紅項。

### 2.2 認證

一次性互動登入(瀏覽器 OAuth,Claude Max 訂閱):

```powershell
claude auth login
```

憑證落盤 `~/.claude/.credentials.json`,**任何 subprocess 零配置直接可用**,
不需要任何 token 環境變數。隨時驗證:

```powershell
claude auth status --text
```

已登入 exit 0,未登入 exit 1(provider 可用作前置檢查)。

> **陷阱**:`CLAUDE_CODE_OAUTH_TOKEN` 環境變數優先級**高於**落盤憑證,殘留的
> 壞 token 會蓋掉有效登入造成 401。provider 應在 subprocess env 中主動剔除此變數。

## 3. 實測結果(2026-08-27,CLI 2.1.247)

### A 組 — 核心(provider 呼叫路徑,全數通過)

| # | 項目 | 結果 |
|---|---|---|
| A1 | 安裝健康(`--version` / `doctor`) | ✅ 無紅項 |
| A2 | `-p` 最小生成 | ✅ 3.3s(haiku) |
| A3 | `--output-format json` 信封 | ✅ 欄位:`result` / `is_error` / `terminal_reason` / `errors` / `duration_ms` / `num_turns` / `usage` / `modelUsage`;`total_cost_usd` 非零但訂閱模式僅供參考 |
| A4 | `--json-schema`(簡單 schema) | ✅ |
| A5 | `--json-schema`(實戰 `ChildCardGenerationResult`) | ✅ **前提:schema 必須展平 `$defs`/`$ref`**。含 $defs 的原始 pydantic schema 在 haiku 上觸發 CLI 內建 5 次重試全敗(`terminal_reason: structured_output_retry_exhausted`,耗 189s);展平後 haiku/sonnet/opus 全過,enum 均被遵守 |
| A6 | `result` 內容形態 | ✅ 純 JSON、無 Markdown 圍欄(防禦性剝殼仍保留) |
| A7 | `--model` 別名/全名/錯名 | ✅ `haiku`→`claude-haiku-4-5-20251001`、`sonnet`→`claude-sonnet-5`、全名可用;錯名 → stderr `[claude-code:unrecognized_model]` + `is_error:true` |
| A8 | `--tools ""` 工具禁用 | ✅ `num_turns:1` 無工具執行(模型可能在文本中「表演」工具呼叫,對 --json-schema 場景無影響) |
| A9 | `--safe-mode` 隔離 | ✅ canary CLAUDE.md 對照實驗:無旗標時暗號被觸發,有旗標時完全隔離 |
| A10 | `--no-session-persistence` | ✅ 對照成立:有旗標無新檔(2→2),無旗標留檔(2→3) |
| A11 | stdin 17.8KB UTF-8 日文實戰 prompt | ✅ 無亂碼。教訓:讀取端必須顯式 UTF-8(bytes + `decode("utf-8")`),不可依賴系統編碼(cp950) |
| A12 | auth 失效形態 | ✅ 無憑證 → `result` 含 `Not logged in · Please run /login`;壞 env token → `401 OAuth access token is invalid` |
| A13 | Python `subprocess` 呼叫 | ✅ 落盤憑證直接生效,零 env 配置(併發測試即以此形態執行) |
| A14 | `--effort` × 模型相容矩陣 | ✅ `medium`/`high`/`xhigh`/`max` × haiku、`high` × sonnet、`max` × opus 全部接受,無模型限制(官方文檔稱「層級取決於模型」,實測未遇到限制) |
| A15 | 非法 `--effort` 值 | ⚠️ **靜默回退**:stderr Warning + 以預設力度照常執行(`is_error:false`)—— 與 `--model` 錯名的硬失敗行為相反,**provider 必須自行白名單驗證 effort 值** |
| A16 | 非法 `--json-schema` | ✅ 快速失敗:stderr `Error: --json-schema is not valid JSON: ...`,不燒額度,可識別 |
| A17 | `ANTHROPIC_API_KEY` env 優先級 | ✅ 無威脅:塞假 key 呼叫照常成功 —— 登入態憑證優先(與 `CLAUDE_CODE_OAUTH_TOKEN` 會蓋憑證的行為**相反**)。仍建議衛生性剔除 |

> A14–A17 為 2026-08-27 第二輪補測:`--effort` 進入定案命令後升格核心(A 組
> 定義=呼叫路徑上的每個行為假設),連帶掃出同類「值域未驗證」盲區一併補測。

### B 組 — 優化(採用/不採用結論)

| # | 項目 | 結果 / 結論 |
|---|---|---|
| B1 | `--effort` | ✅ **採用,明寫 `--effort high` 為預設**(不依賴 CLI 隱式預設,防版本漂移)。實測 opus+`high` 21.7s 與無旗標基準 20.5s 行為一致、品質相同。**降力度警示(數據點,非禁用)**:sonnet+`low` 11.6s(vs 36s)快 3 倍,但挖空腦補了原文不存在的助詞「が」,違反 prompt 硬規則;頂級模型(opus/fable)+`low` 未評估 —— 想調低先以實戰 prompt 驗證品質再上 |
| B2 | `--fallback-model` | 旗標可用,暫不納入(保持呼叫簡單,量產遇過載再啟用) |
| B3 | `--system-prompt` | ✅ **採用**。canary 標記驗證生效;handler 的 system_prompt 走此旗標,並省下預設系統提示的 token |
| B4 | 併發多進程 | ✅ 2 路併發正常(各 ~2.4s)。管線目前序列,留作未來選項 |
| B5 | 耗時基準(17.8KB 實戰 prompt + 展平 schema) | **opus ≈ 21s / sonnet ≈ 36s / haiku ≈ 116s**(各 1 次)。速度與品質同向:預設 opus,備選 sonnet |
| B6 | `--setting-sources` | 不納入,`--safe-mode` 已覆蓋隔離需求 |
| B7 | `--exclude-dynamic-system-prompt-sections` | 不納入,僅對預設系統提示有效,與 `--system-prompt` 整替互斥 |
| B8 | `--bare` | ❌ **排除**。auth 僅認 `ANTHROPIC_API_KEY`,不讀 OAuth,訂閱模式不可用 |

### C 組 — 未測(與 headless 純生成無關)

| 旗標 | 理由 |
|---|---|
| `-c` / `-r` / `--fork-session` / `--session-id` | 會話延續類;provider 每次全新進程 |
| `--cloud` / `--environment` / `--teleport` | 雲端會話;本案全本機 |
| `--mcp-config` / `--strict-mcp-config` / `--agents` / `--plugin-*` | 無工具無插件場景 |
| `--ide` / `--tmux` / `--chrome` / `--remote-control` | 互動 UI 整合 |
| `--dangerously-skip-permissions` 系列 | `--tools ""` 下無權限請求 |
| `--input-format` / stream-json 類 | 單發 json 輸出,不做串流 |
| `--max-budget-usd` | 僅 API key 計費有意義 |
| 額度耗盡輸出形態 | 不可主動重現;量產自然遇到時補記 |

### 值域實測 — 定案命令中每個參數的具體可用值

| 參數 | 實測可用值 | 非法值行為 |
|---|---|---|
| `--model` | 全量實測(2026-08-27,本帳號訂閱)。**別名**:`opus`→`claude-opus-5`、`sonnet`→`claude-sonnet-5`、`haiku`→`claude-haiku-4-5-20251001`、`fable`→`claude-fable-5`。**完整名**:`claude-fable-5` / `claude-opus-5` / `claude-opus-4-8` / `claude-opus-4-7` / `claude-opus-4-6` / `claude-sonnet-5` / `claude-sonnet-4-6` / `claude-haiku-4-5` / `claude-haiku-4-5-20251001` 共 9 個全數可用。**不可用**:`claude-mythos-5`(僅限核准組織) | 兩種錯誤形態:①模型名不存在 → stderr `[claude-code:unrecognized_model]` **硬失敗**;②模型存在但無權限 → 信封 `api_error` + `result` 含 `may not have access to it`(provider 已針對此形態做免重試分流) |
| `--effort` | `low` / `medium` / `high` / `xhigh` / `max`,三模型全部接受。定案:預設 `high`;sonnet+`low` 實測品質下降(B1 數據點,其他模型組合未評估,調低前先驗證);`xhigh`/`max` 未做品質評估,需要時另測 | **靜默回退**:stderr Warning + 以預設力度照跑(`is_error:false`)→ 呼叫端必須自行白名單驗證 |
| `--output-format` | 採用 `json`(`text` 為 CLI 預設不採用;`stream-json` 排除) | 未測(白名單保證不會送出非法值) |
| `--json-schema` | 展平後的 JSON Schema(`$defs`/`$ref` 必須先解掉,見 A5);enum/巢狀/陣列均被遵守 | **快速失敗**:stderr `Error: --json-schema is not valid JSON`,不燒額度 |
| `--tools` | `""`(空字串)= 全部禁用(A8) | — |
| `--system-prompt` | 任意文本,整替預設系統提示(B3) | — |
| auth 相關 env | `CLAUDE_CODE_OAUTH_TOKEN`:**優先級高於落盤憑證,必須剔除**(A12);`ANTHROPIC_API_KEY`:登入態優先、無威脅,衛生性剔除(A17) | — |

## 4. 品質驗證摘要

實戰 prompt(J2 模板真渲染:世界觀 + 120 行規則 + 12 句日文對話,目標動詞
「見つかる」)在三個模型上的輸出全部通過:

- pydantic `ChildCardGenerationResult.model_validate`
- 自他判定正確(intransitive)
- `cloze_blanks` 為目標句一字不差的子字串
- 12 句翻譯全保留且 id 連續
- 翻譯語感自然(繁中口語)

### 4.1 模型×力度全量掃描(2026-08-27,真實管線 `--limit 10 --skip-narrator`)

七組配置各以獨立 backend 實跑生成管線,66 張卡全數通過程式化檢查
(挖空子字串、保留句數、summary 規則等)+ 逐張人工評閱:

| 配置 | 秒/張 | 硬失敗 | 語言品質(有效候選句上) |
|---|---|---|---|
| opus@low | 32.5 | 0 | 1 處翻譯混入英文、1 處過度挖空(含「なんて、」) |
| **opus@medium** | **35.4** | **0** | **零錯誤**(僅 1 行雙版本翻譯的外觀瑕疵);對垃圾候選句的處理全場最誠實 |
| opus@high | 53.8 | 0 | 零錯誤;正確識別「よりによって」慣用化 |
| sonnet@medium | 53.0 | 0 | 補助動詞挖空一致性 1 處偏差、summary 1 處混入英文 |
| sonnet@high | 81.3 | 0 | 複合動詞挖空範圍 1 處偏差 |
| sonnet@xhigh | 113.5 | 0 | 零錯誤 |
| sonnet@max | ~400(10 張超時,完成 6) | 0 | 1 處**捏造詞源**(把招呼語「よっす」解釋成「寄ります縮約」) |

**結論:`opus` + `medium` 為最高性價比**(35.4 秒/張,品質與 high 持平)。
`high` 保留為保守選項;sonnet 系列在本任務全面慢於同級 opus 且小錯較多;
`max` 過慢且未帶來品質增益。

**決賽複驗(2026-08-27,opus@medium vs opus@high 各 10 張,逐張評閱)**:

| | opus@medium | opus@high |
|---|---|---|
| 秒/張 | **38.1** | 58.2(+53%) |
| 管線失敗 | 0 | 1(挖空定位失敗) |
| 真候選句品質 | 6/6 全對 | 5/5 全對 |
| 假陽性候選處理 | 4/4 誠實標注,零拒答 | 4/5 誠實標注,**1 次內容拒答** |

high 的那次失敗是對露骨場景候選句的**內容拒答**(回空白挖空 + 拒絕翻譯),
且與它同批處理過同等內容的行為不一致;medium 對 4 張同類場景全部正常完成。
拒答會落入既有的 `record_failure` 跳句路徑,不炸批次,但浪費候選句與時間。
兩輪合計(掃描+決賽):medium 20 張零硬失敗、零語言錯誤;high 品質相同但
慢 5 成且有 1/21 拒答率。**opus@medium 定案為預設建議值。**

**掃描的附帶發現(管線級,與模型無關)**:短動詞(如「よる」)的 ES 候選
存在大量假陽性(語氣詞「〜よっ」、招呼語「よっす」),越晚消費的配置遇到
越多;模型無拒答機制只能將錯就錯。opus 系列會誠實標注「本句不含目標動詞」
並講解同形陷阱,sonnet@max 則出現捏造。治本之道是把 JP_CoreVerb 的 fugashi
token 驗證移植到 JP_VerbPair(已另開任務)。

## 5. 定案旗標組合(交付給 provider 計劃)

```
claude -p --safe-mode --tools "" --no-session-persistence \
  --model <LLM_MODEL_NAME:opus(預設)/ sonnet / haiku / 全名> \
  --effort <LLM_CLAUDE_CODE_EFFORT:high(預設)> \
  --output-format json \
  --system-prompt <handler 的 system_prompt> \
  --json-schema <經 _resolve_json_schema 展平後的 schema JSON> \
  < user_prompt(stdin,UTF-8 bytes)
```

provider 實作要點(全部有實測背書):

1. schema 先展平:複用現有 `LLMClient._resolve_json_schema()`(A5)
2. auth 靠落盤憑證;subprocess env 剔除 `CLAUDE_CODE_OAUTH_TOKEN`(必須,A12)
   與 `ANTHROPIC_API_KEY`(衛生性,A17)
3. stdout 一律 bytes + `decode("utf-8")`(A11)
4. 成功判定:`is_error:false` + `terminal_reason:"completed"`,`result` 為純 JSON(A6)
5. 失敗分流:`structured_output_retry_exhausted`(CLI 5 次重試耗盡)/
   `Not logged in`(未認證)/ `401 OAuth access token is invalid`(壞 token)/
   stderr `[claude-code:unrecognized_model]`(模型名錯誤)
6. 單次超時 ≥ 300s(CLI 內建重試最壞情況 ≈ 190s)
7. **effort 值必須在 Python 端白名單驗證**(`low`/`medium`/`high`/`xhigh`/`max`)
   —— CLI 對非法值靜默回退不報錯(A15),打錯字會無聲地跑在錯誤力度上

## 6. 遺留風險

| 風險 | 應對 |
|---|---|
| CLI 自動更新後行為漂移 | provider 日誌記錄 `--version`;信封欄位變化時比對本文件 §3 基準 |
| 額度耗盡輸出形態未知 | 量產首次遇到時記錄形態,補進 provider 的錯誤分流 |
| 登入憑證過期 | A12 形態可偵測;報錯訊息附 `claude auth login` 教學 |

## 7. 驗收標準

- [x] 新開終端 `claude --version` 直接可用,路徑非版本化
- [x] `claude doctor` 無紅項
- [x] subprocess 零配置完成 `-p` 生成(落盤憑證)
- [x] A 組 17 項全數通過(⚠️ 均附已接受的註記),❌ 為零
- [x] 定案命令中每個參數的可用值域均有實測紀錄(§3 值域實測表)
- [x] B 組 8 項各有採用/不採用結論
- [x] 定案旗標組合與三模型基準已交付 provider 計劃
- [x] git 與 `.env` 均不含任何憑證
