#!/usr/bin/env python3
"""Calls one tool of an MCP server through the MCP gateway, from a script (see README, "MCP gateway").

A skill script uses it when the result of a tool mustn't reach the conversation, e.g. an access token kept in a
shell variable, or a large result written to a file. The gateway applies its groups and approvals as for Claude Code;
a write call waits for its approval on the host.

The text of the result goes to stdout. A tool error goes to stderr, with exit code 1; a failure of the gateway or of
the server, with exit code 2.

Installed in the image as /usr/local/bin/mcp-call. Usage:
    mcp-call <server> <tool> [<arguments as a JSON object>]
    e.g. mcp-call keycloak keycloak_token '{"profile": "bfs"}'

MCP_GATEWAY_URL gives the gateway (default http://claude-gateway:8090).
"""

import contextlib
import http.client
import json
import os
import re
import sys
from collections.abc import Sequence
from typing import Final, cast
from urllib.parse import urlsplit

DEFAULT_GATEWAY: Final = "http://claude-gateway:8090"
# Longer than the time given to approve a write call on the host (GATEWAY_APPROVAL_SECONDS, default 120).
TIMEOUT_SECONDS: Final = 180
NAME_PATTERN: Final = re.compile(r"[a-z0-9_-]{1,64}")
EXIT_TOOL_ERROR: Final = 1
EXIT_FAILURE: Final = 2


class CallError(Exception):
    """A failure of the gateway or of the MCP server (not a tool error)."""


def parse_answer(body: bytes, content_type: str, request_id: int) -> dict[str, object]:
    """Return the JSON-RPC answer to a request, from a JSON body or from server-sent events."""
    candidates: list[str]
    text = body.decode("utf-8", errors="replace")
    if content_type.startswith("text/event-stream"):
        candidates = [line[len("data:") :].strip() for line in text.splitlines() if line.startswith("data:")]
    else:
        candidates = [text]
    for candidate in candidates:
        try:
            message: object = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and cast("dict[str, object]", message).get("id") == request_id:
            return cast("dict[str, object]", message)
    message_text = f"no answer to request {request_id} from the MCP server"
    raise CallError(message_text)


class Session:
    """An MCP session with one server, through the gateway."""

    def __init__(self, gateway: str, server: str) -> None:
        """Prepare the connection to the route of the server."""
        url = urlsplit(gateway)
        if url.scheme != "http" or not url.hostname:
            message = f"invalid MCP_GATEWAY_URL: {gateway}"
            raise CallError(message)
        self._connection = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=TIMEOUT_SECONDS)
        self._path = f"{url.path.rstrip('/')}/mcp/{server}"
        self._session_id: str | None = None
        self._next_id = 1

    def request(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        """Send a request and return its result; a JSON-RPC error raises CallError."""
        request_id = self._next_id
        self._next_id += 1
        message: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        status, content_type, body = self._post(message)
        if status != http.client.OK:
            text = f"the gateway answered {status} to {method}: {body.decode('utf-8', errors='replace')[:300]}"
            raise CallError(text)
        answer = parse_answer(body, content_type, request_id)
        error = answer.get("error")
        if isinstance(error, dict):
            text = f"{method} refused: {cast('dict[str, object]', error).get('message')}"
            raise CallError(text)
        result = answer.get("result")
        return cast("dict[str, object]", result) if isinstance(result, dict) else {}

    def notify(self, method: str) -> None:
        """Send a notification."""
        self._post({"jsonrpc": "2.0", "method": method})

    def close(self) -> None:
        """End the session, then the connection."""
        if self._session_id is not None:
            with contextlib.suppress(OSError, http.client.HTTPException):
                self._send("DELETE", None)
        self._connection.close()

    def _post(self, message: dict[str, object]) -> tuple[int, str, bytes]:
        return self._send("POST", json.dumps(message).encode("utf-8"))

    def _send(self, method: str, body: bytes | None) -> tuple[int, str, bytes]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        self._connection.request(method, self._path, body=body, headers=headers)
        response = self._connection.getresponse()
        data = response.read()
        self._session_id = response.getheader("Mcp-Session-Id") or self._session_id
        return response.status, response.getheader("Content-Type") or "", data


def call(gateway: str, server: str, tool: str, arguments: dict[str, object]) -> tuple[bool, str]:
    """Call one tool; return whether it's a tool error, and the text of its result."""
    session = Session(gateway, server)
    try:
        session.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mcp-call", "version": "1"}},
        )
        session.notify("notifications/initialized")
        result = session.request("tools/call", {"name": tool, "arguments": arguments})
    except (OSError, http.client.HTTPException) as error:
        message = f"the MCP gateway can't be reached at {gateway} ({error}): is claude_gateway running on the host?"
        raise CallError(message) from error
    finally:
        session.close()
    content = result.get("content")
    texts = [
        str(cast("dict[str, object]", item).get("text", ""))
        for item in (cast("list[object]", content) if isinstance(content, list) else [])
        if isinstance(item, dict) and cast("dict[str, object]", item).get("type") == "text"
    ]
    return result.get("isError") is True, "\n".join(texts)


def main(argv: Sequence[str]) -> int:
    """Run one call and print its result."""
    usage = "usage: mcp-call <server> <tool> [<arguments as a JSON object>]"
    if len(argv) not in {2, 3} or not all(NAME_PATTERN.fullmatch(name) for name in argv[:2]):
        print(usage, file=sys.stderr)
        return EXIT_FAILURE
    try:
        arguments: object = json.loads(argv[2]) if len(argv) == 3 else {}  # noqa: PLR2004  # optional arguments
    except json.JSONDecodeError as error:
        print(f"mcp-call: the arguments aren't JSON ({error})", file=sys.stderr)
        return EXIT_FAILURE
    if not isinstance(arguments, dict):
        print("mcp-call: the arguments must be a JSON object", file=sys.stderr)
        return EXIT_FAILURE
    try:
        is_error, text = call(
            os.environ.get("MCP_GATEWAY_URL", DEFAULT_GATEWAY), argv[0], argv[1], cast("dict[str, object]", arguments)
        )
    except CallError as error:
        print(f"mcp-call: {error}", file=sys.stderr)
        return EXIT_FAILURE
    if is_error:
        print(f"mcp-call: {text}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
