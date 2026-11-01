"""Building blocks shared by the secret broker and the MCP gateway (see README, "Secret broker").

- Windows: the groups opened by the person on the host, closed after a time without use and after a maximum time
- agent_notifier: the spoken notifications (agent-notify)
- AdminServer, run_ctl: the commands of the host, on a Unix socket inside the container, reached with `docker exec`,
  which the agent container can't use
"""

import contextlib
import json
import logging
import logging.handlers
import shutil
import socket
import socketserver
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, cast, override

SECONDS_PER_MINUTE: Final = 60
MINUTES_PER_HOUR: Final = 60
NOTIFY_INTERVAL_SECONDS: Final = 120.0
EXIT_NOT_READY: Final = 3
MAX_LOG_BYTES: Final = 1024 * 1024

type Clock = Callable[[], float]
type Notifier = Callable[[str], None]
type AdminCommand = Callable[[Mapping[str, object]], tuple[bool, str]]


class WindowClosedError(Exception):
    """The group of an operation isn't open."""

    def __init__(self, group: str) -> None:
        """Keep the group, for the message."""
        super().__init__(f"group '{group}' is closed: run `claude_secret allow {group}` on the host")
        self.group = group


def format_duration(seconds: float) -> str:
    """Format a duration in seconds below one minute, in minutes, or in hours and minutes from one hour."""
    if seconds < SECONDS_PER_MINUTE:
        return f"{max(0, int(seconds))} s"
    minutes = int(seconds // SECONDS_PER_MINUTE)
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours} h {rest:02d} min" if hours else f"{minutes} min"


def agent_notifier() -> Notifier:
    """Return a spoken notification through agent-notify, sent in the background; nothing without agent-notify."""
    command = shutil.which("agent-notify")

    def notify(text: str) -> None:
        if command is not None:
            threading.Thread(target=_run_quietly, args=([command, text],), daemon=True).start()

    return notify


