"""World state database (SQLite, ./world.db or $WORLD_DB).

Generic model that fits any world:
  entities  (id, type, name, data JSON)   - characters, npcs, items, locations, quests, quirks...
  relations (src, kind, dst, data JSON)   - player located_in tavern, player owns sword...
  events    (id, text, changes JSON)      - story log

CLI (run inside a world folder):  python3 -m db <command> ...
  init                                   create tables (safe to re-run)
  summary                                player, location, inventory, quests, recent events
  get <id>                               one entity with its relations
  list <type>                            all entities of a type
  find <text>                            search entities by id or name
  create <type> <id> <name> [json]       create entity; data is a JSON object
  set <id> key=value ...                 set data fields (dotted keys ok, values parsed as JSON); name=... renames
  unset <id> key ...                     remove data fields
  adjust <id> <key> <delta> [--min N] [--max N]   add to a numeric field, clamped
  delete <id>                            delete entity and its relations
  relate <src> <kind> <dst>              add relation
  unrelate <src> <kind> <dst>            remove relation
  move <id> <location_id>                replace the located_in relation
  related <id> [kind]                    entities that <id> points to
  give <owner> <item_id> [qty] [--name N] [--data JSON]   give item stack (created if missing)
  take <owner> <item_id> [qty]           remove qty; ownership dropped at 0
  log <text>                             record a story event
  events [n]                             last n events

Module use in world scripts:  from db import get, update, adjust, relate, log, ...
"""
import argparse
import builtins
import json
import os
import sqlite3
import sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE TABLE IF NOT EXISTS relations (
    src TEXT NOT NULL,
    kind TEXT NOT NULL,
    dst TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (src, kind, dst)
);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(dst, kind);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    changes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class NotFound(Exception):
    pass


_conn = None


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(os.environ.get("WORLD_DB", "world.db"))
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


# ---- helpers ---------------------------------------------------------------

def _entity(row):
    if row is None:
        return None
    return {"id": row["id"], "type": row["type"], "name": row["name"], "data": json.loads(row["data"])}


def _get_path(data, key):
    for part in key.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _set_path(data, key, value):
    parts = key.split(".")
    for part in parts[:-1]:
        if not isinstance(data.get(part), dict):
            data[part] = {}
        data = data[part]
    data[parts[-1]] = value


def _del_path(data, key):
    parts = key.split(".")
    for part in parts[:-1]:
        data = data.get(part)
        if not isinstance(data, dict):
            return
    data.pop(parts[-1], None)


def _save(entity):
    con = connect()
    con.execute(
        "UPDATE entities SET name=?, data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (entity["name"], json.dumps(entity["data"], ensure_ascii=False), entity["id"]),
    )
    con.commit()
    return entity


# ---- entities --------------------------------------------------------------

def get(eid):
    return _entity(connect().execute("SELECT * FROM entities WHERE id=?", (eid,)).fetchone())


def require(eid):
    entity = get(eid)
    if entity is None:
        raise NotFound(f"no entity with id {eid!r}")
    return entity


def list_type(type_):
    rows = connect().execute("SELECT * FROM entities WHERE type=? ORDER BY name", (type_,)).fetchall()
    return [_entity(r) for r in rows]


def find(text):
    like = f"%{text}%"
    rows = connect().execute(
        "SELECT * FROM entities WHERE id LIKE ? OR name LIKE ? ORDER BY type, name", (like, like)
    ).fetchall()
    return [_entity(r) for r in rows]


def create(type_, eid, name, data=None):
    con = connect()
    try:
        con.execute(
            "INSERT INTO entities (id, type, name, data) VALUES (?, ?, ?, ?)",
            (eid, type_, name, json.dumps(data or {}, ensure_ascii=False)),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"entity {eid!r} already exists")
    con.commit()
    return get(eid)


def update(eid, changes=None, name=None):
    entity = require(eid)
    for key, value in (changes or {}).items():
        _set_path(entity["data"], key, value)
    if name is not None:
        entity["name"] = name
    return _save(entity)


def unset(eid, *keys):
    entity = require(eid)
    for key in keys:
        _del_path(entity["data"], key)
    return _save(entity)


