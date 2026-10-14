import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import readline  # noqa: F401 -- arrow keys and history at the >>> prompt
except ImportError:
    pass

import groq
import openai
from dotenv import load_dotenv
from groq import Groq
from openai import OpenAI

import compaction
import sessions
import ui
from progressmd import read_progress, update_progress
from tasks import TaskList


# The project to work on: first argument, or the current directory.
#   python agent.py ~/projects/myapp
# Headless: run one request from a file, write the outcome as JSON, and exit.
#   python agent.py ~/projects/myapp --prompt-file request.md --result-file result.json
_parser = argparse.ArgumentParser(description="harnest: a coding agent in your terminal")
_parser.add_argument("project", nargs="?", default=".",
                     help="project directory (default: current directory)")
_parser.add_argument("--prompt-file",
                     help="run this one request instead of the >>> prompt, then exit")
_parser.add_argument("--result-file",
                     help="with --prompt-file: write the answer, token usage and config as JSON")
ARGS = _parser.parse_args()

# Resolved before the chdir below, so relative paths mean the caller's directory.
PROMPT_FILE = Path(ARGS.prompt_file).expanduser().resolve() if ARGS.prompt_file else None
RESULT_FILE = Path(ARGS.result_file).expanduser().resolve() if ARGS.result_file else None

PROJECT_DIR = Path(ARGS.project).expanduser().resolve()

if not PROJECT_DIR.is_dir():
    raise SystemExit(f"Not a directory: {PROJECT_DIR}")
if PROMPT_FILE and not PROMPT_FILE.is_file():
    raise SystemExit(f"No such prompt file: {PROMPT_FILE}")

os.chdir(PROJECT_DIR)


# ============================================================
# CONFIGURATION
# ============================================================

# Which provider to use: "mistral" or "groq".
# Flip this one line to switch; everything else stays the same.
PROVIDER = "mistral"

MODELS = {
    "mistral": "ministral-14b-latest",   # 30 req/min, 262k context, vision + tools
    "groq": "openai/gpt-oss-120b",       # 8,000 tokens/min -- tight for a tool loop
}

MODEL = MODELS[PROVIDER]

# Context window in tokens. Compaction kicks in at COMPACT_AT of it.
CONTEXT_WINDOW = {"mistral": 262_144, "groq": 131_072}[PROVIDER]

# Can the model look at images? gpt-oss on Groq is text-only.
SUPPORTS_VISION = {"mistral": True, "groq": False}[PROVIDER]


# ============================================================
# HARNESS TOGGLES
# ============================================================

# Persistent task state:
# True  -> agent has access to PROGRESS.md
# False -> agent does not have access to PROGRESS.md
#
# Use this for your ablation experiment.
USE_PROGRESS = True

# DECISIONS.md: the WHY behind earlier choices, kept across compactions.
USE_DECISIONS = True

# Verification loop: the agent plans tasks with a verify command each, and a
# final answer is sent back until every command passes.
USE_VERIFICATION = True

COMPACT_AT = 0.70             # summarise when the context is this full
MAX_VERIFY_NUDGES = 3         # times an unverified answer is sent back
MAX_PARALLEL_SUBAGENTS = 3
SUBAGENT_MAX_STEPS = 30


# ============================================================
# SETUP
# ============================================================

# .env sits next to agent.py, not in the project being worked on.
load_dotenv(Path(__file__).resolve().parent / ".env")

if PROVIDER == "mistral":
    api_key = os.getenv("MISTRAL_API_KEY")

    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY not found in .env")

    # Mistral speaks the OpenAI API shape, so the loop below is unchanged.
    client = OpenAI(
        api_key=api_key,
        base_url="https://api.mistral.ai/v1",
        max_retries=5,
        timeout=60.0,
    )

else:
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found in .env")

    client = Groq(api_key=api_key, max_retries=5, timeout=60.0)


# Each SDK raises its own rate-limit class -- catch whichever applies.
RATE_LIMITED = (groq.RateLimitError, openai.RateLimitError)

# Set by Ctrl+C. Subagent threads check it every step and stop.
CANCEL = threading.Event()


