"""Single-loop chat agent.

One chat turn = ONE agent loop over a single growing conversation:

* each round is one LLM call; its text streams to the user as a ``content``
  block, and its tool calls are dispatched with their ``role=tool`` results
  appended back into the conversation;
* a round that DOES call tools is "narration" — its text is a preamble to
  the tool work — and the loop continues;
* a round that calls NO tools is the ``finish``: its text IS the final
  user-facing answer and the loop ends (the model deciding it is done; a
  first round without tool calls is the "no exploration needed" fast path);
* if the round budget runs out while tools are still being requested, one
  final tool-less ``finish`` round is forced.

``ask_user`` pauses the turn for a reply and resumes in-protocol; an
unresolved pause (or a terminator tool) halts the turn.

There is no separate respond pass and no text destination has to be guessed
mid-stream: every round's text streams to the user as it is generated, and a
``call_role`` (``narration`` vs ``finish``) emitted when the round completes
tells the frontend how to render that round's text.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from deeptutor.agents._shared.capability_result import emit_capability_result
from deeptutor.agents.chat.context_budget import LLMRequestSnapshot
from deeptutor.agents.chat.dsml_tool_calls import extract_dsml_tool_calls, has_dsml_tool_calls
from deeptutor.core.agentic.messages import assistant_message_with_tool_calls
from deeptutor.core.agentic.tool_dispatch import DispatchOutcome
from deeptutor.core.agentic.usage import message_content_chars, record_streamed_usage
from deeptutor.core.context import UnifiedContext
from deeptutor.core.observability.stop_reason import stop_completed
from deeptutor.core.observability.trace_sidecar import record_header_async, unavailable_ref
from deeptutor.core.stream_bus import StreamBus
from deeptutor.core.trace import build_trace_metadata, merge_trace_metadata, new_call_id
from deeptutor.services.llm import clean_thinking_tags
from deeptutor.services.llm.multimodal import should_degrade_to_text, strip_image_parts_inplace
from deeptutor.services.llm.request_compat import (
    is_image_input_unsupported,
    is_stream_options_unsupported,
    is_tool_schema_unsupported,
)

if TYPE_CHECKING:  # pragma: no cover
    from deeptutor.agents.chat.agentic_pipeline import AgenticChatPipeline

logger = logging.getLogger(__name__)

# The loop runs over a single conversation; this is the maximum number of
# tool-calling rounds before a tool-less finish is forced. The model normally
# exits earlier by replying without tool calls.
LOOP_STAGE = "responding"

_THINK_OPEN_RE = re.compile(r"<\s*think(?:ing)?\b[^>]*>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"<\s*/\s*think(?:ing)?\s*>", re.IGNORECASE)
# Longest partial tag worth waiting a chunk for (e.g. "</thinking" + slack).
_TAG_HOLDBACK_CHARS = 24


class InlineThinkFilter:
    """Incremental ``<think>``/``<thinking>`` splitter for streamed content.

    Some providers surface reasoning inline in the *content* channel (instead
    of ``reasoning_content``), wrapped in think tags. Splitting at streaming
    time keeps the user-facing content channel clean everywhere downstream —
    the live bubble, the persisted message, and the loop's finish detection —
    in one place. The raw text (tags included) still goes back into the LLM
    conversation untouched.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_think = False

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Consume *chunk*; return ``(kind, text)`` segments, kind in
        ``{"content", "thinking"}``. May hold back a partial trailing tag
        until the next chunk (``flush`` releases it at stream end)."""
        self._buffer += chunk
        segments: list[tuple[str, str]] = []
        while True:
            pattern = _THINK_CLOSE_RE if self._in_think else _THINK_OPEN_RE
            match = pattern.search(self._buffer)
            if match is None:
                break
            if match.start() > 0:
                segments.append((self._kind(), self._buffer[: match.start()]))
            self._buffer = self._buffer[match.end() :]
            self._in_think = not self._in_think
        emit_upto = len(self._buffer)
        tag_start = self._buffer.rfind("<")
        if (
            tag_start != -1
            and len(self._buffer) - tag_start <= _TAG_HOLDBACK_CHARS
            and ">" not in self._buffer[tag_start:]
        ):
            emit_upto = tag_start
        if emit_upto > 0:
            segments.append((self._kind(), self._buffer[:emit_upto]))
            self._buffer = self._buffer[emit_upto:]
        return segments

    def flush(self) -> list[tuple[str, str]]:
        """Release whatever is still buffered (stream ended)."""
        if not self._buffer:
            return []
        segments = [(self._kind(), self._buffer)]
        self._buffer = ""
        return segments

    def _kind(self) -> str:
        return "thinking" if self._in_think else "content"


@dataclass(slots=True)
class AgentLoopState:
    """Turn-level counters shared across the loop's rounds."""

    rounds: int = 0
    tool_steps: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class LLMCallResult:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""


