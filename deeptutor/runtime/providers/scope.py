"""What a turn needs to know to decide which provider tools it may use.

Deliberately a plain input record, not a protocol every provider must
implement: MCP and CLI apps differ in what their grants are keyed by (tool
names vs. app ids), in what "not ready" means, and in whether preloading is
even meaningful. Forcing one policy interface over both would buy a shared
type and pay for it with per-kind branches hidden inside it. The branches
live in :mod:`deeptutor.runtime.providers.authorize`, named and readable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolScope:
    """Identity + per-turn policy inputs for external-provider tools."""

    #: Id of the **owning account** — a partner resolves to the person who owns
    #: it, and an administrator to the deployment id. Deliberately not "the
    #: current user id": owner-keyed state (a caller's own MCP servers, their
    #: secrets) is addressed by this, and reading it under one identity while
    #: writing it under another is how one account's servers become invisible to
    #: itself. Comes from ``multi_user.paths.current_owner_id``.
    owner_id: str = ""
    #: A partner is a synthetic non-admin user anchored to an owner's
    #: workspace; its own configured filter is the authority for its surface,
    #: not the (absent) per-user grant.
    is_partner: bool = False
    session_id: str = ""
    #: The server-routed capability name for this turn. Provider policy must
    #: never infer this from model arguments.
    active_capability: str = "chat"
    #: The caller's own configured whitelist (a partner's ``mcp_tools``).
    #: ``None`` = the caller imposes no restriction.
    caller_whitelist: frozenset[str] | None = None
    #: Providers authorised by holding a *resource* rather than by a grant:
    #: attaching a PageIndex knowledge base authorises that server. Given as
    #: provider ids because the turn knows which resources it holds, not which
    #: tool names they expand to — the view resolves that against the live
    #: pool.
    implicit_provider_ids: frozenset[str] = frozenset()
    #: An exclusive knowledge capability owns the turn and replaces the tool
    #: surface, so provider tools must not be advertised (see ``authorize``).
    exclusive_capability: bool = False
    #: Provider tools this capability *drives* rather than discovers, given as
    #: server-side (unwrapped) names. They enter the turn with their schemas
    #: already in the window instead of behind a ``load_tools`` round-trip.
    #:
    #: Why this is a scope field and not a rule inside the view: which tools a
    #: capability leads with is the capability's own knowledge, and the view
    #: must not grow a table of third-party tool names to keep that knowledge
    #: in. Empty for every other caller, so nothing else changes shape.
    preload_original_names: frozenset[str] = frozenset()
    #: Same, by name prefix — for a provider whose tool set grows on its own
    #: side. A prefix rather than an enumeration so that adding a tool over
    #: there does not silently become "present but unreachable" over here.
    preload_original_prefixes: frozenset[str] = frozenset()


__all__ = ["ToolScope"]
