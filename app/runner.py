"""Runs an AI agent CLI headless in a world folder and turns its JSON output into UI events.

Both backends yield the same events: init, text, tool, tool_result, result, error.
- claude: Claude Code (`claude -p --output-format stream-json`)
- codex:  OpenAI Codex CLI (`codex exec --json`)
"""
import asyncio
import json
import os
import re
import shlex
import uuid
from pathlib import Path
from typing import AsyncIterator

from . import auth

ENGINE_DIR = Path(os.environ.get("ENGINE_DIR", "/app/engine"))
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "").strip()
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_MODEL = os.environ.get("CODEX_MODEL", "").strip()
TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
OUTPUT_LIMIT = 1500
_SHELL_WRAPPER = re.compile(r"^(?:/\S*/)?(?:ba|z)?sh -l?c (.+)$", re.S)


def system_prompt(mode: str) -> str:
    prompts = ENGINE_DIR / "prompts"
    return "\n\n".join(
        (prompts / name).read_text(encoding="utf-8") for name in ("common.md", f"{mode}.md")
    )


def _rel(path: str, cwd: Path) -> str:
    if path.startswith(str(cwd)):
        path = path[len(str(cwd)):].lstrip("/")
    return path


def _tool_summary(name: str, args: dict, cwd: Path) -> str:
    if name == "Bash":
        return args.get("command", "")
    path = _rel(args.get("file_path") or args.get("path") or "", cwd)
    if name in ("Glob", "Grep"):
        return args.get("pattern", "") + (f" in {path}" if path else "")
    return path


def _result_text(content) -> str:
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    text = str(content or "")
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + " …"


async def _spawn(args: list[str], cwd: Path, env: dict, prompt: str):
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        limit=16 * 1024 * 1024,
    )
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    return proc, asyncio.create_task(proc.stderr.read())


async def _json_lines(proc) -> AsyncIterator[dict]:
    async for raw in proc.stdout:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if isinstance(msg, dict):
            yield msg


def run(provider: str, cwd: Path, mode: str, prompt: str, session_id: str | None) -> AsyncIterator[dict]:
    """session_id: the conversation to resume, or None to start a new one."""
    backend = _run_codex if provider == "codex" else _run_claude
    return backend(cwd, mode, prompt, session_id)


# ---- Claude Code -----------------------------------------------------------

async def _run_claude(cwd: Path, mode: str, prompt: str, session_id: str | None) -> AsyncIterator[dict]:
    args = [
        CLAUDE_BIN, "-p",
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        "--permission-mode", "bypassPermissions",
        "--tools", TOOLS,
        "--append-system-prompt", system_prompt(mode),
    ]
    args += ["--resume", session_id] if session_id else ["--session-id", str(uuid.uuid4())]
    if CLAUDE_MODEL:
        args += ["--model", CLAUDE_MODEL]

    proc, stderr_task = await _spawn(args, cwd, auth.claude_env(), prompt)
    got_result = False
    streamed = False  # text of the current assistant message already sent as deltas
    try:
        async for msg in _json_lines(proc):
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


# ---- OpenAI Codex ----------------------------------------------------------

def _codex_command(command) -> str:
    """Codex reports shell calls as `bash -lc '<cmd>'`; show just <cmd>."""
    if isinstance(command, list):
        command = shlex.join(command)
    command = str(command or "")
    m = _SHELL_WRAPPER.match(command)
    if m:
        try:
            parts = shlex.split(m.group(1))
            if len(parts) == 1:
                return parts[0]
        except ValueError:
            pass
    return command


async def _run_codex(cwd: Path, mode: str, prompt: str, session_id: str | None) -> AsyncIterator[dict]:
    # The value of -c is parsed as TOML; a JSON string (without \u escapes) is a valid TOML string.
    instructions = json.dumps(system_prompt(mode), ensure_ascii=False)
    opts = [
        "--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
        "-c", f"developer_instructions={instructions}",
    ]
    if CODEX_MODEL:
        opts += ["-m", CODEX_MODEL]
    args = [CODEX_BIN, "exec", "resume", *opts, session_id, "-"] if session_id else [CODEX_BIN, "exec", *opts, "-"]

    proc, stderr_task = await _spawn(args, cwd, auth.codex_env(), prompt)
    got_result = False
    reply: list[str] = []  # agent messages since the last tool call: the final reply
    last_error = ""
    try:
        async for msg in _json_lines(proc):
            kind = msg.get("type")
            item = msg.get("item") or {}
            itype = item.get("type")
            if kind == "thread.started":
                yield {"type": "init", "session_id": msg.get("thread_id")}
            elif kind == "item.started" and itype == "command_execution":
                reply = []
                yield {"type": "tool", "id": item.get("id"), "name": "Shell",
                       "input": _codex_command(item.get("command"))}
            elif kind == "item.completed" and itype == "command_execution":
                exit_code = item.get("exit_code")
                yield {"type": "tool_result", "id": item.get("id"),
                       "output": _result_text(item.get("aggregated_output")),
                       "is_error": exit_code not in (0, None) or item.get("status") == "failed"}
            elif kind == "item.completed" and itype == "file_change":
                reply = []
                changes = item.get("changes") or []
                summary = ", ".join(f"{c.get('kind', 'edit')} {_rel(c.get('path', ''), cwd)}" for c in changes)
                yield {"type": "tool", "id": item.get("id"), "name": "Edit", "input": summary}
            elif kind == "item.completed" and itype == "agent_message":
                text = item.get("text") or ""
                yield {"type": "text", "text": ("\n\n" if reply else "") + text}
                reply.append(text)
            elif kind == "error":
                last_error = msg.get("message") or last_error
            elif kind == "turn.completed":
                got_result = True
                yield {"type": "result", "text": "\n\n".join(reply) or "The game master finished without a reply.",
                       "is_error": not reply}
            elif kind == "turn.failed":
                got_result = True
                message = (msg.get("error") or {}).get("message") or last_error or "Codex turn failed"
                yield {"type": "result", "text": message, "is_error": True}
    finally:
        if proc.returncode is None:
            await proc.wait()
    stderr = (await stderr_task).decode("utf-8", "replace").strip()
    if not got_result:
        message = last_error or stderr[-2000:] or f"codex exited with code {proc.returncode}"
        yield {"type": "error", "message": message}
