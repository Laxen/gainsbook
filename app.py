import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)
DATA_DIR = Path(os.environ.get("DATA_DIR", "/share/gainsbook"))


class _IngressMiddleware:
    """Set SCRIPT_NAME from the X-Ingress-Path header sent by Home Assistant.

    This makes Flask's url_for() produce URLs that are relative to the
    ingress base path (e.g. /api/hassio_ingress/<token>/) instead of /.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        ingress_path = environ.get("HTTP_X_INGRESS_PATH", "").rstrip("/")
        if ingress_path:
            environ["SCRIPT_NAME"] = ingress_path
            path_info = environ.get("PATH_INFO", "")
            if path_info.startswith(ingress_path):
                environ["PATH_INFO"] = path_info[len(ingress_path):] or "/"
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _IngressMiddleware(app.wsgi_app)


# ── storage helpers ───────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load(filename: str, default):
    _ensure_dir()
    p = DATA_DIR / filename
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _save(filename: str, data) -> None:
    _ensure_dir()
    (DATA_DIR / filename).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_exercises():
    return _load("exercises.json", [])


def save_exercises(data):
    _save("exercises.json", data)


def load_routines():
    return _load("routines.json", [])


def save_routines(data):
    _save("routines.json", data)


def load_sessions():
    return _load("sessions.json", [])


def save_sessions(data):
    _save("sessions.json", data)


def load_drafts():
    return _load("drafts.json", {})


def save_drafts(data):
    _save("drafts.json", data)


# ── lookup helpers ────────────────────────────────────────────────────────────

def get_routine_by_id(routines, r_id):
    return next((r for r in routines if r["id"] == r_id), None)


def last_session_for_exercise(sessions: list, exercise_id: str) -> dict | None:
    """Return the most recent session entry for the given exercise, or None."""
    for session in reversed(sessions):
        for entry in session.get("entries", []):
            if entry["exercise_id"] == exercise_id:
                return {
                    "reps": entry["reps"],
                    "comment": entry.get("comment", ""),
                    "date": session["date"],
                }
    return None


# ── routes: home ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    routines = load_routines()
    exercises = load_exercises()
    ex_map = {e["id"]: e["name"] for e in exercises}
    drafts = load_drafts()
    return render_template("index.html", routines=routines, ex_map=ex_map, drafts=drafts)


# ── routes: exercises ─────────────────────────────────────────────────────────

@app.route("/exercises", methods=["GET", "POST"])
def exercises():
    data = load_exercises()
    message = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            data.append({"id": str(uuid.uuid4()), "name": name})
            save_exercises(data)
            message = f'Exercise "{name}" added.'
        else:
            message = "Name cannot be empty."
    return render_template("exercises.html", exercises=data, message=message)


@app.route("/exercises/<ex_id>/delete", methods=["POST"])
def delete_exercise(ex_id):
    data = load_exercises()
    data = [e for e in data if e["id"] != ex_id]
    save_exercises(data)
    # also remove from all routines
    routines = load_routines()
    for r in routines:
        r["exercise_ids"] = [eid for eid in r["exercise_ids"] if eid != ex_id]
    save_routines(routines)
    return redirect(url_for("exercises"))


# ── routes: routines ──────────────────────────────────────────────────────────

@app.route("/routines", methods=["GET", "POST"])
def routines():
    data = load_routines()
    exercises = load_exercises()
    message = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            data.append({"id": str(uuid.uuid4()), "name": name, "exercise_ids": []})
            save_routines(data)
            message = f'Routine "{name}" added.'
        else:
            message = "Name cannot be empty."
    ex_map = {e["id"]: e["name"] for e in exercises}
    return render_template(
        "routines.html", routines=data, exercises=exercises, ex_map=ex_map, message=message
    )


@app.route("/routines/<r_id>/delete", methods=["POST"])
def delete_routine(r_id):
    data = load_routines()
    data = [r for r in data if r["id"] != r_id]
    save_routines(data)
    return redirect(url_for("routines"))


@app.route("/routines/<r_id>/add_exercise", methods=["POST"])
def routine_add_exercise(r_id):
    ex_id = request.form.get("exercise_id", "").strip()
    if not ex_id:
        return redirect(url_for("routines"))
    data = load_routines()
    routine = get_routine_by_id(data, r_id)
    if routine and ex_id not in routine["exercise_ids"]:
        routine["exercise_ids"].append(ex_id)
        save_routines(data)
    return redirect(url_for("routines"))


@app.route("/routines/<r_id>/remove_exercise/<ex_id>", methods=["POST"])
def routine_remove_exercise(r_id, ex_id):
    data = load_routines()
    routine = get_routine_by_id(data, r_id)
    if routine:
        routine["exercise_ids"] = [eid for eid in routine["exercise_ids"] if eid != ex_id]
        save_routines(data)
    return redirect(url_for("routines"))


@app.route("/routines/<r_id>/move_exercise/<ex_id>/<direction>", methods=["POST"])
def routine_move_exercise(r_id, ex_id, direction):
    if direction not in ("up", "down"):
        return redirect(url_for("routines"))
    data = load_routines()
    routine = get_routine_by_id(data, r_id)
    if routine and ex_id in routine["exercise_ids"]:
        ids = routine["exercise_ids"]
        idx = ids.index(ex_id)
        if direction == "up" and idx > 0:
            ids[idx], ids[idx - 1] = ids[idx - 1], ids[idx]
        elif direction == "down" and idx < len(ids) - 1:
            ids[idx], ids[idx + 1] = ids[idx + 1], ids[idx]
        routine["exercise_ids"] = ids
        save_routines(data)
    return redirect(url_for("routines"))


# ── routes: workout ───────────────────────────────────────────────────────────

def _parse_form_entries(routine: dict) -> dict:
    """Read reps/comment fields from the current request form for all exercises."""
    entries = {}
    for ex_id in routine["exercise_ids"]:
        entries[ex_id] = {
            "reps_str": request.form.get(f"reps_{ex_id}", "").strip(),
            "comment": request.form.get(f"comment_{ex_id}", "").strip(),
        }
    return entries


def _entries_to_session(routine: dict, entries: dict) -> list:
    """Convert draft entries dict to the list format used in sessions.json."""
    result = []
    for ex_id in routine["exercise_ids"]:
        d = entries.get(ex_id, {})
        reps = []
        for part in d.get("reps_str", "").split():
            try:
                reps.append(int(part))
            except ValueError:
                pass
        result.append({"exercise_id": ex_id, "reps": reps, "comment": d.get("comment", "")})
    return result


@app.route("/workout/<r_id>", methods=["GET"])
def workout(r_id):
    all_routines = load_routines()
    routine = get_routine_by_id(all_routines, r_id)
    if not routine:
        return redirect(url_for("index"))

    exercises = load_exercises()
    sessions = load_sessions()
    drafts = load_drafts()

    ex_map = {e["id"]: e["name"] for e in exercises}
    draft_entries = drafts.get(r_id, {}).get("entries", {})
    last = {
        ex_id: last_session_for_exercise(sessions, ex_id)
        for ex_id in routine["exercise_ids"]
    }

    return render_template(
        "workout.html",
        routine=routine,
        ex_map=ex_map,
        last=last,
        draft_entries=draft_entries,
    )


@app.route("/workout/<r_id>/save", methods=["POST"])
def workout_save(r_id):
    """Save progress as a draft and return to the workout form (PRG)."""
    all_routines = load_routines()
    routine = get_routine_by_id(all_routines, r_id)
    if not routine:
        return redirect(url_for("index"))

    drafts = load_drafts()
    drafts[r_id] = {"entries": _parse_form_entries(routine)}
    save_drafts(drafts)
    return redirect(url_for("workout", r_id=r_id))


@app.route("/workout/<r_id>/finish", methods=["POST"])
def workout_finish(r_id):
    """Commit the workout to sessions.json, clear the draft, redirect home (PRG)."""
    all_routines = load_routines()
    routine = get_routine_by_id(all_routines, r_id)
    if not routine:
        return redirect(url_for("index"))

    entries = _entries_to_session(routine, _parse_form_entries(routine))
    sessions = load_sessions()
    sessions.append({
        "id": str(uuid.uuid4()),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "routine_id": r_id,
        "entries": entries,
    })
    save_sessions(sessions)

    drafts = load_drafts()
    drafts.pop(r_id, None)
    save_drafts(drafts)

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8098)
