"""Evaluation harness for harnest. See evals/README.md."""

from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
HARNEST_ROOT = EVALS_DIR.parent

REPOS_DIR = EVALS_DIR / "repos"   # reusable starting projects
TASKS_DIR = EVALS_DIR / "tasks"   # one folder per task
RUNS_DIR = EVALS_DIR / "runs"     # results (gitignored)
