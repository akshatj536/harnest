"""Workspaces and environment for a trial: an identical starting state every time.

build_workspace() turns a task into a fresh git repo whose only commit is the
starting state. The author, committer and date are fixed, so the commit SHA is
the same in every trial, and the setup patch is applied before that commit, so
`git log` cannot reveal an injected bug.
"""

import difflib
import hashlib
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Neutral identity: nothing in the workspace should hint that this is a test.
NEUTRAL_GIT = {
    "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@example.com",
}
FIXED_DATE = "2026-01-05T09:00:00+00:00"

COPY_IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")


# ============================================================
# Git
# ============================================================

def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(NEUTRAL_GIT)
    env.update({
        "GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE,
        # Ignore the user's own git config (signing, hooks, templates...).
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    })
    return env


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=" + os.devnull, *args],
        cwd=cwd, env=_git_env(), capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}:\n{proc.stderr.strip()}")
    return proc


def ignored_by_repo(repo: Path, paths: list[str]) -> set[str]:
    """Which of `paths` the .gitignore rules of `repo` (a git work tree) ignore.

    Uses only the repo's own rules: the user's global git config is switched off.
    """
    if not paths or not (repo / ".git").exists():
        return set()
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=repo, env=_git_env(), input="\0".join(paths) + "\0",
        capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):   # 1 just means "nothing is ignored"
        raise RuntimeError(f"git check-ignore failed in {repo}:\n{proc.stderr.strip()}")
    return {p for p in proc.stdout.split("\0") if p}


def apply_patch(patch: Path, cwd: Path) -> str | None:
    """Apply a patch to a git working tree. Returns an error message, or None."""
    proc = git(["apply", "--whitespace=nowarn", str(patch)], cwd, check=False)
    if proc.returncode != 0:
        return proc.stderr.strip() or f"git apply exited {proc.returncode}"
    return None


def build_workspace(task, dest: Path, apply_setup: bool = True) -> str:
    """Create the task's starting state in `dest` (which must not exist).

    Returns the SHA of the single baseline commit.
    """

    shutil.copytree(task.repo.path, dest, ignore=COPY_IGNORE, symlinks=True)
    git(["init", "-q"], dest)

    if apply_setup and task.setup_patch:
        error = apply_patch(task.setup_patch, dest)
        if error:
            raise RuntimeError(f"{task.id}: setup patch does not apply:\n{error}")

    git(["add", "-A"], dest)
    git(["commit", "-q", "--allow-empty", "-m", "Initial commit"], dest)

    if git(["status", "--porcelain"], dest).stdout.strip():
        raise RuntimeError(f"{task.id}: workspace is not clean after the baseline commit")

    return git(["rev-parse", "HEAD"], dest).stdout.strip()


# ============================================================
# Manifests: what changed, without trusting the agent's .git
# ============================================================

def manifest(root: Path) -> dict[str, str]:
    """Map every file's relative path to a hash of its contents. Skips .git."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = "symlink:" + os.readlink(path)
            else:
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(out.items()))


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def all(self) -> list[str]:
        return sorted(self.added + self.removed + self.modified)


def compare(before: dict, after: dict) -> Changes:
    return Changes(
        added=sorted(set(after) - set(before)),
        removed=sorted(set(before) - set(after)),
        modified=sorted(p for p in set(before) & set(after) if before[p] != after[p]),
    )


def _lines(path: Path) -> list[str] | None:
    """A file's lines for diffing, or None if it is missing or binary."""
    if not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def text_diff(start: Path, final: Path, changes: Changes) -> str:
    """A unified diff of every changed file, for people to read."""
    out = []
    for rel in changes.all:
        before, after = _lines(start / rel), _lines(final / rel)
        if before is None or after is None:
            out.append(f"Binary file {rel} changed\n")
            continue
        out.extend(difflib.unified_diff(
            before, after,
            fromfile="/dev/null" if rel in changes.added else f"a/{rel}",
            tofile="/dev/null" if rel in changes.removed else f"b/{rel}",
        ))
        if out and not out[-1].endswith("\n"):
            out.append("\n")
    return "".join(out)


# ============================================================
# Environment and processes
# ============================================================

def trial_env(home: Path) -> dict:
    """The environment the agent and the graders run in."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}}
    env.update(NEUTRAL_GIT)
    env.update({
        "HARNEST_HOME": str(home),
        # The interpreter running the evals (normally the venv) comes first, so
        # `python` and `pytest` mean the same thing to the agent and the graders.
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    return env


def kill_group(proc: subprocess.Popen, sig: int = signal.SIGKILL) -> None:
    """Signal a process and everything it started (it must own its session)."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run_shell(cmd: str, cwd: Path, env: dict, timeout: int) -> tuple[int | None, str]:
    """Run a shell command. Returns (exit code or None on timeout, output)."""
    proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        kill_group(proc)
        out, _ = proc.communicate()
        code = None
        out += f"\n[timed out after {timeout}s]"
    finally:
        kill_group(proc)   # anything the command left running in the background
    return code, out
