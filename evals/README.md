# harnest evals

Runs harnest on fixed coding tasks, several times each, in clean and identical workspaces. The final state of each workspace is graded with deterministic checks, and every trial's pass/fail result, tokens and time are recorded.

```bash
source .venv/bin/activate
python -m evals list                                   # repos and tasks
python -m evals validate                               # check every task and grader (no model calls)
python -m evals run --trials 3                         # all tasks, 3 trials each
python -m evals run --task feature-cli-json --trials 5 --label after-prompt-change
python -m evals show <run-id>                          # a run's summary
python -m evals show <run-id>/scratch-roman-numerals/trial-1   # one trial in detail
python -m evals report <run-a> <run-b>                 # pool trials, e.g. 1 earlier + 2 new = 3 per task
python -m evals start feature-cli-json /tmp/t          # a task's starting state, for writing patches
python -m pytest                                       # tests for the eval harness itself
```

`run` validates the selected tasks first and refuses to start if any are broken. Results go to `evals/runs/<run-id>/` (gitignored).

## How a trial works

```
evals/repos/<repo>/  ──copy──►  /tmp/ws-XXXX/project   + setup.patch, then ONE git commit
                                       │                  (same SHA every trial)
                                       ▼
                     python agent.py <workspace> --prompt-file … --result-file …
                                       │   timeout → Ctrl+C, then kill; the whole
                                       │   process group is killed afterwards
                                       ▼
                     snapshot: changed files, diff, transcript, tokens, time
                                       │
                                       ▼
                     each grader gets its own copy of the final workspace;
                     hidden tests are copied in only now
                                       │
                                       ▼
                     PASS if every gating grader passes
```

## What PASS and FAIL mean

- **Trial PASS**: every grader with `gating = true` passes. Anything else is a **FAIL**.
- **INVALID**: the agent stopped without making a single model call, for example because the API key is missing or the network is down. Invalid trials are reported separately and don't count toward the score.
- **Per task**, the summary shows:
  - **pass rate**
  - **pass@k**: at least one of k trials passed
  - **pass^k**: all k trials passed. This is the reliability number, and it drops quickly with k.
  - median time, tokens and API calls
- `self_verified` in each trial record says whether harnest's own verification loop believed the work was done. Compare it with the real verdict: when harnest says verified but the trial fails, its checks were too weak.

## Every grader is checked itself

`python -m evals validate` runs each task three ways, without any model:

| Agent | Applies | Must be | Proves |
|---|---|---|---|
| NullAgent | nothing (the starting state) | **FAIL**: every `outcome` grader fails, every `guard` passes | The task isn't already solved, and each outcome grader can tell "done" from "not done". |
| PatchAgent | `solution.patch` | **PASS**: every grader passes | The task is solvable, and the graders accept a correct answer. |
| PatchAgent | each `known_bad/*.patch` | **FAIL** | The graders catch plausible wrong answers, not only empty ones. |

`validate` also checks:
- each repo's own `check` passes on a clean copy
- prompts don't give away that they are evals
- the starting state builds to the same commit every time

## Keeping harnest blind to the evals

- **harnest has no eval code.** `--prompt-file`, `--result-file` and `HARNEST_HOME` are generic features.
- **The workspace is `/tmp/ws-XXXX/project`**, outside this repo, with nothing in its path that says "eval". It contains only the starting repo. Hidden tests, solutions, known-bad patches, `task.toml` and repo notes never exist inside it while the agent runs.
- **`setup.patch` is applied before the only commit**, so `git log` and `git diff` cannot reveal an injected bug.
- **Prompts read like normal requests.** `validate` rejects words such as "eval", "grader", "hidden tests" or "benchmark".
- **`no_peeking` (gating) fails the trial** if any tool call, or anything in the agent's log, mentions the `evals/` path, `hidden_tests`, `solution.patch`, `known_bad`, `setup.patch` or `task.toml`.
- **Graders are hard to game.** Test commands run with:
  - `python -I`, so files in the workspace can't shadow modules
  - `-c pyproject.toml`, so a `pytest.ini` the agent writes is ignored
  - `--noconftest`
  - `restore`, so tests and config are reset to the starting state first
  - overlays that replace anything already at that path

**Known gaps:**
- The agent can still read the filesystem with `run_command`. `no_peeking` detects this but doesn't prevent it, and running the agent under `bwrap` would close the gap.
- `PATH` points at harnest's `.venv`.
- Subagents' tool calls are only checked through the printed log, which shortens long arguments.

## Layout

```
evals/
  repos/<name>/          a starting project, copied verbatim (only what a real project would hold)
  repos/<name>.toml      its description, `check` command and reuse notes (never copied)
  tasks/<id>/
    task.toml            repo, prompt, timeout, graders
    prompt.md            exactly what the agent is asked
    setup.patch          optional: turns the repo into this task's starting state
    hidden_tests/        optional: copied in only when grading
    solution.patch       a known-good answer
    known_bad/*.patch    plausible wrong answers
  runs/<run-id>/         run.json · results.jsonl · summary.md · <task>/trial-N/
                           agent.log · transcript.json · result.json · diff.patch · grades.json · workspace/
```

