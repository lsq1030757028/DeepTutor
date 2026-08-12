# -*- coding: utf-8 -*-
"""compile_bundle — 原子工具 6：编译（pytest+Playwright 单轨，决策 0014）。

输入：ApprovedCaseSet → 输出 `AutomationBundle`（批次目录 bundle/，自包含 pytest 工程）。
牙（挂产物）：compile-gate 最小版（architecture-analysis §3 工序 6）：
  1 schema：输入 caseset 全量校验 + 双 digest 重算比对（禁反写复算，任一不一致 BLOCK）
  1b 轨道纯度（E22，DoD#4b / 0023 裁定 2）：声明的 track 与 op 实际隐含的轨必须一致、
     一份 caseset 不许混轨、API 轨用例不许要 UI-only 证据、未知 op 判红不默认放行。
     **排在建目录之前**：它的判据是「零产物落盘」，先 mkdir 再校验就留了一地半成品。
  2 静态检查：生成的 .py 全部 py_compile 过
  3 collect：pytest --collect-only 收得到且条数 = 可自动化 case 数
  4 case 映射：collected 测试名 ↔ bundle.json 映射一一对应（带 digest 回指）
  5 凭据扫描：credential_scan 对 bundle 目录零命中（凭据只在执行期经环境注入）
任一不过 = 整个 bundle 目录删除（fail-closed，不留半成品）。

自包含：_redlines.py/_runtime.py/_harness.py 为 server/journey 对应模块的逐字嵌入
（生成头注明源与 sha256）；bundle 不内联 base_url 与任何凭据值。
"""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
from typing import Any

from server.journey import artifacts, digest, schema
from server.journey.gates import capability_ladder, conservation, credential_scan, track_purity
from server.journey.pw_harness import case_slug

COMPILER_VERSION = "m1.2"
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_EMBED = {"_redlines.py": "redlines.py", "_runtime.py": "pw_runtime.py",
          "_harness.py": "pw_harness.py", "_dbro.py": "db_readonly.py",
          "_pid_ledger.py": "pid_ledger.py"}

_CONFTEST = '''# 自动生成：AutomationBundle conftest（compiler {version}）。派生物，禁手改。
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest

import _harness as H
import _runtime as rt


@pytest.fixture(scope="session")
def tp_browser():
    """per-run 一个浏览器实例，run 内各 case 复用；finally 必关（ADR-M1-02）。"""
    ctx = H.ctx()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pid = None
        try:  # PID 登记（尽力而为；登记表只杀登记过的 PID）
            pid = browser._impl_obj._connection._transport._proc.pid
        except Exception:
            pass
        if pid:
            rt.register_pid(ctx["run_dir"], pid, "chromium")
        try:
            yield browser
        finally:
            try:
                browser.close()
            finally:
                if pid:
                    rt.deregister_pid(ctx["run_dir"], pid)
'''

_PYTEST_INI = """; 自动生成：钉 rootdir 在 bundle 内，隔离仓库 pytest 配置。派生物，禁手改。
[pytest]
addopts = -p no:cacheprovider
"""


def _embed_sources(bundle_dir: str) -> dict[str, str]:
    hashes = {}
    for target, src_name in _EMBED.items():
        src_path = os.path.join(_SRC_DIR, src_name)
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        sha = hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
        header = (f"# 生成物：源=server/journey/{src_name} sha256:{sha} "
                  f"compiler={COMPILER_VERSION}。逐字嵌入，禁手改。\n")
        with open(os.path.join(bundle_dir, target), "w", encoding="utf-8") as fh:
            fh.write(header + src)
        hashes[target] = sha
    return hashes


