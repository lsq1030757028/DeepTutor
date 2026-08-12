# -*- coding: utf-8 -*-
"""pw_runtime — AutomationBundle 内嵌执行运行时（编译器逐字嵌入 bundle 为 _runtime.py）。

五条红线的执行落点（单源 redlines 纯函数，嵌入版名 _redlines）：
  1 跨 host 拒发：goto/request 前判等价类；页面路由拦截第三方域 abort 并记录；
  2 变量残留 skip：渲染后仍残留 {{var}} → SKIP_MISSING_VARS；
  3 凭据零回显：一切落盘文本过 scrub 门；变量值只存在于进程内存与 TP_VARS_JSON 环境变量；
  4 无断言不算过：断言计数 0 的 case 强制不得 pass（结果记 no_assertions）；
  5 不跟随重定向：API 轨零跳转；UI 轨每步动作后复查主文档落点 host。

写确认：side_effects.writes=true 的 case 必须在 TP_WRITE_AUTHORIZED_IDS 里，
否则 SKIP_WRITE_UNCONFIRMED（授权收据在批次 events.jsonl，由 execute 工具下发）。
运行时会再从真实 HTTP 方法独立派生写风险；POST/PUT/PATCH/DELETE 等不能靠错误的
``writes=false`` 声明绕过确认。UI click 本身有读写两义，不在这里粗暴推断。

探测性（probing）case：只记录观测，恒不 pass/fail（判决权在产品——探测层语义）。

证据增量落盘：每 case 结束即 append results.jsonl（崩溃后已完成 case 不重跑）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

try:  # 包内形态（开发/测试）
    from server.journey import redlines as _rl
except ImportError:  # bundle 嵌入形态
    import _redlines as _rl  # type: ignore

try:
    from server.journey import db_readonly as _dbro
except ImportError:  # bundle 嵌入形态
    import _dbro  # type: ignore


class CaseSkip(Exception):
    """红线/前置拦截：不执行，理由进账。"""

    def __init__(self, code: str, reason: str):
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


class CaseBlocked(Exception):
    """执行中被环境阻断（连接失败等）：BLOCKED，不判业务。"""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def load_context() -> dict[str, Any]:
    base_url = _env("TP_BASE_URL").rstrip("/")
    if not base_url:
        raise RuntimeError("TP_BASE_URL 未注入——bundle 不内联环境，执行方必须显式传入")
    variables = {}
    raw = _env("TP_VARS_JSON")
    if raw:
        variables = json.loads(raw)
    variables.setdefault("baseUrl", base_url)
    return {
        "base_url": base_url,
        "variables": variables,
        "run_dir": _env("TP_RUN_DIR") or os.path.join(os.getcwd(), "run-local"),
        "write_authorized": set(filter(None, _env("TP_WRITE_AUTHORIZED_IDS").split(","))),
        "done_cases": set(filter(None, _env("TP_DONE_CASE_IDS").split(","))),
        "scrub_pairs": _rl.scrub_map(variables),
    }


_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def effective_write_risk(meta: dict[str, Any]) -> bool:
    """Derive write risk from executable facts, conservatively honoring declaration."""
    if bool(meta.get("writes")):
        return True
    actions = list(meta.get("actions") or [])
    return any(
        str(action.get("op") or "") == "request"
        and str(action.get("method") or "GET").upper() not in _SAFE_HTTP_METHODS
        for action in actions
    )


class CaseRunner:
    """单 case 执行器。conftest 每 case 建一个，负责红线、断言计数与证据落盘。"""

    def __init__(self, ctx: dict[str, Any], meta: dict[str, Any], page: Any = None):
        self.ctx = ctx
        self.meta = meta          # {case_id, title, probing, writes, track, digests…}
        self.page = page          # UI 轨的 playwright page（API 轨为 None）
        self.assertions: list[dict[str, Any]] = []
        self.observations: list[str] = []
        self.aborted_requests: list[str] = []
        self.http_transcript: list[dict[str, Any]] = []
        self.db_transcript: list[dict[str, Any]] = []
        self.db_metrics: dict[str, dict[str, Any]] = {}   # 守恒量基线
        self._db_conn: Any = None
        self._cookies: dict[str, str] = {}   # per-case cookie jar（一个 case = 一个会话）
        self.status = "executed"  # executed|skipped|blocked
        self.skip_reason = ""
        cid = meta["case_id"]
        if cid in ctx["done_cases"]:
            raise CaseSkip("SKIP_RESUME_DONE", "上轮已完成，本轮续跑跳过")
        if effective_write_risk(meta) and cid not in ctx["write_authorized"]:
            raise CaseSkip("SKIP_WRITE_UNCONFIRMED",
                           "写用例未获写确认授权（批次 events.jsonl 无该 case 的 write_confirm）")

    # ── 工具 ────────────────────────────────────────────────────────────
    def _scrub(self, text: Any) -> Any:
        return _rl.scrub(text, self.ctx["scrub_pairs"])

    def _render(self, text: str) -> str:
        rendered = _rl.render(text, self.ctx["variables"])
        left = _rl.unresolved_vars(rendered)
        if left:
            raise CaseSkip("SKIP_MISSING_VARS", "变量未提供:" + ",".join(left))
        return rendered

    def _url(self, path: str) -> str:
        path = self._render(path)
        url = path if "://" in path else self.ctx["base_url"] + (
            path if path.startswith("/") else "/" + path)
        nav = _rl.navigation_allowed(self.ctx["base_url"], url)
        if not nav["allowed"]:
            raise CaseSkip("SKIP_CROSS_HOST", nav["reason"])
        return url

    def _record_assert(self, kind: str, expected: Any, actual: Any, passed: bool) -> None:
        self.assertions.append({
            "kind": kind,
            "expected": self._scrub(expected),
            "actual": self._scrub(actual),
            "passed": bool(passed),
        })

    def _check_landing(self) -> None:
        """红线 5（UI 轨）：动作后主文档落点必须仍在等价类内。"""
        if self.page is None:
            return
        landing = self.page.url
        nav = _rl.navigation_allowed(self.ctx["base_url"], landing)
        if not nav["allowed"]:
            raise CaseSkip("SKIP_REDIRECT_ESCAPE", "重定向落点越出等价类:中止 " + nav["reason"])

    # ── 动作分发 ────────────────────────────────────────────────────────
    def act(self, action: dict[str, Any]) -> None:
        op = action["op"]
        fn = getattr(self, "_op_" + op, None)
        if fn is None:
            raise CaseBlocked(f"未知 op:{op}")
        fn(action)

    # UI 轨
    def _op_goto(self, a: dict[str, Any]) -> None:
        url = self._url(a["path"])
        try:
            self.page.goto(url, wait_until="load")
        except Exception as exc:  # noqa: BLE001 - 环境阻断如实分类
            raise CaseBlocked(self._scrub(f"goto 失败:{exc}"))
        self._check_landing()

    def _op_fill(self, a: dict[str, Any]) -> None:
        value = self._render(str(a.get("value", "")))
        self.page.fill(a["selector"], value)

    def _op_click(self, a: dict[str, Any]) -> None:
        self.page.click(a["selector"])
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:  # noqa: BLE001 - 静态页无网络活动属正常
            pass
        self._check_landing()

    def _op_wait_load(self, a: dict[str, Any]) -> None:
        self.page.wait_for_load_state(a.get("state", "networkidle"))
        self._check_landing()

    def _op_expect_title_contains(self, a: dict[str, Any]) -> None:
        want = self._render(a["text"])
        got = self.page.title()
        self._record_assert("title_contains", want, got, want in got)

    def _op_expect_url_contains(self, a: dict[str, Any]) -> None:
        want = self._render(a["text"])
        got = self.page.url
        self._check_landing()
        self._record_assert("url_contains", want, got, want in got)

    def _op_expect_text(self, a: dict[str, Any]) -> None:
        want = self._render(a["contains"])
        try:
            got = self.page.text_content(a["selector"], timeout=8000) or ""
        except Exception as exc:  # noqa: BLE001
            self._record_assert("text", want, f"(定位失败:{type(exc).__name__})", False)
            return
        self._record_assert("text", want, got.strip()[:300], want in got)

    def _op_expect_visible(self, a: dict[str, Any]) -> None:
        try:
            vis = self.page.is_visible(a["selector"], timeout=8000)
        except Exception:  # noqa: BLE001
            vis = False
        self._record_assert("visible", True, vis, vis is True)

    def _op_expect_hidden(self, a: dict[str, Any]) -> None:
        try:
            vis = self.page.is_visible(a["selector"])
        except Exception:  # noqa: BLE001
            vis = False
        self._record_assert("hidden", False, vis, vis is False)

    # API 轨（红线 5：零跳转）
    def _op_request(self, a: dict[str, Any]) -> None:
        url = self._url(a["path"])
        method = a.get("method", "GET").upper()
        data = None
        headers = {}
        if a.get("body_form"):
            form = {k: self._render(str(v)) for k, v in a["body_form"].items()}
            data = "&".join(f"{k}={v}" for k, v in form.items()).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif a.get("body_json") is not None:
            data = self._render(json.dumps(a["body_json"], ensure_ascii=False)).encode()
            headers["Content-Type"] = "application/json"
        # 自定义请求头（鉴权用）。值走 _render，所以写的是 {{adminToken}} 这种变量引用，
        # **不是字面量** —— 凭据只经执行期 TP_VARS_JSON 进内存，bundle 里存的始终是变量名。
        # 头的值不进 http_transcript（那里只记 method/url/status/body_head），
        # 且变量值已在 scrub_map 里，万一从别的口子漏出来也会被遮盖。
        for hk, hv in (a.get("headers") or {}).items():
            headers[str(hk)] = self._render(str(hv))
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kw):  # noqa: D102
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.time()
        try:
            with opener.open(req, timeout=15) as resp:
                status = resp.status
                body = resp.read(65536).decode("utf-8", "replace")
                set_cookies = resp.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = (exc.read(65536) or b"").decode("utf-8", "replace")
            set_cookies = exc.headers.get_all("Set-Cookie") or [] if exc.headers else []
        except (urllib.error.URLError, OSError) as exc:
            raise CaseBlocked(self._scrub(f"请求失败:{exc}"))
        for sc in set_cookies:  # 会话内 cookie 持久（红线 3：cookie 值不落盘、不回显）
            kv = sc.split(";", 1)[0].strip()
            if "=" in kv:
                k, v = kv.split("=", 1)
                self._cookies[k] = v
        self.last_response = {"status": status, "body": body}
        self.http_transcript.append({
            "method": method,
            "url": self._scrub(url),
            "status": status,
            "elapsed_ms": int((time.time() - started) * 1000),
            "body_head": self._scrub(body[:2000]),
        })

    def _op_expect_status(self, a: dict[str, Any]) -> None:
        got = getattr(self, "last_response", {}).get("status")
        self._record_assert("status", a["status"], got, got == a["status"])

    def _op_expect_json_path(self, a: dict[str, Any]) -> None:
        body = getattr(self, "last_response", {}).get("body", "")
        try:
            doc = json.loads(body)
        except ValueError:
            self._record_assert("json_path", a.get("equals"), "(响应不是 JSON)", False)
            return
        cur: Any = doc
        for part in str(a["path"]).split("."):
            if isinstance(cur, list) and part.isdigit():
                cur = cur[int(part)] if int(part) < len(cur) else None
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
        self._record_assert("json_path:" + a["path"], a.get("equals"), cur,
                            cur == a.get("equals"))

    # ── 数据层（L3，轨道中立：不碰 self.page，UI/API 两轨都能用）──────────
    #
    # 连接**惰性建立**：一条不含 db op 的用例不该因为没配 DSN 而 BLOCKED。
    # 但一旦用例里有 db op，缺 DSN/缺驱动就是 BLOCKED —— 不降级成跳过，
    # 更不当通过（那是护栏 3 点名的静默降级：一条从没验过数据的用例显示成绿的）。
    def _db(self) -> Any:
        if getattr(self, "_db_conn", None) is None:
            try:
                self._db_conn = _dbro.connect()
            except _dbro.DbChannelError as exc:
                raise CaseBlocked(f"L3 只读通道不可用：{exc}")
        return self._db_conn

    def _db_query(self, a: dict[str, Any]) -> dict[str, Any]:
        sql = self._render(str(a["sql"]))
        try:
            return _dbro.query(self._db(), sql)
        except _dbro.DbStatementRejected as exc:
            # 白名单拒发是**用例写错了**，不是环境问题 —— 用 CaseSkip 会让它
            # 悄悄不算数，所以判 BLOCKED 让它必须被看见。
            raise CaseBlocked(f"SQL 未过只读白名单：{exc}")
        except Exception as exc:  # noqa: BLE001 - 驱动异常族杂
            raise CaseBlocked(self._scrub(f"只读查询失败：{type(exc).__name__}"))

    def _op_db_query(self, a: dict[str, Any]) -> None:
        """跑一条只读查询并留档。本身不产生断言（观测 op）。"""
        res = self._db_query(a)
        self.last_db = res
        self.db_transcript.append({
            "sql": self._scrub(self._render(str(a["sql"]))),
            "row_count": res["row_count"],
            "truncated": res["truncated"],
        })

    def _op_expect_db_rows(self, a: dict[str, Any]) -> None:
        """断言**本次查询返回的行数**。

        注意它与 `SELECT count(*)` 不是一回事：本 op 数的是取回来的行，会被
        `limit` 截断；要断"库里有多少条"请用 `expect_db_value` 配 `count(*)`，
        让数据库去数。取被截断的结果下计数断言，与本线那条取证纪律
        （穷尽断言不得建立在截断输出上）是同一个错误换了一层。
        """
        res = self._db_query(a)
        self.last_db = res
        want = a["rows"]
        got = res["row_count"]
        if res["truncated"]:
            self.observations.append(
                f"expect_db_rows 的结果被 limit 截断（>={got} 行），该断言不可信")
        self._record_assert("db_rows", want, got,
                            got == want and not res["truncated"])

    def _op_expect_db_value(self, a: dict[str, Any]) -> None:
        """断言标量值。零行 = 断言失败，不是通过（"查不到"不等于"符合预期"）。"""
        res = self._db_query(a)
        self.last_db = res
        got = res["rows"][0][0] if res["rows"] else None
        want = a["equals"]
        # 数值口径统一：DB 侧 count(*) 回 int/Decimal，配方里写的是 JSON 数字
        if isinstance(want, (int, float)) and not isinstance(want, bool) and got is not None:
            try:
                got = type(want)(got)
            except (TypeError, ValueError):
                pass
        self._record_assert("db_value:" + str(a.get("label") or "")[:40],
                            want, got, got == want)

    def _op_db_snapshot(self, a: dict[str, Any]) -> None:
        """记一个守恒量的**操作前**基线。存查询本身，不只存值。

        存查询是关键：`expect_db_delta` 会**重跑同一条 SQL**。若只存值、由配方
        再写一遍查询，就可以快照 A 却对 B 求差 —— 守恒闸会全绿而守恒根本没被验。
        """
        metric = str(a["metric"])
        sql = str(a["sql"])
        value = self._db_query({"sql": sql})
        got = value["rows"][0][0] if value["rows"] else None
        if got is None:
            raise CaseBlocked(f"守恒量 {metric!r} 的基线查询零行——无法建立基线")
        # `_query` 只在进程内供复查使用；落盘证据只保留 scrub 后的已渲染 SQL。
        # 否则 SQL 中一旦含变量占位，结果卡要么无法复核实际查询，要么会把变量值
        # 原样落盘。执行值与证据值分开保存，二者都不冒充对方。
        self.db_metrics[metric] = {
            "_query": sql,
            "sql": self._scrub(self._render(sql)),
            "before": got,
        }
        self.observations.append(f"db_snapshot {metric}={got}")

    def _op_expect_db_delta(self, a: dict[str, Any]) -> None:
        """断言守恒量的**变化量**等于期望差。重跑基线那条 SQL，不接受另给一条。"""
        metric = str(a["metric"])
        base = self.db_metrics.get(metric)
        if base is None:
            raise CaseBlocked(
                f"守恒量 {metric!r} 没有基线：expect_db_delta 必须配一个先行的 "
                f"db_snapshot。缺基线时求出来的差是拿 0 当起点算的，会假绿。")
        after_res = self._db_query({"sql": base["_query"]})
        after = after_res["rows"][0][0] if after_res["rows"] else None
        if after is None:
            raise CaseBlocked(f"守恒量 {metric!r} 的复查零行")
        try:
            actual = after - base["before"]
        except TypeError:
            raise CaseBlocked(f"守恒量 {metric!r} 不是可相减的类型")
        want = a["delta"]
        passed = actual == want
        # 这组 before/after/delta 是 `db_snapshot.json` 的事实来源。此前只在断言里
        # 留 actual、在 metrics 里留 before，执行器虽声明需要 db_snapshot，却永远
        # 产不出该证据文件，真实通过的写用例会被正式投影降成 PENDING。
        base.update({
            "after": after,
            "delta": actual,
            "expected_delta": want,
            "passed": passed,
        })
        self._record_assert(f"db_delta:{metric}", want, actual, passed)

    # ── 收尾 ────────────────────────────────────────────────────────────
    def result(self) -> dict[str, Any]:
        n = len(self.assertions)
        failed = [x for x in self.assertions if not x["passed"]]
        if self.meta.get("probing"):
            outcome = "observed"          # 探测层：无判决权
        elif n == 0:
            outcome = "no_assertions"     # 红线 4：无断言不算过
        elif failed:
            outcome = "failed"
        else:
            outcome = "passed"
        return {
            "case_id": self.meta["case_id"],
            "case_version": self.meta.get("case_version"),
            "title": self.meta.get("title", ""),
            "probing": bool(self.meta.get("probing")),
            "track": self.meta.get("track"),
            "outcome": outcome,
            "assertions": self.assertions,
            "assertion_count": n,
            "aborted_third_party_requests": self.aborted_requests,
            "http_transcript": self.http_transcript,
            "db_transcript": self.db_transcript,
            "db_metrics": {
                k: {mk: mv for mk, mv in v.items() if not mk.startswith("_")}
                for k, v in self.db_metrics.items()
            },
            "observations": self.observations,
            "source_case_digest": self.meta.get("source_case_digest"),
            "oracle_digest": self.meta.get("oracle_digest"),
            "schema_version": self.meta.get("schema_version"),
        }


def install_route_guard(page: Any, base_url: str, runner: CaseRunner) -> None:
    """红线 1（子资源面）：同等价类放行，第三方域 abort 并记录。"""

    def _route(route: Any) -> None:
        url = route.request.url
        if _rl.route_decision(base_url, url) == "continue":
            route.continue_()
        else:
            runner.aborted_requests.append(runner._scrub(url))
            route.abort()

    page.route("**/*", _route)


def append_result(run_dir: str, row: dict[str, Any]) -> None:
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "results.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def register_pid(run_dir: str, pid: int, kind: str) -> None:
    # Bundle 只含嵌入模块，不能反向依赖宿主源码树。
    try:
        from server.journey import pid_ledger
    except ImportError:  # compiled self-contained bundle
        import _pid_ledger as pid_ledger
    pid_ledger.register_pid(run_dir, pid, kind)


def deregister_pid(run_dir: str, pid: int) -> None:
    try:
        from server.journey import pid_ledger
    except ImportError:  # compiled self-contained bundle
        import _pid_ledger as pid_ledger
    pid_ledger.deregister_pid(run_dir, pid)
