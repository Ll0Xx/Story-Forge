"""Logins for the two AI providers that can run the game master.

Claude: token from the environment, or saved by the in-browser flow, which drives `claude setup-token`
in a pseudo-terminal: it reads the sign-in URL, types the code the player pastes back, and stores the
resulting long-lived token in the claude-home volume.

Codex (ChatGPT): `codex login --device-auth` prints a URL and a one-time code, then waits until the
player approves it on OpenAI's site and writes auth.json into the codex-home volume.
"""
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

PROVIDERS = ("claude", "codex")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
TOKEN_FILE = CONFIG_DIR / "storyforge-token"

CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_HOME = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
CODEX_AUTH_FILE = CODEX_HOME / "auth.json"

# Each CLI only gets its own provider's credentials, so neither AI can read the other's key with `env`.
CLAUDE_SECRETS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
CODEX_SECRETS = ("CODEX_API_KEY", "OPENAI_API_KEY")

_CURSOR_FORWARD = re.compile(r"\x1b\[\d*C")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>]")
_URL = re.compile(r"https://\S+/oauth/authorize\?\S+")
_TOKEN = re.compile(r"sk-ant-oat\d+-[A-Za-z0-9_-]+")
_DEVICE_URL = re.compile(r"https://\S+/device\b\S*")
_DEVICE_CODE = re.compile(r"one-time code.*?\n\s*([A-Z0-9]{4,}-[A-Z0-9]{4,})", re.S)


class LoginError(Exception):
    pass


# ---- status ----------------------------------------------------------------

def token() -> str | None:
    env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if env:
        return env
    try:
        return TOKEN_FILE.read_text().strip() or None
    except OSError:
        return None


def _claude_from_env() -> bool:
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or os.environ.get("ANTHROPIC_API_KEY"))


def _codex_from_env() -> bool:
    return bool(os.environ.get("CODEX_API_KEY", "").strip())


def connected(provider: str) -> bool:
    if provider == "claude":
        return bool(token() or os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "codex":
        return _codex_from_env() or CODEX_AUTH_FILE.exists()
    return False


def status() -> dict:
    """Per provider: connected, and whether the login comes from .env (then it can't be disconnected here)."""
    from_env = {"claude": _claude_from_env(), "codex": _codex_from_env()}
    return {p: {"connected": connected(p), "from_env": from_env[p]} for p in PROVIDERS}


def default_provider() -> str:
    return next((p for p in PROVIDERS if connected(p)), "claude")


def _env_without(*names: str) -> dict:
    return {k: v for k, v in os.environ.items() if k not in names}


def claude_env() -> dict:
    env = _env_without(*CODEX_SECRETS)
    t = token()
    if t:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = t
    return env


def codex_env() -> dict:
    return _env_without(*CLAUDE_SECRETS)


def logout(provider: str) -> None:
    if provider == "claude":
        TOKEN_FILE.unlink(missing_ok=True)
    elif provider == "codex":
        codex_login.cancel()
        subprocess.run([CODEX_BIN, "logout"], capture_output=True, timeout=30)
        CODEX_AUTH_FILE.unlink(missing_ok=True)


# ---- Claude: `claude setup-token` in a pseudo-terminal ---------------------

class ClaudeLogin:
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
                env = _env_without("CLAUDE_CODE_OAUTH_TOKEN", *CODEX_SECRETS)
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


# ---- Codex: `codex login --device-auth` ------------------------------------

class CodexLogin:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.output = ""
        self.lock = threading.Lock()

    def start(self) -> dict:
        """Launch the device login and return {url, code} for the player to enter on OpenAI's site."""
        with self.lock:
            self._stop()
            CODEX_HOME.mkdir(parents=True, exist_ok=True)
            env = _env_without(*CLAUDE_SECRETS, *CODEX_SECRETS)
            self.proc = subprocess.Popen(
                [CODEX_BIN, "login", "--device-auth"], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            self.output = ""
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
            end = time.time() + 30
            while time.time() < end and self.proc.poll() is None:
                url, code = _DEVICE_URL.search(self.output), _DEVICE_CODE.search(self.output)
                if url and code:
                    return {"url": url.group(0), "code": code.group(1)}
                time.sleep(0.2)
            tail = self._tail()
            self._stop()
            raise LoginError(f"Could not get a sign-in code from ChatGPT. Output: {tail}")

    def state(self) -> str:
        """waiting | done | idle; raises LoginError if the login failed."""
        with self.lock:
            if self.proc is None:
                return "done" if connected("codex") else "idle"
            code = self.proc.poll()
            if code is None:
                return "waiting"
            tail = self._tail()
            self.proc = None
            if code == 0 and connected("codex"):
                return "done"
            raise LoginError(f"Login failed or the code expired. Start again. Output: {tail}")

    def cancel(self) -> None:
        with self.lock:
            self._stop()

    def _pump(self, proc: subprocess.Popen):
        for raw in iter(lambda: proc.stdout.read1(4096), b""):
            self.output += _ANSI.sub("", raw.decode("utf-8", "replace"))

    def _tail(self) -> str:
        return " ".join(self.output.split())[-400:]

    def _stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc = None


claude_login = ClaudeLogin()
codex_login = CodexLogin()
