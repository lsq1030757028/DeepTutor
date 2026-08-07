"""三段提示词。**零 DeepTutor import。**

## 为什么提示词单独成模块

它们是这条链路里**唯一会影响产出质量、又完全没有类型约束**的部分。
散在调用点意味着改一句话要翻三个文件，也没法在测试里断言"该有的约束还在"。

## 借鉴与出处（对标结论，见 docs/design/README.md）

- **模板渲染真实端点结构、模型只填值**（借鉴 MeterSphere）：
  把端点的字段名与样例形状写进 prompt 骨架，并明写"不许新增字段"，
  把"AI 幻觉出不存在的字段"从架构上掐掉，而不是靠事后校验去抓。
- **先出清单再出细节**（借鉴 Apifox 的分步生成）：
  第二段只要标题与意图，第三段才写请求与断言。改标题比改整条用例便宜十倍，
  而 HAR 没有 schema、我们的输入质量天生比 OpenAPI 差，人工审核越早越省。

**这两条借的都是交互设计与方法，不是代码**——MeterSphere 是 GPLv3，
它的 prompt 是源码文件同样受 GPL 覆盖，一个字都不能抄（决策见 0011 的许可证段）。

## 一条硬纪律

素材里的 PII 已经在 `context.py` 换成了 `<手机号>` 这类占位符。
提示词里因此**必须**告诉模型：占位符是脱敏结果，照抄即可，不要试图"还原"或编造真值。
不写这句，模型会自作主张填一个像样的手机号进去，用例就带上了假数据。
"""

from __future__ import annotations

import json
from typing import Any

from server.generate.context import Material

_JSON_ONLY = "只输出 JSON，不要任何解释文字，不要 markdown 代码围栏。"

_PLACEHOLDER_RULE = (
    "素材里形如 <手机号> <邮箱> <身份证> <标识> <姓名> 的是脱敏占位符，"
    "不是真实值。需要用到时**原样保留占位符**，绝不要编造一个真实的值填进去。"
)


def _material_block(m: Material) -> str:
    """素材的统一呈现。三段共用，保证模型每次看到的结构一致。"""
    parts = [
        f"这份抓包共 {m.stats.get('requests')} 条请求，"
        f"滤掉噪声 {m.stats.get('noise_filtered')} 条，"
        f"归并出 {m.stats.get('endpoints')} 个端点。",
        "",
        "端点清单（JSON）：",
        json.dumps(m.endpoints, ensure_ascii=False, indent=1),
    ]
    if m.login:
        parts += ["", "识别到的登录请求：",
                  json.dumps(m.login, ensure_ascii=False, indent=1)]
    else:
        parts += ["", "（这份抓包里没有识别出登录请求）"]
    return "\n".join(parts)


# ── 第一段：起草场景描述 ──────────────────────────────────────────────────

SCENARIO_SYSTEM = (
    "你在帮测试工程师描述一段抓包记录到底在做什么业务。"
    "你的输出会直接显示在界面上给人读，也会被人改。"
)


def scenario_prompt(m: Material) -> str:
    """让模型用**一段大白话**说这份抓包在做什么。

    设计稿定的形态：不是表单、不是分条，就是一段话，例如
    「目前测的是输入密码登录，点击首页，切换设备，退出登录」。
    用户明确否掉过结构化字段版本——「用户也不知道」什么叫数据约束、前置条件。
    """
    return f"""{_material_block(m)}

---

用一段大白话说清楚：这份抓包在做什么业务操作。

要求：
- **就是一段话**，按用户实际操作的先后顺序讲，一两句话，不要分条、不要小标题
- 用业务语言，不要出现接口路径、HTTP 方法、状态码这些技术词
- 只说抓包里真实出现过的，没抓到的不要提，不要补充你觉得"应该有"的步骤
- 不要写"本次抓包包含"这类开场白，直接说business动作

参考这个语气：目前测的是输入密码登录，点击首页，切换设备，退出登录。

{_PLACEHOLDER_RULE}

直接输出那段话，不要引号，不要任何其它内容。"""


# ── 第二段：挑端点 + 出用例清单 ───────────────────────────────────────────

OUTLINE_SYSTEM = (
    "你是接口测试工程师。你要从一份抓包里挑出与给定业务场景相关的接口，"
    "并列出该测哪些用例。这一步只出清单，不写具体请求。"
)


