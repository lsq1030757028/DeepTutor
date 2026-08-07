"""测试工作台的后端接口（二开扩展，不是上游模块）。

本文件是 DeepTutor 与 `extensions/test-partner` 之间**唯一**的接线点。
设计稿见 `extensions/test-partner/docs/design/workbench-full.html`（决策 0010 通过）。

## 为什么要动 sys.path

`extensions/test-partner/server/` 里的模块之间用绝对导入（`from server import ...`），
而 `extensions` 不是 Python 包（目录名带连字符，也不在 `pyproject.toml` 的打包范围内）。
测试期靠 `extensions/test-partner/conftest.py` 把该目录塞进 `sys.path` 才解析得了。

这里做同一件事，好处是 `server/` 下 670 例测试与全部导入语句**一行都不用改**，
搬迁保持"纯搬不改"。代价是一次显式的 `sys.path` 变更——写在这里而不是散落各处，
就是为了让它只有一个位置、可被审查。

## 每用户隔离（决策 0009）

DeepTutor 自带按用户的工作区（`deeptutor/multi_user/paths.py` 的 `UserScope`），
交付批次落在 `<用户 scope 根>/test-workbench/deliveries/` 下。

`extensions/test-partner/server/gateway/workbench.py` 的每个函数本来就接受可选的
`root` 参数，所以隔离**不需要改那个模块**——把当前用户的目录传进去即可。

⚠️ 本 router 在 `main.py` 里必须带 `dependencies=_auth` 注册。裸挂（不带依赖）时
`CurrentUser` 的 ContextVar 不会被安装，落盘会**静默写进 admin 工作区且不报错**
（见 `deeptutor/api/routers/auth.py` 的 `_install_current_user`）。
隔离形态下这不是"不整洁"，是串数据。
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

from fastapi import APIRouter, HTTPException

from deeptutor.multi_user.context import get_current_user_or_none
from deeptutor.multi_user.paths import ADMIN_WORKSPACE_ROOT

logger = logging.getLogger(__name__)

# ── extensions 接线：把 test-partner 目录塞进 sys.path，见模块 docstring ──────
_EXT_ROOT = Path(__file__).resolve().parents[3] / "extensions" / "test-partner"
if _EXT_ROOT.is_dir() and str(_EXT_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXT_ROOT))

try:
    from server.gateway import workbench as _wb  # type: ignore[import-not-found]
except ImportError as exc:  # pragma: no cover - 只在扩展没打进镜像时触发
    _wb = None
    _IMPORT_ERROR = exc
    logger.warning(
        "测试工作台扩展未加载：%s（找过 %s）。"
        "镜像里没有 extensions/ 时会走到这里——Dockerfile 的 COPY 漏了。",
        exc, _EXT_ROOT,
    )
else:
    _IMPORT_ERROR = None

router = APIRouter()


def _deliveries_root() -> str:
    """当前用户的交付批次目录。

    admin 的 scope 根是 `data/`，普通用户是 `data/users/<uid>/`——两者都由
    DeepTutor 给出，这里只在其下再开一层 `test-workbench/deliveries`，
    不自己拼用户 id，免得和平台的迁移逻辑各说各话。
    """
    user = get_current_user_or_none()
    base = Path(user.scope.root) if user is not None else ADMIN_WORKSPACE_ROOT
    root = base / "test-workbench" / "deliveries"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _require_extension() -> Any:
    if _wb is None:
        raise HTTPException(
            status_code=503,
            detail=f"测试工作台扩展未加载：{_IMPORT_ERROR}",
        )
    return _wb


@router.get("/health")
def health() -> dict[str, Any]:
    """扩展是否装上了。

    骨架阶段就要有这一条：镜像里没有 `extensions/` 时，除了这条之外的接口
    全是 503，光看 503 分不清是"没装"还是"坏了"。这条把两者分开。
    """
    return {
        "extension_loaded": _wb is not None,
        "extension_path": str(_EXT_ROOT),
        "error": str(_IMPORT_ERROR) if _IMPORT_ERROR else None,
    }


@router.get("/deliveries")
def list_deliveries() -> dict[str, Any]:
    """当前用户的交付批次列表。别人的看不到。"""
    wb = _require_extension()
    try:
        return wb.scan_deliveries(_deliveries_root())
    except wb.WorkbenchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/deliveries/{delivery_id}")
def get_delivery(delivery_id: str) -> dict[str, Any]:
    """一个批次的详情（元信息 + 用例表）。"""
    wb = _require_extension()
    try:
        return wb.read_delivery(delivery_id, _deliveries_root())
    except wb.WorkbenchError as exc:
        # 批次 id 非法或不存在都走这里。id 校验在 workbench.safe_delivery_id 里，
        # 非法直接抛而不是"清洗后继续"——那是路径穿越的常见入口。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
