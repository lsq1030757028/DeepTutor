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
# 每条带四段——为什么红 / 什么时候该消失 / 谁复核 / 上次真跑核过是哪天怎么跑的——
# 缺一段 deductions.py 判红。**按 nodeid 扣，不按文件扣**：文件级扣除会连带把同文件
# 里其他真红一起藏掉（2026-08-11 实测：cors 那条文件级扣除，该文件 4 例里只有 2 例
# 真红，另 2 例——含一条专测 partner PATCH 预检的——被顺带豁免了）。
# 本闸每次运行都会打印「本次扣除 N 条」并列出清单：**扣除本身必须可见**，
# 否则它就变成了另一种静默（与「不许假绿」是同一族问题的镜像：红久了没人看，
# 真红混进来谁也认不出）。看到条数变多就要问为什么。
# 扣已知、其余任何红都算真红。

filter_unexpected_node_failures() {
  local output="$1"
  local known_pattern="$2"
  if [ -n "$known_pattern" ]; then
    printf '%s\n' "$output" | grep "^not ok" | grep -vE "$known_pattern" || true
  else
    printf '%s\n' "$output" | grep "^not ok" || true
  fi
}

# 单元测试只加载筛选函数，不运行四层回归闸。
if [ "${BASH_SOURCE[0]}" != "$0" ]; then
  return 0
fi

set -uo pipefail
IMAGE="${1:-deeptutor:p3-full}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
FAIL=0
DEDUCT="$ROOT/extensions/test-partner/scripts/deductions.py"
NODE_BIN="${NODE_BIN:-node}"
NPM_BIN="${NPM_BIN:-npm}"
say(){ printf '\n=== %s ===\n' "$1"; }
verdict(){ if [ "$1" -eq 0 ]; then printf '  [PASS] %s\n' "$2"; else printf '  [FAIL] %s\n' "$2"; FAIL=1; fi }

# 解释器**钉死在扩展自己的 venv**，不用环境里的 `python`（2026-08-11 加）。
#
# 为什么：本闸此前用裸 `python`，于是闸是红是绿取决于你在哪个 shell 里跑它——
# 实测同一份代码，venv 里 1217 passed / 3 skipped，全局 python 里
# `test_journey_call_surface` 直接 ModuleNotFoundError（全局没装 `mcp`）。
# **一个结论随环境变的闸不是闸**，它只是把"我这儿是绿的"这句话自动化了一遍。
#
# 缺 venv 时**判红并说清怎么建**，不静默回落到 `python`：回落等于把上面那个
# 不确定性又请回来，而且这次还带着"闸说它跑过了"的背书。
PY="$ROOT/extensions/test-partner/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/extensions/test-partner/.venv/bin/python"

say "0/4 解释器与具名扣除清单"
if [ ! -x "$PY" ]; then
  printf '  [FAIL] 扩展 venv 不存在：%s\n' "$PY"
  printf '        先建：cd extensions/test-partner && python -m venv .venv \\\n'
  printf '              && .venv/Scripts/python -m pip install -r requirements-dev.txt\n'
  FAIL=1
else
  printf '  解释器：%s（%s）\n' "$PY" "$("$PY" -V 2>&1)"
fi
"$PY" "$DEDUCT" --validate >/dev/null 2>&1
verdict $? "known-deductions.json 四段式完整（为什么红/何时删/谁复核/上次真跑核过）"
"$PY" "$DEDUCT" --print

say "1/4 扩展层 pytest 全量"
( cd "$ROOT/extensions/test-partner" && "$PY" -m pytest -q --no-header 2>&1 | tail -2 )
verdict "${PIPESTATUS[0]}" "extensions/test-partner pytest（venv 解释器）"

say "2/4 i18n 硬闸"
# 脚本真身在 web/scripts/ 下，不在仓库根（在根目录跑会 MODULE_NOT_FOUND）
( cd "$ROOT/web" && "$NODE_BIN" scripts/i18n_parity.mjs )
verdict $? "i18n parity"

say "3/4 前端 node 测试 + eslint"
KNOWN_BROKEN_NODE_TESTS=$("$PY" "$DEDUCT" --layer web-node-tests --format grep-pattern)
if [ ! -d "$ROOT/web/node_modules" ]; then
  printf '  [FAIL] web/node_modules 未安装。先跑：cd web && npm ci\n'
  FAIL=1
else
  node_out=$(cd "$ROOT/web" && "$NODE_BIN" scripts/run-node-tests.mjs 2>&1)
  printf '%s\n' "$node_out" | grep -E "^# (pass|fail) " | sed 's/^/  /'
  unexpected=$(filter_unexpected_node_failures "$node_out" "$KNOWN_BROKEN_NODE_TESTS")
  if [ -z "$unexpected" ]; then
    verdict 0 "web test:node（具名扣除清单之外零失败）"
  else
    printf '%s\n' "$unexpected" | head -5
    verdict 1 "web test:node 出现基线外失败"
  fi
  ( cd "$ROOT/web" && "$NODE_BIN" node_modules/eslint/bin/eslint.js . >/dev/null 2>&1 )
  verdict $? "eslint（0 error 即过，warning 不拦）"
  audit_out=$(cd "$ROOT/web" && "$NPM_BIN" audit --audit-level=high --no-fund 2>&1)
  audit_code=$?
  if [ "$audit_code" -ne 0 ]; then
    printf '%s\n' "$audit_out" | tail -12
  fi
  verdict "$audit_code" "npm audit（high/critical 必须为 0；中低风险显式留给审查）"
fi

say "4/4 上游 tests/api（只读源码 + 临时数据卷；镜像不带 pytest，先装）"
# Git Bash 下 /d/... 形式的挂载路径会被改写，用 Windows 形式（pwd -W）。
# 源码只读，/repo/data 用随容器销毁的匿名卷；先从只读种子复制配置和夹具，
# 避免回归测试改到用户数据。生产镜像默认忽略进程覆盖，这里显式关闭，
# 让 monkeypatch 环境变量的配置契约按 CI 测试形态生效。
# 容器内不接 tail——管道会把 pytest 的退出码换成 tail 的 0。
WIN_ROOT="$(cd "$ROOT" && pwd -W 2>/dev/null || pwd)"
UPSTREAM_DESELECT=$("$PY" "$DEDUCT" --layer upstream-tests --format pytest-deselect)
MSYS_NO_PATHCONV=1 docker run --rm -e DEEPTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=0 \
  -v "$WIN_ROOT:/repo:ro" -v "$WIN_ROOT/data:/seed-data:ro" -v /repo/data \
  --entrypoint sh "$IMAGE" -c \
  "cp -a /seed-data/. /repo/data/; \
   pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pytest pytest-asyncio >/dev/null 2>&1; \
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
