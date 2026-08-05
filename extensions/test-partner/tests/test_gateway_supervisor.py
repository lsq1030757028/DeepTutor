"""子服务管家的测试：环境清场与注入、生命周期、失败诊断分类、令牌不出流。

真的 mcp-server-tapd 一次都不起（它 import 期就打 api.tapd.cn），
生命周期用 `tests/fixtures/fake_service.py` 当替身，通过 `command` 注入。
"""

from __future__ import annotations

import os
import socket
import sys
import time

import pytest

from server.gateway.config import GatewayConfig
from server.gateway.supervisor import (
    PINNED_API_BASE_URL,
    POLLUTED_ENV_KEYS,
    SupervisorError,
    TapdSupervisor,
    classify_log,
    probe_port,
)

TOKEN = "tapd-pat-9f8e7d6c5b4a39281706abcdef012345"
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                    "fake_service.py")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def cfg(tmp_path):
    config = GatewayConfig(str(tmp_path / "config"))
    config.set_secret("TAPD_ACCESS_TOKEN", TOKEN)
    return config


def make_supervisor(cfg, tmp_path, mode="serve", port=None):
    port = port or free_port()
    cfg.save_settings({"services": {"tapd": {"port": port}}})
    return TapdSupervisor(
        config=cfg,
        runtime_dir=str(tmp_path),
        log_path=str(tmp_path / "tapd-start.log"),
        command=[sys.executable, FAKE, "--port", str(port), "--mode", mode],
        startup_timeout_s=12.0,
    )


# ── 环境注入 ────────────────────────────────────────────────────────────────

def test_build_env_clears_inherited_pollution(cfg, tmp_path, monkeypatch):
    # 这台机器的环境里本来就有一个 TAPD token —— 实测踩过的那个坑
    monkeypatch.setenv("TAPD_ACCESS_TOKEN", "野生的继承令牌")
    monkeypatch.setenv("TAPD_API_USER", "someone")
    monkeypatch.setenv("TAPD_API_PASSWORD", "hunter2")
    monkeypatch.setenv("TAPD_BASE_URL", "https://evil.example.com")
    env = make_supervisor(cfg, tmp_path).build_env()

    assert env["TAPD_ACCESS_TOKEN"] == TOKEN          # 用的是配置页里那个
    assert "TAPD_API_USER" not in env                  # Basic Auth 一路封死
    assert "TAPD_API_PASSWORD" not in env
    assert "TAPD_BASE_URL" not in env


