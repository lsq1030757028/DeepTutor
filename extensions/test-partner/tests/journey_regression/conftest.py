# -*- coding: utf-8 -*-
"""journey_regression — 本 spec 防复发判据的可执行样例集（批次 6 / spec r3）。

## 这个目录是什么

`deeptutor-test-positioning-20260818` 那轮重定位里，每条**防复发**判据在这里留
一个可执行样例（AC11 / AC13 / AC14 / AC16 / AC21）。样例分两类：

- **服务端契约回放**：把 F-3 那场审讯的症状形态直接打在服务端契约上，
  契约必须拒绝（AC11/AC13/AC14 的机械半边）；
- **提示词锚**：直接读 DT 侧 prompt yaml 文件断锚（AC16/AC21 的纪律半边）。
  文件级断言刻意不 import DT 运行时——扩展 venv 不背 DT 依赖集，
  而「锚在不在文件里」不需要运行时。

## 诚实分级（照抄 spec 的话）

判断在模型 ⇒ 判据也在行为。这里能钉死的是「纪律真的在提示词里」与
「服务端契约真的会拒绝」；**「模型真的照着做」只有 UAT 会话能证**。
不要拿本目录的绿当行为保证。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

#: 仓库根（extensions/test-partner/tests/journey_regression/ 上翻四级）
REPO_ROOT = Path(__file__).resolve().parents[4]
PROMPTS_DIR = REPO_ROOT / "deeptutor" / "agents" / "test" / "prompts"


@pytest.fixture(params=["zh", "en"])
def prompt_body(request) -> str:
    """DT 测试模式提示词全文（两语参数化，文件级读取不 import DT 运行时）。"""
    path = PROMPTS_DIR / request.param / "test_journey.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return "\n".join(str(v) for v in doc.values() if isinstance(v, str))
