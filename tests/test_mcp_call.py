import io
import json
import threading
import unittest
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from unittest import mock

from claude_gateway import KEYCLOAK_POLICY, Gateway, GatewayHTTPServer, Route, Settings
from mcp_call import CallError, call, main, parse_answer
from mcp_server import McpHttpServer, McpServer, Tool, ToolError


def token(arguments: Mapping[str, object]) -> str:
    if arguments.get("profile") != "bfs":
        message = f"unknown profile '{arguments.get('profile')}'"
        raise ToolError(message)
    return "the-token"


SERVER = McpServer("fake-keycloak", "1", [Tool("keycloak_token", "Token.", {"type": "object"}, token)])


def start(server: ThreadingHTTPServer, test: unittest.TestCase) -> int:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server.server_address[1]


class EndToEndTest(unittest.TestCase):
    """mcp-call, through the real gateway, to a real MCP server of the agent."""

    def setUp(self) -> None:
        upstream = start(McpHttpServer(("127.0.0.1", 0), SERVER), self)
        route = Route("keycloak", f"http://127.0.0.1:{upstream}/mcp", KEYCLOAK_POLICY)
        self.notifications: list[str] = []
        self.gateway = Gateway(Settings(routes=(route,)), notifier=self.notifications.append)
        self.url = f"http://127.0.0.1:{start(GatewayHTTPServer(('127.0.0.1', 0), self.gateway), self)}"

    def test_call_while_the_group_is_open(self) -> None:
        self.gateway.allow("keycloak")
        self.assertEqual(call(self.url, "keycloak", "keycloak_token", {"profile": "bfs"}), (False, "the-token"))
        self.assertEqual(call(self.url, "keycloak", "keycloak_token", {"profile": "x"}), (True, "unknown profile 'x'"))

    def test_closed_group(self) -> None:
        is_error, text = call(self.url, "keycloak", "keycloak_token", {"profile": "bfs"})
        self.assertTrue(is_error)
        self.assertIn("claude_secret allow keycloak", text)

    def test_refused_tool(self) -> None:
        self.gateway.allow("keycloak")
        self.assertEqual(
            call(self.url, "keycloak", "keycloak_write", {}),
            (True, "MCP gateway: the tool keycloak_write is refused by the MCP gateway"),
        )

    def test_failures(self) -> None:
        with self.assertRaisesRegex(CallError, "answered 404"):
            call(self.url, "unknown", "keycloak_token", {})
        with self.assertRaisesRegex(CallError, "can't be reached"):
            call("http://127.0.0.1:1", "keycloak", "keycloak_token", {})
        with self.assertRaisesRegex(CallError, "invalid MCP_GATEWAY_URL"):
            call("https://gateway", "keycloak", "keycloak_token", {})

    def test_main(self) -> None:
        self.gateway.allow("keycloak")
        with (
            mock.patch.dict("os.environ", {"MCP_GATEWAY_URL": self.url, "MCP_CALL_TIMEOUT_SECONDS": "900"}),
            mock.patch("sys.stdout", io.StringIO()) as stdout,
            mock.patch("sys.stderr", io.StringIO()) as stderr,
        ):
            self.assertEqual(main(["keycloak", "keycloak_token", '{"profile": "bfs"}']), 0)
            self.assertEqual(main(["keycloak", "keycloak_token", '{"profile": "x"}']), 1)
            self.assertEqual(main(["unknown", "keycloak_token"]), 2)
        self.assertEqual(stdout.getvalue(), "the-token\n")
        self.assertIn("mcp-call: unknown profile 'x'", stderr.getvalue())
        self.assertIn("mcp-call: the gateway answered 404", stderr.getvalue())


class ArgumentsTest(unittest.TestCase):
    def test_invalid_arguments(self) -> None:
        for argv, message in (
            ([], "usage"),
            (["Keycloak", "tool"], "usage"),
            (["keycloak", "tool", "{", "x"], "usage"),
            (["keycloak", "tool", "{"], "aren't JSON"),
            (["keycloak", "tool", "[1]"], "must be a JSON object"),
        ):
            with self.subTest(argv=argv), mock.patch("sys.stderr", io.StringIO()) as stderr:
                self.assertEqual(main(argv), 2)
                self.assertIn(message, stderr.getvalue())


class TimeoutTest(unittest.TestCase):
    def test_invalid_timeout(self) -> None:
        for value in ("0", "3601", "ten", "-5"):
            with (
                self.subTest(value=value),
                mock.patch.dict("os.environ", {"MCP_CALL_TIMEOUT_SECONDS": value}),
                mock.patch("sys.stderr", io.StringIO()) as stderr,
            ):
                self.assertEqual(main(["keycloak", "keycloak_token"]), 2)
                self.assertIn("MCP_CALL_TIMEOUT_SECONDS must be between 1 and 3600", stderr.getvalue())


class ParseAnswerTest(unittest.TestCase):
    def test_json_and_events(self) -> None:
        answer: dict[str, object] = {"jsonrpc": "2.0", "id": 2, "result": {}}
        self.assertEqual(parse_answer(json.dumps(answer).encode(), "application/json", 2), answer)
        events = b"event: message\r\ndata: not json\r\n\r\ndata: " + json.dumps(answer).encode() + b"\r\n\r\n"
        self.assertEqual(parse_answer(events, "text/event-stream", 2), answer)
        with self.assertRaisesRegex(CallError, "no answer to request 3"):
            parse_answer(json.dumps(answer).encode(), "application/json", 3)


if __name__ == "__main__":
    unittest.main()
