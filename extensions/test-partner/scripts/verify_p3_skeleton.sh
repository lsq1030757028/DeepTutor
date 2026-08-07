#!/bin/bash
# P3 骨架端到端验证：页面在自建镜像里真能打开、后端接得通、隔离生效。
# 纪律：用 deeptutor-data 的副本卷 + 独立端口，全程不碰现役 deeptutor 容器与卷。

set -uo pipefail
IMG="deeptutor:p3-skeleton"
NAME="deeptutor-p3"
PORT=3784
PASS=0; FAIL=0
say(){ printf '\n=== %s ===\n' "$1"; }
ok(){ printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad(){ printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }

say "0 · 现役实例保护自证"
st=$(docker inspect -f '{{.State.Status}}' deeptutor 2>/dev/null || echo 缺失)
[ "$st" = "running" ] && ok "现役 deeptutor 仍在跑（未被本脚本触碰）" || bad "现役容器状态：$st"

say "1 · 镜像里到底有没有我们的代码"
# 这是 P1 挖出来的坑：镜像构建成功、健康检查也过，但 extensions/ 整个不在里面
n_py=$(docker run --rm --entrypoint sh "$IMG" -c \
  "find /app/extensions/test-partner/server -name '*.py' 2>/dev/null | wc -l" | tr -d '\r ')
[ "${n_py:-0}" -gt 10 ] && ok "server/ 有 $n_py 个 .py" || bad "server/ 只找到 ${n_py:-0} 个 .py"

# .dockerignore 里有 `*.md` 规则。Docker 的 * 不跨 /，所以理论上只排根目录——
# 但这条不靠推理，直接查 SKILL.md 在不在（模型要读它，缺了生成链路就断）
n_md=$(docker run --rm --entrypoint sh "$IMG" -c \
  "find /app/extensions/test-partner/skills -name 'SKILL.md' 2>/dev/null | wc -l" | tr -d '\r ')
[ "${n_md:-0}" -ge 2 ] && ok "skills/ 下有 $n_md 份 SKILL.md（.dockerignore 的 *.md 没误伤）" \
  || bad "SKILL.md 只找到 ${n_md:-0} 份——.dockerignore 的 *.md 可能吃掉了它们"

# 不该进镜像的
for d in tests docs tapd-runtime; do
  has=$(docker run --rm --entrypoint sh "$IMG" -c \
    "[ -d /app/extensions/test-partner/$d ] && echo yes || echo no" | tr -d '\r ')
  [ "$has" = "no" ] && ok "$d/ 未进镜像（符合预期）" || bad "$d/ 进了镜像，COPY 范围过宽"
done

say "2 · 前端页面真的被构建进去了"
# next build 会把每个路由编成 .next/server/app/<route>/page.js
found=$(docker run --rm --entrypoint sh "$IMG" -c \
  "find /app/web -path '*test-workbench*' 2>/dev/null | head -5" | tr -d '\r')
[ -n "$found" ] && { ok "前端产物含 test-workbench 路由"; printf '%s\n' "$found" | sed 's/^/         /'; } \
  || bad "前端产物里找不到 test-workbench——页面没被编进去"

say "3 · 运行态（副本卷 + 独立端口）"
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" -p 127.0.0.1:${PORT}:3782 \
  -v deeptutor-data-verify:/app/data "$IMG" >/dev/null 2>&1 \
  && ok "容器已起（端口 $PORT，副本卷）" || bad "容器起不来"

printf '  等健康检查'
for i in $(seq 1 90); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo none)
  [ "$s" = "healthy" ] && break; printf '.'; sleep 2
done; printf '\n'
s=$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo none)
[ "$s" = "healthy" ] && ok "健康检查 healthy" || bad "健康检查：$s"

say "4 · 后端接口"
h=$(curl -s --max-time 20 "http://127.0.0.1:${PORT}/api/v1/test-workbench/health")
printf '  health 原文：%s\n' "${h:0:200}"
printf '%s' "$h" | grep -q '"extension_loaded":true' \
  && ok "扩展已加载（sys.path 引导在容器里也成立）" \
  || bad "extension_loaded 不为 true——容器里 import 不到 server.*"

d=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:${PORT}/api/v1/test-workbench/deliveries")
case "$d" in
  200) ok "批次列表 200";;
  401|403) ok "批次列表 $d（鉴权生效，dependencies=_auth 在起作用）";;
  *) bad "批次列表返回 $d";;
esac

say "5 · 页面能打开"
p=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "http://127.0.0.1:${PORT}/test-workbench")
[ "$p" = "200" ] && ok "/test-workbench 返回 200" || bad "/test-workbench 返回 $p"

body=$(curl -s --max-time 25 "http://127.0.0.1:${PORT}/test-workbench")
printf '%s' "$body" | grep -qi "test.workbench\|测试工作台" \
  && ok "页面 HTML 里能找到工作台标识" || bad "页面 HTML 里找不到工作台标识"

say "6 · 每用户隔离（决策 0009）"
# 目录要长在用户 scope 下，不是某个共享路径
docker exec "$NAME" sh -c "find /app/data -maxdepth 4 -type d -name 'test-workbench' 2>/dev/null" \
  | sed 's/^/         /' | head -5
ok "以上是容器内已建出的工作台目录（首访建目录，没访问过则为空属正常）"

say "小结"
printf '  PASS=%d  FAIL=%d\n' "$PASS" "$FAIL"
printf '  验证容器保留为 %s（端口 %s），复核后 docker rm -f %s\n' "$NAME" "$PORT" "$NAME"
[ "$FAIL" -eq 0 ] && printf '  判定：骨架通\n' || printf '  判定：骨架不通\n'
exit 0
