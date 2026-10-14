"""Graders: functions that look at a finished trial and say pass or fail.

A grader type is one function registered with @grader("type"). It receives a
GradeContext plus the parameters from the task's task.toml, and returns
(passed, details). Every grader gets its OWN copy of the final workspace, so
graders cannot affect each other and their order does not matter.

To add a grader type: write a function in code.py or transcript.py (or a new
module imported at the bottom of this file) and use it from any task.toml.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

GRADERS: dict[str, Callable] = {}


def grader(type_name: str):
    """Register a grader type under the name used in task.toml."""
    def register(fn):
        GRADERS[type_name] = fn
        return fn
    return register


@dataclass
class GradeContext:
    task: object             # the Task being graded
    workspace: Path          # a private copy of the final state -- free to modify
    start: Path              # the starting state, for restoring files (do not modify)
    baseline: dict           # manifest of the starting state: path -> sha256
    final: dict              # manifest of the final state
    transcript: list | None  # the agent's messages; [] if nothing ran; None if lost
    log: str                 # everything the agent printed
    env: dict                # environment to run commands in


@dataclass
class GradeResult:
    id: str
    type: str
    role: str
    gating: bool
    passed: bool
    details: str
    error: bool = False      # the grader itself crashed (a bug in the task or grader)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


GRADING_COPY_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache")


def run_graders(task, final_dir: Path, start_dir: Path, baseline: dict, final: dict,
                transcript: list | None, log: str, env: dict, scratch: Path) -> list[GradeResult]:
    results = []

    for spec in task.graders:
        workspace = scratch / spec.id
        shutil.copytree(final_dir, workspace, ignore=GRADING_COPY_IGNORE, symlinks=True)

        ctx = GradeContext(task=task, workspace=workspace, start=start_dir, baseline=baseline,
                           final=final, transcript=transcript, log=log, env=env)

        started = time.monotonic()
        try:
            passed, details = GRADERS[spec.type](ctx, **spec.params)
            error = False
        except Exception as e:
            passed, details, error = False, f"grader crashed: {type(e).__name__}: {e}", True

        results.append(GradeResult(
            id=spec.id, type=spec.type, role=spec.role, gating=spec.gating,
            passed=bool(passed), details=details, error=error,
            seconds=round(time.monotonic() - started, 2),
        ))

    return results


# Register the built-in grader types.
from . import code, transcript  # noqa: E402,F401
