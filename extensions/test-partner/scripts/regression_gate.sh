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
# 已知基线扣除（都是上游/环境问题，不是我们的；扣已知、其余任何红都算真红）：
# - 第 3 层：4 个测试文件因上游测试构建的 ESM/CJS 混用而载入即炸
#   （dist 里 code-block-themes.js 带 export 却被 require；这 4 个文件在我们
#   分支 diff 里零命中，2026-08-08 实测归因）。
# - 第 4 层：test_cors_settings 2 例在纯上游基线镜像同样红
#   （见 BUILD-NOTES"两条红的归因"），文件级 --deselect。

set -uo pipefail
IMAGE="${1:-deeptutor:p3-full}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAIL=0
say(){ printf '\n=== %s ===\n' "$1"; }
verdict(){ if [ "$1" -eq 0 ]; then printf '  [PASS] %s\n' "$2"; else printf '  [FAIL] %s\n' "$2"; FAIL=1; fi }

say "1/4 扩展层 pytest 全量"
( cd "$ROOT/extensions/test-partner" && python -m pytest -q --no-header 2>&1 | tail -2 )
verdict "${PIPESTATUS[0]}" "extensions/test-partner pytest"

say "2/4 i18n 硬闸"
# 脚本真身在 web/scripts/ 下，不在仓库根（在根目录跑会 MODULE_NOT_FOUND）
( cd "$ROOT/web" && node scripts/i18n_parity.mjs )
verdict $? "i18n parity"

say "3/4 前端 node 测试 + eslint"
KNOWN_BROKEN_NODE_TESTS="appearance-settings-page|code-block-themes|provider-trace-row|rich-code-block"
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
docker run --rm -v "$WIN_ROOT:/repo" --entrypoint sh "$IMAGE" -c \
  "pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pytest pytest-asyncio >/dev/null 2>&1; \
   cd /repo && python -m pytest tests/api -q --no-header --tb=no \
     --deselect tests/api/test_cors_settings.py" 2>&1 | tail -3
verdict "${PIPESTATUS[0]}" "upstream tests/api（已排除基线镜像同红的 cors 2 例）"

say "结果"
if [ "$FAIL" -eq 0 ]; then
  echo "  全绿。可以进入合入前的非作者审查（MERGE_REVIEW_SPEC）。"
else
  echo "  有红项，先修再谈合入。"
fi
exit "$FAIL"
