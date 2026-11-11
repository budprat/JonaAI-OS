# ABOUTME: Collects facts about how a job's data was loaded so they travel with its result
# ABOUTME: instead of ending up only in the server log.
"""Per-job notes, such as how many of the available scenes a job actually used."""

import contextvars
from typing import List, Optional

_notes: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar("job_notes", default=None)


def start_job_notes() -> List[str]:
    """Begin collecting for the current context and return the list being filled."""
    notes: List[str] = []
    _notes.set(notes)
    return notes


def add_job_note(note: str) -> None:
    """Record a note on the current job. Outside a job this does nothing."""
    notes = _notes.get()
    if notes is not None and note not in notes:
        notes.append(note)
