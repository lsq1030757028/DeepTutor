"""执行、导出与测试环境的接口（工作台第 6-9 屏）。

## 与其他 workbench 路由的分工

`test_workbench.py` 是只读面，`test_workbench_generate.py` 是花钱面（调模型），
本文件是**发真实 HTTP 请求的面**——执行用例会往被测环境打真流量。
想审"哪些接口会对外发请求"，读这一个文件就够。

## 测试环境金库放哪（决策 0011 落点二）

环境（名字 + base_url + 变量键值）整份落在
`owner_secrets_dir(owner_id)/test-workbench/environments.json`：

- **值必须进平台机密目录**是 0011 拍板的——`owner_secrets_dir` 按 owner 寻址、
  chmod 0700、位于 exec 沙箱唯一不挂载的分支，每用户凭据隔离在平台层已解决。
- **名字与 base_url 跟着值同住一个文件**是本文件的判断（自决，待审计）：
  0011 落点三说非敏感部分"落在用户 scope 下"，但拆两个文件意味着改一个环境要
  原子改两处——`gateway/config.py` 的模块注释里论证过这正是会出错的地方。
  整份放进机密目录只是把非敏感数据保护过头，没有反向风险。

存取直接复用扩展的 `GatewayConfig`（每 owner 一个实例、各指各的目录）：
校验、原子写、去值投影一行不重写。它的 `secrets.env` 白名单面在这里用不到，
不碰即可——0011 已裁定 TAPD 令牌走平台 MCP 配置面，不在这里。

## 执行为什么能用线程（对照 generate 的 asyncio 说明）

`RunRegistry` 在后台线程里跑 `execute_cases`，而线程不继承 ContextVar——
但这里**没有串数据风险**：批次目录、环境金库都在**发起请求的当下**（ContextVar
还活着）解析成绝对路径/实例，绑进该 owner 的 registry；线程里跑的代码
不再读任何"当前用户"。generate 必须用 asyncio 是因为它的落盘发生在任务里，
这里的落盘路径在任务开始前就定死了。

## 凭据

本文件**碰不到任何凭据值**：保存环境时值从请求体直达 `GatewayConfig`
（不进日志、不进返回值），执行时只把环境名交给 `execute_cases`，
解析发生在扩展层，出站结果是它脱敏后的那份。
"""

from __future__ import annotations

from functools import partial
import io
import logging
import threading
from typing import Any
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from deeptutor.api.routers.test_workbench_paths import (
    deliveries_root as _deliveries_root,
    owner_id as _owner,
    require_extension as _require_extension,
)
from deeptutor.multi_user.paths import owner_secrets_dir

logger = logging.getLogger(__name__)

router = APIRouter()

#: 每 owner 一个环境金库实例 / 一个执行台账。都在内存里按需建：
#: 金库本体是磁盘文件（重启不丢），台账是过程量（重启即清，历史看执行报告）。
_STORES: dict[str, Any] = {}
_REGISTRIES: dict[str, Any] = {}
_LOCK = threading.Lock()


def _env_store() -> Any:
    """当前用户的环境金库（`GatewayConfig`，根在平台机密目录下）。"""
    _require_extension()
    from server.gateway.config import GatewayConfig  # type: ignore[import-not-found]

    owner = _owner()
    with _LOCK:
        store = _STORES.get(owner)
        if store is None:
            store = GatewayConfig(
                config_dir=str(owner_secrets_dir(owner) / "test-workbench"))
            _STORES[owner] = store
        return store


def _registry() -> Any:
    """当前用户的执行台账。批次目录与环境金库在此刻（请求上下文内）绑定。"""
    wb = _require_extension()
    from server import execute  # type: ignore[import-not-found]

    owner = _owner()
    store = _env_store()
    root = _deliveries_root()
    with _LOCK:
        registry = _REGISTRIES.get(owner)
        if registry is None:
            registry = wb.RunRegistry(
                executor=partial(execute.execute_cases, env_store=store),
                deliveries_root_dir=root)
            _REGISTRIES[owner] = registry
        return registry


def _wb_error(exc: Any) -> HTTPException:
    """WorkbenchError → HTTP。code 一并带出去，页面按它分支。"""
    return HTTPException(status_code=400, detail={
        "code": getattr(exc, "code", "WORKBENCH_ERROR"), "message": str(exc)})


# ── 测试环境与变量（设计稿第 9 屏） ──────────────────────────────────────────

class EnvironmentBody(BaseModel):
    name: str = Field(..., min_length=1)
    base_url: str = ""
    note: str = ""
    #: `[{key, value}]`。**编辑时值留空 = 保留原值**——页面拿到的是去值投影，
    #: 值输入框必然是空的，把空值当"清空"会让每次编辑都抹掉全部凭据。
    variables: list[dict[str, Any]] = Field(default_factory=list)
    #: 改名式编辑时带旧名字（按旧名定位记录）。
    original_name: str = ""


@router.get("/environments")
def list_environments() -> dict[str, Any]:
    """当前用户的环境清单（**去值投影**：变量只出键名与掩码）。"""
    store = _env_store()
    return {"ok": True, "environments": store.environments_public()}


