"""Claude login: token from the environment, or saved by the in-browser login flow.

The browser flow drives `claude setup-token` in a pseudo-terminal: it reads the sign-in URL,
types the code the player pastes back, and stores the resulting long-lived token in the
claude-home volume.
"""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import threading
import time
from pathlib import Path

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
TOKEN_FILE = CONFIG_DIR / "storyforge-token"

_CURSOR_FORWARD = re.compile(r"\x1b\[\d*C")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>]")
_URL = re.compile(r"https://\S+/oauth/authorize\?\S+")
_TOKEN = re.compile(r"sk-ant-oat\d+-[A-Za-z0-9_-]+")


def token() -> str | None:
    env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env:
        return env
    try:
        return TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


def logged_in() -> bool:
    return bool(token() or os.environ.get("ANTHROPIC_API_KEY"))


def claude_env() -> dict:
    env = dict(os.environ)
    t = token()
    if t:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = t
    return env


class LoginError(Exception):
    pass


class LoginFlow:
    def __init__(self):
        self.pid = None
        self.fd = None
        self.output = ""
        self.lock = threading.Lock()

    def start(self) -> str:
        """Launch `claude setup-token` and return the sign-in URL."""
        with self.lock:
            self._stop()
            pid, fd = pty.fork()
            if pid == 0:
                # Wide terminal so the URL and token are printed on one line.
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 4000, 0, 0))
                env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
                os.execvpe(CLAUDE_BIN, [CLAUDE_BIN, "setup-token"], env)
            self.pid, self.fd, self.output = pid, fd, ""
            url = self._read_until(_URL, timeout=30)
            if not url:
                tail = self._tail()
                self._stop()
                raise LoginError(f"Could not get a sign-in link from Claude. Output: {tail}")
            return url

    def finish(self, code: str) -> None:
        """Send the code from the sign-in page and save the token."""
        with self.lock:
            if self.fd is None:
                raise LoginError("Login was not started (or expired). Start again.")
            self.output = ""
            os.write(self.fd, code.strip().encode() + b"\r")
            found = self._read_until(_TOKEN, timeout=60)
            if found:
                self._read_until(re.compile(r"(?!)"), timeout=1)  # let the rest of the token arrive
                found = _TOKEN.search(self.output).group(0)
            tail = self._tail()
            self._stop()
            if not found:
                raise LoginError(f"Login failed. Check the code and try again. Output: {tail}")
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(found)
            TOKEN_FILE.chmod(0o600)

    def _read_until(self, pattern: re.Pattern, timeout: float) -> str | None:
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.3)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:  # process exited
                break
            if not chunk:
                break
            text = _CURSOR_FORWARD.sub(" ", chunk.decode("utf-8", "replace"))
            self.output += _ANSI.sub("", text)
            match = pattern.search(self.output)
            if match:
                return match.group(0)
        return None

    def _tail(self) -> str:
        return " ".join(self.output.split())[-400:]

    def _stop(self):
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        self.pid = self.fd = None


login_flow = LoginFlow()
