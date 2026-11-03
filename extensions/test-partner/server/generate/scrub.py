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

## 序列化 JSON 要先解开再脱敏（BB-465）

HAR 把请求体与响应体存成**字符串**（`postData.text`、`content.text`），
里面是一整份序列化 JSON。若只把它当普通字符串走值形态正则，
**依赖键名的姓名规则就永远不会触发**——实测 `{"realName": "张三"}` 序列化后
原样出境，而手机号、身份证因为有形态特征反而被挡住了，问题因此长期被掩盖。

所以遇到"看起来是 JSON 的字符串"先解开、按结构脱敏、再序列化回去
（见 `_scrub_json_string`）。代价是格式被规范化（缩进与键序按 `json.dumps` 重排），
对 prompt 素材与交付产物都无影响；解不开就原样退回走文本规则，不会丢内容。

## 覆盖面与已知局限（如实写，不吹）

覆盖：中国大陆身份证、手机号、邮箱、银行卡号、IPv4、长不透明 id、
以及「键名像姓名 + 值是 2-4 个汉字」这一种启发式。

**不覆盖**：住址、生日、护照号、车牌、以及任何我们没想到的形态。
本模块是**减少**出境面，不是保证零 PII 出境。界面上不得宣称"已全部脱敏"。
"""

from __future__ import annotations

import json
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


#: 序列化 JSON 的嵌套解包上限。HAR 里出现过 body 套 body（网关转发原文），
#: 但再深就该怀疑是构造的数据了——给个界，免得畸形输入把栈吃穿。
_MAX_JSON_UNWRAP_DEPTH = 3


def _looks_like_json(text: str) -> bool:
    """便宜的预筛：只有像对象/数组的才值得试着解析。

    不用 try-parse 打头是因为绝大多数字符串都不是 JSON，
    每个都进一次异常处理在报告规模上是可观的浪费。
    """
    s = text.lstrip()
    return s[:1] in ("{", "[") and len(s) >= 2


def _scrub_json_string(text: str, depth: int) -> tuple[str, dict[str, int]] | None:
    """字符串若是序列化 JSON，解开按结构脱敏再序列化回去。

    返回 None 表示"这不是能解开的 JSON"，调用方退回走文本规则。
    只接受解析结果是 dict/list 的情形——`"123"` 也是合法 JSON 但会解成 int，
    换回去就把字符串变成了数字，那是改数据不是脱敏。
    """
    if depth >= _MAX_JSON_UNWRAP_DEPTH or not _looks_like_json(text):
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    cleaned, hits = scrub_payload(parsed, _depth=depth + 1)
    if not hits:
        # **没命中就原样退回，一个字节都不改。** 重新序列化会规范化空格与键序，
        # 而请求体的字节形态不是无所谓的：HMAC 签名类接口对 body 逐字节取摘要，
        # 把 `{"a":1}` 写成 `{"a": 1}` 就会验签失败。命中时才改是可以的——
        # 那时 body 反正已经因为脱敏而变了，签名本来就不再成立。
        return None
    return json.dumps(cleaned, ensure_ascii=False), hits


def scrub_payload(node: Any, _key: str = "", _depth: int = 0) -> tuple[Any, dict[str, int]]:
    """递归脱敏任意 JSON 结构（报告、样例、请求体都走这里）。

    字符串先试着当序列化 JSON 解开（BB-465：HAR 的 body 就是这个形态，
    不解开则依赖键名的姓名规则永远不触发），解不开再走 :func:`scrub_text`；
    此外当**键名像姓名**且值是 2-4 个汉字时，额外替换成 ``<姓名>``——
    这一条是启发式，见模块 docstring 的局限说明。
    """
    total: dict[str, int] = {}

    def merge(h: dict[str, int]) -> None:
        for k, v in h.items():
            total[k] = total.get(k, 0) + v

    if isinstance(node, str):
        if _key and _looks_like_name_key(_key) and _CJK_NAME.match(node):
            return PLACEHOLDER.format("姓名"), {"姓名": 1}
        nested = _scrub_json_string(node, _depth)
        if nested is not None:
            merge(nested[1])
            return nested[0], total
        cleaned, hits = scrub_text(node)
        merge(hits)
        return cleaned, total

    if isinstance(node, dict):
        out_d: dict[Any, Any] = {}
        for k, v in node.items():
            cleaned, hits = scrub_payload(v, str(k), _depth)
            merge(hits)
            out_d[k] = cleaned
        return out_d, total

    if isinstance(node, list):
        out_l = []
        for item in node:
            cleaned, hits = scrub_payload(item, _key, _depth)
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
