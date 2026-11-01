# FEAT — 經 SSH 手動部署到正式機（`deploy_remote.sh`），不經 GitHub Actions

| 欄位 | 內容 |
|---|---|
| **創建日期** | 2026-10-20 |
| **性質** | 部署工具 + 使用說明 |
| **狀態** | ✅ 完成（2026-10-20，首次部署前端 `dac7670` 驗證通過；回滾與部署後端尚未實際演練，見 §8） |
| **範圍** | 新增 repo 根目錄的 `deploy_remote.sh`；恢復 `deploy_local.sh`（原封不動，2026-10-20 曾在 `efded5f` 移除） |
| **不動** | `.github/workflows/main.yml` 與 CI/CD 流程；Portainer Git stack 的設定；正式機上的 `.env` |
| **PR / 進度** | 尚未 commit |
| **關聯文件** | `docs/wip/portainer_gitops_deploy_FEAT_2026-10-20.md`（正式站平常的部署方式：CI → GHCR → Portainer） |

---

## 1. 用途

正式站平常的部署方式是：push 到 `main` → GitHub Actions 檢查、建映像、推 GHCR → 呼叫 Portainer 的 stack webhook 重建容器。

有些時候想跳過這條路：

- 想在正式站看**還沒合併**的分支效果（CI 只部署 `main`，分支手動觸發也只跑檢查、不部署）。
- 不想等 CI（後端映像沒快取時要 11–12 分鐘）。
- CI、GitHub 或 GHCR 出問題時的備用部署。

`deploy_remote.sh` 在**開發機**上執行：把程式碼差分上傳到正式機，在正式機建映像，只重建指定的容器，並確認新版本真的上線。

## 2. 與 CI 部署的差異

| 項目 | CI（push 到 `main`） | `deploy_remote.sh` |
|---|---|---|
| 部署前檢查 | 先跑後端 ruff＋pytest、前端 lint＋tsc＋build，失敗就不部署 | **不跑任何檢查**，要自己先在本地跑（§4.1） |
| 部署哪些 | 依變更路徑自動判斷前端、後端 | 自己指定 `frontend`／`backend`／`all` |
| 建映像的地方 | GitHub runner，amd64＋arm64 | 正式機本機，只建它的架構（amd64） |
| 映像存放 | 推到 GHCR（`:latest`、`:<sha>`） | 只存在正式機本地，不推 GHCR |
| 重建容器 | Portainer stack webhook（Portainer 拉映像、依 stack 設定重建） | 腳本自己 `docker compose up --force-recreate` |
| compose 與設定 | Portainer stack 追蹤的 `main` 上的 compose；前端金鑰來自 Portainer 環境變數 | 部署的那個 commit 裡的 compose；前端金鑰從現有容器複製（同一個值） |
| 確認上線 | 經 Cloudflare 公開網址輪詢版本端點 | 在正式機上打 localhost 的版本端點，同樣比對 commit |
| 回滾 | 沒有，要 revert 再部署 | 自動留 `pre-<舊 commit>` 回滾點，一行指令回滾 |
| 能部署的版本 | 只有 `main` | 任何分支、commit，或未 commit 的修改 |

**跑起來的容器等同於 CI 部署的**：同一份 Dockerfile、同樣的 `GIT_SHA`、同一個後端 `.env`、同樣的前端金鑰與網路。

**兩點要記得：**

1. **它繞過 Portainer。** Portainer stack 記得的仍是 `main`。下一次 CI 部署 `main`，或在 Portainer 按「Pull and redeploy」，都會從 GHCR 拉 `main` 的映像，**覆蓋**掉腳本部署的版本。所以分支的修改要長期留在正式站，最後還是要合併進 `main`。
2. **少了品質關卡。** 部署前一定要先在本地跑檢查（§4.1）。

## 3. 前提

- 在 Git Bash（或任何 bash）裡執行，本機要有 `git`、`ssh`。
- 能免密碼 SSH 到正式機（預設 `jacky917@192.168.50.172`），而且該使用者能直接執行 `docker`。
- 正式機上要有 `git`、`docker compose`、`curl`、`flock`（目前都有）。
- 第一次執行會在正式機的 `~/fluencytides-deploy/src` 建一個 git repo，之後沿用。

## 4. 使用方式

在 repo 裡任何目錄執行都可以。

### 4.1 部署前先在本地跑檢查（取代 CI 的檢查）

```bash
cd backend && python -m ruff check app scripts tests && python -m pytest tests -q
```

```bash
cd frontend && npm run lint && npm test && npm run build
```

### 4.2 部署

只部署前端，版本為目前的 HEAD（工作區必須是乾淨的）：

```bash
./deploy_remote.sh frontend
```

前後端都部署：

```bash
./deploy_remote.sh all
```

部署指定的分支、commit 或標籤（工作區有其他未 commit 的檔案時也能用）：

```bash
./deploy_remote.sh frontend --ref feat/handlers-api-redesign
```

連同還沒 commit 的修改一起部署（把整個工作區，含未追蹤、未被 `.gitignore` 排除的檔案，打包成一個暫時的 commit；不會動到你的分支與暫存區）：

```bash
./deploy_remote.sh frontend --allow-dirty
```

部署後順便清掉沒有標籤的 dangling 映像：

```bash
./deploy_remote.sh all --prune
```

### 4.3 回滾

回滾到最新的 `pre-*` 回滾點（即上一次部署前的版本）：

```bash
./deploy_remote.sh rollback frontend
```

回滾到指定的回滾點：

```bash
./deploy_remote.sh rollback frontend pre-b10012b
```

列出正式機上有哪些回滾點：

```bash
ssh jacky917@192.168.50.172 'docker images ghcr.io/jacky917/fluencytides-frontend --format "{{.Tag}}\t{{.CreatedSince}}"'
```

