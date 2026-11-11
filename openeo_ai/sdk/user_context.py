# ABOUTME: Per-request authenticated user id for in-process tool handlers.
# ABOUTME: A ContextVar holding a MUTABLE per-session ref so handlers read the
# ABOUTME: current turn's user even on a reused SDK client.

"""Tenant context for in-process tools.

The Claude SDK bridge is a process-wide singleton; its tool handlers are built
once and cached, and the SDK dispatches them from a long-lived reader task
spawned at ``client.connect()``. `anyio`/`asyncio` freeze the context at task
spawn time, so a plain ContextVar holding the user *value* set per turn would
NOT reach handlers on a reused client — they would see the connect-time user.

Fix: the ContextVar holds a MUTABLE ref ``{"value": user_id}``. The bridge binds
the per-session ref into the reader task's context at connect and updates
``ref["value"]`` at the start of every turn. The reader task's frozen context
still points at the same ref object, so handlers read the CURRENT turn's user —
even after the connect-time freeze, and even if a client is reused across users.
"""

import contextvars
from typing import Any, Dict, Optional

_current_user_ref: contextvars.ContextVar = contextvars.ContextVar(
    "jonaai_current_user_ref", default=None
)


def bind_user_ref(ref: Dict[str, Any]) -> "contextvars.Token":
    """Bind a per-session mutable ref ({'value': user_id}) into the context.

    Called by the bridge BEFORE client.connect() so the reader task freezes this
    ref; the bridge then mutates ref['value'] each turn.
    """
    return _current_user_ref.set(ref)


def set_current_user_id(user_id: Optional[str]) -> "contextvars.Token":
    """Bind a fresh ref holding user_id. For direct/test call sites that invoke
    handlers in the same task (no SDK reader task in the way)."""
    return _current_user_ref.set({"value": user_id})


def get_current_user_id() -> Optional[str]:
    """The authenticated user id for the current turn, or None if unset."""
    ref = _current_user_ref.get()
    return ref.get("value") if ref else None
