import json
import os
import subprocess
import sys
import time
from pathlib import Path

import groq
import openai
from dotenv import load_dotenv
from groq import Groq
from openai import OpenAI

from progressmd import read_progress, update_progress


# The project to work on: first argument, or the current directory.
#   python agent.py ~/projects/myapp
PROJECT_DIR = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser().resolve()

if not PROJECT_DIR.is_dir():
    raise SystemExit(f"Not a directory: {PROJECT_DIR}")

os.chdir(PROJECT_DIR)


# ============================================================
# CONFIGURATION
# ============================================================

# Add the files/folders you want to provide as context here.
#
# Examples:
#   "src/api.py"
#   "src/models.py"
#   "src/"
#
# Leave empty if you don't want to provide any codebase context.
CONTEXT_PATHS = [
    # CLAUDE.md is auto-included by collect_context -- do not list it here.
]


# Groq model to use
# Which provider to use: "mistral" or "groq".
# Flip this one line to switch; everything else stays the same.
PROVIDER = "mistral"

MODELS = {
    "mistral": "ministral-14b-latest",   # 30 req/min, 262k context, vision + tools
    "groq": "openai/gpt-oss-120b",       # 8,000 tokens/min -- tight for a tool loop
}

MODEL = MODELS[PROVIDER]


# ============================================================
# HARNESS TOGGLES
# ============================================================

# Persistent task state:
# True  -> agent has access to PROGRESS.md
# False -> agent does not have access to PROGRESS.md
#
# Use this for your ablation experiment.
USE_PROGRESS = True


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


# ============================================================
# CONTEXT LOADING
# ============================================================

def read_file(path: Path) -> str:
    """Read a text file and return its contents."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"[Skipped binary/non-text file: {path}]"
    except Exception as e:
        return f"[Could not read {path}: {e}]"


def collect_context(paths):
    """
    Read all files specified in CONTEXT_PATHS.

    Also automatically reads CLAUDE.md from the project root
    if it exists.
    """
    context_parts = []

    # --------------------------------------------------------
    # CLAUDE.md
    # --------------------------------------------------------

    claude_file = Path("CLAUDE.md")

    if claude_file.exists() and claude_file.is_file():
        content = read_file(claude_file)

        context_parts.append(
            f"\n===== CLAUDE.md =====\n"
            f"{content}\n"
        )

    # --------------------------------------------------------
    # User-specified context
    # --------------------------------------------------------

    for raw_path in paths:
        path = Path(raw_path)

        if not path.exists():
            context_parts.append(
                f"\n===== {path} =====\n"
                f"[Path does not exist]\n"
            )
            continue

        # Single file
        if path.is_file():
            content = read_file(path)

            context_parts.append(
                f"\n===== FILE: {path} =====\n"
                f"{content}\n"
            )

        # Folder
        elif path.is_dir():
            for file_path in sorted(path.rglob("*")):

                # Skip directories
                if not file_path.is_file():
                    continue

                # Skip common irrelevant directories
                if any(
                    part in {".git", "__pycache__", ".venv", "venv", "node_modules"}
                    for part in file_path.parts
                ):
                    continue

                content = read_file(file_path)

                context_parts.append(
                    f"\n===== FILE: {file_path} =====\n"
                    f"{content}\n"
                )

    return "\n".join(context_parts)


# ============================================================
# TOOLS
# ============================================================

MAX_STEPS = 60     # how many tool rounds before giving up
MAX_OUTPUT = 20000      # chars of command output to keep
MAX_FILE_OUTPUT = 50000  # file reads get a bigger budget -- files are bounded
ROOT = Path.cwd().resolve()


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


def _confirm(what: str) -> bool:
    """Ask for permission before executing a tool."""
    print(f"  [tool] {what}")
    return input("  allow? [y/N] ").strip().lower() == "y"


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
# Shell
# ------------------------------------------------------------

def run_command(command: str) -> str:
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "[Command timed out after 60 seconds]"

    output = (proc.stdout + proc.stderr).strip() or "(no output)"
    return f"[exit {proc.returncode}]\n{_truncate(output)}"


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


def tool_browser_screenshot(path: str = "screenshot.png") -> str:
    page = _page()
    page.screenshot(path=path, full_page=True)
    return (f"[Saved screenshot of {page.url} to {path}]"
            " -- if that URL is about:blank you photographed an empty tab;"
            " call browser_open first.")


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

    _tool("file_write", "Create or overwrite a file with the given content.",
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
# Persistent state tools
# ------------------------------------------------------------

if USE_PROGRESS:
    TOOLS += [
        _tool("read_progress", "Read PROGRESS.md, the persistent task state from previous sessions. "
                               "Call this when starting work.",
              {}, []),

        _tool("update_progress", "Overwrite PROGRESS.md with the full updated document. "
                                 "Send the COMPLETE file, not just the changed part.",
              {"content": {"type": "string", "description": "the whole PROGRESS.md"}}, ["content"]),
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

        _tool("browser_screenshot", "Save a screenshot of the current page.",
              {"path": {"type": "string", "description": "optional filename"}}, []),
    ]


TOOL_FUNCS = {
    "file_read": tool_file_read,
    "file_write": tool_file_write,
    "file_search": tool_file_search,
    "run_command": run_command,
    "start_background": start_background,
    "browser_open": tool_browser_open,
    "browser_elements": tool_browser_elements,
    "browser_click": tool_browser_click,
    "browser_type": tool_browser_type,
    "browser_screenshot": tool_browser_screenshot,
}


if USE_PROGRESS:
    TOOL_FUNCS["read_progress"] = read_progress
    TOOL_FUNCS["update_progress"] = update_progress


def dispatch_tool(name: str, args: dict) -> str:
    func = TOOL_FUNCS.get(name)

    if func is None:
        return f"[Unknown tool: {name}]"

    print(f"\n  [tool] {name}")
    if args:
        print(f"  arguments: {json.dumps(args, indent=2)}")

    # No permission prompt: every tool call runs straight away.
    try:
        return func(**args)
    except Exception as e:
        # Hand the error back so the model can correct itself.
        return f"[{name} failed: {e}]"


# ============================================================
# TOKEN USAGE
# ============================================================

# Running totals across the whole session. Every API call re-sends the full
# history, so input tokens per call grow as the conversation gets longer.
SESSION_TOKENS = {"input": 0, "output": 0, "calls": 0}


def _record_usage(response, turn_tokens: dict) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return

    inp = usage.prompt_tokens or 0
    out = usage.completion_tokens or 0

    turn_tokens["input"] += inp
    turn_tokens["output"] += out
    turn_tokens["calls"] += 1

    SESSION_TOKENS["input"] += inp
    SESSION_TOKENS["output"] += out
    SESSION_TOKENS["calls"] += 1

    print(f"  [tokens] call: {inp} in / {out} out")


# ============================================================
# LLM CALL
# ============================================================

def generate_code(user_prompt: str, context: str, messages: list,
                  turn_tokens: dict) -> str:
    """Run one turn of the conversation.

    `messages` is the whole conversation so far and is extended in place, so
    every earlier request, tool call, tool result and answer is sent again
    with the next message -- like a normal chat bot.

    `turn_tokens` is filled with this turn's input/output token counts.
    """

    tool_names = ", ".join(t["function"]["name"] for t in TOOLS)

    progress_instructions = ""

    if USE_PROGRESS:
        progress_instructions = """