### 4.4 說明與設定

```bash
./deploy_remote.sh --help
```

| 環境變數 | 預設 | 用途 |
|---|---|---|
| `DEPLOY_HOST` | `jacky917@192.168.50.172` | SSH 目標 |
| `REMOTE_DIR` | 登入使用者的 `~/fluencytides-deploy` | 正式機上放原始碼與鎖檔的目錄 |
| `REMOTE_DATA` | `/DATA/AppData/FluencyTides` | 讀後端 `.env` 的資料目錄 |

例如部署到另一台主機：

```bash
DEPLOY_HOST=user@other-host ./deploy_remote.sh frontend
```

## 5. 流程

1. **決定版本**：HEAD、`--ref`，或 `--allow-dirty` 的工作區快照。
2. **差分上傳**：`git push` 到正式機的 `~/fluencytides-deploy/src`（ref `refs/deploy/current`）。git 只傳正式機沒有的物件；repo 壓縮後約 4 MB，第一次之後每次通常只傳幾 KB。
3. **在正式機上**（有鎖檔，同一時間只允許一個部署）：
   1. checkout 該 commit，清掉未追蹤的檔案。
   2. 對每個目標：
      - 把**目前容器正在用的映像**標成 `pre-<舊 commit 前 7 碼>`（查不到舊版本時用時間戳）。
      - `docker build`，`GIT_SHA` 為這次的 commit，標上 `:latest` 與 `:<sha>`；沿用正式機的建置快取。
      - `docker compose up -d --no-deps --force-recreate --pull never`，只重建這個容器。
      - 輪詢 `http://127.0.0.1:8080/version.json`（前端）或 `http://127.0.0.1:8000/api/health`（後端），直到回報的 `git_sha` 等於這次的 commit；120 秒內沒成功就印出容器 log 與回滾指令，並以失敗結束。
   3. `all` 時先後端、再前端。
4. **回滾**：把指定（或最新）的 `pre-*` 標籤改回 `:latest`，重建容器，確認端點有回應。

### 設定與密鑰

- **後端**：compose 的 `env_file` 直接讀正式機上的 `/DATA/AppData/FluencyTides/backend/.env`，與 Portainer 部署相同。
- **前端**：`API_SECRET_KEY` 平常由 Portainer 注入（nginx 轉發 API 時帶上）。腳本重建前先從目前的前端容器讀出，讀不到時改讀後端 `.env`，寫進正式機上權限 600 的暫存檔，用完即刪；不會顯示，也不會傳回開發機。少了它，所有 API 都會回 401。

## 6. 為什麼不直接用 `deploy_local.sh`

`deploy_local.sh`（在正式機上執行的舊腳本）保留不動，但不適合拿來做這件事：

1. 一定前後端一起重建，不能只部署一邊。
2. 直接執行前端的 compose 時不會帶 Portainer 注入的 `API_SECRET_KEY`，nginx 轉發不帶金鑰，API 全部回 401。
3. 在正式機上執行，需要正式機上已經有程式碼，本身不負責上傳。
4. 清理映像時用 `read -p` 等待回答，非互動執行時會直接中斷（`docs/bk/15_Bug_Scan_Report.md` 的 S039）。

## 7. 疑難排解

| 狀況 | 原因與處理 |
|---|---|
| `無法以 SSH 連線到 …` | 檢查本機能否 `ssh jacky917@192.168.50.172`；正式機記憶體耗盡時 SSH 會卡在交換 banner（2026-10-20 發生過） |
| `工作區有未 commit 的修改` | 先 commit；或改用 `--ref` 部署已 commit 的版本；或加 `--allow-dirty` 連同修改一起部署 |
| `另一個部署正在進行中` | 等前一個部署結束；確定沒有其他部署時，刪掉正式機上的 `~/fluencytides-deploy/.deploy.lock` 再試 |
| `… 在 120 秒內沒有回報預期的版本` | 腳本已印出容器 log；修正後重新部署，或先 `./deploy_remote.sh rollback <目標>` |
| 網頁能開但 API 都 401 | 前端沒帶到 `API_SECRET_KEY`：確認後端 `.env` 有這個值，再重新部署前端 |
| `mkdir: … Permission denied` | `REMOTE_DIR` 指到登入使用者不能寫的目錄（例如 `/DATA/AppData` 屬於 root）；用預設值即可 |

## 8. 驗收（2026-10-20）

部署 `feat/handlers-api-redesign` 的 `dac7670`（三頁版面統一）到正式站前端：

```bash
./deploy_remote.sh frontend --ref HEAD
```

- [x] 差分上傳、建置、重建、確認上線一次完成，共 5 秒（映像沿用正式機的建置快取）
- [x] 部署前自動留下回滾點 `pre-b10012b`，指向部署前容器正在用的映像
- [x] 正式機上 `version.json` 回報 `dac7670`，腳本的上線確認比對成功
- [x] 經前端 nginx 轉發的 `GET /api/v1/handlers/decks` 回 200（`API_SECRET_KEY` 有帶到）；`/graph` 回 200
- [x] 只重建前端容器，後端容器未被重建
- [x] `deploy_local.sh` 與 `efded5f` 刪除前的版本逐位元組相同
- [ ] 回滾指令實際執行（尚未在正式站演練；需要時用 §4.3 的指令）
- [ ] 部署後端（`./deploy_remote.sh backend`）實際執行（這次只部署前端）

開發過程的更正：原本把原始碼放在 `/DATA/AppData/FluencyTides/src`，但該目錄屬於 root，登入使用者無法寫入；改放登入使用者的家目錄（`REMOTE_DIR`），`/DATA/...` 只用來讀後端 `.env`。