def test_build_env_pins_api_base_url(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TAPD_API_BASE_URL", "http://127.0.0.1:9")
    env = make_supervisor(cfg, tmp_path).build_env()
    # 凭据流向的唯一闸门：不受环境、不受配置文件影响
    assert env["TAPD_API_BASE_URL"] == PINNED_API_BASE_URL == "https://api.tapd.cn"


def test_build_env_injects_optional_bot_url_only_when_set(cfg, tmp_path, monkeypatch):
    for key in POLLUTED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    supervisor = make_supervisor(cfg, tmp_path)
    assert "BOT_URL" not in supervisor.build_env()
    cfg.set_secret("BOT_URL", "https://qyapi.weixin.qq.com/hook")
    assert supervisor.build_env()["BOT_URL"] == "https://qyapi.weixin.qq.com/hook"


def test_build_env_does_not_leak_deeptutor_token_to_tapd(cfg, tmp_path):
    cfg.set_secret("DEEPTUTOR_TOKEN", "dt-admin-token-abcdefg")
    env = make_supervisor(cfg, tmp_path).build_env()
    assert "DEEPTUTOR_TOKEN" not in env


def test_build_env_refuses_without_token(tmp_path):
    empty = GatewayConfig(str(tmp_path / "empty"))
    supervisor = TapdSupervisor(config=empty, runtime_dir=str(tmp_path),
                                log_path=str(tmp_path / "log.txt"),
                                command=[sys.executable, "-c", "pass"])
    with pytest.raises(SupervisorError) as excinfo:
        supervisor.build_env()
    assert excinfo.value.code == "NO_TOKEN"


def test_start_reports_no_token_instead_of_launching(tmp_path):
    empty = GatewayConfig(str(tmp_path / "empty"))
    port = free_port()
    empty.save_settings({"services": {"tapd": {"port": port}}})
    supervisor = TapdSupervisor(
        config=empty, runtime_dir=str(tmp_path), log_path=str(tmp_path / "log.txt"),
        command=[sys.executable, FAKE, "--port", str(port)])
    result = supervisor.start()
    assert result["ok"] is False and result["code"] == "NO_TOKEN"
    assert not probe_port(port)   # 一个进程都没起


def test_default_command_uses_pinned_venv_executable(cfg, tmp_path):
    supervisor = TapdSupervisor(config=cfg, runtime_dir=str(tmp_path / "rt"))
    command = supervisor.build_command()
    assert command[0].endswith("mcp-server-tapd.exe") or command[0].endswith(
        "mcp-server-tapd")
    assert ".venv" in command[0]
    assert "--mode=streamable-http" in command
    assert f"--port={supervisor.port}" in command
    # 绑 0.0.0.0 是容器可达的必要条件，不能被改成 127.0.0.1
    assert "--host=0.0.0.0" in command


def test_missing_runtime_reports_clear_error(cfg, tmp_path):
    # 端口要挑一个没人听的：默认端口 3795 在开发机上常常正被常驻网关的 TAPD 子服务占着，
    # 那时 start() 会先走「已经在服务，复用」的分支，根本走不到缺可执行文件这一支。
    cfg.save_settings({"services": {"tapd": {"port": free_port()}}})
    supervisor = TapdSupervisor(config=cfg, runtime_dir=str(tmp_path / "不存在"),
                                log_path=str(tmp_path / "log.txt"))
    result = supervisor.start()
    assert result["ok"] is False and result["code"] == "RUNTIME_MISSING"
    assert "重建 .venv" in result["message"]


# ── 生命周期 ────────────────────────────────────────────────────────────────

def test_status_when_nothing_runs(cfg, tmp_path):
    supervisor = make_supervisor(cfg, tmp_path)
    status = supervisor.status()
    assert status["running"] is False
    assert status["managed"] is False
    assert status["token_configured"] is True
    assert status["port"] == supervisor.port


def test_start_stop_restart_lifecycle(cfg, tmp_path):
    supervisor = make_supervisor(cfg, tmp_path)
    try:
        started = supervisor.start()
        assert started["ok"] is True, started
        assert started["code"] == "STARTED"
        assert started["status"]["running"] is True
        assert started["status"]["managed"] is True
        assert started["status"]["pid"]

        first_pid = started["status"]["pid"]
        restarted = supervisor.restart()
        assert restarted["ok"] is True, restarted
        assert restarted["status"]["running"] is True
        assert restarted["status"]["pid"] != first_pid

        stopped = supervisor.stop()
        assert stopped["ok"] is True
        # 端口要真的放开（下一次启动才不会撞上自己）
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and probe_port(supervisor.port):
            time.sleep(0.1)
        assert supervisor.status()["running"] is False
    finally:
        supervisor.stop()


def test_start_is_idempotent_when_port_already_serving(cfg, tmp_path):
    supervisor = make_supervisor(cfg, tmp_path)
    try:
        assert supervisor.start()["ok"] is True
        again = supervisor.start()
        assert again["ok"] is True
        assert again["code"] == "ALREADY_RUNNING"
    finally:
        supervisor.stop()


def test_crash_on_start_is_classified_as_token_rejected(cfg, tmp_path):
    supervisor = make_supervisor(cfg, tmp_path, mode="crash")
    result = supervisor.start()
    assert result["ok"] is False
    assert result["code"] == "TOKEN_REJECTED"
    assert "401" in result["message"]
    assert result["log_tail"]        # 页面上能展开看原始日志


def test_silent_crash_reports_no_log(cfg, tmp_path):
    supervisor = make_supervisor(cfg, tmp_path, mode="silent")
    result = supervisor.start()
    assert result["ok"] is False
    assert result["code"] == "NO_LOG"


def test_child_env_has_no_basic_auth_and_carries_token(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TAPD_API_USER", "someone")
    supervisor = make_supervisor(cfg, tmp_path)
    try:
        assert supervisor.start()["ok"] is True
        log = supervisor.read_log()
        assert "TAPD_ACCESS_TOKEN=set" in log        # 注入到了
        assert "TAPD_API_USER=unset" in log          # 清场生效
        assert "TAPD_API_BASE_URL=set" in log        # 钉死值注入了
    finally:
        supervisor.stop()


# ── 凭据不出流 ──────────────────────────────────────────────────────────────

def test_token_never_appears_in_status_or_log(cfg, tmp_path):
    supervisor = make_supervisor(cfg, tmp_path)
    try:
        started = supervisor.start()
        assert TOKEN not in repr(started)
        assert TOKEN not in repr(supervisor.status())
        assert TOKEN not in supervisor.read_log()
        assert TOKEN not in " ".join(supervisor.build_command())
    finally:
        supervisor.stop()


def test_log_tail_scrubs_a_leaked_token(cfg, tmp_path):
    """兜底：万一子进程哪天把令牌打进日志，`log_tail` 也不许把它端给页面。"""
    supervisor = make_supervisor(cfg, tmp_path)
    with open(supervisor.log_path, "w", encoding="utf-8") as handle:
        handle.write(f"ERROR using token {TOKEN} failed\n")
    tail = supervisor.log_tail()
    assert TOKEN not in "\n".join(tail)
    assert "<redacted>" in tail[0]


# ── 诊断分类表 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("OSError: [WinError 10048] 通常每个套接字地址只允许使用一次", "PORT_TAKEN"),
    ("requests.exceptions.HTTPError: 401 Client Error: Unauthorized", "TOKEN_REJECTED"),
    ("HTTPError: 403 Client Error: Forbidden for url", "TOKEN_REJECTED"),
    ("requests.exceptions.ConnectionError: HTTPSConnectionPool getaddrinfo failed",
     "NETWORK_UNREACHABLE"),
    ("ModuleNotFoundError: No module named 'mcp_server_tapd'", "RUNTIME_BROKEN"),
    ("something entirely unexpected happened", "UNKNOWN"),
    ("", "NO_LOG"),
    ("   \n  ", "NO_LOG"),
])
def test_classify_log(text, expected):
    code, advice = classify_log(text)
    assert code == expected
    assert advice.strip()


def test_port_takes_priority_over_credentials_in_classification():
    """端口占用排在凭据之前，否则「换个端口就好」会被误诊成「令牌坏了」。"""
    mixed = "401 Unauthorized ... [WinError 10048] address already in use"
    assert classify_log(mixed)[0] == "PORT_TAKEN"
