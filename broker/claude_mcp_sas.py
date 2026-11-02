#!/usr/bin/env python3
"""SAS MCP server of the agent (see README, "MCP gateway").

Runs in its own container (claude-mcp-sas, started by `claude_mcp start sas`), behind the MCP gateway. It holds the
account of the non-production SAS server, REF environment (SPIGES_SAS_REF_SSH_USER, SPIGES_SAS_REF_SSH_PASSWORD), and
runs ssh and scp for the agent, so that the password never enters the agent container.

- sas_exec  runs a shell command in ~/agent on the server
- sas_put   copies files of the drop folder to ~/agent/<dir> on the server
- sas_get   copies ~/agent/<file> of the server to the drop folder
- sas_run   runs ~/agent/<program>.sas with the REF SIS workspace server, and returns the exit code and the TEST,
            ERROR and WARNING lines of the log

Controls:
- the servers, their host key algorithms and the REF SAS command are fixed in the code; the host keys are pinned in a
  known hosts file mounted read only from the host, which the agent can't change
- the password reaches ssh and scp through SSH_ASKPASS only (this program, called back with SAS_MCP_ASKPASS=1), in
  the environment of these processes, never on a command line
- after a rejected login, every next call is refused until the server is restarted (claude_mcp restart sas): the
  account is a technical account shared with the SpiGes backend, which repeated failures could lock
- the remote paths are relative to ~/agent, without `..`; the local files are in the drop folder only

Installed in the image as /usr/local/bin/claude-mcp-sas. Usage:
    claude-mcp-sas serve --env-file <file> [--port <port>]
"""

import logging
import os
import re
import shlex
import subprocess  # nosec B404  # ssh and scp, with a list of arguments
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, cast

from mcp_server import McpServer, Tool, ToolError, parse_serve_arguments, read_env_file, serve

USER_SECRET: Final = "SPIGES_SAS_REF_SSH_USER"  # noqa: S105  # nosec B105  # the name of the secret
PASSWORD_SECRET: Final = "SPIGES_SAS_REF_SSH_PASSWORD"  # noqa: S105  # nosec B105  # the name of the secret
ASKPASS_VARIABLE: Final = "SAS_MCP_ASKPASS"
PASSWORD_VARIABLE: Final = "SAS_MCP_PASSWORD"  # noqa: S105  # nosec B105  # the name of the variable
KNOWN_HOSTS_VARIABLE: Final = "SAS_KNOWN_HOSTS_FILE"
DROP_DIR_VARIABLE: Final = "SAS_DROP_DIR"
REMOTE_BASE: Final = "agent"
COMMAND_TIMEOUT_SECONDS: Final = 14 * 60
MAX_OUTPUT_CHARACTERS: Final = 30_000
REMOTE_PATH_PATTERN: Final = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")
LOGIN_REJECTED_PATTERN: Final = re.compile(
    r"permission denied|authentication failed|too many authentication", re.IGNORECASE
)

LOGGER: Final = logging.getLogger("mcp-server")


@dataclass(frozen=True, slots=True)
class SasServer:
    """A SAS server the agent may reach: host, accepted host key algorithms, and the REF SAS command."""

    host: str
    host_key_algorithms: str
    sas_command: str


REF_SAS_COMMAND: Final = "/appl/sas/ref/metadata/9.4/Lev2/SIS/sas.sh"
SERVERS: Final[Mapping[str, SasServer]] = {
    # Current SAS server.
    "pssas10i02": SasServer(
        "pssas10i02.bfi.admin.ch", "ssh-ed25519,ecdsa-sha2-nistp256,rsa-sha2-512,rsa-sha2-256", REF_SAS_COMMAND
    ),
    # New SAS server of the migration (sas94ref = pssas105a01): only its ed25519 key was checked.
    "sas94": SasServer("sas94ref.bfs.admin.ch", "ssh-ed25519", REF_SAS_COMMAND),
}
DEFAULT_SERVER: Final = "pssas10i02"