def _gen_tests(caseset: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    lines = [
        f"# 自动生成：AutomationBundle 用例（源 caseset={caseset['caseset_id']}，"
        f"compiler={COMPILER_VERSION}）。派生物，禁手改，重编译=删目录重跑 compile。",
        "import _harness as H",
        "",
        "CASES = {",
    ]
    mapping = []
    for c in caseset["cases"]:
        auto = c.get("automation") or {}
        if not auto.get("admissible"):
            continue
        recipe = auto["recipe"]
        meta = {
            "case_id": c["case_id"],
            "case_version": c["case_version"],
            "title": c.get("title", ""),
            "probing": bool((c.get("source_anchor") or {}).get("probing")),
            "writes": bool((c.get("side_effects") or {}).get("writes")),
            "track": recipe["track"],
            "actions": recipe["actions"],
            "source_case_digest": c["source_case_digest"],
            "oracle_digest": c["oracle_digest"],
            "schema_version": caseset["schema_version"],
        }
        lines.append(f"    {c['case_id']!r}: {meta!r},")  # repr=合法 Python 字面量
        test_name = "test_" + case_slug(c["case_id"])
        mapping.append({
            "case_id": c["case_id"], "case_version": c["case_version"],
            "source_case_digest": c["source_case_digest"],
            "oracle_digest": c["oracle_digest"],
            "test_name": test_name, "file": "test_cases.py",
            "track": recipe["track"], "probing": meta["probing"],
            "writes": meta["writes"],
        })
    lines.append("}")
    lines.append("")
    for m in mapping:
        if m["track"] == "ui":
            lines += [f"def {m['test_name']}(tp_browser):",
                      f"    H.run_ui_case(tp_browser, CASES[{m['case_id']!r}])",
                      ""]
        else:
            lines += [f"def {m['test_name']}():",
                      f"    H.run_api_case(CASES[{m['case_id']!r}])",
                      ""]
    return "\n".join(lines), mapping


def _collect_only(bundle_dir: str) -> tuple[list[str], str, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-c", "pytest.ini", "."],
        cwd=bundle_dir, capture_output=True, text=True, encoding="utf-8",
        timeout=120)
    names = re.findall(r"test_cases\.py::(test_[a-z0-9_]+)", proc.stdout or "")
    return names, (proc.stdout or "") + (proc.stderr or ""), proc.returncode


def compile_bundle(batch_id: str) -> dict[str, Any]:
    caseset = artifacts.load_artifact(batch_id, "approved_caseset")
    problems: list[str] = []
    # 闸 1：schema + 禁反写复算
    check = schema.validate_caseset(caseset)
    if not check["ok"]:
        problems += [f"schema {e['code']} {e['where']}: {e['problem']}"
                     for e in check["errors"]]
    for c in caseset.get("cases", []):
        for e in digest.verify_case_digests(c):
            problems.append(f"digest 复算不一致 {c.get('case_id')}: {e}")
    if problems:
        return {"ok": False, "gate": "compile-gate#1-schema", "problems": problems}

    # 闸 1b：轨道纯度（E22，DoD#4b）。**在建目录之前**——与门票闸同理，
    # 拒绝的判据是「零产物落盘」，先 mkdir 再校验就等于留了一地半成品。
    purity = track_purity.check_caseset(caseset)
    if not purity["ok"]:
        return {"ok": False, "gate": "compile-gate#1b-track-purity",
                "problems": [f"{p['code']} {p['where']}: {p['problem']}"
                             for p in purity["problems"]]}

    # 闸 1c：守恒（E19，设计稿 §6.2）。同样**在建目录之前** —— 与 E22 一个理由。
    # L3 未授予时本闸不判，但会产出显式 gap 交给 coverage：躲得掉闸，躲不掉账。
    profile = (artifacts.load_artifact(batch_id, "intake_profile")
               if artifacts.has_artifact(batch_id, "intake_profile") else {})
    l3 = capability_ladder.granted(profile, "L3")
    cons = conservation.check_caseset(caseset, l3_granted=l3)
    if not cons["ok"]:
        return {"ok": False, "gate": "compile-gate#1c-conservation",
                "problems": [f"{p['code']}.{p['sub']} {p['where']}: {p['problem']}"
                             for p in cons["problems"]]}

    bundle_dir = os.path.join(artifacts.batch_dir(batch_id), "bundle")
    if os.path.isdir(bundle_dir):
        shutil.rmtree(bundle_dir)
    os.makedirs(bundle_dir)
    try:
        embed_hashes = _embed_sources(bundle_dir)
        test_src, mapping = _gen_tests(caseset)
        if not mapping:
            problems.append("caseset 里没有任何 admissible=true 的用例，无可编译内容")
            raise _GateFail
        with open(os.path.join(bundle_dir, "test_cases.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(test_src)
        with open(os.path.join(bundle_dir, "conftest.py"), "w",
                  encoding="utf-8") as fh:
            fh.write(_CONFTEST.format(version=COMPILER_VERSION))
        with open(os.path.join(bundle_dir, "pytest.ini"), "w",
                  encoding="utf-8") as fh:
            fh.write(_PYTEST_INI)
        manifest = {
            "artifact": "automation_bundle",
            "schema_version": "1",
            "batch_id": batch_id,
            "caseset_id": caseset["caseset_id"],
            "caseset_sha256": digest.sha256_digest(caseset),
            "caseset_schema_version": caseset["schema_version"],
            "compiler_version": COMPILER_VERSION,
            "generated_at": artifacts.now_iso(),
            "embedded_sources": embed_hashes,
            # L3 状态随 bundle 走：执行侧与报告侧都要知道这份产物是在
            # 「有数据层」还是「没数据层」的前提下编出来的。缺了它，
            # 一份没有守恒断言的写用例 bundle 无法区分是躲了闸还是没授权。
            "capability_l3_granted": l3,
            "conservation_declared_gaps": cons["declared_gaps"],
            "cases": mapping,
        }
        with open(os.path.join(bundle_dir, "bundle.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)

        # 闸 2：静态检查
        for name in os.listdir(bundle_dir):
            if name.endswith(".py"):
                try:
                    py_compile.compile(os.path.join(bundle_dir, name),
                                       doraise=True)
                except py_compile.PyCompileError as exc:
                    problems.append(f"py_compile {name}: {exc}")
        if problems:
            raise _GateFail

        # 闸 3+4：collect 与映射
        collected, out, collect_rc = _collect_only(bundle_dir)
        expected = [m["test_name"] for m in mapping]
        if collect_rc != 0 or sorted(collected) != sorted(expected):
            problems.append(
                f"collect 失败或与映射不一致：rc={collect_rc} "
                f"collected={sorted(collected)} "
                f"expected={sorted(expected)}；输出尾部：{out[-500:]}")
            raise _GateFail

        # 闸 5：凭据扫描（bundle 里不许出现任何高熵串/凭据；digest 形态放行）。
        # 内嵌运行时源码（_redlines/_runtime/_harness）豁免高熵启发式——它们是确定性
        # 代码、完整性由 manifest.embedded_sources 的 sha256 保证；known-secret 仍全量扫。
        scan = credential_scan.scan_tree(bundle_dir, skip_rel=list(_EMBED))
        if not scan["ok"]:
            problems.append(f"凭据扫描命中：known={scan['known_hits']} "
                            f"entropy={[h['token_preview'] for h in scan['entropy_hits']]}")
            raise _GateFail
    except _GateFail:
        shutil.rmtree(bundle_dir, ignore_errors=True)
        return {"ok": False, "gate": "compile-gate", "problems": problems}
    return {"ok": True, "bundle_dir": bundle_dir, "manifest": manifest,
            "collected": len(mapping),
            "credential_scan": {"scanned": scan["scanned_files"]}}


class _GateFail(Exception):
    pass