def adjust(eid, key, delta, min=None, max=None):
    entity = require(eid)
    old = _get_path(entity["data"], key) or 0
    if not isinstance(old, (int, float)):
        raise ValueError(f"{eid}.{key} is not a number: {old!r}")
    new = old + delta
    if min is not None:
        new = builtins.max(new, min)
    if max is not None:
        new = builtins.min(new, max)
    _set_path(entity["data"], key, new)
    _save(entity)
    return {"id": eid, "key": key, "old": old, "new": new}


def delete(eid):
    con = connect()
    con.execute("DELETE FROM relations WHERE src=? OR dst=?", (eid, eid))
    cur = con.execute("DELETE FROM entities WHERE id=?", (eid,))
    con.commit()
    return {"deleted": eid, "found": cur.rowcount > 0}


# ---- relations -------------------------------------------------------------

def relate(src, kind, dst, data=None):
    require(src)
    require(dst)
    con = connect()
    con.execute(
        "INSERT OR REPLACE INTO relations (src, kind, dst, data) VALUES (?, ?, ?, ?)",
        (src, kind, dst, json.dumps(data or {}, ensure_ascii=False)),
    )
    con.commit()
    return {"src": src, "kind": kind, "dst": dst}


def unrelate(src, kind, dst):
    con = connect()
    cur = con.execute("DELETE FROM relations WHERE src=? AND kind=? AND dst=?", (src, kind, dst))
    con.commit()
    return {"removed": cur.rowcount > 0}


def related(src, kind=None):
    """Entities that src points to, each with the relation kind."""
    sql = "SELECT e.*, r.kind AS rel FROM relations r JOIN entities e ON e.id = r.dst WHERE r.src=?"
    params = [src]
    if kind:
        sql += " AND r.kind=?"
        params.append(kind)
    rows = connect().execute(sql + " ORDER BY e.name", params).fetchall()
    return [dict(_entity(r), rel=r["rel"]) for r in rows]


def related_to(dst, kind=None):
    """Entities that point to dst, each with the relation kind."""
    sql = "SELECT e.*, r.kind AS rel FROM relations r JOIN entities e ON e.id = r.src WHERE r.dst=?"
    params = [dst]
    if kind:
        sql += " AND r.kind=?"
        params.append(kind)
    rows = connect().execute(sql + " ORDER BY e.name", params).fetchall()
    return [dict(_entity(r), rel=r["rel"]) for r in rows]


def location_of(eid):
    places = related(eid, "located_in")
    return places[0] if places else None


def move(eid, location_id):
    require(location_id)
    connect().execute("DELETE FROM relations WHERE src=? AND kind='located_in'", (eid,))
    return relate(eid, "located_in", location_id)


def give(owner, item_id, qty=1, name=None, data=None):
    """Give qty of an item stack to owner. Creates the item if it doesn't exist (needs name)."""
    require(owner)
    item = get(item_id)
    if item is None:
        if not name:
            raise ValueError(f"item {item_id!r} does not exist; pass a name to create it")
        item = create("item", item_id, name, dict(data or {}, qty=0))
    con = connect()
    owned = con.execute(
        "SELECT 1 FROM relations WHERE src=? AND kind='owns' AND dst=?", (owner, item_id)
    ).fetchone()
    if not owned:
        # an item stack has a single owner: transfer it
        con.execute("DELETE FROM relations WHERE kind='owns' AND dst=?", (item_id,))
        item["data"]["qty"] = 0
        relate(owner, "owns", item_id)
    item["data"]["qty"] = item["data"].get("qty", 0) + qty
    _save(item)
    return {"owner": owner, "item": item_id, "qty": item["data"]["qty"]}


def take(owner, item_id, qty=1):
    item = require(item_id)
    have = item["data"].get("qty", 1)
    if have < qty:
        raise ValueError(f"{owner} has only {have} of {item_id}")
    item["data"]["qty"] = have - qty
    _save(item)
    if item["data"]["qty"] <= 0:
        unrelate(owner, "owns", item_id)
    return {"owner": owner, "item": item_id, "qty": item["data"]["qty"]}


# ---- events ----------------------------------------------------------------

def log(text, changes=None):
    con = connect()
    cur = con.execute(
        "INSERT INTO events (text, changes) VALUES (?, ?)",
        (text, json.dumps(changes, ensure_ascii=False) if changes is not None else None),
    )
    con.commit()
    return {"event": cur.lastrowid}


