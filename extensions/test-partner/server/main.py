"""测试伙伴（test-partner）能力网关入口。宿主机唯一常驻进程。

一个进程两个面（决策 `docs/decisions/0006-capability-gateway.md`）：

- **MCP 面**：streamable-http 监听 0.0.0.0:3790，供 Docker 中的 DeepTutor 实例
  通过 http://host.docker.internal:3790/mcp 挂载。占主线程。
- **配置面**：只绑 127.0.0.1:3789 的本地配置页（`server/gateway/`），独立守护线程。
  浏览器打开就能填 TAPD 令牌、看各服务状态灯、重启子服务、刷新聊天会话。

关于 SDK API：官方 Python SDK 自 2.0.0 起把高层服务器类 `FastMCP` 更名为
`MCPServer`（`mcp.server.fastmcp` 模块已移除），本文件用的就是同一个类。
若需回到 FastMCP 命名，需把依赖降到 mcp<2（末版 1.29.0）。

本地启动：
    python -m server.main
或：
    scripts\\start_server.cmd

开发期想在别的端口起一份（不打扰常驻实例）：设 TEST_PARTNER_PORT / TEST_PARTNER_HOST
（MCP 面）与 TEST_PARTNER_GATEWAY_PORT（配置面）。设 TEST_PARTNER_GATEWAY=0 可只起
MCP 面不起配置面。
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from server import case_validate, delivery, execute, har_parse

SERVER_NAME = "test-partner"
SERVER_VERSION = "0.8.0"
HOST = os.environ.get("TEST_PARTNER_HOST", "0.0.0.0")
PORT = int(os.environ.get("TEST_PARTNER_PORT", "3790"))

#: `cases` 的入参标注：类型放开成 Any（形状不对也要能进到服务端，由
#: `server/args_tolerance.py` 规整；标成 list[dict] 会在 SDK 的 pydantic 校验层
#: 就硬拒，工具内的宽容解包根本轮不到跑），期望形状写在 description 里给模型看。
CasesArg = Annotated[Any, Field(description=(
    "用例数组（JSON 列表，每个元素是一条用例对象）。"
    "传成 JSON 字符串、多包一层 {\"input\": ...}、单条时传对象，都会被规整回来并在返回值的"
    " normalized 里列出——但按数组直传才是省一次往返的写法。"))]

mcp = MCPServer(
    SERVER_NAME,
    version=SERVER_VERSION,
    instructions=(
        "测试伙伴的技能工具服务器。提供测试相关的确定性工具，编排交给调用方的 agent loop。"
    ),
)


@mcp.tool()
def ping() -> dict[str, Any]:
    """存活探针：返回服务器名、版本与状态，用于确认 MCP 连接已打通。"""
    return {
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "status": "ok",
    }


@mcp.tool()
def server_info() -> str:
    """返回本服务器的用途简介（中文），说明当前能力与后续规划。"""
    return (
        "这是「测试伙伴」（test-partner）的技能工具服务器，"
        "为 DeepTutor 实例提供测试相关的确定性工具能力。"
        "编排与对话由 DeepTutor 的 agent loop 负责，本服务器只提供工具，不改 DeepTutor 内核。\n"
        f"当前版本 {SERVER_VERSION}，已上线工具："
        "ping（存活探针）、server_info（本说明）、"
        "parse_har（HAR 体检：端点识别、脱敏后的请求样例）、"
        "validate_cases（用例确定性校验：格式/字段/覆盖/可执行请求块）、"
        "save_delivery（交付产物与收据 JSON 落盘，支持 Postman Collection）、"
        "execute_cases（按用例的 request 块真发请求、逐条判断言、出执行报告）、"
        "list_environments（列出配置页里配好的测试环境，只出名字与地址）。\n"
        "HAR 链路的工序剧本见技能 har-testing："
        "parse_har → 汇报体检摘要 → 确认测试意图 → 生成用例（按样例填 request 块）→ "
        "validate_cases 自检 → 定向修复一次再验 → save_delivery（默认 xlsx+postman）→ "
        "问用户要不要当场执行 → list_environments 看有哪些环境 → execute_cases(env=...)。\n"
        "执行要的 base_url 与 {{token}} 等变量在本机配置页 http://localhost:3789 的"
        "「测试环境」分区配一次即可，聊天里只说环境名——凭据不进聊天、不进模型上下文。\n"
        "postman 产物是标准 Collection v2.1，可直接导入 Apifox 或 Postman 执行；"
        "不想绕道 Apifox 就直接用 execute_cases 当场跑。"
    )


@mcp.tool()
def parse_har(har_path: str = "", har_content: str = "",
              max_endpoints: int = har_parse.DEFAULT_MAX_ENDPOINTS,
              include_samples: bool = True,
              max_samples: int = har_parse.DEFAULT_MAX_SAMPLES) -> dict[str, Any]:
    """HAR 体检：识别端点、业务域、鉴权方式、可重放档位与**脱敏后的请求样例**。

    输入二选一：
    - har_path：宿主机绝对路径，或相对仓库根 inbox/ 目录的文件名（如 "demo.har"）；
    - har_content：小 HAR 可直接贴 JSON 内容。
    max_endpoints 控制逐端点摘要的条数上限（默认 120，业务 host 优先）；
    include_samples / max_samples 控制请求样例（默认给前 40 个端点各一条 sample：
    method、归一化 URL、非凭证 query 键名、白名单请求头、掩码后的请求体、响应状态码）。
    样例是给用例填 request 块用的真实素材——不要凭空编造样例里没有的字段。

    脱敏是硬约束：凭证键的值先结构化掩码成 <redacted>，收尾再跑一遍哨兵扫描强制替换，
    token、cookie、口令类字段的值永远不出现在返回值里。
    文件缺失、JSON 坏损、非 HAR 结构等情况返回 {"ok": false, "error": ..., "hint": ...}。
    """
    return har_parse.parse_har_report(har_path=har_path, har_content=har_content,
                                      max_endpoints=max_endpoints,
                                      include_samples=include_samples,
                                      max_samples=max_samples)


@mcp.tool()
def validate_cases(cases: CasesArg, endpoints: Any = None) -> dict[str, Any]:
    """用例确定性校验：六字段、编号唯一、优先级、粒度、执行声明、端点覆盖率、请求块。

    cases：用例数组，每条至少含精简六字段（编号/标题/前置条件/操作步骤/预期结果/优先级），
    中英键名都认（case_id/title/preconditions/steps/expected/priority 亦可）。
    每条可另带**可选**的 request 块（可执行化）：
        {method, url, headers:[{key,value}], body:{mode,raw,language},
         assertions:[{type:"status"|"json_path"|"body_contains", expected, path?}]}
    带了就按 E10-E15 / W05 校验：method 合法、url 非空且与关联端点一致、断言写法合法、
    请求块里不许出现明文凭证（一律写 <redacted> 或 {{token}} 变量）、至少一条断言。
    endpoints：可选，直接把 parse_har 报告里的 endpoints 数组传进来，用于覆盖率核对。

    返回 {ok, errors:[{case_id, field, code, problem}], warnings:[...], coverage, summary}。
    errors 非空即未通过——按 case_id + field 定向修复后重新调用本工具复验。
    覆盖率缺口与「请求块没断言」只算 warning，不判失败。

    入参宽容：cases 传成 JSON 字符串、外面多包一层 {"input": ...}、只有一条时传对象
    而不是数组——这些都会被规整回来，并在返回值的 `normalized` 里如实列出规整动作
    （看到 normalized 就说明这次传歪了，下次按提示直传数组）。
    """
    return case_validate.validate_cases(cases, endpoints)


@mcp.tool()
def save_delivery(cases: CasesArg, title: Any = "测试用例",
                  format: Any = delivery.DEFAULT_FORMAT,
                  source_fingerprint: Any = "",
                  login_request: Any = None) -> dict[str, Any]:
    """把用例落盘成交付产物 + 收据 JSON，返回落盘路径清单。

    format：xlsx+postman（默认，HAR 链路双产物）| xlsx | csv | markdown | postman。
    - xlsx/csv/markdown 列名对齐 TAPD 用例库，给人读、给 TAPD 导入；
    - postman 产出 <标题slug>.postman_collection.json（Collection v2.1）：
      每条带 request 块的用例翻成一个可执行 item，assertions 翻成 pm.test 脚本，
      按所属模块分文件夹，baseUrl 走 collection 变量；没有 request 块的用例进占位 item
      并在返回值的 warnings 里计数。**可直接导入 Apifox（导入 → Postman）或 Postman 执行。**
    title：交付标题，用于目录名、产物标题与 collection 名。
    source_fingerprint：可选，填 parse_har 报告里的 source.fingerprint，让收据能溯源。
    login_request：可选，**HAR 链路且体检说支持登录换新时就带上**——把 parse_har 报告里
      `replay.login_request` 原样传进来（账密位置是占位符，真值不在里面）。带上之后
      这个批次就自带"怎么换新 token"，用户在工作台里选「登录换新」当场能跑，
      不用回聊天让你再传一遍那个对象。

    落盘到 deliveries/<YYYYMMDD-HHmmss>-<title-slug>/，含全部产物文件与 receipt.json
    （输入指纹、生成时间、校验结果摘要、产物清单与各自 sha256）。

    入参宽容：cases 的 JSON 字符串/单键包裹/单个用例对象，format 的大小写与空格，
    都会被规整回来；规整动作在返回值与收据的 `normalized` 里如实列出。
    """
    return delivery.save_delivery(cases, fmt=format, title=title,
                                  source_fingerprint=source_fingerprint,
                                  login_request=login_request)


@mcp.tool()
def list_environments() -> dict[str, Any]:
    """列出本机配置页里已配好的**测试环境**（名字 + 环境地址 + 变量个数）。

    执行用例前先调这个：有已配环境就问用户「用哪个环境」，然后把环境名传给
    execute_cases 的 env 参数——**不要让用户在聊天里贴 token**。
    没有任何环境时，引导用户去配置页 http://localhost:3789 的「测试环境」分区配一次
    （环境名、base_url、变量键值对），这样凭据存在他自己机器上，不经过聊天。

    返回 {ok, count, environments:[{name, base_url, variable_count}], config_url}。
    **变量的值与键名都不在返回值里**——值只在网关进程内解析给真实请求用。
    """
    from server import gateway
    from server.gateway.config import default_config

    rows = default_config().environment_index()
    return {
        "ok": True,
        "count": len(rows),
        "environments": rows,
        "config_url": gateway.gateway_url(),
        "hint": ("执行时把环境名传给 execute_cases 的 env 参数，变量由网关在本机解析。"
                 if rows else
                 "本机还没有配置任何测试环境。让用户去 http://localhost:3789 的"
                 "「测试环境」分区配一个：填环境名、base_url、以及用例里 {{变量}} 对应的值。"
                 "这样凭据存在他自己机器上，不进聊天、不进模型上下文。"),
    }


@mcp.tool()
def execute_cases(cases: CasesArg, base_url: str = "", variables: Any = None,
                  case_ids: Any = None, timeout_s: int = execute.DEFAULT_TIMEOUT_S,
                  delivery_dir: str = "", title: str = "",
                  env: str = "", auth: str = execute.DEFAULT_AUTH_MODE,
                  login_request: Any = None,
                  auth_token_variable: str = execute.DEFAULT_TOKEN_VAR
                  ) -> dict[str, Any]:
    """**真发请求执行用例**：按每条用例的 request 块逐条请求、逐条判断言、出执行报告。

    用例自带的 request 块本身就是可执行的请求描述，不必绕道 Apifox/Postman 导入。
    同步逐条执行（不并发，保证日志可读与被测系统安全）。

    cases：用例数组（同 validate_cases，入参形状宽容）。**执行范围默认是本次生成的
      全部用例**——缩减范围必须是用户明确要求的，不要自己只挑几条传进来。
    env：**凭据的正门，优先用它**。传配置页里配好的环境名（先调 list_environments 看
      有哪些），网关在本机解析出 base_url 与全部变量值，**值不经过模型、不进聊天**。
    base_url：被测环境地址（如 https://api.example.com），替换用例里的 {{baseUrl}}。
      传了 env 就可以不传；两个都传时**显式的 base_url 覆盖环境里配的地址**。
    variables：可选键值映射，替换 {{token}} 之类其余变量。与 env 的合并规则：
      环境变量先铺底，这里传的同名键覆盖它（便于临时改一个值）。
      **不要主动向用户索要 token 明文**——让他去配置页配环境。用户坚持在聊天里给值时
      本参数仍可用，但要先提醒一次风险（值会进聊天记录与模型上下文）。
      这些值只用于真实请求，不会出现在返回结果、执行报告或任何日志里
      （响应体里回显的凭证也会被换回占位）。
    case_ids：可选，只执行指定编号（数组或逗号分隔字符串）；不传则全执行。
    timeout_s：单请求超时秒数，默认 15（夹取 1-120）。
    delivery_dir：可选，把执行报告并进 save_delivery 返回的那个交付目录；
      不传（或不是 deliveries/ 下的既有目录）就在 deliveries/ 下新建执行报告目录。
    auth：凭据怎么来，`none` | `env`（默认，现状行为）| `login`。
      - `none`：环境变量表一个都不注入（只认显式 variables），跑公开接口用；
      - `env`：用环境「变量表」里配好的值（现状）；
      - `login`：**登录换新**——执行前先重放登录请求拿一个新 token 注入后续用例，
        等价 Postman 的 pre-request auth。HAR 里录的 token 会过期，档A HAR
        （parse_har 报告 replay.auth_refresh.supported 为 true）优先用这个模式：
        用户只要在配置页那个环境里配一次 login_username / login_password 两个变量，
        以后每轮自动换新，不用再手工贴 token。
    login_request：`auth="login"` 时必给——把 parse_har 报告里的
      `replay.login_request` 原样传进来（它的 body 里账密位置是 {{login_username}} /
      {{login_password}} 占位，真值在用户本机配置页里，不经聊天）。
    auth_token_variable：换来的新 token 注入成哪个变量名，默认 `token`
      （用例里写 `Bearer {{token}}` 就能用上）。

    登录换新失败（账密缺失、登录 4xx/5xx、取不到 token、登录端点跨 host）→
    **整轮不执行**，返回 {ok:false, error:"LOGIN_*"} 与可读原因，不落报告，
    也不会带着坏 token 硬跑出一堆 401。

    安全红线（命中即标 skipped，绝不发请求）：
    - 用例 URL 解析出的 host 与 base_url 的 host 不一致 → 跨 host 不发；也不跟随重定向；
      **登录请求同样受这条约束**，登录端点跨 host 直接中止整轮，不把账密发去别处；
    - 变量没提供、渲染后仍残留 {{var}} → 不发（reason 只写变量名）；
    - 没有 request 块 → 人执行用例，不发；request 块没有断言 → 跑完无从判定成败，不发。

    返回 {ok, summary:{total, executed, passed, failed, skipped, verdict},
    received_case_count（本次收到几条用例，用来对账执行范围）, selected_case_count,
    results:[{case_id, name, passed, assertions:[{type,expected,actual,passed}],
    status_code, elapsed_ms, error}], environment,
    auth:{mode, refreshed, login_endpoint, token_variable, note}（只有模式与端点，
    账密与新 token 一个字节都不在里面）, report_dir, files, report_hint}。
    env 写错时返回 {ok:false, error:"ENV_NOT_FOUND"} 并列出已配环境名。
    ok 只表示这轮执行跑起来了，**不表示用例全过**；失败条目如实计数——
    失败可能是被测系统真有问题，这正是测试的意义，不要解读成「用例生成得不好」。
    """
    return execute.execute_cases(cases, base_url=base_url, variables=variables,
                                 case_ids=case_ids, timeout_s=timeout_s,
                                 delivery_dir=delivery_dir, title=title, env=env,
                                 auth=auth, login_request=login_request,
                                 auth_token_variable=auth_token_variable)


def start_gateway() -> str:
    """先把配置面拉起来，返回它的地址（起不来时返回空串，不拦 MCP 面）。

    配置面是"填凭据的地方"，MCP 面是"干活的地方"——前者挂了后者照样能用，
    所以这里所有异常都吞掉只打提示，绝不让一个 Web 页拖垮常驻服务。
    """
    if os.environ.get("TEST_PARTNER_GATEWAY", "1").strip().lower() in ("0", "false", "no"):
        return ""
    try:
        from server import gateway

        port = gateway.gateway_port()
        gateway.serve_in_thread(port=port, version=SERVER_VERSION,
                                mcp_host=HOST, mcp_port=PORT)
        return gateway.gateway_url(port)
    except Exception as exc:  # noqa: BLE001 - 配置面可选，不许拦住 MCP 面
        print(f"[test-partner] 配置页没起来（MCP 面不受影响）：{exc}")
        return ""


def main() -> None:
    """先起配置面（独立线程），再以 streamable-http 传输启动 MCP 面（主线程）。

    host/port 在 2.0 SDK 里是 run() 的关键字参数，不再走构造函数或 settings。
    绑 0.0.0.0 时 SDK 不会自动开 DNS-rebinding 保护，因此容器侧用
    host.docker.internal 作 Host 头访问不会被拦。
    """
    config_url = start_gateway()
    if config_url:
        print(f"[test-partner] 配置页：{config_url}   ← 浏览器打开这个地址填凭据")
    print(f"[test-partner] MCP 面：http://{HOST}:{PORT}/mcp   (Ctrl+C 停止)")
    mcp.run(transport="streamable-http", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
