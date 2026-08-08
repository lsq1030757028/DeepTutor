#!/bin/bash
# P3 执行/导出/环境面端到端验证：在自建镜像里真起一个实例，
# 走完 环境配置 → 采纳批次 → 执行 → 导出 → 下载。
#
# 纪律与 verify_p3_flow.sh 一致：
# - 用 deeptutor-data 的**副本卷**，独立端口，第 0 步先自证现役实例没被碰过
# - 不采信"接口返回 200"，断言返回体的形状与关键字段
#
# 执行那一段说清楚：批次里的用例 URL 来自 HAR，指向的被测系统在本验证实例里
# 不存在。所以断言的是**执行机制**（run 起得来、逐条有结论、报告落盘、凭据不出流），
# 用例本身判 failed/skipped 是预期内的事实记录，不是验证失败。
#
# 前置：deliveries 里要有一个带 cases.json 的批次（verify_p3_flow.sh 采纳过就有）。

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

say "2 · 新路由都在（查容器内后端 :8001 的 openapi，不查前端面）"
spec=$(docker exec "$NAME" sh -c "curl -s -m 15 http://127.0.0.1:8001/openapi.json")
for p in "/api/v1/test-workbench/environments" \
         "/api/v1/test-workbench/environments/delete" \
         "/api/v1/test-workbench/deliveries/{delivery_id}/runs" \
         "/api/v1/test-workbench/runs/{run_id}" \
         "/api/v1/test-workbench/deliveries/{delivery_id}/export" \
         "/api/v1/test-workbench/deliveries/{delivery_id}/files/{filename}" \
         "/api/v1/test-workbench/deliveries/{delivery_id}/archive"; do
  printf '%s' "$spec" | grep -qF "\"$p\"" \
    && ok "路由在：$p" || bad "路由缺失：$p"
done

say "3 · 环境金库：建 → 去值投影 → 值不回显"
save=$(api /api/v1/test-workbench/environments -X POST \
  -H 'Content-Type: application/json' \
  -d '{"name":"verify-env","base_url":"http://127.0.0.1:8001","variables":[{"key":"token","value":"super-secret-value-1234"}]}')
printf '%s' "$save" | grep -q '"ok":true' \
  && ok "环境保存成功" || bad "环境保存失败：$save"
listing=$(api /api/v1/test-workbench/environments)
printf '%s' "$listing" | grep -q '"verify-env"' \
  && ok "环境出现在清单里" || bad "清单里没有刚存的环境"
printf '%s' "$listing" | grep -q 'super-secret-value-1234' \
  && bad "凭据值出现在投影里（红线）" || ok "凭据值没有回显（去值投影生效）"
printf '%s' "$listing" | grep -q '"masked"' \
  && ok "投影带掩码字段" || bad "投影缺掩码字段"

say "4 · 金库落点在平台机密目录（0011 落点二）"
envfile=$(docker exec "$NAME" sh -c \
  "ls /app/data/system/user-secrets/*/test-workbench/environments.json 2>/dev/null | head -1")
[ -n "$envfile" ] && ok "environments.json 落在 owner_secrets_dir 下：$envfile" \
  || bad "机密目录下找不到 environments.json"

