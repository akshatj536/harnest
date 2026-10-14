"""The task list behind the verification loop.

The model splits a request into tasks with plan_tasks, each with a shell command
that proves the task is done. Only the harness can mark a task passed: it runs
the command and checks the exit code. The model never gets to say "done" itself.
"""


class TaskList:
    def __init__(self):
        self.tasks = []             # dicts: id, description, verify, status
        self.files_changed = False  # set by the loop on file_write / file_edit

    # --------------------------------------------------------
    # Tools
    # --------------------------------------------------------

    def plan(self, tasks) -> str:
        """plan_tasks: replace the plan. Every task starts unverified."""

        if not isinstance(tasks, list) or not tasks:
            return "[plan_tasks needs a non-empty list of tasks]"

        planned, seen = [], set()

        for i, task in enumerate(tasks, 1):
            if not isinstance(task, dict):
                return f"[Task {i} is not an object with id, description and verify]"

            task_id = str(task.get("id") or f"t{i}").strip()
            verify = str(task.get("verify") or "").strip()

            if not verify:
                return (f"[Task {task_id} has no verify command. Every task needs a shell "
                        f"command that exits 0 only when the task is really done.]")
            if task_id in seen:
                return f"[Duplicate task id {task_id}]"
            seen.add(task_id)

            planned.append({
                "id": task_id,
                "description": str(task.get("description") or "").strip(),
                "verify": verify,
                "status": "pending",
            })

        self.tasks = planned
        return (f"[Planned {len(planned)} tasks. Do each one, then call verify_task "
                f"with its id.]\n{self.render()}")

    def verify(self, task_id: str, runner) -> str:
        """verify_task: run the task's command. runner(cmd) -> (exit code or None, output)."""

        task = next((t for t in self.tasks if t["id"] == str(task_id)), None)

        if task is None:
            known = ", ".join(t["id"] for t in self.tasks) or "none -- call plan_tasks first"
            return f"[No task with id {task_id!r}. Known ids: {known}]"

        return self._run(task, runner)

    # --------------------------------------------------------
    # Harness side
    # --------------------------------------------------------

    def recheck(self, runner) -> None:
        """Re-run every verify command. A later edit can break a task that passed earlier."""
        for task in self.tasks:
            self._run(task, runner)

    def problem(self) -> str | None:
        """Why the work cannot be accepted yet, or None if it can."""

        if self.tasks:
            open_tasks = [t for t in self.tasks if t["status"] != "passed"]
            if not open_tasks:
                return None
            lines = "\n".join(f"- {t['id']} ({t['status']}): {t['description']}  "
                              f"$ {t['verify']}" for t in open_tasks)
            return ("Not done yet. These tasks have not passed their verify command:\n"
                    f"{lines}\n\n"
                    "Fix the work, then call verify_task for each one. Change a verify command "
                    "(by calling plan_tasks again) only if the command itself is wrong -- never "
                    "to make a failing check pass. If you truly cannot finish, say exactly what "
                    "is blocking you.")

        if self.files_changed:
            return ("You changed files but never called plan_tasks, so nothing has been "
                    "verified. Call plan_tasks with a verify command for each part of the "
                    "request, then call verify_task for each one.")

        return None

    def render(self) -> str:
        marks = {"passed": "[x]", "failed": "[!]", "pending": "[ ]"}
        return "\n".join(f"{marks[t['status']]} {t['id']}: {t['description']}  $ {t['verify']}"
                         for t in self.tasks) or "(no tasks planned)"

    @staticmethod
    def _run(task: dict, runner) -> str:
        code, output = runner(task["verify"])
        task["status"] = "passed" if code == 0 else "failed"

        if code == 0:
            verdict = "PASSED"
        elif code is None:
            verdict = "FAILED (timed out)"
        else:
            verdict = f"FAILED (exit {code})"

        return f"[{task['id']} {verdict}] $ {task['verify']}\n{output}"
