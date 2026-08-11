#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归闸的具名扣除读取器 —— 让「扣了什么」可见、可校验、可到期。

## 为什么不是在闸脚本里写几个 grep 模式

那正是本文件要替换掉的形态。散落的 `KNOWN_BROKEN_XXX="a|b|c"` 与 `--deselect`
参数有三个问题：**说不清扣了几条**（要人去数字符串）、**说不清为什么扣**
（注释在别处、常年不更新）、**永远不会过期**（没有任何东西提醒你去复核）。

扣除不可见，就变成了另一种静默 —— 与「不许假绿」是同一族问题的镜像：
假绿是把红说成绿，静默扣除是把红藏起来不说。红久了没人看，真红混进来谁也认不出。

## 三段式是硬要求

每条扣除必须回答：为什么红 / 什么时候该消失 / 谁复核。缺任何一段本工具判红——
**没有到期条件的扣除等于永久豁免**，而永久豁免的东西没人再看一眼。

## 用法

    python scripts/deductions.py --layer upstream-tests --format pytest-deselect
    python scripts/deductions.py --layer web-node-tests --format grep-pattern
    python scripts/deductions.py --print          # 人读清单（闸每次都打）
    python scripts/deductions.py --validate       # 只校验三段完整性

退出码：0 = 清单合法，1 = 有条目缺三段之一或文件坏损。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_LIST = HERE / "known-deductions.json"

#: 三段式必填字段。缺一即判红——见模块文档串「三段式是硬要求」。
REQUIRED_FIELDS = ("why_red", "when_it_should_disappear", "reviewer")


def load(path: Path) -> tuple[list[dict], list[str]]:
    """读清单并校验。返回 (条目, 错误列表)。"""
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"E_LIST_UNREADABLE: {path}: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("deductions"), list):
        return [], [f"E_LIST_SHAPE: {path} 必须是 {{deductions: [...]}}"]

    rows: list[dict] = []
    seen: set[str] = set()
    for i, row in enumerate(data["deductions"]):
        if not isinstance(row, dict):
            errors.append(f"E_ROW_TYPE[{i}]: 条目必须是对象")
            continue
        rid = str(row.get("id") or "").strip()
        if not rid:
            errors.append(f"E_ROW_ID[{i}]: 缺 id")
        elif rid in seen:
            errors.append(f"E_ROW_DUPLICATE[{rid}]: id 重复")
        else:
            seen.add(rid)
        if not str(row.get("selector") or "").strip():
            errors.append(f"E_ROW_SELECTOR[{rid or i}]: 缺 selector（扣谁）")
        if not str(row.get("layer") or "").strip():
            errors.append(f"E_ROW_LAYER[{rid or i}]: 缺 layer（哪一层的闸）")
        for field in REQUIRED_FIELDS:
            if not str(row.get(field) or "").strip():
                errors.append(
                    f"E_ROW_{field.upper()}[{rid or i}]: 缺 {field}。"
                    "扣除必须回答「为什么红 / 什么时候该消失 / 谁复核」——"
                    "没有到期条件的扣除等于永久豁免。")
        rows.append(row)
    return rows, errors


def for_layer(rows: list[dict], layer: str) -> list[dict]:
    return [r for r in rows if r.get("layer") == layer]


def render(rows: list[dict], fmt: str) -> str:
    if fmt == "pytest-deselect":
        return " ".join(f"--deselect {r['selector']}" for r in rows)
    if fmt == "grep-pattern":
        # 供 `grep -vE` 用；多条以 | 连接
        return "|".join(r["selector"] for r in rows)
    if fmt == "count":
        return str(len(rows))
    if fmt == "json":
        return json.dumps(rows, ensure_ascii=False, indent=1)
    raise SystemExit(f"未知 --format：{fmt}")


def human(rows: list[dict]) -> str:
    """闸每次运行都打的那段。**扣除本身要可见**，否则它就是另一种静默。"""
    lines = [f"  本次扣除 {len(rows)} 条，清单如下："]
    if not rows:
        lines.append("    （无）")
    for r in rows:
        lines.append(f"    · [{r.get('layer')}] {r.get('id')}")
        lines.append(f"        扣谁   : {r.get('selector')}")
        lines.append(f"        为什么 : {r.get('why_red')}")
        lines.append(f"        何时删 : {r.get('when_it_should_disappear')}")
        lines.append(f"        复核人 : {r.get('reviewer')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", type=Path, default=DEFAULT_LIST)
    ap.add_argument("--layer", default="")
    ap.add_argument("--format", default="")
    ap.add_argument("--print", action="store_true", dest="do_print")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()

    rows, errors = load(args.list)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    if args.layer:
        rows = for_layer(rows, args.layer)
    if args.validate:
        print(f"deductions: {len(rows)} 条，三段式完整")
        return 0
    if args.do_print:
        print(human(rows))
        return 0
    if args.format:
        print(render(rows, args.format))
        return 0
    print(human(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
