"""宿主侧模型适配器的回归测试。

适配器住在 `deeptutor/api/routers/`（宿主侧），但测试放在这里，
因为它守的是 `server/generate/` 那三条防线在真实调用路径上**没有被绕过**。

不发真请求：`deeptutor.services.llm.complete` 与 `allowed_llm_options`
都用假的替身注入，断言的是"传下去的参数对不对"。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

import pytest

# 与 test_api_router.py 同源：适配器在仓库根下的 deeptutor/ 里，
# 而 conftest.py 只把 extensions/test-partner 塞进了 sys.path。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from server.generate.model import (
    SPEC_DETAIL,
    SPEC_SCENARIO,
    CallSpec,
    ModelTimeout,
    ModelUnavailable,
)


def _load_adapter(monkeypatch, *, options, complete_impl):
    """装一个只有假替身的适配器。

    适配器在函数体内部才 import 上游模块（不是模块顶层），
    正是为了让这里能在 import 之前把替身塞进 sys.modules。
    """
    fake_access = types.ModuleType("deeptutor.multi_user.model_access")
    fake_access.allowed_llm_options = lambda: options
    monkeypatch.setitem(sys.modules, "deeptutor.multi_user.model_access", fake_access)

    fake_llm = types.ModuleType("deeptutor.services.llm")
    fake_llm.complete = complete_impl
    monkeypatch.setitem(sys.modules, "deeptutor.services.llm", fake_llm)

    import importlib
    mod = importlib.import_module("deeptutor.api.routers.test_workbench_model")
    return importlib.reload(mod)


class Recorder:
    """记录 complete() 收到的关键字参数。"""

    def __init__(self, reply="ok", delay=0.0):
        self.kwargs = None
        self.prompt = None
        self.reply = reply
        self.delay = delay

    async def __call__(self, prompt, **kwargs):
        self.prompt = prompt
        self.kwargs = kwargs
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply


ONE_MODEL = {"active": None, "options": [{"model": "gpt-4o-mini", "label": "x"}]}
NO_MODEL = {"active": None, "options": []}


# ── 空态：没配模型要判成空态，不是把 401 甩给用户 ────────────────────────

def test_no_model_raises_a_typed_empty_state(monkeypatch):
    """上游在"一个模型都没配"时不抛错，会解析成默认 provider + 空 key，

    用户最终拿到对方的 401。这个区分必须在适配器这层做出来。
    """
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=NO_MODEL, complete_impl=rec)
    with pytest.raises(ModelUnavailable, match="还没有可用的模型"):
        asyncio.run(mod.call_model("p", system="s", spec=SPEC_SCENARIO))
    assert rec.kwargs is None, "没模型时不该真去调"


def test_capability_probe_reflects_availability(monkeypatch):
    mod = _load_adapter(monkeypatch, options=NO_MODEL, complete_impl=Recorder())
    assert mod.has_usable_model() is False
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=Recorder())
    assert mod.has_usable_model() is True


def test_upstream_failure_is_not_a_500(monkeypatch):
    """读配置炸了也只当"没有可用模型"，不该把页面打成 500。"""
    def boom():
        raise RuntimeError("目录读坏了")
    fake = types.ModuleType("deeptutor.multi_user.model_access")
    fake.allowed_llm_options = boom
    monkeypatch.setitem(sys.modules, "deeptutor.multi_user.model_access", fake)
    monkeypatch.setitem(sys.modules, "deeptutor.services.llm",
                        types.ModuleType("deeptutor.services.llm"))
    import importlib
    mod = importlib.reload(
        importlib.import_module("deeptutor.api.routers.test_workbench_model"))
    assert mod.has_usable_model() is False


# ── 三条防线必须真的传下去，不能在适配器这层被绕过 ───────────────────────

def test_retry_guards_are_passed_through(monkeypatch):
    """压 max_retries 与关退避是防线不是调优——上游默认带退避，最坏累计 500 秒量级。"""
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec)
    asyncio.run(mod.call_model("p", system="s", spec=SPEC_SCENARIO))
    assert rec.kwargs["max_retries"] == 1
    assert rec.kwargs["exponential_backoff"] is False


def test_max_tokens_is_always_explicit(monkeypatch):
    """不显式传的话上游会用它自己的默认值，我们的档位形同虚设。"""
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec)
    asyncio.run(mod.call_model("p", system="s", spec=SPEC_DETAIL))
    assert rec.kwargs["max_tokens"] == SPEC_DETAIL.max_tokens


def test_json_mode_is_requested_only_when_the_spec_says_so(monkeypatch):
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec)
    asyncio.run(mod.call_model("p", system="s", spec=SPEC_DETAIL))
    assert rec.kwargs["response_format"] == {"type": "json_object"}

    rec2 = Recorder()
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec2)
    asyncio.run(mod.call_model("p", system="s", spec=SPEC_SCENARIO))
    assert "response_format" not in rec2.kwargs, "起草场景要的是大白话不是 JSON"


def test_model_is_passed_explicitly_not_left_to_system_default(monkeypatch):
    """不传 selection 时上游读 admin 目录——多用户下等于把 admin 的模型

    借给未被授权的用户，绕过 grant。所以必须显式传。
    """
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec)
    asyncio.run(mod.call_model("p", system="s", spec=SPEC_SCENARIO))
    assert rec.kwargs["model"] == "gpt-4o-mini"


def test_active_selection_wins_over_the_first_option(monkeypatch):
    options = {"active": {"model": "active-model"},
               "options": [{"model": "other-model"}]}
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=options, complete_impl=rec)
    asyncio.run(mod.call_model("p", system="s", spec=SPEC_SCENARIO))
    assert rec.kwargs["model"] == "active-model"


def test_system_prompt_and_body_reach_the_model(monkeypatch):
    rec = Recorder()
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec)
    asyncio.run(mod.call_model("正文", system="系统词", spec=SPEC_SCENARIO))
    assert rec.prompt == "正文"
    assert rec.kwargs["system_prompt"] == "系统词"


# ── 超时：必须由我们兜，且不自动重试 ─────────────────────────────────────

def test_timeout_is_enforced_by_us(monkeypatch):
    """上游建客户端时没传 timeout，卡住就是挂死。这一条是唯一的墙钟保障。"""
    rec = Recorder(delay=0.3)
    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=rec)
    spec = CallSpec(max_tokens=100, timeout_s=1)
    object.__setattr__(spec, "timeout_s", 0.05)   # 缩短以免测试真等
    with pytest.raises(ModelTimeout, match="没有返回"):
        asyncio.run(mod.call_model("p", system="s", spec=spec))


def test_timeout_does_not_auto_retry(monkeypatch):
    """重试一个已经超时的长请求是账单放大器，该不该再来由人决定。"""
    calls = {"n": 0}

    async def slow(prompt, **kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.3)
        return "x"

    mod = _load_adapter(monkeypatch, options=ONE_MODEL, complete_impl=slow)
    spec = CallSpec(max_tokens=100, timeout_s=1)
    object.__setattr__(spec, "timeout_s", 0.05)
    with pytest.raises(ModelTimeout):
        asyncio.run(mod.call_model("p", system="s", spec=spec))
    assert calls["n"] == 1
