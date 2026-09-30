# harnest

A small coding-agent harness: an LLM in a tool loop that can read and write files, run shell commands, start servers, and drive a real browser inside a project directory.

It runs as a conversation. Every request, tool call, tool result and answer stays in the history and is sent again with the next message, until you exit.

## Setup

Requires Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium        # optional, for the browser tools

cp .env.example .env               # then fill in your API key
```

## Run

```bash
python agent.py /path/to/project   # or run it from inside the project with no argument
```

Type a request at the `>>>` prompt. Type `exit` to quit, which also ends the conversation.

If the project has a `CLAUDE.md` in its root, it is loaded as context on the first message.

## Tools

| Tool | What it does |
|---|---|
| `file_read`, `file_write`, `file_search` | Read, overwrite, and grep files inside the project |
| `run_command` | Run a shell command that finishes on its own (60s timeout) |
| `start_background` | Start a server or watcher and return right away, logging to a file |
| `read_progress`, `update_progress` | Persistent task state in the project's `PROGRESS.md` (toggle with `USE_PROGRESS`) |
| `browser_*` | Open, inspect, click, type and screenshot pages (only if Playwright is installed) |

File tools refuse paths outside the project directory. `run_command` does not: it can run any shell command.

**Tool calls run without asking for approval.** Point it at a project under git and commit before each run, so anything it does can be undone.

## Configuration

Edit the top of `agent.py`:

- `PROVIDER`: `"mistral"` (default, `ministral-14b-latest`) or `"groq"`
- `USE_PROGRESS`: give the agent `PROGRESS.md` or not
- `MAX_STEPS`: tool rounds per request before it gives up
- `CONTEXT_PATHS`: extra files or folders to include as context on the first message

## Token usage

After each API call it prints that call's input and output tokens, and after each answer, totals for the turn and the session. Every call re-sends the whole conversation, so the session total grows much faster than the conversation does: the latest call's input is the current context size, and the session total is what you have spent.
