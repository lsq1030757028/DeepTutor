# -*- coding: utf-8 -*-
"""code_anchor — L5/L6：部署指纹 commit 锚 + 需求↔代码反向对账（设计稿 §6.4/§6.5）。

靶 = **DT 自身 dogfood**（0018 已拍，不是开放项）。

## 部署锚为什么必须是 origin 可达的 commit

工作区既有铁律（D1 假阳性那次）：`code_identity` 必须绑一个 **origin 可达**的 commit，
并用 `git cat-file` 真验过。本地 HEAD 是不够的 —— 一个只存在于本机的 commit
（没 push、或者被 rebase 掉了）拿去当"这次测的是哪版代码"，事后**任何人都复现不了**，
而它长得和一个正常锚一模一样。

脏工作树同理：工作树有未提交改动时，跑的代码和那个 commit 不是一回事。
**这时不许拿 commit 冒充部署锚**，按 SSOT 的 L1 口径降级成「当前在跑实例@时间戳」。

降级不是失败 —— 能力锁就是为它准备的。**假装锚到了才是失败。**

## 反向对账的三种结论，以及为什么后两种不是缺陷

| 结论 | 含义 | 处置 |
|---|---|---|
| `implemented` | 规则找得到实现锚（文件:行） | 覆盖图上正常计 |
| `not_found` | 需求写了，代码里找不到 | 进 coverage 当 **gap**，不当缺陷 |
| `orphan` | 代码有分支，需求没说 | 同上 |

后两种**是不是缺陷要人判**（设计稿 §6.4 原文）：`not_found` 可能是没做，也可能是
我们的检索词不对；`orphan` 可能是多做了，也可能是需求文档没写全。机器只能说
"对不上"，说不了"谁错了"。把它们直接判成缺陷会产出一堆需要人去否掉的假单。
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any

DEGRADED_TO_L1 = "degraded_to_l1"


def _git(repo: str, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.returncode, (p.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def deploy_anchor(repo: str) -> dict[str, Any]:
    """本次被测部署的代码锚。返回 {level, commit?, reason?, checks}。

    `level` 只有两种取值：`L5`（锚住了）与 `degraded_to_l1`（锚不住，如实降级）。
    **没有第三种"大概锚住了"。**
    """
    checks: dict[str, Any] = {}

    rc, head = _git(repo, "rev-parse", "HEAD")
    checks["head"] = head if rc == 0 else f"(取不到:{head})"
    if rc != 0:
        return {"level": DEGRADED_TO_L1, "reason": "不是 git 仓库或 git 不可用",
                "checks": checks}

    # 1 对象真的存在（不是一个看起来像 sha 的字符串）
    rc_cat, _ = _git(repo, "cat-file", "-e", head + "^{commit}")
    checks["object_exists"] = rc_cat == 0

    # 2 origin 可达：**判据是它在某条远端分支上**，不是"有个 origin remote"
    rc_r, branches = _git(repo, "branch", "-r", "--contains", head)
    remote_branches = [b.strip() for b in branches.splitlines() if b.strip()] \
        if rc_r == 0 else []
    checks["remote_branches"] = remote_branches
    origin_reachable = any(b.startswith("origin/") for b in remote_branches)
    checks["origin_reachable"] = origin_reachable

    # 3 工作树干净：有未提交改动时，跑的代码和这个 commit 不是一回事
    rc_s, status = _git(repo, "status", "--porcelain")
    dirty = bool(status.strip()) if rc_s == 0 else True
    checks["dirty_worktree"] = dirty
    checks["dirty_files"] = [l[:120] for l in status.splitlines()[:20]] if dirty else []

    if not checks["object_exists"]:
        return {"level": DEGRADED_TO_L1, "checks": checks,
                "reason": "HEAD 指向的对象不存在（git cat-file 判否）"}
    if not origin_reachable:
        return {"level": DEGRADED_TO_L1, "checks": checks,
                "reason": f"commit {head[:12]} 不在任何 origin/* 分支上——"
                          f"只存在于本机的 commit 当部署锚，事后没人复现得了，"
                          f"而它长得和一个正常锚一模一样"}
    if dirty:
        return {"level": DEGRADED_TO_L1, "checks": checks,
                "reason": f"工作树有 {len(status.splitlines())} 处未提交改动——"
                          f"跑的代码与 {head[:12]} 不是一回事，不许拿它冒充部署锚"}
    return {"level": "L5", "commit": head, "checks": checks}


# ── 反向对账 ──────────────────────────────────────────────────────────────

_CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".java"}
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
              "build", ".next", ".pytest_cache", "data"}


def _iter_code_files(root: str, limit: int = 4000):
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1] in _CODE_EXT:
                yield os.path.join(dirpath, name)
                n += 1
                if n >= limit:
                    return


def find_anchors(root: str, needles: list[str], *,
                 limit_per_needle: int = 5) -> dict[str, list[str]]:
    """在代码树里找每个检索词的命中位置，返回 {needle: ["相对路径:行", ...]}。

    命中**只作为"找得到"的证据**，不作为"实现正确"的证据 —— 一个标识符出现在
    某个文件里，说明不了它的行为对。这层区别写在这里，是因为
    `implemented` 这个词很容易被读成"做对了"。
    """
    hits: dict[str, list[str]] = {n: [] for n in needles}
    pats = {n: re.compile(re.escape(n), re.I) for n in needles}
    for path in _iter_code_files(root):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        for i, line in enumerate(lines, 1):
            for n, pat in pats.items():
                if len(hits[n]) >= limit_per_needle:
                    continue
                if pat.search(line):
                    hits[n].append(f"{rel}:{i}")
    return hits


def build_requirement_code_map(rules: list[dict[str, Any]], repo: str, *,
                               needles_of: Any = None) -> dict[str, Any]:
    """需求↔代码反向对账产物（设计稿 §6.4）。

    `needles_of(rule) -> list[str]`：每条规则的检索词。**必须由调用方给**——
    从规则正文自动切词会产出一堆"的/和/支持"这种命中，让 `implemented` 变得毫无意义。
    """
    rows = []
    for rule in rules:
        needles = list(needles_of(rule)) if needles_of else []
        hits = find_anchors(repo, needles) if needles else {}
        found = {n: v for n, v in hits.items() if v}
        if not needles:
            verdict, why = "not_found", ("没有给检索词——这不是「代码里没有」，"
                                        "是「我们没找」")
        elif found:
            verdict, why = "implemented", ""
        else:
            verdict, why = "not_found", f"检索词 {needles} 在代码树里零命中"
        rows.append({
            "rule_id": rule.get("rule_id"),
            "statement": rule.get("statement", ""),
            "needles": needles,
            "anchors": found,
            "verdict": verdict,
            "why": why,
            # 这一栏防的是把"找得到"读成"做对了"
            "anchor_means": "命中=该标识符在代码里存在；不等于其行为符合需求",
        })
    return {
        "artifact": "requirement_code_map",
        "repo": os.path.basename(os.path.abspath(repo)),
        "rules": rows,
        "summary": {
            "implemented": sum(1 for r in rows if r["verdict"] == "implemented"),
            "not_found": sum(1 for r in rows if r["verdict"] == "not_found"),
        },
        "note": "not_found / orphan 进 coverage 当 gap，**不当缺陷**——"
                "是不是缺陷要人判（设计稿 §6.4）",
    }


__all__ = ["DEGRADED_TO_L1", "build_requirement_code_map", "deploy_anchor",
           "find_anchors"]
