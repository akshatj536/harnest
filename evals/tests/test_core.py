"""Tests for the eval harness itself (not for harnest). Run: python -m pytest evals/tests -q"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import EVALS_DIR
from evals.environment import build_workspace, compare, git, manifest
from evals.graders import GradeContext
from evals.graders.code import diff_scope, files_unchanged
from evals.graders.transcript import no_peeking
from evals.report import pass_at_k, pass_pow_k
from evals.runner import validate_task
from evals.task import TaskError, load_task, prompt_problems


# ============================================================
# pass@k / pass^k
# ============================================================

def test_pass_at_1_is_the_pass_rate():
    assert pass_at_k(4, 3, 1) == pytest.approx(0.75)
    assert pass_pow_k(4, 3, 1) == pytest.approx(0.75)


def test_pass_at_k_and_pass_pow_k_diverge():
    # 3 of 4 passed: some 3-trial draw always contains a pass, but only 1 of the
    # 4 possible draws is all passes.
    assert pass_at_k(4, 3, 3) == 1.0
    assert pass_pow_k(4, 3, 3) == pytest.approx(0.25)


def test_all_or_nothing():
    assert pass_at_k(3, 0, 3) == 0.0
    assert pass_pow_k(3, 3, 3) == 1.0


# ============================================================
# Workspaces and manifests
# ============================================================

def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    (repo / "README.md").write_text("hello\n")
    return repo


def fake_task(repo: Path, setup: Path | None = None):
    return SimpleNamespace(id="t", repo=SimpleNamespace(path=repo), setup_patch=setup)


def test_baseline_commit_is_identical_every_time(tmp_path):
    task = fake_task(make_repo(tmp_path))
    assert build_workspace(task, tmp_path / "one") == build_workspace(task, tmp_path / "two")


def test_setup_patch_is_not_visible_in_history(tmp_path):
    repo = make_repo(tmp_path)
    patch = tmp_path / "setup.patch"
    patch.write_text("--- a/pkg/a.py\n+++ b/pkg/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")

    ws = tmp_path / "ws"
    build_workspace(fake_task(repo, patch), ws)

    assert (ws / "pkg" / "a.py").read_text() == "x = 2\n"
    assert git(["rev-list", "--count", "HEAD"], ws).stdout.strip() == "1"
    assert "x = 1" not in git(["log", "-p"], ws).stdout


def test_compare_finds_added_removed_and_modified(tmp_path):
    before = {"a": "1", "b": "2", "c": "3"}
    after = {"a": "1", "b": "changed", "d": "4"}
    changes = compare(before, after)
    assert (changes.added, changes.removed, changes.modified) == (["d"], ["c"], ["b"])


def test_manifest_skips_git(tmp_path):
    ws = tmp_path / "ws"
    build_workspace(fake_task(make_repo(tmp_path)), ws)
    assert set(manifest(ws)) == {"README.md", "pkg/a.py"}


# ============================================================
# Graders
# ============================================================

def ctx(baseline=None, final=None, transcript=None, log=""):
    return GradeContext(task=None, workspace=Path("."), start=Path("."), baseline=baseline or {},
                        final=final or {}, transcript=transcript, log=log, env={})


def test_files_unchanged_allows_new_files_by_default():
    c = ctx({"tests/a.py": "1"}, {"tests/a.py": "1", "tests/new.py": "2"})
    assert files_unchanged(c, paths=["tests/*"])[0]
    assert not files_unchanged(c, paths=["tests/*"], allow_new=False)[0]


def test_files_unchanged_catches_edits_and_deletions():
    assert not files_unchanged(ctx({"tests/a.py": "1"}, {"tests/a.py": "2"}), paths=["tests/*"])[0]
    assert not files_unchanged(ctx({"tests/a.py": "1"}, {}), paths=["tests/*"])[0]


def test_diff_scope_ignores_harness_artifacts():
    c = ctx({}, {"PROGRESS.md": "x", "DECISIONS.md": "y", "roman.py": "z"})
    assert diff_scope(c, allowed=["roman.py"])[0]
    assert not diff_scope(ctx({}, {"setup.py": "x"}), allowed=["roman.py"])[0]


def test_diff_scope_ignores_what_the_project_gitignores(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("books.json\n")
    start = tmp_path / "start"
    build_workspace(fake_task(repo), start)
    baseline = manifest(start)

    c = GradeContext(task=None, workspace=start, start=start, baseline=baseline,
                     final={**baseline, "books.json": "x"}, transcript=[], log="", env={})
    assert diff_scope(c, allowed=["pkg/*"])[0]

    c.final = {**baseline, "notes.json": "x"}
    assert not diff_scope(c, allowed=["pkg/*"])[0]


def call(name, **args):
    import json
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "1", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_no_peeking():
    assert no_peeking(ctx(transcript=[]))[0]
    assert no_peeking(ctx(transcript=[call("file_read", path="roman.py")]))[0]
    assert not no_peeking(ctx(transcript=[call("run_command", command=f"cat {EVALS_DIR}/x")]))[0]
    assert not no_peeking(ctx(transcript=[call("run_command", command="find / -name hidden_tests")]))[0]
    assert not no_peeking(ctx(transcript=None))[0]   # lost transcript: cannot confirm
    assert not no_peeking(ctx(transcript=[], log="▸ file_read /x/solution.patch"))[0]


# ============================================================
# Task loading and validation
# ============================================================

def write_task(root: Path, toml: str, prompt: str = "Write roman.py.") -> Path:
    task_dir = root / "demo"
    (task_dir / "repo").mkdir(parents=True)
    (task_dir / "repo" / "README.md").write_text("demo\n")
    (task_dir / "prompt.md").write_text(prompt)
    (task_dir / "task.toml").write_text('id = "demo"\nrepo = "./repo"\n' + toml)
    return task_dir


ALWAYS_PASSES = '''
[[graders]]
id = "always"
type = "command"
role = "outcome"
cmd = "true"
'''


def test_unknown_grader_type_is_rejected(tmp_path):
    with pytest.raises(TaskError, match="unknown type"):
        load_task(write_task(tmp_path, '[[graders]]\nid = "x"\ntype = "nope"\nrole = "outcome"\n'))


def test_misspelt_grader_parameter_is_rejected(tmp_path):
    with pytest.raises(TaskError, match="cmnd"):
        load_task(write_task(tmp_path, '[[graders]]\nid = "x"\ntype = "command"\n'
                                       'role = "outcome"\ncmnd = "true"\n'))


def test_task_without_an_outcome_grader_is_rejected(tmp_path):
    with pytest.raises(TaskError, match="outcome"):
        load_task(write_task(tmp_path, '[[graders]]\nid = "x"\ntype = "no_peeking"\nrole = "guard"\n'))


def test_prompt_must_not_mention_grading(tmp_path):
    task = load_task(write_task(tmp_path, ALWAYS_PASSES, prompt="Make the hidden tests pass."))
    assert prompt_problems(task)


def test_validate_rejects_a_grader_that_passes_on_the_starting_state(tmp_path):
    task = load_task(write_task(tmp_path, ALWAYS_PASSES))
    _, problems = validate_task(task)
    assert any("already passes" in p for p in problems)
    assert any("no solution.patch" in p for p in problems)


def test_validate_rejects_a_known_bad_answer_the_graders_miss(tmp_path):
    task_dir = write_task(tmp_path, '''
[[graders]]
id = "has-file"
type = "command"
role = "outcome"
cmd = "test -f roman.py"
''')
    new_file = ("diff --git a/roman.py b/roman.py\nnew file mode 100644\n--- /dev/null\n"
                "+++ b/roman.py\n@@ -0,0 +1 @@\n+x = 1\n")
    (task_dir / "solution.patch").write_text(new_file)
    (task_dir / "known_bad").mkdir()
    (task_dir / "known_bad" / "also-creates-file.patch").write_text(new_file)

    _, problems = validate_task(load_task(task_dir))
    assert problems == ["demo: known_bad/also-creates-file.patch passes -- the graders miss it"]