def read_file(path: Path) -> str:
    """Read a text file and return its contents."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"[Skipped binary/non-text file: {path}]"
    except Exception as e:
        return f"[Could not read {path}: {e}]"


# ============================================================
# TOOLS
# ============================================================

MAX_STEPS = 60     # how many tool rounds before giving up
STOPPED_AT_MAX_STEPS = "[Stopped: hit MAX_STEPS without a final answer]"
MAX_OUTPUT = 20000      # chars of command output to keep
MAX_FILE_OUTPUT = 50000  # file reads get a bigger budget -- files are bounded
MAX_IMAGE_BYTES = 4_000_000
ROOT = Path.cwd().resolve()

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return text[:limit] + f"\n\n[... truncated, {cut} more characters. Narrow your request.]"


def _safe_path(path: str):
    """Resolve a path and refuse anything outside the project directory."""
    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT)):
        return None
    return target


# ------------------------------------------------------------
# Files
# ------------------------------------------------------------

def tool_file_read(path: str) -> str:
    target = _safe_path(path)
    if target is None:
        return "[Refused: path is outside the project directory]"
    if not target.is_file():
        return f"[Not a file: {path}]"
    return _truncate(read_file(target), MAX_FILE_OUTPUT)


def tool_file_write(path: str, content: str) -> str:
    target = _safe_path(path)
    if target is None:
        return "[Refused: path is outside the project directory]"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"[Wrote {len(content)} characters to {path}]"


def tool_file_edit(path: str, old_string: str, new_string: str,
                   replace_all: bool = False) -> str:
    """Replace an exact snippet instead of rewriting the whole file."""

    target = _safe_path(path)
    if target is None:
        return "[Refused: path is outside the project directory]"
    if not target.is_file():
        return f"[Not a file: {path}. Use file_write to create new files.]"
    if not old_string:
        return "[old_string is empty. Copy the exact lines to replace from file_read.]"

    # Models sometimes send booleans as strings.
    if isinstance(replace_all, str):
        replace_all = replace_all.lower() == "true"

    text = target.read_text(encoding="utf-8")
    count = text.count(old_string)

    if count == 0:
        return (f"[old_string not found in {path}. Re-read the file with file_read and "
                f"copy the exact text, including indentation and blank lines.]")
    if count > 1 and not replace_all:
        return (f"[old_string appears {count} times in {path}. Include more surrounding "
                f"lines so it matches once, or set replace_all to true.]")

    if replace_all:
        text = text.replace(old_string, new_string)
    else:
        text = text.replace(old_string, new_string, 1)

    target.write_text(text, encoding="utf-8")
    ui.diff(old_string, new_string)

    replaced = count if replace_all else 1
    return f"[Edited {path}: replaced {replaced} occurrence(s)]"


def tool_file_search(pattern: str, path: str = ".") -> str:
    target = _safe_path(path or ".")
    if target is None or not target.exists():
        return f"[No such path: {path}. Use '.' for the project root.]"

    proc = subprocess.run(
        ["grep", "-rn", "--exclude-dir=.git", "--exclude-dir=.venv",
         "--exclude-dir=__pycache__", pattern, path],
        capture_output=True, text=True, timeout=60,
    )

    return _truncate(proc.stdout.strip() or "[No matches]")


# ------------------------------------------------------------
# Images
# ------------------------------------------------------------
# A tool can return {"text": ..., "image": path}. The text becomes the tool
# result; run_loop then sends the picture itself in a user message, because
# OpenAI-style tool results can only hold text.

def _check_image(target: Path) -> str | None:
    """An error message if this image cannot be sent, else None."""
    if not SUPPORTS_VISION:
        return f"[{MODEL} cannot see images]"
    if target.suffix.lower() not in IMAGE_TYPES:
        return f"[Not a supported image type: {target.name} (png, jpg, gif, webp)]"
    size = target.stat().st_size
    if size > MAX_IMAGE_BYTES:
        return f"[Image is {size:,} bytes, over the {MAX_IMAGE_BYTES:,} limit. Crop or resize it.]"
    return None


def _image_part(target: Path) -> dict:
    data = base64.b64encode(target.read_bytes()).decode()
    mime = IMAGE_TYPES[target.suffix.lower()]
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}


def tool_view_image(path: str):
    target = _safe_path(path)
    if target is None:
        return "[Refused: path is outside the project directory]"
    if not target.is_file():
        return f"[Not a file: {path}]"

    problem = _check_image(target)
    if problem:
        return problem
    return {"text": f"[Showing {path} -- the image follows]", "image": target}


# ------------------------------------------------------------
# Shell
# ------------------------------------------------------------

def _run(command: str, timeout: int = 60) -> tuple[int | None, str]:
    """Run a command; return (exit code, output). The code is None on timeout."""
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"[Command timed out after {timeout} seconds]"

    output = (proc.stdout + proc.stderr).strip() or "(no output)"
    return proc.returncode, _truncate(output)


def run_command(command: str) -> str:
    code, output = _run(command)
    if code is None:
        return output
    return f"[exit {code}]\n{output}"


def start_background(command: str, log_file: str = "background.log") -> str:
    """Launch a command that does not exit on its own, and return immediately."""

    log_path = _safe_path(log_file)
    if log_path is None:
        return "[Refused: log path is outside the project directory]"

    # Output goes to a file, not a pipe -- otherwise we would block again.
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            command, shell=True, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )

    time.sleep(2)   # give it a moment to crash or announce itself

    printed = _truncate(read_file(log_path).strip() or "(nothing printed yet)")

    if proc.poll() is not None:
        return (f"[Exited immediately with code {proc.returncode}]\n"
                f"--- {log_file} ---\n{printed}")

    return (f"[Still running after 2s. PID {proc.pid}, logging to {log_file}]\n"
            f"--- {log_file} ---\n{printed}")


# ------------------------------------------------------------
# Browser  (needs: pip install playwright && playwright install chromium)
# ------------------------------------------------------------

try:
    from playwright.sync_api import sync_playwright
    BROWSER_AVAILABLE = True
except ImportError:
    BROWSER_AVAILABLE = False

_browser_state = {}


BROWSER_PROFILE = ROOT / ".browser-profile"


def _page():
    if not _browser_state:
        pw = sync_playwright().start()

        # A persistent profile keeps cookies and logins between runs, so you
        # only sign in to Spotify once instead of every session.
        context = pw.chromium.launch_persistent_context(
            str(BROWSER_PROFILE), headless=False
        )

        _browser_state["pw"] = pw
        _browser_state["browser"] = context
        _browser_state["page"] = context.pages[0] if context.pages else context.new_page()

    return _browser_state["page"]


def tool_browser_open(url: str) -> str:
    page = _page()
    page.goto(url, wait_until="domcontentloaded")
    text = page.inner_text("body")
    return _truncate(
        f"[Opened {url}]\nTitle: {page.title()}\n"
        f"(call browser_elements to see what is clickable)\n\n{text}"
    )


def tool_browser_click(selector: str) -> str:
    page = _page()
    page.click(selector, timeout=10000)
    return f"[Clicked {selector}] now at {page.url}"


def tool_browser_type(selector: str, text: str) -> str:
    page = _page()
    page.fill(selector, text)
    return f"[Typed into {selector}]"


def tool_browser_elements() -> str:
    """List interactive elements on the page and how to target them."""
    page = _page()
    items = page.eval_on_selector_all(
        "a, button, input, textarea, select",
        """els => els.slice(0, 60).map(e => {
            const id = e.id ? '#' + e.id : '';
            const label = (e.innerText || e.value || e.placeholder || e.name || '').trim().slice(0, 60);
            return e.tagName.toLowerCase() + id + ' :: ' + label;
        })""",
    )
    return _truncate("\n".join(items) or "[No interactive elements found]")


def tool_browser_screenshot(path: str = "screenshot.png", full_page: bool = False):
    target = _safe_path(path)
    if target is None:
        return "[Refused: path is outside the project directory]"

    page = _page()
    page.screenshot(path=str(target), full_page=full_page in (True, "true"))
    text = (f"[Saved screenshot of {page.url} to {path}]"
            " -- if that URL is about:blank you photographed an empty tab;"
            " call browser_open first.")

    # Show the model the picture too, not just where it was saved.
    if _check_image(target) is None:
        return {"text": text, "image": target}
    return text


# ------------------------------------------------------------
# Registry
# ------------------------------------------------------------

def _tool(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOLS = [
    _tool("file_read", "Read a text file from the project.",
          {"path": {"type": "string", "description": "e.g. 'app/claude.py'"}}, ["path"]),

    _tool("file_edit", "Change part of an existing file: replace old_string with new_string. "
                       "old_string must be copied exactly from the file (indentation included) "
                       "and must match only once unless replace_all is true. Prefer this to "
                       "file_write for any change to an existing file.",
          {"path": {"type": "string"},
           "old_string": {"type": "string", "description": "exact text currently in the file"},
           "new_string": {"type": "string", "description": "text to put in its place"},
           "replace_all": {"type": "boolean", "description": "replace every match (default false)"}},
          ["path", "old_string", "new_string"]),

    _tool("file_write", "Create a new file, or overwrite a file completely. For changes to an "
                        "existing file use file_edit instead.",
          {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),

    _tool("file_search", "Search the project for a pattern (like grep -rn).",
          {"pattern": {"type": "string", "description": "like grep -rn"},
           "path": {"type": "string", "description": "optional subdirectory"}},
          ["pattern"]),

    _tool("run_command", "Run a shell command that FINISHES on its own and return its output. "
                         "Never use this for servers or watchers -- it waits for the command to exit "
                         "and will time out. Keep commands simple and avoid quotes inside them.",
          {"command": {"type": "string", "description": "e.g. 'ls app/'"}}, ["command"]),

    _tool("start_background", "Start a long-running command that never exits on its own (a server, "
                              "a watcher) and return immediately. Use this instead of run_command for "
                              "things like uvicorn. Returns the PID plus whatever it printed in 2s.",
          {"command": {"type": "string", "description": "e.g. 'uvicorn app.main:app --reload'"},
           "log_file": {"type": "string", "description": "optional log filename"}}, ["command"]),
]


# ------------------------------------------------------------
# Optional tools
# ------------------------------------------------------------

if SUPPORTS_VISION:
    TOOLS += [
        _tool("view_image", "Look at an image file in the project (png, jpg, gif, webp), "
                            "e.g. a screenshot the user mentions.",
              {"path": {"type": "string"}}, ["path"]),
    ]

if USE_PROGRESS:
    TOOLS += [
        _tool("read_progress", "Read PROGRESS.md, the persistent task state from previous sessions. "
                               "Call this when starting work.",
              {}, []),

        _tool("update_progress", "Overwrite PROGRESS.md with the full updated document. "
                                 "Send the COMPLETE file, not just the changed part.",
              {"content": {"type": "string", "description": "the whole PROGRESS.md"}}, ["content"]),
    ]

if USE_DECISIONS:
    TOOLS += [
        _tool("record_decision", "Log a non-obvious decision and WHY you made it in DECISIONS.md, "
                                 "so the reason survives when old context is summarised away.",
              {"decision": {"type": "string", "description": "what you chose"},
               "why": {"type": "string", "description": "the reason"},
               "rejected": {"type": "string", "description": "alternatives you ruled out"}},
              ["decision", "why"]),
    ]

if USE_VERIFICATION:
    TOOLS += [
        _tool("plan_tasks", "Split the request into tasks, each with a shell command that exits 0 "
                            "only when the task is really done. Call before changing anything. "
                            "Calling again replaces the plan.",
              {"tasks": {"type": "array", "items": {"type": "object", "properties": {
                  "id": {"type": "string", "description": "short id, e.g. 't1'"},
                  "description": {"type": "string"},
                  "verify": {"type": "string",
                             "description": "e.g. 'pytest tests/test_add.py -q' or "
                                            "'curl -sf localhost:8000/health'"},
              }, "required": ["id", "description", "verify"]}}},
              ["tasks"]),

        _tool("verify_task", "Run a task's verify command. The harness runs it and marks the task "
                             "passed only if it exits 0.",
              {"id": {"type": "string"}}, ["id"]),
    ]

TOOLS += [
    _tool("delegate", "Run subagents in parallel. Each starts with a fresh context and the file, "
                      "shell and task tools (no browser) and returns a report. Use for independent "
                      "pieces of work; parallel subagents must never edit the same file.",
          {"tasks": {"type": "array", "items": {"type": "object", "properties": {
              "name": {"type": "string", "description": "short label, e.g. 'tests'"},
              "prompt": {"type": "string",
                         "description": "complete, self-contained instructions"},
          }, "required": ["name", "prompt"]}}},
          ["tasks"]),
]


# ------------------------------------------------------------
# Browser tools
# ------------------------------------------------------------

if BROWSER_AVAILABLE:
    TOOLS += [
        _tool("browser_open", "Open a URL and return the page's visible text.",
              {"url": {"type": "string"}}, ["url"]),

        _tool("browser_elements", "List the page's clickable elements with their CSS selectors. "
                                  "Call this before clicking or typing.",
              {}, []),

        _tool("browser_click", "Click an element by CSS selector.",
              {"selector": {"type": "string"}}, ["selector"]),

        _tool("browser_type", "Type text into an element by CSS selector.",
              {"selector": {"type": "string"}, "text": {"type": "string"}}, ["selector", "text"]),

        _tool("browser_screenshot", "Save a screenshot of the current page"
                                    + (" and look at it." if SUPPORTS_VISION else "."),
              {"path": {"type": "string", "description": "optional filename"},
               "full_page": {"type": "boolean", "description": "whole page, not just the "
                                                               "visible part (default false)"}},
              []),
    ]


# Subagents get everything except: delegate (no recursion), the browser
# (Playwright's sync API is not thread-safe) and update_progress (parallel
# writers would overwrite each other).
SUBAGENT_TOOLS = [
    t for t in TOOLS
    if t["function"]["name"] not in {"delegate", "update_progress"}
    and not t["function"]["name"].startswith("browser_")
]


TOOL_FUNCS = {
    "file_read": tool_file_read,
    "file_edit": tool_file_edit,
    "file_write": tool_file_write,
    "file_search": tool_file_search,
    "view_image": tool_view_image,
    "run_command": run_command,
    "start_background": start_background,
    "read_progress": read_progress,
    "update_progress": update_progress,
    "record_decision": compaction.record_decision,
    "browser_open": tool_browser_open,
    "browser_elements": tool_browser_elements,
    "browser_click": tool_browser_click,
    "browser_type": tool_browser_type,
    "browser_screenshot": tool_browser_screenshot,
}


def dispatch_tool(name: str, args: dict, allowed: set, tasks: TaskList,
                  label: str) -> tuple[str, Path | None]:
    """Run one tool call. Returns (text result, image to show the model or None)."""

    if name not in allowed:
        return f"[Unknown tool: {name}]", None

    ui.tool_call(name, args, label)

    # No permission prompt: every tool call runs straight away.
    try:
        # These three need the calling loop's own task list or label.
        if name == "plan_tasks":
            result = tasks.plan(args.get("tasks"))
            ui.checklist(tasks, label)
        elif name == "verify_task":
            result = tasks.verify(args.get("id", ""), lambda cmd: _run(cmd, timeout=120))
            ui.checklist(tasks, label)
        elif name == "delegate":
            result = delegate(args.get("tasks") or [])
        else:
            result = TOOL_FUNCS[name](**args)
    except Exception as e:
        # Hand the error back so the model can correct itself.
        result = f"[{name} failed: {e}]"

    image = None
    if isinstance(result, dict):
        image, result = result.get("image"), result["text"]

    if name in {"file_write", "file_edit"}:
        tasks.files_changed = True

    if name not in {"plan_tasks", "verify_task"}:
        ui.tool_result(result, label)

    return result, image


# ============================================================
# TOKEN USAGE
# ============================================================

# Running totals. Every API call re-sends the full history, so input tokens per
# call grow as the conversation gets longer -- "cached" is the part of that
# input the provider served from its prompt cache.
SESSION_TOKENS = {"input": 0, "cached": 0, "output": 0, "calls": 0}
TURN_TOKENS = {"input": 0, "cached": 0, "output": 0, "calls": 0}

# Size of the main conversation, as measured by the last API call.
CONTEXT = {"tokens": 0}

_token_lock = threading.Lock()   # subagents record usage from threads


def _cached_tokens(usage) -> int:
    details = getattr(usage, "prompt_tokens_details", None)
    if isinstance(details, dict):
        return details.get("cached_tokens") or 0
    return getattr(details, "cached_tokens", 0) or 0


def _record_usage(response, label: str) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    inp = usage.prompt_tokens or 0
    out = usage.completion_tokens or 0
    cached = _cached_tokens(usage)

    with _token_lock:
        for totals in (TURN_TOKENS, SESSION_TOKENS):
            totals["input"] += inp
            totals["cached"] += cached
            totals["output"] += out
            totals["calls"] += 1

        if label == "main":
            CONTEXT["tokens"] = inp

    pct = inp / CONTEXT_WINDOW if label == "main" else None
    ui.call_tokens(inp, cached, out, label, pct)


# ============================================================
# SYSTEM PROMPT
# ============================================================
# Built ONCE at startup and never changed. Providers cache by exact prefix, so
# a byte-identical system prompt + tool list is what lets every call reuse the
# cache. Nothing dynamic (time, PROGRESS.md, DECISIONS.md) goes in here.

# Project instructions: AGENTS.md (the cross-tool standard), else CLAUDE.md.
INSTRUCTIONS_FILE = next((n for n in ("AGENTS.md", "CLAUDE.md") if Path(n).is_file()), None)
PROJECT_INSTRUCTIONS = read_file(Path(INSTRUCTIONS_FILE)) if INSTRUCTIONS_FILE else ""


def build_system_prompt(tools: list, subagent: bool = False) -> str:
    tool_names = ", ".join(t["function"]["name"] for t in tools)
    names = set(tool_names.split(", "))

    if subagent:
        role = """
