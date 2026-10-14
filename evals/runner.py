"""Running trials, and checking that tasks and their graders are sound.

One trial:
  1. build a fresh workspace in /tmp (repo + setup patch + baseline commit)
  2. run the agent on it
  3. snapshot what changed
  4. build a second, untouched copy of the starting state (for restoring files)
  5. run every grader on its own copy of the final workspace
  6. write the artifacts and return one record
"""

import json
import shutil
import tempfile
from pathlib import Path

from .agents import NullAgent, PatchAgent
from .environment import build_workspace, compare, manifest, run_shell, text_diff, trial_env
from .graders import run_graders
from .task import prompt_problems


def _verdict(status: str, grades: list) -> str:
    if status == "invalid":
        return "invalid"
    return "pass" if all(g.passed for g in grades if g.gating) else "fail"


def run_trial(task, agent, trial_dir: Path, keep_workspace: bool = True) -> dict:
    trial_dir.mkdir(parents=True, exist_ok=True)

    # Neutral name: the agent can see this path, so it must not say "eval".
    side = Path(tempfile.mkdtemp(prefix="ws-"))
    refs = None

    try:
        workspace = side / "project"
        baseline_sha = build_workspace(task, workspace)
        baseline = manifest(workspace)
        env = trial_env(side / "home")

        run = agent.run(task, workspace, trial_dir, env)

        final = manifest(workspace)
        changes = compare(baseline, final)

        # Created only after the agent has finished, so it never existed while
        # the agent could look around.
        refs = Path(tempfile.mkdtemp(prefix="grading-"))
        start = refs / "start"
        start_sha = build_workspace(task, start)
        if start_sha != baseline_sha:
            raise RuntimeError(f"{task.id}: starting state is not reproducible "
                               f"({baseline_sha[:10]} vs {start_sha[:10]})")

        grades = run_graders(task, workspace, start, baseline, final, run.transcript,
                             run.log, env, refs / "grading")
        verdict = _verdict(run.status, grades)

        record = {
            "task": task.id,
            "agent": agent.name,
            "verdict": verdict,
            "status": run.status,
            "error": run.error,
            "seconds": run.seconds,
            "tokens": run.tokens,
            "hit_max_steps": bool((run.result or {}).get("hit_max_steps")),
            "self_verified": (run.result or {}).get("unverified") is None if run.result else None,
            "baseline_sha": baseline_sha,
            "files_changed": changes.all,
            "graders": [{"id": g.id, "passed": g.passed, "gating": g.gating, "role": g.role,
                         "error": g.error} for g in grades],
            "config": (run.result or {}).get("config"),
        }

        (trial_dir / "grades.json").write_text(
            json.dumps([g.to_dict() for g in grades], indent=1), encoding="utf-8")
        (trial_dir / "diff.patch").write_text(text_diff(start, workspace, changes), encoding="utf-8")
        (trial_dir / "record.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        if keep_workspace:
            shutil.copytree(workspace, trial_dir / "workspace", symlinks=True,
                            ignore=shutil.ignore_patterns(".git"))

        record["grades"] = grades   # full results, for callers in this process
        return record

    finally:
        shutil.rmtree(side, ignore_errors=True)
        if refs is not None:
            shutil.rmtree(refs, ignore_errors=True)


# ============================================================
# validate: are the task and its graders sound?
# ============================================================

def _grade_line(record: dict) -> str:
    return ", ".join(f"{g.id} {'✓' if g.passed else '✗'}" for g in record["grades"])


def validate_task(task) -> tuple[list[str], list[str]]:
    """Run the null, solution and known-bad agents. Returns (report lines, problems).

    The rules (see evals/README.md):
      starting state  -> FAIL; every outcome grader fails, every guard passes
      solution.patch  -> PASS; every grader passes
      known_bad/*     -> FAIL
    """

    lines, problems = [], []
    problems += [f"{task.id}: {p}" for p in prompt_problems(task)]

    with tempfile.TemporaryDirectory(prefix="validate-") as tmp:
        tmp = Path(tmp)

        start = run_trial(task, NullAgent(), tmp / "start", keep_workspace=False)
        ok = start["verdict"] == "fail"
        lines.append(f"  starting state      {start['verdict'].upper():<5} "
                     f"{'ok' if ok else 'WRONG, must FAIL'}   ({_grade_line(start)})")
        if not ok:
            problems.append(f"{task.id}: the starting state already passes")
        for g in start["grades"]:
            if g.error:
                problems.append(f"{task.id}: grader {g.id} crashed on the starting state: {g.details}")
            elif g.role == "outcome" and g.passed:
                problems.append(f"{task.id}: outcome grader {g.id} already passes on the starting "
                                f"state, so it cannot tell done from not done")
            elif g.role == "guard" and not g.passed:
                problems.append(f"{task.id}: guard grader {g.id} fails on the untouched starting "
                                f"state: {g.details.splitlines()[0] if g.details else ''}")

        if task.solution is None:
            problems.append(f"{task.id}: no solution.patch, so nobody has shown the task is solvable")
        else:
            sol = run_trial(task, PatchAgent(task.solution), tmp / "solution", keep_workspace=False)
            ok = sol["verdict"] == "pass" and sol["status"] == "completed"
            lines.append(f"  solution            {sol['verdict'].upper():<5} "
                         f"{'ok' if ok else 'WRONG, must PASS'}   ({_grade_line(sol)})")
            if sol["status"] != "completed":
                problems.append(f"{task.id}: {sol['error']}")
            for g in sol["grades"]:
                if not g.passed:
                    problems.append(f"{task.id}: grader {g.id} fails on the solution: "
                                    f"{g.details[-600:]}")

        if not task.known_bad:
            problems.append(f"{task.id}: no known_bad/*.patch, so nothing shows the graders "
                            f"catch a plausible wrong answer")
        for patch in task.known_bad:
            bad = run_trial(task, PatchAgent(patch), tmp / f"bad-{patch.stem}", keep_workspace=False)
            caught = [g.id for g in bad["grades"] if g.gating and not g.passed]
            ok = bad["status"] == "completed" and bad["verdict"] == "fail"
            lines.append(f"  known_bad/{patch.stem:<22} {bad['verdict'].upper():<5} "
                         f"{'ok' if ok else 'WRONG, must FAIL'}   "
                         f"(caught by {', '.join(caught) or 'nothing'})")
            if bad["status"] != "completed":
                problems.append(f"{task.id}: known_bad/{patch.name}: {bad['error']}")
            elif bad["verdict"] != "fail":
                problems.append(f"{task.id}: known_bad/{patch.name} passes -- the graders miss it")

    return lines, problems


def validate_repo(repo) -> tuple[str, str | None]:
    """Run a repo's own check on a clean copy. Returns (report line, problem or None)."""
    if not repo.check:
        return f"  {repo.name:<20} no check", None

    with tempfile.TemporaryDirectory(prefix="repo-") as tmp:
        copy = Path(tmp) / "project"
        shutil.copytree(repo.path, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        code, output = run_shell(repo.check, copy, trial_env(Path(tmp) / "home"), timeout=300)

    if code == 0:
        return f"  {repo.name:<20} check passes   ($ {repo.check})", None
    return (f"  {repo.name:<20} CHECK FAILS    ($ {repo.check})",
            f"repo {repo.name}: its check fails on a clean copy:\n{output[-1500:]}")
