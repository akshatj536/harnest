"""Command line for the eval harness.

    python -m evals list                         repos and tasks
    python -m evals validate [--task ID ...]     check tasks and graders (no model calls)
    python -m evals run [--task ID ...] [--trials N] [--label NAME]
    python -m evals show RUN[/TASK/trial-N]      summary, or one trial's transcript
    python -m evals report RUN [RUN ...]         pool trials from several runs
    python -m evals start TASK DIR [--no-setup]  a task's starting state, for writing patches
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import HARNEST_ROOT, RUNS_DIR
from .agents import HarnestAgent
from .environment import build_workspace
from .repo import list_repos
from .report import render_summary
from .runner import run_trial, validate_repo, validate_task
from .task import TaskError, list_tasks


# ============================================================
# list
# ============================================================

def cmd_list(args) -> int:
    print("Repos (evals/repos/):")
    for repo in list_repos():
        print(f"  {repo.name:<20} {repo.description}")

    print("\nTasks (evals/tasks/):")
    for task in list_tasks():
        gating = sum(g.gating for g in task.graders)
        print(f"  {task.id:<28} {task.category:<12} repo={task.repo.name:<14} "
              f"{len(task.graders)} graders ({gating} gating), {len(task.known_bad)} known-bad")
    return 0


# ============================================================
# validate
# ============================================================

def _validate(task_ids: list[str] | None) -> list[str]:
    problems = []

    print("Repos:")
    for repo in list_repos():
        line, problem = validate_repo(repo)
        print(line)
        if problem:
            problems.append(problem)

    for task in list_tasks(task_ids):
        print(f"\n{task.id}  ({task.category}, repo {task.repo.name})")
        lines, task_problems = validate_task(task)
        print("\n".join(lines))
        problems += task_problems

    return problems


def cmd_validate(args) -> int:
    problems = _validate(args.task)
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll tasks valid: every starting state fails, every solution passes, "
          "every known-bad answer is caught.")
    return 0


# ============================================================
# run
# ============================================================

def _harness_version() -> dict:
    def git(*a):
        return subprocess.run(["git", *a], cwd=HARNEST_ROOT, capture_output=True,
                              text=True).stdout.strip()

    digest = hashlib.sha256()
    for path in sorted(HARNEST_ROOT.glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())

    return {
        "git_sha": git("rev-parse", "HEAD"),
        # Only harnest's own top-level modules count, not the evals.
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no", "--", ":(glob)*.py")),
        "files_hash": digest.hexdigest(),
    }


def cmd_run(args) -> int:
    tasks = list_tasks(args.task)

    if not args.skip_validate:
        print("Validating tasks first (no model calls)...\n")
        problems = _validate(args.task)
        if problems:
            print("\nNot running: fix these first (or pass --skip-validate):")
            for p in problems:
                print(f"  - {p}")
            return 1
        print()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + (f"-{args.label}" if args.label else "")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True)

    meta = {
        "run_id": run_id,
        "started": datetime.now().isoformat(timespec="seconds"),
        "harness": _harness_version(),
        "tasks": [t.id for t in tasks],
        "trials": args.trials,
        "argv": sys.argv[1:],
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    agent = HarnestAgent()
    records = []
    interrupted = False

    try:
        for task in tasks:
            for i in range(1, args.trials + 1):
                print(f"[{task.id}] trial {i}/{args.trials} ...", end=" ", flush=True)
                record = run_trial(task, agent, run_dir / task.id / f"trial-{i}")
                record.pop("grades")
                record["trial"] = i
                records.append(record)
                with open(run_dir / "results.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")

                failed = [g["id"] for g in record["graders"] if g["gating"] and not g["passed"]]
                print(f"{record['verdict'].upper()}  {record['seconds']:.0f}s  "
                      f"{record['tokens']['input']:,} in / {record['tokens']['output']:,} out"
                      + (f"  (failed: {', '.join(failed)})" if failed else "")
                      + (f"  [{record['status']}: {record['error']}]"
                         if record["status"] != "completed" else ""))
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted -- summarising the trials that finished.")

    configs = [r["config"] for r in records if r.get("config")]
    meta.update({
        "finished": datetime.now().isoformat(timespec="seconds"),
        "interrupted": interrupted,
        "model": configs[0].get("model") if configs else None,
        "agent_config": configs[0] if configs else None,
    })
    (run_dir / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    if records:
        summary = render_summary(meta, records)
        (run_dir / "summary.md").write_text(summary, encoding="utf-8")
        print("\n" + summary)
    print(f"Results: {run_dir}")
    return 1 if interrupted else 0


# ============================================================
# show
# ============================================================

def _resolve_run_path(arg: str) -> Path:
    path = Path(arg)
    if not path.exists():
        path = RUNS_DIR / arg
    if not path.exists():
        raise SystemExit(f"No run or trial at {arg}")
    return path


def cmd_show(args) -> int:
    path = _resolve_run_path(args.path)

    if (path / "summary.md").is_file():
        print((path / "summary.md").read_text(encoding="utf-8"))
        return 0

    if not (path / "record.json").is_file():
        raise SystemExit(f"{path} is neither a run nor a trial folder")

    record = json.loads((path / "record.json").read_text(encoding="utf-8"))
    print(f"{record['task']}  {path.name}:  {record['verdict'].upper()}  ({record['status']}, "
          f"{record['seconds']:.0f}s, {record['tokens']['input']:,} in / "
          f"{record['tokens']['output']:,} out, {record['tokens']['calls']} API calls)")
    if record.get("error"):
        print(f"error: {record['error']}")

    print("\n=== Graders ===")
    for g in json.loads((path / "grades.json").read_text(encoding="utf-8")):
        mark = "✓" if g["passed"] else "✗"
        print(f"{mark} {g['id']}  ({g['type']}, {g['role']}, "
              f"{'gating' if g['gating'] else 'reported only'})")
        if not g["passed"] or args.verbose:
            print("    " + g["details"].replace("\n", "\n    "))

    print("\n=== Diff ===")
    diff = (path / "diff.patch").read_text(encoding="utf-8")
    print(diff or "(no changes)")

    transcript = path / "transcript.json"
    if transcript.is_file():
        sys.path.insert(0, str(HARNEST_ROOT))
        from compaction import render_transcript
        data = json.loads(transcript.read_text(encoding="utf-8"))
        print("\n=== Transcript ===")
        print(render_transcript(data.get("messages", [])))
    else:
        print("\n(no transcript)")
    return 0


# ============================================================
# report: combine runs into one summary
# ============================================================

def cmd_report(args) -> int:
    """Pool the trials of several runs, e.g. 1 earlier trial + 2 new ones = 3 per task.

    Only runs of the same harnest code (files hash) and model are pooled, since
    otherwise the trials are not measuring the same thing.
    """

    runs = [_resolve_run_path(r) for r in args.runs]
    metas = [json.loads((r / "run.json").read_text(encoding="utf-8")) for r in runs]

    hashes = {m["harness"]["files_hash"] for m in metas}
    models = {m.get("model") for m in metas if m.get("model")}
    if (len(hashes) > 1 or len(models) > 1) and not args.force:
        print("These runs used different harnest code or models, so their trials can't be pooled:")
        for r, m in zip(runs, metas):
            print(f"  {r.name}: files hash {m['harness']['files_hash'][:10]}, model {m.get('model')}")
        print("Pass --force to pool them anyway.")
        return 1

    records, counts = [], {}
    for r in runs:
        for line in (r / "results.jsonl").read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            counts[record["task"]] = counts.get(record["task"], 0) + 1
            record["trial"] = f"{counts[record['task']]} ({r.name}/trial-{record['trial']})"
            records.append(record)

    if not records:
        print("No trials in these runs.")
        return 1

    meta = {**metas[0],
            "run_id": " + ".join(r.name for r in runs),
            "trials": "/".join(str(n) for n in sorted(set(counts.values())))}
    summary = render_summary(meta, sorted(records, key=lambda r: r["task"]))
    print(summary)

    if args.out:
        Path(args.out).write_text(summary, encoding="utf-8")
        print(f"Saved to {args.out}")
    return 0


# ============================================================
# start
# ============================================================

def cmd_start(args) -> int:
    [task] = list_tasks([args.task])
    dest = Path(args.dir).expanduser().resolve()
    if dest.exists():
        raise SystemExit(f"{dest} already exists")

    sha = build_workspace(task, dest, apply_setup=not args.no_setup)
    print(f"Created {'the base repo' if args.no_setup else 'the starting state'} of "
          f"{task.id} in {dest} (commit {sha[:10]}).")
    print("Edit it, then save your changes as a patch with:")
    print(f"  git -C {dest} add -A && git -C {dest} diff --cached > PATCH_FILE")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description="harnest eval harness")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list repos and tasks")

    p = sub.add_parser("validate", help="check every task and grader, without calling a model")
    p.add_argument("--task", action="append", help="only this task (repeatable)")

    p = sub.add_parser("run", help="run harnest on tasks")
    p.add_argument("--task", action="append", help="only this task (repeatable)")
    p.add_argument("--trials", type=int, default=3, help="trials per task (default 3)")
    p.add_argument("--label", help="added to the run folder's name")
    p.add_argument("--skip-validate", action="store_true", help="don't validate tasks first")

    p = sub.add_parser("show", help="print a run's summary, or one trial in detail")
    p.add_argument("path", help="RUN_ID, RUN_ID/TASK/trial-N, or a path")
    p.add_argument("-v", "--verbose", action="store_true", help="show details of passing graders too")

    p = sub.add_parser("report", help="pool the trials of several runs into one summary")
    p.add_argument("runs", nargs="+", help="run ids or paths")
    p.add_argument("--out", help="also save the summary to this file")
    p.add_argument("--force", action="store_true",
                   help="pool runs even if the harnest code or model differs")

    p = sub.add_parser("start", help="create a task's starting state, for writing patches")
    p.add_argument("task")
    p.add_argument("dir")
    p.add_argument("--no-setup", action="store_true", help="the base repo, without setup.patch")

    args = parser.parse_args()
    if getattr(args, "trials", 1) < 1:
        parser.error("--trials must be at least 1")

    handlers = {"list": cmd_list, "validate": cmd_validate, "run": cmd_run,
                "show": cmd_show, "report": cmd_report, "start": cmd_start}
    try:
        return handlers[args.command](args)
    except TaskError as e:
        print(f"Task error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
