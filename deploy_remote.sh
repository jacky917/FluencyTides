#!/usr/bin/env bash

# =========================================================
# 用途：從開發機經 SSH 把程式碼差分上傳到正式機，在正式機建映像並重建容器，不經 GitHub Actions。
# Purpose: Upload code to the production host over SSH (only the git
#          objects it lacks), build the images there and recreate the
#          containers, without GitHub Actions.
# =========================================================
# 用法 / Usage（在 repo 根目錄或任何子目錄執行皆可）：
#   ./deploy_remote.sh [frontend|backend|all] [選項]
#   ./deploy_remote.sh rollback <frontend|backend> [映像標籤]
#
# 選項 / Options:
#   --ref <git ref>   部署指定的 commit／分支／標籤（預設：目前的 HEAD）
#   --allow-dirty     工作區有未 commit 的修改時，把整個工作區（含未追蹤、未被 .gitignore
#                     排除的檔案）打包成一個暫時的 commit 一起部署；不會動到你的分支與暫存區
#   --prune           部署後清掉沒有標籤的 dangling 映像（docker image prune -f）
#   -h, --help        顯示說明
#
# 環境變數 / Environment:
#   DEPLOY_HOST   SSH 目標（預設 jacky917@192.168.50.172）
#   REMOTE_DIR    正式機上放原始碼與鎖檔的目錄（預設登入使用者的 ~/fluencytides-deploy；
#                 /DATA/AppData 屬於 root，一般使用者不能寫）
#   REMOTE_DATA   正式機上的資料目錄，讀後端 .env 用（預設 /DATA/AppData/FluencyTides）
#
# 流程 / Flow:
#   1. 決定要部署的 commit（HEAD、--ref，或 --allow-dirty 的工作區快照）
#   2. git push 到正式機的 $REMOTE_DIR/src（git 只傳對方沒有的物件，即差分上傳）
#   3. 正式機 checkout 該 commit，為每個目標：
#      a. 把「目前容器正在用的映像」打上 pre-<舊 commit> 標籤，作為回滾點
#      b. docker build（GIT_SHA=要部署的 commit；沿用主機上的建置快取）
#      c. docker compose up --force-recreate 只重建這個容器
#      d. 輪詢 /version.json（前端）或 /api/health（後端），確認回報的 git_sha 就是這次的 commit
#   4. 回滾：rollback 會把指定（預設最新）的 pre-* 標籤改回 :latest 並重建容器
#
# 注意 / Notes:
#   - 正式站平常由 Portainer Git stack 管理（CI 部署 main 時會從 GHCR 拉 :latest 重建）。
#     用這支腳本部署後，下一次 CI 部署 main 會覆蓋回 main 的版本。
#   - 前端的 API_SECRET_KEY 由 Portainer 注入；重建前會從目前的前端容器讀出（讀不到時改讀
#     後端的 .env），只寫入正式機上權限 600 的暫存檔，用完即刪，不會顯示或傳回開發機。
#   - 後端的設定一律讀正式機上的 $REMOTE_DATA/backend/.env（與 docker-compose.yml 相同）。
#   - 原本在正式機上執行的 deploy_local.sh 保留不動，兩者互不依賴。
# =========================================================

set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-jacky917@192.168.50.172}"
REMOTE_DIR="${REMOTE_DIR:-}"
REMOTE_DATA="${REMOTE_DATA:-/DATA/AppData/FluencyTides}"
IMAGE_PREFIX="ghcr.io/jacky917/fluencytides"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=30 -o ServerAliveInterval=30)

usage() {
    # 印出檔頭的說明註解 / Print the header comment
    sed -n '/^# 用法/,/^set -euo pipefail/p' "$0" | sed -n 's/^# \{0,1\}//p' | grep -v '^=*$'
}

die() {
    echo "❌ $*" >&2
    exit 1
}

ssh_run() {
    ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "$@"
}

# ---------------------------------------------------------
# 解析參數 / Parse arguments
# ---------------------------------------------------------
COMMAND="deploy"
TARGET="all"
REF=""
ALLOW_DIRTY=0
PRUNE=0
ROLLBACK_TAG=""

if [ "${1:-}" = "rollback" ]; then
    COMMAND="rollback"
    shift
    TARGET="${1:-}"
    [ "$TARGET" = "frontend" ] || [ "$TARGET" = "backend" ] || die "rollback 要指定 frontend 或 backend"
    shift
    ROLLBACK_TAG="${1:-}"
else
    while [ $# -gt 0 ]; do
        case "$1" in
            frontend|backend|all) TARGET="$1" ;;
            --ref) shift; REF="${1:-}"; [ -n "$REF" ] || die "--ref 需要一個 git ref" ;;
            --allow-dirty) ALLOW_DIRTY=1 ;;
            --prune) PRUNE=1 ;;
            -h|--help) usage; exit 0 ;;
            *) die "不認得的參數：$1（用 --help 看說明）" ;;
        esac
        shift
    done
