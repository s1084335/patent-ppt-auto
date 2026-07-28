#!/usr/bin/env bash
# 一鍵部署 backend＋worker 容器（Lightning／任何有 docker 的機器）。
#
# 為什麼要有這支：`docker run` 的參數漏一個就會出事，而且全都是**靜默**的——
#   - 漏 `-v patent-data:/app/data`   → 分群 artifact 隨舊容器消失，DB 仍記著路徑，
#                                       增量分群一律 FileNotFoundError；模型每次重下載
#   - 漏 `-v patent-output:/app/output` → ai:narrative 讀不到報表，解讀產不出來
#   - 漏 `-e PGOPTIONS`（含 extensions）→ pgvector 型別找不到，embeddings 全掛
#   - 漏 `-e DATABASE_URL`              → 靜默 fallback 到 localhost:5433，所有端點 500
# 2026-07-27～28 這四種各踩過至少一次，全部表象都像「程式壞掉」。
# Dockerfile 的 VOLUME 宣告救不了：那只產生匿名 volume，每次 docker run 都是新的。
#
# 用法：
#   export DB_URL='postgresql://...supabase.com:5432/postgres?sslmode=require'
#   bash scripts/deploy_containers.sh                    # pull + build + 起兩個容器
#   bash scripts/deploy_containers.sh --no-pull          # 不動 git，只重建容器
#   bash scripts/deploy_containers.sh --branch master    # 指定分支
#
# DB_URL 未設時，會自動從專案根目錄 .env 讀 DATABASE_URL。

set -euo pipefail

IMAGE="patent-ppt:latest"
# 預設 master（2026-07-28 起）。原本寫死開發分支 fix/clustering-incremental-hash-2026-07-27，
# 該分支合併進 master 並刪除後，這裡沒同步改 → Lightning 上跑腳本得到
# `fatal: couldn't find remote ref ...`，看起來像 git 壞掉，實際是預設值過期。
# ⚠ 分支名寫死在部署腳本裡就會有這個問題；要部署別的分支請用 --branch 傳。
BRANCH="${BRANCH:-master}"
DO_PULL=1
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [ $# -gt 0 ]; do
    case "$1" in
        --no-pull) DO_PULL=0; shift ;;
        --branch)  BRANCH="$2"; shift 2 ;;
        *) echo "未知參數：$1" >&2; exit 2 ;;
    esac
done

cd "$PROJECT_ROOT"

# ── 1. 環境參數：DB_URL 沒設就從 .env 撈，不要求使用者每次手貼 ──
if [ -z "${DB_URL:-}" ]; then
    if [ -f .env ]; then
        DB_URL="$(grep -E '^DATABASE_URL=' .env | head -1 | cut -d= -f2-)"
    fi
fi
if [ -z "${DB_URL:-}" ]; then
    echo "✗ DB_URL 未設定，且 .env 內找不到 DATABASE_URL" >&2
    echo "  請先 export DB_URL='postgresql://...'" >&2
    exit 1
fi
# 靜默 fallback 是最難查的失敗，寧可在這裡就擋下來。
case "$DB_URL" in
    postgresql://*|postgres://*) ;;
    *) echo "✗ DB_URL 格式不像連線字串：${DB_URL:0:30}..." >&2; exit 1 ;;
esac
case "$DB_URL" in
    *localhost*|*127.0.0.1*)
        echo "⚠ DB_URL 指向本機（${DB_URL:0:40}...）。容器內沒有這個服務，"
        echo "  所有 DB 端點會 500。確定要繼續嗎？[y/N]"
        read -r ans; [ "$ans" = "y" ] || exit 1 ;;
esac

# pgvector 裝在 Supabase 的 extensions schema，search_path 少了它 embeddings 全掛。
PGOPTIONS_VALUE='-c search_path=core_layer,raw_layer,public,extensions'

echo "== 設定 =="
echo "  分支     : $BRANCH$([ $DO_PULL -eq 0 ] && echo '（--no-pull，不動 git）')"
echo "  image    : $IMAGE"
echo "  DB       : $(echo "$DB_URL" | sed -E 's#://[^@]*@#://***@#')"
echo

