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

import json
import logging
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile

from deeptutor.multi_user.context import get_current_user_or_none
from deeptutor.multi_user.paths import ADMIN_WORKSPACE_ROOT

logger = logging.getLogger(__name__)

# 扩展接线与用户目录都下沉到 test_workbench_paths（共用，且断开与生成面的循环）。
from deeptutor.api.routers.test_workbench_paths import (  # noqa: E402
    EXT_ROOT as _EXT_ROOT,
    IMPORT_ERROR as _IMPORT_ERROR,
    _wb,
    deliveries_root as _deliveries_root,
    drafts_root as _drafts_root,
    require_extension as _require_extension,
)

router = APIRouter()






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


# ── HAR 体检（设计稿第 2 屏）─────────────────────────────────────────────────
#
# 这一步**不调模型**，是纯本地解析，所以免费、立即出结果。
#
# 最硬的一条纪律：**HAR 原件绝不落盘。** 它含未脱敏的 Authorization / Cookie /
# 账号密码，一旦落进用户目录，后续任何一次打包、导出、备份都会把它带出去。
# 这里只在内存里解析，落盘的是**脱敏后的报告**（草稿），供后面生成用例时引用。

MAX_HAR_BYTES = 40 * 1024 * 1024
"""HAR 上传体积上限。

不是拍脑袋定的：对标 MeterSphere 时看到它的真实 issue #25162——浏览器录制的 HAR
导入直接报 Jackson `exceeds the maximum length (5000000)`，而且是**用户传完了才吃异常**，
没有前置提示。这里改成流式读、边读边计数、超限立刻拒，不等把整个文件读进内存再说。

40 MB 是权衡后的值：一次典型抓包（约 400 个请求）在 3 MB 量级，40 MB 已经是几千个请求；
再往上放，解析时的峰值内存（字符串 + 解析后的对象，几倍膨胀）就不好收场了——
这一点在本项目构建镜像时被 apt 的 OOM 教训过一次，宁可保守。
"""




async def _read_upload_capped(file: UploadFile) -> bytes:
    """流式读上传内容，超限即拒。

    分块读而不是 `await file.read()` 一把梭，就是为了在超限的那一刻就停手，
    而不是先把一个几百 MB 的文件读进内存、再回头说"太大了"。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_HAR_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"HAR 超过 {MAX_HAR_BYTES // (1024 * 1024)} MB 上限。"
                    "抓包时先按域名过滤一下，或只保留要测的那段操作再导出。"
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/har/inspect")
async def inspect_har(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传 HAR，本地体检，返回脱敏后的报告并存成草稿。

    返回里带 `draft_id`，下一步「描述场景 → 生成用例」凭它引用这份体检结果，
    不必把 HAR 再传一遍（也就不必在服务端留着原件）。
    """
    _require_extension()  # 扩展没装时先给 503，别等到 import 才炸出 ImportError
    from server import har_parse  # type: ignore[import-not-found]

    raw = await _read_upload_capped(file)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="HAR 不是 UTF-8 文本。它应当是浏览器导出的 .har（JSON），不是压缩包或二进制。",
        ) from exc

    report = har_parse.parse_har_report(har_content=content)
    # parse_har_report 出错时返回带 error 字段的结果而不抛异常（工具边界的既定口径），
    # 这里把它翻成 HTTP 语义，同时把它给的 hint 一起带出去——那句 hint 是给人看的。
    if not report.get("ok", False):
        raise HTTPException(
            status_code=400,
            detail={
                "error": report.get("error"),
                "message": report.get("message"),
                "hint": report.get("hint"),
            },
        )

    draft_id = f"har-{uuid4().hex[:12]}"
    draft_path = _drafts_root() / f"{draft_id}.json"
    draft_path.write_text(
        json.dumps({"draft_id": draft_id, "source_name": file.filename, "report": report},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "draft_id": draft_id,
        "source_name": file.filename,
        "report": report,
        # 界面上必须如实说，不许写成"已全部脱敏"——凭证换成了占位，但身份证/手机号/
        # 邮箱这类个人信息不在脱敏词表范围内（缺陷 BB-424，open）。
        "redaction_notice": {
            "credentials_redacted": True,
            "pii_redacted": False,
            "defect": "BB-424",
            "message": "凭证已换成变量占位；身份证、手机号、邮箱这类个人信息暂不在脱敏范围内，分享产物前请自行检查。",
        },
    }


@router.get("/har/drafts/{draft_id}")
def get_har_draft(draft_id: str) -> dict[str, Any]:
    """取回一份体检草稿。id 走与批次同一套校验，非法直接拒。"""
    wb = _require_extension()
    try:
        safe = wb.safe_delivery_id(draft_id)
    except wb.WorkbenchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = _drafts_root() / f"{safe}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"草稿 {safe} 不存在。")
    return json.loads(path.read_text(encoding="utf-8"))


# ── 生成面 ────────────────────────────────────────────────────────────────
# 会花钱的接口单独成文件（`test_workbench_generate.py`），挂在这里。
# 分开的理由是审查面：想知道"哪些接口会调模型"，读那一个文件就够。
#
# 放在文件末尾而不是顶部 import：那个模块反过来要 import 本模块的
# `_drafts_root` / `_require_extension`，顶部导入会成环。
def _include_generate_routes() -> None:
    from deeptutor.api.routers import test_workbench_generate

    router.include_router(test_workbench_generate.router)


_include_generate_routes()
