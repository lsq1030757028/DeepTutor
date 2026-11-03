# -*- coding: utf-8 -*-
"""0012 第二/三步：令牌写入端点 + stdio 条目形态（DoD#3 / ADR-M2-02 / 0027）。

盯的是三件事：凭据不回显、白名单只放只读、不改上游。
"""
import os

import pytest

from server.gateway import secret_write as sw


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, owner, server, values):
        self.calls.append((owner, server, dict(values)))


# ── 凭据纪律 ────────────────────────────────────────────────────────────────


def test_returns_no_value_only_configured_and_mask():
    store = _Recorder()
    out = sw.write_tapd_token("A" * 36 + "beef", store=store)
    assert out["ok"] and out["configured"] is True
    assert out["masked"].endswith("beef") and out["masked"].startswith("*")
    # 返回体里**任何字段都不许出现完整值**
    for value in out.values():
        assert "A" * 36 not in str(value)


def test_short_token_is_fully_masked():
    out = sw.write_tapd_token("short", store=_Recorder())
    assert out["masked"] == "*****"


def test_value_goes_to_platform_store_under_shared_owner():
    store = _Recorder()
    sw.write_tapd_token("t" * 40, store=store)
    owner, server, values = store.calls[0]
    assert owner == sw.SHARED_OWNER == "_shared"
    assert server == "tapd"
    assert set(values) == {"TAPD_ACCESS_TOKEN"}


def test_empty_value_is_rejected_not_treated_as_delete():
    """平台语义里空值 = 删除，但那不该由一个叫「写令牌」的端点顺手做掉。"""
    out = sw.write_tapd_token("   ", store=_Recorder())
    assert out["ok"] is False and out["code"] == sw.E_EMPTY_VALUE


def test_key_whitelist_blocks_a_generic_write():
    """这是 TAPD 令牌入口，不是通用密钥写口。"""
    for key in ("OPENAI_API_KEY", "TAPD_API_PASSWORD", "anything"):
        out = sw.write_secret(key, "x" * 40, store=_Recorder())
        assert out["ok"] is False and out["code"] == sw.E_KEY_NOT_ALLOWED


def test_outside_container_says_why_not_stacktrace():
    """宿主机进程 import 不到 deeptutor.*。给一句可执行的话，不给 ImportError 栈。"""
    out = sw.write_tapd_token("t" * 40, store=None)
    # 宿主机跑测试时 deeptutor 不可 import ⇒ 走这条；容器内跑则真写入。
    if not out["ok"]:
        assert out["code"] == sw.E_NOT_IN_CONTAINER
        assert "容器内" in out["message"]


# ── stdio 条目形态（0012 第三步）───────────────────────────────────────────


def test_stdio_entry_uses_secret_reference_not_plaintext():
    entry = sw.stdio_entry()
    assert entry["type"] == "stdio"
    assert entry["env"]["TAPD_ACCESS_TOKEN"] == "${secret:TAPD_ACCESS_TOKEN}"


def test_stdio_entry_pins_the_api_base_url():
    """包对 base_url 无任何校验——不钉死，一个笔误就把令牌发去别处。"""
    assert sw.stdio_entry()["env"]["TAPD_API_BASE_URL"] == "https://api.tapd.cn"


def test_stdio_entry_is_not_registered_while_the_venv_is_gone():
    """条目里的命令路径与 Dockerfile 装出来的 venv 必须对得上，
    否则条目注册成功但一拉起就 ENOENT。

    Delta 二十（16ba0f7e）已把 venv 从镜像里整块删掉，所以现在的约束反过来：
    Dockerfile 里没有那个 venv 时，运行时代码一处都不许再注册这个条目。"""
    entry = sw.stdio_entry()
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    with open(os.path.join(repo_root, "Dockerfile"), encoding="utf-8") as fh:
        source = fh.read()
    if "/opt/tapd-mcp" in source:
        assert entry["command"].startswith("/opt/tapd-mcp/")
        return
    callers = []
    for base in ("deeptutor", os.path.join("extensions", "test-partner", "server")):
        for dirpath, _, files in os.walk(os.path.join(repo_root, base)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                if "stdio_entry(" in text.replace("def stdio_entry(", ""):
                    callers.append(os.path.relpath(path, repo_root))
    assert callers == [], f"venv 已移除，但仍有运行时代码注册 stdio 条目：{callers}"


def test_default_whitelist_is_read_only(monkeypatch):
    """**0027 裁定 2 不可让步**：写工具必须与确认闸同批放行。
    默认值里出现任何写工具，就是那个"先放权后补约束"的口子。"""
    tools = set(sw.stdio_entry()["enabled_tools"])
    assert tools == {"get_stories_or_tasks", "get_stories_fields_info"}
    for banned in ("create_story", "update_story", "add_bug", "update_bug",
                   "create_task", "update_task"):
        assert banned not in tools


def test_dockerfile_never_runs_uvx():
    """供应链纪律：禁 uvx（`tapd-runtime/PINNED.md`）。
    钉版本的两个 ARG 随 Delta 二十的 fork 块一起删了，禁 uvx 这条仍然有效。"""
    dockerfile = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "Dockerfile")
    with open(dockerfile, encoding="utf-8") as fh:
        source = fh.read()
    # uvx 会拉最新版，正是 PINNED.md 点名禁用的。
    # 只查**可执行行**——注释里写「禁用 uvx」是我们要的，不该被自己的断言判红。
    executed = [line for line in source.split("\n")
                if line.strip() and not line.lstrip().startswith("#")]
    offenders = [line.strip() for line in executed if "uvx " in line]
    assert offenders == [], f"Dockerfile 里有 uvx 调用（会拉最新版）：{offenders}"


def test_fork_block_is_deletable_as_one_piece():
    """0018 批准 Dockerfile 触点加深的**附加条件**：独立成可整块删除的 fork 块。"""
    dockerfile = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))), "Dockerfile")
    with open(dockerfile, encoding="utf-8") as fh:
        source = fh.read()
    assert "[fork] TAPD MCP 运行时" in source
    assert "fork 块结束" in source
