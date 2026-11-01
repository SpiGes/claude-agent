import io
import json
import logging
import socket
import tempfile
import threading
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

import access
from access import WindowClosedError, Windows, format_duration


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def join_daemon_threads() -> None:
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=5)


class FormatDurationTest(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(format_duration(-5), "0 s")
        self.assertEqual(format_duration(20), "20 s")
        self.assertEqual(format_duration(60), "1 min")
        self.assertEqual(format_duration(59 * 60 + 59), "59 min")
        self.assertEqual(format_duration(3600), "1 h 00 min")
        self.assertEqual(format_duration(4 * 3600 - 60), "3 h 59 min")


class NotifierTest(unittest.TestCase):
    def test_without_agent_notify(self) -> None:
        with mock.patch("shutil.which", return_value=None), mock.patch("threading.Thread") as thread:
            access.agent_notifier()("text")
        thread.assert_not_called()

    def test_with_agent_notify(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/agent-notify"), mock.patch("subprocess.run") as run:
            access.agent_notifier()("Autorisation requise pour git")
            join_daemon_threads()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["/bin/agent-notify", "Autorisation requise pour git"])

    def test_failure_is_ignored(self) -> None:
        with mock.patch("shutil.which", return_value="/nonexistent/agent-notify"):
            access.agent_notifier()("text")
            join_daemon_threads()


class WindowsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.notifications: list[str] = []
        self.closed: list[tuple[str, str]] = []
        self.windows = Windows(
            ("devops", "jira"),
            idle_seconds=30 * 60,
            max_seconds=4 * 3600,
            clock=self.clock,
            notifier=self.notifications.append,
            on_close=lambda group, reason: self.closed.append((group, reason)),
        )

    def test_closed_group_notifies_once_per_interval(self) -> None:
        with self.assertRaises(WindowClosedError) as raised:
            self.windows.use("devops")
        self.assertIn("claude_secret allow devops", str(raised.exception))
        with self.assertRaises(WindowClosedError):
            self.windows.use("devops")
        self.assertEqual(self.notifications, ["Autorisation requise pour devops"])
        self.clock.now += access.NOTIFY_INTERVAL_SECONDS
        with self.assertRaises(WindowClosedError):
            self.windows.use("devops")
        self.assertEqual(len(self.notifications), 2)

    def test_idle_limit_is_moved_by_each_use(self) -> None:
        self.assertIn("closes after 30 min without use", self.windows.open("devops"))
        self.clock.now += 29 * 60
        self.windows.use("devops")
        self.clock.now += 29 * 60
        self.windows.use("devops")
        self.clock.now += 30 * 60
        with self.assertRaises(WindowClosedError):
            self.windows.use("devops")
        self.assertEqual(self.closed, [("devops", "idle")])

    def test_maximum_time_even_when_used(self) -> None:
        self.windows.open("devops")
        for _ in range(15):
            self.clock.now += 15 * 60
            self.windows.use("devops")
        self.clock.now += 15 * 60
        with self.assertRaises(WindowClosedError):
            self.windows.use("devops")
        self.assertEqual(self.closed, [("devops", "maximum time")])

    def test_status_closes_expired_groups(self) -> None:
        self.windows.open("devops")
        self.clock.now += 10 * 60
        self.assertEqual(
            self.windows.status_lines(),
            [
                "  devops     open, closes in 20 min (idle limit 20 min, maximum 3 h 50 min)",
                "  jira       closed",
            ],
        )
        self.clock.now += 20 * 60
        self.assertEqual(self.windows.status_lines()[0], "  devops     closed")
        self.assertEqual(self.closed, [("devops", "idle")])

    def test_revoke_and_unknown_groups(self) -> None:
        self.windows.open("devops")
        self.windows.open("jira")
        self.assertEqual(self.windows.revoke("jira"), ["jira"])
        self.assertEqual(self.windows.revoke("all"), ["devops"])
        self.assertEqual(self.windows.revoke("all"), [])
        self.assertEqual(self.closed, [("jira", "revoked"), ("devops", "revoked")])
        for action in (self.windows.open, self.windows.revoke):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(ValueError, "unknown group 'gti'"):
                action("gti")
        with self.assertRaisesRegex(ValueError, "unknown group 'all'"):
            self.windows.open("all")

    def test_close_all_reports_nothing(self) -> None:
        self.windows.open("devops")
        self.windows.close_all()
        self.assertEqual(self.windows.status_lines()[0], "  devops     closed")
        self.assertEqual(self.closed, [])


class AdminTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.socket_path = Path(directory.name) / "run" / "admin.sock"
        self.requests: list[Mapping[str, object]] = []

    def command(self, request: Mapping[str, object]) -> tuple[bool, str]:
        self.requests.append(request)
        return request.get("command") != "fail", f"done {access.admin_arguments(request)}"

    def ctl(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = access.run_ctl(list(args), self.socket_path, "usage", stdin_command="load")
        return code, out.getvalue(), err.getvalue()

    def test_not_ready_and_usage(self) -> None:
        self.assertEqual(self.ctl("status")[0], access.EXIT_NOT_READY)
        code, _, err = self.ctl()
        self.assertEqual((code, err), (2, "usage\n"))

    def test_commands_through_the_socket(self) -> None:
        server = access.AdminServer(self.socket_path, self.command)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual(self.socket_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.socket_path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.ctl("allow", "git"), (0, "done ['git']\n", ""))
        self.assertEqual(self.ctl("fail"), (1, "", "done []\n"))
        self.assertEqual(self.ctl("unlocked"), (0, "", ""))
        self.ctl("load", stdin="KEY=value\n")
        self.assertEqual(self.requests[-1], {"command": "load", "args": [], "data": "KEY=value\n"})
        self.assertEqual(access.admin_arguments({"args": "not a list"}), [])
        for raw in (b"not json\n", b"[1]\n"):
            with self.subTest(raw=raw), socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.socket_path))
                client.sendall(raw)
                answer = json.loads(client.makefile("r", encoding="utf-8").readline())
            self.assertFalse(answer["ok"])


class LogTest(unittest.TestCase):
    def test_rotation_and_format(self) -> None:
        logger = logging.getLogger("test-access")
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "service.log"
            log_file.write_bytes(b"x" * (access.MAX_LOG_BYTES + 1))
            access.configure_log(logger, log_file)
            handler = logger.handlers[-1]
            try:
                logger.info("after rotation")
            finally:
                logger.removeHandler(handler)
                handler.close()
            self.assertEqual(Path(f"{log_file}.1").stat().st_size, access.MAX_LOG_BYTES + 1)
            self.assertRegex(
                log_file.read_text(encoding="utf-8"), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ after rotation\n$"
            )


if __name__ == "__main__":
    unittest.main()
