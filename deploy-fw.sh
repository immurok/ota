#!/bin/bash
#
# deploy-fw.sh — 固件发布一条龙：编译 → 生成 manifest+资产 → 更新官网 → 部署 → 校验
#
# 官网是 Cloudflare Pages（website/ 根目录即发布内容）。本脚本：
#   1. 调 release-web.sh 构建 release .imfw 并 stage 到 ota/web-dist/
#   2. 把英文发布说明写进 manifest.json（--notes）
#   3. 拷贝到 website/fw/（App 的 manifestURL 指向 https://immurok.com/fw/manifest.json）
#   4. wrangler pages deploy 发布整个站点
#   5. curl 校验线上 manifest 版本
#
# 用法：
#   ota/deploy-fw.sh --notes "Fix occasional unlock failure"
#   ota/deploy-fw.sh --no-build                 # 复用 firmware/build 现有 .imfw（不重新编译）
#   ota/deploy-fw.sh --dry-run                  # 只编译+stage+拷贝到 website/fw/，不真正部署
#   ota/deploy-fw.sh --ver 5 --notes "..."      # 覆盖硬件版本（默认 VER=6）
#
# 前置：wrangler 已登录（npx wrangler login），本地固件构建环境（TOOLCHAIN_PATH、
#       firmware/SDK/、ota_keys 就位）——仅在需要编译时。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
WEB_DIR="$PROJECT_DIR/website"
WEB_DIST="$SCRIPT_DIR/web-dist"
FW_PUB_DIR="$WEB_DIR/fw"
PAGES_PROJECT="immurok"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[FW-DEPLOY]${NC} $1"; }
warn() { echo -e "${YELLOW}[FW-DEPLOY]${NC} $1"; }
die()  { echo -e "${RED}[FW-DEPLOY] ERROR:${NC} $1" >&2; exit 1; }

# ── 参数解析 ──
NO_BUILD=0
DRY_RUN=0
VER="6"
NOTES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --no-build) NO_BUILD=1; shift ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --ver)      VER="${2:?--ver 需要一个值}"; shift 2 ;;
        --notes)    NOTES="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,30p' "$0"; exit 0 ;;
        *) die "未知参数 '$1'（-h 看用法）" ;;
    esac
done

command -v python3 >/dev/null || die "需要 python3（用于写入 manifest 发布说明）"
[ -d "$WEB_DIR" ] || die "官网目录不存在：$WEB_DIR"

# ── 1. 构建 + stage（复用 release-web.sh）──
if [ "$NO_BUILD" -eq 1 ]; then
    info "跳过编译，复用 firmware/build 现有 .imfw"
    "$SCRIPT_DIR/release-web.sh" --no-build
else
    info "编译 VER=$VER release 固件并生成 manifest ..."
    # release-web.sh 固定 VER=6；如需其它硬件版本，先单独构建再 --no-build
    if [ "$VER" != "6" ]; then
        info "VER=${VER}：先用 build-ota.sh 构建，再让 release-web.sh 复用产物"
        "$SCRIPT_DIR/build-ota.sh" "VER=$VER" release
        "$SCRIPT_DIR/release-web.sh" --no-build
    else
        "$SCRIPT_DIR/release-web.sh"
    fi
fi
[ -f "$WEB_DIST/manifest.json" ] || die "release-web.sh 未生成 $WEB_DIST/manifest.json"

# ── 2. 写入发布说明（英文）──
if [ -n "$NOTES" ]; then
    info "写入英文发布说明到 manifest.json"
    NOTES="$NOTES" python3 - "$WEB_DIST/manifest.json" <<'PY'
import json, os, sys
p = sys.argv[1]
m = json.load(open(p))
m["latest"]["notes"] = os.environ["NOTES"]
json.dump(m, open(p, "w"), ensure_ascii=False, indent=2)
print("  notes =", m["latest"]["notes"])
PY
else
    # 检查 manifest 里的 notes 是否为空，空则告警（不阻断，便于快速迭代）
    EMPTY=$(python3 - "$WEB_DIST/manifest.json" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print("1" if not (m["latest"].get("notes") or "").strip() else "0")
PY
)
    [ "$EMPTY" = "1" ] && warn "manifest 发布说明为空（App 更新弹窗将无说明）。可用 --notes 补上。"
fi

VER_STR=$(python3 -c "import json;print(json.load(open('$WEB_DIST/manifest.json'))['latest']['version'])")

# ── 3. 拷贝到 website/fw/ ──
mkdir -p "$FW_PUB_DIR"
cp "$WEB_DIST"/*.imfw "$FW_PUB_DIR"/
cp "$WEB_DIST"/manifest.json "$FW_PUB_DIR"/
info "已拷贝到 ${FW_PUB_DIR}："
ls -la "$FW_PUB_DIR" | sed 's/^/    /'

# 固件二进制正常入 git（随仓库走）：曾经把 website/fw/ gitignore 掉，结果只要
# 有人在没有本地 fw/ 的机器上跑一次普通的 `wrangler pages deploy .`（比如只是
# 改网页文案），Cloudflare Pages 的整站快照替换就会把线上 /fw/ 清空——2026-08-09
# 就这么把固件文件搞丢过一次。所以现在 fw/ 必须提交，提醒操作者别忘了。
if [ -n "$(git -C "$WEB_DIR" status --porcelain -- fw/ 2>/dev/null)" ]; then
    warn "website/fw/ 有未提交的改动，记得 git add website/fw/ && git commit，否则下次别人在没有这些文件的检出上部署网站，线上 /fw/ 会再次被清空"
fi

# manifest 缓存 5 分钟（发布后尽快生效），其余 /fw/ 资产按内容命名可长缓存
HEADERS="$WEB_DIR/_headers"
if ! { [ -f "$HEADERS" ] && grep -q '/fw/manifest.json' "$HEADERS"; }; then
    {
        echo ""
        echo "/fw/manifest.json"
        echo "  Cache-Control: public, max-age=300"
    } >> "$HEADERS"
    info "已在 website/_headers 加上 manifest 的 max-age=300"
fi

# ── 4. 部署 ──
if [ "$DRY_RUN" -eq 1 ]; then
    warn "--dry-run：已 stage 到 website/fw/，未部署。手动部署命令："
    echo "    cd $WEB_DIR && npx wrangler pages deploy . --project-name=$PAGES_PROJECT"
    exit 0
fi

info "部署官网到 Cloudflare Pages（project=${PAGES_PROJECT}）..."
( cd "$WEB_DIR" && npx wrangler pages deploy . --project-name="$PAGES_PROJECT" )

# ── 5. 线上校验 ──
info "校验线上 manifest（可能需几秒 CDN 生效）..."
sleep 3
ONLINE_VER=$(curl -fsS "https://immurok.com/fw/manifest.json" 2>/dev/null \
    | python3 -c "import json,sys;print(json.load(sys.stdin)['latest']['version'])" 2>/dev/null || echo "")
if [ "$ONLINE_VER" = "$VER_STR" ]; then
    info "线上 manifest 版本 = $ONLINE_VER ✓"
else
    warn "线上版本 '$ONLINE_VER' 与预期 '$VER_STR' 不一致（CDN 可能还在生效，稍后再 curl 确认）"
fi

info "完成。固件 $VER_STR 已发布："
echo "    https://immurok.com/fw/manifest.json"
echo "    https://immurok.com/fw/immurok-ik1-v$VER_STR.imfw"
echo "    https://immurok.com/fw/immurok-ik1-v1.6.0-bridge.imfw"
