# ABOUTME: Declares every persisted status lifecycle (states and allowed moves) in one place,
# ABOUTME: so writers check moves against data instead of scattering string literals.
"""Status lifecycles for durable records.

Each `Lifecycle` lists its states and which moves are allowed. Staying in the same state is
always allowed, because retries and stale reclaims rewrite the current status.

Writers call `check(current, target)`, which raises `IllegalTransition` naming the allowed
next states. SQL writers that can race use `sources(target)` in a `WHERE status IN (...)`
clause, so the move only applies if the row is still in a state that permits it.

`USER_SETTABLE` lists the statuses an API caller may request. The rest are written only by
the worker or executor that owns the record.
"""

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, Mapping, Optional, Tuple


class IllegalTransition(ValueError):
    """A status move the lifecycle doesn't allow. Routes answer it with 409."""

    def __init__(self, lifecycle: "Lifecycle", current: str, target: str,
                 allowed: Optional[Iterable[str]] = None):
        self.lifecycle = lifecycle
        self.current = current
        self.target = target
        # What the mover may do from here: the lifecycle's moves, or fewer for an API caller.
        self.allowed = sorted(lifecycle.next_states(current) if allowed is None else allowed)
        none = "nothing, it is final" if allowed is None else "nothing an API caller may request"
        super().__init__(
            f"{lifecycle.name} can't move from '{current}' to '{target}'. "
            f"Allowed from '{current}': {', '.join(self.allowed) if self.allowed else none}."
        )


@dataclass(frozen=True)
class Lifecycle:
    name: str
    moves: Mapping[str, FrozenSet[str]]

    @property
    def states(self) -> FrozenSet[str]:
        return frozenset(self.moves)

    @property
    def terminal(self) -> FrozenSet[str]:
        return frozenset(state for state, targets in self.moves.items() if not targets)

    def next_states(self, current: str) -> FrozenSet[str]:
        return self.moves.get(current, frozenset())

    def can(self, current: Optional[str], target: str) -> bool:
        if target not in self.moves:
            return False
        if current is None or current == target:
            return True
        return target in self.next_states(current)

    def check(self, current: Optional[str], target: str) -> None:
        if target not in self.moves:
            raise IllegalTransition(self, str(current), target)
        if not self.can(current, target):
            raise IllegalTransition(self, str(current), target)

    def sources(self, target: str) -> Tuple[str, ...]:
        """States a record may be in for a move to `target`, including `target` itself."""
        return tuple(sorted(s for s in self.moves if s == target or target in self.moves[s]))


def _lifecycle(name: str, moves: Dict[str, Iterable[str]]) -> Lifecycle:
    return Lifecycle(name, {state: frozenset(targets) for state, targets in moves.items()})


# One run of a watch. The worker retries or defers by going back to queued.
OBSERVATION = _lifecycle("observation", {
    "queued": {"running", "cancelled"},
    "running": {"completed", "failed", "queued"},
    "failed": {"queued", "running"},
    "cancelled": {"queued"},
    "completed": set(),
})

SIGNAL = _lifecycle("signal", {
    "open": {"acknowledged", "resolved"},
    "acknowledged": {"open", "resolved"},
    "resolved": {"open", "acknowledged"},
})

# A notification for a signal. Failed ones can be queued again by hand.
DELIVERY = _lifecycle("delivery", {
    "pending": {"sending"},
    "sending": {"sent", "pending", "failed"},
    "failed": {"pending"},
    "sent": set(),
})

WATCH = _lifecycle("watch", {
    "active": {"paused", "archived"},
    "paused": {"active", "archived"},
    "archived": {"active"},
})

# Pending or queued work may settle directly; what matters is that settled states stay put.
BATCH_ITEM = _lifecycle("batch item", {
    "pending": {"queued", "running", "completed", "failed", "cancelled"},
    "queued": {"running", "completed", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
})

BATCH_JOB = _lifecycle("batch", {
    "pending": {"queued", "running", "completed", "failed", "partially_completed", "cancelled"},
    "queued": {"running", "completed", "failed", "partially_completed", "cancelled"},
    "running": {"completed", "failed", "partially_completed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "partially_completed": set(),
    "cancelled": set(),
})

SCHEDULE = _lifecycle("schedule", {
    "active": {"paused", "cancelled", "expired"},
    "paused": {"active", "cancelled"},
    "cancelled": set(),
    "expired": set(),
})

# The offsite copy of a saved result. A copy that failed or was refused for space is tried
# again later, and a synced one goes back to pending when the result's files change.
RESULT_COPY = _lifecycle("result copy", {
    "pending": {"synced", "failed", "refused"},
    "failed": {"pending", "synced", "refused"},
    "refused": {"pending", "synced", "failed"},
    "synced": {"pending"},
})

ALL = (OBSERVATION, SIGNAL, DELIVERY, WATCH, BATCH_ITEM, BATCH_JOB, SCHEDULE, RESULT_COPY)

# What an API caller may ask for. Everything else is the owning worker's to write.
USER_SETTABLE: Dict[str, FrozenSet[str]] = {
    OBSERVATION.name: frozenset({"queued", "cancelled"}),
    SIGNAL.name: SIGNAL.states,
    DELIVERY.name: frozenset({"pending"}),
    WATCH.name: WATCH.states,
}

# Where an API caller may make those moves from. A record a worker holds (a running observation,
# a delivery being sent) is the worker's: re-queueing it ran it twice. The worker's own retries
# (running->queued, sending->pending) are still allowed for the worker, which doesn't check this.
USER_SOURCES: Dict[str, Dict[str, FrozenSet[str]]] = {
    OBSERVATION.name: {"queued": frozenset({"failed", "cancelled"})},
    DELIVERY.name: {"pending": frozenset({"failed"})},
}


def check_user_move(lifecycle: Lifecycle, current: Optional[str], target: str) -> None:
    """Check a move an API caller asked for: the lifecycle allows it, and from where it is."""
    lifecycle.check(current, target)
    sources = USER_SOURCES.get(lifecycle.name, {})
    if target in sources and current not in sources[target]:
        allowed = [t for t in lifecycle.next_states(current)
                   if t in USER_SETTABLE.get(lifecycle.name, lifecycle.states)
                   and (t not in sources or current in sources[t])]
        raise IllegalTransition(lifecycle, str(current), target, allowed=allowed)


# OpenEO job statuses use different words (`finished`, `error`, `canceled`) from this app's
# records. Consumers map them through here rather than listing spellings themselves.
OPENEO_FINISHED = frozenset({"finished", "completed"})
OPENEO_FAILED = frozenset({"error", "failed"})
OPENEO_CANCELLED = frozenset({"canceled", "cancelled"})


def openeo_outcome(status: Optional[str]) -> str:
    """Return finished, failed, cancelled, or pending for any OpenEO job status."""
    value = (status or "").strip().lower()
    if value in OPENEO_FINISHED:
        return "finished"
    if value in OPENEO_FAILED:
        return "failed"
    if value in OPENEO_CANCELLED:
        return "cancelled"
    return "pending"
