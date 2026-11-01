#!/usr/bin/env python3
"""Secret broker of the agent (see README, "Secret broker").

Runs in its own container (claude-broker, started by launch.sh), next to the agent containers. It keeps the
secrets of an age-encrypted file in memory, and hands them out to the agent only while their group is open.
A group is opened and closed on the host with claude_secret, through `docker exec`, which the agent container
can't use: the agent can't open a group by itself. A group closes BROKER_IDLE_MINUTES after its last use, and
BROKER_MAX_HOURS after it was opened, whichever comes first.

Installed in the image as /usr/local/bin/claude-broker. Usage:
    claude-broker serve                   runs the service (command of the container)
    claude-broker ctl load                loads the decrypted secrets file, read on stdin
    claude-broker ctl allow <group>       opens a group
    claude-broker ctl revoke <group>|all  closes a group, or every group
    claude-broker ctl status              prints the state of the broker and of each group
    claude-broker ctl unlocked            exits with 0 when the secrets are loaded, 1 otherwise (3: service not ready)
    claude-broker ctl lock                forgets the secrets and closes every group

Secrets file (one KEY=VALUE per line, as a docker env file; see secrets.example):
    GIT_<n>_URL, GIT_<n>_USERNAME, GIT_<n>_PASSWORD or GIT_<n>_BEARER   a Git token (group "git")
    GH_<NAME>_TOKEN, GH_<NAME>_OWNER                                    a GitHub API token of gh, for one
                                                                        organization or account (group "gh")
    GH_TOKEN                                                            a GitHub API token for any other owner

Every request and every command is logged to BROKER_LOG, never with a secret value.
"""

import contextlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import socketserver

# Only runs agent-notify, see _run_quietly.
import subprocess  # nosec B404
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, cast, override
from urllib.parse import parse_qs, urlsplit

GROUPS: Final = ("git", "gh")
DEFAULT_GIT_USERNAME: Final = "token"
NOTIFY_INTERVAL_SECONDS: Final = 120.0
MAX_REQUEST_BYTES: Final = 64 * 1024
MAX_LOG_BYTES: Final = 1024 * 1024
SECONDS_PER_MINUTE: Final = 60
MINUTES_PER_HOUR: Final = 60
EXIT_NOT_READY: Final = 3

KEY_PATTERN: Final = re.compile(r"[A-Z][A-Z0-9_]*")
GIT_KEY_PATTERN: Final = re.compile(r"GIT_(\d+)_(URL|USERNAME|PASSWORD|BEARER)")
GH_KEY_PATTERN: Final = re.compile(r"GH_([A-Z0-9_]+?)_(TOKEN|OWNER)")
GIT_URL_PATTERN: Final = re.compile(r"https://[^/\s]+(/\S*)?")
# Characters of a GitHub organization or account name: the owner is written to the log and to the answer.
GH_OWNER_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,100}")

LOGGER: Final = logging.getLogger("claude-broker")

type Clock = Callable[[], float]
type Notifier = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings of the broker, read from the environment of its container."""

    port: int = 8080
    idle_seconds: float = 30 * 60
    max_seconds: float = 4 * 3600
    log_file: Path = Path("/log/broker.log")
    # On the tmpfs of the broker container, which only its own processes can reach.
    admin_socket: Path = Path("/tmp/claude-broker/admin.sock")  # noqa: S108  # nosec B108

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> "Settings":
        """Read BROKER_PORT, BROKER_IDLE_MINUTES, BROKER_MAX_HOURS, BROKER_LOG and BROKER_ADMIN_SOCKET."""
        defaults = cls()
        return cls(
            port=int(environ.get("BROKER_PORT", defaults.port)),
            idle_seconds=float(environ.get("BROKER_IDLE_MINUTES", defaults.idle_seconds / 60)) * 60,
            max_seconds=float(environ.get("BROKER_MAX_HOURS", defaults.max_seconds / 3600)) * 3600,
            log_file=Path(environ.get("BROKER_LOG", str(defaults.log_file))),
            admin_socket=Path(environ.get("BROKER_ADMIN_SOCKET", str(defaults.admin_socket))),
        )


class SecretsFileError(ValueError):
    """The secrets file can't be loaded; the message gives line numbers or keys, never a value."""