You are a subagent of a coding assistant, working inside a real project directory.
You were given one self-contained task. Do it, then reply with a short report:
what you did, which files you changed, how you verified it, and anything left undone.
Other subagents may be working in parallel -- only touch the files your task is about.
"""
    else:
        role = """
You are a coding assistant working inside a real project directory.
"""

    prompt = f"""{role}
You have these tools and they work right now: {tool_names}

Use them. Do not say you are unable to do something these tools can do --
if a browser tool is listed above, you can open and operate a real browser.
Look things up with the tools instead of guessing.

Keep shell commands simple and avoid quotes inside them.
run_command waits for a command to finish, so use start_background for anything
that keeps running (servers, watchers). Then verify it yourself -- curl the port
or read the log -- rather than assuming it worked.

You are an agent, not an advisor. Answer questions by USING the tools,
never by telling the user what to run themselves.

- If answering needs a command, call run_command and report the real output.
- Never print a command for the user to copy and paste.
- If a command fails, read the error and try a different approach.
- Never state an output you did not actually receive from a tool.
- Never describe what a file contains unless you have read those exact lines.
  Read the WHOLE file with file_read rather than guessing from a fragment.
- Finish the task. Do not end by offering to do it or asking whether to continue.
- If something blocks you (a login, a missing file, a permission), say exactly
  what blocked you and stop. Do not explain the code instead.

