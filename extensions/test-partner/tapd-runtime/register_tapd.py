"""把宿主机上的 mcp-server-tapd 注册进 DeepTutor 的部署注册表（Deployment registry）。

被 `setup_tapd.cmd` 第 4 步调用，也可以单独跑。只用标准库（urllib），
用 test-partner 主 venv 的 Python 跑就行，不需要装任何依赖。

    ..\\.venv\\Scripts\\python.exe register_tapd.py            # 注册（幂等）
    ..\\.venv\\Scripts\\python.exe register_tapd.py --dry-run  # 只读现网配置，不写
    ..\\.venv\\Scripts\\python.exe register_tapd.py --status   # 只看 tapd 条目当前状态
    ..\\.venv\\Scripts\\python.exe register_tapd.py --remove   # 删掉 tapd 条目（恢复现场）

为什么走 API 而不是让用户在页面上填：条目字段有 10 个（type/url/tool_timeout/
enabled_tools/...），页面上填错一个就是"连不上但不知道为什么"。这里的字段形状是
2026-08-05 对着现网 `GET /api/v1/settings/mcp` 的既有条目实测抄下来的，
不是凭记忆写的——`auth` 和 `catalog_entry` 两个字段就是实测才发现的。

接口出处（DeepTutor v1.5.8 容器内 `deeptutor/api/routers/mcp_settings.py`）：

- `GET  /api/v1/settings/mcp`              读 servers + 实时连接 status
- `PUT  /api/v1/settings/mcp/servers/tapd` 单条 upsert（同名覆盖，其余条目逐字节不动）
- `DELETE /api/v1/settings/mcp/servers/tapd`

用单条 upsert 而不是整表 PUT，是因为整表 PUT 会让本脚本对它不认识的字段负责——
读回来再写回去，任何没建模的字段都会被静默抹掉。upsert 只碰 tapd 这一条。

鉴权：这组路由挂了 `require_admin`。本地部署 `AUTH_ENABLED=false` 时所有请求都算
admin，不需要凭据；多用户部署下需要 admin 的 token，用环境变量 `DEEPTUTOR_TOKEN`
传（会以 `Authorization: Bearer` 发出，只发给 `DEEPTUTOR_API_BASE`）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

#: DeepTutor 后端。容器把 3782 映射到宿主机，脚本在宿主机上跑，所以是 127.0.0.1。
API_BASE = os.environ.get("DEEPTUTOR_API_BASE", "http://127.0.0.1:3782").rstrip("/")

#: 注册表里的条目名。DeepTutor 会用它给工具名加前缀（mcp_tapd_get_stories_or_tasks）。
SERVER_NAME = "tapd"

#: 容器视角的服务地址。宿主机上服务绑 0.0.0.0:3795，容器里要用 host.docker.internal
#: 才能打回宿主机；填 127.0.0.1 会打到容器自己身上，表现为"服务明明起着却连不上"。
MCP_URL = "http://host.docker.internal:3795/mcp"

#: 默认只放行两个只读工具。mcp-server-tapd 一共 43 个工具，其中 17 个是写操作
#: （create_bug / update_story_or_task ...），而这个包的凭据是模块级单例，
#: 写操作在 TAPD 审计里全部归到同一个 token 名下，追不到人。
#: 出处：docs/research/mcp-server-tapd-audit-20260804.md 传输面一节。
DEFAULT_ENABLED_TOOLS = ["get_stories_or_tasks", "get_stories_fields_info"]

TIMEOUT = 20


class Fail(RuntimeError):
    """带退出码的可读失败。"""

    def __init__(self, message: str, code: int = 3) -> None:
        super().__init__(message)
        self.code = code


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{API_BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    token = os.environ.get("DEEPTUTOR_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        if exc.code in (401, 403):
            raise Fail(
                f"DeepTutor 拒绝了这次调用（HTTP {exc.code}）。\n"
                "  这组配置接口只允许 admin 调用。你的 DeepTutor 如果开了登录\n"
                "  （AUTH_ENABLED=true），需要先拿到 admin 的 token，再设环境变量后重跑：\n"
                "      set DEEPTUTOR_TOKEN=<你的 admin token>\n"
                f"  服务端原话：{detail}",
                code=4,
            ) from exc
        raise Fail(
            f"DeepTutor 返回 HTTP {exc.code}（{method} {path}）。\n  服务端原话：{detail}",
            code=3,
        ) from exc
    except urllib.error.URLError as exc:
        raise Fail(
            f"连不上 DeepTutor（{API_BASE}）：{exc.reason}\n"
            "  DeepTutor 没在跑，或者端口不是 3782。先确认容器活着：\n"
            "      docker ps --filter name=deeptutor\n"
            "  起不来就先起容器，然后重跑本脚本（或重跑 setup_tapd.cmd，它是幂等的）。",
            code=1,
        ) from exc

    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Fail(f"DeepTutor 的响应不是 JSON（{method} {path}）：{raw[:200]}", code=3) from exc


def get_settings() -> dict:
    return _request("GET", "/api/v1/settings/mcp")


def build_entry(existing: dict | None) -> dict:
    """按现网既有条目的字段形状造 tapd 条目。

    存在同名条目时以它为底再覆盖我们管的那几个键——这样用户手工加过的字段
    （比如自己收窄过的 enabled_tools、或者这个版本有而本脚本不认识的新字段）
    不会因为一次重跑就被抹掉。
    """
    entry = dict(existing) if existing else {}
    entry.update(
        {
            "type": "streamableHttp",
            "command": "",
            "args": [],
            "env": {},
            "cwd": "",
            "url": MCP_URL,
            "headers": {},
            "tool_timeout": 30,
            "disabled_tools": entry.get("disabled_tools") or [],
            "enabled": True,
            "auth": entry.get("auth", ""),
            "catalog_entry": entry.get("catalog_entry", ""),
        }
    )
    # enabled_tools：用户自己改过就尊重他的，只在缺失/为空时才灌默认值。
    # "*" 视为没收窄过，按默认的两个只读工具重设（43 个工具里 17 个是写操作）。
    current = existing.get("enabled_tools") if existing else None
    if not current or current == ["*"]:
        entry["enabled_tools"] = list(DEFAULT_ENABLED_TOOLS)
    else:
        entry["enabled_tools"] = list(current)
    return entry


def find_status(settings: dict) -> dict | None:
    for row in settings.get("status") or []:
        if row.get("name") == SERVER_NAME:
            return row
    return None


def report_status(settings: dict) -> int:
    row = find_status(settings)
    if row is None:
        print("[register] 注册表里没有名为 tapd 的条目。")
        return 2
    state = row.get("status", "")
    tools = row.get("tools") or []
    if state == "connected":
        print(f"[register] tapd 已连接，DeepTutor 侧可见 {len(tools)} 个工具：")
        for tool in tools:
            print(f"           - {tool.get('name', '')}")
        return 0
    print(f"[register] tapd 条目在，但状态是 {state or '(空)'}。")
    if row.get("error"):
        print(f"           DeepTutor 报的错：{row['error']}")
    print("           最常见原因：宿主机上的 3795 服务没起来，或者容器打不到")
    print("           host.docker.internal。先跑 start_tapd.cmd 把服务起起来。")
    return 2


def do_register(dry_run: bool) -> int:
    settings = get_settings()
    servers = settings.get("servers") or {}
    existing = servers.get(SERVER_NAME)

    print(f"[register] DeepTutor 在 {API_BASE}，现有 {len(servers)} 个 MCP 条目："
          f"{', '.join(sorted(servers)) or '(空)'}")
    if existing:
        print("[register] 已有 tapd 条目，按幂等规则覆盖更新（保留你自定义过的字段）。")
    else:
        print("[register] 没有 tapd 条目，新建。")

    entry = build_entry(existing)
    print("[register] 将写入：")
    print("           " + json.dumps(entry, ensure_ascii=False))

    if dry_run:
        print("[register] --dry-run：只读不写，到此为止。")
        return 0

    result = _request("PUT", f"/api/v1/settings/mcp/servers/{SERVER_NAME}", entry)
    if not result:
        raise Fail("PUT 返回了空响应，无法确认是否写入成功。", code=3)

    # PUT 的响应里已经带了 reload 之后的 status，但还是再 GET 一次：
    # 连接是异步建的，重新读一遍拿到的才是稳定态。
    code = report_status(get_settings())
    if code == 0:
        print()
        print("TAPD 能力已生效，直接在 DeepTutor 聊天里贴需求链接即可")
    return code


def do_remove() -> int:
    _request("DELETE", f"/api/v1/settings/mcp/servers/{SERVER_NAME}")
    print("[register] 已从 DeepTutor 注册表删除 tapd 条目。")
    return 0


EXPLAIN = {
    "token-prompt": """
