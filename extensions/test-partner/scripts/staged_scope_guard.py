# -*- coding: utf-8 -*-
"""staged_scope_guard — 提交前比对暂存集与本线声明的独占前缀，越界即中止（BB-518）。

## 为什么是脚本不是约定

并行施工时「只 add 自己的文件」已经被约定过三次，2026-08-12 当天仍出了两次事故：

- 一条线用了全量 add，把另一条线在建的 5 个文件卷进自己的提交；
- 另一条线覆盖了别人的 `SKILL.md`（`Write` 报 updated 而不是 created，那是
  「你正在覆盖别人的东西」的一等信号，当时没停手核查）。

同类第三次就不该再加约定。这个脚本把「只提交自己的东西」变成一条**会中止的检查**：
暂存集里出现不在本线前缀内的路径，退出码非 0 并逐条列出。

## 用法

    # 装成 pre-commit（推荐，每次 commit 自动跑）
    git config core.hooksPath .githooks   # 若还没设过
    # 或者手动：提交前跑一次
    python -X utf8 extensions/test-partner/scripts/staged_scope_guard.py --agent m2-defect

    # 不用声明文件，直接给前缀（可重复）
    python -X utf8 .../staged_scope_guard.py --allow extensions/test-partner/skills/defect-filing/

    # 自测两例（越界必中止 / 合规必放行），不碰仓库状态
    python -X utf8 .../staged_scope_guard.py --selftest

退出码：0 = 放行；1 = 有越界文件；2 = 用法或环境错误（含**声明里没有这个 agent**
——未知一律 fail closed，不许"查不到就放行"）。

## 边界（如实说）

- 它看的是**暂存集**，管不住 `git commit -a`（那条路径绕过 index）。所以配套纪律仍是
  「先 `git add <具体文件>` 再 commit」，脚本是给这条纪律加一道会响的闸，不是替代它。
- 前缀是路径前缀匹配，不做 glob。前缀以 `/` 结尾表示目录，否则表示文件名前缀
  （`extensions/test-partner/tests/test_defect_` 这种形态是刻意支持的）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
SCOPES_PATH = os.path.join(_HERE, "agent-scopes.json")


def normalise(paths) -> list[str]:
    return [str(p).replace("\\", "/").strip() for p in paths if str(p).strip()]


def check(staged, allow) -> dict:
    """纯函数：返回 {ok, offenders, checked, allow}。空前缀集一律判越界（fail closed）。"""
    staged = normalise(staged)
    allow = normalise(allow)
    if not allow:
        return {"ok": False, "offenders": staged, "checked": len(staged), "allow": [],
                "reason": "没有声明任何独占前缀——空声明按越界处理，不许查不到就放行"}
    offenders = [p for p in staged if not any(p.startswith(a) for a in allow)]
    return {"ok": not offenders, "offenders": offenders,
            "checked": len(staged), "allow": allow, "reason": ""}


def load_scopes(path: str = SCOPES_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def staged_paths(repo_root: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                         cwd=repo_root, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"git diff --cached 失败：{out.stderr.strip()}")
    return normalise(out.stdout.splitlines())


def _selftest() -> int:
    """两例：越界暂存集必须中止，合规暂存集必须放行。"""
    allow = ["extensions/test-partner/skills/defect-filing/",
             "extensions/test-partner/tests/test_defect_"]

    bad = check(["extensions/test-partner/skills/defect-filing/SKILL.md",
                 "server/journey/defect_exit.py",          # 别人的独占区
                 "web/components/test-journey/copy.ts"],   # 另一条线的独占区
                allow)
    case1 = (bad["ok"] is False
             and bad["offenders"] == ["server/journey/defect_exit.py",
                                      "web/components/test-journey/copy.ts"])

    good = check(["extensions/test-partner/skills/defect-filing/SKILL.md",
                  "extensions/test-partner/skills/defect-filing/scripts/render_draft.py",
                  "extensions/test-partner/tests/test_defect_write_gate.py"],
                 allow)
    case2 = good["ok"] is True and good["offenders"] == []

    case3 = check(["anything"], []).get("ok") is False   # 空声明 fail closed

    print(f"[selftest] 越界集判中止: {'PASS' if case1 else 'FAIL'}（越界 {bad['offenders']}）")
    print(f"[selftest] 合规集判放行: {'PASS' if case2 else 'FAIL'}（检查 {good['checked']} 个路径）")
    print(f"[selftest] 空声明 fail closed: {'PASS' if case3 else 'FAIL'}")
    return 0 if (case1 and case2 and case3) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="提交前独占区护栏（BB-518）")
    ap.add_argument("--agent", help="声明文件里的线名，如 m2-defect")
    ap.add_argument("--allow", action="append", default=[], help="独占前缀，可重复")
    ap.add_argument("--repo-root", default=None, help="默认按本脚本位置上溯到仓库根")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    allow = list(args.allow)
    if args.agent:
        scopes = load_scopes()
        entry = (scopes.get("agents") or {}).get(args.agent)
        if entry is None:
            sys.stderr.write(
                f"E_UNKNOWN_AGENT: 声明文件里没有 {args.agent!r}。"
                f"已登记：{sorted((scopes.get('agents') or {}))}。\n"
                "未知一律 fail closed——先让协调方把这条线的独占区写进 agent-scopes.json。\n")
            return 2
        allow += list(entry.get("allow") or [])

    root = args.repo_root or os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
    try:
        staged = staged_paths(root)
    except RuntimeError as exc:
        sys.stderr.write(f"E_GIT: {exc}\n")
        return 2

    r = check(staged, allow)
    if r["ok"]:
        print(f"[scope-guard] 放行：{r['checked']} 个暂存路径都在本线独占区内。")
        return 0

    sys.stderr.write(
        "E_OUT_OF_SCOPE: 暂存集里有不属于本线独占区的文件，已中止提交。\n"
        + (f"  原因：{r['reason']}\n" if r["reason"] else "")
        + "  越界文件：\n"
        + "".join(f"    - {p}\n" for p in r["offenders"])
        + "  本线独占前缀：\n"
        + "".join(f"    - {a}\n" for a in r["allow"])
        + "  处置：git restore --staged <上面这些文件>，只 add 自己的；\n"
          "        如果这些文件确实该归你，先找协调方改 agent-scopes.json，别就地放行。\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
