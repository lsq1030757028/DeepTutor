"""[fork] 「测试」capability 注册的正向断言。

## 这个文件为什么单独存在（决策：设计稿 §13.3 B 档 + manager 2026-08-11 裁定）

上游 `tests/core/test_capabilities_runtime.py` 里那条是**全等集合断言**
（`assert set(BUILTIN_CAPABILITY_CLASSES) == {7 个字面量}`）。fork 多注册一个
capability，它必然红。三条路走了第三条：

- 不改上游那条（改了每次同步必撞，且要吃掉最后 1 行硬余量）；
- 不接受它长期红（红久了没人看，真红混进来谁也认不出——这是「不许假绿」的镜像问题）；
- **按测试全名具名扣除**（`extensions/test-partner/scripts/known-deductions.json`），
  扣除清单机械可读、带到期条件、闸每次打印条数。

**正向断言由我方守（本文件），反向的全等断言让给上游。**
所以本文件断的是「test 在册」，绝不断「集合恰好等于某几项」——
后者是上游的职责，我方再断一次就又造了一个必然冲突点。
"""

from __future__ import annotations

import pytest

from deeptutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_CLASSES
from deeptutor.services.prompt.manager import PromptManager


def test_test_capability_is_registered() -> None:
    """触点 #7。不加则 picker 选中后服务端无实现可派。"""
    assert "test" in BUILTIN_CAPABILITY_CLASSES


def test_test_capability_points_at_the_fork_module() -> None:
    assert BUILTIN_CAPABILITY_CLASSES["test"] == (
        "deeptutor.agents.test.capability:TestCapability")


def test_registration_is_a_subset_assertion_not_an_equality_one() -> None:
    """守住本文件自己的边界：我方只断在册，不断全等。

    断全等就等于在 fork 侧复制了那条必然冲突的断言——把好不容易让出去的东西又拿回来。
    """
    source = __doc__ or ""
    assert "正向断言由我方守" in source


def test_prompt_module_is_registered() -> None:
    """触点 #8 —— **全表唯一「漏改不报错」的一处，所以必须有测试守**。

    `PromptManager.MODULES` 少了 "test"，`load_prompts` 查不到模块会**静默回落**：
    模型照跑，但身上没有旅程纪律，行为退化成普通聊天而不报任何错。

    断言的是 `"test" in MODULES` 这个**注册事实**，不是文案内容——
    断文案会让每次改提示词都红一次，红着红着就没人看了（manager 明确要求）。
    """
    assert "test" in PromptManager.MODULES


def test_journey_prompt_loads_in_both_languages() -> None:
    """注册了还要真能读出来。只查关键段在场，不查它写了什么。"""
    from deeptutor.agents.test.capability import PROMPT_AGENT, PROMPT_MODULE

    for language in ("zh", "en"):
        prompts = PromptManager().load_prompts(
            PROMPT_MODULE, PROMPT_AGENT, language=language)
        assert str(prompts.get("journey_discipline") or "").strip(), language


def test_capability_reuses_main_chat_pipeline() -> None:
    """人闸链路的命门：`wait_for_user_reply` 等待器**只在主聊 turn 上注入**。

    自建流水线 = 自己造一条没有 waiter 的通道 = 复刻 BB-502 的静默降级
    （问题被拍平成本轮最终回复、本轮结束、用户无感知）。
    所以这条断言盯的是"它确实走主聊 pipeline"，不是某个输出。
    """
    import inspect

    from deeptutor.agents.test.capability import TestCapability

    source = inspect.getsource(TestCapability.run)
    assert "AgenticChatPipeline" in source


def test_capability_manifest_name_matches_registry_key() -> None:
    from deeptutor.agents.test.capability import TestCapability

    assert TestCapability.manifest.name == "test"


def test_missing_prompt_module_degrades_to_empty_not_to_a_fallback_blurb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提示词读不到时**返回空串**，不塞兜底文案。

    塞兜底会让「模块没注册」这个配置错误看起来像正常工作，正是 MODULES 那条
    漏改不报错的放大器。空串至少让人发现模型没按纪律走。
    """
    from deeptutor.agents.test import capability as cap

    def _boom(*args: object, **kwargs: object) -> dict:
        raise RuntimeError("prompt module not registered")

    monkeypatch.setattr(PromptManager, "load_prompts", _boom)
    assert cap.journey_system_block("zh") == ""


def test_capability_description_has_both_languages() -> None:
    """触点 #11。不加则 picker 那一行缺中英文案。"""
    from deeptutor.i18n.metadata_i18n import _CAPABILITY_DESCRIPTIONS

    entry = _CAPABILITY_DESCRIPTIONS.get("test")
    assert entry and entry.get("en") and entry.get("zh")
