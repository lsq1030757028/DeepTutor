"""测试工作台的共用地基：扩展接线 + 当前用户的目录。

## 为什么单独一个模块

`test_workbench.py`（只读面）与 `test_workbench_generate.py`（花钱面）都要用
这几样东西，而后者又要被前者 include 进去。两边互相 import 会成环——
**实际撞到过**：谁先被导入，谁在对方眼里就是"部分初始化"的模块。

把共用部分下沉到这里，两边都只依赖它、彼此不依赖，环就断了。
这是结构上的正解，不是调整 import 位置能解决的。

## sys.path 引导

`extensions/test-partner/server/` 内部用绝对导入（`from server import ...`），
而 `extensions` 不是 Python 包（目录名带连字符，也不在 `pyproject.toml` 的打包范围）。
这里把它塞进 `sys.path`，好处是 `server/` 下那七百多例测试与全部导入语句一行不用改。

代价是一次显式的 `sys.path` 变更——**收敛在这一个文件里**，就是为了它可被审查。
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

from fastapi import HTTPException

from deeptutor.multi_user.context import get_current_user_or_none
from deeptutor.multi_user.paths import ADMIN_WORKSPACE_ROOT

logger = logging.getLogger(__name__)

EXT_ROOT = Path(__file__).resolve().parents[3] / "extensions" / "test-partner"
if EXT_ROOT.is_dir() and str(EXT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXT_ROOT))

try:
    from server.gateway import workbench as _wb  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - 只在扩展没打进镜像时触发
    _wb = None
    IMPORT_ERROR: Exception | None = exc
    logger.warning(
        "测试工作台扩展未加载：%s（找过 %s）。"
        "镜像里没有 extensions/ 时会走到这里——Dockerfile 的 COPY 漏了。",
        exc, EXT_ROOT,
    )
else:
    IMPORT_ERROR = None


def require_extension() -> Any:
    if _wb is None:
        raise HTTPException(status_code=503,
                            detail=f"测试工作台扩展未加载：{IMPORT_ERROR}")
    return _wb


def _user_root() -> Path:
    """当前用户的 scope 根。

    admin 是 `data/`，普通用户是 `data/users/<uid>/`——两者都由 DeepTutor 给出。
    这里**不自己拼用户 id**，免得和平台的迁移逻辑各说各话。
    """
    user = get_current_user_or_none()
    return Path(user.scope.root) if user is not None else ADMIN_WORKSPACE_ROOT


def owner_id() -> str:
    """当前用户的 id；没有当前用户时用 admin 兜底。

    与平台 `LOCAL_ADMIN_ID` 同值（"local-admin"）。生成任务台账、执行台账、
    环境金库都拿它当分区键——三处必须同一口径，所以住在这里。
    """
    user = get_current_user_or_none()
    return str(getattr(user, "id", "") or "local-admin")


def deliveries_root() -> str:
    """当前用户的交付批次目录（决策 0009：按用户隔离）。"""
    root = _user_root() / "test-workbench" / "deliveries"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def drafts_root() -> Path:
    """当前用户的体检草稿目录。与交付批次同层，都在用户 scope 之下。"""
    root = _user_root() / "test-workbench" / "drafts"
    root.mkdir(parents=True, exist_ok=True)
    return root
