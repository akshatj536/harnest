"""Tasks: one coding problem each, in evals/tasks/<id>/.

    task.toml          what to run and how to grade it (see evals/README.md)
    prompt.md          exactly what the agent is asked -- never mentions grading
    setup.patch        optional: turns the repo into this task's starting state
    hidden_tests/      optional: copied in only at grading time
    solution.patch     a known-good answer (validate requires it to PASS)
    known_bad/*.patch  plausible wrong answers (validate requires them to FAIL)
"""

import inspect
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import TASKS_DIR
from .graders import GRADERS
from .repo import Repo, load_repo

ROLES = {"outcome", "guard"}

# Words that would tell the agent it is being evaluated. Prompts must read like
# an ordinary request from a user.
GIVEAWAYS = re.compile(r"\b(evals?|evaluation|grader|graders|grading|graded|benchmark|"
                       r"hidden[ _]tests?|solution\.patch|known[ _]bad)\b", re.IGNORECASE)


class TaskError(ValueError):
    pass


@dataclass
class GraderSpec:
    id: str
    type: str
    role: str        # "outcome": proves the work was done; "guard": nothing was broken
    gating: bool     # gating graders decide PASS/FAIL; the rest are reported only
    params: dict = field(default_factory=dict)


@dataclass
class Task:
    id: str
    dir: Path
    category: str
    difficulty: str
    description: str
    repo: Repo
    prompt: str
    timeout: int
    graders: list[GraderSpec]
    setup_patch: Path | None
    solution: Path | None
    known_bad: list[Path]


def _grader_spec(task_id: str, raw: dict) -> GraderSpec:
    raw = dict(raw)
    gid = raw.pop("id", None)
    gtype = raw.pop("type", None)
    role = raw.pop("role", None)
    gating = raw.pop("gating", True)

    if not gid or not gtype:
        raise TaskError(f"{task_id}: every grader needs an id and a type")
    if gtype not in GRADERS:
        raise TaskError(f"{task_id}: grader {gid} has unknown type {gtype!r} "
                        f"(known: {', '.join(sorted(GRADERS))})")
    if role not in ROLES:
        raise TaskError(f"{task_id}: grader {gid} needs role = \"outcome\" or \"guard\"")
    if not isinstance(gating, bool):
        raise TaskError(f"{task_id}: grader {gid}: gating must be true or false")

    # Catch misspelt or missing parameters now, not halfway through a run.
    signature = inspect.signature(GRADERS[gtype])
    accepted = [p for p in signature.parameters if p != "ctx"]
    unknown = [p for p in raw if p not in accepted]
    if unknown:
        raise TaskError(f"{task_id}: grader {gid} ({gtype}): unknown parameter(s) "
                        f"{', '.join(unknown)}; it takes {', '.join(accepted) or 'none'}")
    try:
        signature.bind(None, **raw)
    except TypeError as e:
        raise TaskError(f"{task_id}: grader {gid} ({gtype}): {e}") from None

    return GraderSpec(id=gid, type=gtype, role=role, gating=gating, params=raw)


def load_task(task_dir: Path) -> Task:
    toml_path = task_dir / "task.toml"
    if not toml_path.is_file():
        raise TaskError(f"No task.toml in {task_dir}")

    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    task_id = data.get("id")
    if task_id != task_dir.name:
        raise TaskError(f"{task_dir.name}: id in task.toml ({task_id!r}) must match the folder name")

    # Repo: a shared one from evals/repos/, or "./something" inside the task folder.
    repo_ref = data.get("repo")
    if not repo_ref:
        raise TaskError(f"{task_id}: task.toml needs repo = \"<name>\" or \"./<folder>\"")
    if repo_ref.startswith("./"):
        path = task_dir / repo_ref[2:]
        if not path.is_dir():
            raise TaskError(f"{task_id}: no repo folder {path}")
        repo = Repo(name=f"{task_id}/{repo_ref[2:]}", path=path)
    else:
        try:
            repo = load_repo(repo_ref)
        except ValueError as e:
            raise TaskError(f"{task_id}: {e}") from None

    prompt_path = task_dir / data.get("prompt", "prompt.md")
    if not prompt_path.is_file():
        raise TaskError(f"{task_id}: no prompt file {prompt_path.name}")

    setup_patch = None
    if data.get("setup_patch"):
        setup_patch = task_dir / data["setup_patch"]
        if not setup_patch.is_file():
            raise TaskError(f"{task_id}: no setup patch {setup_patch.name}")

    graders = [_grader_spec(task_id, g) for g in data.get("graders", [])]
    ids = [g.id for g in graders]
    if len(ids) != len(set(ids)):
        raise TaskError(f"{task_id}: grader ids must be unique")
    if not any(g.role == "outcome" and g.gating for g in graders):
        raise TaskError(f"{task_id}: needs at least one gating outcome grader")

    solution = task_dir / "solution.patch"

    return Task(
        id=task_id,
        dir=task_dir,
        category=data.get("category", ""),
        difficulty=data.get("difficulty", ""),
        description=data.get("description", ""),
        repo=repo,
        prompt=prompt_path.read_text(encoding="utf-8").strip(),
        timeout=int(data.get("timeout_seconds", 900)),
        graders=graders,
        setup_patch=setup_patch,
        solution=solution if solution.is_file() else None,
        known_bad=sorted((task_dir / "known_bad").glob("*.patch")),
    )


def prompt_problems(task: Task) -> list[str]:
    """Words in the prompt that would tell the agent it is being evaluated."""
    return [f"prompt mentions {m.group(0)!r}" for m in GIVEAWAYS.finditer(task.prompt)]


def list_tasks(ids: list[str] | None = None) -> list[Task]:
    folders = sorted(p for p in TASKS_DIR.iterdir() if (p / "task.toml").is_file())

    if ids:
        known = {p.name: p for p in folders}
        missing = [i for i in ids if i not in known]
        if missing:
            raise TaskError(f"Unknown task(s): {', '.join(missing)}. "
                            f"Known: {', '.join(known) or 'none'}")
        folders = [known[i] for i in ids]

    return [load_task(p) for p in folders]
