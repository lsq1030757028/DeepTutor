# -*- coding: utf-8 -*-
"""dod_ci — M1 DoD 中标注「CI 判」的项的离线可跑集合（设计稿 §5 归属表）。

设计稿 §5 把 DoD 分「CI 判（全离线可跑）」与「手动集成跑」。本脚本把前者收成一条
命令：全离线、零凭据、零网络（除本地 pytest）。手动集成项（活靶端到端）不在此，
其证据在 agent-lab-runs/.../evidence/（first-run、fault-injection-and-determinism）。

用法：python scripts/dod_ci.py         全绿 exit 0，任一红 exit 1
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# DoD 条目（设计稿 §5 编号）→ 离线 CI 检查（pytest 节点或脚本）
CI_ITEMS = [
    ("#4b 故障注入类2：闸链变异被拦",
     "tests/test_journey_exec.py::test_mutation_handwritten_verdict_blocked "
     "tests/test_journey_exec.py::test_mutation_tampered_claim_blocked "
     "tests/test_journey_exec.py::test_mutation_deleted_evidence_blocked"),
    ("#4c 零假绿机制金标（assert 分层五闸）",
     "tests/test_gate_assert.py"),
    ("#5 verdict 由闸链投影生成、禁手写（投影器向量）",
     "tests/test_gate_projection.py"),
    ("#7 凭据零落盘扫描器自身+金标样本",
     "tests/test_gate_credscan.py"),
    ("#9 覆盖图 coverage_ledger 生成逻辑 golden",
     "tests/test_journey_exec.py::test_coverage_ledger_built "
     "tests/test_journey_exec.py::test_coverage_gap_unexplained_not_done "
     "tests/test_journey_console.py"),
    ("#10 用例集删除后可从 ApprovedCaseSet 重编译（确定性子集）",
     "tests/test_journey_exec.py::test_projection_is_deterministic_rewrite_not_append "
     "tests/test_journey_exec.py::test_compile_produces_bundle_and_passes_gate"),
    ("#11 部分旅程交付（产物闸离线部分）",
     "tests/test_journey_tools_design.py::test_clarify_prefix_is_legal_delivery "
     "tests/test_journey_tools_design.py::test_tier_heuristic_splits"),
    ("#13 schema/digest 计算 + 蒸馏/牙移植向量",
     "tests/test_journey_digest.py tests/test_journey_schema.py "
     "tests/test_gate_sot.py tests/test_gate_downstream.py tests/test_gate_cases.py "
     "tests/test_gate_evidence.py tests/test_gate_mechanical.py "
     "tests/test_journey_redlines.py"),
    ("护栏5 执行层五红线新拓扑口径（各有测试）",
     "tests/test_journey_redlines.py "
     "tests/test_journey_exec.py::test_execute_cross_host_skipped "
     "tests/test_journey_exec.py::test_execute_missing_vars_skipped "
     "tests/test_journey_exec.py::test_execute_redirect_not_followed "
     "tests/test_journey_exec.py::test_execute_business_fail_not_fake_green "
     "tests/test_journey_exec.py::test_execute_credscan_catches_echoed_secret "
     "tests/test_journey_exec.py::test_execute_write_unconfirmed_skipped"),
]


def main() -> int:
    print("== M1 DoD CI（离线可跑项，设计稿 §5）==")
    failures = []
    for label, nodes in CI_ITEMS:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p",
             "no:cacheprovider", *nodes.split()],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace")
        tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
        ok = proc.returncode == 0
        print(f"  {'PASS' if ok else 'FAIL'}  {label}\n         {tail}")
        if not ok:
            failures.append(label)
    print(f"\n== {len(CI_ITEMS) - len(failures)}/{len(CI_ITEMS)} DoD CI 项通过 ==")
    if failures:
        print("未过:", "; ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
