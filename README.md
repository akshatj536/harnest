# harnest

A coding-agent harness: an LLM in a tool loop that can read, edit and write files, run shell commands, start servers, drive a real browser, look at screenshots, and hand work to parallel subagents, all inside one project directory.

It runs as a conversation. Every request, tool call, tool result and answer stays in the history and is sent again with the next message. When the context gets 70% full, the history is summarised automatically.

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

Type a request at the `>>>` prompt.

- **Ctrl+C** clears the line you are typing. During a turn, it stops the agent and returns you to the prompt. The conversation carries on, and the agent knows the turn was cut short.
- **Ctrl+D** or `exit` quits.

If the project has an `AGENTS.md` in its root, it is added to the system prompt. A `CLAUDE.md` is used instead when there is no `AGENTS.md`.

### Headless mode

To run one request from a script instead of the `>>>` prompt:

```bash
python agent.py /path/to/project --prompt-file request.md --result-file result.json
```

The command runs the request as a single turn and exits. The result file is JSON containing:
- the answer
- whether the verification loop passed (`unverified` is null when it did)
- whether the turn hit `MAX_STEPS`
- the turn's token usage
- the session id
- the time taken
- the harness configuration

The exit code is 0 if the turn finished and 1 if it crashed.

Set `HARNEST_HOME` to keep these sessions apart from your interactive ones. Sessions are then saved under `$HARNEST_HOME/sessions/` instead of `~/.harnest/sessions/`.

## Commands

| Command | What it does |
|---|---|
| `/name <name>` | Name this session |
| `/resume <name>` | Load a saved session (by name or id) |
| `/sessions` | List saved sessions for this project |
| `/new` | Start a fresh session |
| `/compact` | Summarise the conversation now |
| `/help` | List commands |

Every session autosaves after each turn to `~/.harnest/sessions/<project-path>/` (or `$HARNEST_HOME/sessions/<project-path>/` if `HARNEST_HOME` is set). Unnamed sessions are saved under a timestamp id.

## Tools

| Tool | What it does |
|---|---|
| `file_read`, `file_search` | Read and grep files inside the project |
| `file_edit` | Replace an exact snippet of a file. It must match once, unless `replace_all` is set |
| `file_write` | Create a file, or rewrite one completely |
| `view_image` | Look at an image file (vision models only) |
| `run_command` | Run a shell command that finishes on its own (60s timeout) |
| `start_background` | Start a server or watcher and return right away, logging to a file |
| `plan_tasks`, `verify_task` | The verification loop (see below) |
| `record_decision` | Log a decision and why it was made to `DECISIONS.md` |
| `read_progress`, `update_progress` | Persistent task state in the project's `PROGRESS.md` |
| `delegate` | Run up to 3 subagents in parallel and collect their reports |
| `browser_*` | Open, inspect, click, type and screenshot pages (only if Playwright is installed). The model sees each screenshot |

File tools refuse paths outside the project directory. `run_command` does not: it can run any shell command.

**Tool calls run without asking for approval.** Point it at a project under git and commit before each run, so anything it does can be undone.

## How it works

**Verification loop.** For a request that changes things, the agent first calls `plan_tasks`. This splits the work into tasks, each with a shell command that exits 0 only when the task is done. `verify_task` runs that command, and the harness, not the model, decides pass or fail.

When the model gives its final answer, the harness re-runs every check. If any check fails, the answer is sent back with the failing tasks, up to 3 times. The same happens if files changed with no plan. After that, the answer is shown with an **UNVERIFIED** warning.

**Compaction and DECISIONS.md.** When the last call used 70% of the context window, one model call does two things:

- It writes a summary that replaces the history.
- It extracts every decision and its reason. The harness appends these to the project's `DECISIONS.md`.

The summary and the decisions are both put back into the new context. That way, the *why* behind earlier work is kept even after the details are gone.

**Subagents.** `delegate` gives each subagent a fresh context, the file, shell and task tools, and its own verification loop. Subagents can't delegate further, use the browser, or write `PROGRESS.md`.

**Prompt caching.** Mistral and Groq both cache by exact prefix. To keep the prefix identical from call to call:

- The system prompt and tool list are built once and never change.
- The history is only ever appended to.

So each call reuses everything but the newest messages. Compaction is the only thing that resets the cache. The `cached` figure in the token lines shows how much was served from cache.

## Evals

`evals/` measures whether a change to harnest makes it better or worse. It runs the agent on fixed coding tasks:
- build a module from scratch
- add a CLI feature
- fix a bug from a report
- fix a failing test
- answer a question about a repo

Each task runs several times, each time in a clean, identical workspace. The final state is graded with deterministic checks, and every trial's pass/fail, tokens and time are recorded.

```bash
python -m evals validate          # check every task and grader, no model calls
python -m evals run --trials 3    # run harnest on every task
python -m evals show <run-id>     # summary: pass rate, pass^k, tokens, time
```

The agent is never told it is being evaluated, and the tests that grade it are not in its workspace. See [evals/README.md](evals/README.md) for how it works and how to add tasks and graders.

## Configuration

Edit the top of `agent.py`:

- `PROVIDER`: `"mistral"` (default, `ministral-14b-latest`, can see images) or `"groq"` (text only)
- `USE_PROGRESS`, `USE_DECISIONS`, `USE_VERIFICATION`: switch each harness feature on or off, for ablation experiments
- `COMPACT_AT`: how full the context gets before it is summarised (default `0.70`)
- `MAX_STEPS`, `MAX_VERIFY_NUDGES`, `MAX_PARALLEL_SUBAGENTS`, `SUBAGENT_MAX_STEPS`

## Token usage

After each API call it prints:

- that call's input tokens (and how many were cached)
- its output tokens
- how full the context window is

After each answer, it prints totals for the turn and the session. Every call re-sends the whole conversation, so the session total grows much faster than the conversation does.
