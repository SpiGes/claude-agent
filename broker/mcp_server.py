"""Minimal MCP server over HTTP, for the services of the agent behind the MCP gateway (see README, "MCP gateway").

The services written for the agent (Keycloak, later SAS and Oracle) run each in a container of their own, with their
own secrets, and are reached by the agent through the gateway only. This module implements what they need of the
streamable HTTP transport of MCP, with the standard library only: a POST of one JSON-RPC message, answered with JSON
(`initialize`, `ping`, `tools/list`, `tools/call`; a notification gets 202). It keeps no session and offers no stream
(GET is refused). A tool returns text; a ToolError reaches the agent as a tool error (`isError`).

The secrets of a service are read from a file of settings (`--env-file`, `KEY=value` lines) written by claude_mcp
start in the tmpfs of the container, and removed once the server listens: they never are in the environment of the
process, nor on a disk.
"""

import json
import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, cast, override

PROTOCOL_VERSION: Final = "2025-06-18"
MAX_REQUEST_BYTES: Final = 1024 * 1024
MCP_PATH: Final = "/mcp"
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602

LOGGER: Final = logging.getLogger("mcp-server")

type ToolHandler = Callable[[Mapping[str, object]], str]


class ToolError(Exception):
    """A failure of a tool, returned to the agent as a tool error with this message."""


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool of the server: its name, description, JSON schema of its arguments, and handler."""

    name: str
    description: str
    input_schema: Mapping[str, object]
    handler: ToolHandler

    def definition(self) -> dict[str, object]:
        """Return the tool as listed by tools/list."""
        return {"name": self.name, "description": self.description, "inputSchema": dict(self.input_schema)}


class McpServer:
    """The JSON-RPC methods of an MCP server that offers tools."""

    def __init__(self, name: str, version: str, tools: Sequence[Tool]) -> None:
        """Serve the given tools under the given name."""
        self._name = name
        self._version = version
        self._tools = {tool.name: tool for tool in tools}

    def handle(self, message: object) -> dict[str, object] | None:
        """Answer one JSON-RPC message; None for a notification, which gets no answer."""
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST, "a single JSON-RPC message is expected")
        request = cast("dict[str, object]", message)
        method = request.get("method")
        if "id" not in request:
            return None
        request_id = request["id"]
        params = request.get("params")
        params_map = cast("dict[str, object]", params) if isinstance(params, dict) else {}
        methods: dict[str, Callable[[], dict[str, object]]] = {
            "initialize": lambda: _result(request_id, self._initialize(params_map)),
            "ping": lambda: _result(request_id, {}),
            "tools/list": lambda: _result(request_id, {"tools": [tool.definition() for tool in self._tools.values()]}),
            "tools/call": lambda: self._call(request_id, params_map),
        }
        answer = methods.get(str(method))
        return answer() if answer is not None else _error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")

    def _initialize(self, params: Mapping[str, object]) -> dict[str, object]:
        version = params.get("protocolVersion")
        return {
            "protocolVersion": version if isinstance(version, str) else PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self._name, "version": self._version},
        }

    def _call(self, request_id: object, params: Mapping[str, object]) -> dict[str, object]:
        tool = self._tools.get(str(params.get("name")))
        if tool is None:
            return _error(request_id, INVALID_PARAMS, f"unknown tool: {params.get('name')}")
        arguments = params.get("arguments")
        try:
            text = tool.handler(cast("dict[str, object]", arguments) if isinstance(arguments, dict) else {})
        except ToolError as error:
            LOGGER.info("%s refused: %s", tool.name, error)
            return _result(request_id, {"content": [{"type": "text", "text": str(error)}], "isError": True})
        except Exception:
            # The details stay in the log of the container: they may hold settings of the service.
            LOGGER.exception("%s failed", tool.name)
            text = f"the tool {tool.name} failed: see the log of its container (docker logs)"
            return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": True})
        return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _result(request_id: object, result: Mapping[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": dict(result)}


def _error(request_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def read_env_file(path: Path) -> dict[str, str]:
    """Read `KEY=value` lines; empty lines and lines starting with # are skipped, the value is kept as it is."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            message = f"invalid line in {path}: no KEY=value"
            raise ValueError(message)
        values[key.strip()] = value
    return values


class McpHttpServer(ThreadingHTTPServer):
    """HTTP server of an MCP server, one thread per request."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], server: McpServer) -> None:
        """Listen on the address and answer with the given MCP server."""
        super().__init__(address, McpRequestHandler)
        self.mcp = server


class McpRequestHandler(BaseHTTPRequestHandler):
    """The streamable HTTP transport, reduced to JSON answers."""

    protocol_version = "HTTP/1.1"

    @property
    def _mcp(self) -> McpServer:
        server = self.server
        if not isinstance(server, McpHttpServer):  # pragma: no cover - set by McpHttpServer
            message = "the handler needs an McpHttpServer"
            raise TypeError(message)
        return server.mcp

    def do_POST(self) -> None:
        """Answer one JSON-RPC message."""
        if self.path.split("?", 1)[0] != MCP_PATH:
            self._reply(HTTPStatus.NOT_FOUND, b"unknown path", close=True)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self._reply(HTTPStatus.BAD_REQUEST, b"invalid or too large Content-Length", close=True)
            return
        try:
            message: object = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(HTTPStatus.BAD_REQUEST, b"invalid JSON")
            return
        answer = self._mcp.handle(message)
        if answer is None:
            self._reply(HTTPStatus.ACCEPTED, b"")
            return
        self._reply(HTTPStatus.OK, json.dumps(answer, ensure_ascii=False).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        """Refuse the stream of the server: this server sends nothing on its own."""
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b"no stream")

    def do_DELETE(self) -> None:
        """Accept the end of a session: there is no session to end."""
        self._reply(HTTPStatus.OK, b"")

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Log nothing: a tool logs what it does, without the default output of http.server."""

    def _reply(
        self, status: HTTPStatus, data: bytes, content_type: str = "text/plain; charset=utf-8", *, close: bool = False
    ) -> None:
        self.send_response(status)
        if close:
            # The body of the request wasn't read: it mustn't be read as the next request.
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def parse_serve_arguments(args: Sequence[str], usage: str) -> tuple[Path, int]:
    """Read `--env-file <path> [--port <port>]`, the arguments of the `serve` command of a service."""
    env_file: Path | None = None
    port = 8000
    rest = list(args)
    while rest:
        option = rest.pop(0)
        if option == "--env-file" and rest:
            env_file = Path(rest.pop(0))
        elif option == "--port" and rest and rest[0].isdigit():
            port = int(rest.pop(0))
        else:
            raise ValueError(usage)
    if env_file is None:
        raise ValueError(usage)
    return env_file, port


def serve(server: McpServer, port: int) -> None:
    """Run the server until the container stops; the log goes to stderr (docker logs)."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with McpHttpServer(("0.0.0.0", port), server) as http_server:  # noqa: S104  # nosec B104  # own network only
        LOGGER.info("listening on port %s", port)
        http_server.serve_forever()
