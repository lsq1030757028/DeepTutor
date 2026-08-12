"""Authenticated, owner-bound read surface for Test Journey pages.

The browser receives two explicit read operations, never a generic MCP tool
executor. The current owner is derived from the authenticated request context
and signed into the shared test-partner call immediately before dispatch.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from deeptutor.multi_user.paths import current_owner_id
from deeptutor.runtime.registry.tool_registry import get_tool_registry
from deeptutor.services.mcp import get_mcp_manager
from deeptutor.services.mcp.manager import wrapped_tool_name
from deeptutor.services.test_journey.trust import (
    TrustedJourneyContext,
    bind_trusted_journey_context,
)

router = APIRouter()


class TraceOpenRequest(BaseModel):
    """The only browser-side Journey action: open one verified trace handle."""

    model_config = ConfigDict(extra="forbid")

    trace_rel: str = Field(min_length=1, max_length=512)


def _error(code: str, message: str, *, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "code": code, "message": message},
    )


async def _read(tool_name: str, **arguments: Any) -> JSONResponse | dict[str, Any]:
    await get_mcp_manager().ensure_started()
    tool = get_tool_registry().get(
        wrapped_tool_name("test-partner", f"journey_{tool_name}")
    )
    if tool is None:
        return _error(
            "E_MCP_UNAVAILABLE",
            "The test-partner Journey service is not connected.",
            status=503,
        )

    request_id = uuid4().hex
    trusted = TrustedJourneyContext(
        owner_id=current_owner_id(),
        session_id=f"workbench:{request_id}",
        turn_id=request_id,
        capability="test",
        surface="workbench",
    )
    with bind_trusted_journey_context(trusted):
        result = await tool.execute(**arguments)
    if not result.success:
        return _error(
            "E_MCP_UNAVAILABLE",
            str(result.content or "Journey service refused the request")[:200],
            status=503,
        )
    try:
        body = json.loads(str(result.content))
    except (TypeError, ValueError):
        return _error(
            "E_MCP_UNAVAILABLE",
            "The Journey service returned invalid JSON.",
            status=502,
        )
    if not isinstance(body, dict):
        return _error(
            "E_MCP_UNAVAILABLE",
            "The Journey service returned an invalid payload.",
            status=502,
        )
    return body


@router.get("/batches")
async def list_batches() -> Any:
    return await _read("list_batches")


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str) -> Any:
    return await _read("get_batch", batch_id=batch_id)


@router.post("/batches/{batch_id}/runs/{run_id}/trace")
async def open_trace(batch_id: str, run_id: str, request: TraceOpenRequest) -> Any:
    return await _read(
        "open_trace",
        batch_id=batch_id,
        run_id=run_id,
        trace_rel=request.trace_rel,
    )


__all__ = ["router"]
