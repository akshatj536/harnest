"""Agents the eval harness can run on a task.

All agents share one method, run(task, workspace, trial_dir, env) -> AgentRun.

    HarnestAgent  the real thing: `python agent.py` as a black-box subprocess.
                  If harnest's command line changes, only this class changes.
    NullAgent     does nothing -- used to check the starting state FAILS.
    PatchAgent    applies a patch -- used to check the solution PASSES and the
                  known-bad answers FAIL, without calling any model.
"""

import json
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import HARNEST_ROOT
from .environment import apply_patch, kill_group

ZERO_TOKENS = {"input": 0, "cached": 0, "output": 0, "calls": 0}


@dataclass
class AgentRun:
    status: str                      # completed | crashed | timeout | invalid
    seconds: float = 0.0
    exit_code: int | None = None
    tokens: dict = field(default_factory=lambda: dict(ZERO_TOKENS))
    transcript: list | None = None   # [] if nothing ran, None if it was lost
    log: str = ""
    result: dict | None = None       # harnest's --result-file
    error: str | None = None


class NullAgent:
    name = "null"

    def run(self, task, workspace: Path, trial_dir: Path, env: dict) -> AgentRun:
        return AgentRun(status="completed", transcript=[])


class PatchAgent:
    def __init__(self, patch: Path):
        self.patch = patch
        self.name = f"patch:{patch.stem}"

    def run(self, task, workspace: Path, trial_dir: Path, env: dict) -> AgentRun:
        error = apply_patch(self.patch, workspace)
        if error:
            return AgentRun(status="crashed", transcript=[], error=f"patch does not apply: {error}")
        return AgentRun(status="completed", transcript=[])


class HarnestAgent:
    name = "harnest"

    # How long harnest gets to save its session after Ctrl+C before it is killed.
    GRACE_SECONDS = 20

    def __init__(self, agent_py: Path = HARNEST_ROOT / "agent.py", python: str = sys.executable):
        self.agent_py = agent_py
        self.python = python

    def run(self, task, workspace: Path, trial_dir: Path, env: dict) -> AgentRun:
        # The request and result live next to the workspace under neutral names,
        # never inside the eval folders, so nothing the agent can see mentions them.
        side = workspace.parent
        prompt_path = side / "request.md"
        result_path = side / "out.json"
        prompt_path.write_text(task.prompt + "\n", encoding="utf-8")

        log_path = trial_dir / "agent.log"
        cmd = [self.python, str(self.agent_py), str(workspace),
               "--prompt-file", str(prompt_path), "--result-file", str(result_path)]

        started = time.monotonic()
        timed_out = False

        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                proc.wait(timeout=task.timeout)
            except subprocess.TimeoutExpired:
                # Ctrl+C first: harnest closes the turn and saves its session.
                timed_out = True
                kill_group(proc, signal.SIGINT)
                try:
                    proc.wait(timeout=self.GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    kill_group(proc)
                    proc.wait()
            except KeyboardInterrupt:
                kill_group(proc)
                proc.wait()
                raise
            finally:
                kill_group(proc)   # servers or watchers the agent left running

        seconds = round(time.monotonic() - started, 1)
        log_text = log_path.read_text(encoding="utf-8", errors="replace")

        result = None
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            shutil.copy2(result_path, trial_dir / "result.json")

        session = self._find_session(Path(env["HARNEST_HOME"]), result)
        transcript = None
        if session is not None:
            shutil.copy2(session, trial_dir / "transcript.json")
            data = json.loads(session.read_text(encoding="utf-8"))
            transcript = data.get("messages")

        if result is not None:
            tokens = {**ZERO_TOKENS, **result.get("turn_tokens", {})}
        elif session is not None:
            tokens = {**ZERO_TOKENS, **data.get("tokens", {})}
        else:
            tokens = dict(ZERO_TOKENS)

        error = (result or {}).get("error")
        if timed_out:
            status = "timeout"
        elif result is None or error:
            status = "crashed"
            error = error or f"exited {proc.returncode} without writing a result"
        else:
            status = "completed"

        # Never reached the model (missing key, network down): not the agent's fault.
        if status != "completed" and tokens["calls"] == 0:
            status = "invalid"

        return AgentRun(status=status, seconds=seconds, exit_code=proc.returncode,
                        tokens=tokens, transcript=transcript, log=log_text,
                        result=result, error=error)

    @staticmethod
    def _find_session(home: Path, result: dict | None) -> Path | None:
        """The session file harnest saved under HARNEST_HOME."""
        files = sorted((home / "sessions").glob("*/*.json"))
        if result and result.get("session_id"):
            exact = [f for f in files if f.stem == result["session_id"]]
            if exact:
                return exact[0]
        return files[0] if len(files) == 1 else None