@router.post("/environments")
def save_environment(body: EnvironmentBody) -> dict[str, Any]:
    """新增/编辑一个环境。值只在本函数内流动，不进返回值、不进日志。"""
    store = _env_store()
    from server.gateway.config import (  # type: ignore[import-not-found]
        EnvironmentConfigError,
        normalize_variables,
    )

    try:
        variables = normalize_variables(body.variables)
        previous = store.get_environment(body.original_name.strip() or body.name)
        if previous:
            for key, value in list(variables.items()):
                if not value and key in previous["variables"]:
                    variables[key] = previous["variables"][key]
        saved = store.upsert_environment(
            {"name": body.name, "base_url": body.base_url,
             "note": body.note, "variables": variables},
            original_name=body.original_name)
    except EnvironmentConfigError as exc:
        raise HTTPException(status_code=400, detail={
            "code": exc.code, "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail={
            "code": "SAVE_FAILED",
            "message": f"写环境配置失败：{exc}"}) from exc

    logger.info("workbench: 环境「%s」已保存（%d 个变量，值不记录）",
                saved["name"], len(saved["variables"]))
    return {"ok": True, "saved": saved["name"],
            "environments": store.environments_public()}


class EnvironmentDeleteBody(BaseModel):
    name: str = Field(..., min_length=1)


@router.post("/environments/delete")
def delete_environment(body: EnvironmentDeleteBody) -> dict[str, Any]:
    """删一个环境（名字走请求体，不走 URL——中文环境名进路径是自找编码问题）。"""
    store = _env_store()
    if not store.delete_environment(body.name):
        raise HTTPException(status_code=404, detail={
            "code": "ENV_NOT_FOUND", "message": f"没有名为「{body.name}」的环境。"})
    return {"ok": True, "environments": store.environments_public()}


# ── 执行（设计稿第 8 屏） ───────────────────────────────────────────────────

class RunBody(BaseModel):
    env: str = Field(..., min_length=1)
    case_ids: list[str] = Field(default_factory=list)
    #: 「跟随环境」= env（默认）、「登录换新」= login。none 不上页面。
    auth: str = "env"
    timeout_s: int = 15


@router.post("/deliveries/{delivery_id}/runs")
def start_run(delivery_id: str, body: RunBody) -> dict[str, Any]:
    """开一轮执行，立刻返回 run 句柄。入参闸全在 `RunRegistry.start` 里。"""
    wb = _require_extension()
    try:
        return _registry().start(
            delivery_id=delivery_id, env=body.env, case_ids=body.case_ids,
            timeout_s=body.timeout_s, auth=body.auth)
    except wb.WorkbenchError as exc:
        raise _wb_error(exc) from exc


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    """轮询一轮执行。只查**当前用户自己的**台账——别人的 run id 在这里就是 404。"""
    run = _registry().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="这轮执行不存在（或已被清出台账）。")
    return run


@router.get("/deliveries/{delivery_id}/runs/active")
def active_run(delivery_id: str) -> dict[str, Any]:
    """这个批次上有没有正在跑的一轮（页面刷新后靠它接回进度条）。"""
    wb = _require_extension()
    try:
        safe = wb.safe_delivery_id(delivery_id)
    except wb.WorkbenchError as exc:
        raise _wb_error(exc) from exc
    return {"run": _registry().active_run_for(safe)}


# ── 导出与下载（设计稿第 7 屏） ─────────────────────────────────────────────

class ExportBody(BaseModel):
    #: 页面四张卡的多选结果，如 ["xlsx", "postman"]。
    formats: list[str] = Field(..., min_length=1)
    #: 导出产物里的个人信息换成保形占位符（BB-424）。**默认开**——
    #: 安全默认不该要求用户先知道有这么个开关；要带真实测试数据的可显式关。
    redact_pii: bool = True


@router.post("/deliveries/{delivery_id}/export")
def export_delivery(delivery_id: str, body: ExportBody) -> dict[str, Any]:
    """把批次（重新）写成所选格式，产物落在批次目录内，返回文件清单。"""
    wb = _require_extension()
    try:
        return wb.export_delivery(delivery_id, body.formats, _deliveries_root(),
                                  redact_pii=body.redact_pii)
    except wb.WorkbenchError as exc:
        raise _wb_error(exc) from exc


@router.get("/deliveries/{delivery_id}/files/{filename}")
def download_file(delivery_id: str, filename: str) -> Response:
    """下载批次目录里的一个产物。文件名校验在扩展层（路径穿越在那里就地拒）。"""
    wb = _require_extension()
    try:
        path = wb.delivery_file_path(delivery_id, filename, _deliveries_root())
    except wb.WorkbenchError as exc:
        raise _wb_error(exc) from exc
    return FileResponse(path, filename=filename,
                        media_type="application/octet-stream")


@router.get("/deliveries/{delivery_id}/archive")
def download_archive(delivery_id: str, files: str = "") -> Response:
    """把若干产物打成一个 zip（设计稿：多选导出时"打包成一个 zip"）。

    `files` 是逗号分隔的文件名。整包在内存里组——产物是用例表和 collection，
    量级是几十 KB，为它上流式打包属于过度设计。
    """
    wb = _require_extension()
    names = [n.strip() for n in files.split(",") if n.strip()]
    if not names:
        raise HTTPException(status_code=400, detail="没说要打包哪些文件。")
    root = _deliveries_root()
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(wb.delivery_file_path(delivery_id, name, root),
                              arcname=name)
    except wb.WorkbenchError as exc:
        raise _wb_error(exc) from exc
    safe_id = wb.safe_delivery_id(delivery_id)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_id}.zip"'})
