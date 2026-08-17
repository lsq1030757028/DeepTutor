#!/bin/bash
# 闭环判据验证（决策 0012 的 AC-1 ～ AC-8）。
#
# 与既有两个 verify 脚本同一套纪律：副本卷、独立端口、第 0 步自证现役实例没被碰。
# 每条断言直接对应 spec 里的一条判据，编号一致——验收时照着念即可。
#
# 判据里属于"界面表现"的部分（禁用态、展开区渲染）这里验的是**其数据前提**：
# 接口给没给出这些信息。像素层面的走查在浏览器里做，不在本脚本冒充。

set -uo pipefail
IMAGE="${1:-deeptutor:p3-closure}"
NAME="deeptutor-closure"
PORT=3786
PASS=0; FAIL=0
say(){ printf '\n=== %s ===\n' "$1"; }
ok(){ printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad(){ printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }
api(){ curl -s -m 25 "http://127.0.0.1:${PORT}$1" "${@:2}"; }

say "0 · 现役实例保护自证"
[ "$(docker inspect -f '{{.State.Status}}' deeptutor 2>/dev/null)" = "running" ] \
  && ok "现役 deeptutor 仍在跑，未被本脚本触碰" || bad "现役实例状态异常"

say "1 · 起验证实例"
docker rm -f "$NAME" >/dev/null 2>&1
# 镜像必须是本地已有的：不加这一步的话 docker 会去 registry 拉，
# 网络不通时报的是一句 registry EOF，与"镜像没构建出来"这个真因差得很远。
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  bad "本地没有镜像 $IMAGE——先构建它（构建失败时别用 | tail 吞掉退出码）"
  printf '\n  PASS %d / FAIL %d\n' "$PASS" "$FAIL"
  exit 1
fi
RUNERR=$(docker run -d --name "$NAME" -p 127.0.0.1:${PORT}:3782 \
  -v deeptutor-data-verify:/app/data "$IMAGE" 2>&1 >/dev/null)
[ -z "$RUNERR" ] && ok "容器已启动（端口 ${PORT}）" || bad "容器启动失败：$RUNERR"
printf '  等待健康检查'
for _ in $(seq 1 90); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = "healthy" ] && break
  printf '.'; sleep 2
done; printf '\n'
[ "$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null)" = "healthy" ] \
  && ok "健康检查 healthy" || bad "健康检查未通过"

say "2 · 造夹具：一条变量齐、一条缺变量、一条不可执行"
# -u deeptutor 不能省：docker exec 默认 root，造出来的批次目录归 root，
# 而应用进程是 deeptutor 用户——写不进去，表现成 PATCH 报 500。
# 这是脚手架造的假故障，判定依据是真实批次的属主都是 deeptutor。
docker exec -u deeptutor -i "$NAME" python - <<'PYEOF' >/dev/null 2>&1
import sys
sys.path.insert(0, "/app/extensions/test-partner")
from server import delivery
cases = [
    {"case_id": "TC-001", "title": "健康检查应通过", "priority": "高", "module": "闭环夹具",
     "steps": ["GET health"], "expected": "200",
     "request": {"method": "GET", "url": "{{baseUrl}}/api/v1/test-workbench/health",
                 "headers": [{"key": "X-Probe", "value": "{{probe_token}}"}],
                 "assertions": [{"type": "status", "expected": 200},
                                {"type": "body_contains", "expected": "extension_loaded"}]}},
    {"case_id": "TC-002", "title": "缺变量的用例", "priority": "中", "module": "闭环夹具",
     "steps": ["GET x"], "expected": "200",
     "request": {"method": "GET", "url": "{{baseUrl}}/api/v1/test-workbench/health",
                 "headers": [{"key": "X-Missing", "value": "{{never_configured}}"}],
                 "assertions": [{"type": "status", "expected": 200}]}},
    {"case_id": "TC-003", "title": "人工核对文案", "priority": "低", "module": "闭环夹具",
     "steps": ["人工检查"], "expected": "无错别字"},
]
r = delivery.save_delivery(cases, fmt="csv", title="closure-fixture",
                           out_root="/app/data/test-workbench/deliveries")
