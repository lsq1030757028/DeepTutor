"""假子服务，给 supervisor 的生命周期测试当替身。

真的 mcp-server-tapd 起不得：它在 import 期就拿令牌去打 https://api.tapd.cn/users/info，
测试里起它等于往 TAPD 发假凭据。所以 supervisor 的 `command` 是可注入的，
测试注入本脚本。

三种模式，对应三条要测的路径：

    --mode serve   绑端口并挂住（成功路径）
    --mode crash   往 stdout 吐一段 401 traceback 然后退出（启动失败 + 诊断分类）
    --mode silent  什么都不打就退出（NO_LOG 分支）

`serve` 还会把它拿到的几个环境变量打进 stdout，供"清场与注入是否生效"的断言用——
**只打键名与是否存在，不打值**，测试自己就得守住"令牌不进日志"这条线。
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time

WATCHED = ("TAPD_ACCESS_TOKEN", "TAPD_API_BASE_URL", "TAPD_API_USER",
           "TAPD_API_PASSWORD", "TAPD_BASE_URL", "BOT_URL")

CRASH_LOG = """Traceback (most recent call last):
  File "site-packages/mcp_server_tapd/tapd.py", line 88, in get_user_info
    response.raise_for_status()
requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url:
https://api.tapd.cn/users/info?s=mcp
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--mode", default="serve",
                        choices=["serve", "crash", "silent"])
    args = parser.parse_args()

    if args.mode == "silent":
        return 1
    if args.mode == "crash":
        sys.stdout.write(CRASH_LOG)
        sys.stdout.flush()
        return 1

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(8)
    # 只报「在不在」，绝不打值——这个日志文件会被测试逐字检查。
    for name in WATCHED:
        sys.stdout.write(f"[fake] {name}={'set' if os.environ.get(name) else 'unset'}\n")
    sys.stdout.write(f"[fake] serving on 127.0.0.1:{args.port}\n")
    sys.stdout.flush()
    try:
        while True:
            try:
                conn, _ = listener.accept()
                conn.close()
            except OSError:
                break
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()
        time.sleep(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
