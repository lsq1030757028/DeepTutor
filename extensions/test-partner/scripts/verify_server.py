"""测试伙伴能力网关本地验证脚本（回归用）。

两个面都验：

- **MCP 面**：连上正在运行的 streamable-http 服务器，跑 initialize + list_tools +
  call ping 等，逐项断言；
- **配置面**：GET 一次配置页，断言 200、是中文页面、**且页面里不含任何已配置的凭据原值**。

全过打印 PASS 并以 0 退出，任一失败非 0 退出。

用法（服务器需已在跑）：
    .venv\\Scripts\\python.exe scripts\\verify_server.py
    .venv\\Scripts\\python.exe scripts\\verify_server.py --url http://127.0.0.1:3790/mcp
    .venv\\Scripts\\python.exe scripts\\verify_server.py --gateway-url http://127.0.0.1:3789
    .venv\\Scripts\\python.exe scripts\\verify_server.py --skip-gateway

注意 SDK API：mcp 2.0.0 把客户端传输入口从 1.x 的 `streamablehttp_client`
更名为 `streamable_http_client`，且只 yield (read, write) 两元组。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:3790/mcp"
DEFAULT_GATEWAY_PORT = 3789
DEFAULT_GATEWAY_URL = f"http://127.0.0.1:{DEFAULT_GATEWAY_PORT}"
EXPECTED_TOOLS = {"ping", "server_info", "parse_har", "validate_cases",
                  "save_delivery", "execute_cases", "list_environments"}
EXPECTED_PING = {"server": "test-partner", "version": "0.8.0", "status": "ok"}
#: 每个 M2/M2.5/M2.6 工具必须暴露的入参（防止改签名后 agent 侧调不通）
EXPECTED_TOOL_ARGS = {
    "parse_har": {"har_path", "har_content", "max_endpoints", "include_samples"},
    "validate_cases": {"cases", "endpoints"},
    "save_delivery": {"cases", "title", "format", "source_fingerprint"},
    "execute_cases": {"cases", "base_url", "variables", "case_ids", "timeout_s",
                      "delivery_dir", "env"},
    "list_environments": set(),          # 无入参，但必须在工具面上
}
#: 供 parse_har 走一遍真实调用的极小 HAR（不落任何文件，纯内存）
SMOKE_HAR = json.dumps({"log": {"entries": [{
    "startedDateTime": "2026-08-04T09:00:00.000Z",
    "request": {"method": "GET", "url": "https://api.example.com/api/v1/orders/10086",
                "headers": [{"name": "Authorization", "value": "Bearer smoke-token-0123456789"}],
                "cookies": []},
    "response": {"status": 200, "headers": [], "cookies": [],
                 "content": {"mimeType": "application/json", "text": "{\"code\":0}"}},
}]}}, ensure_ascii=False)


class VerifyError(AssertionError):
    """验证断言失败。"""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)
    print(f"  PASS  {message}")


async def verify(url: str, gateway_url: str = DEFAULT_GATEWAY_URL) -> None:
    print(f"[verify] 连接 {url}")
    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # 1) initialize
            init = await session.initialize()
            print(f"[verify] initialize -> {init.server_info.name} v{init.server_info.version} "
                  f"(protocol {init.protocol_version})")
            check(init.server_info.name == "test-partner", "服务器名为 test-partner")

            # 2) list_tools
            listed = await session.list_tools()
            names = {tool.name for tool in listed.tools}
            print(f"[verify] list_tools -> {sorted(names)}")
            for tool in listed.tools:
                print(f"         - {tool.name}: {tool.description}")
            check(EXPECTED_TOOLS <= names, f"工具齐备 {sorted(EXPECTED_TOOLS)}")

            # 3) call ping
            result = await session.call_tool("ping")
            check(getattr(result, "is_error", False) is not True, "ping 调用未报错")

            payload = getattr(result, "structured_content", None)
            if payload is None:
                raw = result.content[0].text  # type: ignore[union-attr]
                payload = json.loads(raw)
            print(f"[verify] call ping -> {json.dumps(payload, ensure_ascii=False)}")
            check(payload == EXPECTED_PING, f"ping 返回等于 {EXPECTED_PING}")

            # 4) call server_info（顺带确认第二个工具可用）
            info = await session.call_tool("server_info")
            info_text = info.content[0].text  # type: ignore[union-attr]
            check("测试伙伴" in info_text, "server_info 返回中文简介")
            check(
                all(k in info_text for k in ("parse_har", "validate_cases",
                                             "save_delivery", "execute_cases")),
                "server_info 提到 M2/M2.6 的四个工具",
            )

            # 5) M2 三工具的签名（入参名是 agent 侧的调用契约，改了就调不通）
            by_name = {tool.name: tool for tool in listed.tools}
            for name, expected_args in EXPECTED_TOOL_ARGS.items():
                tool = by_name[name]
                # 2.0 SDK 的结果模型是 snake_case：input_schema（1.x 叫 inputSchema）
                props = set((tool.input_schema or {}).get("properties", {}))
                check(expected_args <= props, f"{name} 暴露入参 {sorted(expected_args)}")
                check(bool(tool.description and tool.description.strip()),
                      f"{name} 带中文说明")

            # 6) parse_har 真调一次（内存里的极小 HAR，不落文件、不碰 inbox）
            har_result = await session.call_tool("parse_har", {"har_content": SMOKE_HAR})
            har_payload = getattr(har_result, "structured_content", None)
            if har_payload is None:
                har_payload = json.loads(har_result.content[0].text)  # type: ignore[union-attr]
            check(har_payload.get("ok") is True, "parse_har 体检返回 ok")
            check(har_payload["endpoints"][0]["path"] == "/api/v1/orders/:id",
                  "parse_har 路径参数已归一化为 :id")
            check("smoke-token-0123456789" not in json.dumps(har_payload, ensure_ascii=False),
                  "parse_har 输出里没有凭证值")
            sample = har_payload["endpoints"][0].get("sample") or {}
            check(sample.get("method") == "GET" and sample.get("url", "").endswith(
                "/api/v1/orders/:id"), "parse_har 给出了请求样例（method + 归一化 URL）")
            check(all(h.get("value") == "<redacted>" for h in sample.get("headers", [])
                      if h.get("key", "").lower() == "authorization"),
                  "parse_har 样例里的凭证头值已脱敏")

            # 7) validate_cases 真调一次（一条故意缺优先级的用例，应被拦下）
            bad_case = {"编号": "TC-001", "标题": "冒烟用例", "前置条件": "无",
                        "操作步骤": ["调用 GET /api/v1/orders/:id"],
                        "预期结果": "状态码 200", "优先级": "P0"}
            val = await session.call_tool("validate_cases", {"cases": [bad_case]})
            val_payload = getattr(val, "structured_content", None)
            if val_payload is None:
                val_payload = json.loads(val.content[0].text)  # type: ignore[union-attr]
            check(val_payload.get("ok") is False, "validate_cases 拦下非法优先级")
            check(val_payload["errors"][0]["code"] == "E08_PRIORITY_INVALID",
                  "validate_cases 报错码定位到优先级字段")

            # 8) execute_cases 真调一次——故意不给 base_url，走入参闸返回，
            #    **不发任何网络请求、不落任何文件**（冒烟脚本不该去打真实被测系统）
            exe = await session.call_tool(
                "execute_cases", {"cases": [dict(bad_case, 优先级="高")], "base_url": ""})
            exe_payload = getattr(exe, "structured_content", None)
            if exe_payload is None:
                exe_payload = json.loads(exe.content[0].text)  # type: ignore[union-attr]
            check(exe_payload.get("ok") is False, "execute_cases 在缺 base_url 时不执行")
            check(exe_payload.get("error") == "BASE_URL_REQUIRED",
                  "execute_cases 缺 base_url 的错误码正确")

            # 9) list_environments 真调一次——它的返回值会进模型上下文，
            #    所以除了形状，还要断言**变量的值一个都不在里面**。
            envs = await session.call_tool("list_environments")
            env_payload = getattr(envs, "structured_content", None)
            if env_payload is None:
                env_payload = json.loads(envs.content[0].text)  # type: ignore[union-attr]
            check(env_payload.get("ok") is True, "list_environments 返回 ok")
            rows = env_payload.get("environments") or []
            check(env_payload.get("count") == len(rows),
                  f"list_environments 报告 {len(rows)} 个已配环境")
            check(all(set(row) == {"name", "base_url", "variable_count"}
                      for row in rows),
                  "list_environments 每条只有 name / base_url / variable_count")
            # 指向的必须是**这个实例自己的**配置页：开发实例跑在 3788 上时它就该说
            # 3788，写死 3789 会把「引导用户去哪填凭据」这件事指错地方。
            expected_port = urlsplit(gateway_url).port or DEFAULT_GATEWAY_PORT
            check(str(expected_port) in str(env_payload.get("config_url", "")),
                  f"list_environments 指向本机配置页（:{expected_port}）")
            check_environment_values_absent(
                json.dumps(env_payload, ensure_ascii=False),
                "list_environments 的返回值")


def environment_values() -> list[str]:
    """本机已配测试环境里的全部变量原值（只在本脚本内用来做「不该出现」的断言）。"""
    from server.gateway.config import GatewayConfig

    values: list[str] = []
    for row in GatewayConfig().load_environments():
        values.extend(value for value in row["variables"].values() if value)
    return values


def check_environment_values_absent(text: str, where: str) -> None:
    """断言 `text` 里没有任何测试环境变量的原值（或够长的前缀片段）。"""
    values = environment_values()
    for value in values:
        check(value not in text, f"{where}不含某个环境变量的原值")
        if len(value) >= 12:
            check(value[:8] not in text, f"{where}不含该值的前 8 位片段")
    check(True, f"{where}：{len(values)} 个环境变量值全部未出现")


def verify_gateway(base_url: str) -> None:
    """配置面存活断言：页面能打开，而且**没有把任何凭据印在上面**。

    "不含令牌值"是这里唯一真正重要的一条：配置页是凭据离本机最近的地方，
    一旦它回显，凭据就跟着浏览器历史、截图、录屏一起跑了。
    """
    print(f"[verify] 配置页 {base_url}")
    request = urllib.request.Request(base_url, headers={"Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            status = response.status
            body = response.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise VerifyError(
            f"配置页打不开（{base_url}）：{exc}。"
            "网关是不是没起来，或者配置面端口被 TEST_PARTNER_GATEWAY_PORT 改过？"
        ) from exc

    check(status == 200, "配置页返回 200")
    check("测试伙伴 · 能力网关" in body, "配置页是中文的网关页面")
    check("TAPD 访问令牌" in body, "配置页有 TAPD 令牌表单")
    check("新增环境" in body, "配置页有「测试环境」分区")
    check_environment_values_absent(body, "配置页")

    from server.gateway.config import GatewayConfig

    secrets = GatewayConfig().read_secrets()
    configured = {name: value for name, value in secrets.items() if value}
    for name, value in configured.items():
        check(value not in body, f"配置页不含 {name} 的原值")
        if len(value) >= 12:
            check(value[:8] not in body, f"配置页不含 {name} 的前 8 位片段")
    check(True, f"已配置凭据 {sorted(configured) or '(无)'} 全部未回显")


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 test-partner 能力网关")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP 端点，默认 {DEFAULT_URL}")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL,
                        help=f"配置页地址，默认 {DEFAULT_GATEWAY_URL}")
    parser.add_argument("--skip-gateway", action="store_true",
                        help="只验 MCP 面（用于 TEST_PARTNER_GATEWAY=0 起的实例）")
    args = parser.parse_args()

    try:
        asyncio.run(verify(args.url, args.gateway_url))
        if not args.skip_gateway:
            verify_gateway(args.gateway_url)
    except VerifyError as exc:
        print(f"\n[verify] FAIL  {exc}", file=sys.stderr)
        return 1
    except BaseException as exc:  # noqa: BLE001 - 顶层收口，展开 anyio 的 ExceptionGroup
        print("\n[verify] ERROR", file=sys.stderr)
        traceback.print_exception(exc, file=sys.stderr)
        return 2

    print("\n[verify] ALL PASS 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