def _run_quietly(command: list[str]) -> None:
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        # A program found in the PATH of the image at startup; the text is one argument, without a shell.
        subprocess.run(  # noqa: S603  # nosec B603
            command, check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


class Windows:
    """Open groups: a group closes `idle_seconds` after its last use, and `max_seconds` after it was opened."""

    def __init__(  # noqa: PLR0913 - every setting of a window is needed
        self,
        groups: Sequence[str],
        *,
        idle_seconds: float,
        max_seconds: float,
        clock: Clock,
        notifier: Notifier,
        on_close: Callable[[str, str], None],
    ) -> None:
        """Start with every group closed; `on_close(group, reason)` is called when a group closes."""
        self.groups = tuple(groups)
        self._idle_seconds = idle_seconds
        self._max_seconds = max_seconds
        self._clock = clock
        self._notifier = notifier
        self._on_close = on_close
        self._lock = threading.Lock()
        self._opened: dict[str, float] = {}
        self._last_used: dict[str, float] = {}
        self._last_notified: dict[str, float] = {}

    def check_group(self, group: str, *, allow_all: bool) -> None:
        """Raise ValueError for an unknown group (`all` is accepted when `allow_all`)."""
        if group not in self.groups and not (allow_all and group == "all"):
            names = ", ".join(self.groups) + (", or all" if allow_all else "")
            message = f"unknown group '{group}' (groups: {names})"
            raise ValueError(message)

    def open(self, group: str) -> str:
        """Open a group; return the message for the person."""
        self.check_group(group, allow_all=False)
        with self._lock:
            now = self._clock()
            self._opened[group] = now
            self._last_used[group] = now
        return (
            f"Group '{group}' open: it closes after {format_duration(self._idle_seconds)} without use, "
            f"and in {format_duration(self._max_seconds)} at the latest."
        )

    def revoke(self, group: str) -> list[str]:
        """Close a group, or every group (`all`); return the groups closed."""
        self.check_group(group, allow_all=True)
        with self._lock:
            groups = list(self._opened) if group == "all" else [group]
            closed = [name for name in groups if self._close(name)]
        for name in closed:
            self._on_close(name, "revoked")
        return closed

    def close_all(self) -> None:
        """Close every group without calling `on_close` (e.g. when the secrets are forgotten)."""
        with self._lock:
            self._opened.clear()
            self._last_used.clear()

    def use(self, group: str) -> None:
        """Record a use of the group; raise WindowClosedError, with a notification, when it isn't open."""
        now = self._clock()
        with self._lock:
            expired = self._close_expired(now)
            is_open = group in self._opened
            if is_open:
                self._last_used[group] = now
        self._report_expired(expired)
        if not is_open:
            self.notify_once(group, f"Autorisation requise pour {group}")
            raise WindowClosedError(group)

    def notify_once(self, key: str, text: str) -> None:
        """Send `text` at most once per NOTIFY_INTERVAL_SECONDS for the same key."""
        now = self._clock()
        with self._lock:
            last = self._last_notified.get(key)
            if last is not None and now - last < NOTIFY_INTERVAL_SECONDS:
                return
            self._last_notified[key] = now
        self._notifier(text)

    def status_lines(self) -> list[str]:
        """Return one line per group: open with the time left, or closed."""
        now = self._clock()
        with self._lock:
            expired = self._close_expired(now)
            lines: list[str] = []
            for group in self.groups:
                if group in self._opened:
                    idle_left = self._idle_seconds - (now - self._last_used[group])
                    max_left = self._max_seconds - (now - self._opened[group])
                    lines.append(
                        f"  {group:<10} open, closes in {format_duration(min(idle_left, max_left))} "
                        f"(idle limit {format_duration(idle_left)}, maximum {format_duration(max_left)})"
                    )
                else:
                    lines.append(f"  {group:<10} closed")
        self._report_expired(expired)
        return lines

    def _close(self, group: str) -> bool:
        if self._opened.pop(group, None) is None:
            return False
        del self._last_used[group]
        return True

    def _close_expired(self, now: float) -> list[tuple[str, str]]:
        expired: list[tuple[str, str]] = []
        for group in list(self._opened):
            idle = now - self._last_used[group] >= self._idle_seconds
            if idle or now - self._opened[group] >= self._max_seconds:
                self._close(group)
                expired.append((group, "idle" if idle else "maximum time"))
        return expired

    def _report_expired(self, expired: list[tuple[str, str]]) -> None:
        for group, reason in expired:
            self._on_close(group, reason)


class AdminServer(socketserver.ThreadingUnixStreamServer):
    """Commands of the host, on a Unix socket inside the container: one JSON line per command and per answer."""

    daemon_threads = True

    def __init__(self, path: Path, command: AdminCommand) -> None:
        """Serve `command` on the socket `path`, readable by its owner only."""
        self.command = command
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        super().__init__(str(path), AdminHandler)
        path.chmod(0o600)


class AdminHandler(socketserver.StreamRequestHandler):
    """One command per connection."""

    @override
    def handle(self) -> None:
        server = self.server
        if not isinstance(server, AdminServer):  # pragma: no cover - set by AdminServer
            message = "the handler needs an AdminServer"
            raise TypeError(message)
        try:
            request: object = json.loads(self.rfile.readline())
        except json.JSONDecodeError as error:
            ok, message = False, f"invalid request: {error}"
        else:
            ok, message = (
                server.command(cast("dict[str, object]", request))
                if isinstance(request, dict)
                else (False, "invalid request: a JSON object is expected")
            )
        self.wfile.write((json.dumps({"ok": ok, "message": message}) + "\n").encode("utf-8"))


def admin_arguments(request: Mapping[str, object]) -> list[str]:
    """Return the arguments of a command, as strings."""
    raw_args = request.get("args", [])
    return [str(arg) for arg in cast("list[object]", raw_args)] if isinstance(raw_args, list) else []


def run_ctl(args: Sequence[str], socket_path: Path, usage: str, *, stdin_command: str | None = None) -> int:
    """Send a command of the host to the running service; return the exit code of the command.

    `stdin_command` names the command whose data is read on stdin (e.g. `load`). The answer is printed, except for
    `unlocked`, whose answer is the exit code only. EXIT_NOT_READY when the service doesn't answer.
    """
    if not args:
        print(usage, file=sys.stderr)
        return 2
    request: dict[str, object] = {"command": args[0], "args": list(args[1:])}
    if args[0] == stdin_command:
        request["data"] = sys.stdin.read()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(socket_path))
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = json.loads(client.makefile("r", encoding="utf-8").readline())
    except OSError as error:
        print(f"service not ready ({error})", file=sys.stderr)
        return EXIT_NOT_READY
    ok = bool(response["ok"])
    if args[0] != "unlocked":
        print(response["message"], file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def configure_log(logger: logging.Logger, log_file: Path) -> None:
    """Log to `log_file` in UTC; above MAX_LOG_BYTES, the log becomes `<log_file>.1` (replacing the previous one)."""
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=MAX_LOG_BYTES, backupCount=1, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