@dataclass(frozen=True, slots=True)
class Completed:
    """Result of an ssh or scp process."""

    status: int
    stdout: str
    stderr: str


type Runner = Callable[[Sequence[str], Mapping[str, str]], Completed]


def run_process(args: Sequence[str], env: Mapping[str, str]) -> Completed:
    """Run ssh or scp without stdin, and return its exit code and output."""
    try:
        process = subprocess.run(  # noqa: S603  # nosec B603  # fixed program, list of arguments
            list(args),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        message = f"stopped after {COMMAND_TIMEOUT_SECONDS // 60} minutes"
        raise ToolError(message) from error
    return Completed(process.returncode, process.stdout, process.stderr)


def remote_path(value: object, name: str) -> str:
    """Return a path relative to ~/agent, checked: no absolute path, no `..`, no option, plain characters only."""
    if not isinstance(value, str) or not REMOTE_PATH_PATTERN.fullmatch(value):
        message = f"{name}: a path relative to ~/{REMOTE_BASE} is expected (letters, digits, _ . / -)"
        raise ToolError(message)
    if ".." in PurePosixPath(value).parts:
        message = f"{name}: '..' isn't allowed"
        raise ToolError(message)
    return value


def limited(text: str) -> str:
    """Return the end of a long output, with a note."""
    if len(text) <= MAX_OUTPUT_CHARACTERS:
        return text
    return f"[... first {len(text) - MAX_OUTPUT_CHARACTERS} characters cut]\n{text[-MAX_OUTPUT_CHARACTERS:]}"


class SasRef:
    """Commands on a SAS server of REF, with the account of the secrets file."""

    def __init__(
        self,
        secrets: Mapping[str, str],
        known_hosts: Path,
        drop_dir: Path,
        askpass: str,
        run: Runner = run_process,
    ) -> None:
        """Keep the account in memory; the known hosts file and the drop folder are checked at each call."""
        self._user = secrets.get(USER_SECRET, "")
        self._password = secrets.get(PASSWORD_SECRET, "")
        if not self._user or not self._password:
            message = f"{USER_SECRET} and {PASSWORD_SECRET} are expected in the secrets of the server"
            raise ValueError(message)
        self._known_hosts = known_hosts
        self._drop_dir = drop_dir
        self._askpass = askpass
        self._run = run
        self._lock = threading.Lock()
        self._login_rejected = False

    def exec(self, arguments: Mapping[str, object]) -> str:
        """Run a shell command in ~/agent."""
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            message = "the argument command is expected"
            raise ToolError(message)
        server = self._server(arguments)
        result = self._ssh(server, f"mkdir -p ~/{REMOTE_BASE} && cd ~/{REMOTE_BASE} && {command}")
        return self._output(result)

    def put(self, arguments: Mapping[str, object]) -> str:
        """Copy files of the drop folder to ~/agent/<dir>."""
        files = arguments.get("files")
        if not isinstance(files, list) or not files:
            message = "the argument files is expected: a list of files of the drop folder"
            raise ToolError(message)
        local = [self._local_file(file, must_exist=True) for file in cast("list[object]", files)]
        target = remote_path(arguments.get("remote_dir"), "remote_dir")
        server = self._server(arguments)
        self._check(self._ssh(server, f"mkdir -p ~/{REMOTE_BASE}/{shlex.quote(target)}"))
        result = self._scp(server, [str(path) for path in local], f"{REMOTE_BASE}/{target}/", upload=True)
        self._check(result)
        return f"{len(local)} file(s) copied to ~/{REMOTE_BASE}/{target}/"

    def get(self, arguments: Mapping[str, object]) -> str:
        """Copy ~/agent/<file> to the drop folder."""
        source = remote_path(arguments.get("remote_file"), "remote_file")
        local = self._local_file(arguments.get("local_file"), must_exist=False)
        local.parent.mkdir(parents=True, exist_ok=True)
        server = self._server(arguments)
        self._check(self._scp(server, [f"{REMOTE_BASE}/{source}"], str(local), upload=False))
        return f"~/{REMOTE_BASE}/{source} copied to {local}"

    def run_sas(self, arguments: Mapping[str, object]) -> str:
        """Run a SAS program of ~/agent with the REF SIS workspace server."""
        program = remote_path(arguments.get("program"), "program")
        if not program.endswith(".sas"):
            message = "program: a .sas file is expected"
            raise ToolError(message)
        server = self._server(arguments)
        path = f"$HOME/{REMOTE_BASE}/{shlex.quote(program)}"
        log = f"$HOME/{REMOTE_BASE}/{shlex.quote(program.removesuffix('.sas') + '.log')}"
        command = (
            f'{SERVERS[server].sas_command} {path} -log {log} -NORSASUSER; echo "exit code: $?"; '
            f"grep -a -E '^(TEST|ERROR|WARNING)' {log} || true"
        )
        return self._output(self._ssh(server, command))

    def _server(self, arguments: Mapping[str, object]) -> str:
        server = arguments.get("server", DEFAULT_SERVER)
        if not isinstance(server, str) or server not in SERVERS:
            message = f"server: one of {', '.join(SERVERS)} is expected"
            raise ToolError(message)
        return server

    def _local_file(self, value: object, *, must_exist: bool) -> Path:
        """Return a file of the drop folder, given relative to it or as an absolute path in it."""
        if not isinstance(value, str) or not value:
            message = f"a file of the drop folder {self._drop_dir} is expected"
            raise ToolError(message)
        drop = self._drop_dir.resolve()
        path = (self._drop_dir / value).resolve()
        if not path.is_relative_to(drop) or path == drop:
            message = f"{value}: only the files of the drop folder {self._drop_dir} can be used"
            raise ToolError(message)
        if must_exist and not path.is_file():
            message = f"{value}: no such file in the drop folder {self._drop_dir}"
            raise ToolError(message)
        return path

    def _options(self, server: str) -> list[str]:
        if not self._known_hosts.is_file():
            message = f"no known hosts file ({self._known_hosts}): see AGENT_SAS_KNOWN_HOSTS_FILE"
            raise ToolError(message)
        return [
            "-o", f"UserKnownHostsFile={self._known_hosts}",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"HostKeyAlgorithms={SERVERS[server].host_key_algorithms}",
            "-o", "PreferredAuthentications=keyboard-interactive,password",
            "-o", "PubkeyAuthentication=no",
            "-o", "NumberOfPasswordPrompts=1",
            "-o", "ConnectTimeout=15",
            "-o", "LogLevel=ERROR",
        ]  # fmt: skip

    def _ssh(self, server: str, command: str) -> Completed:
        args = ["ssh", *self._options(server), "--", f"{self._user}@{SERVERS[server].host}", command]
        return self._connect(server, args)

    def _scp(self, server: str, sources: Sequence[str], target: str, *, upload: bool) -> Completed:
        remote = f"{self._user}@{SERVERS[server].host}:"
        if upload:
            args = ["scp", "-q", *self._options(server), "--", *sources, remote + target]
        else:
            args = ["scp", "-q", *self._options(server), "--", *(remote + source for source in sources), target]
        return self._connect(server, args)

    def _connect(self, server: str, args: Sequence[str]) -> Completed:
        with self._lock:
            if self._login_rejected:
                message = (
                    "a login was rejected before: every call is refused until the person checks the account and "
                    "restarts the server (claude_mcp restart sas); don't retry"
                )
                raise ToolError(message)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108  # nosec B108  # tmpfs of the container
            "SSH_ASKPASS": self._askpass,
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": "none",
            ASKPASS_VARIABLE: "1",
            PASSWORD_VARIABLE: self._password,
        }
        LOGGER.info("%s on %s", args[0], server)
        result = self._run(args, env)
        if result.status != 0 and LOGIN_REJECTED_PATTERN.search(result.stderr):
            with self._lock:
                self._login_rejected = True
            LOGGER.error("login rejected on %s: every next call is refused until a restart", server)
            message = (
                f"AUTHENTICATION FAILED on {server}: stopped, don't retry; tell the person, who checks the account "
                "and restarts the server (claude_mcp restart sas)"
            )
            raise ToolError(message)
        return result

    @staticmethod
    def _check(result: Completed) -> None:
        if result.status != 0:
            message = f"failed with exit code {result.status}: {limited(result.stderr.strip())}"
            raise ToolError(message)

    @staticmethod
    def _output(result: Completed) -> str:
        text = result.stdout + (f"\n[stderr]\n{result.stderr}" if result.stderr.strip() else "")
        return f"{limited(text.rstrip())}\n[exit status of ssh: {result.status}]"


