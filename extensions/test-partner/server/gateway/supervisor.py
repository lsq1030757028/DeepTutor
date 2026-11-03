"""子服务管家：网关代管 mcp-server-tapd 子进程（启/停/重启/状态/失败诊断）。

取代 `tapd-runtime\\setup_tapd.cmd` 的 [2/4]-[3/4] 两步。差别在于：cmd 脚本起的是一个
用户看得见、关得掉的最小化窗口，本模块起的是网关的子进程——生死跟着网关走，
用户面前只有配置页上的一个状态灯和一个"重启"按钮。

## 环境注入的三件事（照抄 start_tapd.cmd 的安全约定，一条不少）

1. **清场**：`TAPD_ACCESS_TOKEN` / `TAPD_API_BASE_URL` / `TAPD_BASE_URL` /
   `TAPD_API_USER` / `TAPD_API_PASSWORD` / `BOT_URL` 先从继承来的环境里删干净。
   这台机器的环境里本来就有一个 TAPD token，不清的话子进程会拿一个谁也没在配置页里
   填过的凭据悄悄起来——实测踩过。
2. **只注入白名单**：`TAPD_ACCESS_TOKEN`（必填）与 `BOT_URL`（可选）从 secrets.env 来。
   `TAPD_API_USER` / `TAPD_API_PASSWORD` 永远不注入：那条路是 Basic Auth，base64 可逆，
   等于明文托管账号密码（审计条件 B）。
3. **钉死** `TAPD_API_BASE_URL=https://api.tapd.cn`。包对 base_url 无任何校验，
   这一行是凭据流向的唯一闸门，不参数化、不给页面改。

补一条 python-dotenv 的坑：包的 `app_config.py` 会 `load_dotenv()` 向上逐级找 `.env`，
一路能找到 `tapd-runtime\\.env`。好在 `load_dotenv` 默认 `override=False`——已经在环境里
的键不会被文件覆盖，所以我们注入的令牌稳赢那份历史 `.env`。但历史 `.env` 里如果有我们
**没注入**的键（比如 BOT_URL），它还是会生效；排障时记得这一条。

## 生命周期的局限（Windows，说清楚不掩饰）

`atexit` + 显式 `terminate()` 能覆盖"网关正常退出"和"Ctrl+C"。覆盖不了的是网关被
任务管理器 End Task / `taskkill /f` 强杀——那种情况下 TAPD 子进程会遗留在 3795 上。
网关下次起来会探到端口已在服务，`status()` 报 `running=True, managed=False`
（"活着但不是我起的"），页面上给"重启"按钮让用户接管：重启会先按端口找到并杀掉遗留
进程再起新的。要做到内核级联动得上 Windows Job Object（需要 pywin32，主 venv 里没有），
0.6 不引这个依赖。
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from server.gateway.config import GatewayConfig, default_config

#: 起子进程前要从继承环境里清掉的键（顺序无关，照抄 start_tapd.cmd 的清场清单）
POLLUTED_ENV_KEYS = (
    "TAPD_ACCESS_TOKEN",
    "TAPD_API_BASE_URL",
    "TAPD_BASE_URL",
    "TAPD_API_USER",
    "TAPD_API_PASSWORD",
    "BOT_URL",
)

#: 钉死的 API 域名。见模块 docstring 第 3 条——不要参数化。
PINNED_API_BASE_URL = "https://api.tapd.cn"

#: 从 secrets.env 注入给 TAPD 子进程的键（白名单；DEEPTUTOR_TOKEN 不在此列，
#: 那是给 deeptutor_client 调本地 API 用的，跟 TAPD 无关）
INJECTED_SECRET_KEYS = ("TAPD_ACCESS_TOKEN", "BOT_URL")

#: 启动失败的日志特征 -> 诊断。命中第一条即停，**顺序即优先级**：
#: 端口占用必须排在凭据之前，否则"换个端口就好"会被误诊成"令牌坏了"。
#:
#: 这张表与 `tapd-runtime/register_tapd.py` 的 START_SYMPTOMS 是同一份知识的两个副本。
#: 不合并是有意的：register_tapd.py 必须能脱离本包、只用标准库单独跑（排障路径）。
#: 改其中一处务必同步改另一处。
START_SYMPTOMS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("10048", "address already in use", "只允许使用一次"),
        "PORT_TAKEN",
        "端口已经被别的程序占了。先确认是不是上一次的 TAPD 服务还开着"
        "（配置页点「重启」会先杀掉遗留进程再起）；确实是别的程序占的话，"
        "改端口要同时改 config/gateway.json 里 services.tapd 的 port 与 mcp_url。",
    ),
    (
        ("401", "unauthorized", "403", "forbidden"),
        "TOKEN_REJECTED",
        "TAPD 把这个令牌拒了（401/403）。官方包在启动那一刻就拿令牌去打 "
        "https://api.tapd.cn/users/info 做校验，令牌错、过期、或者没授权到任何项目，"
        "进程当场退出，不会「先起来再报错」。回 TAPD 个人设置重新生成一个令牌，"
        "在本页面重新粘贴保存。",
    ),
    (
        ("connectionerror", "maxretryerror", "getaddrinfo",
         "newconnectionerror", "timed out"),
        "NETWORK_UNREACHABLE",
        "这台机器连不上 TAPD 的服务器。启动期那次 /users/info 校验请求根本没发出去——"
        "常见于公司网络要走代理、VPN 没连、或者 DNS 拦了 api.tapd.cn。"
        "网络通了以后在本页面点「重启 TAPD 服务」。",
    ),
    (
        ("modulenotfounderror", "no module named"),
        "RUNTIME_BROKEN",
        "tapd-runtime\\.venv 里的依赖不全。按 tapd-runtime/README.md「重建 .venv」"
        "那节重装，然后回本页面点「重启 TAPD 服务」。",
    ),
)

UNKNOWN_DIAGNOSIS = (
    "UNKNOWN",
    "没认出是哪种已知故障。最常见的还是令牌不对——官方包启动即校验令牌，"
    "令牌坏了进程直接退出。换个新令牌重试是第一顺位；仍不行就看完整启动日志。",
)


#: 端口探测超时。回环上一个活着的监听套接字握手是亚毫秒级的，0.6 秒绰绰有余。
#: 之所以要挑明：这台机器上连一个**关着**的 127.0.0.1 端口不是立刻 refused 而是
#: 一路等到超时（本地有过滤驱动在丢 SYN），所以这个值直接决定"服务没起来"时
#: 状态页要转多久的圈。别为了保险往大了调。
PROBE_TIMEOUT_S = 0.6


def probe_port(port: int, host: str = "127.0.0.1",
               timeout: float = PROBE_TIMEOUT_S) -> bool:
    """端口上有没有人在服务。`create_connection` 成功即算活。"""
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def classify_log(text: str) -> tuple[str, str]:
    """把启动日志分类成 (错误码, 中文诊断)。空日志单独一类。"""
    haystack = (text or "").lower()
    if not haystack.strip():
        return ("NO_LOG",
                "子进程连一行日志都没写出来，说明可执行文件根本没跑起来。"
                "先确认 tapd-runtime\\.venv 建好了（README「重建 .venv」一节）。")
    for needles, code, advice in START_SYMPTOMS:
        if any(needle in haystack for needle in needles):
            return code, advice
    return UNKNOWN_DIAGNOSIS


class SupervisorError(RuntimeError):
    """带错误码的可读失败。`code` 给页面做分支，`args[0]` 是给人看的中文。"""

    def __init__(self, message: str, code: str = "SUPERVISOR_ERROR") -> None:
        super().__init__(message)
        self.code = code


class TapdSupervisor:
    """mcp-server-tapd 子进程的管家。一个网关进程一个实例。"""

    #: 服务名，用在返回值与日志里
    name = "tapd"

    def __init__(
        self,
        *,
        config: GatewayConfig | None = None,
        runtime_dir: str | None = None,
        log_path: str | None = None,
        command: list[str] | None = None,
        startup_timeout_s: float = 25.0,
        probe_host: str = "127.0.0.1",
    ) -> None:
        self.config = config or default_config()
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.runtime_dir = os.path.abspath(runtime_dir or
                                           os.path.join(repo_root, "tapd-runtime"))
        # 日志沿用 tapd-runtime/tapd-start.log：这个名字在 README 和排障脚本里
        # 已经是约定，换名字等于让老文档说谎。该文件已被 tapd-runtime/.gitignore 拦住。
        self.log_path = os.path.abspath(log_path or
                                        os.path.join(self.runtime_dir, "tapd-start.log"))
        self._command_override = list(command) if command else None
        self.startup_timeout_s = startup_timeout_s
        self.probe_host = probe_host
        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        atexit.register(self._atexit_stop)

    # ── 配置派生 ────────────────────────────────────────────────────────────

    @property
    def port(self) -> int:
        return int(self.config.service_settings(self.name).get("port") or 3795)

    @property
    def executable(self) -> str:
        """PINNED 版本的可执行文件：tapd-runtime/.venv/Scripts/mcp-server-tapd.exe。

        走 venv 的控制台脚本，**不要**用 `python -m mcp_server_tapd.server`：
        包的 `__init__` 已经 import 过该模块，runpy 会再执行一遍，
        import 期那次 `/users/info` 请求会发两次。
        """
        scripts = "Scripts" if os.name == "nt" else "bin"
        binary = "mcp-server-tapd.exe" if os.name == "nt" else "mcp-server-tapd"
        return os.path.join(self.runtime_dir, ".venv", scripts, binary)

    def build_command(self) -> list[str]:
        if self._command_override is not None:
            return list(self._command_override)
        return [
            self.executable,
            "--mode=streamable-http",
            # 绑 0.0.0.0 是 Docker 容器能连到宿主机服务的必要条件；
            # 代价与适用边界见 tapd-runtime/README.md「为什么绑 0.0.0.0」。
            "--host=0.0.0.0",
            f"--port={self.port}",
        ]

    def build_env(self) -> dict[str, str]:
        """造子进程的环境：清场 → 注入白名单 → 钉死 base_url。

        没配令牌就抛 `SupervisorError(code="NO_TOKEN")`——宁可不起，
        也不让它拿继承来的野令牌起来。
        """
        env = dict(os.environ)
        for key in POLLUTED_ENV_KEYS:
            env.pop(key, None)

        secrets = self.config.read_secrets()
        token = (secrets.get("TAPD_ACCESS_TOKEN") or "").strip()
        if not token:
            raise SupervisorError(
                "还没有配置 TAPD 令牌。在本页面的「TAPD 访问令牌」里粘贴后保存即可。",
                code="NO_TOKEN")

        for key in INJECTED_SECRET_KEYS:
            value = (secrets.get(key) or "").strip()
            if value:
                env[key] = value

        env["TAPD_API_BASE_URL"] = PINNED_API_BASE_URL
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    # ── 状态 ────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """探端口 + 看进程。返回值里**没有任何凭据**，可以直接给页面。

        - `running`：端口上有人在服务（唯一权威的"能用"判据）。
        - `managed`：这个服务是本网关起的（有活着的子进程句柄）。
          `running and not managed` = 遗留进程或用户手工起的，重启会先接管。
        """
        with self._lock:
            process = self._process
            alive = process is not None and process.poll() is None
            pid = process.pid if alive and process is not None else None
            exit_code = None
            if process is not None and process.poll() is not None:
                exit_code = process.returncode
        port = self.port
        port_open = probe_port(port, self.probe_host)
        return {
            "service": self.name,
            "running": port_open,
            "managed": bool(alive),
            "pid": pid,
            "port": port,
            "exit_code": exit_code,
            "token_configured": bool(self.config.get_secret("TAPD_ACCESS_TOKEN")),
            "log_path": self.log_path,
        }

    # ── 启停 ────────────────────────────────────────────────────────────────

    def start(self) -> dict[str, Any]:
        """起子进程并等端口活。返回 `{ok, code, message, status}`，不抛异常给上层。

        端口已经在服务时直接复用（幂等），不重复起。
        """
        with self._lock:
            if probe_port(self.port, self.probe_host):
                return self._ok("端口 %d 已经在服务，复用现有实例。" % self.port,
                                code="ALREADY_RUNNING")

            try:
                env = self.build_env()
            except SupervisorError as exc:
                return self._fail(str(exc), code=exc.code)

            command = self.build_command()
            if self._command_override is None and not os.path.exists(command[0]):
                return self._fail(
                    f"找不到 TAPD 运行时可执行文件：{command[0]}\n"
                    "按 tapd-runtime/README.md「重建 .venv」那节把它建出来。",
                    code="RUNTIME_MISSING")

            self._truncate_log()
            try:
                log_handle = open(self.log_path, "ab", buffering=0)
            except OSError as exc:
                return self._fail(f"打不开启动日志 {self.log_path}：{exc}",
                                  code="LOG_UNWRITABLE")
            try:
                self._process = subprocess.Popen(  # noqa: S603 - 命令全部由本模块拼
                    command,
                    cwd=self.runtime_dir,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                log_handle.close()
                return self._fail(f"起不了子进程：{exc}", code="SPAWN_FAILED")
            finally:
                # Popen 已经复制了句柄，父进程这份可以关掉。
                try:
                    log_handle.close()
                except OSError:
                    pass

            if self._wait_healthy():
                return self._ok("TAPD 服务已在 127.0.0.1:%d 上就绪。" % self.port,
                                code="STARTED")

            # 没起来：进程可能已经死了（import 期校验令牌失败是最常见的），
            # 也可能还活着但端口没开。两种都收尸，然后读日志分类。
            self._terminate()
            code, advice = classify_log(self.read_log())
            return self._fail("TAPD 服务没起来。" + advice, code=code,
                              log_tail=self.log_tail())

    def stop(self) -> dict[str, Any]:
        """停子进程。只管自己起的那个；端口上是遗留进程时另走 `_kill_stale`。"""
        with self._lock:
            self._terminate()
            return self._ok("TAPD 服务已停止。", code="STOPPED")

    def restart(self) -> dict[str, Any]:
        """重启：先停自己的，再杀端口上的遗留进程，最后重新起。"""
        with self._lock:
            self._terminate()
            if probe_port(self.port, self.probe_host):
                self._kill_stale()
            return self.start()

    # ── 内部 ────────────────────────────────────────────────────────────────

    def _wait_healthy(self) -> bool:
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if probe_port(self.port, self.probe_host):
                return True
            process = self._process
            if process is not None and process.poll() is not None:
                # 进程已经退了，再等也不会有端口。给磁盘一点时间把日志刷完。
                time.sleep(0.2)
                return False
            time.sleep(0.25)
        return False

    def _terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - 收尸尽力而为，不能让它挡住上层
                pass
        except OSError:
            pass

    def _kill_stale(self) -> None:
        """杀掉占着端口的遗留进程（上一次网关被强杀留下的）。

        Windows 上用 netstat 找 PID 再 taskkill——不引 psutil。
        找不到就算了，`start()` 随后会以「端口已在服务」的分支复用它。
        """
        if os.name != "nt":
            return
        try:
            raw_output = subprocess.run(  # noqa: S603,S607 - 固定命令，无用户输入
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True, text=False, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        output = (raw_output or b"").decode("ascii", errors="ignore")
        needle = f":{self.port}"
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 5 or "LISTENING" not in line:
                continue
            if not parts[1].endswith(needle):
                continue
            pid = parts[-1]
            if not pid.isdigit() or pid == "0":
                continue
            try:
                subprocess.run(  # noqa: S603,S607
                    ["taskkill", "/PID", pid, "/F", "/T"],
                    capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass
            time.sleep(0.5)

    def _atexit_stop(self) -> None:
        try:
            self._terminate()
        except Exception:  # noqa: BLE001 - 退出路径上不许再抛
            pass

    def _truncate_log(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "wb"):
                pass
        except OSError:
            pass

    def read_log(self) -> str:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def log_tail(self, lines: int = 8) -> list[str]:
        """日志末几行，给页面做「展开详情」。

        日志里不会有令牌值：我们从不把令牌写进命令行或日志，子进程自己也只打
        「token: loaded from env」。万一包哪天改了行为，这里做一道兜底过滤。
        """
        token = self.config.get_secret("TAPD_ACCESS_TOKEN")
        tail = [ln.rstrip() for ln in self.read_log().splitlines() if ln.strip()][-lines:]
        if token:
            tail = [ln.replace(token, "<redacted>") for ln in tail]
        return tail

    def _ok(self, message: str, code: str) -> dict[str, Any]:
        return {"ok": True, "code": code, "message": message, "status": self.status()}

    def _fail(self, message: str, code: str,
              log_tail: list[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": False, "code": code, "message": message, "status": self.status()}
        if log_tail:
            payload["log_tail"] = log_tail
        return payload


if __name__ == "__main__":  # pragma: no cover - 手工排障入口
    supervisor = TapdSupervisor()
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    result = {"start": supervisor.start, "stop": supervisor.stop,
              "restart": supervisor.restart, "status": supervisor.status}[action]()
    print(result)