## The tasks

| Task | Repo | Skill | PASS means |
|---|---|---|---|
| `scratch-roman-numerals` | python-empty | build from scratch | the hidden spec tests pass (known values, all 3999 round trips, range errors, non-canonical input rejected) |
| `feature-cli-json` | bookshelf | add a feature | the hidden `--json` tests pass · the original tests still pass · no existing test changed |
| `debug-pagination-offset` | bookshelf + setup.patch | debug from a bug report | the hidden paging tests pass, including "page 0 is still rejected" · the original tests pass · no test changed |
| `debug-failing-test` | bookshelf + setup.patch | debug from a failing test | that test passes as originally written · the whole suite passes · the hidden other-casing tests pass · no test changed |
| `navigate-overdue-charge` | bookshelf | find where something happens | `ANSWER.txt` is exactly `bookshelf.policies.overdue_charge` · no project file changed or added |

Every task also has `no-peeking` (gating) and `stays-in-scope` (reported only: files changed outside the expected ones).

## Adding a repo

1. Create `evals/repos/<name>/` as a real, healthy project: code, tests, README.
2. Create `evals/repos/<name>.toml` with `description`, `check` (a command that must pass on a clean copy) and `notes`. In `notes`, record the reuse seams future tasks can use: bug sites, missing features, dead files, and facts for question tasks. Never put these notes inside the repo folder, because the agent would read them.
3. Run `python -m evals validate`.

## Adding a task

1. Create `evals/tasks/<id>/task.toml`:

   ```toml
   id = "<id>"                 # must match the folder name
   category = "debug"          # build | feature | debug | navigate | ...
   repo = "bookshelf"          # or "./repo" for a task-local repo
   setup_patch = "setup.patch" # optional
   prompt = "prompt.md"
   timeout_seconds = 900

   [[graders]]
   id = "fixes-bug"
   type = "command"
   role = "outcome"            # outcome: proves it's done; guard: nothing was broken
   gating = true               # default true; false = reported only
   cmd = "python -I -m pytest -q -p no:cacheprovider --noconftest -c pyproject.toml hidden_tests"
   overlay = "hidden_tests"
   restore = ["pyproject.toml"]
   ```

2. Write `prompt.md` using the checklist below.
3. Make the patches from a real starting state:

   ```bash
   python -m evals start <id> /tmp/t --no-setup   # base repo: edit, then diff into setup.patch
   python -m evals start <id> /tmp/t              # starting state: edit, then diff into solution.patch or known_bad/*.patch
   git -C /tmp/t add -A && git -C /tmp/t diff --cached > evals/tasks/<id>/solution.patch
   ```

4. Run `python -m evals validate --task <id>` and fix it until it says valid.

**Prompt checklist:**
- Two engineers would agree on what "done" means. State the exact names, formats, edge cases and errors that the graders check, and check nothing the prompt doesn't state or clearly imply.
- Write it the way a user would. Never mention tests you'll run, graders or evaluation.
- Say what must not change ("don't change the tests", "don't change any other files") when a guard grader checks it.
- Include a reproduction for bugs, and the real output for failing tests.

## Adding a grader type

Graders live in `evals/graders/`. Each is one function:

```python
@grader("my_check")
def my_check(ctx, some_param: str, optional: int = 3):
    # ctx.workspace: a private copy of the final state (free to modify)
    # ctx.start: the starting state, for restoring files
    # ctx.baseline / ctx.final: path -> sha256 manifests
    # ctx.transcript: the agent's messages ([] if nothing ran, None if lost)
    # ctx.log, ctx.env, ctx.task
    return passed, "details shown in `evals show`"
```

`task.toml` parameters are checked against the function's signature when the task loads, so a misspelt parameter fails immediately.

**Built-in grader types:**

| Type | Passes when | Parameters |
|---|---|---|
| `command` | The command exits 0 in the grading copy. | `cmd`, `timeout` (300), `overlay`, `restore` |
| `files_unchanged` | Every starting file matching `paths` is byte-identical. New files are allowed unless `allow_new = false`. | `paths`, `allow_new` |
| `diff_scope` | Every changed file matches `allowed`. harnest's own files (`PROGRESS.md`, `DECISIONS.md`, …) and anything the starting repo's `.gitignore` ignores (such as a database the agent created while trying the app) don't count. | `allowed` |
| `file_equals` | The file's content equals `expected` after trimming whitespace. | `path`, `expected` |
| `no_peeking` | No tool call or log line touches eval material. | none |

Model-graded or human-reviewed graders can use the same interface. Record them as non-deterministic and calibrate them against your own judgments before letting them gate.
