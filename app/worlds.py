"""World folders: metadata, sessions, chat logs and state snapshots for the UI."""
import io
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

WORLDS_DIR = Path(os.environ.get("WORLDS_DIR", "/app/worlds"))
SETUP_KEYS = ["setting", "tone", "rules", "combat", "character", "ui", "scripts"]
_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def world_dir(wid: str) -> Path | None:
    if not _ID_RE.match(wid):
        return None
    d = WORLDS_DIR / wid
    return d if (d / "world.json").exists() else None


def _new_dir() -> Path:
    WORLDS_DIR.mkdir(parents=True, exist_ok=True)
    base = "w-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    d, n = WORLDS_DIR / base, 1
    while d.exists():
        n += 1
        d = WORLDS_DIR / f"{base}-{n}"
    d.mkdir()
    return d


def create() -> dict:
    d = _new_dir()
    for sub in ("rules", "scripts", "memory", ".chat"):
        (d / sub).mkdir()
    meta = {
        "name": "New world",
        "tagline": "",
        "genre": "",
        "status": "building",
        "setup": {k: False for k in SETUP_KEYS},
        "ui_wishes": [],
    }
    (d / "world.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return read_meta(d)


def read_meta(d: Path) -> dict:
    try:
        meta = json.loads((d / "world.json").read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError
    except (OSError, ValueError):
        meta = {"name": d.name, "status": "building", "broken": True}
    meta.setdefault("name", d.name)
    meta.setdefault("status", "building")
    meta.setdefault("setup", {})
    meta["id"] = d.name
    meta["updated"] = _last_activity(d)
    return meta


def _last_activity(d: Path) -> float:
    times = [p.stat().st_mtime for p in (d / ".chat").glob("*.jsonl")]
    return max(times, default=(d / "world.json").stat().st_mtime)


def list_worlds() -> list[dict]:
    if not WORLDS_DIR.exists():
        return []
    metas = [read_meta(d) for d in WORLDS_DIR.iterdir() if (d / "world.json").exists()]
    return sorted(metas, key=lambda m: m["updated"], reverse=True)


def delete(d: Path) -> None:
    shutil.rmtree(d)


# ---- export / import -------------------------------------------------------
# Claude sessions live in the claude-home volume and can't move between machines, so
# .sessions.json is left out; the GM rebuilds context from WORLD.md, recap and world.db.

def _exportable(rel: Path) -> bool:
    return rel.name != ".sessions.json" and "__pycache__" not in rel.parts


def export_zip(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            rel = p.relative_to(d)
            if p.is_file() and _exportable(rel):
                z.write(p, rel.as_posix())
    return buf.getvalue()


def import_zip(data: bytes) -> dict:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("Not a zip file.")
    names = [n for n in z.namelist() if not n.endswith("/")]
    # Accept both a zip of the world's contents and a zip of the world folder itself.
    prefix = next((n[: -len("world.json")] for n in sorted(names, key=len)
                   if n == "world.json" or n.endswith("/world.json")), None)
    if prefix is None or prefix.count("/") > 1:
        raise ValueError("This zip doesn't contain a Story Forge world (world.json not found).")
    d = _new_dir()
    root = d.resolve()
    try:
        for name in names:
            if not name.startswith(prefix):
                continue
            rel = Path(name[len(prefix):])
            target = (d / rel).resolve()
            if not target.is_relative_to(root) or not _exportable(rel):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
        (d / ".chat").mkdir(exist_ok=True)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return read_meta(d)


# ---- sessions (engine-owned) ----------------------------------------------

def read_sessions(d: Path) -> dict:
    try:
        return json.loads((d / ".sessions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_sessions(d: Path, sessions: dict) -> None:
    (d / ".sessions.json").write_text(json.dumps(sessions, indent=2), encoding="utf-8")


# ---- chat log --------------------------------------------------------------

def append_chat(d: Path, mode: str, entry: dict) -> dict:
    entry = {"ts": time.time(), **entry}
    (d / ".chat").mkdir(exist_ok=True)
    with open(d / ".chat" / f"{mode}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_chat(d: Path, mode: str) -> list[dict]:
    path = d / ".chat" / f"{mode}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ---- files & state for the side panels ------------------------------------

def list_files(d: Path) -> list[str]:
    files = []
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if p.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            files.append(rel.as_posix())
    return files


def read_state(d: Path) -> dict | None:
    db = d / "world.db"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row

        def entity(row):
            return {"id": row["id"], "type": row["type"], "name": row["name"], "data": json.loads(row["data"])}

        def related(src, kind):
            rows = con.execute(
                "SELECT e.* FROM relations r JOIN entities e ON e.id = r.dst "
                "WHERE r.src=? AND r.kind=? ORDER BY e.name", (src, kind)).fetchall()
            return [entity(r) for r in rows]

        row = con.execute("SELECT * FROM entities WHERE id='player'").fetchone()
        player = entity(row) if row else None
        locations = related("player", "located_in")
        quests = [entity(r) for r in con.execute("SELECT * FROM entities WHERE type='quest' ORDER BY name")]
        events = [r["text"] for r in con.execute("SELECT text FROM events ORDER BY id DESC LIMIT 5")]
        state = {
            "player": player,
            "location": locations[0] if locations else None,
            "inventory": related("player", "owns"),
            "quests": [q for q in quests if q["data"].get("status", "active") == "active"],
            "events": list(reversed(events)),
        }
        con.close()
        return state
    except sqlite3.Error:
        return None
