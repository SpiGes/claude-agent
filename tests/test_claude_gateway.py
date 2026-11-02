import http.client
import io
import json
import socket
import tempfile
import threading
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, override
from unittest import mock

import claude_gateway
from claude_gateway import (
    CONFLUENCE_POLICY,
    DEVOPS_POLICY,
    JIRA_POLICY,
    POLICIES,
    Approvals,
    Gateway,
    GatewayHTTPServer,
    Route,
    Settings,
    ToolCall,
    agent_name,
    details,
    parse_routes,
    run_admin_command,
    summarize,
    without_denied_tools,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def tool_call(tool: str, call_id: int = 7, **arguments: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": call_id, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}


def write_call(tool: str = "devops_work_item_update", summary: str = "") -> ToolCall:
    return ToolCall("devops", "DevOps", tool, summary, "{}", "agent dev 2")


TOOLS_LIST: dict[str, object] = {
    "jsonrpc": "2.0",
    "id": 2,
    "result": {
        "tools": [{"name": "devops_work_item_get"}, {"name": "devops_work_item_delete"}, {"name": "devops_wiki_x"}]
    },
}


class FakeMcp(BaseHTTPRequestHandler):
    """MCP server of the tests: a POST is answered with two server-sent events; the second waits for `release`."""

    protocol_version = "HTTP/1.1"
    release = threading.Event()
    received: ClassVar[list[tuple[str, str, dict[str, str]]]] = []

    def _record(self, body: str) -> None:
        type(self).received.append((self.command, body, dict(self.headers.items())))

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        self._record(body)
        if '"method": "notifications/' in body or '"method":"notifications/' in body:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if '"tools/list"' in body:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._chunk(b"event: message\r\ndata: " + json.dumps(TOOLS_LIST).encode() + b"\r\n\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        self.send_header("Mcp-Session-Id", "session-1")
        self.send_header("X-Internal", "not forwarded")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self._chunk(b"event: message\ndata: first\n\n")
        type(self).release.wait(5)
        self._chunk(b"event: message\ndata: second\n\n")
        self.wfile.write(b"0\r\n\r\n")

    def do_DELETE(self) -> None:
        self._record("")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def serving(server: ThreadingHTTPServer) -> Generator[int]:
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


class GatewayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.notifications: list[str] = []
        self.route = Route("devops", "http://127.0.0.1:1/mcp", DEVOPS_POLICY)
        self.gateway = self.make_gateway(self.route, approval_seconds=5)

    def make_gateway(self, route: Route, approval_seconds: float) -> Gateway:
        settings = Settings(routes=(route,), approval_seconds=approval_seconds)
        return Gateway(settings, clock=self.clock, notifier=self.notifications.append)

    def decide_soon(self, gateway: Gateway, *args: str, approve: bool = True) -> threading.Thread:
        """Approve or deny the next waiting call from another thread, as the person would on the host."""

        def decide() -> None:
            for _ in range(500):
                if gateway.approvals.pending_lines():
                    gateway.approvals.decide(list(args), approve=approve)
                    return
                threading.Event().wait(0.01)

        thread = threading.Thread(target=decide, daemon=True)
        thread.start()
        return thread


class PolicyTest(unittest.TestCase):
    def test_classify(self) -> None:
        self.assertEqual(DEVOPS_POLICY.classify("devops_work_item_get"), "read")
        self.assertEqual(DEVOPS_POLICY.classify("devops_pull_request_create_comment"), "write")
        self.assertEqual(DEVOPS_POLICY.classify("devops_work_item_link_update"), "write")
        self.assertEqual(DEVOPS_POLICY.classify("devops_new_tool_of_a_later_version"), "write")
        self.assertEqual(DEVOPS_POLICY.classify("devops_work_item_delete"), "denied")
        self.assertEqual(DEVOPS_POLICY.classify("devops_repository_get"), "denied")
        self.assertEqual(DEVOPS_POLICY.classify("devops_wiki_page_get"), "denied")

    def test_classify_jira_read_only(self) -> None:
        self.assertEqual(JIRA_POLICY.classify("jira_get_issue"), "read")
        self.assertEqual(JIRA_POLICY.classify("jira_search"), "read")
        for tool in (
            "jira_create_issue",
            "jira_add_comment",
            "jira_transition_issue",
            "jira_new_tool_of_a_later_version",
        ):
            with self.subTest(tool=tool):
                self.assertEqual(JIRA_POLICY.classify(tool), "denied")

    def test_classify_confluence(self) -> None:
        self.assertEqual(CONFLUENCE_POLICY.classify("confluence_get_page"), "read")
        self.assertEqual(CONFLUENCE_POLICY.classify("confluence_download_attachment"), "read")
        for tool in (
            "confluence_update_page",
            "confluence_upload_attachments",
            "confluence_new_tool_of_a_later_version",
        ):
            with self.subTest(tool=tool):
                self.assertEqual(CONFLUENCE_POLICY.classify(tool), "write")
        for tool in ("confluence_delete_page", "confluence_move_page", "confluence_set_page_restrictions"):
            with self.subTest(tool=tool):
                self.assertEqual(CONFLUENCE_POLICY.classify(tool), "denied")

    def test_policies_follow_the_rules_of_the_dev_profile(self) -> None:
        """A tool denied by settings.json is refused by the gateway, a tool that asks for confirmation is a write."""
        settings = json.loads((Path(__file__).parents[1] / "profiles/dev/settings.json").read_text(encoding="utf-8"))
        for kind, expected in (("deny", "denied"), ("ask", "write")):
            for rule in settings["permissions"][kind]:
                server, _, tool = rule.removeprefix("mcp__").partition("__")
                if not rule.startswith("mcp__") or server not in POLICIES:
                    continue
                # A rule that ends with * stands for every tool of that prefix, e.g. a tool of a later version.
                name = tool.removesuffix("*") + ("_of_a_later_version" if tool.endswith("*") else "")
                with self.subTest(rule=rule):
                    self.assertEqual(POLICIES[server].classify(name), expected)


class SettingsTest(unittest.TestCase):
    def test_environment(self) -> None:
        settings = Settings.from_environment(
            {
                "GATEWAY_PORT": "9000",
                "GATEWAY_ROUTES": "devops=http://claude-mcp-devops:8000/mcp",
                "GATEWAY_IDLE_MINUTES": "10",
                "GATEWAY_MAX_HOURS": "1",
                "GATEWAY_APPROVAL_SECONDS": "30",
                "GATEWAY_LOG": "/x/g.log",
                "GATEWAY_ADMIN_SOCKET": "/x/a.sock",
            }
        )
        self.assertEqual(settings.port, 9000)
        self.assertEqual(settings.routes, (Route("devops", "http://claude-mcp-devops:8000/mcp", DEVOPS_POLICY),))
        self.assertEqual((settings.idle_seconds, settings.max_seconds, settings.approval_seconds), (600, 3600, 30))
        self.assertEqual((settings.log_file, settings.admin_socket), (Path("/x/g.log"), Path("/x/a.sock")))
        self.assertEqual(Settings.from_environment({}), Settings())
        routes = parse_routes(
            "devops=http://claude-mcp-devops:8000/mcp,jira=http://claude-mcp-jira:8000/mcp,"
            "confluence=http://claude-mcp-confluence:8000/mcp"
        )
        self.assertEqual([route.policy for route in routes], [DEVOPS_POLICY, JIRA_POLICY, CONFLUENCE_POLICY])

    def test_invalid_routes(self) -> None:
        for text in ("unknown=http://x/mcp", "devops=https://x/mcp", "devops"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "invalid route"):
                parse_routes(text)
        self.assertEqual(parse_routes(" , "), ())


class AgentNameTest(unittest.TestCase):
    def test_name_of_the_container(self) -> None:
        for host, expected in (
            ("claude-agent-dev-2.bfs-claude-agent", "agent dev 2"),
            ("claude-agent-qualitycheck-1", "agent qualitycheck 1"),
            ("cb52821ed763.bfs-claude-agent", "an unknown agent (172.21.0.4)"),
            ("claude-agent-dev-x.bfs-claude-agent", "an unknown agent (172.21.0.4)"),
        ):
            with self.subTest(host=host):

                def resolve(_: str, host: str = host) -> tuple[str, list[str], list[str]]:
                    return host, [], []

                self.assertEqual(agent_name("172.21.0.4", resolve), expected)

    def test_address_without_name(self) -> None:
        def no_name(address: str) -> tuple[str, list[str], list[str]]:
            raise socket.herror(1, f"no name for {address}")

        self.assertEqual(agent_name("172.21.0.9", no_name), "an unknown agent (172.21.0.9)")


class SummarizeTest(unittest.TestCase):
    def test_summary(self) -> None:
        self.assertEqual(
            summarize({"pull_request_id": 44283, "content": "long text", "nested": {"a": 1}, "status": "fixed"}),
            "pull_request_id=44283, status=fixed",
        )
        self.assertEqual(summarize("not arguments"), "")
        self.assertTrue(summarize({"title": "x" * 200}).endswith("…"))
        self.assertLessEqual(len(summarize({f"k{n}": "v" * 50 for n in range(10)})), 160)
        self.assertEqual(summarize({"title": "two\nlines"}), "title=two lines")

    def test_details(self) -> None:
        self.assertEqual(details({"text": "é", "id": 1}), '{\n  "id": 1,\n  "text": "é"\n}')
        long_text = details({"text": "x" * 5000})
        self.assertTrue(long_text.endswith("characters in all)"))
        self.assertLess(len(long_text), 4100)
        # An encoded file is shown by its length, so that the other arguments stay visible.
        attachment = details({"content_base64": "A" * 50000, "content_id": "123", "filename": "a.png"})
        self.assertEqual(
            attachment,
            '{\n  "content_base64": "(50000 characters of base64)",\n  "content_id": "123",\n  "filename": "a.png"\n}',
        )
        self.assertEqual(
            summarize({"content_base64": "A" * 10, "new_content": "x", "filename": "a.png"}), "filename=a.png"
        )

    def test_without_denied_tools(self) -> None:
        expected = [{"name": "devops_work_item_get"}]
        as_json = without_denied_tools(json.dumps(TOOLS_LIST).encode(), "application/json", DEVOPS_POLICY)
        self.assertEqual(json.loads(as_json)["result"]["tools"], expected)
        event = b"event: message\ndata: " + json.dumps(TOOLS_LIST).encode() + b"\n\n"
        lines = without_denied_tools(event, "text/event-stream", DEVOPS_POLICY).decode().split("\n")
        self.assertEqual(lines[0], "event: message")
        self.assertEqual(json.loads(lines[1].removeprefix("data: "))["result"]["tools"], expected)
        self.assertEqual(lines[2:], ["", ""])
        for body, content_type in (
            (b"not json", "application/json"),
            (b'{"result": {}}', "application/json"),
            (b"data: not json\n\n", "text/event-stream"),
            (json.dumps(TOOLS_LIST).encode(), "text/plain"),
        ):
            with self.subTest(body=body, content_type=content_type):
                self.assertEqual(without_denied_tools(body, content_type, DEVOPS_POLICY), body)


class ApprovalsTest(GatewayTestCase):
    def test_approve_the_oldest_call(self) -> None:
        thread = self.decide_soon(self.gateway)
        self.assertEqual(self.gateway.approvals.request(write_call(summary="id=1")), (True, "approved"))
        thread.join()
        self.assertEqual(self.notifications, ["Écriture DevOps à approuver pour agent dev 2 : devops_work_item_update"])

    def test_deny(self) -> None:
        thread = self.decide_soon(self.gateway, "1", approve=False)
        self.assertEqual(self.gateway.approvals.request(write_call()), (False, "denied"))
        thread.join()

    def test_timeout(self) -> None:
        approvals = Approvals(0.05, self.clock, self.notifications.append)
        self.assertEqual(approvals.request(write_call()), (False, "no approval in time"))
        self.assertEqual(approvals.pending_lines(), [])

    def test_approval_for_the_tool(self) -> None:
        thread = self.decide_soon(self.gateway, "--tool", "5m")
        self.assertTrue(self.gateway.approvals.request(write_call("devops_pull_request_create_comment"))[0])
        thread.join()
        self.assertEqual(
            self.gateway.approvals.request(write_call("devops_pull_request_create_comment")),
            (True, "approved for the tool"),
        )
        self.clock.now += 5 * 60
        approvals_timeout = Approvals(0.05, self.clock, self.notifications.append)
        self.assertFalse(approvals_timeout.request(write_call("devops_pull_request_create_comment"))[0])

    def test_decision_errors(self) -> None:
        with self.assertRaisesRegex(ValueError, "no call is waiting"):
            self.gateway.approvals.decide([], approve=True)
        blocked = threading.Thread(target=self.gateway.approvals.request, args=(write_call(),), daemon=True)
        blocked.start()
        while not self.gateway.approvals.pending_lines():
            threading.Event().wait(0.01)
        self.assertEqual(self.gateway.approvals.pending_lines(), ["  1: agent dev 2, DevOps devops_work_item_update"])
        for args, approve, expected in (
            (["9"], True, "no waiting call 9 \\(waiting: 1\\)"),
            (["x"], True, "invalid argument 'x'"),
            (["--tool", "5"], True, "invalid argument '--tool'"),
            (["--tool", "5m"], False, "invalid argument '--tool'"),
            (["1", "2"], True, "invalid argument '2'"),
        ):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, expected):
                self.gateway.approvals.decide(args, approve=approve)
        self.gateway.approvals.decide(["1"], approve=False)
        blocked.join()

    def test_decision_just_after_the_timeout_counts(self) -> None:
        # The person approves while the wait times out: the call is approved, as the person was told.
        def approve_at_once(_: str) -> None:
            approvals.decide([], approve=True)

        approvals = Approvals(5, self.clock, approve_at_once)
        with mock.patch.object(threading.Event, "wait", return_value=False):
            self.assertEqual(approvals.request(write_call()), (True, "approved"))

    def test_closing_the_group_forgets_the_tools_approved(self) -> None:
        self.gateway.allow("devops")
        thread = self.decide_soon(self.gateway, "--tool", "5m")
        self.assertIsNone(self.gateway.check(self.route, tool_call("devops_work_item_update", work_item_id=1)))
        thread.join()
        self.gateway.revoke("devops")
        self.gateway.allow("devops")
        thread = self.decide_soon(self.gateway, approve=False)
        refusal = self.gateway.check(self.route, tool_call("devops_work_item_update", work_item_id=1))
        thread.join()
        self.assertEqual(refusal, "the write call devops_work_item_update was denied on the host")


class CheckTest(GatewayTestCase):
    def test_other_messages_go_through_even_when_closed(self) -> None:
        for message in ({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, {"method": "notifications/initialized"}):
            with self.subTest(message=message):
                self.assertIsNone(self.gateway.check(self.route, message))
        self.assertEqual(self.gateway.check(self.route, [1]), "a single JSON-RPC message is expected")

    def test_denied_tool(self) -> None:
        self.gateway.allow("devops")
        refusal = self.gateway.check(self.route, tool_call("devops_work_item_delete", id=1))
        self.assertEqual(refusal, "the tool devops_work_item_delete is refused by the MCP gateway")

    def test_write_on_a_read_only_route_is_refused_without_approval(self) -> None:
        route = Route("jira", "http://127.0.0.1:1/mcp", JIRA_POLICY)
        gateway = self.make_gateway(route, approval_seconds=5)
        gateway.allow("jira")
        refusal = gateway.check(route, tool_call("jira_create_issue", project_key="SIS2"))
        self.assertEqual(refusal, "the tool jira_create_issue is refused by the MCP gateway")
        self.assertEqual(gateway.approvals.pending_lines(), [])
        self.assertIsNone(gateway.check(route, tool_call("jira_get_issue", issue_key="SIS2-472")))

    def test_closed_group(self) -> None:
        refusal = self.gateway.check(self.route, tool_call("devops_work_item_get", work_item_id=1))
        self.assertIn("claude_secret allow devops", refusal or "")
        self.assertEqual(self.notifications, ["Autorisation requise pour devops"])

    def test_read_goes_through_while_open(self) -> None:
        self.gateway.allow("devops")
        self.assertIsNone(self.gateway.check(self.route, tool_call("devops_work_item_get", work_item_id=1)))
        for malformed in (
            {"method": "tools/call", "params": "malformed"},
            {"method": "tools/call", "params": {"name": 1}},
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(self.gateway.check(self.route, malformed), "invalid tool call: no tool name")

    def test_write_denied_on_the_host(self) -> None:
        self.gateway.allow("devops")
        thread = self.decide_soon(self.gateway, approve=False)
        refusal = self.gateway.check(self.route, tool_call("devops_work_item_update", work_item_id=1))
        thread.join()
        self.assertEqual(refusal, "the write call devops_work_item_update was denied on the host")

    def test_write_needs_an_approval(self) -> None:
        self.gateway.allow("devops")
        thread = self.decide_soon(self.gateway)
        self.assertIsNone(self.gateway.check(self.route, tool_call("devops_work_item_update", work_item_id=1)))
        thread.join()
        gateway = self.make_gateway(self.route, approval_seconds=0.05)
        gateway.allow("devops")
        refusal = gateway.check(self.route, tool_call("devops_work_item_update", work_item_id=1))
        self.assertIn("wasn't approved within 0 s", refusal or "")


class AdminCommandTest(GatewayTestCase):
    def test_commands(self) -> None:
        self.assertEqual(run_admin_command(self.gateway, {"command": "pending"}), (True, "No waiting call."))
        ok, message = run_admin_command(self.gateway, {"command": "allow", "args": ["devops"]})
        self.assertTrue(ok, message)
        ok, message = run_admin_command(self.gateway, {"command": "status"})
        self.assertTrue(ok)
        self.assertIn("devops     open", message)
        self.assertIn("No waiting call.", message)
        self.assertEqual(
            run_admin_command(self.gateway, {"command": "revoke", "args": ["all"]}), (True, "Closed: devops")
        )
        self.assertEqual(
            run_admin_command(self.gateway, {"command": "revoke", "args": ["all"]}), (True, "Closed: no open group")
        )

    def test_status_and_approve_with_a_waiting_call(self) -> None:
        self.gateway.allow("devops")
        result: list[str | None] = []
        thread = threading.Thread(
            target=lambda: result.append(
                self.gateway.check(self.route, tool_call("devops_work_item_update", work_item_id=3))
            ),
            daemon=True,
        )
        thread.start()
        while not self.gateway.approvals.pending_lines():
            threading.Event().wait(0.01)
        status = run_admin_command(self.gateway, {"command": "status"})[1]
        self.assertIn("Waiting calls:\n  1: an unknown agent, DevOps devops_work_item_update (work_item_id=3)", status)
        self.assertEqual(
            run_admin_command(self.gateway, {"command": "pending"})[1],
            "  1: an unknown agent, DevOps devops_work_item_update (work_item_id=3)\n"
            '      {\n        "work_item_id": 3\n      }',
        )
        ok, message = run_admin_command(self.gateway, {"command": "approve", "args": ["--tool", "5m"]})
        self.assertEqual(
            (ok, message),
            (True, "Call 1 approved: devops_work_item_update, and the next calls of devops_work_item_update for 5 min"),
        )
        thread.join()
        self.assertEqual(result, [None])

    def test_refusals(self) -> None:
        self.assertEqual(run_admin_command(self.gateway, {"command": "deny"}), (False, "no call is waiting"))
        self.assertEqual(run_admin_command(self.gateway, {"command": "allow", "args": ["jira"]})[0], False)
        self.assertEqual(
            run_admin_command(self.gateway, {"command": "status", "args": ["x"]}), (False, "unknown command: status x")
        )
        self.assertEqual(run_admin_command(self.gateway, {"command": "rm"}), (False, "unknown command: rm"))


class HttpTest(GatewayTestCase):
    def setUp(self) -> None:
        super().setUp()
        FakeMcp.release.clear()
        FakeMcp.received = []
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeMcp)
        self.upstream.daemon_threads = True
        upstream_port = self.enterContext(serving(self.upstream))
        self.route = Route("devops", f"http://127.0.0.1:{upstream_port}/mcp", DEVOPS_POLICY)
        self.gateway = self.make_gateway(self.route, approval_seconds=5)
        self.port = self.enterContext(serving(GatewayHTTPServer(("127.0.0.1", 0), self.gateway)))

    def post(self, message: object, headers: dict[str, str] | None = None) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("POST", "/mcp/devops", body=json.dumps(message), headers=headers or {})
        return connection.getresponse()

    def test_stream_is_forwarded_as_it_comes(self) -> None:
        response = self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"Content-Type": "application/json", "Mcp-Session-Id": "session-1", "X-Secret": "dropped"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(response.getheader("Mcp-Session-Id"), "session-1")
        self.assertIsNone(response.getheader("X-Internal"))
        self.assertEqual(response.read1(100), b"event: message\ndata: first\n\n")
        FakeMcp.release.set()
        self.assertEqual(response.read(), b"event: message\ndata: second\n\n")
        method, body, headers = FakeMcp.received[0]
        self.assertEqual((method, json.loads(body)["method"]), ("POST", "initialize"))
        self.assertEqual(headers.get("Mcp-Session-Id"), "session-1")
        self.assertNotIn("X-Secret", headers)

    def test_notification_and_delete(self) -> None:
        response = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((response.status, response.read()), (202, b""))
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("DELETE", "/mcp/devops")
        self.assertEqual(connection.getresponse().status, 200)

    def test_refused_tool_call_is_a_tool_error(self) -> None:
        response = self.post(tool_call("devops_work_item_get", call_id=42, work_item_id=1))
        self.assertEqual(response.status, 200)
        answer = json.loads(response.read())
        self.assertEqual(answer["id"], 42)
        self.assertTrue(answer["result"]["isError"])
        self.assertIn("claude_secret allow devops", answer["result"]["content"][0]["text"])
        self.assertEqual(FakeMcp.received, [])

    def test_read_tool_call_goes_through_while_open(self) -> None:
        self.gateway.allow("devops")
        FakeMcp.release.set()
        response = self.post(tool_call("devops_work_item_get", work_item_id=1))
        self.assertEqual(response.read(), b"event: message\ndata: first\n\nevent: message\ndata: second\n\n")

    def test_tools_list_without_denied_tools(self) -> None:
        response = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(response.status, 200)
        data = response.read().decode()
        self.assertEqual(response.getheader("Content-Length"), str(len(data.encode())))
        message = json.loads(data.split("\r\n")[1].removeprefix("data: "))
        self.assertEqual(message["result"]["tools"], [{"name": "devops_work_item_get"}])

    def test_errors(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        for method, path, body, headers, status in (
            ("POST", "/other", "{}", {}, 404),
            ("GET", "/mcp/jira", None, {}, 404),
            ("POST", "/mcp/devops", "x", {"Content-Length": "abc"}, 400),
            ("POST", "/mcp/devops", "not json", {}, 400),
            ("POST", "/mcp/devops", "x" * (claude_gateway.MAX_REQUEST_BYTES + 1), {}, 413),
        ):
            with self.subTest(method=method, path=path, status=status):
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, status)
                connection.close()

    def test_error_before_the_body_closes_the_connection(self) -> None:
        """The next request on a kept-alive connection isn't read from the unread body of a refused one."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("POST", "/mcp/unknown", body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}))
        response = connection.getresponse()
        self.assertEqual((response.status, response.read()), (404, b"unknown path"))
        self.assertEqual(response.getheader("Connection"), "close")
        # http.client opens a new connection, as an MCP client does after Connection: close.
        connection.request("POST", "/mcp/devops", body=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize"}))
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 200)

    def test_write_given_up_by_the_agent_is_not_forwarded(self) -> None:
        """A write approved after the agent closed its connection doesn't reach the MCP server."""
        self.gateway.allow("devops")
        body = json.dumps(tool_call("devops_work_item_update", work_item_id=1)).encode()
        client = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        client.sendall(
            b"POST /mcp/devops HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        with self.assertLogs("claude-gateway", "INFO") as logs:
            for _ in range(500):
                if self.gateway.approvals.pending_lines():
                    break
                threading.Event().wait(0.01)
            client.close()
            self.gateway.approvals.decide([], approve=True)
            for _ in range(500):
                if any("not forwarded" in line for line in logs.output):
                    break
                threading.Event().wait(0.01)
        self.assertTrue(any("not forwarded" in line for line in logs.output), logs.output)
        self.assertEqual([body for _, body, _ in FakeMcp.received if "tools/call" in body], [])

    def test_unreachable_mcp_server(self) -> None:
        route = Route("devops", "http://127.0.0.1:1/mcp", DEVOPS_POLICY)
        gateway = self.make_gateway(route, approval_seconds=5)
        port = self.enterContext(serving(GatewayHTTPServer(("127.0.0.1", 0), gateway)))
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("GET", "/mcp/devops")
        response = connection.getresponse()
        self.assertEqual(response.status, 502)
        self.assertIn("claude_mcp start devops", response.read().decode())


class MainTest(unittest.TestCase):
    def test_usage(self) -> None:
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(claude_gateway.main([]), 2)

    def test_ctl(self) -> None:
        with mock.patch.object(claude_gateway, "run_ctl", return_value=7) as ctl:
            self.assertEqual(claude_gateway.main(["ctl", "pending"]), 7)
        self.assertEqual(ctl.call_args.args[0], ["pending"])

    def test_serve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environ = {
                "GATEWAY_PORT": "0",
                "GATEWAY_ROUTES": "devops=http://claude-mcp-devops:8000/mcp",
                "GATEWAY_LOG": str(Path(directory) / "gateway.log"),
                "GATEWAY_ADMIN_SOCKET": str(Path(directory) / "run" / "admin.sock"),
            }
            with (
                mock.patch.dict("os.environ", environ),
                mock.patch.object(GatewayHTTPServer, "serve_forever") as serve_forever,
            ):
                self.assertEqual(claude_gateway.main(["serve"]), 0)
            handler = claude_gateway.LOGGER.handlers[-1]
            claude_gateway.LOGGER.removeHandler(handler)
            handler.close()
            serve_forever.assert_called_once()
            self.assertTrue((Path(directory) / "run" / "admin.sock").is_socket())
            log = (Path(directory) / "gateway.log").read_text(encoding="utf-8")
            self.assertIn("started (routes devops=http://claude-mcp-devops:8000/mcp; idle limit 30 min", log)


if __name__ == "__main__":
    unittest.main()
