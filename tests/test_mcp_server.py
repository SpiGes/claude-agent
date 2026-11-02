import http.client
import json
import tempfile
import threading
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from mcp_server import (
    McpHttpServer,
    McpServer,
    Tool,
    ToolError,
    parse_serve_arguments,
    read_env_file,
)


def echo(arguments: Mapping[str, object]) -> str:
    return f"echo {arguments.get('text')}"


def refuse(_: Mapping[str, object]) -> str:
    message = "refused on purpose"
    raise ToolError(message)


def crash(_: Mapping[str, object]) -> str:
    message = "internal detail"
    raise RuntimeError(message)


SERVER = McpServer(
    "test-server",
    "1",
    [
        Tool("echo", "Echoes a text.", {"type": "object"}, echo),
        Tool("refuse", "Always refuses.", {"type": "object"}, refuse),
        Tool("crash", "Always fails.", {"type": "object"}, crash),
    ],
)


def call(tool: str, **arguments: object) -> dict[str, object]:
    message = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    answer = SERVER.handle(message)
    assert answer is not None
    return answer


class McpServerTest(unittest.TestCase):
    def test_initialize_and_ping(self) -> None:
        answer = SERVER.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}})
        self.assertEqual(
            answer,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "x",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "test-server", "version": "1"},
                },
            },
        )
        default = SERVER.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(default and default["result"]["protocolVersion"], "2025-06-18")  # type: ignore[index]
        self.assertEqual(
            SERVER.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"}), {"jsonrpc": "2.0", "id": 2, "result": {}}
        )

    def test_notification_has_no_answer(self) -> None:
        self.assertIsNone(SERVER.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tools_list(self) -> None:
        answer = SERVER.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert answer is not None
        result = cast("dict[str, list[dict[str, object]]]", answer["result"])
        tools = result["tools"]
        self.assertEqual(tools[0], {"name": "echo", "description": "Echoes a text.", "inputSchema": {"type": "object"}})
        self.assertEqual([tool["name"] for tool in tools], ["echo", "refuse", "crash"])

    def test_tools_call(self) -> None:
        self.assertEqual(
            call("echo", text="hi")["result"], {"content": [{"type": "text", "text": "echo hi"}], "isError": False}
        )
        self.assertEqual(
            call("refuse")["result"], {"content": [{"type": "text", "text": "refused on purpose"}], "isError": True}
        )
        with self.assertLogs("mcp-server", "ERROR"):
            failed = call("crash")["result"]
        self.assertTrue(failed["isError"])  # type: ignore[index]
        self.assertNotIn("internal detail", json.dumps(failed))

    def test_errors(self) -> None:
        for message, code in (
            ([1], -32600),
            ({"jsonrpc": "2.0", "id": 4, "method": "other"}, -32601),
            ({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "unknown"}}, -32602),
        ):
            with self.subTest(message=message):
                answer = SERVER.handle(message)
                assert answer is not None
                self.assertEqual(answer["error"]["code"], code)  # type: ignore[index]
        answer = SERVER.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "echo"}})
        self.assertEqual(answer and answer["result"]["content"][0]["text"], "echo None")  # type: ignore[index]


class SettingsTest(unittest.TestCase):
    def test_read_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "env"
            path.write_text("# comment\n\nKEY=value=with=signs\n OTHER = x \n", encoding="utf-8")
            self.assertEqual(read_env_file(path), {"KEY": "value=with=signs", "OTHER": " x "})
            path.write_text("no separator\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no KEY=value"):
                read_env_file(path)

    def test_serve_arguments(self) -> None:
        self.assertEqual(parse_serve_arguments(["--env-file", "/tmp/e", "--port", "9"], "usage"), (Path("/tmp/e"), 9))
        self.assertEqual(parse_serve_arguments(["--env-file", "/tmp/e"], "usage"), (Path("/tmp/e"), 8000))
        for args in ([], ["--port", "9"], ["--env-file"], ["--port", "x", "--env-file", "e"], ["--other"]):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "usage"):
                parse_serve_arguments(args, "usage")


class HttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.http_server = McpHttpServer(("127.0.0.1", 0), SERVER)
        threading.Thread(target=self.http_server.serve_forever, daemon=True).start()
        self.addCleanup(self.http_server.server_close)
        self.addCleanup(self.http_server.shutdown)
        self.connection = http.client.HTTPConnection("127.0.0.1", self.http_server.server_address[1], timeout=10)
        self.addCleanup(self.connection.close)

    def send(self, method: str, path: str, body: bytes | None = None, **headers: str) -> tuple[int, str, bytes]:
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        return response.status, response.getheader("Content-Type") or "", response.read()

    def test_post(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        self.assertEqual(
            self.send("POST", "/mcp", body), (200, "application/json", b'{"jsonrpc": "2.0", "id": 1, "result": {}}')
        )
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
        self.assertEqual(self.send("POST", "/mcp", notification)[0], 202)
        self.assertEqual(self.send("POST", "/mcp", b"not json")[0:3:2], (400, b"invalid JSON"))

    def test_other_requests(self) -> None:
        self.assertEqual(self.send("GET", "/mcp")[0], 405)
        self.assertEqual(self.send("DELETE", "/mcp")[0], 200)
        self.assertEqual(self.send("POST", "/other", b"{}")[0], 404)
        status = self.send("POST", "/mcp", b"x", **{"Content-Length": "abc"})[0]
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
