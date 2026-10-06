"""Runs the Claude Code CLI headless in a world folder and turns its stream-json output into UI events."""
import asyncio
import json
import os
from pathlib import Path
from typing import AsyncIterator

from . import auth

ENGINE_DIR = Path(os.environ.get("ENGINE_DIR", "/app/engine"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "").strip()
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
OUTPUT_LIMIT = 1500


def system_prompt(mode: str) -> str:
    prompts = ENGINE_DIR / "prompts"
    return "\n\n".join(
        (prompts / name).read_text(encoding="utf-8") for name in ("common.md", f"{mode}.md")
    )


def _tool_summary(name: str, args: dict, cwd: Path) -> str:
    if name == "Bash":
        return args.get("command", "")
    path = args.get("file_path") or args.get("path") or ""
    if path.startswith(str(cwd)):
        path = path[len(str(cwd)):].lstrip("/")
    if name in ("Glob", "Grep"):
        return args.get("pattern", "") + (f" in {path}" if path else "")
    return path


def _result_text(content) -> str:
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    text = str(content or "")
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + " …"


async def run(cwd: Path, mode: str, prompt: str, session_id: str, resume: bool) -> AsyncIterator[dict]:
    """Yields events: init, text, tool, tool_result, result, error."""
    args = [
        CLAUDE_BIN, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--permission-mode", "bypassPermissions",
        "--tools", TOOLS,
        "--append-system-prompt", system_prompt(mode),
    ]
    args += ["--resume", session_id] if resume else ["--session-id", session_id]
    if CLAUDE_MODEL:
        args += ["--model", CLAUDE_MODEL]

    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=auth.claude_env(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    stderr_task = asyncio.create_task(proc.stderr.read())

    got_result = False
    streamed = False  # text of the current assistant message already sent as deltas
    try:
        async for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "system" and msg.get("subtype") == "init":
                yield {"type": "init", "session_id": msg.get("session_id")}
            elif kind == "stream_event":
                ev = msg.get("event", {})
                delta = ev.get("delta", {})
                if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    streamed = True
                    yield {"type": "text", "text": delta.get("text", "")}
            elif kind == "assistant" and not msg.get("parent_tool_use_id"):
                for block in msg.get("message", {}).get("content", []):
                    if block.get("type") == "text" and not streamed:
                        yield {"type": "text", "text": block.get("text", "")}
                    elif block.get("type") == "tool_use":
                        yield {"type": "tool", "id": block.get("id"), "name": block.get("name"),
                               "input": _tool_summary(block.get("name", ""), block.get("input", {}), cwd)}
                streamed = False
            elif kind == "user":
                content = msg.get("message", {}).get("content", [])
                for block in content if isinstance(content, list) else []:
                    if block.get("type") == "tool_result":
                        yield {"type": "tool_result", "id": block.get("tool_use_id"),
                               "output": _result_text(block.get("content")),
                               "is_error": bool(block.get("is_error"))}
            elif kind == "result":
                got_result = True
                yield {"type": "result", "text": msg.get("result") or "",
                       "is_error": bool(msg.get("is_error")), "session_id": msg.get("session_id")}
    finally:
        if proc.returncode is None:
            await proc.wait()
    stderr = (await stderr_task).decode("utf-8", "replace").strip()
    if not got_result:
        yield {"type": "error", "message": stderr[-2000:] or f"claude exited with code {proc.returncode}"}
