"""工作台后端：「批次 → 用例 → 执行 → 结果」这条线。

M3.6 第一版（0.7.0）。用户拍板的一句话是
`HAR先放过吧，后续把变量、交互这些做好，再接入吧，我不想基于会话体验了`——
固定工作流不该靠聊天来驱动：勾几条用例、选个环境、点执行、看结果表，
这些动作用鼠标一秒完成的事，让模型转述三轮既慢又不可靠。

调研结论（决策 0006 补记与 PLAN M3.6）：DeepTutor 侧没有任何 UI 扩展点，
MCP 工具的返回值被压成纯文本，markdown 层会把 iframe 转义掉。所以**交互只能长在
我们自己的页面里**，工作台就是配置页的第五个 tab。

## 本模块只做三件事

1. **扫批次**：`deliveries/` 下每个目录一个批次，元信息读 `receipt.json`。
2. **读用例**：读批次里的 `cases.json`（0.7.0 起 `save_delivery` 无条件落的那份）。
3. **管执行**：把 `server/execute.py` 的 `execute_cases` 放进后台线程跑，
   进度与结果记在内存里供轮询。

**执行逻辑一行都不重写**——本模块不认识 HTTP，只认识"哪些用例、哪个环境"。
另写一份 HTTP 客户端就意味着 `execute.py` 那四条安全红线（跨 host 不发、
变量缺失不发、凭据不出流、不判绿不猜）要在第二个地方重新实现一遍，
两份实现迟早会漂。

## 旧批次怎么办

0.7.0 之前的批次目录里没有 `cases.json`。反推不是好主意：xlsx 里没有 `request` 块，
collection 里的断言已经被编译成 pm.test 的 JS 文本，反解等于自己写个 JS 解释器，
而且解错了会**静默地执行错的断言**。所以旧批次明说"没有结构化数据"，
只列产物路径，执行按钮不给点——降级要看得见，不要假装能用。

## 凭据

本模块**碰不到任何凭据值**：执行时只把「环境名」传给 `execute_cases`，
值由它在网关进程内向配置中心解析（`server/gateway/config.py` 的注释里写了为什么）。
返回给页面的执行结果是 `execute_cases` 出站脱敏之后的那一份，本模块不做二次加工。

## 鉴权方式（0.8）

执行面上的「鉴权方式」两个选项直接对应 `execute_cases` 的 `auth` 参数：
「跟随环境」= `env`（现状），「登录换新」= `login`。选后者时本模块从批次的
`cases.json` 里取 `login_request`（`save_delivery` 落盘时带进去的那份，账密是占位符）
交给执行层——**批次里没有这份描述就不给起跑**，理由说清楚，别让用户点了没反应。
账密仍然只在配置页的环境变量表里，本模块照旧一个值都碰不到。
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import re
import tempfile
import threading
import time
from typing import Any, Iterator

from server import case_validate, delivery, execute

log = logging.getLogger("test-partner.gateway")

#: 批次目录名的时间戳前缀（`20260805-111325-标题`），用来排序
_STAMP_RE = re.compile(r"^(\d{8})-(\d{6})")

#: 目录名里不许出现的东西：路径分隔符与上跳。批次 id 从 URL 里来，
#: 它只能是 `deliveries/` 下的一级目录名，不是一个可以随便拼的路径。
_UNSAFE_ID_RE = re.compile(r"[\\/]|^\.{1,2}$|^\s*$")

#: 内存里最多留几轮执行记录。轮询完就没人要了，留着只是给用户按刷新用。
MAX_RUNS_KEPT = 20

#: 执行报告文件名（与 `server/execute.py` 的 `_write_reports` 同源）
EXECUTION_REPORT_JSON = "execution_report.json"

#: 页面上「鉴权方式」两个选项 → `execute_cases` 的 auth 值（`none` 不上页面：
#: 它是给跑公开接口的调用方用的，勾进 UI 只会让人误选成"什么都不带"）
AUTH_MODES_ON_PAGE = ("env", "login")


class WorkbenchError(ValueError):
    """工作台可预期的错误。`code` 给页面做分支，`args[0]` 是给人看的中文。"""

    def __init__(self, message: str, code: str = "WORKBENCH_ERROR") -> None:
        super().__init__(message)
        self.code = code


# ── 批次扫描 ────────────────────────────────────────────────────────────────

def deliveries_root(root: str | None = None) -> str:
    return os.path.abspath(root or delivery.DELIVERIES_DIR)


def safe_delivery_id(raw: Any) -> str:
    """批次 id 校验。非法直接抛——不做"清洗后继续"，那是路径穿越的常见入口。"""
    text = str(raw or "").strip()
    if not text or _UNSAFE_ID_RE.search(text) or os.path.isabs(text):
        raise WorkbenchError(f"批次编号「{text}」不合法。", code="BAD_DELIVERY_ID")
    return text


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _list_files(dir_path: str) -> list[dict[str, Any]]:
    """批次目录里的文件清单（绝对路径 + 字节数）。页面只给路径+复制，不做下载。"""
    rows: list[dict[str, Any]] = []
    try:
        names = sorted(os.listdir(dir_path))
    except OSError:
        return rows
    for name in names:
        full = os.path.join(dir_path, name)
        if not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        rows.append({"name": name, "path": full, "bytes": size})
    return rows


def _stamp_of(dir_name: str) -> str:
    match = _STAMP_RE.match(dir_name)
    return (match.group(1) + match.group(2)) if match else ""


def describe_delivery(dir_path: str) -> dict[str, Any]:
    """一个批次目录 → 列表页要的那点元信息。**坏 receipt 不许让整页塌。**"""
    dir_name = os.path.basename(dir_path.rstrip(os.sep))
    receipt_path = os.path.join(dir_path, "receipt.json")
    receipt = _read_json(receipt_path) if os.path.isfile(receipt_path) else None
    receipt_broken = os.path.isfile(receipt_path) and not isinstance(receipt, dict)
    receipt = receipt if isinstance(receipt, dict) else {}

    cases_path = os.path.join(dir_path, delivery.CASES_FILE)
    has_cases = os.path.isfile(cases_path)

    case_count = receipt.get("case_count")
    if not isinstance(case_count, int):
        case_count = None

    report = _read_json(os.path.join(dir_path, EXECUTION_REPORT_JSON))
    last_execution = None
    if isinstance(report, dict):
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        last_execution = {
            "executed_at": str(report.get("executed_at") or ""),
            "environment": str(report.get("environment") or ""),
            "verdict": str(summary.get("verdict") or ""),
            "passed": summary.get("passed"),
            "failed": summary.get("failed"),
            "skipped": summary.get("skipped"),
        }

    return {
        "id": dir_name,
        "path": os.path.abspath(dir_path),
        "title": str(receipt.get("title") or dir_name),
        "generated_at": str(receipt.get("generated_at") or ""),
        "format": str(receipt.get("format") or ""),
        "case_count": case_count,
        "validation_ok": (receipt.get("validation") or {}).get("ok")
                         if isinstance(receipt.get("validation"), dict) else None,
        "structured": has_cases,
        "receipt_ok": bool(receipt) and not receipt_broken,
        "receipt_broken": receipt_broken,
        "executed": last_execution is not None,
        "last_execution": last_execution,
        "files": _list_files(dir_path),
        "sort_key": _stamp_of(dir_name),
    }


def scan_deliveries(root: str | None = None) -> dict[str, Any]:
    """扫 `deliveries/` → 批次列表（时间倒序）。目录不存在 = 空态，不是错误。"""
    base = deliveries_root(root)
    if not os.path.isdir(base):
        return {"ok": True, "deliveries_dir": base, "deliveries": [],
                "empty_reason": "还没有任何交付批次——deliveries/ 目录还不存在。"}
    rows: list[dict[str, Any]] = []
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        if os.path.isdir(full):
            rows.append(describe_delivery(full))
    # 时间戳前缀是目录名的一部分（`save_delivery` 造的），拿它排序比读文件 mtime 稳：
    # 复制过来的目录 mtime 会变，名字不会。没有前缀的目录排最后。
    rows.sort(key=lambda row: (row["sort_key"], row["id"]), reverse=True)
    out: dict[str, Any] = {"ok": True, "deliveries_dir": base, "deliveries": rows}
    if not rows:
        out["empty_reason"] = ("deliveries/ 目录是空的。让测试伙伴生成一批用例"
                               "（save_delivery 落盘）之后，这里就会出现批次。")
    return out


# ── 用例读取 ────────────────────────────────────────────────────────────────

def _assertion_count(request: Any) -> int:
    if not isinstance(request, dict):
        return 0
    assertions = request.get("assertions")
    return len(assertions) if isinstance(assertions, (list, tuple)) else 0


def _as_text_list(value: Any) -> list[str]:
    """列表字段规整。字符串不算列表——`for s in "abc"` 会拆成三个字符。"""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def required_vars(request: Any) -> list[str]:
    """一条用例要用到的变量名（去重保序）。`baseUrl` 不算——它来自环境的地址栏，
    不是变量表里的一项，混进去会让用户以为自己还差配一个变量。

    复用 `execute.missing_vars` 而不是另写一个正则：那个函数是执行层判"变量缺失
    不发请求"的同一个实现，两处若各写各的，界面说"齐了"而执行说"缺"就成了必然。
    传空 mapping 即得到"全部被引用的变量"。
    """
    if not isinstance(request, dict):
        return []
    texts = [str(request.get("url") or "")]
    headers = request.get("headers")
    if isinstance(headers, (list, tuple)):
        for h in headers:
            if isinstance(h, dict):
                texts.append(str(h.get("value", "")))
            elif isinstance(h, str):
                texts.append(h)
    body = request.get("body")
    if isinstance(body, dict) and body.get("raw") is not None:
        raw = body["raw"]
        texts.append(raw if isinstance(raw, str)
                     else json.dumps(raw, ensure_ascii=False))
    out: list[str] = []
    for text in texts:
        for name in execute.missing_vars(text, {}):
            if name != execute.BASE_URL_VAR and name not in out:
                out.append(name)
    return out


def _case_row(case: Any, index: int) -> dict[str, Any]:
    """一条用例 → 表格行 + 展开详情。**请求块原样带出，`{{变量}}` 不解析。**

    页面上要看得见凭据"放在哪个位置"（哪个头、哪个字段），但看不见值——
    值压根不在这份文件里，它在配置页的测试环境里，执行时才在网关进程内解析。
    """
    if not isinstance(case, dict):
        return {"case_id": f"#{index + 1}", "title": "(这条用例不是对象，读不出来)",
                "module": "", "priority": "", "executable": False,
                "assertion_count": 0, "broken": True}
    request = case.get("request")
    return {
        "case_id": str(case.get("case_id") or f"#{index + 1}"),
        "title": str(case.get("title") or "(无标题)"),
        "module": str(case.get("module") or ""),
        "priority": str(case.get("priority") or ""),
        "case_type": str(case.get("case_type") or ""),
        "preconditions": str(case.get("preconditions") or ""),
        "steps": _as_text_list(case.get("steps")),
        "expected": str(case.get("expected") or ""),
        "test_data": str(case.get("test_data") or ""),
        "endpoints": _as_text_list(case.get("endpoints")),
        "request": request if isinstance(request, dict) else None,
        "executable": isinstance(request, dict) and _assertion_count(request) > 0,
        "assertion_count": _assertion_count(request),
        # 这条用例引用了哪些变量。**页面拿它与所选环境的键名做差集**算"还缺哪些"——
        # 差集在前端算，切换环境下拉框就能立刻更新，不必回后端再问一次。
        "required_vars": required_vars(request),
        # 这条是模型写的还是人改过的（0012 ADR-2 的留痕）。缺字段的旧批次按 ai 处理：
        # 它们成文时还没有编辑功能，不可能是人改的。
        "origin": str(case.get("origin") or "ai"),
        "broken": False,
    }


def _login_request_of(payload: Any) -> dict[str, Any] | None:
    """`cases.json` 里的登录请求描述。没有 / 不是对象都返回 None。"""
    if not isinstance(payload, dict):
        return None
    spec = payload.get("login_request")
    return spec if isinstance(spec, dict) and spec else None


def _auth_refresh_offer(payload: Any) -> dict[str, Any]:
    """页面上「登录换新」这个选项该不该给点 →｛available, endpoint, reason｝。

    只出"能不能 + 端点长什么样"，登录请求描述本身留在网关侧。
    """
    spec = _login_request_of(payload)
    if spec is None:
        return {
            "available": False,
            "reason": ("这个批次落盘时没带登录请求描述，所以给不出「登录换新」。"
                       "让测试伙伴重跑一次 HAR 链路：体检报告里 "
                       "replay.auth_refresh.supported 为 true 时，"
                       "save_delivery 会把登录请求一并存进批次。"),
        }
    path = str(spec.get("path") or spec.get("url") or "")
    return {
        "available": True,
        "endpoint": f"{str(spec.get('method') or '').upper()} {path}".strip(),
        "reason": ("执行前先重放这个登录请求换一个新 token，注入后续用例。"
                   "账号口令取环境变量表里的 login_username / login_password——"
                   "没配这两个变量会整轮不执行并说明原因。"),
    }


def read_delivery(delivery_id: str, root: str | None = None) -> dict[str, Any]:
    """一个批次的详情（元信息 + 用例表）。旧批次走降级分支，不反推、不假装。"""
    safe_id = safe_delivery_id(delivery_id)
    dir_path = os.path.join(deliveries_root(root), safe_id)
    if not os.path.isdir(dir_path):
        raise WorkbenchError(f"没有名为「{safe_id}」的交付批次。",
                             code="DELIVERY_NOT_FOUND")

    info = describe_delivery(dir_path)
    result: dict[str, Any] = {"ok": True, "delivery": info, "cases": [],
                              "structured": False, "degraded": None}

    cases_path = os.path.join(dir_path, delivery.CASES_FILE)
    if not os.path.isfile(cases_path):
        result["degraded"] = {
            "code": "NO_CASES_JSON",
            "message": ("该批次没有结构化用例数据（缺 cases.json），"
                        "只能查看产物路径。cases.json 是 0.7.0 起随每次交付落盘的，"
                        "这之前的批次没有——重新生成一批即可在工作台里勾选执行。"),
        }
        return result

    payload = _read_json(cases_path)
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, (list, tuple)):
        result["degraded"] = {
            "code": "CASES_JSON_BROKEN",
            "message": (f"读不出 {delivery.CASES_FILE} 里的用例数组（文件坏了或格式不对），"
                        "只能查看产物路径。"),
        }
        return result

    # 「登录换新」能不能选，看这个批次落盘时带没带登录请求描述。
    # 页面只拿到"能不能选 + 登录端点是什么"，那份描述本身不出网关——
    # 它虽然只有占位符，但没有任何理由让浏览器多拿一份。
    result["auth_refresh"] = _auth_refresh_offer(payload)
    result["cases"] = [_case_row(row, i) for i, row in enumerate(rows)]
    result["structured"] = True
    result["case_count"] = len(result["cases"])
    result["executable_count"] = sum(1 for c in result["cases"] if c["executable"])
    result["modules"] = sorted({c["module"] for c in result["cases"] if c["module"]})
    return result


def load_cases_for_execution(delivery_id: str, root: str | None = None) -> tuple:
    """→（批次目录, 原始用例数组, 登录请求描述或 None）。喂给 `execute_cases` 的就是原文。

    刻意不喂 `read_delivery` 出来的表格行：那是给页面看的投影，字段名虽然同源，
    但多绕一层就多一个"投影时漏了某个字段"的可能。执行要的是落盘时那份原文。
    """
    safe_id = safe_delivery_id(delivery_id)
    dir_path = os.path.join(deliveries_root(root), safe_id)
    cases_path = os.path.join(dir_path, delivery.CASES_FILE)
    if not os.path.isdir(dir_path):
        raise WorkbenchError(f"没有名为「{safe_id}」的交付批次。",
                             code="DELIVERY_NOT_FOUND")
    if not os.path.isfile(cases_path):
        raise WorkbenchError(
            "该批次没有结构化用例数据（缺 cases.json），无法在工作台里执行。"
            "重新让测试伙伴生成一批用例即可。", code="NO_CASES_JSON")
    payload = _read_json(cases_path)
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, (list, tuple)) or not rows:
        raise WorkbenchError(f"{delivery.CASES_FILE} 里没有可执行的用例数组。",
                             code="CASES_JSON_BROKEN")
    return dir_path, list(rows), _login_request_of(payload)


# ── 变量反查：这个变量被谁用着（闭环稿 B2 屏） ──────────────────────────────

def variable_usage(root: str | None = None) -> dict[str, Any]:
    """扫全部批次 →「变量名 → 哪些批次的哪几条用例在用它」。

    为什么要有它：变量在配置页是个孤立的键值对，用户问的是"我配这个有什么用"。
    没有这张反查表，答案只能靠人去每个批次里翻。

    实现上就是把每个批次的 `required_vars` 汇总反转。全表扫描——批次是几十个量级，
    每个 cases.json 几十 KB，一次扫描远快于为它建索引再维护索引一致性。
    真到了慢的那天，缓存的键是 deliveries 目录的 mtime，不是现在该做的事。
    """
    base = deliveries_root(root)
    usage: dict[str, dict[str, Any]] = {}
    if not os.path.isdir(base):
        return {"ok": True, "usage": {}}
    for name in sorted(os.listdir(base)):
        dir_path = os.path.join(base, name)
        if not os.path.isdir(dir_path):
            continue
        payload = _read_json(os.path.join(dir_path, delivery.CASES_FILE))
        rows = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(rows, (list, tuple)):
            continue
        title = str((payload or {}).get("title") or name) if isinstance(payload, dict) else name
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("case_id") or "")
            for var in required_vars(row.get("request")):
                slot = usage.setdefault(var, {"delivery_count": 0, "case_count": 0,
                                              "deliveries": []})
                entry = next((d for d in slot["deliveries"] if d["id"] == name), None)
                if entry is None:
                    entry = {"id": name, "title": title, "case_ids": []}
                    slot["deliveries"].append(entry)
                    slot["delivery_count"] += 1
                if case_id and case_id not in entry["case_ids"]:
                    entry["case_ids"].append(case_id)
                    slot["case_count"] += 1
    return {"ok": True, "usage": usage}


# ── 用例编辑（闭环稿 D 屏 · 0012 ADR-2） ────────────────────────────────────

#: 允许被编辑的顶层字段。**白名单而不是黑名单**：漏掉一个该禁的字段，
#: 后果是让调用方改到 case_id 这种身份字段，比漏掉一个该放的严重得多。
EDITABLE_FIELDS = ("title", "module", "priority", "case_type", "preconditions",
                   "steps", "expected", "test_data", "endpoints", "request")
_CASE_UPDATE_THREAD_LOCK = threading.Lock()


@contextmanager
def _case_update_lock(cases_path: str) -> Iterator[None]:
    """Lock the complete read/validate/write mutation across threads/processes."""
    lock_path = cases_path + ".lock"
    with _CASE_UPDATE_THREAD_LOCK:
        with open(lock_path, "a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_cases_atomic(cases_path: str, payload: Any) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(cases_path)}.", suffix=".tmp",
        dir=os.path.dirname(cases_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, cases_path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _update_case_locked(safe_id: str, cases_path: str, case_id: str,
                        patch: dict[str, Any]) -> dict[str, Any]:
    payload = _read_json(cases_path)
    rows = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise WorkbenchError(f"{delivery.CASES_FILE} 读不出用例数组。",
                             code="CASES_JSON_BROKEN")

    wanted = str(case_id or "").strip()
    index = next((i for i, r in enumerate(rows)
                  if isinstance(r, dict) and str(r.get("case_id") or "") == wanted), -1)
    if index < 0:
        raise WorkbenchError(f"批次里没有编号「{wanted}」的用例。", code="CASE_NOT_FOUND")

    updated = dict(rows[index])
    updated.update(patch)
    updated["origin"] = "human"
    verdict = case_validate.validate_cases([updated])
    errors = [e for e in (verdict.get("errors") or [])]
    if errors:
        raise WorkbenchError(
            "改完之后这条用例不合格，没有保存："
            + "；".join(str(e.get("message") or e) for e in errors[:3]),
            code="CASE_INVALID")

    rows[index] = updated
    if isinstance(payload, dict):
        payload["cases"] = rows
        payload["last_edited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    else:
        payload = rows
    try:
        _write_cases_atomic(cases_path, payload)
    except OSError as exc:
        raise WorkbenchError(
            f"改动没能写进批次目录：{type(exc).__name__}: {exc}。"
            "常见原因是该批次目录不是当前服务进程建的（属主不同），"
            "用例本身没有被改动。", code="CASES_WRITE_FAILED") from exc

    log.info("workbench: 批次 %s 的用例 %s 已被人工修改（%d 个字段）",
             safe_id, wanted, len(patch))
    return {"ok": True, "case": _case_row(updated, index),
            "warnings": [str(w.get("message") or w)
                         for w in (verdict.get("warnings") or [])[:5]]}


def update_case(delivery_id: str, case_id: str, patch: Any,
                root: str | None = None) -> dict[str, Any]:
    """就地改一条已采纳用例，并标记为人工修改。

    **改完立刻按 `validate_cases` 复校，不合法直接拒、不落盘**——
    这是 0010 硬约束二的延伸：那道闸管"不合格的别进库"，
    没有理由允许合格的东西被改成不合格之后留在库里。

    只改 `cases.json`（执行与页面都读它）。导出产物不同步重写：
    它们是某一次导出动作的快照，改了用例就重新导出一次即可——
    偷偷改掉用户已经拿去评审的那份 xlsx 更糟。
    """
    safe_id = safe_delivery_id(delivery_id)
    dir_path = os.path.join(deliveries_root(root), safe_id)
    cases_path = os.path.join(dir_path, delivery.CASES_FILE)
    if not os.path.isfile(cases_path):
        raise WorkbenchError("这个批次没有结构化用例数据，改不了。", code="NO_CASES_JSON")
    if not isinstance(patch, dict) or not patch:
        raise WorkbenchError("没给出要改的内容。", code="EMPTY_PATCH")

    rejected = sorted(set(patch) - set(EDITABLE_FIELDS))
    if rejected:
        raise WorkbenchError(
            f"这些字段不允许编辑：{'、'.join(rejected)}。"
            f"可改的是：{'、'.join(EDITABLE_FIELDS)}。", code="FIELD_NOT_EDITABLE")

    with _case_update_lock(cases_path):
        return _update_case_locked(safe_id, cases_path, case_id, patch)


# ── 导出（设计稿第 7 屏） ───────────────────────────────────────────────────

#: 页面上四张导出卡对应的单格式名（`delivery.FORMATS` 里的组合值不在此列：
#: 页面是多选卡片，组合由本函数自己拼）
EXPORT_FORMATS = ("xlsx", "csv", "markdown", "postman")


def export_delivery(delivery_id: str, formats: Any,
                    root: str | None = None,
                    redact_pii: bool = True) -> dict[str, Any]:
    """把批次的结构化用例（重新）写成所选格式，落在**批次目录内**。

    与 `save_delivery` 的分工：那边是"采纳时建批次"（新目录 + 收据），
    这边是"对既有批次补产物"——同名文件直接覆盖，重导出即刷新。
    不写收据也不动 `cases.json`：收据记录的是采纳那一笔，导出只是投影。

    产物内容来自 `cases.json` 原文（与执行同一份），标题与来源指纹也取自它——
    导出的 markdown/collection 里写的指纹必须和采纳时一致，换了就不是同一批用例。
    """
    safe_id = safe_delivery_id(delivery_id)
    dir_path, cases, _ = load_cases_for_execution(safe_id, root)

    wanted: list[str] = []
    for raw in (formats if isinstance(formats, (list, tuple)) else [formats]):
        try:
            normalized = delivery.normalize_format(raw)
        except delivery.DeliveryError as exc:
            raise WorkbenchError(str(exc.message), code="FORMAT_UNSUPPORTED") from exc
        for one in normalized.split("+"):
            if one not in wanted:
                wanted.append(one)
    if not wanted:
        raise WorkbenchError("一种导出格式都没选。", code="NO_FORMAT_SELECTED")

    meta = _read_json(os.path.join(dir_path, delivery.CASES_FILE))
    meta = meta if isinstance(meta, dict) else {}
    title = str(meta.get("title") or describe_delivery(dir_path)["title"])
    source_fingerprint = str(meta.get("source_fingerprint") or "")

    # 与 save_delivery 同一条纪律：导出产物脱敏，cases.json 原样不动（BB-424）。
    # 这里的输入就是 cases.json 的原文，所以脱敏只影响本次写出的产物。
    export_cases, pii_hits = (delivery.scrub_cases_for_export(cases) if redact_pii
                              else (list(cases), {}))
    rows, index = delivery.to_rows(export_cases)
    if not rows:
        raise WorkbenchError("这批用例没有一条能落成表格行，无可导出内容。",
                             code="CASES_ALL_INVALID")

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    slug = delivery.slugify(title)
    filenames = {"xlsx": "cases.xlsx", "csv": "cases.csv", "markdown": "cases.md",
                 "postman": f"{slug}.postman_collection.json"}
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    for one in wanted:
        product = os.path.join(dir_path, filenames[one])
        if one == "xlsx":
            delivery._write_xlsx(product, rows)
        elif one == "csv":
            delivery._write_csv(product, rows)
        elif one == "markdown":
            delivery._write_markdown(product, rows, index, title, generated_at,
                                     source_fingerprint)
        else:
            stats = delivery._write_postman(product, export_cases, title,
                                            source_fingerprint)
            if stats.get("placeholder_count"):
                warnings.append(
                    f"{stats['placeholder_count']}/{stats['item_count']} "
                    "条用例没有 request 块，collection 里是占位 item")
            if stats.get("items_without_test"):
                warnings.append(
                    f"{stats['items_without_test']} 条用例没有可执行断言，"
                    "导入后跑完无从判定成败")
        files.append({"name": filenames[one], "path": os.path.abspath(product),
                      "bytes": os.path.getsize(product)})

    log.info("workbench: 批次 %s 导出 %s（%d 条用例，脱敏 %s）",
             safe_id, "+".join(wanted), len(rows), "开" if redact_pii else "关")
    return {"ok": True, "format": "+".join(wanted), "case_count": len(rows),
            "files": files, "warnings": warnings,
            # 命中数一路回传到界面：静默替换会让用户以为产物里还是原值
            "pii_redaction": {"applied": bool(redact_pii), "hits": pii_hits}}


def delivery_file_path(delivery_id: str, filename: str,
                       root: str | None = None) -> str:
    """批次目录内某个产物的绝对路径（给下载端点用）。

    文件名走与批次 id 同一套白名单校验：只能是目录下的一级文件名，
    带路径分隔符或上跳的直接拒——这是下载端点，路径穿越在这里就是任意文件读。
    """
    safe_id = safe_delivery_id(delivery_id)
    name = str(filename or "").strip()
    if not name or _UNSAFE_ID_RE.search(name) or os.path.isabs(name):
        raise WorkbenchError(f"文件名「{name}」不合法。", code="BAD_FILENAME")
    full = os.path.join(deliveries_root(root), safe_id, name)
    if not os.path.isfile(full):
        raise WorkbenchError(f"批次「{safe_id}」里没有文件「{name}」。",
                             code="FILE_NOT_FOUND")
    return os.path.abspath(full)


# ── 执行（后台线程 + 轮询） ─────────────────────────────────────────────────

class RunRegistry:
    """执行任务的内存台账。

    为什么不上 websocket：一轮执行几秒到几十秒，前端每秒 GET 一次就够了，
    而 websocket 要在 starlette 上多一条协议路径、多一套断连重连，
    换来的只是"进度条更跟手"。这个取舍写在这里，免得下次有人顺手加。

    为什么不落盘：进度是过程量，执行结果本来就有 `execution_report.json`
    落在批次目录里（`execute_cases` 干的）。网关重启后没有历史 run 是对的——
    要看历史就去看报告。
    """

    def __init__(self, executor: Any, deliveries_root_dir: str | None = None):
        #: 注入点：测试用假执行器不发真实请求；宿主线传的是已绑好**当前用户金库**的
        #: `partial(execute_cases, env_store=...)`。
        #:
        #: **executor 必填，没有默认值。** 曾经默认取裸的 `execute.execute_cases`，
        #: 那条路径不带 env_store，执行时会回落到进程级全局配置根——在多用户形态下
        #: 就是 A 的执行读了全机共用的凭据表。所有调用点本来就都显式传了，
        #: 这个默认值只是个等人踩的坑，去掉它零成本。
        if executor is None:
            raise ValueError(
                "RunRegistry 需要显式的 executor：宿主线必须传绑好当前用户金库的执行器，"
                "否则环境解析会回落到全局配置根（决策 0009 的隔离就是假的）。")
        self._executor = executor
        self._root = deliveries_root_dir
        self._runs: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._seq = 0

    # ── 查询 ────────────────────────────────────────────────────────────────

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(str(run_id or ""))
            return dict(run) if run else None

    def active_run_for(self, delivery_id: str) -> dict[str, Any] | None:
        with self._lock:
            for run_id in reversed(self._order):
                run = self._runs[run_id]
                if run["delivery_id"] == delivery_id and run["state"] == "running":
                    return dict(run)
        return None

    # ── 启动 ────────────────────────────────────────────────────────────────

    def start(self, *, delivery_id: str, env: str, case_ids: Any = None,
              timeout_s: Any = execute.DEFAULT_TIMEOUT_S,
              auth: Any = execute.DEFAULT_AUTH_MODE) -> dict[str, Any]:
        """开一轮执行，立刻返回 run 句柄（不等执行完）。

        入参闸在这里全过一遍，**过不了就一条请求都不发**：批次要存在、要有
        结构化用例、要选了环境、要至少勾一条。页面上这些都拦得住，
        但端点不能依赖页面——curl 也打得进来。
        """
        safe_id = safe_delivery_id(delivery_id)
        environment = str(env or "").strip()
        if not environment:
            raise WorkbenchError(
                "没选测试环境。执行要知道往哪个地址发请求，"
                "去「测试环境」tab 配一个，再回来选。", code="ENV_REQUIRED")
        mode = str(auth or "").strip().lower() or execute.DEFAULT_AUTH_MODE
        if mode not in AUTH_MODES_ON_PAGE:
            raise WorkbenchError(
                f"鉴权方式「{mode}」不认识，只有「跟随环境」（env）与"
                "「登录换新」（login）两种。", code="BAD_AUTH_MODE")
        ids = [str(c).strip() for c in (case_ids or []) if str(c).strip()]
        if not ids:
            raise WorkbenchError("一条用例都没勾。", code="NO_CASE_SELECTED")

        dir_path, cases, login_request = load_cases_for_execution(safe_id, self._root)
        if mode == "login" and login_request is None:
            # 早拦：让执行层去报 LOGIN_REQUEST_REQUIRED 也行，但那要先建一轮 run、
            # 起一个线程，用户看到的是"执行了一下然后红了"。这里说清楚就不必绕。
            raise WorkbenchError(
                "这个批次里没有登录请求描述，做不了登录换新。"
                "让测试伙伴重跑一次 HAR 链路（体检说支持登录换新时，落盘会把登录请求"
                "一并存进批次），或者改选「跟随环境」并在环境里配好 token 变量。",
                code="NO_LOGIN_REQUEST")

        with self._lock:
            # Check and register under the same lock.  The former
            # active_run_for() -> start() sequence left a TOCTOU window where
            # two concurrent POSTs could both pass the check, fire duplicate
            # requests, and overwrite the same execution_report files.
            busy = next((self._runs[run_id] for run_id in reversed(self._order)
                         if self._runs[run_id]["delivery_id"] == safe_id
                         and self._runs[run_id]["state"] == "running"), None)
            if busy:
                raise WorkbenchError(
                    f"这个批次上还有一轮执行没跑完（{busy['done']}/{busy['total']}）。"
                    "等它结束再点。", code="RUN_IN_PROGRESS")
            self._seq += 1
            run_id = f"run-{int(time.time())}-{self._seq}"
            run: dict[str, Any] = {
                "run_id": run_id,
                "delivery_id": safe_id,
                "delivery_dir": dir_path,
                "environment": environment,
                "auth": mode,
                "state": "running",
                "total": len(ids),
                "done": 0,
                "current_case_id": "",
                "current_title": "",
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": "",
                "result": None,
                "error": "",
            }
            self._runs[run_id] = run
            self._order.append(run_id)
            self._evict_locked()

        thread = threading.Thread(
            target=self._run, name=f"workbench-{run_id}", daemon=True,
            kwargs={"run_id": run_id, "cases": cases, "case_ids": ids,
                    "env": environment, "timeout_s": timeout_s,
                    "delivery_dir": dir_path, "auth": mode,
                    "login_request": login_request,
                    "title": describe_delivery(dir_path)["title"]})
        thread.start()
        # 只记环境名、鉴权模式与条数——都是标签，变量值本模块压根拿不到
        log.info("workbench: 批次 %s 开跑 %d 条用例（环境「%s」，鉴权 %s，run=%s）",
                 safe_id, len(ids), environment, mode, run_id)
        return self.get(run_id) or run

    def _evict_locked(self) -> None:
        while len(self._order) > MAX_RUNS_KEPT:
            oldest = self._order.pop(0)
            self._runs.pop(oldest, None)

    def _update(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.update(fields)

    def _run(self, *, run_id: str, cases: list, case_ids: list, env: str,
             timeout_s: Any, delivery_dir: str, title: str,
             auth: str = execute.DEFAULT_AUTH_MODE,
             login_request: Any = None) -> None:
        def progress(state: dict) -> None:
            self._update(run_id, done=int(state.get("done") or 0),
                         total=int(state.get("total") or 0) or len(case_ids),
                         current_case_id=state.get("current_case_id") or "",
                         current_title=state.get("current_title") or "")

        try:
            result = self._executor(
                cases, case_ids=case_ids, env=env, timeout_s=timeout_s,
                delivery_dir=delivery_dir, title=title, progress=progress,
                auth=auth, login_request=login_request,
                # 报告合法根 = 本台账扫批次用的同一个根。不传的话执行层按
                # MCP 线的模块常量判定，用户 scope 下的批次目录会被误拒
                # （报告落不进批次，实测踩过）。
                deliveries_root=deliveries_root(self._root))
        except Exception as exc:  # noqa: BLE001 - 后台线程里的异常必须收进 run
            self._update(run_id, state="error",
                         error=f"{type(exc).__name__}: {exc}",
                         finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            return
        if not isinstance(result, dict):
            self._update(run_id, state="error",
                         error="执行器没有返回结果对象。",
                         finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            return
        # `ok:false` = 这轮压根没跑起来（环境不存在、base_url 非法之类），
        # 与"跑了但有用例失败"是两回事，后者 ok 仍是 true。
        state = "done" if result.get("ok") else "error"
        self._update(
            run_id, state=state, result=result,
            error="" if result.get("ok") else str(result.get("message") or
                                                  result.get("error") or ""),
            done=int((result.get("summary") or {}).get("total") or len(case_ids)),
            current_case_id="", current_title="",
            finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))
