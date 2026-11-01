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
    claude-broker ctl export <KEY>        prints a secret of type 2 (e.g. DEVOPS_PAT), for a container started by the
                                          host (claude_mcp); never available on the network

Secrets file (one KEY=VALUE per line, as a docker env file; see secrets.example):
    GIT_<n>_URL, GIT_<n>_USERNAME, GIT_<n>_PASSWORD or GIT_<n>_BEARER   a Git token (group "git")
    GH_<NAME>_TOKEN, GH_<NAME>_OWNER                                    a GitHub API token of gh, for one
                                                                        organization or account (group "gh")
    GH_TOKEN                                                            a GitHub API token for any other owner
    any other KEY                                                       a secret of type 2, only exported to the host

Every request and every command is logged to BROKER_LOG, never with a secret value.
"""

import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, override
from urllib.parse import parse_qs, urlsplit

from access import (
    AdminServer,
    Clock,
    Notifier,
    WindowClosedError,
    Windows,
    admin_arguments,
    agent_notifier,
    configure_log,
    format_duration,
    run_ctl,
)

GROUPS: Final = ("git", "gh")
DEFAULT_GIT_USERNAME: Final = "token"
MAX_REQUEST_BYTES: Final = 64 * 1024

KEY_PATTERN: Final = re.compile(r"[A-Z][A-Z0-9_]*")
GIT_KEY_PATTERN: Final = re.compile(r"GIT_(\d+)_(URL|USERNAME|PASSWORD|BEARER)")
GH_KEY_PATTERN: Final = re.compile(r"GH_([A-Z0-9_]+?)_(TOKEN|OWNER)")
GIT_URL_PATTERN: Final = re.compile(r"https://[^/\s]+(/\S*)?")
# Characters of a GitHub organization or account name: the owner is written to the log and to the answer.
GH_OWNER_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,100}")

LOGGER: Final = logging.getLogger("claude-broker")


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


class Broker:
    """State of the broker (secrets, open groups), shared by the service and the commands of the host."""

    def __init__(self, settings: Settings, clock: Clock = time.monotonic, notifier: Notifier | None = None) -> None:
        """Start locked, without any open group."""
        self._lock = threading.Lock()
        self._secrets: Secrets | None = None
        self.windows = Windows(
            GROUPS,
            idle_seconds=settings.idle_seconds,
            max_seconds=settings.max_seconds,
            clock=clock,
            notifier=notifier if notifier is not None else agent_notifier(),
            on_close=lambda group, reason: LOGGER.info("group %s closed (%s)", group, reason),
        )

    @property
    def unlocked(self) -> bool:
        """Return True when the secrets are loaded."""
        with self._lock:
            return self._secrets is not None

    # --- Service ----------------------------------------------------------------------------------------

    def git_credential(self, request: str) -> tuple[str, str]:
        """Answer a `get` request of the Git credential protocol; return the answer and what it was for."""
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
        matches = [entry for entry in self._use("git").git if entry.covers(url)]
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

    def _use(self, group: str) -> Secrets:
        """Check that the secrets are loaded and the group open, and record the use."""
        with self._lock:
            secrets = self._secrets
        if secrets is None:
            self.windows.notify_once("locked", "Le courtier est verrouillé")
            message = "the secret broker is locked: run `claude_broker` on the host to unlock it"
            raise BrokerError(HTTPStatus.LOCKED, message)
        try:
            self.windows.use(group)
        except WindowClosedError as error:
            raise BrokerError(HTTPStatus.FORBIDDEN, str(error)) from error
        return secrets

    # --- Commands of the host ---------------------------------------------------------------------------

    def load(self, text: str) -> str:
        """Load the decrypted secrets file; every group is closed."""
        secrets = Secrets.parse(text)
        with self._lock:
            self._secrets = secrets
        self.windows.close_all()
        LOGGER.info("unlocked (%s)", secrets.summary)
        return f"Secret broker unlocked: {secrets.summary}."

    def lock(self) -> str:
        """Forget the secrets and close every group."""
        with self._lock:
            self._secrets = None
        self.windows.close_all()
        LOGGER.info("locked")
        return "Secret broker locked: secrets forgotten, every group closed."

    def allow(self, group: str) -> str:
        """Open a group."""
        self.windows.check_group(group, allow_all=False)
        if not self.unlocked:
            message = "the secret broker is locked: run `claude_broker` first"
            raise ValueError(message)
        message = self.windows.open(group)
        LOGGER.info("group %s opened", group)
        return message

    def revoke(self, group: str) -> str:
        """Close a group, or every group (`all`)."""
        closed = self.windows.revoke(group)
        return "Closed: " + (", ".join(closed) if closed else "no open group")

    def status(self) -> str:
        """Return the state of the broker and the time left for each group."""
        with self._lock:
            secrets = self._secrets
        first = f"Secret broker: unlocked ({secrets.summary})" if secrets else "Secret broker: locked"
        return "\n".join([first, *self.windows.status_lines()])

    def export(self, key: str) -> str:
        """Return a secret of type 2 (neither a Git nor a GitHub token), for a container started by the host."""
        with self._lock:
            secrets = self._secrets
        if secrets is None:
            message = "the secret broker is locked: run `claude_broker` first"
            raise ValueError(message)
        value = secrets.others.get(key)
        if value is None:
            message = f"no secret {key} of type 2 in the secrets file"
            raise ValueError(message)
        LOGGER.info("secret %s exported to the host", key)
        return value


def run_admin_command(broker: Broker, request: Mapping[str, object]) -> tuple[bool, str]:
    """Run a command of the host (`ctl`); return whether it succeeded and its message."""
    command = request.get("command")
    args = admin_arguments(request)
    if command == "unlocked":
        unlocked = broker.unlocked
        return unlocked, "unlocked" if unlocked else "locked"
    without_argument: dict[str, Callable[[], str]] = {
        "load": lambda: broker.load(str(request.get("data", ""))),
        "lock": broker.lock,
        "status": broker.status,
    }
    with_argument: dict[str, Callable[[str], str]] = {
        "allow": broker.allow,
        "revoke": broker.revoke,
        "export": broker.export,
    }
    try:
        if isinstance(command, str) and command in without_argument and not args:
            return True, without_argument[command]()
        if isinstance(command, str) and command in with_argument and len(args) == 1:
            return True, with_argument[command](args[0])
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


def serve(settings: Settings) -> None:
    """Run the service and the commands of the host until the container stops."""
    configure_log(LOGGER, settings.log_file)
    broker = Broker(settings)
    admin = AdminServer(settings.admin_socket, lambda request: run_admin_command(broker, request))
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    LOGGER.info(
        "started (idle limit %s, maximum %s); locked",
        format_duration(settings.idle_seconds),
        format_duration(settings.max_seconds),
    )
    # Every interface of the container: the broker is only reachable on the Docker network of the agents.
    BrokerHTTPServer(("0.0.0.0", settings.port), broker).serve_forever()  # noqa: S104  # nosec B104


def main(argv: Sequence[str] | None = None) -> int:
    """Run `serve` or `ctl <command>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    settings = Settings.from_environment()
    if args[:1] == ["serve"]:
        serve(settings)
        return 0
    if args[:1] == ["ctl"]:
        return run_ctl(args[1:], settings.admin_socket, __doc__ or "", stdin_command="load")
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