assert r.get("ok"), r
PYEOF
# 夹具标题用 ASCII：Git Bash 把 python -c 里的中文按 GBK 发出去，
# 容器里按 UTF-8 读就成了乱码，匹配永远不中（本仓踩过三次的老坑）。
BATCH=$(api /api/v1/test-workbench/deliveries | python -c "
import json,sys
rows=json.load(sys.stdin).get('deliveries') or []
for r in rows:
    if r.get('structured') and 'closure-fixture' in (r.get('title') or ''):
        print(r['id']); break")
[ -n "$BATCH" ] && ok "夹具批次就绪：$BATCH" || bad "夹具没造出来"

# 只配 probe_token，故意不配 never_configured
api /api/v1/test-workbench/environments -X POST -H 'Content-Type: application/json' \
  -d '{"name":"closure-env","base_url":"http://127.0.0.1:8001","variables":[{"key":"probe_token","value":"closure-probe-secret-9911"}]}' >/dev/null

say "AC-1 · 用例展开能看到完整请求与逐条断言"
DET=$(api "/api/v1/test-workbench/deliveries/$BATCH")
printf '%s' "$DET" | python -c "
import json,sys
d=json.load(sys.stdin); c=[x for x in d['cases'] if x['case_id']=='TC-001'][0]
req=c.get('request') or {}
assert req.get('method')=='GET', '缺方法'
assert req.get('url'), '缺地址'
assert req.get('headers'), '缺请求头'
assert len(req.get('assertions') or [])==2, '断言条数对不上'
print('  [PASS] 方法/地址/请求头/2 条断言齐备')" && PASS=$((PASS+1)) || bad "AC-1 详情字段不全"

say "AC-2 · 标出缺哪些变量（required_vars 是差集的数据前提）"
printf '%s' "$DET" | python -c "
import json,sys
d=json.load(sys.stdin)
by={x['case_id']:x for x in d['cases']}
assert by['TC-001']['required_vars']==['probe_token'], by['TC-001'].get('required_vars')
assert by['TC-002']['required_vars']==['never_configured'], by['TC-002'].get('required_vars')
assert by['TC-003']['required_vars']==[], '无请求块的用例不该有变量'
assert 'baseUrl' not in by['TC-001']['required_vars'], 'baseUrl 不该算作变量'
print('  [PASS] 三条用例的 required_vars 各自正确，baseUrl 已排除')" && PASS=$((PASS+1)) || bad "AC-2 required_vars 不对"

say "AC-3 · 配上变量后能看出用例变为可执行（反查表是那句回执的依据）"
USAGE=$(api /api/v1/test-workbench/environments/usage)
printf '%s' "$USAGE" | python -c "
import json,sys
u=json.load(sys.stdin)['usage']
assert 'probe_token' in u, '已配的变量不在反查表里'
assert 'never_configured' in u, '未配但被引用的变量也该在反查表里（否则用户不知道该配它）'
assert u['probe_token']['case_count']>=1
print('  [PASS] 反查表能回答「这个变量被谁用着」，含未配置的那个')" && PASS=$((PASS+1)) || bad "AC-3 反查表不对"

say "AC-4 · 在跑任务可被查回（常驻条的数据来源）"
ACT=$(api /api/v1/test-workbench/generate/jobs/active)
printf '%s' "$ACT" | grep -q '"job"' \
  && ok "active 端点可用（空闲时 job 为 null，有任务时给任务号）" \
  || bad "active 端点不可用：$ACT"

say "AC-6 · 已采纳用例可编辑、留痕、且改坏了拒存"
ED=$(api "/api/v1/test-workbench/deliveries/$BATCH/cases/TC-001" -X PATCH \
  -H 'Content-Type: application/json' -d '{"title":"edited-by-hand-probe"}')
printf '%s' "$ED" | grep -q '"origin": *"human"' \
  && ok "编辑成功并标记为人工修改" || bad "编辑失败或没留痕：$ED"
api "/api/v1/test-workbench/deliveries/$BATCH" | grep -q 'edited-by-hand-probe' \
  && ok "改动真落盘（列表复读得到新标题）" || bad "改动没落盘"
BADEDIT=$(api "/api/v1/test-workbench/deliveries/$BATCH/cases/TC-001" -X PATCH \
  -H 'Content-Type: application/json' \
  -d '{"request":{"method":"GET","url":"{{baseUrl}}/x","assertions":[{"type":"json_path","path":"","expected":1}]}}')
printf '%s' "$BADEDIT" | grep -q 'CASE_INVALID' \
  && ok "改成不合格的用例被拒（0010 硬约束二的延伸）" || bad "坏用例没被拦：$BADEDIT"
IDEDIT=$(api "/api/v1/test-workbench/deliveries/$BATCH/cases/TC-001" -X PATCH \
  -H 'Content-Type: application/json' -d '{"case_id":"TC-999"}')
printf '%s' "$IDEDIT" | grep -qi 'not_permitted\|FIELD_NOT_EDITABLE\|不允许\|Unprocessable\|detail' \
  && ok "身份字段改不动" || bad "身份字段被改了：$IDEDIT"

say "AC-7 · 可执行为零的判定依据（executable_count）"
printf '%s' "$DET" | python -c "
import json,sys
d=json.load(sys.stdin)
assert 'executable_count' in d, '缺 executable_count，界面无从判断该不该禁用'
print('  [PASS] executable_count =', d['executable_count'])" && PASS=$((PASS+1)) || bad "AC-7 缺判定依据"

say "AC-8 · 界面拿到的任何数据里都没有变量真值"
# **先证明这个端点真的回了东西，再说它里面没有真值。**
# 只写「grep 不到就算过」是坏闸：接口挂掉返回空串时它照样绿——
# BUILD-NOTES 记过同类教训（"因不存在而恒真的断言"）。
for ep in "/api/v1/test-workbench/environments" \
          "/api/v1/test-workbench/environments/usage" \
          "/api/v1/test-workbench/deliveries/$BATCH"; do
  BODY=$(api "$ep")
  if ! printf '%s' "$BODY" | grep -q '"ok"\|"cases"\|"usage"\|"environments"'; then
    bad "$ep 没有返回可识别的响应体（脱敏断言无从谈起）：${BODY:0:80}"
  elif printf '%s' "$BODY" | grep -q 'closure-probe-secret-9911'; then
    bad "真值出现在 $ep（红线）"
  else
    ok "$ep 有响应体且不含真值"
  fi
done

say "清理夹具环境"
api /api/v1/test-workbench/environments/delete -X POST \
  -H 'Content-Type: application/json' -d '{"name":"closure-env"}' >/dev/null

say "结果"
printf '  PASS %d / FAIL %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