@dataclass(slots=True)
class LoopOutcome:
    """Result of running the turn's loop.

    ``final_text`` is the user-facing answer (the finish round's text, or a
    terminator tool's content). ``completed`` is False only when the turn
    halted on an unresolved ``ask_user`` pause — the pending question is then
    the turn's final artefact.

    ``last_step`` names the loop-level outcome of the step that ended the turn
    (``pause`` / ``terminator`` / ``forced_budget`` / ``forced_error``). Those
    four are decided *after* the LLM call they follow has already reported
    ``call_status/complete``, so they cannot be carried on that event without
    delaying it past the round's tool dispatch; they ride the turn's ``stop``
    envelope instead. A plain finish leaves this ``None``.
    """

    final_text: str = ""
    completed: bool = False
    last_step: dict[str, Any] | None = None


class AgentLoop:
    """Run one chat turn as a single agent loop over one conversation."""

    def __init__(
        self,
        *,
        pipeline: "AgenticChatPipeline",
        context: UnifiedContext,
        stream: StreamBus,
        client: Any,
        enabled_tools: list[str],
        tool_schemas: list[dict[str, Any]] | None,
    ) -> None:
        self.pipeline = pipeline
        self.context = context
        self.stream = stream
        self.client = client
        self.enabled_tools = enabled_tools
        self.tool_schemas = tool_schemas
        self._last_request: LLMRequestSnapshot | None = None
        # Turn/step double numbering. One user turn is one AgentLoop, so the
        # turn index is read off the history this turn was built from (the
        # count of user messages already in the conversation) and every LLM
        # call inside the loop takes the next step index.
        self._turn_index = _count_user_turns(context)
        self._step_index = 0
        # Provider-compat retries spent inside this turn (stream_options /
        # tool-schema / image fallbacks). Reported on the turn's ``stop`` so a
        # failure that took three attempts does not read like a first-try one.
        self._provider_retries = 0
        # Prompt-block breakdown, measured once. The system prompt is
        # byte-stable for the whole turn by design (``prompt_blocks``), so
        # re-tokenising it every round would burn CPU to produce the same
        # numbers.
        self._prompt_segments_cache: list[dict[str, Any]] | None = None

    async def run(self) -> None:
        state = AgentLoopState()
        # Optional async pre-pass briefings (e.g. explore_context) run BEFORE
        # the answer stage so they form their own preceding activity group and
        # their grounding can ride in the loop's user-message seed.
        capability_briefing = await self.pipeline._capability_pre_loop_briefings(
            self.context, self.stream
        )
        async with self.stream.stage(LOOP_STAGE, source="chat"):
            # First row of this turn's trace: what the capability-assembly step
            # managed to put on the table, before the model gets a say. Without
            # it, "the model did not call the tool" and "the tool was never
            # offered" look identical after the fact.
            await self.pipeline.emit_channel_health(self.stream, stage=LOOP_STAGE)
            seed_block = await self.pipeline._retrieve_kb_seed_block(self.context, self.stream)
            capability_seed = self.pipeline._capability_pre_loop_seed(self.context)
            seed_block = "\n\n".join(
                block
                for block in (
                    seed_block.strip(),
                    capability_seed.strip(),
                    capability_briefing.strip(),
                )
                if block
            )
            messages = self.pipeline._build_loop_messages(
                context=self.context,
                enabled_tools=self.enabled_tools,
                kb_seed=seed_block,
                include_tool_manifest=bool(self.tool_schemas),
            )
            outcome = await self._run_loop(
                messages=messages,
                state=state,
                checkpoint_boundary=len(messages),
            )

        if state.sources:
            await self.stream.sources(
                state.sources,
                source="chat",
                stage=LOOP_STAGE,
                metadata={"trace_kind": "sources"},
            )
        payload: dict[str, Any] = {
            "response": outcome.final_text,
            "completed": outcome.completed,
            "engine": "agent_loop",
            "rounds": state.rounds,
            "tool_steps": state.tool_steps,
            # Reaching here means the loop ended on its own terms, which is the
            # ``completed`` row ``turns.status`` will carry. The cancelled and
            # failed terminal states never get this far — they are stamped on
            # the turn-terminal error event in ``turn_runtime``.
            "stop": stop_completed(
                retries=self._provider_retries or None,
                step_outcome=outcome.last_step,
            ),
        }
        if self._last_request is not None:
            budget = self.pipeline.measure_context_budget(self._last_request)
            if budget is not None:
                payload["metadata"] = {"context_budget": budget}
        await emit_capability_result(
            self.stream,
            payload,
            source="chat",
            usage=self.pipeline.usage,
        )

    def _clean(self, text: str) -> str:
        return clean_thinking_tags(text, self.pipeline.binding, self.pipeline.model).strip()

    # ---- agent loop --------------------------------------------------------

    async def _run_loop(
        self,
        *,
        messages: list[dict[str, Any]],
        state: AgentLoopState,
        checkpoint_boundary: int,
    ) -> LoopOutcome:
        """Run rounds of one LLM call + tool dispatch over *messages*.

        A round with tool calls keeps its assistant message (text + tool
        calls) and the ``role=tool`` results in-conversation, then continues.
        A round with no tool calls is the finish: its text — already streamed
        to the user — is the answer, and the loop ends.
        """
        explore_label = self.pipeline._t("labels.exploring", default="Exploring")
        nudged_empty_finish = False
        for _round in range(max(1, self.pipeline.effective_max_rounds(self.context))):
            try:
                result = await self._call_llm(
                    messages=messages,
                    label=explore_label,
                    call_kind="agent_loop_round",
                    trace_role="explore",
                    max_tokens=self.pipeline.loop_max_tokens,
                    tool_schemas=self.tool_schemas,
                    can_nudge=not nudged_empty_finish,
                )
            except Exception as exc:
                # A mid-loop LLM failure (timeout / transient network) must not
                # discard a turn that already gathered useful work. Salvage it
                # with a forced finish; only a failure on the very first round
                # (nothing gathered yet) propagates as before.
                if state.rounds == 0:
                    raise
                logger.warning(
                    "agent loop round failed after %d round(s); forcing finish: %s",
                    state.rounds,
                    exc,
                )
                return await self._forced_finish(messages, state, reason="error")
            state.rounds += 1
            if not result.tool_calls:
                final_text = self._clean(result.text)
                if not final_text and not nudged_empty_finish:
                    # The round produced only internal reasoning (e.g. the
                    # whole reply inside <think>) — the model planned but
                    # never acted. Keep its raw text in-conversation (the
                    # plan/script lives there) and nudge it once to act
                    # instead of falling back to an empty answer.
                    nudged_empty_finish = True
                    await self.stream.progress(
                        self.pipeline._t(
                            "notices.empty_finish_nudged",
                            default=(
                                "The round produced only internal reasoning; "
                                "asked the model to continue."
                            ),
                        ),
                        source="chat",
                        stage=LOOP_STAGE,
                        metadata={"trace_kind": "warning"},
                    )
                    if result.text:
                        messages.append({"role": "assistant", "content": result.text})
                    messages.append(
                        {
                            "role": "user",
                            "content": self.pipeline._t(
                                "loop.finish_empty_nudge",
                                default=(
                                    "Your previous round produced only internal "
                                    "reasoning — no tool call and no user-facing "
                                    "answer. Continue now: either call the tools "
                                    "to execute your plan, or write the final "
                                    "user-facing answer directly."
                                ),
                            ),
                        }
                    )
                    continue
                # Finish: the text streamed live this round IS the answer.
                return await self._finalize_finish(final_text)

            messages.append(assistant_message_with_tool_calls(result.text, result.tool_calls))
            dispatch = await self.pipeline._dispatch_tool_calls(
                tool_calls=result.tool_calls,
                context=self.context,
                stream=self.stream,
                iteration_index=state.tool_steps,
                stage=LOOP_STAGE,
            )
            state.tool_steps += 1
            state.sources.extend(dispatch.sources)
            messages.extend(dispatch.tool_messages)

            if dispatch.pause:
                resumed = await self.pipeline._await_user_reply_and_resolve(
                    context=self.context,
                    stream=self.stream,
                    dispatch=dispatch,
                )
                if not resumed:
                    # The pending question is already the turn's final
                    # artefact (or the user abandoned the turn) — stop.
                    return LoopOutcome(
                        final_text="",
                        completed=False,
                        last_step=self._last_step("pause"),
                    )
                # The user's answers were substituted into the matching
                # ``role=tool`` message; the next round sees them in-protocol.
                continue

            checkpoint_boundary = self._fold_context_checkpoint(
                messages=messages,
                dispatch=dispatch,
                checkpoint_boundary=checkpoint_boundary,
            )

            if dispatch.terminate:
                payload = dispatch.terminate_payload or {}
                await self.pipeline._emit_terminator_final_response(self.stream, payload)
                return LoopOutcome(
                    final_text=str(payload.get("content") or ""),
                    completed=True,
                    last_step=self._last_step("terminator"),
                )

        # Round budget ran out while still requesting tools — force a finish.
        return await self._forced_finish(messages, state)

    def _fold_context_checkpoint(
        self,
        *,
        messages: list[dict[str, Any]],
        dispatch: DispatchOutcome,
        checkpoint_boundary: int,
    ) -> int:
        summary = _last_context_checkpoint_summary(dispatch)
        if not summary:
            return checkpoint_boundary
        prefix = messages[:checkpoint_boundary]
        prefix.append(
            {
                "role": "system",
                "content": f"[Context checkpoint]\n{summary}",
            }
        )
        messages[:] = prefix
        return len(messages)

    async def _forced_finish(
        self,
        messages: list[dict[str, Any]],
        state: AgentLoopState,
        *,
        reason: str = "budget",
    ) -> LoopOutcome:
        if reason == "error":
            notice = self.pipeline._t(
                "notices.loop_error_finish",
                default="A step failed; answering with what has been gathered.",
            )
        else:
            notice = self.pipeline._t(
                "notices.loop_budget_exhausted",
                default="Exploration budget reached; answering with what has been gathered.",
            )
        await self.stream.progress(
            notice,
            source="chat",
            stage=LOOP_STAGE,
            metadata={"trace_kind": "warning"},
        )
        messages.append({"role": "user", "content": self.pipeline._finish_exhausted_instruction()})
        outcome_kind = "forced_error" if reason == "error" else "forced_budget"
        try:
            result = await self._call_llm(
                messages=messages,
                label=self.pipeline._t("labels.final_response", default="Final response"),
                call_kind="llm_final_response",
                trace_role="response",
                max_tokens=self.pipeline.loop_max_tokens,
                tool_schemas=None,  # tools disabled so the model must finish
                outcome_hint=outcome_kind,
                outcome_detail=reason,
            )
        except Exception as exc:
            # The salvage call itself failed (e.g. the provider is still
            # stalling). Don't bubble up and lose the turn — emit the graceful
            # fallback answer instead.
            logger.warning("forced-finish LLM call failed: %s", exc)
            return await self._finalize_finish(
                "", last_step=self._last_step(outcome_kind, detail=reason)
            )
        state.rounds += 1
        return await self._finalize_finish(
            result.text, last_step=self._last_step(outcome_kind, detail=reason)
        )

    def _last_step(self, kind: str, *, detail: str = "") -> dict[str, Any]:
        """Loop-level outcome of the step that is ending the turn."""
        step: dict[str, Any] = {
            "kind": kind,
            "turn_index": self._turn_index,
            "step_index": max(0, self._step_index - 1),
        }
        if detail:
            step["detail"] = detail
        return step

    async def _finalize_finish(
        self,
        raw_text: str,
        *,
        last_step: dict[str, Any] | None = None,
    ) -> LoopOutcome:
        final_text = self._clean(raw_text)
        if not final_text:
            # The finish round produced no usable text; nothing streamed to
            # the user, so emit a fallback answer here.
            final_text = self.pipeline._t(
                "notices.empty_final_response",
                default=(
                    "I could not produce a useful response from the model "
                    "output. Please try again or narrow the request."
                ),
            )
            await self.pipeline._emit_protocol_fallback_final_response(self.stream, final_text)
        return LoopOutcome(final_text=final_text, completed=True, last_step=last_step)

    # ---- LLM call ----------------------------------------------------------

    async def _call_llm(
        self,
        *,
        messages: list[dict[str, Any]],
        label: str,
        call_kind: str,
        trace_role: str,
        max_tokens: int,
        tool_schemas: list[dict[str, Any]] | None = None,
        can_nudge: bool = False,
        outcome_hint: str | None = None,
        outcome_detail: str = "",
    ) -> LLMCallResult:
        await self.pipeline._guard_context_window(messages, self.stream)
        stage = LOOP_STAGE
        step_index = self._step_index
        self._step_index += 1
        call_id = new_call_id(f"chat-{stage}")
        trace_meta = build_trace_metadata(
            call_id=call_id,
            phase=stage,
            label=label,
            call_kind=call_kind,
            trace_id=call_id,
            trace_role=trace_role,
            trace_group="stage",
        )
        await self.stream.progress(
            label,
            source="chat",
            stage=stage,
            metadata=merge_trace_metadata(
                trace_meta,
                {
                    "trace_kind": "call_status",
                    "call_state": "running",
                    "turn_index": self._turn_index,
                    "step_index": step_index,
                },
            ),
        )

        kwargs: dict[str, Any] = {
            "model": self.pipeline.model,
            "messages": messages,
            "stream": True,
            **self.pipeline._completion_kwargs(max_tokens=max_tokens),
        }
        if self.pipeline.usage is not None:
            kwargs["stream_options"] = {"include_usage": True}
        if tool_schemas:
            kwargs["tools"] = tool_schemas
            kwargs["tool_choice"] = "auto"
        # What this request actually carried, pinned now: the loop keeps
        # appending to ``messages`` and the deferred loader keeps appending to
        # ``tool_schemas``, so the turn's context budget is read off the last
        # snapshot rather than off the lists' end state.
        #
        # The forced-finish round deliberately ships no ``tools`` so the model
        # must answer. That absence is a loop mechanic, not a turn that ran
        # without tools, so the last non-empty schema list stands — otherwise a
        # turn that spent eight rounds calling tools would report zero tokens
        # for the schemas that sat in its window the whole time.
        carried = list(tool_schemas or [])
        if not carried and self._last_request is not None:
            carried = self._last_request.tool_schemas
        self._last_request = LLMRequestSnapshot(messages=list(messages), tool_schemas=carried)

        # What this call was given, recorded before it is made. The bodies go
        # to the session sidecar (off the business database, out of the
        # model's reach); the event carries only the reference.
        header_ref = await self._record_header(
            kwargs=kwargs,
            messages=messages,
            tool_schemas=tool_schemas,
            step_index=step_index,
        )
        await self._emit_model_input(
            trace_meta=trace_meta,
            stage=stage,
            step_index=step_index,
            messages=messages,
            tool_schemas=tool_schemas,
            header_ref=header_ref,
        )

        # Providers (esp. Gemini OpenAI-compat) may attach ``usage`` to more
        # than one stream chunk. Keep the latest frame; it is recorded once
        # after the stream via ``record_streamed_usage``.
        usage_seen: Any = None
        text_parts: list[str] = []
        tool_acc: dict[int, dict[str, str]] = {}
        output_chars = 0
        finish_reason = ""
        think_filter = InlineThinkFilter()
        # Once a round's content channel starts carrying DeepSeek DSML tool-call
        # markup (issue #666), divert the rest of it to the thinking channel so
        # the raw markup never streams as the user-facing answer. It is parsed
        # into real tool calls after the stream completes.
        dsml_stream_active = False
        chunk_meta = merge_trace_metadata(trace_meta, {"trace_kind": "llm_chunk"})

        async def _emit_segments(segments: list[tuple[str, str]]) -> None:
            for kind, segment in segments:
                if kind == "content":
                    await self.stream.content(
                        segment, source="chat", stage=stage, metadata=chunk_meta
                    )
                else:
                    await self.stream.thinking(
                        segment, source="chat", stage=stage, metadata=chunk_meta
                    )

        response_stream = await self._create_response_stream(kwargs, trace_meta, stage)
        try:
            async for chunk in response_stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_seen = usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                if getattr(choice, "finish_reason", None):
                    finish_reason = str(choice.finish_reason)
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                reasoning_text = getattr(delta, "reasoning_content", None) or getattr(
                    delta,
                    "reasoning",
                    None,
                )
                if reasoning_text:
                    output_chars += len(reasoning_text)
                    await self.stream.thinking(
                        reasoning_text, source="chat", stage=stage, metadata=chunk_meta
                    )

                content = getattr(delta, "content", None)
                if content:
                    output_chars += len(content)
                    text_parts.append(content)
                    # Every round's text streams to the user; the round's
                    # call_role (emitted at completion) tells the frontend
                    # whether to render it as narration or as the answer.
                    # Inline <think> segments are split off to the thinking
                    # channel so the content stream stays user-facing.
                    if not dsml_stream_active and has_dsml_tool_calls("".join(text_parts)):
                        dsml_stream_active = True
                    segments = think_filter.feed(content)
                    if dsml_stream_active:
                        segments = [("thinking", seg) for _, seg in segments]
                    await _emit_segments(segments)

                for tc_delta in getattr(delta, "tool_calls", None) or []:
                    index = int(getattr(tc_delta, "index", 0) or 0)
                    acc = tool_acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    tcid = getattr(tc_delta, "id", None)
                    if tcid:
                        acc["id"] += str(tcid)
                    fn = getattr(tc_delta, "function", None)
                    if fn is None:
                        continue
                    name = getattr(fn, "name", None)
                    arguments = getattr(fn, "arguments", None)
                    if name:
                        acc["name"] += str(name)
                        output_chars += len(str(name))
                    if arguments:
                        acc["arguments"] += str(arguments)
                        output_chars += len(str(arguments))
        finally:
            close = getattr(response_stream, "close", None)
            if callable(close):
                with suppress(Exception):
                    await close()

        flushed = think_filter.flush()
        if dsml_stream_active:
            flushed = [("thinking", seg) for _, seg in flushed]
        await _emit_segments(flushed)
        text = "".join(text_parts)
        record_streamed_usage(
            self.pipeline.usage,
            usage_seen,
            input_chars=sum(message_content_chars(message) for message in messages),
            output_chars=output_chars,
        )

        tool_calls = [
            {
                "id": data.get("id") or f"call_{idx}",
                "name": data.get("name", ""),
                "arguments": data.get("arguments") or "{}",
            }
            for idx, data in sorted(tool_acc.items())
            if data.get("name")
        ]

        # Fallback: a DeepSeek deployment without native function calling emits
        # its tool calls as DSML markup in the content channel instead of as
        # structured ``tool_calls`` (issue #666). Parse them out so the normal
        # dispatch path runs the tools, and drop the markup from ``text`` so it
        # can't leak into the conversation / answer.
        if not tool_calls:
            dsml_calls, cleaned_text = extract_dsml_tool_calls(text)
            if dsml_calls:
                tool_calls = dsml_calls
                text = cleaned_text

        step_outcome = self._step_outcome(
            tool_calls=tool_calls,
            text=text,
            can_nudge=can_nudge,
            outcome_hint=outcome_hint,
            outcome_detail=outcome_detail,
        )
        await self.stream.progress(
            "",
            source="chat",
            stage=stage,
            metadata=merge_trace_metadata(
                trace_meta,
                {
                    "trace_kind": "call_status",
                    "call_state": "complete",
                    # A round with tool calls is narration; a tool-less round
                    # is the finish whose text is the user-facing answer.
                    "call_role": "narration" if tool_calls else "finish",
                    "turn_index": self._turn_index,
                    "step_index": step_index,
                    # Verbatim from the provider — ``stop`` / ``tool_calls`` /
                    # ``length`` / ``content_filter`` / "" — deliberately not
                    # normalised: "the provider sent nothing" is itself a lead.
                    "finish_reason": finish_reason,
                    "step_outcome": step_outcome,
                    "tool_calls_requested": [call["name"] for call in tool_calls],
                    "tools_offered": len(tool_schemas or []),
                    "header_ref": header_ref,
                },
            ),
        )
        return LLMCallResult(text=text, tool_calls=tool_calls, finish_reason=finish_reason)

    # ---- what the model was given -----------------------------------------

    async def _record_header(
        self,
        *,
        kwargs: dict[str, Any],
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]] | None,
        step_index: int,
    ) -> dict[str, Any]:
        """Write this call's three header parts to the session sidecar."""
        try:
            return await record_header_async(
                session_id=self.context.session_id,
                parts=_header_parts(self.pipeline.binding, kwargs, messages, tool_schemas),
                turn_index=self._turn_index,
                step_index=step_index,
            )
        except Exception:  # pragma: no cover - recording must never sink a turn
            logger.debug("header capture skipped", exc_info=True)
            return unavailable_ref()

    async def _emit_model_input(
        self,
        *,
        trace_meta: dict[str, Any],
        stage: str,
        step_index: int,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]] | None,
        header_ref: dict[str, Any],
    ) -> None:
        """One ``model_input`` event per LLM call: the shape of the request.

        Shape only. The prompt text and the tool schemas are in the sidecar,
        reachable through ``header_ref`` — putting them here would double them
        into ``turn_events`` and the assistant message both.

        ``content`` is empty for the same reason ``channel_health``'s is: a
        ``progress`` event with content and a non-``call_status`` trace_kind is
        rendered as conversation. The readable line rides ``status_key``.
        """
        try:
            loader = getattr(self.pipeline, "_deferred_loader", None)
            loaded = sorted(getattr(loader, "loaded_names", set()) or set())
            pool_size = len(getattr(self.pipeline, "_deferred_pool", []) or [])
            await self.stream.progress(
                "",
                source="chat",
                stage=stage,
                metadata=merge_trace_metadata(
                    trace_meta,
                    {
                        "trace_kind": "model_input",
                        "status_key": "model_input",
                        "turn_index": self._turn_index,
                        "step_index": step_index,
                        "prompt_segments": self._prompt_segments(),
                        # The direct answer to "what did it have in hand" —
                        # the whole list, not a count, because the question is
                        # always about one specific tool.
                        "tools_in_window": [_schema_name(item) for item in tool_schemas or []],
                        "tools_loaded": loaded,
                        "tools_deferred_count": max(0, pool_size - len(loaded)),
                        "messages_skeleton": [_message_skeleton(item) for item in messages],
                        "header_ref": header_ref,
                    },
                ),
            )
        except Exception:  # pragma: no cover - informational extra
            logger.debug("model_input event not emitted", exc_info=True)

    def _prompt_segments(self) -> list[dict[str, Any]]:
        """``[{name, tokens, sha12}]`` for the blocks the prompt was built from.

        ``name`` is the block's own name, not the context-budget segment it
        rolls up into: the budget deliberately merges several blocks into one
        line for the composer, and merging them here would hide which block
        moved.
        """
        if self._prompt_segments_cache is not None:
            return self._prompt_segments_cache
        segments: list[dict[str, Any]] = []
        try:
            counter = _token_counter()
            for block in getattr(self.pipeline, "_last_prompt_blocks", []) or []:
                content = (block.content or "").strip()
                if not content:
                    continue
                segments.append(
                    {
                        "name": block.name,
                        "tokens": counter(content),
                        "sha12": hashlib.sha256(content.encode("utf-8")).hexdigest()[:12],
                    }
                )
        except Exception:  # pragma: no cover - informational extra
            logger.debug("prompt segment breakdown unavailable", exc_info=True)
        self._prompt_segments_cache = segments
        return segments

    def _step_outcome(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        text: str,
        can_nudge: bool,
        outcome_hint: str | None,
        outcome_detail: str,
    ) -> dict[str, Any]:
        """Why this LLM call ended, as the loop reads it.

        Deliberately parallel to ``finish_reason`` rather than merged with it:
        the provider's word and the loop's own reading disagreeing is exactly
        the kind of thing worth being able to see.

        Only the outcomes decidable *at call completion* appear here. A round
        whose tool dispatch later pauses or terminates the turn is still
        ``narration`` from this call's point of view; that turn-level ending
        rides ``stop.step_outcome`` (see :class:`LoopOutcome`).
        """
        if outcome_hint:
            outcome: dict[str, Any] = {"kind": outcome_hint}
            if outcome_detail:
                outcome["detail"] = outcome_detail
            return outcome
        if tool_calls:
            return {"kind": "narration"}
        if can_nudge and not self._clean(text):
            # Matches the loop's own nudge condition: a tool-less round whose
            # text cleans away to nothing gets one prod to act instead of
            # ending the turn on an empty answer.
            return {"kind": "nudge_empty"}
        return {"kind": "finish"}

    async def _create_response_stream(
        self,
        kwargs: dict[str, Any],
        trace_meta: dict[str, Any],
        stage: str,
    ) -> Any:
        try:
            return await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "stream_options" in kwargs and is_stream_options_unsupported(exc):
                self._provider_retries += 1
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop("stream_options", None)
                return await self.client.chat.completions.create(**retry_kwargs)
            if kwargs.get("tools") and is_tool_schema_unsupported(exc):
                self._provider_retries += 1
                await self.stream.progress(
                    self.pipeline._t(
                        "notices.tool_schema_fallback",
                        default="Provider rejected native tool schemas; retrying without tools.",
                    ),
                    source="chat",
                    stage=stage,
                    metadata=merge_trace_metadata(
                        trace_meta,
                        {"trace_kind": "warning", "tool_schema_fallback": True},
                    ),
                )
                retry_kwargs = dict(kwargs)
                retry_kwargs.pop("tools", None)
                retry_kwargs.pop("tool_choice", None)
                self.tool_schemas = None
                return await self.client.chat.completions.create(**retry_kwargs)
            if is_image_input_unsupported(exc) and should_degrade_to_text(
                self.pipeline.binding,
                self.pipeline.model,
                kwargs.get("messages") or [],
            ):
                self._provider_retries += 1
                strip_image_parts_inplace(kwargs["messages"])
                await self.stream.progress(
                    self.pipeline._t(
                        "notices.image_fallback",
                        default="Model does not support image input; retrying without images.",
                    ),
                    source="chat",
                    stage=stage,
                    metadata=merge_trace_metadata(
                        trace_meta,
                        {"trace_kind": "warning", "image_fallback": True},
                    ),
                )
                return await self.client.chat.completions.create(**kwargs)
            raise