def events(n=10):
    rows = connect().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [
        {"id": r["id"], "text": r["text"], "changes": json.loads(r["changes"]) if r["changes"] else None}
        for r in reversed(rows)
    ]


def summary():
    player = get("player")
    return {
        "player": player,
        "location": location_of("player") if player else None,
        "inventory": related("player", "owns") if player else [],
        "active_quests": [q for q in list_type("quest") if q["data"].get("status", "active") == "active"],
        "entity_counts": {
            r["type"]: r["n"]
            for r in connect().execute("SELECT type, COUNT(*) AS n FROM entities GROUP BY type")
        },
        "recent_events": events(5),
    }


# ---- CLI -------------------------------------------------------------------

def _parse_value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def main(argv=None):
    p = argparse.ArgumentParser(prog="db", description="World state database; prints JSON.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("summary")
    sub.add_parser("get").add_argument("id")
    sub.add_parser("list").add_argument("type")
    sub.add_parser("find").add_argument("text")
    c = sub.add_parser("create")
    c.add_argument("type"), c.add_argument("id"), c.add_argument("name"), c.add_argument("data", nargs="?")
    s = sub.add_parser("set")
    s.add_argument("id"), s.add_argument("pairs", nargs="+")
    u = sub.add_parser("unset")
    u.add_argument("id"), u.add_argument("keys", nargs="+")
    a = sub.add_parser("adjust")
    a.add_argument("id"), a.add_argument("key"), a.add_argument("delta", type=float)
    a.add_argument("--min", type=float), a.add_argument("--max", type=float)
    sub.add_parser("delete").add_argument("id")
    for name in ("relate", "unrelate"):
        r = sub.add_parser(name)
        r.add_argument("src"), r.add_argument("kind"), r.add_argument("dst")
    m = sub.add_parser("move")
    m.add_argument("id"), m.add_argument("location")
    rl = sub.add_parser("related")
    rl.add_argument("id"), rl.add_argument("kind", nargs="?")
    g = sub.add_parser("give")
    g.add_argument("owner"), g.add_argument("item"), g.add_argument("qty", type=int, nargs="?", default=1)
    g.add_argument("--name"), g.add_argument("--data")
    t = sub.add_parser("take")
    t.add_argument("owner"), t.add_argument("item"), t.add_argument("qty", type=int, nargs="?", default=1)
    sub.add_parser("log").add_argument("text")
    sub.add_parser("events").add_argument("n", type=int, nargs="?", default=10)
    args = p.parse_args(argv)

    def num(x):
        return int(x) if x is not None and float(x).is_integer() else x

    try:
        if args.cmd == "init":
            connect()
            out = {"ok": True}
        elif args.cmd == "summary":
            out = summary()
        elif args.cmd == "get":
            out = require(args.id)
            out["relations"] = related(args.id)
            out["referenced_by"] = related_to(args.id)
        elif args.cmd == "list":
            out = list_type(args.type)
        elif args.cmd == "find":
            out = find(args.text)
        elif args.cmd == "create":
            out = create(args.type, args.id, args.name, json.loads(args.data) if args.data else None)
        elif args.cmd == "set":
            changes, name = {}, None
            for pair in args.pairs:
                key, sep, value = pair.partition("=")
                if not sep:
                    raise ValueError(f"expected key=value, got {pair!r}")
                if key == "name":
                    name = value
                else:
                    changes[key] = _parse_value(value)
            out = update(args.id, changes, name)
        elif args.cmd == "unset":
            out = unset(args.id, *args.keys)
        elif args.cmd == "adjust":
            out = adjust(args.id, args.key, num(args.delta), num(args.min), num(args.max))
        elif args.cmd == "delete":
            out = delete(args.id)
        elif args.cmd == "relate":
            out = relate(args.src, args.kind, args.dst)
        elif args.cmd == "unrelate":
            out = unrelate(args.src, args.kind, args.dst)
        elif args.cmd == "move":
            out = move(args.id, args.location)
        elif args.cmd == "related":
            out = related(args.id, args.kind)
        elif args.cmd == "give":
            out = give(args.owner, args.item, args.qty, args.name, json.loads(args.data) if args.data else None)
        elif args.cmd == "take":
            out = take(args.owner, args.item, args.qty)
        elif args.cmd == "log":
            out = log(args.text)
        elif args.cmd == "events":
            out = events(args.n)
    except (NotFound, ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