PROGRESS.md is your persistent task state across sessions. Read it with
read_progress when you start work, and keep it current with update_progress:
mark finished steps, set the current step, and record discoveries, decisions
and blockers. It is state, not instructions -- conventions live in CLAUDE.md.
"""

    system_prompt = f"""
You are a coding assistant working inside a real project directory.

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

{progress_instructions}

When changing code:
- Read the file first with file_read.
- Write the change with file_write, sending back the COMPLETE file.
- Then say what you changed and why.

Follow the project conventions described in CLAUDE.md.
"""

    # First turn: system prompt + codebase context. Later turns only add the
    # new request -- the context is already earlier in the conversation.
    if not messages:
        messages.append({"role": "system", "content": system_prompt})

        user_message = f"""
USER REQUEST:
{user_prompt}

CODEBASE CONTEXT:
{context if context else "[No codebase context was provided.]"}
"""
    else:
        user_message = f"""
USER REQUEST:
{user_prompt}
"""

    messages.append({"role": "user", "content": user_message})

    # Keep calling the model until it stops asking for tools.
    for _ in range(MAX_STEPS):

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
            )
        except RATE_LIMITED:
            print("  [rate limited -- waiting 60s for the window to reset]")
            time.sleep(60)
            continue

        _record_usage(response, turn_tokens)

        message = response.choices[0].message

        # No tool calls means this is the final answer.
        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content or ""})
            return message.content

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
        for call in message.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            result = dispatch_tool(call.function.name, args)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })

    stopped = "[Stopped: hit MAX_STEPS without a final answer]"
    # Close the turn with an assistant message so the next user message is valid.
    messages.append({"role": "assistant", "content": stopped})
    return stopped


# ============================================================
# INTERACTIVE LOOP
# ============================================================

def main():

    context = collect_context(CONTEXT_PATHS)

    # The conversation, kept across requests until you exit.
    history = []

    print("\n" + "=" * 70)
    print("AGENT CODE ASSISTANT (conversation mode)")
    print("=" * 70)

    print(f"\nWorking on: {ROOT}")

    claude_file = Path("CLAUDE.md")

    if claude_file.exists() and claude_file.is_file():
        print("\nCLAUDE.md found and included as context.")
    else:
        print("\nNo CLAUDE.md found.")

    print("\nTools available:")
    for tool in TOOLS:
        print(f"  - {tool['function']['name']}")

    if CONTEXT_PATHS:
        print("\nContext provided:")
        for path in CONTEXT_PATHS:
            print(f"  - {path}")
    else:
        print("\nNo additional codebase context provided.")

    print("\nType your coding request.")
    print("Type 'exit' to quit.\n")

    while True:

        user_prompt = input(">>> ").strip()

        if user_prompt.lower() in {"exit", "quit"}:
            break

        if not user_prompt:
            continue

        print("\nGenerating...\n")

        turn_start = len(history)
        turn_tokens = {"input": 0, "output": 0, "calls": 0}

        try:
            result = generate_code(user_prompt, context, history, turn_tokens)

            print("=" * 70)
            print("MODEL RESPONSE")
            print("=" * 70)
            print(result)
            print()

        except Exception as e:
            # Drop the half-finished turn so the history stays valid.
            del history[turn_start:]
            print(f"\nError: {e}\n")

        # Printed even after an error -- the tokens were still spent.
        print("-" * 70)
        print(f"TOKENS this turn:  {turn_tokens['input']} in / "
              f"{turn_tokens['output']} out  ({turn_tokens['calls']} API calls)")
        print(f"TOKENS session:    {SESSION_TOKENS['input']} in / "
              f"{SESSION_TOKENS['output']} out  ({SESSION_TOKENS['calls']} API calls)")
        print("-" * 70 + "\n")


if __name__ == "__main__":
    main()