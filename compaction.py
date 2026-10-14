"""Context compaction and DECISIONS.md.

When the context fills up, the conversation is replaced by a summary. Summaries
keep the WHAT but tend to lose the WHY, so before the history is thrown away the
same summariser call also extracts every decision and its reason, and the harness
appends them to DECISIONS.md. The reasons then survive any number of compactions.
"""

import threading
from datetime import datetime
from pathlib import Path


# Relative on purpose, like PROGRESS.md: each project keeps its own file.
DECISIONS_FILE = Path("DECISIONS.md")

HEADER = """# Decisions

Why things were done the way they were. Appended by the agent and by the
harness on every context compaction -- newest last.
"""

_lock = threading.Lock()   # subagents may record decisions at the same time


# ============================================================
# DECISIONS.md
# ============================================================

def _append(block: str) -> None:
    with _lock:
        if not DECISIONS_FILE.exists():
            DECISIONS_FILE.write_text(HEADER, encoding="utf-8")
        with DECISIONS_FILE.open("a", encoding="utf-8") as f:
            f.write(block)


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def record_decision(decision: str, why: str, rejected: str = "") -> str:
    """Tool: log one decision and the reason for it."""
    entry = f"\n### {_stamp()}\n- **{decision.strip()}** -- Why: {why.strip()}"
    if rejected.strip():
        entry += f" Rejected: {rejected.strip()}"
    _append(entry + "\n")
    return f"[Recorded decision in {DECISIONS_FILE}]"


def read_decisions(limit: int = 8000) -> str:
    """The newest `limit` characters of DECISIONS.md."""
    if not DECISIONS_FILE.exists():
        return ""
    text = DECISIONS_FILE.read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    return f"[... older decisions are in {DECISIONS_FILE}]\n" + text[-limit:]


# ============================================================
# COMPACTION
# ============================================================

SUMMARISE_PROMPT = """
You are compacting the conversation of a coding agent so it can keep working with
a fresh context. The transcript and the current DECISIONS.md are below.

Reply with exactly these two sections and nothing else.

## DECISIONS
One bullet per decision in the transcript that is NOT already in DECISIONS.md --
choices of approach, library, design, trade-off, bug fix, and preferences or
constraints the user stated. Format:
- **<the decision>** -- Why: <the reason>. Rejected: <alternatives, or none>.
Write (none) if there are no new decisions.

## SUMMARY
- Goal: what the user asked for, in their terms.
- Done: what has been completed, with file paths and function names.
- State: what works, what fails, exact error messages still open.
- Next: the next concrete step.
Be specific. Prefer names, paths and commands over prose.
"""


def _text_of(content) -> str:
    """Message content as plain text; images become a placeholder."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if part.get("type") == "text":
            parts.append(part.get("text", ""))
        else:
            parts.append("[image]")
    return "\n".join(parts)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n[... {len(text) - limit} chars cut]"


def render_transcript(messages: list) -> str:
    """Flatten the history for the summariser. Long tool output is clipped --
    the summary needs what happened, not every line of every file read."""

    out = []
    for m in messages:
        role = m["role"]
        text = _text_of(m.get("content"))

        if role == "system":
            continue
        if role == "tool":
            out.append(f"TOOL RESULT:\n{_clip(text, 3000)}")
        elif role == "assistant":
            if text:
                out.append(f"ASSISTANT:\n{_clip(text, 6000)}")
            for call in m.get("tool_calls") or []:
                fn = call["function"]
                out.append(f"TOOL CALL: {fn['name']}({_clip(fn['arguments'] or '', 1500)})")
        else:
            out.append(f"USER:\n{_clip(text, 20000)}")

    return "\n\n".join(out)


def _split(reply: str) -> tuple[str, str]:
    """Split the summariser's reply into (decisions, summary)."""
    if "## SUMMARY" not in reply:
        return "", reply.strip()
    head, summary = reply.split("## SUMMARY", 1)
    decisions = head.replace("## DECISIONS", "").strip()
    if decisions.lower().strip("(). ") == "none":
        decisions = ""
    return decisions, summary.strip()


def compact(messages: list, complete, request: str | None = None,
            task_text: str = "", use_decisions: bool = True) -> str:
    """Replace `messages` (in place) with the system prompt plus a summary.

    `complete(messages) -> str` makes one plain model call.
    `request` is the user request still being worked on, if compacting mid-turn.
    Returns a one-line report for the user.
    """

    existing = read_decisions() if use_decisions else ""

    reply = complete([
        {"role": "system", "content": SUMMARISE_PROMPT},
        {"role": "user", "content":
            f"CURRENT DECISIONS.md:\n{existing or '(empty)'}\n\n"
            f"TRANSCRIPT:\n{render_transcript(messages)}"},
    ])

    decisions, summary = _split(reply)

    if use_decisions and decisions:
        _append(f"\n### {_stamp()} (compaction)\n{decisions}\n")

    body = ("[The earlier conversation was compacted to free context. "
            "This summary replaces it.]\n\n"
            f"SUMMARY OF EARLIER WORK:\n{summary}\n")

    if use_decisions:
        body += ("\nDECISIONS SO FAR (from DECISIONS.md -- the reasons behind earlier "
                 "choices; stay consistent with them unless the user changes course):\n"
                 f"{read_decisions() or '(none recorded)'}\n")

    if task_text:
        body += f"\nCURRENT TASK CHECKLIST:\n{task_text}\n"

    system = [m for m in messages[:1] if m["role"] == "system"]

    if request:
        # Mid-turn: end on a user message so the model just carries on working.
        body += f"\nCURRENT USER REQUEST (still in progress -- continue it):\n{request}\n"
        messages[:] = system + [{"role": "user", "content": body}]
    else:
        messages[:] = system + [
            {"role": "user", "content": body},
            {"role": "assistant", "content": "Understood. I have the summary and the decisions."},
        ]

    added = len([l for l in decisions.splitlines() if l.strip().startswith("-")])
    return f"Compacted. {added} new decision(s) saved to {DECISIONS_FILE}."
