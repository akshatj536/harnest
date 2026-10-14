"""Starting repos: reusable projects that tasks build on.

    evals/repos/<name>/       copied verbatim into every trial's workspace, so it
                              holds only what a real project would
    evals/repos/<name>.toml   metadata, deliberately OUTSIDE the copied folder:

        description = "what the project is"
        check = "python -I -m pytest -q"   # must pass on a clean copy (optional)
        notes = "reuse seams for task authors: bug sites, missing features, ..."

A task can also bring its own repo (repo = "./repo" in task.toml).
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import REPOS_DIR


@dataclass
class Repo:
    name: str
    path: Path
    description: str = ""
    check: str = ""
    notes: str = ""


def load_repo(name: str) -> Repo:
    folder = REPOS_DIR / name
    meta = REPOS_DIR / f"{name}.toml"

    if not folder.is_dir():
        raise ValueError(f"No repo folder {folder}")
    if not meta.is_file():
        raise ValueError(f"Repo {name} has no {meta.name} next to its folder")

    data = tomllib.loads(meta.read_text(encoding="utf-8"))
    return Repo(name=name, path=folder,
                description=data.get("description", ""),
                check=data.get("check", ""),
                notes=data.get("notes", ""))


def list_repos() -> list[Repo]:
    return [load_repo(p.name) for p in sorted(REPOS_DIR.iterdir())
            if p.is_dir() and not p.name.startswith((".", "_"))]
