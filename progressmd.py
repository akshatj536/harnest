"""Persistent task state for the agent, stored in PROGRESS.md.

PROGRESS.md is STATE, not instructions: it records what has been done and what
is left, so work survives across sessions. Project conventions stay in CLAUDE.md.
"""

from pathlib import Path


# Relative on purpose: agent.py chdirs into the project, so each project
# being worked on keeps its own PROGRESS.md.
PROGRESS_FILE = Path("PROGRESS.md")


TEMPLATE = """# Current Task
(none yet)

# Plan
- [ ] (not planned yet)

# Current Step
(not started)

# Important Discoveries

# Decisions

# Blocked
"""


def read_progress() -> str:
    """Return the contents of PROGRESS.md, creating it from the template if missing."""

    if not PROGRESS_FILE.exists():
        PROGRESS_FILE.write_text(TEMPLATE, encoding="utf-8")
        return f"[Created {PROGRESS_FILE} from the empty template]\n\n{TEMPLATE}"

    return PROGRESS_FILE.read_text(encoding="utf-8")


def update_progress(content: str) -> str:
    """Overwrite PROGRESS.md with the full updated document."""

    PROGRESS_FILE.write_text(content, encoding="utf-8")
    return f"[Updated {PROGRESS_FILE}, {len(content)} characters]"