fi

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || die "請在 FluencyTides 的 git repo 裡執行"
START=$SECONDS

# 預設放在登入使用者的家目錄 / Default to the login user's home directory
if [ -z "$REMOTE_DIR" ]; then
    REMOTE_DIR="$(ssh_run 'printf %s "$HOME"')/fluencytides-deploy" || die "無法以 SSH 連線到 $DEPLOY_HOST"
fi

# ---------------------------------------------------------
# 1. 決定要部署的 commit / Resolve the commit to deploy
# ---------------------------------------------------------
# 把整個工作區（含未追蹤、未被忽略的檔案）寫成一個暫時的 commit：用獨立的暫存區檔案，
# 不影響使用者目前的暫存區與分支。
# Snapshot the whole working tree into a commit using a separate index file.
snapshot_commit() {
    local index tree
    index="$(git rev-parse --git-dir)/deploy-snapshot-index"
    rm -f "$index"
    GIT_INDEX_FILE="$index" git read-tree HEAD
    GIT_INDEX_FILE="$index" git add -A
    tree=$(GIT_INDEX_FILE="$index" git write-tree)
    rm -f "$index"
    git commit-tree "$tree" -p HEAD -m "deploy snapshot of working tree on $(git rev-parse --short HEAD)"
}

if [ "$COMMAND" = "deploy" ]; then
    if [ -n "$REF" ]; then
        SHA=$(git rev-parse --verify --quiet "$REF^{commit}") || die "找不到 git ref：$REF"
        DESCRIPTION="$REF"
    elif [ -n "$(git status --porcelain)" ]; then
        if [ "$ALLOW_DIRTY" -ne 1 ]; then
            git status --short >&2
            die "工作區有未 commit 的修改。請先 commit，或加上 --allow-dirty 連同這些修改一起部署"
        fi
        SHA=$(snapshot_commit)
        DESCRIPTION="$(git rev-parse --abbrev-ref HEAD) + 未 commit 的修改"
    else
        SHA=$(git rev-parse HEAD)
        DESCRIPTION="$(git rev-parse --abbrev-ref HEAD)"
    fi

    echo "========================================================="
    echo " FluencyTides 遠端部署 → $DEPLOY_HOST"
    echo "   目標：$TARGET"
    echo "   版本：${SHA:0:7}（$DESCRIPTION）"
    echo "========================================================="

    # -----------------------------------------------------
    # 2. 差分上傳 / Upload only the missing git objects
    # -----------------------------------------------------
    echo "📤 上傳程式碼（git push，只傳正式機沒有的物件）..."
    ssh_run "mkdir -p '$REMOTE_DIR/src' && cd '$REMOTE_DIR/src' && { [ -d .git ] || git init --quiet; }"
    GIT_SSH_COMMAND="ssh ${SSH_OPTS[*]}" \
        git push --quiet --force "ssh://$DEPLOY_HOST$REMOTE_DIR/src" "$SHA:refs/deploy/current"
fi

# ---------------------------------------------------------
# 3. 在正式機上建置、重建容器、確認上線 / Build, recreate and verify on the host
# ---------------------------------------------------------
# ssh 會把參數接成一行字串，空字串會消失、讓後面的參數錯位，所以每個參數都先加上引號
# ssh joins arguments into one string; quote each so empty ones survive.
REMOTE_ARGS=$(printf ' %q' "$COMMAND" "$TARGET" "${SHA:-}" "$PRUNE" "$REMOTE_DIR" "$REMOTE_DATA" "$IMAGE_PREFIX" "$ROLLBACK_TAG")
ssh_run "bash -s --$REMOTE_ARGS" <<'REMOTE'
set -euo pipefail
COMMAND=$1 TARGET=$2 SHA=$3 PRUNE=$4 WORKDIR=$5 DATA=$6 PREFIX=$7 ROLLBACK_TAG=$8
SRC="$WORKDIR/src"

# 同一時間只允許一個部署 / One deployment at a time
mkdir -p "$WORKDIR"
exec 9>"$WORKDIR/.deploy.lock"
flock -n 9 || { echo "❌ 另一個部署正在進行中（$WORKDIR/.deploy.lock）" >&2; exit 1; }

endpoint() {
    case "$1" in
        frontend) echo "http://127.0.0.1:8080/version.json" ;;
        backend) echo "http://127.0.0.1:8000/api/health" ;;
    esac
}

# 容器目前回報的 commit；查不到時為空字串 / The commit the container reports
live_sha() {
    curl -fsS -m 5 "$(endpoint "$1")" 2>/dev/null | sed -n 's/.*"git_sha" *: *"\([^"]*\)".*/\1/p' || true
}

