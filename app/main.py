"""Story Forge web server: REST for worlds, WebSocket for live turns."""
import asyncio
import re
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, runner, worlds

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MODES = ("build", "play")
MAX_IMPORT_BYTES = 200 * 1024 * 1024
PROVIDER_NAMES = {"claude": "Claude", "codex": "ChatGPT"}
LOST_SESSION_NOTE = (
    "(Engine note: the previous conversation was lost. Before replying, re-read WORLD.md, "
    "memory/recap.md and `python3 -m db summary` to restore context.)\n\n"
)

app = FastAPI(title="Story Forge")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _world_or_404(wid: str) -> Path:
    d = worlds.world_dir(wid)
    if d is None:
        raise HTTPException(404, "world not found")
    return d


def _snapshot(d: Path) -> dict:
    return {"world": worlds.read_meta(d), "state": worlds.read_state(d), "files": worlds.list_files(d)}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


def _provider_or_400(provider: str) -> str:
    if provider not in auth.PROVIDERS:
        raise HTTPException(400, "unknown provider")
    return provider


@app.get("/api/health")
def health():
    return {"providers": auth.status()}


class LoginCode(BaseModel):
    code: str


@app.post("/api/login/claude/start")
def claude_login_start():
    try:
        return {"url": auth.claude_login.start()}
    except auth.LoginError as e:
        raise HTTPException(502, str(e))


@app.post("/api/login/claude/finish")
def claude_login_finish(body: LoginCode):
    try:
        auth.claude_login.finish(body.code)
    except auth.LoginError as e:
        raise HTTPException(400, str(e))
    return {"providers": auth.status()}


@app.post("/api/login/codex/start")
def codex_login_start():
    try:
        return auth.codex_login.start()
    except auth.LoginError as e:
        raise HTTPException(502, str(e))


@app.get("/api/login/codex/status")
def codex_login_status():
    try:
        return {"state": auth.codex_login.state(), "providers": auth.status()}
    except auth.LoginError as e:
        raise HTTPException(400, str(e))


@app.post("/api/login/codex/cancel")
def codex_login_cancel():
    auth.codex_login.cancel()
    return {"ok": True}


@app.post("/api/logout/{provider}")
def logout(provider: str):
    auth.logout(_provider_or_400(provider))
    return {"providers": auth.status()}


class NewWorld(BaseModel):
    provider: str | None = None


class Provider(BaseModel):
    provider: str


@app.get("/api/worlds")
def list_worlds():
    return worlds.list_worlds()


@app.post("/api/worlds")
def create_world(body: NewWorld | None = None):
    provider = body.provider if body and body.provider else auth.default_provider()
    return worlds.create(_provider_or_400(provider))


@app.post("/api/worlds/import")
async def import_world(request: Request):
    data = await request.body()
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "File is too large.")
    try:
        return worlds.import_zip(data)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/worlds/{wid}/export")
def export_world(wid: str):
    d = _world_or_404(wid)
    slug = re.sub(r"[^a-z0-9]+", "-", worlds.read_meta(d)["name"].lower()).strip("-") or wid
    return Response(
        worlds.export_zip(d),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@app.get("/api/worlds/{wid}")
def get_world(wid: str, mode: str = "play"):
    d = _world_or_404(wid)
    if mode not in MODES:
        raise HTTPException(400, "bad mode")
    return {**_snapshot(d), "messages": worlds.read_chat(d, mode), "busy": locks[wid].locked()}


@app.put("/api/worlds/{wid}/provider")
def set_world_provider(wid: str, body: Provider):
    d = _world_or_404(wid)
    if locks[wid].locked():
        raise HTTPException(409, "world is busy")
    if worlds.provider(d) != _provider_or_400(body.provider):
        worlds.set_provider(d, body.provider)
    return worlds.read_meta(d)


@app.delete("/api/worlds/{wid}")
def delete_world(wid: str):
    d = _world_or_404(wid)
    if locks[wid].locked():
        raise HTTPException(409, "world is busy")
    worlds.delete(d)
    return {"ok": True}


@app.websocket("/api/worlds/{wid}/ws")
async def world_socket(ws: WebSocket, wid: str):
    await ws.accept()
    d = worlds.world_dir(wid)
    if d is None:
        await ws.close(code=4404)
        return

    async def send(event: dict):
        # The turn must finish (and be logged) even if the browser went away.
        try:
            await ws.send_json(event)
        except Exception:
            pass

    try:
        while True:
            msg = await ws.receive_json()
            mode, text = msg.get("mode"), (msg.get("text") or "").strip()
            if mode not in MODES or not text:
                continue
            if locks[wid].locked():
                await send({"type": "error", "message": "The game master is still working on the previous turn."})
                continue
            async with locks[wid]:
                await run_turn(send, d, mode, text, msg.get("display"))
    except WebSocketDisconnect:
        pass


async def run_turn(send, d: Path, mode: str, text: str, display: str | None):
    """display: if set, the message is an engine instruction shown to the player as this note."""
    if display:
        worlds.append_chat(d, mode, {"role": "system", "text": display})
    else:
        worlds.append_chat(d, mode, {"role": "user", "text": text})

    provider = worlds.provider(d)
    if not auth.connected(provider):
        name = PROVIDER_NAMES[provider]
        message = (f"This world's game master runs on {name}, which isn't connected. Connect {name} "
                   "from the top bar, or switch this world to another provider on the home page.")
        worlds.append_chat(d, mode, {"role": "error", "text": message})
        await send({"type": "error", "mode": mode, "message": message, **_snapshot(d)})
        return

    sessions = worlds.read_sessions(d)
    sessions["provider"] = provider
    session_id = sessions.get(mode)
    prompt = text

    for attempt in range(2):
        tools, final, error, started = [], None, None, False
        async for ev in runner.run(provider, d, mode, prompt, session_id):
            kind = ev["type"]
            if kind == "init":
                started = True
                if ev.get("session_id") and sessions.get(mode) != ev["session_id"]:
                    sessions[mode] = ev["session_id"]
                    worlds.write_sessions(d, sessions)
            elif kind == "result":
                final = ev
            elif kind == "error":
                error = ev["message"]
            else:
                if kind == "tool":
                    tools.append({"name": ev["name"], "input": ev["input"]})
                elif kind == "tool_result" and tools:
                    tools[-1]["output"] = ev["output"]
                await send(ev)
        if session_id and not started and attempt == 0:
            # Session files are gone (e.g. the claude-home volume was reset): start fresh with a recovery note.
            session_id = None
            prompt = LOST_SESSION_NOTE + text
            continue
        break

    if final and not final["is_error"]:
        entry = worlds.append_chat(d, mode, {"role": "gm", "text": final["text"], "tools": tools})
        await send({"type": "done", "mode": mode, "message": entry, **_snapshot(d)})
    else:
        message = (final or {}).get("text") or error or "Unknown error"
        worlds.append_chat(d, mode, {"role": "error", "text": message})
        await send({"type": "error", "mode": mode, "message": message, **_snapshot(d)})