When changing code:
- Read the file first with file_read.
- Change it with file_edit, copying old_string exactly from what you read.
  Use file_write only for new files or complete rewrites.
- Then say what you changed and why.

Write your final answer in Markdown.
"""

    if "plan_tasks" in names:
        prompt += """
Verify your work -- the harness enforces this.
For any request that changes files or the system, call plan_tasks FIRST: split
the request into small tasks, each with a shell command that exits 0 only when
that task is really done (a test, running the script, a curl, a grep for the
expected line). After doing each task, call verify_task. The harness runs the
command itself -- you cannot mark a task done. If it fails, read the output, fix
the work and verify again. A final answer is sent back to you while any task is
unverified. Questions that change nothing need no plan.
"""

    if "read_progress" in names:
        prompt += """
PROGRESS.md is your persistent task state across sessions. Read it with
read_progress when you start work, and keep it current with update_progress:
mark finished steps, set the current step, and record discoveries and blockers.
It is state, not instructions -- conventions live in AGENTS.md.
"""

    if "record_decision" in names:
        prompt += """
When you make a non-obvious choice -- an approach, a library, a trade-off, the
fix for a tricky bug -- call record_decision with the decision and WHY. Old
context gets summarised away; DECISIONS.md is how the reasons survive.
"""

    if "view_image" in names:
        prompt += """