class BrokerError(Exception):
    """A refusal of the service, returned to the agent with its HTTP status."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        """Keep the HTTP status of the answer."""
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class GitEntry:
    """A Git token of the secrets file, for the remotes whose URL starts with `url`."""

    name: str
    url: str
    username: str
    password: str | None = field(repr=False)
    bearer: str | None = field(repr=False)

    def covers(self, url: str) -> bool:
        """Return True when `url` is the URL of the entry or under it, without regard to case (as the servers)."""
        prefix, folded = self.url.casefold(), url.casefold()
        return folded == prefix or folded.startswith(prefix + "/")

    def credential(self, capabilities: frozenset[str]) -> str:
        """Answer of the Git credential protocol: Basic (user name and password) or Bearer."""
        if self.bearer is not None:
            if "authtype" not in capabilities:
                message = f"{self.name} is a Bearer token, which needs Git 2.46 or later"
                raise BrokerError(HTTPStatus.BAD_REQUEST, message)
            return f"capability[]=authtype\nauthtype=Bearer\ncredential={self.bearer}\n"
        return f"username={self.username}\npassword={self.password}\n"


@dataclass(frozen=True, slots=True)
class GhEntry:
    """A GitHub API token of gh, for one organization or account (`owner`), or for any other owner (None)."""

    name: str
    owner: str | None
    token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Secrets:
    """Content of the secrets file."""

    git: tuple[GitEntry, ...]
    gh: tuple[GhEntry, ...]
    others: Mapping[str, str] = field(repr=False)

    @property
    def summary(self) -> str:
        """Number of secrets of each kind, for the messages and the log."""
        return f"{len(self.git)} Git tokens, {len(self.gh)} GitHub API tokens, {len(self.others)} other secrets"

    @classmethod
    def parse(cls, text: str) -> "Secrets":
        """Parse the decrypted secrets file; raises SecretsFileError with every error found."""
        errors: list[str] = []
        values = _read_values(text, errors)
        git: dict[int, dict[str, str]] = {}
        gh: dict[str, dict[str, str]] = {}
        others: dict[str, str] = {}
        for key, value in values.items():
            if git_match := GIT_KEY_PATTERN.fullmatch(key):
                git.setdefault(int(git_match.group(1)), {})[git_match.group(2)] = value
            elif key == "GH_TOKEN":
                gh.setdefault("", {})["TOKEN"] = value
            elif gh_match := GH_KEY_PATTERN.fullmatch(key):
                gh.setdefault(gh_match.group(1), {})[gh_match.group(2)] = value
            else:
                others[key] = value
        secrets = cls(_git_entries(git, errors), _gh_entries(gh, errors), others)
        if errors:
            raise SecretsFileError("; ".join(errors))
        if not (secrets.git or secrets.gh or secrets.others):
            message = "the secrets file is empty (wrong passphrase?)"
            raise SecretsFileError(message)
        return secrets


def _read_values(text: str, errors: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not KEY_PATTERN.fullmatch(key):
            errors.append(f"line {number}: expected KEY=VALUE")
        elif value != value.strip():
            errors.append(f"line {number}: {key} starts or ends with a space")
        elif not value:
            errors.append(f"line {number}: {key} is empty")
        else:
            values[key] = value
    return values


def _git_entries(fields_by_index: Mapping[int, Mapping[str, str]], errors: list[str]) -> tuple[GitEntry, ...]:
    entries: list[GitEntry] = []
    for index, fields in sorted(fields_by_index.items()):
        url = fields.get("URL")
        if url is None or not GIT_URL_PATTERN.fullmatch(url):
            errors.append(f"GIT_{index}_URL: missing, or not an https:// URL")
        elif ("PASSWORD" in fields) == ("BEARER" in fields):
            errors.append(f"GIT_{index}: exactly one of GIT_{index}_PASSWORD and GIT_{index}_BEARER is expected")
        else:
            entries.append(
                GitEntry(
                    name=f"GIT_{index}",
                    url=url.rstrip("/"),
                    username=fields.get("USERNAME", DEFAULT_GIT_USERNAME),
                    password=fields.get("PASSWORD"),
                    bearer=fields.get("BEARER"),
                )
            )
    return tuple(entries)


def _gh_entries(fields_by_name: Mapping[str, Mapping[str, str]], errors: list[str]) -> tuple[GhEntry, ...]:
    entries: list[GhEntry] = []
    for name, fields in sorted(fields_by_name.items()):
        key = f"GH_{name}_TOKEN" if name else "GH_TOKEN"
        token, owner = fields.get("TOKEN"), fields.get("OWNER")
        if token is None:
            errors.append(f"{key}: missing")
        elif name and owner is None:
            errors.append(f"GH_{name}_OWNER: missing (the GitHub organization or account of {key})")
        else:
            entries.append(GhEntry(name=key, owner=owner, token=token))
    return tuple(entries)


def agent_notifier() -> Notifier:
    """Spoken notification through agent-notify, sent in the background; nothing when it isn't installed."""
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