# ── 2. pull ──
if [ $DO_PULL -eq 1 ]; then
    echo "== 1/4 取得最新程式碼 =="
    # --prune：清掉遠端已刪分支的本機引用。少了它，被刪的分支在本機仍看得到，
    # checkout 會「成功」但接著的 pull 才報 `couldn't find remote ref`，
    # 錯誤訊息離真正原因很遠（2026-07-28 實機踩過）。
    git fetch origin --prune

    # 目標分支在遠端不存在時直接停，訊息講清楚怎麼辦——不要讓 checkout／pull
    # 各自吐一段 git 原文，使用者得自己拼湊發生什麼事。
    if ! git rev-parse --verify --quiet "origin/$BRANCH" >/dev/null; then
        echo "  ✗ 遠端沒有分支 origin/$BRANCH"
        echo "    可能它已合併進 master 並被刪除。改用：bash $0 --branch master"
        echo "    遠端現有分支：$(git branch -r --format='%(refname:short)' | tr '\n' ' ')"
        exit 1
    fi

    # 本機有未推的 commit 時先擋下，避免 checkout 走掉後找不到
    ahead=$(git rev-list --count "origin/$(git rev-parse --abbrev-ref HEAD)..HEAD" 2>/dev/null || echo 0)
    if [ "$ahead" -gt 0 ]; then
        echo "  ✗ 目前分支有 $ahead 個未推送的 commit，先處理再部署："
        git log --oneline "-$ahead"
        echo "    要捨棄請自行 git reset --hard；本腳本不代為丟棄你的 commit。"
        exit 1
    fi

    git checkout "$BRANCH"
    git pull origin "$BRANCH"
    echo "  現在在：$(git log --oneline -1)"
    echo
fi

# ── 3. build ──
echo "== 2/4 重建 image =="
docker rm -f patent-backend patent-worker >/dev/null 2>&1 || true
docker build -t "$IMAGE" .
echo

# ── 4. 起容器：兩組 named volume，backend 與 worker 必須掛同一組 ──
# 兩者是不同容器、檔案系統不共享，共用 volume 才能讓 backend 讀到 worker 產的報表。
COMMON_ARGS=(
    -v patent-data:/app/data
    -v patent-output:/app/output
    -e DATABASE_URL="$DB_URL"
    -e PGOPTIONS="$PGOPTIONS_VALUE"
    --restart unless-stopped
)

echo "== 3/4 啟動容器 =="
docker run -d --name patent-backend \
    "${COMMON_ARGS[@]}" \
    -p 8000:8000 -e APP_ROLE=backend -e PORT=8000 \
    "$IMAGE" >/dev/null
echo "  patent-backend 已啟動"

docker run -d --name patent-worker \
    "${COMMON_ARGS[@]}" \
    -e APP_ROLE=worker \
    "$IMAGE" >/dev/null
echo "  patent-worker 已啟動"
echo

# ── 5. 自我檢查：把「靜默失敗」變成「當場看得到」 ──
echo "== 4/4 檢查 =="
sleep 6
fail=0

for name in patent-backend patent-worker; do
    status="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)"
    restarts="$(docker inspect -f '{{.RestartCount}}' "$name" 2>/dev/null || echo '?')"
    if [ "$status" = "running" ] && [ "$restarts" = "0" ]; then
        echo "  ✓ $name running"
    else
        echo "  ✗ $name status=$status restarts=$restarts"
        echo "    ---- 最後 15 行 log ----"
        docker logs --tail 15 "$name" 2>&1 | sed 's/^/    /'
        fail=1
    fi
    # volume 必須是具名的那兩個，不能是匿名 volume（匿名＝重建就丟）
    for v in patent-data patent-output; do
        if docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$name" 2>/dev/null | grep -qw "$v"; then
            echo "  ✓ $name 掛載 $v"
        else
            echo "  ✗ $name 沒掛 $v —— 資料會在下次重建時消失"
            fail=1
        fi
    done
done

# API 實際回應（容器 running 不代表 DB 通）
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 localhost:8000/api/v1/health 2>/dev/null || echo 000)"
if [ "$code" = "200" ]; then
    echo "  ✓ API health 200"
else
    echo "  ✗ API health HTTP $code（DB 連線多半有問題，看 backend log）"
    fail=1
fi

echo
if [ $fail -eq 0 ]; then
    echo "✅ 部署完成，全部檢查通過。前端：localhost:8000"
else
    echo "❌ 有檢查未通過，見上方 ✗ 項目。"
    exit 1
fi
