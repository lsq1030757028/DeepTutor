"""Fallback parser for text-protocol ``<tool_call>`` blocks (BB-717).

Some providers reach DeepTutor through a text-only relay that has no native
function calling (the UAT Claude bridge is one): the relay asks the model to
write a tool call as::

    <tool_call>
    {"name": "ask_user", "input": {...}}
    </tool_call>

and turns the block back into a structured tool call. When the model's JSON is
slightly off — an unescaped double quote inside a string, one stray closing
bracket — a strict relay gives up, and the whole block streams to the user as
the final answer: the card never appears, the trajectory shows zero tool
calls, and the turn ends (2026-09-05 UAT, session
``unified_1788603204448_e9762dd3``, twice in one walk-through).

This module is the host-side guarantee that such a block **never reaches the
conversation as text**: a well-formed or mechanically repairable block becomes
a tool call on the normal dispatch path; an unrepairable one is stripped from
the text (and logged) so the loop treats the round as an empty answer and
nudges the model instead of ending on raw JSON.

Pure functions — no I/O, no LLM. Mirrors :mod:`dsml_tool_calls` (issue #666).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# The protocol block. ``<tool_call>`` alone (no JSON yet) already counts as a
# signal so a streaming round can divert the rest to the thinking channel.
TEXT_TOOL_CALL_SIGNAL_RE = re.compile(r"<tool_call>", re.IGNORECASE)
_BLOCK_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.IGNORECASE | re.DOTALL)
# An opening tag whose closing tag never arrived (truncated output): still
# not something the user should read.
_DANGLING_RE = re.compile(r"<tool_call>.*\Z", re.IGNORECASE | re.DOTALL)
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.DOTALL)


def has_text_tool_call_markup(text: str | None) -> bool:
    """Cheap check for whether ``text`` carries a ``<tool_call>`` block."""
    return bool(text) and bool(TEXT_TOOL_CALL_SIGNAL_RE.search(text))


def repair_json_text(raw: str) -> str:
    """Mechanically fix the two slips models make inside a call block.

    1. An unescaped ``"`` inside a string: a quote that is not followed (after
       whitespace) by ``,`` ``:`` ``}`` ``]`` or the end cannot be a closing
       quote, so it is escaped.
    2. Unbalanced brackets: a closer that does not match the innermost opener
       is dropped, anything after the root object closes is cut, and openers
       still pending at the end are closed.

    No semantic guessing: text that is still invalid afterwards stays invalid
    and the caller refuses it rather than dispatching a guess.
    """
    text = _JSON_FENCE_RE.sub("", raw)
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                out.append(text[i : i + 2])
                i += 2
                continue
            if ch == '"':
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j >= n or text[j] in ",:}]":
                    in_str = False
                    out.append(ch)
                else:
                    out.append('\\"')
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "{[":
            stack.append(ch)
            out.append(ch)
        elif ch in "}]":
            want = "{" if ch == "}" else "["
            if stack and stack[-1] == want:
                stack.pop()
                out.append(ch)
                if not stack:
                    break
            # a closer that matches nothing: dropped
        else:
            out.append(ch)
        i += 1
    while stack:
        out.append("}" if stack.pop() == "{" else "]")
    return "".join(out)


def lenient_json_object(raw: str) -> dict[str, Any] | None:
    """Strict parse first, then the repaired text. ``None`` when neither is a dict."""
    for candidate in (raw, repair_json_text(raw)):
        try:
            obj, _end = json.JSONDecoder().raw_decode(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def extract_text_tool_calls(text: str | None) -> tuple[list[dict[str, Any]], str]:
    """Turn ``<tool_call>`` blocks in ``text`` into structured tool calls.

    Returns ``(tool_calls, cleaned_text)``. With no markup present the result
    is ``([], text)`` (same object) so callers can treat it as "not a
    text-protocol round". Blocks that cannot be parsed even after repair
    produce no call but are still removed from ``cleaned_text``: raw protocol
    JSON is never an answer.
    """
    if not has_text_tool_call_markup(text):
        return [], text or ""
    assert text is not None
    calls: list[dict[str, Any]] = []
    unparsable = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal unparsable
        call = lenient_json_object(match.group(1))
        name = str((call or {}).get("name") or "").strip() if call else ""
        if not name:
            unparsable += 1
            return ""
        arguments = call.get("input")
        if arguments is None:
            arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append(
            {
                "id": f"textcall_{len(calls)}",
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        )
        return ""

    cleaned = _BLOCK_RE.sub(_replace, text)
    if _DANGLING_RE.search(cleaned):
        unparsable += 1
        cleaned = _DANGLING_RE.sub("", cleaned)
    if unparsable:
        logger.warning(
            "text-protocol tool call block unparsable (%d); stripped from the answer",
            unparsable,
        )
    return calls, cleaned.strip()