class Broker:
    """State of the broker (secrets, open groups), shared by the service and the commands of the host."""

    def __init__(self, settings: Settings, clock: Clock = time.monotonic, notifier: Notifier | None = None) -> None:
        """Start locked, without any open group."""
        self._settings = settings
        self._clock = clock
        self._notifier = notifier if notifier is not None else agent_notifier()
        self._lock = threading.Lock()
        self._secrets: Secrets | None = None
        self._opened: dict[str, float] = {}
        self._last_used: dict[str, float] = {}
        self._last_notified: dict[str, float] = {}

    @property
    def unlocked(self) -> bool:
        """True when the secrets are loaded."""
        with self._lock:
            return self._secrets is not None

    # --- Service ----------------------------------------------------------------------------------------

    def git_credential(self, request: str) -> tuple[str, str]:
        """Answers a `get` request of the Git credential protocol; returns the answer and what it was for."""
        fields: dict[str, str] = {}
        capabilities: set[str] = set()
        for line in request.splitlines():
            key, _, value = line.partition("=")
            if key == "capability[]":
                capabilities.add(value)
            elif key:
                fields[key] = value
        host = fields.get("host")
        if fields.get("protocol") != "https" or not host:
            raise BrokerError(HTTPStatus.BAD_REQUEST, "only https URLs are served")
        url = f"https://{host}/{fields.get('path', '')}".rstrip("/")

        with self._lock:
            secrets = self._use("git")
            matches = [entry for entry in secrets.git if entry.covers(url)]
        if not matches:
            raise BrokerError(HTTPStatus.NOT_FOUND, f"no Git token in the secrets file for {url}")
        entry = max(matches, key=lambda candidate: len(candidate.url))
        return entry.credential(frozenset(capabilities)), f"{url} ({entry.name})"

    def gh_token(self, owner: str) -> tuple[str, str]:
        """Return the GitHub API token of `owner`, and what it was for.

        The owner is compared without regard to case; a GH_TOKEN without owner serves any other owner. Without
        owner, the only token is returned, if there's just one.
        """
        if owner and not GH_OWNER_PATTERN.fullmatch(owner):
            raise BrokerError(HTTPStatus.BAD_REQUEST, "invalid owner")
        with self._lock:
            entries = self._use("gh").gh
        if not entries:
            raise BrokerError(HTTPStatus.NOT_FOUND, "no GitHub API token in the secrets file")
        if owner:
            matches = [entry for entry in entries if (entry.owner or "").casefold() == owner.casefold()]
            matches = matches or [entry for entry in entries if entry.owner is None]
            if not matches:
                message = f"no GitHub API token in the secrets file for the owner '{owner}'"
                raise BrokerError(HTTPStatus.NOT_FOUND, message)
        elif len(entries) == 1:
            matches = list(entries)
        else:
            owners = ", ".join(entry.owner or "(default)" for entry in entries)
            message = (
                f"the GitHub owner can't be found from the command (owners: {owners}): "
                "use -R <owner>/<repository>, or run gh in a clone of the repository"
            )
            raise BrokerError(HTTPStatus.BAD_REQUEST, message)
        return matches[0].token, f"{owner or '(no owner)'} ({matches[0].name})"

    # --- Commands of the host ---------------------------------------------------------------------------

    def load(self, text: str) -> str:
        """Load the decrypted secrets file; every group is closed."""
        secrets = Secrets.parse(text)
        with self._lock:
            self._secrets = secrets
            self._opened.clear()
            self._last_used.clear()
        LOGGER.info("unlocked (%s)", secrets.summary)
        return f"Secret broker unlocked: {secrets.summary}."

    def lock(self) -> str:
        """Forgets the secrets and closes every group."""
        with self._lock:
            self._secrets = None
            self._opened.clear()
            self._last_used.clear()
        LOGGER.info("locked")
        return "Secret broker locked: secrets forgotten, every group closed."

    def allow(self, group: str) -> str:
        """Open a group."""
        _check_group(group, allow_all=False)
        with self._lock:
            if self._secrets is None:
                message = "the secret broker is locked: run `claude_broker` first"
                raise ValueError(message)
            now = self._clock()
            self._opened[group] = now
            self._last_used[group] = now
        LOGGER.info("group %s opened", group)
        return (
            f"Group '{group}' open: it closes after {format_duration(self._settings.idle_seconds)} without use, "
            f"and in {format_duration(self._settings.max_seconds)} at the latest."
        )

    def revoke(self, group: str) -> str:
        """Close a group, or every group (`all`)."""
        _check_group(group, allow_all=True)
        with self._lock:
            groups = list(self._opened) if group == "all" else [group]
            for name in groups:
                if self._opened.pop(name, None) is not None:
                    del self._last_used[name]
                    LOGGER.info("group %s closed (revoked)", name)
        return "Closed: " + (", ".join(groups) if groups else "no open group")

    def status(self) -> str:
        """State of the broker and time left for each group."""
        with self._lock:
            now = self._clock()
            self._close_expired(now)
            secrets = self._secrets
            lines = [f"Secret broker: unlocked ({secrets.summary})" if secrets else "Secret broker: locked"]
            for group in GROUPS:
                if group in self._opened:
                    idle_left = self._settings.idle_seconds - (now - self._last_used[group])
                    max_left = self._settings.max_seconds - (now - self._opened[group])
                    lines.append(
                        f"  {group:<4} open, closes in {format_duration(min(idle_left, max_left))} "
                        f"(idle limit {format_duration(idle_left)}, maximum {format_duration(max_left)})"
                    )
                else:
                    lines.append(f"  {group:<4} closed")
        return "\n".join(lines)

    # --- Groups (called with the lock held) -------------------------------------------------------------

    def _close_expired(self, now: float) -> None:
        for group in list(self._opened):
            idle = now - self._last_used[group] >= self._settings.idle_seconds
            if idle or now - self._opened[group] >= self._settings.max_seconds:
                del self._opened[group]
                del self._last_used[group]
                LOGGER.info("group %s closed (%s)", group, "idle" if idle else "maximum time")

    def _use(self, group: str) -> Secrets:
        """Check that the secrets are loaded and the group open, and record the use."""
        now = self._clock()
        self._close_expired(now)
        if self._secrets is None:
            self._notify_once("locked", "Le courtier est verrouillé", now)
            message = "the secret broker is locked: run `claude_broker` on the host to unlock it"
            raise BrokerError(HTTPStatus.LOCKED, message)
        if group not in self._opened:
            self._notify_once(group, f"Autorisation requise pour {group}", now)
            message = f"group '{group}' is closed: run `claude_secret allow {group}` on the host"
            raise BrokerError(HTTPStatus.FORBIDDEN, message)
        self._last_used[group] = now
        return self._secrets

    def _notify_once(self, key: str, text: str, now: float) -> None:
        """At most one notification per NOTIFY_INTERVAL_SECONDS for the same key."""
        last = self._last_notified.get(key)
        if last is None or now - last >= NOTIFY_INTERVAL_SECONDS:
            self._last_notified[key] = now
            self._notifier(text)


