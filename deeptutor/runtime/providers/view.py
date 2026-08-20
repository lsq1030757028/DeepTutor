"""Assemble one turn's external-provider tool surface.

This is the composition seam that used to live inline in the chat pipeline:
start the providers, work out what this caller may use, filter the pool,
build the progressive-disclosure loader, and render the manifest block. It
lives here so the policy is testable on its own and so a second consumer (or
a second provider kind) does not mean a second copy inside a pipeline.

Contract: :func:`build_tool_view` **never raises**. A provider that is down,
misconfigured, or slow degrades to "no external tools this turn" — the turn
itself must still run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import time
from typing import Any

from deeptutor.core.tool_protocol import BaseTool, ToolLookup
from deeptutor.runtime.providers.allowlist import Allowlist
from deeptutor.runtime.providers.authorize import authorize_mcp_tools
from deeptutor.runtime.providers.scope import ToolScope
from deeptutor.runtime.registry.deferred_tools import (
    DeferredToolLoader,
    provider_identity,
    render_deferred_tools_manifest,
)
from deeptutor.runtime.registry.scoped_registry import ScopedToolRegistry

logger = logging.getLogger(__name__)

#: Ceiling on connecting a caller's own servers. This runs before the turn's
#: first stream event, so it is the one place a slow third-party host could
#: present as "DeepTutor hung".
_OWNER_SCOPE_TIMEOUT_S = 3.0


#: Assembly stages that can degrade — one per ``return []`` site in this
#: module, plus the pipeline's defence-in-depth fallback. Named values rather
#: than free text so "which channel dropped out" is answerable mechanically.
STAGE_OWNER_SCOPE = "owner_scope"
STAGE_CLI_APPS = "cli_apps"
STAGE_TOOL_VIEW = "tool_view"

REASON_TIMEOUT = "timeout"
REASON_ERROR = "error"


@dataclass(frozen=True)
class Degradation:
    """One assembly stage that produced nothing, and why.

    This is what the health field exists for: the four ``return []`` sites
    below each hand the turn a smaller tool surface than was asked for, and
    until now they said so only to a log line nobody reads mid-incident.
    """

    stage: str
    reason: str
    detail: str = ""
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "reason": self.reason,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class ToolViewHealth:
    """What the assembly of one turn's tool surface actually managed to do."""

    degradations: tuple[Degradation, ...] = ()

    @property
    def state(self) -> str:
        return "degraded" if self.degradations else "ok"

    @property
    def degraded(self) -> bool:
        return bool(self.degradations)

    def with_degradation(self, degradation: Degradation) -> "ToolViewHealth":
        return ToolViewHealth(degradations=(*self.degradations, degradation))

    def merged(self, other: "ToolViewHealth") -> "ToolViewHealth":
        return ToolViewHealth(degradations=(*self.degradations, *other.degradations))

    def as_dicts(self) -> list[dict[str, Any]]:
        return [degradation.as_dict() for degradation in self.degradations]


@dataclass(frozen=True)
class ProviderToolView:
    """One turn's resolved provider surface."""

    #: Registry the turn should use for lookup and dispatch (scoped view).
    registry: ToolLookup
    #: ``None`` when this turn has no external tools at all.
    loader: DeferredToolLoader | None
    #: Provider tools visible this turn — the manifest's contents, and what
    #: the context-budget chip counts as "unloaded extended tools".
    pool: tuple[BaseTool, ...]
    #: Rendered ``extended_tools`` system-prompt block ("" when suppressed).
    manifest: str
    #: Which assembly stages, if any, degraded while building this view.
    #: Defaults to healthy, so every existing construction site stays valid.
    health: ToolViewHealth = field(default=ToolViewHealth())

    @classmethod
    def empty(
        cls,
        registry: ToolLookup,
        health: ToolViewHealth | None = None,
    ) -> "ProviderToolView":
        return cls(
            registry=registry,
            loader=None,
            pool=(),
            manifest="",
            health=health or ToolViewHealth(),
        )

    def attach(self, tool_schemas: list[dict[str, Any]]) -> None:
        """Add already-loaded schemas and bind the turn's live schema list.

        The loader appends to this list as the model calls ``load_tools``, and
        the agent loop re-reads it every round, so tools become callable
        without rebuilding the request.
        """
        if self.loader is None:
            return
        tool_schemas.extend(self.loader.initial_schemas())
        self.loader.bind_live_schemas(tool_schemas)


