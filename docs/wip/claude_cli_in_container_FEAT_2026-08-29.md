# FEAT — 容器版後端支援 claude-code provider(CLI 進映像 + token 認證)

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-08-29 |
| **性質** | 新增機能設計 + 實作工作項 |
| **範圍** | `backend/Dockerfile`、`backend/docker-compose.yml`、`app/core/config.py`、`app/infrastructure/llm/claude_code_client.py`、`backend/.env.example`、對應單元測試 |
| **不動** | factory 選型邏輯與桌機模式行為(token 未設定時 `_build_env` 行為一絲不變)、生成腳本、NAS 上的實際部署操作(記錄於 §5 供人工執行) |
| **狀態** | 🚧 程式碼完成(2026-08-29);待映像重建與 NAS 部署(§5) |
| **PR / 進度** | [#14](https://github.com/jacky917/FluencyTides/pull/14)(程式碼全量,待合併與部署) |
| **關聯文件** | `docs/archive/claude_code_llm_provider_FEAT_2026-08-27.md`(provider 原始設計)、`docs/archive/claude_cli_env_setup_FEAT_2026-08-27.md` |

---

## 1. 問題與動機

2026-08-28 生成大翻車調查(均**已定位**):NAS(192.168.50.172,CasaOS)上的容器版後端
實際以 `LLM_PROVIDER=google` + gemini-3.1-pro-preview 運行,單日 250 次配額耗盡後連環 429;
且本機腳本按本機 .env 錯標了 190 筆 DB 紀錄(已 SQL 更正)。根因兩層:

1. 容器讀的是 `/DATA/AppData/FluencyTides/backend/.env`(compose `env_file`),與 repo 的
   `.env` 是兩份檔案;`env_file` 只在容器**建立**時注入。
2. 就算把那份 .env 改成 claude-code 也**跑不起來**:
   - 映像(`python:3.13-slim`)內沒有 claude CLI。
   - `claude_code_client._build_env()`(`claude_code_client.py:360-372`)會**剔除**
     `CLAUDE_CODE_OAUTH_TOKEN`——這在桌機是正確防禦(環境殘留壞 token 優先級高於
     落盤憑證,會蓋掉有效登入造成 401),但容器沒有落盤憑證,env token 是唯一可行的
     認證方式,被剔除等於自斷後路。

## 2. 目標與非目標

**目標**
- G1 映像內建 claude CLI(native 安裝,免 Node),`_resolve_cli_path` 的既有
  `shutil.which("claude")` fallback 直接命中,零程式改動。
- G2 新設定 `LLM_CLAUDE_CODE_OAUTH_TOKEN`:有值時 `_build_env` 注入該 token
  (仍剔除 ANTHROPIC_* 衛生變數);未設定時行為與現狀完全相同(桌機零影響)。
- G3 `.env.example` 與 compose 註記完整交代 token 產生方式(`claude setup-token`)、
  安全注意事項與 NAS 端部署步驟。

**非目標**
- 不在 CI 自動重建映像(CI 目前被停用;重建步驟記錄於 §5 由人工執行)。
- 不做 token 自動輪替/過期偵測(CLI 401 時的錯誤訊息已足夠診斷)。
- 不改 factory/main 的選型與啟動容錯邏輯。

## 3. 設計決策

- **D1 CLI 安裝方式**:官方 native installer(`curl -fsSL https://claude.ai/install.sh
  | bash -s -- <version>`),以 build arg `CLAUDE_CODE_VERSION`(預設 latest,部署時建議
  釘版本)控制。以 `apiuser` 身分安裝到 `~/.local/bin`,`ENV PATH` 補上該目錄——
  與既有非 root 安全設計相容,且 `_resolve_cli_path` 的 PATH 探測天然命中。
  **放棄** root 安裝後搬 binary:installer 產生的目錄結構含版本化路徑,搬動易碎。
- **D2 token 注入語意**:`LLM_CLAUDE_CODE_OAUTH_TOKEN` 空值(預設)= 現行剔除行為;
  非空 = 將值寫入 subprocess env 的 `CLAUDE_CODE_OAUTH_TOKEN`。單一開關同時服務
  桌機(不設)與容器(設)兩場景,不引入「執行環境偵測」這類隱式分支。
- **D3 token 安全**:token 等同訂閱憑證,只存在 NAS 的 `/DATA/.../.env`
  (chmod 600、NAS 不對外);`.env.example` 中明文警告。不放 compose、不進 git。
- **D4 CLI 狀態目錄**:token 認證是無狀態的,`~/.claude` 不掛 volume(容器重建即重置,
  無需持久);若日後觀察到每次冷啟的 onboarding 開銷,再補掛載。

## 4. 改動清單

| 檔案 | 改動 |
|---|---|
| `backend/Dockerfile` | 尾段以 apiuser 安裝 claude CLI(build arg 釘版),PATH 補 `~/.local/bin` |
| `backend/docker-compose.yml` | 註解:claude-code 模式所需的 env 變數清單與 token 警告 |
| `app/core/config.py` | 新增 `LLM_CLAUDE_CODE_OAUTH_TOKEN`(預設空) |
| `app/infrastructure/llm/claude_code_client.py` | `_build_env` 依 D2 分流;檔頭註解同步 |
| `backend/.env.example` | 新變數說明(setup-token 流程、安全警告、容器 vs 桌機語意) |
| `backend/tests/test_claude_code_client.py` | `_build_env` 三態測試:未設定剔除/設定注入/ANTHROPIC_* 恆剔除 |

## 5. NAS 端部署步驟(人工,程式碼合併後執行)

```bash
# 1. 本機(已登入 claude)產生長效 token,複製輸出
claude setup-token
```

```bash
# 2. 重建並推送映像(CI 已停用,擇一:本機 build+push,或暫時 enable workflow)
docker build -t ghcr.io/jacky917/fluencytides-backend:latest backend/
docker push ghcr.io/jacky917/fluencytides-backend:latest
```

```bash
# 3. NAS 上編輯 /DATA/AppData/FluencyTides/backend/.env:
#    LLM_PROVIDER=claude-code
#    LLM_MODEL_NAME=claude-opus-5
#    LLM_CLAUDE_CODE_EFFORT=medium
#    LLM_CLAUDE_CODE_OAUTH_TOKEN=<步驟1的輸出>
#    然後強制重建容器(env_file 只在建立時注入,restart 不夠):
docker compose pull && docker compose up -d --force-recreate
```

```bash
# 4. 驗證:容器內 CLI 可用 + 執行期環境正確
docker exec fluencytides-backend claude --version
docker exec fluencytides-backend printenv LLM_PROVIDER
# 後端啟動 log 應出現「LLM Provider = claude-code」與 ClaudeCodeLLMClient 初始化成功
```

## 6. 風險與未知

- **installer 在 slim 映像的依賴**:native installer 需 curl(已有)與基本 glibc;
  若 slim 缺 lib 於 build 時即失敗,能立刻發現。P0 以本機 docker build 驗證
  (本機無 docker 時以 PR 後的人工 build 為驗證點,Dockerfile 保持可讀可改)。
- **token 洩漏面**:D3 已述;此外 audit log(掛載到 NAS)不含 token(_build_env 不落盤)。
- **NAS 資源**:每請求一個 CLI 程序(數百 MB);與未來的併發改造疊加時需留意,
  容器可在 compose 加 memory limit 兜底。
- **回退**:NAS .env 改回 `LLM_PROVIDER=google` + force-recreate 即回到現狀;
  映像內多出的 CLI 對 google 模式零影響(惰性 import,不會被載入)。

## 7. 驗收標準

- [ ] 未設定 token 時,`_build_env` 輸出與改動前完全一致(單元測試)。
- [ ] 設定 token 時,subprocess env 含正確的 `CLAUDE_CODE_OAUTH_TOKEN` 且 ANTHROPIC_* 仍被剔除(單元測試)。
- [ ] 映像 build 成功,容器內 `claude --version` 可執行。
- [ ] NAS 實機:claude-code 模式下成功生成一張卡,Anki tag 為 `(claude-code)opus-5@<effort>` 系列。
- [ ] 桌機模式(本機腳本 + 本機後端)回歸不變。