def _header_parts(
    binding: str,
    kwargs: dict[str, Any],
    messages: list[dict[str, Any]],
    tool_schemas: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """The three independently-hashed parts of one request header.

    Split this way because they change at completely different rates: the
    system prompt is usually constant for a whole session while the tool
    schemas move a few times, so hashing them together would rewrite the
    prompt every time a tool appeared.

    ``messages`` are NOT a part. User and assistant text is already stored in
    full in the messages table, and tool results — the one genuinely new
    exposure — are deliberately out of scope for now.
    """
    system = ""
    if messages and messages[0].get("role") == "system":
        content = messages[0].get("content")
        if isinstance(content, str):
            system = content
    config = {
        "binding": binding,
        **{
            key: value
            for key, value in kwargs.items()
            if key not in {"messages", "tools", "tool_choice", "stream", "stream_options"}
        },
    }
    return {
        "system": system,
        "tools": json.dumps(tool_schemas or [], ensure_ascii=False, sort_keys=True, default=str),
        # Masked as insurance only: the API key travels on the client, not in
        # these kwargs. Cheap here, and this is the one part a provider-
        # specific extra kwarg could ever surface a credential through.
        "config": _redact(json.dumps(config, ensure_ascii=False, sort_keys=True, default=str)),
    }


def _redact(text: str) -> str:
    from deeptutor.core.observability.redaction import redact_text

    return redact_text(text)


def _schema_name(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""
    function = schema.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(schema.get("name") or "")


def _message_skeleton(message: dict[str, Any]) -> dict[str, Any]:
    """One message's shape — role, size, and what kind of parts it carries.

    No bodies: the point is to be able to say "round 3 carried a 40k-character
    tool result" without copying the tool result.
    """
    content = message.get("content")
    chars = 0
    has_image = False
    if isinstance(content, str):
        chars = len(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                chars += len(str(part.get("text") or ""))
            else:
                has_image = True
    return {
        "role": str(message.get("role") or ""),
        "chars": chars,
        "has_image": has_image,
        "has_tool_calls": bool(message.get("tool_calls")),
    }


def _token_counter():
    # Imported lazily for the same reason context_budget does it: the context
    # builder pulls in the agent base classes.
    try:
        from deeptutor.services.session.context_builder import count_tokens

        return count_tokens
    except Exception:  # pragma: no cover - fall back to a size proxy
        return lambda text: max(1, len(text) // 4)


def _count_user_turns(context: UnifiedContext) -> int:
    """Zero-based index of this turn within its session.

    One user turn is one :class:`AgentLoop`, so the turn's own index is the
    number of user messages already in the history it was built from. Read off
    the context rather than kept in a counter: the loop is constructed fresh
    per turn and has nowhere durable to count.
    """
    try:
        history = context.conversation_history or []
        return sum(1 for item in history if (item or {}).get("role") == "user")
    except Exception:  # pragma: no cover - an index is informational
        return 0


def _last_context_checkpoint_summary(dispatch: DispatchOutcome) -> str:
    summary = ""
    for tool_message in dispatch.tool_messages:
        tool_call_id = str(tool_message.get("tool_call_id") or "")
        metadata = dispatch.tool_metadata_by_id.get(tool_call_id) or {}
        checkpoint = metadata.get("_context_checkpoint")
        if not isinstance(checkpoint, dict):
            continue
        candidate = str(checkpoint.get("summary") or "").strip()
        if candidate:
            summary = candidate
    return summary


__all__ = [
    "AgentLoop",
    "AgentLoopState",
    "InlineThinkFilter",
    "LLMCallResult",
    "LOOP_STAGE",
    "LoopOutcome",
]
