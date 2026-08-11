#!/bin/bash
# 本地回归闸：CI 停摆期间（决策 0008 临时口径）的唯一合入前检查入口。
# 四层一次跑完，任何一层红 = 整体红。Actions 恢复后它仍是本地预检，不废弃。
#
#   1. 扩展层 pytest 全量（我们的代码，最快、最该先红）
#   2. i18n 硬闸（parity 是 CI 里的硬闸，本地等价复算）
#   3. 前端 node 测试 + eslint（要求 web/node_modules 已装，缺则明说怎么装）
#   4. 上游 tests/api（在生产镜像里挂仓跑——"上游没被我们改坏"的证据面）
#
# 用法：bash extensions/test-partner/scripts/regression_gate.sh [镜像名]
# 镜像默认 deeptutor:p3-full。
#
# 已知基线扣除：**具名扣除清单是 scripts/known-deductions.json**（机械可读单一真相）。
# 每条带三段——为什么红 / 什么时候该消失 / 谁复核——缺一段 deductions.py 判红。
# 本闸每次运行都会打印「本次扣除 N 条」并列出清单：**扣除本身必须可见**，
# 否则它就变成了另一种静默（与「不许假绿」是同一族问题的镜像：红久了没人看，
# 真红混进来谁也认不出）。看到条数变多就要问为什么。
# 扣已知、其余任何红都算真红。

set -uo pipefail
IMAGE="${1:-deeptutor:p3-full}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAIL=0
DEDUCT="$ROOT/extensions/test-partner/scripts/deductions.py"
say(){ printf '\n=== %s ===\n' "$1"; }
verdict(){ if [ "$1" -eq 0 ]; then printf '  [PASS] %s\n' "$2"; else printf '  [FAIL] %s\n' "$2"; FAIL=1; fi }

say "0/4 具名扣除清单"
python "$DEDUCT" --validate >/dev/null 2>&1
verdict $? "known-deductions.json 三段式完整（为什么红/何时删/谁复核）"
python "$DEDUCT" --print

say "1/4 扩展层 pytest 全量"
( cd "$ROOT/extensions/test-partner" && python -m pytest -q --no-header 2>&1 | tail -2 )
verdict "${PIPESTATUS[0]}" "extensions/test-partner pytest"

say "2/4 i18n 硬闸"
# 脚本真身在 web/scripts/ 下，不在仓库根（在根目录跑会 MODULE_NOT_FOUND）
( cd "$ROOT/web" && node scripts/i18n_parity.mjs )
verdict $? "i18n parity"

say "3/4 前端 node 测试 + eslint"
KNOWN_BROKEN_NODE_TESTS=$(python "$DEDUCT" --layer web-node-tests --format grep-pattern)
if [ ! -d "$ROOT/web/node_modules" ]; then
  printf '  [FAIL] web/node_modules 未安装。先跑：cd web && npm ci\n'
  FAIL=1
else
  node_out=$(cd "$ROOT/web" && npm run --silent test:node 2>&1)
  printf '%s\n' "$node_out" | grep -E "^# (pass|fail) " | sed 's/^/  /'
  unexpected=$(printf '%s\n' "$node_out" | grep "^not ok" \
    | grep -vE "$KNOWN_BROKEN_NODE_TESTS" || true)
  if [ -z "$unexpected" ]; then
    verdict 0 "web test:node（已知 4 个上游坏件之外零失败）"
  else
    printf '%s\n' "$unexpected" | head -5
    verdict 1 "web test:node 出现基线外失败"
  fi
  ( cd "$ROOT/web" && npx --no-install eslint . >/dev/null 2>&1 )
  verdict $? "eslint（0 error 即过，warning 不拦）"
fi

say "4/4 上游 tests/api（镜像内挂仓跑；镜像不带 pytest，先装）"
# Git Bash 下 /d/... 形式的挂载路径会被改写，用 Windows 形式（pwd -W）；
# 容器内不接 tail——管道会把 pytest 的退出码换成 tail 的 0。
WIN_ROOT="$(cd "$ROOT" && pwd -W 2>/dev/null || pwd)"
UPSTREAM_DESELECT=$(python "$DEDUCT" --layer upstream-tests --format pytest-deselect)
docker run --rm -v "$WIN_ROOT:/repo" --entrypoint sh "$IMAGE" -c \
  "pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pytest pytest-asyncio >/dev/null 2>&1; \
   cd /repo && python -m pytest tests/api tests/core -q --no-header --tb=no \
     $UPSTREAM_DESELECT" 2>&1 | tail -3
verdict "${PIPESTATUS[0]}" "upstream tests/api+core（按具名清单扣除）"

say "结果"
if [ "$FAIL" -eq 0 ]; then
  echo "  全绿。可以进入合入前的非作者审查（MERGE_REVIEW_SPEC）。"
else
  echo "  有红项，先修再谈合入。"
fi
exit "$FAIL"
