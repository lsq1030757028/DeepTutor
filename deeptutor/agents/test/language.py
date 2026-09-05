"""[fork] 「测试」模式的回复语种：跟着用户的话走，不跟着界面设置走（BB-698）。

## 为什么需要这个文件

`context.language` 来自请求里的界面语言（`api/routers/chat.py` → `language`），
默认 `en`。上一版把「回复语言 = 用户最后一条消息的语言」写进了提示词
（ff2802df），但**提示词本身按 `context.language` 选目录**：界面是 en 时，模型
收到的是英文纪律段、英文宿主简报、英文工具提示，"请用用户的语言作答"只是
五百行英文里的一句——2026-09-05 UAT 三条中文提问全部英文作答（S3 汉字 0 /
拉丁 1330）。语种要在**选提示词之前**定下来，而不是在提示词里恳求。

## 判定规则

只看用户消息里的**文字**：先剥掉 URL、路径、`#123` 一类标识符（一条需求链接
能贡献几十个拉丁字母，但它不是"英文提问"），再数汉字与拉丁字母：

- 汉字占（汉字 + 拉丁字母）≥ `ZH_RATIO_THRESHOLD` → `zh`；
- 有拉丁字母而汉字不足阈值 → `en`；
- 两者都没有（纯数字 / 纯链接 / 只点了选项）→ **判不出**，回落到对话里上一条
  能判出的用户消息，再回落到界面语言。

阈值取 0.3 不取 0.5：中文提问夹英文术语是常态（"帮我拉一下TAPD的需求"
汉字 9 / 拉丁 4 = 0.69；"测一下 deeptutor 的 login API" 汉字 3 / 拉丁 17 = 0.15
——这一条确实更像中文，但作者本人在混着写，按 en 回也不算错判；真要中文他会
多写两个字）。同一个数字也是离线判据用的门槛：`cjk_ratio(rendered_prompt)`
过它才算"中文提示词真的进了窗口"。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

#: 汉字占字母类字符的比例达到这个数就按中文走；同一门槛也用于离线断言。
ZH_RATIO_THRESHOLD = 0.3

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|/)[\w./\\-]+")
#: 长数字（需求号）与短代号（草稿 d1 / 规则 R2 / 用例 C003）都是引用，不是语言。
_IDENT_RE = re.compile(r"#?\b\d{4,}\b|\b[A-Za-z]{1,3}[-_]?\d+\b")
_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def strip_non_prose(text: str) -> str:
    """去掉链接、路径与长数字标识：它们是引用，不是提问的语言。"""
    cleaned = _URL_RE.sub(" ", str(text or ""))
    cleaned = _PATH_RE.sub(" ", cleaned)
    return _IDENT_RE.sub(" ", cleaned)


def cjk_ratio(text: str) -> float:
    """汉字 /（汉字 + 拉丁字母）。两者都没有时返回 0.0。"""
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = cjk + latin
    return cjk / total if total else 0.0


def detect_message_language(text: str) -> str | None:
    """`"zh"` / `"en"`，判不出返回 `None`（由调用方回落）。"""
    prose = strip_non_prose(text)
    cjk = len(_CJK_RE.findall(prose))
    latin = len(_LATIN_RE.findall(prose))
    if cjk == 0 and latin == 0:
        return None
    return "zh" if cjk / (cjk + latin) >= ZH_RATIO_THRESHOLD else "en"


def _user_texts(history: Iterable[Any]) -> list[str]:
    texts: list[str] = []
    for message in history:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.append(
                " ".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, Mapping) and part.get("type") == "text"
                )
            )
    return texts


def resolve_turn_language(
    user_message: str,
    *,
    conversation_history: Iterable[Any] = (),
    fallback: str = "en",
) -> str:
    """本轮的回复语种：当前消息 → 对话里上一条可判的用户消息 → 界面语言。"""
    detected = detect_message_language(user_message)
    if detected:
        return detected
    for text in reversed(_user_texts(conversation_history)):
        detected = detect_message_language(text)
        if detected:
            return detected
    return "zh" if str(fallback or "").lower().startswith("zh") else "en"


__all__ = [
    "ZH_RATIO_THRESHOLD",
    "cjk_ratio",
    "detect_message_language",
    "resolve_turn_language",
    "strip_non_prose",
]
