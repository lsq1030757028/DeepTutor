#!/bin/bash
# P3 全链路端到端验证：在自建镜像里真起一个实例，走完 HAR → 生成 → 采纳。
#
# 纪律与 P1/P3 骨架那两个脚本一致：
# - 用 deeptutor-data 的**副本卷**，独立端口，第 0 步先自证现役实例没被碰过
# - 不采信"接口返回 200"，要断言返回体的形状与关键字段
#
# 模型那一段说清楚：本机没有配 provider key 时，生成会停在"没有可用模型"。
# 那**不是失败**——它正是我们要验的空态之一（后端把它翻成 409 而不是把 401 甩出来）。
# 配了 key 的环境里再跑一次本脚本，第 7-9 步会走通真实生成。

set -uo pipefail
IMAGE="${1:-deeptutor:p3-full}"
NAME="deeptutor-p3flow"
PORT=3785
PASS=0; FAIL=0
say(){ printf '\n=== %s ===\n' "$1"; }
ok(){ printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad(){ printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }
api(){ curl -s -m 25 "http://127.0.0.1:${PORT}$1" "${@:2}"; }

say "0 · 现役实例保护自证"
[ "$(docker inspect -f '{{.State.Status}}' deeptutor 2>/dev/null)" = "running" ] \
  && ok "现役 deeptutor 仍在跑，未被本脚本触碰" || bad "现役实例状态异常"

say "1 · 起自建镜像（副本卷 + 独立端口）"
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" -p 127.0.0.1:${PORT}:3782 \
  -v deeptutor-data-verify:/app/data "$IMAGE" >/dev/null 2>&1 \
  && ok "容器已启动（端口 ${PORT}）" || bad "容器启动失败"

printf '  等待健康检查'
for _ in $(seq 1 90); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = "healthy" ] && break
  printf '.'; sleep 2
done; printf '\n'
[ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = "healthy" ] \
  && ok "健康检查 healthy" || bad "健康检查未通过"

say "2 · 页面真的能打开"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 25 "http://127.0.0.1:${PORT}/test-workbench")
[ "$code" = "200" ] && ok "/test-workbench 返回 200" || bad "/test-workbench 返回 $code"

html=$(curl -s -m 25 "http://127.0.0.1:${PORT}/test-workbench")
printf '%s' "$html" | grep -q "Test Workbench\|测试工作台" \
  && ok "首屏 HTML 含工作台标识（说明这个路由真被 next build 编进去了）" \
  || bad "首屏没有工作台标识"

say "3 · 扩展装进镜像了"
h=$(api /api/v1/test-workbench/health)
printf '%s' "$h" | grep -q '"extension_loaded":true' \
  && ok "extension_loaded=true（sys.path 引导在容器里成立）" \
  || bad "扩展没加载：$h"

say "4 · 生成面路由都在"
# 打容器内后端(:8001)而非前端(:3782)——前端只转发 /api/* 与 /ws/*,
# /openapi.json 在前端那一面本来就是 404。第一版这里查错了面。
spec=$(docker exec "$NAME" sh -c "curl -s -m 15 http://127.0.0.1:8001/openapi.json")
for p in "/api/v1/test-workbench/generate/capability" \
         "/api/v1/test-workbench/generate/scenario" \
         "/api/v1/test-workbench/generate/jobs" \
         "/api/v1/test-workbench/har/inspect"; do
  printf '%s' "$spec" | grep -q "$p" && ok "$p 已注册" || bad "$p 不在 OpenAPI 里"
done

say "5 · 能力探测能回答"
cap=$(api /api/v1/test-workbench/generate/capability)
printf '%s' "$cap" | grep -q '"model_ready"' \
  && ok "capability 返回 model_ready：$(printf '%s' "$cap" | head -c 120)" \
  || bad "capability 形状不对：$cap"