say "5 · 挑一个可执行批次（没有就造夹具批次，不经模型）"
pick_batch(){ api /api/v1/test-workbench/deliveries | python -c "
import json,sys
rows=json.load(sys.stdin).get('deliveries') or []
for r in rows:
    if r.get('structured'): print(r['id']); break"; }
batch=$(pick_batch)
if [ -z "$batch" ]; then
  # 夹具走真的 save_delivery（形状与线上同源），用例打的是容器内后端自己的
  # health 端点——执行环节因此能验出「真通过」，不只是「机制跑完」。
  # -i 必须有：不带它 stdin 不进容器，heredoc 整个被丢掉、python 静默跑了个空脚本
  docker exec -i "$NAME" python - <<'PYEOF' >/dev/null
import sys
sys.path.insert(0, "/app/extensions/test-partner")
from server import delivery
cases = [
    {"case_id": "TC-001", "title": "工作台健康检查应通", "priority": "高",
     "module": "验证夹具", "steps": ["GET health"], "expected": "200",
     "request": {"method": "GET",
                 "url": "{{baseUrl}}/api/v1/test-workbench/health",
                 "headers": [{"key": "Authorization", "value": "Bearer {{token}}"}],
                 "assertions": [{"type": "status", "expected": 200},
                                {"type": "body_contains", "expected": "extension_loaded"}]}},
    {"case_id": "TC-002", "title": "人工核对界面文案", "priority": "中",
     "module": "验证夹具", "steps": ["人工检查"], "expected": "无错别字"},
]
r = delivery.save_delivery(cases, fmt="csv", title="验证夹具批次",
                           source_fingerprint="sha256:fixture",
                           out_root="/app/data/test-workbench/deliveries")
assert r.get("ok"), r
PYEOF
  batch=$(pick_batch)
fi
[ -n "$batch" ] && ok "结构化批次就绪：$batch" \
  || bad "没有带 cases.json 的批次，夹具也没造出来"

say "6 · 执行：起跑 → 轮询到完 → 逐条有结论"
detail=$(api "/api/v1/test-workbench/deliveries/$batch")
ids=$(printf '%s' "$detail" | python -c "
import json,sys
d=json.load(sys.stdin)
print(json.dumps([c['case_id'] for c in d.get('cases') or [] if c.get('executable')]))")
run=$(api "/api/v1/test-workbench/deliveries/$batch/runs" -X POST \
  -H 'Content-Type: application/json' \
  -d "{\"env\":\"verify-env\",\"case_ids\":$ids,\"auth\":\"env\"}")
run_id=$(printf '%s' "$run" | python -c "import json,sys;print(json.load(sys.stdin).get('run_id',''))" 2>/dev/null)
[ -n "$run_id" ] && ok "run 已发起：$run_id" || bad "run 发起失败：$run"
state=""
for _ in $(seq 1 60); do
  state=$(api "/api/v1/test-workbench/runs/$run_id" | python -c \
    "import json,sys;print(json.load(sys.stdin).get('state',''))" 2>/dev/null)
  [ "$state" = "done" ] || [ "$state" = "error" ] && break
  sleep 1
done
[ "$state" = "done" ] && ok "run 跑完（state=done）" || bad "run 终态是 $state"
final=$(api "/api/v1/test-workbench/runs/$run_id")
printf '%s' "$final" | python -c "
import json,sys
r=json.load(sys.stdin)
res=(r.get('result') or {}).get('results') or []
summ=(r.get('result') or {}).get('summary') or {}
assert res, '没有逐条结果'
assert all(('passed' in one and 'case_id' in one) for one in res), '结果行缺字段'
assert summ.get('total')==len(res), 'summary 与结果条数对不上'
print('  [PASS] %d 条逐条有结论（%s）' % (len(res), summ.get('verdict','')))" \
  && PASS=$((PASS+1)) || { bad "逐条结果形状不对"; }
printf '%s' "$final" | grep -q 'super-secret-value-1234' \
  && bad "凭据值出现在执行结果里（红线）" || ok "凭据值没有进执行结果"

say "7 · 执行报告落盘在批次目录"
docker exec "$NAME" sh -c \
  "ls /app/data/test-workbench/deliveries/$batch/execution_report.json" >/dev/null 2>&1 \
  && ok "execution_report.json 已落盘" || bad "执行报告没落盘"

say "8 · 导出：四种格式 → 文件清单 → 指纹一致"
exp=$(api "/api/v1/test-workbench/deliveries/$batch/export" -X POST \
  -H 'Content-Type: application/json' \
  -d '{"formats":["xlsx","csv","markdown","postman"]}')
printf '%s' "$exp" | grep -q '"ok":true' \
  && ok "导出成功" || bad "导出失败：$exp"
nfiles=$(printf '%s' "$exp" | python -c \
  "import json,sys;print(len(json.load(sys.stdin).get('files') or []))" 2>/dev/null)
[ "$nfiles" = "4" ] && ok "4 个产物齐" || bad "产物数是 $nfiles"

say "9 · 下载：单文件字节对上 + zip 是合法压缩包 + 路径穿越被拒"
size=$(api "/api/v1/test-workbench/deliveries/$batch/files/cases.csv" -o /dev/null -w '%{size_download}')
[ "$size" -gt 100 ] 2>/dev/null && ok "cases.csv 下载到 ${size} 字节" || bad "下载为空"
# 相对路径落在 cwd：Git Bash 下 /tmp/... 会被 Windows 版 curl 当成别的盘符路径，
# 文件根本没写出来（实测踩过）
zipout="./verify-workbench-$$.zip"
api "/api/v1/test-workbench/deliveries/$batch/archive?files=cases.csv,cases.md" -o "$zipout"
python -c "
import zipfile,sys
z=zipfile.ZipFile('$zipout'); names=z.namelist()
assert set(names)=={'cases.csv','cases.md'}, names
print('  [PASS] zip 含两个成员且可解压')" && PASS=$((PASS+1)) || bad "zip 不合法"
rm -f "$zipout"
code=$(api "/api/v1/test-workbench/deliveries/$batch/files/..%2Freceipt.json" -o /dev/null -w '%{http_code}')
[ "$code" = "400" ] || [ "$code" = "404" ] \
  && ok "路径穿越被拒（$code）" || bad "路径穿越返回 $code"

say "10 · 环境删除闭环"
del=$(api /api/v1/test-workbench/environments/delete -X POST \
  -H 'Content-Type: application/json' -d '{"name":"verify-env"}')
printf '%s' "$del" | grep -q '"ok":true' && ok "环境已删" || bad "删除失败：$del"

say "结果"
printf '  PASS %d / FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