# 只重建這一個容器 / Recreate just this container
recreate() {
    local target=$1 envfile
    if [ "$target" = "frontend" ]; then
        # API_SECRET_KEY 由 Portainer 注入：從目前的容器讀出，讀不到時改讀後端 .env；不顯示
        envfile=$(mktemp)
        chmod 600 "$envfile"
        docker inspect fluencytides-frontend --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
            | grep '^API_SECRET_KEY=' > "$envfile" \
            || grep '^API_SECRET_KEY=' "$DATA/backend/.env" > "$envfile" \
            || echo "⚠️  找不到 API_SECRET_KEY，前端將不帶金鑰（後端也沒設定時才正常）" >&2
        docker compose --env-file "$envfile" -f "$SRC/frontend/docker-compose.yml" \
            up -d --no-deps --force-recreate --pull never 2>&1 | tail -3
        rm -f "$envfile"
    else
        docker compose -f "$SRC/backend/docker-compose.yml" \
            up -d --no-deps --force-recreate --pull never 2>&1 | tail -3
    fi
}

# 等容器回報預期的 commit（沒給時只要端點有回應）/ Wait until it reports the commit
wait_live() {
    local target=$1 expected=$2 got=""
    for _ in $(seq 1 60); do
        got=$(live_sha "$target")
        if [ -n "$got" ] && { [ -z "$expected" ] || [ "$got" = "$expected" ]; }; then
            echo "   ✅ $target 已上線（git_sha=${got:0:7}）"
            return 0
        fi
        sleep 2
    done
    echo "❌ $target 在 120 秒內沒有回報預期的版本（目前：${got:-無回應}，預期：${expected:-任一}）" >&2
    docker logs --tail 40 "fluencytides-$target" >&2 || true
    echo "   回滾：./deploy_remote.sh rollback $target" >&2
    return 1
}

# 把目前容器正在用的映像標成回滾點 / Tag the running image as a rollback point
tag_rollback_point() {
    local target=$1 image old tag
    image=$(docker inspect -f '{{.Image}}' "fluencytides-$target" 2>/dev/null) || return 0
    old=$(live_sha "$target")
    tag="pre-${old:0:7}"
    [ -n "$old" ] && [ "$old" != "unknown" ] || tag="pre-$(date +%Y%m%d-%H%M%S)"
    docker tag "$image" "$PREFIX-$target:$tag"
    echo "   🔖 回滾點：$PREFIX-$target:$tag"
}

deploy_one() {
    local target=$1
    echo "---------------------------------------------------------"
    echo "🚀 $target"
    tag_rollback_point "$target"
    echo "   🔨 建置映像..."
    docker build --quiet --build-arg GIT_SHA="$SHA" \
        -t "$PREFIX-$target:latest" -t "$PREFIX-$target:$SHA" "$SRC/$target" > /dev/null
    echo "   ♻️  重建容器..."
    recreate "$target"
    wait_live "$target" "$SHA"
}

if [ "$COMMAND" = "rollback" ]; then
    image="$PREFIX-$TARGET"
    tag=$ROLLBACK_TAG
    if [ -z "$tag" ]; then
        # 映像建立時間最新的 pre-* 標籤 / The newest pre-* tag by image creation time
        tag=$(docker images "$image" --format '{{.CreatedAt}}\t{{.Tag}}' | awk -F'\t' '$2 ~ /^pre-/' | sort -r | head -1 | cut -f2)
    fi
    [ -n "$tag" ] || { echo "❌ 找不到 $image 的 pre-* 回滾點" >&2; exit 1; }
    echo "⏪ 回滾 $TARGET → $image:$tag"
    docker image inspect "$image:$tag" > /dev/null
    docker tag "$image:$tag" "$image:latest"
    [ -f "$SRC/$TARGET/docker-compose.yml" ] || { echo "❌ $SRC 沒有 compose 檔，請先部署過一次" >&2; exit 1; }
    recreate "$TARGET"
    wait_live "$TARGET" ""
    exit 0
fi

# 取出要部署的版本 / Check out the uploaded commit
git -C "$SRC" checkout --quiet --force --detach "$SHA"
git -C "$SRC" clean --quiet -fdx

case "$TARGET" in
    all) deploy_one backend; deploy_one frontend ;;
    *) deploy_one "$TARGET" ;;
esac

if [ "$PRUNE" = "1" ]; then
    echo "🧹 清除 dangling 映像..."
    docker image prune -f | tail -1
fi
REMOTE

echo "========================================================="
echo " ✅ 完成（耗時 $((SECONDS - START)) 秒）"
echo "    前端：https://ft.jacky917.com"
echo "========================================================="