def outline_prompt(m: Material, scenario: str, *, max_cases: int) -> str:
    """挑端点与出清单合成一次调用。

    合成的理由：挑端点这件事的判据就是"跟这个场景相不相关"，
    而相关性只有在想清楚"要测什么"的时候才成立——拆成两次调用，
    第一次没有用例意图做依据，挑出来的东西反而更差，还多花一次钱。

    **挑端点是 AI 的活不是用户的活**（用户明确要求）：一份 HAR 几百个接口，
    让人逐个勾选是把苦力活外抛。所以这里让模型自己挑，界面只展示结果并允许调整。
    """
    return f"""{_material_block(m)}

---

测试工程师描述的业务场景：

{scenario}

---

请完成两件事：

1. 从上面的端点清单里，挑出与这个业务场景**相关**的端点。不相关的不要挑，
   宁可少挑也不要凑数——挑进来的每一个都会被生成用例，都要花钱和人力去审。
2. 为挑出的端点列出测试用例清单。**这一步只要标题和意图，不要写请求和断言。**

用例要求：
- 总数不超过 {max_cases} 条
- 标题用业务语言，一眼看得出在测什么，例如「库存不足时应拒绝下单，不能超卖」
- 意图只能是这四个之一：正常、边界、异常、鉴权
- **只基于抓包里真实出现过的东西**。抓包里没出现过 404，就不要生成"查询不存在的资源"
  这种用例；确实要生成时，必须在 `assumption` 字段里写明这是推测而非观察到的行为
- 优先覆盖业务价值高的路径，不要把配额浪费在同一个端点的细微变体上

{_PLACEHOLDER_RULE}

{_JSON_ONLY}

输出格式：
{{
  "picked_endpoints": [
    {{"method": "POST", "path": "/api/order/create", "why": "场景里的提交订单这一步"}}
  ],
  "skipped_reason": "一句话说明为什么其余端点没挑（例如：与本场景无关的配置类接口）",
  "cases": [
    {{"id": "TC-001", "title": "...", "intent": "正常",
      "endpoint": {{"method": "POST", "path": "/api/order/create"}},
      "assumption": ""}}
  ]
}}"""


# ── 第三段：写请求与断言 ──────────────────────────────────────────────────

DETAIL_SYSTEM = (
    "你是接口测试工程师。你要把已确认的用例清单补全成可执行的接口用例。"
)


def detail_prompt(m: Material, scenario: str, cases: list[dict[str, Any]]) -> str:
    """把清单补成完整用例。

    最关键的两条约束都在这里：
    - **字段只能来自素材**（借鉴 MeterSphere 的模板渲染思路）：不许新增素材里
      没有的字段名，这样"模型编出不存在的字段"就不再是靠事后校验去抓的事故
    - **断言不能只有 status**：只断言状态码的用例跑起来只能证明"接口活着"，
      对测试没有价值。这条同时被 `case_validate` 的 W05 兜底，两处同源。
    """
    picked = json.dumps(cases, ensure_ascii=False, indent=1)
    return f"""{_material_block(m)}

---

业务场景：

{scenario}

---

已确认要写的用例清单：

{picked}

---

为清单里的**每一条**补全请求与断言。

字段纪律（最重要）：
- 请求里出现的字段名，**只能来自上面端点清单的 sample_body 与 query_keys**。
  素材里没有的字段名一律不许出现——不确定就不写，不要凭常识补充。
- 请求体保持素材里的形状，只改这条用例真正要改的那个值。
- URL 必须与该用例 endpoint 的 path 完全一致，不要自行改写路径。

断言纪律：
- **不要只断言 status**。只断言状态码的用例跑起来只能证明接口活着，没有测试价值。
  每条至少再断言一项响应体内容。
- 断言只写抓包里能佐证的、或该用例意图直接要求的，不要凭空发明业务规则。

{_PLACEHOLDER_RULE}
需要鉴权的请求，Authorization 一律写 `Bearer {{{{token}}}}` 这种变量占位形式，
绝不要写真实令牌。

{_JSON_ONLY}

输出格式：
{{
  "cases": [
    {{"id": "TC-001", "title": "...", "intent": "正常",
      "request": {{"method": "POST", "url": "/api/order/create",
                  "headers": [{{"name": "Authorization", "value": "Bearer {{{{token}}}}"}}],
                  "body": {{}}}},
      "assertions": [
        {{"kind": "status", "expect": 200}},
        {{"kind": "json_path", "path": "$.data.orderId", "op": "exists"}}
      ]}}
  ]
}}"""