下一步要你粘贴 TAPD 个人访问令牌。

  去哪拿：TAPD 网页端右上角头像 -> 个人设置 -> 个人访问令牌 -> 生成，
          只勾选你自己需要的项目。

  注意：这个窗口里的输入是**明文显示**的（cmd 的 set /p 没有隐藏输入的办法）。
        令牌会写进 tapd-runtime\\.env（该文件已被 .gitignore 拦住，不会进版本库），
        除此之外不落任何地方；这个窗口关掉，屏幕上的痕迹就没了。
        旁边有人的话，先支开再粘。

  不要用账号密码（TAPD_API_USER / TAPD_API_PASSWORD）：那条路是 Basic Auth，
  base64 可逆，等于明文托管账号密码。
""",
}
# 没有 "python 找不到" 这一条：那种情况下本文件根本跑不起来，
# setup_tapd.cmd 只能自己用 ASCII 英文报，见它的 [0/4] 段。


#: 启动日志。setup_tapd.cmd 把后台起服务的 stdout/stderr 重定向到这里。
START_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tapd-start.log")

#: 日志特征 -> 诊断。命中第一条就停，所以顺序即优先级：
#: 端口占用要排在凭据之前，否则"换个端口就好"会被误诊成"令牌坏了"。
START_SYMPTOMS = [
    (
        ("10048", "address already in use", "只允许使用一次"),
        "3795 端口已经被别的程序占了。",
        "  先看看是不是上一次的 TAPD 服务还开着（任务栏里那个最小化的\n"
        "  \"tapd-runtime :3795\" 窗口）——是的话直接用它，不用重起。\n"
        "  确实是别的程序占的话，改端口要同时改 start_tapd.cmd 和 register_tapd.py。",
    ),
    (
        ("401", "unauthorized", "403", "forbidden"),
        "TAPD 把这个令牌拒了（401/403）。",
        "  官方包在启动那一刻就拿令牌去打 https://api.tapd.cn/users/info 做校验，\n"
        "  令牌错、过期、或者没授权到任何项目，进程当场退出，不会\"先起来再报错\"。\n"
        "  删掉 tapd-runtime\\.env，回 TAPD 个人设置重新生成一个令牌，重跑 setup_tapd.cmd。",
    ),
    (
        ("connectionerror", "maxretryerror", "getaddrinfo", "newconnectionerror", "timed out"),
        "这台机器连不上 TAPD 的服务器。",
        "  启动期那次 /users/info 校验请求根本没发出去。常见于公司网络要走代理、\n"
        "  VPN 没连、或者 DNS 拦了 api.tapd.cn。网络通了以后重跑 setup_tapd.cmd。",
    ),
    (
        ("modulenotfounderror", "no module named"),
        "tapd-runtime\\.venv 里的依赖不全。",
        "  按 README「重建 .venv」那节重装，然后重跑 setup_tapd.cmd。",
    ),
]


def explain_start_failed() -> None:
    print("TAPD 服务没起来。")
    print()

    lines: list[str] = []
    if os.path.exists(START_LOG):
        with open(START_LOG, "r", encoding="utf-8", errors="replace") as handle:
            lines = [ln.rstrip() for ln in handle if ln.strip()]

    if not lines:
        print("  连 tapd-start.log 都没写出来，说明 start_tapd.cmd 压根没跑起来。")
        print("  手动跑一次看它自己报什么：tapd-runtime\\start_tapd.cmd")
        return

    haystack = "\n".join(lines).lower()
    for needles, headline, advice in START_SYMPTOMS:
        if any(needle in haystack for needle in needles):
            print(f"  诊断：{headline}")
            print(advice)
            break
    else:
        print("  没认出是哪种已知故障。启动日志最后几行：")
        print()
        for line in lines[-8:]:
            print(f"    {line}")
        print()
        print("  最常见的还是令牌不对——官方包启动即校验令牌，令牌坏了进程直接退出。")
        print("  删掉 tapd-runtime\\.env 换个新令牌重跑 setup_tapd.cmd 是第一顺位的尝试。")

    print()
    print(f"  完整启动日志（含 traceback）：{START_LOG}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 mcp-server-tapd 注册进 DeepTutor 部署注册表（幂等）"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="只读现网配置并打印将写入的条目，不写")
    group.add_argument("--status", action="store_true", help="只报告 tapd 条目的连接状态")
    group.add_argument("--remove", action="store_true", help="删除 tapd 条目")
    group.add_argument(
        "--explain",
        choices=sorted(list(EXPLAIN) + ["start-failed"]),
        help="打印某个失败分支的中文说明（start-failed 会顺带读 tapd-start.log 做诊断）",
    )
    args = parser.parse_args()

    if args.explain == "start-failed":
        explain_start_failed()
        return 0
    if args.explain:
        print(EXPLAIN[args.explain].strip("\n"))
        return 0

    try:
        if args.status:
            return report_status(get_settings())
        if args.remove:
            return do_remove()
        return do_register(args.dry_run)
    except Fail as exc:
        print(f"\n[register] 失败：{exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
