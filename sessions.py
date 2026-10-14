"""Saved conversations, so a session can be named and resumed later.

Sessions live outside the project, one folder per project:
    ~/.harnest/sessions/home-akshat-projects-myapp/<name or id>.json
($HARNEST_HOME replaces ~/.harnest when it is set.)

Every session autosaves after each turn under a timestamp id. /name gives it a
name (the file is renamed), /resume loads it back.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path


# HARNEST_HOME moves all of harnest's own state somewhere else, e.g. to keep
# scripted runs apart from your interactive sessions.
SESSIONS_ROOT = Path(os.environ.get("HARNEST_HOME") or Path.home() / ".harnest") / "sessions"


def _folder(root: Path) -> Path:
    return SESSIONS_ROOT / str(root).strip("/").replace("/", "-")


def _path(root: Path, session: dict) -> Path:
    return _folder(root) / f"{session['name'] or session['id']}.json"


def new_session() -> dict:
    now = datetime.now()
    return {"id": now.strftime("%Y%m%d-%H%M%S"), "name": None,
            "created": now.isoformat(timespec="seconds")}


def save(root: Path, session: dict, messages: list, tokens: dict, **extra) -> None:
    """Write the session. The system prompt is not saved -- it is rebuilt on resume."""

    folder = _folder(root)
    folder.mkdir(parents=True, exist_ok=True)

    data = {
        **session,
        **extra,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "tokens": tokens,
        "messages": [m for m in messages if m["role"] != "system"],
    }

    _path(root, session).write_text(json.dumps(data, indent=1), encoding="utf-8")


def load(root: Path, key: str) -> dict | None:
    """Find a session by name or by id."""

    folder = _folder(root)
    direct = folder / f"{key}.json"

    if direct.is_file():
        return json.loads(direct.read_text(encoding="utf-8"))

    for path in folder.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("id") == key:
            return data

    return None


def list_all(root: Path) -> list[dict]:
    """One row per saved session, most recently updated first."""

    rows = []
    for path in _folder(root).glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({
            "key": path.stem,
            "updated": data.get("updated", "").replace("T", " "),
            "messages": len(data.get("messages", [])),
        })

    return sorted(rows, key=lambda r: r["updated"], reverse=True)


def rename(root: Path, session: dict, name: str) -> str | None:
    """Give the session a name. Returns an error message, or None on success."""

    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")
    if not clean:
        return "Usage: /name <name>  (letters, digits, '.', '_' and '-')"

    new_path = _folder(root) / f"{clean}.json"

    if new_path.exists():
        other = json.loads(new_path.read_text(encoding="utf-8"))
        if other.get("id") != session["id"]:
            return f"A session called '{clean}' already exists. /resume it or pick another name."

    old_path = _path(root, session)
    session["name"] = clean

    if old_path.exists() and old_path != new_path:
        old_path.rename(new_path)

    return None