def _check_group(group: str, *, allow_all: bool) -> None:
    if group not in GROUPS and not (allow_all and group == "all"):
        names = ", ".join(GROUPS) + (", or all" if allow_all else "")
        message = f"unknown group '{group}' (groups: {names})"
        raise ValueError(message)


def format_duration(seconds: float) -> str:
    """Duration in minutes, or in hours and minutes from one hour."""
    minutes = max(0, int(seconds // SECONDS_PER_MINUTE))
    hours, rest = divmod(minutes, MINUTES_PER_HOUR)
    return f"{hours} h {rest:02d} min" if hours else f"{minutes} min"


def run_admin_command(broker: Broker, request: Mapping[str, object]) -> tuple[bool, str]:
    """Run a command of the host (`ctl`); return whether it succeeded and its message."""
    command = request.get("command")
    raw_args = request.get("args", [])
    args = [str(arg) for arg in cast("list[object]", raw_args)] if isinstance(raw_args, list) else []
    if command == "unlocked":
        unlocked = broker.unlocked
        return unlocked, "unlocked" if unlocked else "locked"
    without_argument: dict[str, Callable[[], str]] = {
        "load": lambda: broker.load(str(request.get("data", ""))),
        "lock": broker.lock,
        "status": broker.status,
    }
    with_group: dict[str, Callable[[str], str]] = {"allow": broker.allow, "revoke": broker.revoke}
    try:
        if isinstance(command, str) and command in without_argument and not args:
            return True, without_argument[command]()
        if isinstance(command, str) and command in with_group and len(args) == 1:
            return True, with_group[command](args[0])
    except ValueError as error:
        return False, str(error)
    return False, f"unknown command: {command} {' '.join(args)}".rstrip()


class BrokerHTTPServer(ThreadingHTTPServer):
    """HTTP service of the agent network: the agent containers ask for their secrets here."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], broker: Broker) -> None:
        """Serve `broker` at `address`."""
        self.broker = broker
        super().__init__(address, ServiceHandler)


class ServiceHandler(BaseHTTPRequestHandler):
    """Requests of the agent: `POST /v1/git` (Git credential protocol), `GET /v1/token/gh?owner=<owner>`."""

    server_version = "claude-broker"

    @property
    def _broker(self) -> Broker:
        server = self.server
        if not isinstance(server, BrokerHTTPServer):  # pragma: no cover - set by BrokerHTTPServer
            message = "the handler needs a BrokerHTTPServer"
            raise TypeError(message)
        return server.broker

    def do_POST(self) -> None:
        """Git credential protocol: the body is the `get` request of Git."""
        if self.path != "/v1/git":
            self._reply(HTTPStatus.NOT_FOUND, "unknown path")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
            return
        request = self.rfile.read(length).decode("utf-8", errors="replace")
        self._serve("git", lambda: self._broker.git_credential(request))

    def do_GET(self) -> None:
        """GitHub API token of gh, for the owner given in the query."""
        url = urlsplit(self.path)
        if url.path != "/v1/token/gh":
            self._reply(HTTPStatus.NOT_FOUND, "unknown path")
            return
        owner = parse_qs(url.query).get("owner", [""])[0]
        self._serve("gh", lambda: self._broker.gh_token(owner))

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Log nothing: the requests are logged by _serve, without the default output of http.server."""

    def _serve(self, operation: str, action: Callable[[], tuple[str, str]]) -> None:
        client = self.client_address[0]
        try:
            body, target = action()
        except BrokerError as error:
            LOGGER.info("%s %s: refused (%s)", client, operation, error)
            self._reply(error.status, str(error))
            return
        LOGGER.info("%s %s %s: allowed", client, operation, target)
        self._reply(HTTPStatus.OK, body)

    def _reply(self, status: HTTPStatus, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class AdminServer(socketserver.ThreadingUnixStreamServer):
    """Commands of the host (claude_secret, claude_broker), on a Unix socket inside the broker container."""

    daemon_threads = True

    def __init__(self, path: Path, broker: Broker) -> None:
        """Serve the commands of `broker` on the socket `path`, readable by its owner only."""
        self.broker = broker
        super().__init__(str(path), AdminHandler)
        path.chmod(0o600)


class AdminHandler(socketserver.StreamRequestHandler):
    """One JSON line per command, one JSON line per answer."""

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
                run_admin_command(server.broker, cast("dict[str, object]", request))
                if isinstance(request, dict)
                else (False, "invalid request: a JSON object is expected")
            )
        self.wfile.write((json.dumps({"ok": ok, "message": message}) + "\n").encode("utf-8"))


def configure_log(log_file: Path) -> None:
    """Log to `log_file` in UTC; above MAX_LOG_BYTES, the log becomes `<log_file>.1` (replacing the previous one)."""
    handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=MAX_LOG_BYTES, backupCount=1, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def serve(settings: Settings) -> None:
    """Run the service and the commands of the host until the container stops."""
    configure_log(settings.log_file)
    broker = Broker(settings)
    settings.admin_socket.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.admin_socket.unlink(missing_ok=True)
    admin = AdminServer(settings.admin_socket, broker)
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    LOGGER.info(
        "started (idle limit %s, maximum %s); locked",
        format_duration(settings.idle_seconds),
        format_duration(settings.max_seconds),
    )
    # Every interface of the container: the broker is only reachable on the Docker network of the agents.
    BrokerHTTPServer(("0.0.0.0", settings.port), broker).serve_forever()  # noqa: S104  # nosec B104


def ctl(args: Sequence[str], settings: Settings) -> int:
    """Send a command to the running service; return the exit code of the command."""
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    request: dict[str, object] = {"command": args[0], "args": list(args[1:])}
    if args[0] == "load":
        request["data"] = sys.stdin.read()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(settings.admin_socket))
            client.sendall((json.dumps(request) + "\n").encode("utf-8"))
            response = json.loads(client.makefile("r", encoding="utf-8").readline())
    except OSError as error:
        print(f"claude-broker: service not ready ({error})", file=sys.stderr)
        return EXIT_NOT_READY
    ok = bool(response["ok"])
    if args[0] != "unlocked":
        print(response["message"], file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: `serve` or `ctl <command>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    settings = Settings.from_environment()
    if args[:1] == ["serve"]:
        serve(settings)
        return 0
    if args[:1] == ["ctl"]:
        return ctl(args[1:], settings)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