You can see images: view_image shows you an image file, and browser_screenshot
shows you the page it captured. Use them when the user mentions a screenshot or
when you need to check how something looks.
"""

    if "delegate" in names:
        prompt += """
delegate runs subagents in parallel, each with a fresh context. Use it for
independent pieces of work -- researching several areas, or changing separate
files. Give each one complete, self-contained instructions, never let two
parallel subagents edit the same file, and check their reports before relying
on them.
"""

    if PROJECT_INSTRUCTIONS:
        prompt += (f"\nFollow the project conventions in {INSTRUCTIONS_FILE}:\n\n"
                   f"===== {INSTRUCTIONS_FILE} =====\n{PROJECT_INSTRUCTIONS}\n")

    return prompt


SYSTEM_PROMPT = build_system_prompt(TOOLS)
SUBAGENT_PROMPT = build_system_prompt(SUBAGENT_TOOLS, subagent=True)


# ============================================================
# LLM CALL
# ============================================================

# Current session, so API calls can send its id as the cache key.
SESSION = sessions.new_session()


def _complete(messages: list, tools: list | None = None):
    kwargs = {"model": MODEL, "messages": messages, "temperature": 0}

    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    # Mistral routes requests with the same key to the same cache. Groq caches
    # automatically and needs nothing.
    if PROVIDER == "mistral":
        kwargs["extra_body"] = {"prompt_cache_key": f"harnest-{SESSION['id']}"}

    return client.chat.completions.create(**kwargs)


def compact_history(messages: list, tasks: TaskList, request: str | None = None) -> None:
    """Summarise the conversation in place and save the decisions."""

    def complete(prompt_messages):
        return _complete(prompt_messages).choices[0].message.content or ""

    with ui.thinking(text="Context is filling up -- summarising..."):
        report = compaction.compact(
            messages, complete, request=request,
            task_text=tasks.render() if tasks.tasks else "",
            use_decisions=USE_DECISIONS,
        )

    CONTEXT["tokens"] = 0   # unknown until the next call measures it
    ui.info(report)


def run_loop(messages: list, tools: list, tasks: TaskList, label: str = "main",
             max_steps: int = MAX_STEPS, request: str | None = None) -> str:
    """Call the model until it gives a final answer that passes verification.

    `messages` is the whole conversation so far and is extended in place, so
    every earlier request, tool call, tool result and answer is sent again
    with the next message -- like a normal chat bot.
    """

    allowed = {t["function"]["name"] for t in tools}
    nudges = 0

    for _ in range(max_steps):

        if CANCEL.is_set():
            return "[Cancelled by user]"

        # Only the main conversation is long-lived enough to need compacting.
        if label == "main" and CONTEXT["tokens"] > COMPACT_AT * CONTEXT_WINDOW:
            compact_history(messages, tasks, request)

        try:
            with ui.thinking(label):
                response = _complete(messages, tools)
        except RATE_LIMITED:
            ui.warn("  [rate limited -- waiting 60s for the window to reset]")
            CANCEL.wait(60)
            continue

        _record_usage(response, label)

        message = response.choices[0].message

        # No tool calls means the model thinks it is done.
        if not message.tool_calls:
            answer = message.content or ""
            messages.append({"role": "assistant", "content": answer})

            if not USE_VERIFICATION:
                return answer

            # Re-run every check: a later edit can break a task that passed earlier.
            if tasks.tasks:
                tasks.recheck(lambda cmd: _run(cmd, timeout=120))
                ui.checklist(tasks, label)

            problem = tasks.problem()
            if problem is None or nudges == MAX_VERIFY_NUDGES:
                return answer

            nudges += 1
            ui.warn(f"  {'' if label == 'main' else f'[{label}] '}"
                    f"not verified -- sending it back ({nudges}/{MAX_VERIFY_NUDGES})")
            messages.append({"role": "user", "content": problem})
            continue

        # 1. Record what the model asked for.
        messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls
            ],
        })

        # 2. Run it and hand the output back.
        images = []
        for call in message.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as e:
                result, image = f"[Arguments were not valid JSON: {e}]", None
            else:
                result, image = dispatch_tool(call.function.name, args, allowed, tasks, label)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
            if image is not None:
                images.append(image)

        # 3. Tool results can only hold text, so pictures follow as a user
        # message. The short assistant line in between keeps the order valid
        # for APIs that reject a user message straight after tool results.
        if images:
            messages.append({"role": "assistant", "content": "Let me look at the image."})
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Image(s) from the tool call above:"},
                *[_image_part(path) for path in images],
            ]})

    # Close the turn with an assistant message so the next user message is valid.
    messages.append({"role": "assistant", "content": STOPPED_AT_MAX_STEPS})
    return STOPPED_AT_MAX_STEPS


def close_interrupted_turn(messages: list, note: str = "[Interrupted by user]") -> None:
    """Make the history valid again after Ctrl+C (or an error) stopped a turn.

    Tool calls that never got a result get a "[cancelled]" one, then the turn is
    closed with an assistant message. Whatever already happened stays in the
    history, so the model still knows which files it changed.
    """

    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]

        if m["role"] == "assistant":
            answered = {x.get("tool_call_id") for x in messages[i + 1:]}
            for call in m.get("tool_calls") or []:
                if call["id"] not in answered:
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": "[cancelled by user]"})
            break

        if m["role"] in {"user", "system"}:
            break

    messages.append({"role": "assistant", "content": note})


# ============================================================
# SUBAGENTS
# ============================================================

def delegate(specs: list) -> str:
    """Run up to MAX_PARALLEL_SUBAGENTS subagents at once and collect their reports."""

    specs = [s for s in specs if isinstance(s, dict) and s.get("prompt")]
    if not specs:
        return "[delegate needs a list of tasks, each with a name and a prompt]"

    def run_one(spec):
        name = str(spec.get("name") or "sub")
        messages = [{"role": "system", "content": SUBAGENT_PROMPT},
                    {"role": "user", "content": spec["prompt"]}]
        tasks = TaskList()

        try:
            report = run_loop(messages, SUBAGENT_TOOLS, tasks, label=name,
                              max_steps=SUBAGENT_MAX_STEPS)
        except Exception as e:
            report = f"[subagent failed: {e}]"

        if USE_VERIFICATION and tasks.problem():
            report += f"\n\n[UNVERIFIED -- its checks did not all pass]\n{tasks.render()}"

        return f"=== {name} ===\n{report}"

    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL_SUBAGENTS)
    try:
        futures = [pool.submit(run_one, spec) for spec in specs]
        return "\n\n".join(f.result() for f in futures)
    except KeyboardInterrupt:
        # Threads cannot be killed; tell them to stop after their current step.
        CANCEL.set()
        ui.warn("Stopping subagents after their current step...")
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# ============================================================
# SLASH COMMANDS
# ============================================================

HELP = """Commands:
  /name <name>      name this session (it autosaves after every turn)
  /resume <name>    load a saved session (name or id)
  /sessions         list saved sessions for this project
  /new              start a fresh session
  /compact          summarise the conversation now
  /help             this list
