# ABOUTME: Keeps CLAUDE.md and AGENTS.md identical apart from their titles, so every coding
# ABOUTME: agent reads the same guidance whichever file its tool opens.
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _body(name: str) -> list:
    lines = (REPO / name).read_text().splitlines()
    assert lines[0] == f"# {name}", f"{name} should start with its own title"
    return lines[1:]


def test_agent_guides_match_apart_from_their_titles():
    claude, agents = _body("CLAUDE.md"), _body("AGENTS.md")
    first_difference = next(
        (n for n, (a, b) in enumerate(zip(claude, agents), start=2) if a != b),
        None if len(claude) == len(agents) else min(len(claude), len(agents)) + 2,
    )
    assert first_difference is None, (
        f"CLAUDE.md and AGENTS.md differ from line {first_difference}; edit both the same way"
    )