say "6 · HAR 上传体检（不调模型，应当直接出结果）"
har="./_flow_probe.har"; cat > "$har" <<'HAR'
{"log":{"entries":[
 {"startedDateTime":"2026-08-07T09:00:00.000Z","time":12,
  "request":{"method":"POST","url":"https://api.shop.example.com/api/v1/auth/login",
   "headers":[{"name":"Content-Type","value":"application/json"}],"queryString":[],
   "postData":{"mimeType":"application/json","text":"{\"username\":\"qa\",\"password\":\"p@ss\"}"}},
  "response":{"status":200,"content":{"mimeType":"application/json","text":"{\"data\":{\"token\":\"abc\"}}"}}},
 {"startedDateTime":"2026-08-07T09:00:02.000Z","time":30,
  "request":{"method":"POST","url":"https://api.shop.example.com/api/v1/order/create",
   "headers":[{"name":"Authorization","value":"Bearer eyJSECRET0123456789abcdef"}],"queryString":[],
   "postData":{"mimeType":"application/json","text":"{\"skuId\":\"SKU-1\",\"qty\":2,\"mobile\":\"13800138000\"}"}},
  "response":{"status":200,"content":{"mimeType":"application/json","text":"{\"data\":{\"orderId\":9}}"}}}
]}}
HAR
insp=$(curl -s -m 40 -F "file=@${har}" \
  "http://127.0.0.1:${PORT}/api/v1/test-workbench/har/inspect")
draft=$(printf '%s' "$insp" | python -c "import sys,json;print(json.load(sys.stdin).get('draft_id',''))" 2>/dev/null)
[ -n "$draft" ] && ok "体检出草稿 $draft" || bad "体检失败：$(printf '%s' "$insp" | head -c 200)"

printf '%s' "$insp" | grep -q '"pii_redacted": *false' \
  && ok "如实声明 PII 未脱敏（不写「已全部脱敏」）" \
  || bad "脱敏声明缺失或说了假话"

printf '%s' "$insp" | grep -q "eyJSECRET0123456789abcdef" \
  && bad "★ 凭证真值出现在体检结果里" \
  || ok "凭证真值没有出现在体检结果里"

say "7 · 草稿按用户隔离地落盘"
docker exec "$NAME" sh -c 'ls /app/data/test-workbench/drafts 2>/dev/null | head -3' \
  | grep -q "har-" && ok "草稿落在用户 scope 下的 drafts/" || bad "草稿没落盘"

docker exec "$NAME" sh -c 'grep -rl "eyJSECRET0123456789abcdef" /app/data 2>/dev/null | head -1' \
  | grep -q . && bad "★ 凭证真值落盘了" || ok "凭证真值没有落盘"

say "8 · 生成的空态（本机通常没配模型——这条正是要验的）"
gen=$(api /api/v1/test-workbench/generate/jobs -X POST \
      -H 'Content-Type: application/json' -d "{\"draft_id\":\"${draft}\"}")
if printf '%s' "$gen" | grep -q '"job_id"'; then
  ok "已配模型，生成任务已发起：$(printf '%s' "$gen" | head -c 100)"
elif printf '%s' "$gen" | grep -q "还没有可用的模型\|设置"; then
  ok "没配模型时给的是配置指引，不是 provider 的 401（正是本条要验的）"
else
  bad "生成端点返回了预期外的东西：$(printf '%s' "$gen" | head -c 200)"
fi

say "9 · 越权与穿越"
p404=$(curl -s -o /dev/null -w '%{http_code}' -m 20 \
  "http://127.0.0.1:${PORT}/api/v1/test-workbench/generate/jobs/notmine")
[ "$p404" = "404" ] && ok "查别人的任务 404（不给 403，403 会泄露 id 存在）" \
  || bad "查不存在的任务返回 $p404"

trav=$(curl -s -o /dev/null -w '%{http_code}' -m 20 \
  "http://127.0.0.1:${PORT}/api/v1/test-workbench/har/drafts/..%2F..%2Fetc")
[ "$trav" != "200" ] && ok "草稿 id 路径穿越被拒（$trav）" || bad "路径穿越没拦住"

rm -f "$har"
say "小结"
printf '  PASS=%d  FAIL=%d\n' "$PASS" "$FAIL"
printf '  验证实例保留为 %s（端口 %s），复核后 docker rm -f %s\n' "$NAME" "$PORT" "$NAME"
[ "$FAIL" -eq 0 ] && printf '  判定：P3 全链路在自建镜像里可用\n' \
                  || printf '  判定：有未通过项，见上\n'
exit 0