Ctrl+C cancels the current line or turn. Ctrl+D or 'exit' quits."""


def harness_config() -> dict:
    """The settings that shape the agent's behaviour, saved with every session."""
    return {
        "provider": PROVIDER, "model": MODEL,
        "use_progress": USE_PROGRESS, "use_decisions": USE_DECISIONS,
        "use_verification": USE_VERIFICATION,
        "max_steps": MAX_STEPS, "max_verify_nudges": MAX_VERIFY_NUDGES,
        "compact_at": COMPACT_AT, "subagent_max_steps": SUBAGENT_MAX_STEPS,
    }


def save_session(history: list) -> None:
    sessions.save(ROOT, SESSION, history, SESSION_TOKENS, provider=PROVIDER, model=MODEL,
                  config=harness_config())


def handle_command(line: str, history: list) -> None:
    command, _, arg = line.partition(" ")
    arg = arg.strip()

    if command == "/help":
        ui.info(HELP)

    elif command == "/name":
        error = sessions.rename(ROOT, SESSION, arg)
        if error:
            ui.error(error)
        else:
            save_session(history)
            ui.info(f"Session saved as '{SESSION['name']}'. Come back with /resume {SESSION['name']}")

    elif command == "/sessions":
        ui.session_list(sessions.list_all(ROOT))

    elif command == "/resume":
        if not arg:
            ui.session_list(sessions.list_all(ROOT))
            ui.info("Usage: /resume <name>")
            return

        data = sessions.load(ROOT, arg)
        if data is None:
            ui.error(f"No session '{arg}'. /sessions lists them.")
            return

        SESSION.clear()
        SESSION.update({k: data.get(k) for k in ("id", "name", "created")})
        history[:] = [{"role": "system", "content": SYSTEM_PROMPT}] + data["messages"]
        SESSION_TOKENS.update(data.get("tokens") or {})
        CONTEXT["tokens"] = 0

        ui.info(f"Resumed '{SESSION['name'] or SESSION['id']}' -- {len(data['messages'])} messages.")
        last = next((m for m in reversed(data["messages"])
                     if m["role"] == "assistant" and m.get("content")), None)
        if last:
            ui.answer(last["content"])

    elif command == "/new":
        SESSION.clear()
        SESSION.update(sessions.new_session())
        history[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
        SESSION_TOKENS.update({"input": 0, "cached": 0, "output": 0, "calls": 0})
        CONTEXT["tokens"] = 0
        ui.info("Started a new session.")

    elif command == "/compact":
        if len(history) <= 1:
            ui.info("Nothing to compact yet.")
            return
        compact_history(history, TaskList())
        save_session(history)

    else:
        ui.error(f"Unknown command {command}. /help lists them.")


# ============================================================
# INTERACTIVE LOOP
# ============================================================

def run_turn(user_prompt: str, history: list) -> dict:
    """Run one request. Returns what happened, for --result-file."""

    CANCEL.clear()
    with _token_lock:
        TURN_TOKENS.update({"input": 0, "cached": 0, "output": 0, "calls": 0})

    tasks = TaskList()   # a fresh plan for every request
    history.append({"role": "user", "content": user_prompt})
    outcome = {"answer": None, "unverified": None, "hit_max_steps": False, "error": None}

    try:
        answer = run_loop(history, TOOLS, tasks, request=user_prompt)
        ui.answer(answer)
        outcome["answer"] = answer
        outcome["hit_max_steps"] = answer == STOPPED_AT_MAX_STEPS

        problem = tasks.problem() if USE_VERIFICATION else None
        if problem:
            ui.unverified(problem)
            outcome["unverified"] = problem

    except KeyboardInterrupt:
        CANCEL.set()
        close_interrupted_turn(history)
        ui.warn("\nInterrupted. Keep chatting -- the agent knows the turn was cut short.")
        outcome["error"] = "interrupted"

    except Exception as e:
        close_interrupted_turn(history, f"[Turn failed: {e}]")
        ui.error(f"\nError: {e}")
        ui.info("If this keeps happening, /new starts a fresh session.")
        outcome["error"] = f"{type(e).__name__}: {e}"

    # Printed even after an error -- the tokens were still spent.
    ui.turn_summary(TURN_TOKENS, SESSION_TOKENS)
    save_session(history)

    return {**outcome, "turn_tokens": dict(TURN_TOKENS)}


def run_headless(prompt_file: Path, result_file: Path | None) -> int:
    """Run the request in prompt_file as a single turn, without the >>> prompt.

    Exit code 0 means the turn finished (verified or not); 1 means it crashed.
    """

    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    started = time.time()
    outcome = run_turn(prompt_file.read_text(encoding="utf-8").strip(), history)

    if result_file:
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text(json.dumps({
            **outcome,
            "session_id": SESSION["id"],
            "seconds": round(time.time() - started, 1),
            "config": harness_config(),
        }, indent=1), encoding="utf-8")

    return 0 if outcome["error"] is None else 1


def main():

    # The conversation, kept across requests until you exit.
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    instructions = (f"{INSTRUCTIONS_FILE} included in the system prompt" if PROJECT_INSTRUCTIONS
                    else "none (no AGENTS.md or CLAUDE.md)")

    ui.banner([
        f"Working on:    {ROOT}",
        f"Model:         {MODEL} ({PROVIDER})",
        f"Instructions:  {instructions}",
        f"Tools:         {', '.join(t['function']['name'] for t in TOOLS)}",
        "",
        "Type a request. /help for commands. Ctrl+C cancels, Ctrl+D or 'exit' quits."
        if PROMPT_FILE is None else f"Running the request in {PROMPT_FILE.name}, then exiting.",
    ])

    if PROMPT_FILE is not None:
        sys.exit(run_headless(PROMPT_FILE, RESULT_FILE))

    while True:

        try:
            user_prompt = input("\n>>> ").strip()
        except KeyboardInterrupt:
            print()   # drop the half-typed line, keep the session
            continue
        except EOFError:
            print()
            break

        if user_prompt.lower() in {"exit", "quit"}:
            break

        if not user_prompt:
            continue

        if user_prompt.startswith("/"):
            try:
                handle_command(user_prompt, history)
            except KeyboardInterrupt:
                ui.warn("\nCancelled.")
            continue

        run_turn(user_prompt, history)

    if len(history) > 1:
        ui.info(f"Session saved as '{SESSION['name'] or SESSION['id']}'.")


if __name__ == "__main__":
    main()