SERVER_PROPERTY: Final = {
    "type": "string",
    "enum": list(SERVERS),
    "description": f"SAS server, REF environment (default {DEFAULT_SERVER}; sas94 = new server of the migration)",
}


def tools(sas: SasRef, drop_dir: Path) -> list[Tool]:
    """Return the tools of the server."""
    return [
        Tool(
            "sas_exec",
            f"Runs a shell command in ~/{REMOTE_BASE} on the SAS server (REF), and returns its output.",
            {
                "type": "object",
                "properties": {"command": {"type": "string"}, "server": SERVER_PROPERTY},
                "required": ["command"],
                "additionalProperties": False,
            },
            sas.exec,
        ),
        Tool(
            "sas_put",
            f"Copies files of the drop folder {drop_dir} to ~/{REMOTE_BASE}/<remote_dir> on the SAS server (REF).",
            {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"Files of {drop_dir}, relative to it or absolute",
                    },
                    "remote_dir": {"type": "string", "description": f"Folder relative to ~/{REMOTE_BASE}"},
                    "server": SERVER_PROPERTY,
                },
                "required": ["files", "remote_dir"],
                "additionalProperties": False,
            },
            sas.put,
        ),
        Tool(
            "sas_get",
            f"Copies ~/{REMOTE_BASE}/<remote_file> of the SAS server (REF) to a file of the drop folder {drop_dir}.",
            {
                "type": "object",
                "properties": {
                    "remote_file": {"type": "string", "description": f"File relative to ~/{REMOTE_BASE}"},
                    "local_file": {"type": "string", "description": f"File of {drop_dir}, relative to it or absolute"},
                    "server": SERVER_PROPERTY,
                },
                "required": ["remote_file", "local_file"],
                "additionalProperties": False,
            },
            sas.get,
        ),
        Tool(
            "sas_run",
            f"Runs ~/{REMOTE_BASE}/<program> with the REF SIS workspace server, writes the log next to it, and "
            "returns the exit code and the TEST, ERROR and WARNING lines of the log.",
            {
                "type": "object",
                "properties": {
                    "program": {"type": "string", "description": f".sas file relative to ~/{REMOTE_BASE}"},
                    "server": SERVER_PROPERTY,
                },
                "required": ["program"],
                "additionalProperties": False,
            },
            sas.run_sas,
        ),
    ]


def main(argv: Sequence[str], environ: Mapping[str, str] = os.environ) -> int:
    """Run the `serve` command, or give the password to ssh when called back as SSH_ASKPASS."""
    if environ.get(ASKPASS_VARIABLE) == "1":
        password = environ.get(PASSWORD_VARIABLE, "")
        if not password:
            return 1
        print(password)  # read by ssh, through a pipe
        return 0
    usage = (__doc__ or "").rsplit("Usage:", 1)[-1].strip()
    if not argv or argv[0] != "serve":
        print(f"usage: {usage}", file=sys.stderr)
        return 2
    try:
        env_file, port = parse_serve_arguments(argv[1:], f"usage: {usage}")
        known_hosts = Path(environ.get(KNOWN_HOSTS_VARIABLE, "/config/sas-known-hosts"))
        drop_dir = Path(environ.get(DROP_DIR_VARIABLE, "/shared/mcp-sas"))
        sas = SasRef(read_env_file(env_file), known_hosts, drop_dir, str(Path(sys.argv[0]).absolute()))
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    serve(McpServer("claude-mcp-sas", "1", tools(sas, drop_dir)), port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
