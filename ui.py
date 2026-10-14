"""Terminal output for the agent: spinner, tool lines, diffs, answers, checklists.

Everything the model wrote is printed as rich Text objects, never as markup, so
a stray "[bold]" in a file or an answer is shown as-is instead of being parsed.
"""

import difflib
from contextlib import nullcontext

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

console = Console(highlight=False)


def _who(label: str) -> str:
    """Prefix for subagent lines, so parallel output stays readable."""
    return "" if label == "main" else f"[{label}] "


def _shorten(text: str, limit: int) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------
# Status lines
# ------------------------------------------------------------

def thinking(label: str = "main", text: str = "Thinking..."):
    # rich allows one live spinner at a time, so subagents (threads) go without.
    if label != "main":
        return nullcontext()
    return console.status(text, spinner="dots")


def info(message: str) -> None:
    console.print(Text(message, style="cyan"))


def warn(message: str) -> None:
    console.print(Text(message, style="yellow"))


def error(message: str) -> None:
    console.print(Text(message, style="bold red"))


# ------------------------------------------------------------
# Tool calls
# ------------------------------------------------------------

# The one argument worth showing for each tool -- dumping every argument as
# JSON would print whole files for file_write.
_KEY_ARG = {
    "file_read": "path", "file_write": "path", "file_edit": "path",
    "view_image": "path", "file_search": "pattern",
    "run_command": "command", "start_background": "command",
    "verify_task": "id", "record_decision": "decision",
    "browser_open": "url", "browser_click": "selector", "browser_type": "selector",
}


def tool_call(name: str, args: dict, label: str = "main") -> None:
    key = _KEY_ARG.get(name)

    if key:
        detail = str(args.get(key, ""))
    elif name == "delegate":
        detail = ", ".join(str(t.get("name", "?")) for t in args.get("tasks", [])
                           if isinstance(t, dict))
    elif name == "plan_tasks":
        detail = f"{len(args.get('tasks') or [])} tasks"
    else:
        detail = ", ".join(f"{k}={v}" for k, v in args.items())

    line = Text(f"  {_who(label)}")
    line.append("▸ ", style="cyan")
    line.append(name, style="bold cyan")
    line.append(f" {_shorten(detail, 100)}", style="cyan")
    console.print(line)


def tool_result(text: str, label: str = "main") -> None:
    lines = text.strip().splitlines() or [""]
    more = f"  (+{len(lines) - 1} lines)" if len(lines) > 1 else ""
    console.print(Text(f"    {_who(label)}{_shorten(lines[0], 110)}{more}", style="dim"))


def diff(old: str, new: str, max_lines: int = 40) -> None:
    """Show what a file_edit changed, red for removed and green for added."""
    out = Text()
    shown = 0

    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1):
        if line.startswith(("---", "+++", "@@")):
            continue
        if shown == max_lines:
            out.append("      ...\n", style="dim")
            break
        style = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
        out.append(f"      {line}\n", style=style)
        shown += 1

    console.print(out, end="")


# ------------------------------------------------------------
# Tasks
# ------------------------------------------------------------

_MARK = {"passed": ("✔", "green"), "failed": ("✘", "red"), "pending": ("○", "dim")}


def checklist(tasks, label: str = "main") -> None:
    out = Text()
    for task in tasks.tasks:
        mark, style = _MARK[task["status"]]
        out.append(f"    {_who(label)}{mark} {task['id']}  {task['description']}\n", style=style)
    console.print(out, end="")


def unverified(problem: str) -> None:
    console.print(Panel(Text(problem), title="UNVERIFIED", title_align="left",
                        border_style="yellow"))


# ------------------------------------------------------------
# Answers and token counts
# ------------------------------------------------------------

def answer(text: str) -> None:
    console.print()
    console.print(Panel(Markdown(text or "_(no answer)_"), title="agent",
                        title_align="left", border_style="green"))


def call_tokens(inp: int, cached: int, out: int, label: str = "main",
                context_pct: float | None = None) -> None:
    line = f"    {_who(label)}[tokens] {inp:,} in ({cached:,} cached) / {out:,} out"
    if context_pct is not None:
        line += f" · context {context_pct:.0%}"
    console.print(Text(line, style="dim"))


def turn_summary(turn: dict, session: dict) -> None:
    console.print(Rule(style="dim"))
    for name, t in (("turn", turn), ("session", session)):
        console.print(Text(
            f"tokens {name:<8} {t['input']:,} in ({t['cached']:,} cached) / "
            f"{t['output']:,} out  ({t['calls']} API calls)", style="dim"))


def banner(lines: list[str]) -> None:
    console.print(Panel(Text("\n".join(lines)), title="harnest", title_align="left",
                        border_style="cyan"))


def session_list(rows: list[dict]) -> None:
    if not rows:
        info("No saved sessions for this project yet.")
        return
    out = Text()
    for row in rows:
        out.append(f"  {row['key']:<28}", style="bold")
        out.append(f" {row['updated']:<20} {row['messages']:>4} messages\n", style="dim")
    console.print(out, end="")
