"""出境脱敏：把要发给模型的素材里的个人信息换成**保形占位符**。

## 为什么需要它，以及它和既有脱敏的区别

`server/har_parse.py` 的脱敏契约是「**凭证**值不出现在输出里」——按凭证语义构词
（password/token/authorization/cookie/sign/apikey…），身份证、手机号、邮箱、
会话 id、姓名都不在保护范围内。这一点已由 BB-424 记在案（status=open）。

BB-424 描述的风险是「PII 留在本地报告与交付产物里」。**本模块解的是更重的一档：
这些素材会被送进 prompt，也就是出境到第三方模型服务商。** 实测（2026-08-07）：
一份含 `idCard` / `mobile` / `email` / `sessionId` / `realName` 的 HAR 走完
`build_report()`，五项原值全部原样留在报告里，只有 password 与 Bearer token 被掩掉。

本地留着和发给外部是两个量级的暴露，所以在进 prompt 这一步单设闸，
而不是等 BB-424 那条修完。两者不互相替代：BB-424 管落盘产物，这里管出境。

## 保形，不是删除

直接删或替换成 `***` 会让生成质量塌掉——模型得知道「这个字段是手机号」
才写得出「手机号少一位应该报错」这种用例。对标时 Apifox 自己承认
「接口文档越完善 AI 效果越好」，而 HAR 本来就没有 schema，再把字段语义抹掉
等于雪上加霜。

所以替换成**带类型的占位符**：`13800138000` → `<手机号>`，
模型看得懂那是什么，但拿不到真值。

## 按值匹配，不按键名

键名不可靠——BB-424 的成因正是「按键名构词的白名单漏了一整类」。
本模块主要按**值的形态**匹配（正则），只有姓名这一类不得不借助键名
（中文姓名没有可靠的形态特征），并且在下面明确标注了它的局限。

## 覆盖面与已知局限（如实写，不吹）

覆盖：中国大陆身份证、手机号、邮箱、银行卡号、IPv4、长不透明 id、
以及「键名像姓名 + 值是 2-4 个汉字」这一种启发式。

**不覆盖**：住址、生日、护照号、车牌、以及任何我们没想到的形态。
本模块是**减少**出境面，不是保证零 PII 出境。界面上不得宣称"已全部脱敏"。
"""

from __future__ import annotations

import re
from typing import Any

#: 占位符统一形状，便于下游一眼认出并在测试里断言。
PLACEHOLDER = "<{}>"

# ── 按值形态匹配 ────────────────────────────────────────────────────────────
# 顺序有意义：先长后短，避免身份证被手机号规则先咬掉一段。
_VALUE_RULES: list[tuple[str, re.Pattern[str]]] = [
    # 中国大陆身份证：18 位，末位可能是 X。放在最前，它包含连续数字段。
    ("身份证", re.compile(r"\b\d{17}[\dXx]\b")),
    # 银行卡：13-19 位纯数字。放在手机号前，否则 11 位规则会咬中它的前缀。
    ("银行卡号", re.compile(r"\b\d{13,19}\b")),
    # 中国大陆手机号：1 开头 11 位。
    ("手机号", re.compile(r"\b1[3-9]\d{9}\b")),
    ("邮箱", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # 长不透明 id（含会话 id）：≥12 位的字母数字混合串，且必须字母数字都有——
    # 纯字母的英文单词、纯数字的计数值都不该被咬。
    ("标识", re.compile(r"\b(?=[A-Za-z0-9_-]{12,}\b)(?=[^\s]*\d)(?=[^\s]*[A-Za-z])[A-Za-z0-9_-]{12,}\b")),
]

# ── 姓名：唯一一条借助键名的启发式 ──────────────────────────────────────────
# 中文姓名没有可靠形态（「张三」和「首页」都是两个汉字），只能靠键名缩小范围。
# 代价是漏检：键名不在下表里的姓名字段抓不到。这是已知局限，不是 bug。
_NAME_KEY_HINTS = ("name", "姓名", "realname", "username", "nickname",
                   "contact", "receiver", "consignee", "applicant")
_CJK_NAME = re.compile(r"^[一-龥]{2,4}$")


def _looks_like_name_key(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in _NAME_KEY_HINTS)


def scrub_text(text: str) -> tuple[str, dict[str, int]]:
    """把一段文本里的 PII 换成保形占位符。

    返回 ``(脱敏后文本, {类型: 命中次数})``。计数用于留痕——
    出境了多少条、什么类型，要能说得出，不能静默处理。
    """
    hits: dict[str, int] = {}
    out = text
    for label, pattern in _VALUE_RULES:
        placeholder = PLACEHOLDER.format(label)
        out, n = pattern.subn(placeholder, out)
        if n:
            hits[label] = hits.get(label, 0) + n
    return out, hits


def scrub_payload(node: Any, _key: str = "") -> tuple[Any, dict[str, int]]:
    """递归脱敏任意 JSON 结构（报告、样例、请求体都走这里）。

    字符串走 :func:`scrub_text`；此外当**键名像姓名**且值是 2-4 个汉字时，
    额外替换成 ``<姓名>``——这一条是启发式，见模块 docstring 的局限说明。
    """
    total: dict[str, int] = {}

    def merge(h: dict[str, int]) -> None:
        for k, v in h.items():
            total[k] = total.get(k, 0) + v

    if isinstance(node, str):
        if _key and _looks_like_name_key(_key) and _CJK_NAME.match(node):
            return PLACEHOLDER.format("姓名"), {"姓名": 1}
        cleaned, hits = scrub_text(node)
        merge(hits)
        return cleaned, total

    if isinstance(node, dict):
        out_d: dict[Any, Any] = {}
        for k, v in node.items():
            cleaned, hits = scrub_payload(v, str(k))
            merge(hits)
            out_d[k] = cleaned
        return out_d, total

    if isinstance(node, list):
        out_l = []
        for item in node:
            cleaned, hits = scrub_payload(item, _key)
            merge(hits)
            out_l.append(cleaned)
        return out_l, total

    return node, total


def scrub_for_prompt(report: Any) -> tuple[Any, dict[str, int]]:
    """进 prompt 前的最后一道闸。

    这是本模块唯一应当被生成链路调用的入口——命名成 ``for_prompt`` 是为了
    让调用点自我解释：**凡是要出境的素材，都得经过这里。**
    """
    return scrub_payload(report)
