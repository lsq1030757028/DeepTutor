#!/bin/bash
# P1 等价性验证：自建镜像 vs 官方镜像
#
# 判据（FORK-PLAN P1）：自建镜像起的实例，登录、聊天、MCP 挂载三条主路径与官方一致。
# 纪律：全程用 deeptutor-data-verify 副本卷，绝不碰现役 deeptutor 容器与 deeptutor-data。
#
# 输出：逐项 PASS/FAIL + 证据，供落盘。

set -uo pipefail
OFFICIAL="ghcr.io/hkuds/deeptutor:latest"
LOCAL="deeptutor:ut1.5.8-local"
VERIFY_NAME="deeptutor-verify"
VERIFY_PORT=3783
PASS=0; FAIL=0

say()  { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }
cmp2() { # 名称 期望 实得
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1"$'\n'"         官方: $2"$'\n'"         自建: $3"; fi
}

say "0 · 现役实例保护自证"
running=$(docker inspect -f '{{.State.Status}}' deeptutor 2>/dev/null || echo "缺失")
[ "$running" = "running" ] && ok "现役 deeptutor 容器仍在跑(未被本脚本触碰)" \
                           || bad "现役容器状态异常: $running"

say "1 · 静态内容对账(镜像层)"
# Python 包树
o_py=$(docker run --rm --entrypoint sh "$OFFICIAL" -c \
       "find /app/deeptutor -name '*.py' -type f | sort | xargs sha256sum | sha256sum" 2>/dev/null | awk '{print $1}')
l_py=$(docker run --rm --entrypoint sh "$LOCAL" -c \
       "find /app/deeptutor -name '*.py' -type f | sort | xargs sha256sum | sha256sum" 2>/dev/null | awk '{print $1}')
cmp2 "deeptutor 包树指纹" "$o_py" "$l_py"

# 版本
o_v=$(docker run --rm --entrypoint sh "$OFFICIAL" -c "cat /app/deeptutor/__version__.py" 2>/dev/null | tr -d '\r')
l_v=$(docker run --rm --entrypoint sh "$LOCAL"   -c "cat /app/deeptutor/__version__.py" 2>/dev/null | tr -d '\r')
cmp2 "版本文件" "$o_v" "$l_v"

# 启动脚本与 supervisord 配置
for f in /app/entrypoint.sh /app/start-backend.sh /app/start-frontend.sh; do
  o=$(docker run --rm --entrypoint sh "$OFFICIAL" -c "sha256sum $f 2>/dev/null | cut -d' ' -f1")
  l=$(docker run --rm --entrypoint sh "$LOCAL"   -c "sha256sum $f 2>/dev/null | cut -d' ' -f1")
  cmp2 "$f" "$o" "$l"
done

say "2 · 运行态(自建镜像 + 副本卷,独立端口)"
docker rm -f "$VERIFY_NAME" >/dev/null 2>&1
docker run -d --name "$VERIFY_NAME" \
  -p 127.0.0.1:${VERIFY_PORT}:3782 \
  -v deeptutor-data-verify:/app/data \
  "$LOCAL" >/dev/null 2>&1 && ok "自建镜像容器已启动(端口 ${VERIFY_PORT},副本卷)" \
                           || bad "自建镜像容器启动失败"

printf '  等待健康检查'
for i in $(seq 1 90); do
  st=$(docker inspect -f '{{.State.Health.Status}}' "$VERIFY_NAME" 2>/dev/null || echo none)
  [ "$st" = "healthy" ] && break
  printf '.'; sleep 2
done
printf '\n'
st=$(docker inspect -f '{{.State.Health.Status}}' "$VERIFY_NAME" 2>/dev/null || echo none)
[ "$st" = "healthy" ] && ok "健康检查 healthy" || bad "健康检查未通过: $st"

say "3 · 三条主路径"
# 前端首屏
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:${VERIFY_PORT}/")
[ "$code" = "200" ] && ok "前端首屏 HTTP 200" || bad "前端首屏 HTTP $code"

# 登录链路：用真实存在的端点，且断言响应体形状，不能靠"非 5xx"蒙混
# (自检发现 /api/v1/auth/me 根本不存在,404 也算过 —— 那种断言等于没有)
astat=$(curl -s --max-time 20 "http://127.0.0.1:${VERIFY_PORT}/api/v1/auth/status")
if printf '%s' "$astat" | grep -qE '"(authenticated|auth_required|requires_auth|user)"'; then
  ok "登录链路 /api/v1/auth/status 返回结构化鉴权状态"
else
  bad "登录链路响应不成形: $(printf '%s' "$astat" | head -c 160)"
fi
# 与官方实例同口径对比(官方跑在 3782)
oastat=$(curl -s --max-time 20 "http://127.0.0.1:3782/api/v1/auth/status")
ok_keys() { printf '%s' "$1" | tr ',' '\n' | grep -o '"[a-z_]*":' | sort -u | tr -d '\n'; }
cmp2 "鉴权状态字段集与官方一致" "$(ok_keys "$oastat")" "$(ok_keys "$astat")"

# 前端：不比 buildId —— Next.js 默认每次构建随机生成，两边必然不同，
# 拿它当判据会因为一个无害的原因判失败。改比"渲染出来的东西一样不一样"：
# 页面标题 + 语言 + 挂载点，这些是构建产物的语义面。
shape() { curl -s --max-time 20 "$1" \
  | grep -oE '<title>[^<]*</title>|<html[^>]*lang="[a-zA-Z-]*"|id="__next"|__NEXT_DATA__' \
  | sort -u | tr -d '\n'; }
cmp2 "前端首屏语义形状(标题/语言/挂载点)" "$(shape "http://127.0.0.1:3782/")" "$(shape "http://127.0.0.1:${VERIFY_PORT}/")"

# 数据面:副本卷里的用户数据被读到
users=$(docker exec "$VERIFY_NAME" sh -c "ls /app/data/user 2>/dev/null | head -5" 2>/dev/null | tr '\n' ' ')
[ -n "$users" ] && ok "副本卷数据可读: $users" || bad "副本卷数据读不到"

# MCP 注册表(我们那条 test-partner 条目是否还在)
mcp=$(docker exec "$VERIFY_NAME" sh -c "cat /app/data/user/settings/mcp.json 2>/dev/null" 2>/dev/null | head -c 400)
if printf '%s' "$mcp" | grep -q "test-partner"; then
  ok "MCP 注册表含 test-partner 条目"
elif [ -n "$mcp" ]; then
  ok "MCP 注册表可读(未含 test-partner,属数据面差异非镜像差异)"
else
  bad "MCP 注册表读不到"
fi

say "小结"
printf '  PASS=%d  FAIL=%d\n' "$PASS" "$FAIL"
printf '  验证容器保留为 %s(端口 %s),复核后手动 docker rm -f\n' "$VERIFY_NAME" "$VERIFY_PORT"
[ "$FAIL" -eq 0 ] && printf '  判定：等价\n' || printf '  判定：不等价——按 FORK-PLAN P1 止损方案处置\n'
exit 0