async def build_tool_view(
    *,
    base_registry: ToolLookup,
    scope: ToolScope,
    language: str = "en",
    refusal_message: str = "",
) -> ProviderToolView:
    """Resolve the provider tools *scope* may use this turn."""
    try:
        return await _build(
            base_registry=base_registry,
            scope=scope,
            language=language,
            refusal_message=refusal_message,
        )
    except Exception as exc:
        logger.warning("provider tool view assembly failed; continuing without", exc_info=True)
        return ProviderToolView.empty(
            base_registry,
            ToolViewHealth().with_degradation(
                Degradation(
                    stage=STAGE_TOOL_VIEW,
                    reason=REASON_ERROR,
                    detail=_detail(exc),
                )
            ),
        )


async def _build(
    *,
    base_registry: ToolLookup,
    scope: ToolScope,
    language: str,
    refusal_message: str,
) -> ProviderToolView:
    from deeptutor.services.mcp import get_mcp_manager, load_loaded_tools

    manager = get_mcp_manager()
    await manager.ensure_started()

    shared_pool = list(base_registry.deferred_tools())
    owned_pool, owned_health = await _owned_tools(manager, scope)
    cli_pool, cli_health = _cli_app_tools(scope)
    health = owned_health.merged(cli_health)
    if not shared_pool and not owned_pool and not cli_pool:
        return ProviderToolView.empty(base_registry, health)

    # Resolve resource-derived authorisation (e.g. an attached PageIndex KB
    # authorises that server) from provider ids to concrete tool names.
    implicit_names = {
        tool.get_definition().name
        for tool in shared_pool
        if provider_identity(tool)[1] in scope.implicit_provider_ids
    }

    allowed = authorize_mcp_tools(
        scope=scope,
        user_grant=_user_grant(scope),
        implicit_names=implicit_names,
        # Servers the caller configured themselves are authorised by ownership:
        # the admin grant governs the deployment's shared servers, and applying
        # it here would make self-service configuration silently useless.
        owned_names=[tool.get_definition().name for tool in owned_pool],
    )
    # CLI apps are authorised by *app id*, not by tool name, and by a different
    # grant field — so their decision is made in ``cli_apps.provider`` and the
    # names it approved are widened in here. Widening an unrestricted allowlist
    # is identity, so an administrator is unaffected.
    allowed = allowed.widen(tool.get_definition().name for tool in cli_pool)

    # Journey tools are a capability boundary, not ordinary administrator-
    # granted plugins. Plain chat/Playground must neither advertise nor dispatch
    # them, even for an otherwise unrestricted administrator.
    journey_names = {
        tool.get_definition().name
        for tool in (*shared_pool, *owned_pool)
        if _is_test_journey_tool(tool)
    }
    if journey_names:
        visible_names = {
            tool.get_definition().name
            for tool in (*shared_pool, *owned_pool, *cli_pool)
            if allowed.allows(tool.get_definition().name)
        }
        if scope.active_capability == "test":
            allowed = Allowlist.of(visible_names)
        else:
            allowed = Allowlist.of(visible_names - journey_names)

    pool = tuple(
        tool
        for tool in (*shared_pool, *owned_pool, *cli_pool)
        if allowed.allows(tool.get_definition().name)
    )
    registry = ScopedToolRegistry(
        base=base_registry,
        # Owner-scoped tools live only in this turn's overlay — they are never
        # published to the process registry, so two accounts whose servers share
        # a name cannot resolve to each other's session.
        overlay=[*owned_pool, *cli_pool],
        allowed=allowed,
        refusal_message=refusal_message,
    )
    if not pool:
        # Nothing authorised: no manifest, no loader — but keep the scoped
        # registry so dispatch still refuses names the model may invent.
        return ProviderToolView(registry=registry, loader=None, pool=(), manifest="", health=health)

    loader = DeferredToolLoader(
        registry=registry,
        session_id=scope.session_id,
        # Resource-authorised tools are preloaded: holding the resource is the
        # permission, so the model should not have to spend a `load_tools`
        # round-trip before its first retrieval.
        loaded=load_loaded_tools(scope.session_id) | implicit_names,
        allowed=allowed.as_set(),
    )
    manifest = (
        ""
        if scope.exclusive_capability
        else render_deferred_tools_manifest(list(pool), language=language)
    )
    return ProviderToolView(
        registry=registry, loader=loader, pool=pool, manifest=manifest, health=health
    )


def _is_test_journey_tool(tool: BaseTool) -> bool:
    try:
        from deeptutor.services.test_journey.trust import is_test_journey_tool

        return is_test_journey_tool(tool)
    except Exception:
        return False


