"""模型适配器：把 DeepTutor 的模型服务包成生成链路要的那个可调用对象。

**这是全项目唯一懂模型的文件。** `extensions/test-partner/server/generate/`
是零 DeepTutor import 的纯逻辑，它只认 `model.CallModel` 这个协议；
谁去调 `deeptutor.services.llm`、怎么施加超时、怎么判"没配模型"，全在这里。

这样分的收益是审查面：想知道"这个功能会怎么花钱、会不会挂死"，只需要读这一个文件。

## 三件必须在这一层做的事

### 一 · 超时由我们套

`deeptutor.services.llm.complete()` 建客户端时没传 timeout，而它默认的
``max_retries`` 带指数退避，最坏累计到 500 秒量级。HAR 这种长输入一旦卡住，
HTTP 请求会挂死、用户只能看着转圈。

所以两件事一起做：把 ``max_retries`` 压到 1（`CallSpec` 的默认值已经是 1，
这里显式传过去），外面再套 ``asyncio.wait_for``。

### 二 · "没配模型"要判成空态，不是异常

上游在一个模型都没配时**不抛错**：它会解析成一个默认 provider + 空 key，
用户最终拿到的是对方返回的 401，而不是 DeepTutor 的"你还没配模型"。

所以调用之前先查 `allowed_llm_options()`——它是按 grant 过滤的，
拿到的就是**当前这个用户**能用的模型。空了就抛 `ModelUnavailable`，
页面据此给配置指引，而不是把一个 401 甩给用户。

### 三 · 不传 selection 就等于借 admin 的模型

不指定模型时上游读的是 admin 目录。多用户部署下，这等于把 admin 的模型
借给一个没被授权的用户，绕过 grant。所以这里**显式**把用户可用的那个
model 传下去，不走"系统默认"。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import sys
from typing import Any

logger = logging.getLogger(__name__)

# 与 test_workbench.py 同源的 sys.path 引导，理由见那边的模块 docstring。
_EXT_ROOT = Path(__file__).resolve().parents[3] / "extensions" / "test-partner"
if _EXT_ROOT.is_dir() and str(_EXT_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXT_ROOT))

from server.generate.model import (  # type: ignore[import-not-found]  # noqa: E402
    CallSpec,
    ModelTimeout,
    ModelUnavailable,
)


def _first_usable_model() -> str | None:
    """当前用户可用的第一个模型 id；一个都没有返回 None。

    走 `allowed_llm_options()` 而不是直接读目录：它按 grant 过滤，
    拿到的是**这个用户**能用的，不是部署里存在的。
    """
    from deeptutor.multi_user.model_access import allowed_llm_options

    try:
        payload = allowed_llm_options()
    except Exception:  # pragma: no cover - 上游异常不该让页面 500
        logger.exception("读取可用模型失败")
        return None

    active = payload.get("active")
    if isinstance(active, dict) and active.get("model"):
        return str(active["model"])

    for opt in payload.get("options") or []:
        if isinstance(opt, dict) and opt.get("model"):
            return str(opt["model"])
    return None


def has_usable_model() -> bool:
    """给 `/capability` 端点用：页面靠它决定是给入口还是给配置指引。"""
    return _first_usable_model() is not None


async def call_model(prompt: str, *, system: str, spec: CallSpec) -> str:
    """`server.generate.model.CallModel` 的宿主实现。

    每次调用都重新查一次可用模型：用户可能在两次生成之间改了配置，
    缓存一个 model id 会让"刚配好却还是报没配"这种问题出现。
    这一步是本地读文件，不值得为它做缓存。
    """
    from deeptutor.services.llm import complete

    model = _first_usable_model()
    if model is None:
        raise ModelUnavailable(
            "还没有可用的模型。请先在 设置 → 模型 里配置一个，"
            "或让管理员把某个模型授权给你。"
        )

    kwargs: dict[str, Any] = {
        "model": model,
        "system_prompt": system,
        "max_tokens": spec.max_tokens,
        "temperature": spec.temperature,
        # 下面三个是防线，不是调优参数——理由见模块 docstring 第一节。
        "max_retries": spec.max_retries,
        "retry_delay": spec.retry_delay,
        "exponential_backoff": spec.exponential_backoff,
    }
    if spec.json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        return await asyncio.wait_for(complete(prompt, **kwargs),
                                      timeout=spec.timeout_s)
    except asyncio.TimeoutError as exc:
        # 不自动重试：重试一个已经超时的长请求是账单放大器，
        # 该不该再来一次由人决定。
        raise ModelTimeout(
            f"模型 {spec.timeout_s:.0f} 秒没有返回。可以少要几条再试一次。"
        ) from exc
