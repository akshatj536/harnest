"""Graders that check the files the agent left behind."""

import shutil
from fnmatch import fnmatchcase
from pathlib import Path

from ..environment import compare, ignored_by_repo, run_shell
from . import grader

# Files harnest itself writes into a project. They never count as changes.
HARNESS_ARTIFACTS = [
    "PROGRESS.md", "DECISIONS.md", "background.log", "screenshot.png",
    "*__pycache__*", "*.pyc", ".pytest_cache/*", ".browser-profile/*",
]


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _matches(path: str, globs: list[str]) -> bool:
    # fnmatch's * also matches "/", so "tests/**" covers everything under tests/.
    return any(fnmatchcase(path, g) for g in globs)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _tail(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "[...]\n" + text[-limit:]


@grader("command")
def command(ctx, cmd: str, timeout: int = 300, overlay=None, restore=None):
    """Pass if `cmd` exits 0 in the grading copy.

    restore: paths reset to the starting state first -- so the agent cannot make
             a check pass by editing the tests or the test config.
    overlay: folders from the task (e.g. hidden_tests) copied in first.
    """

    for rel in _as_list(restore):
        target = ctx.workspace / rel
        _remove(target)
        source = ctx.start / rel
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        elif source.exists():
            shutil.copy2(source, target)

    for rel in _as_list(overlay):
        source = ctx.task.dir / rel
        target = ctx.workspace / Path(rel).name
        _remove(target)   # never mix in anything the agent put there
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    code, output = run_shell(cmd, ctx.workspace, ctx.env, timeout)
    status = "timed out" if code is None else f"exit {code}"
    return code == 0, f"$ {cmd}\n[{status}]\n{_tail(output)}"


@grader("files_unchanged")
def files_unchanged(ctx, paths: list[str], allow_new: bool = True):
    """Pass if every starting file matching `paths` is still there, byte for byte.

    New files are fine (an agent adding its own tests is good practice) unless
    allow_new is false.
    """
    candidates = set(ctx.baseline) | (set() if allow_new else set(ctx.final))
    changed = [p for p in sorted(candidates)
               if _matches(p, paths) and ctx.baseline.get(p) != ctx.final.get(p)]
    if changed:
        return False, "changed: " + ", ".join(changed)
    return True, f"unchanged: {', '.join(paths)}"


@grader("diff_scope")
def diff_scope(ctx, allowed: list[str]):
    """Pass if every changed, added or deleted file matches `allowed`.

    harnest's own files, and files the project's .gitignore ignores (e.g. a
    database the agent created while trying the app), don't count. The ignore
    rules come from the starting state, so editing .gitignore doesn't help.
    """
    changes = compare(ctx.baseline, ctx.final)
    candidates = [p for p in changes.all if not _matches(p, HARNESS_ARTIFACTS)]
    ignored = ignored_by_repo(ctx.start, candidates)
    touched = [p for p in candidates if p not in ignored]
    note = f" (ignored by .gitignore: {', '.join(sorted(ignored))})" if ignored else ""

    outside = [p for p in touched if not _matches(p, allowed)]
    if outside:
        return False, "changed outside the expected files: " + ", ".join(outside) + note
    return True, "changed: " + (", ".join(touched) or "nothing") + note


@grader("file_equals")
def file_equals(ctx, path: str, expected: str):
    """Pass if the file's content equals `expected`, ignoring surrounding whitespace."""
    target = ctx.workspace / path
    if not target.is_file():
        return False, f"{path} does not exist"
    actual = target.read_text(encoding="utf-8", errors="replace").strip()
    if actual == expected.strip():
        return True, f"{path} = {actual!r}"
    return False, f"{path}: expected {expected.strip()!r}, got {actual[:300]!r}"
