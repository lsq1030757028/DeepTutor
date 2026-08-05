"""mcp-server-tapd 宿主机运行时验证脚本。

连上正在跑的 mcp-server-tapd（streamable-http），跑 initialize + list_tools，
把工具名清单打到 stdout —— 这份清单是 M3 用来替换 req-testing 剧本里工具名占位的来源。

只做 initialize / list_tools，**不 call 任何工具**：mcp-server-tapd 的每个工具都会
真打 TAPD API，验证阶段不碰真实数据。

用法（服务器需已在跑；用 test-partner 主 venv 的 mcp 2.0.0 SDK 跑本脚本）：
    ..\\.venv\\Scripts\\python.exe verify_tapd.py
    ..\\.venv\\Scripts\\python.exe verify_tapd.py --url http://127.0.0.1:3795/mcp
    ..\\.venv\\Scripts\\python.exe verify_tapd.py --json   # 只吐 JSON 工具清单

注意 SDK API：mcp 2.0.0 把客户端传输入口从 1.x 的 `streamablehttp_client`
更名为 `streamable_http_client`，且只 yield (read, write) 两元组；
结果模型是 snake_case（input_schema，1.x 叫 inputSchema）。
本脚本跑在 test-partner 主 venv（mcp==2.0.0），
被验证的服务端跑在 tapd-runtime/.venv（mcp==1.29.0），两边协议兼容、互不干扰。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

DEFAULT_URL = "http://127.0.0.1:3795/mcp"
#: 服务端 FastMCP 实例名，见 mcp_server_tapd/server.py 的 FastMCP("mcp-tapd")
EXPECTED_SERVER_NAME = "mcp-tapd"


class VerifyError(AssertionError):
    """验证断言失败。"""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)
    print(f"  PASS  {message}")


async def verify(url: str, as_json: bool) -> list[dict]:
    if not as_json:
        print(f"[verify] 连接 {url}")
    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init = await session.initialize()
            if not as_json:
                print(f"[verify] initialize -> {init.server_info.name} "
                      f"v{init.server_info.version} (protocol {init.protocol_version})")
            if not as_json:
                check(init.server_info.name == EXPECTED_SERVER_NAME,
                      f"服务器名为 {EXPECTED_SERVER_NAME}")

            listed = await session.list_tools()
            tools = [
                {
                    "name": tool.name,
                    "args": sorted((tool.input_schema or {}).get("properties", {})),
                    "required": sorted((tool.input_schema or {}).get("required", []) or []),
                    "summary": (tool.description or "").strip().splitlines()[0]
                    if tool.description else "",
                }
                for tool in listed.tools
            ]
            tools.sort(key=lambda t: t["name"])

            if as_json:
                print(json.dumps(tools, ensure_ascii=False, indent=2))
                return tools

            print(f"\n[verify] list_tools -> {len(tools)} 个工具\n")
            for item in tools:
                print(f"  - {item['name']}({', '.join(item['args'])})")
                if item["summary"]:
                    print(f"      {item['summary']}")
            print()
            check(len(tools) > 0, "list_tools 返回非空")
            return tools


def main() -> int:
    parser = argparse.ArgumentParser(description="验证宿主机上的 mcp-server-tapd")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"MCP 端点，默认 {DEFAULT_URL}")
    parser.add_argument("--json", action="store_true",
                        help="只输出工具清单 JSON（便于灌进剧本模板）")
    args = parser.parse_args()

    try:
        asyncio.run(verify(args.url, args.json))
    except VerifyError as exc:
        print(f"\n[verify] FAIL  {exc}", file=sys.stderr)
        return 1
    except BaseException as exc:  # noqa: BLE001 - 顶层收口，展开 anyio 的 ExceptionGroup
        print("\n[verify] ERROR", file=sys.stderr)
        traceback.print_exception(exc, file=sys.stderr)
        return 2

    if not args.json:
        print("[verify] ALL PASS 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
