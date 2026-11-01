import io
import socket
import threading
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar, override

import git_credential_broker

REQUEST = "capability[]=authtype\nprotocol=https\nhost=devops-server.admin.ch\n"


class FakeBroker(BaseHTTPRequestHandler):
    """Answers like the broker: 200 with the credential, or 403 with the reason."""

    status = 200
    received: ClassVar[list[tuple[str, str]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        type(self).received.append((self.path, body))
        answer = b"username=token\npassword=pat\n" if self.status == 200 else b"group 'git' is closed\n"
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def fake_broker(status: int) -> Generator[str]:
    FakeBroker.status = status
    FakeBroker.received = []
    server = HTTPServer(("127.0.0.1", 0), FakeBroker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def run(argv: list[str], environ: dict[str, str], stdin: str = REQUEST) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = git_credential_broker.main(argv, environ, io.StringIO(stdin), out, err)
    return code, out.getvalue(), err.getvalue()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


class GitCredentialBrokerTest(unittest.TestCase):
    def test_store_and_erase_do_nothing(self) -> None:
        for action in ("store", "erase"):
            self.assertEqual(run([action], {"BROKER_URL": "http://broker"}), (0, "", ""))

    def test_without_broker_url_git_goes_on(self) -> None:
        self.assertEqual(run(["get"], {}), (0, "", ""))

    def test_answer_of_the_broker(self) -> None:
        with fake_broker(200) as url:
            code, out, err = run(["get"], {"BROKER_URL": url})
        self.assertEqual((code, out, err), (0, "username=token\npassword=pat\n", ""))
        self.assertEqual(FakeBroker.received, [("/v1/git", REQUEST)])

    def test_refusal_stops_git(self) -> None:
        with fake_broker(403) as url:
            code, out, err = run(["get"], {"BROKER_URL": url})
        self.assertEqual((code, out), (0, "quit=1\n"))
        self.assertEqual(err, "git-credential-broker: group 'git' is closed\n")

    def test_unreachable_broker_stops_git(self) -> None:
        code, out, err = run(["get"], {"BROKER_URL": f"http://127.0.0.1:{closed_port()}"})
        self.assertEqual((code, out), (0, "quit=1\n"))
        self.assertIn("secret broker unreachable", err)
        self.assertIn("claude_broker", err)

    def test_only_http_urls(self) -> None:
        code, out, err = run(["get"], {"BROKER_URL": "file:///etc/passwd"})
        self.assertEqual((code, out), (0, "quit=1\n"))
        self.assertIn("isn't an http(s) URL", err)


if __name__ == "__main__":
    unittest.main()