async def _owned_tools(
    manager: Any,
    scope: ToolScope,
) -> tuple[list[BaseTool], ToolViewHealth]:
    """Tools from servers this caller configured for themselves.

    Bounded by a per-scope timeout: connecting somebody's own remote server is a
    network round-trip on the turn's critical path, before a single stream event
    has been emitted. A slow server must cost this turn its own tools, not the
    whole turn's first token.
    """
    if scope.is_partner or not scope.owner_id:
        # A partner has no account of its own, so it has no self-configured
        # servers; its surface is the deployment's, filtered by its own list.
        # Not a degradation: nothing here was supposed to connect.
        return [], ToolViewHealth()
    started = time.monotonic()
    try:
        connected = list(
            await asyncio.wait_for(
                manager.ensure_scope(scope.owner_id),
                timeout=_OWNER_SCOPE_TIMEOUT_S,
            )
        )
        return connected, ToolViewHealth()
    except asyncio.TimeoutError:
        logger.warning(
            "per-user MCP scope for %s did not connect in %ss; continuing without",
            scope.owner_id,
            _OWNER_SCOPE_TIMEOUT_S,
        )
        return [], ToolViewHealth().with_degradation(
            Degradation(
                stage=STAGE_OWNER_SCOPE,
                reason=REASON_TIMEOUT,
                # The budget that was missed, not a verdict: a scope that did
                # not answer within 3s was slow here, which is not the same
                # claim as "that server is down".
                detail=f"{_OWNER_SCOPE_TIMEOUT_S}s",
                elapsed_ms=_elapsed_ms(started),
            )
        )
    except Exception as exc:
        logger.warning("per-user MCP scope for %s failed", scope.owner_id, exc_info=True)
        return [], ToolViewHealth().with_degradation(
            Degradation(
                stage=STAGE_OWNER_SCOPE,
                reason=REASON_ERROR,
                detail=_detail(exc),
                elapsed_ms=_elapsed_ms(started),
            )
        )


def _cli_app_tools(scope: ToolScope) -> tuple[list[BaseTool], ToolViewHealth]:
    """Installed CLI apps this caller may invoke, as tools.

    Synchronous and cheap on purpose — it reads two small JSON files. Unlike the
    MCP side there is nothing to connect, so there is no timeout to bound and no
    reason for this to be on the turn's critical path as an awaited task.

    An exclusive knowledge capability replaces the tool surface entirely, so it
    gets none of these (the same rule ``authorize_mcp_tools`` applies to MCP).
    """
    if scope.exclusive_capability:
        # Deliberate policy, not a failure: an exclusive capability replaces
        # the tool surface, so there is nothing here to report as degraded.
        return [], ToolViewHealth()
    started = time.monotonic()
    try:
        from deeptutor.multi_user.tool_access import allowed_cli_apps, exec_override
        from deeptutor.services.cli_apps.provider import authorized_apps, build_app_tools

        access = authorized_apps(
            owner_id=scope.owner_id,
            is_partner=scope.is_partner,
            granted=allowed_cli_apps(),
            exec_allowed=exec_override(),
        )
        return build_app_tools(access.apps), ToolViewHealth()
    except Exception as exc:
        logger.warning("CLI app tools unavailable this turn; continuing", exc_info=True)
        return [], ToolViewHealth().with_degradation(
            Degradation(
                stage=STAGE_CLI_APPS,
                reason=REASON_ERROR,
                detail=_detail(exc),
                elapsed_ms=_elapsed_ms(started),
            )
        )


def _user_grant(scope: ToolScope) -> Allowlist:
    """The caller's ``grant.mcp_tools``, or unrestricted for a partner turn.

    A partner has no account and therefore no grant; its surface is decided by
    the partner's own configured whitelist, which reaches us as
    ``scope.caller_whitelist``.
    """
    if scope.is_partner:
        return Allowlist.unrestricted()
    from deeptutor.multi_user.tool_access import allowed_mcp_tools

    return Allowlist.of(allowed_mcp_tools())


def owner_scope_timeout_s() -> float:
    """The per-scope connect ceiling, read at call time.

    A function rather than a re-export so that reporting the value and
    enforcing it cannot drift apart — including under a test that patches the
    module constant.
    """
    return _OWNER_SCOPE_TIMEOUT_S


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _detail(exc: BaseException) -> str:
    """Short, masked description of a failure, safe to put on the wire.

    Imported lazily: ``core.observability`` must not become a load-time
    dependency of the provider layer.
    """
    from deeptutor.core.observability.redaction import redact_text

    text = str(exc).strip()
    described = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return redact_text(described, max_chars=200)


__all__ = [
    "Degradation",
    "ProviderToolView",
    "REASON_ERROR",
    "REASON_TIMEOUT",
    "STAGE_CLI_APPS",
    "STAGE_OWNER_SCOPE",
    "STAGE_TOOL_VIEW",
    "ToolViewHealth",
    "build_tool_view",
    "owner_scope_timeout_s",
]
