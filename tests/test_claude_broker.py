import http.client
import io
import json
import logging
import socket
import tempfile
import threading
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from unittest import mock

import access
import claude_broker
from claude_broker import (
    Broker,
    BrokerError,
    BrokerHTTPServer,
    GitEntry,
    Secrets,
    SecretsFileError,
    Settings,
    run_admin_command,
)

SECRETS = """# Git tokens
GIT_1_URL=https://devops-server.admin.ch
GIT_1_PASSWORD=devops-pat
GIT_2_URL=https://github.com/SpiGes
GIT_2_USERNAME=x-access-token
GIT_2_PASSWORD=github-spiges
GIT_3_URL=https://bitbucket.bit.admin.ch/scm/p/r.git
GIT_3_BEARER=bitbucket-repo
GIT_4_URL=https://bitbucket.bit.admin.ch/
GIT_4_PASSWORD=bitbucket-wide
GH_SPIGES_TOKEN=gh-spiges
GH_SPIGES_OWNER=SpiGes
GH_OTHER_TOKEN=gh-other
GH_OTHER_OWNER=Rundschreiben-Kodierer
LATER_PHASE_SECRET=later\r
"""
SECRET_VALUES = ("devops-pat", "github-spiges", "bitbucket-repo", "bitbucket-wide", "gh-spiges", "gh-other", "later")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def git_request(host: str, path: str = "", *, authtype: bool = True) -> str:
    lines = ["capability[]=authtype"] if authtype else []
    lines += ["protocol=https", f"host={host}"] + ([f"path={path}"] if path else [])
    return "\n".join(lines) + "\n"


class BrokerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.notifications: list[str] = []
        self.settings = Settings(idle_seconds=30 * 60, max_seconds=4 * 3600)
        self.broker = Broker(self.settings, clock=self.clock, notifier=self.notifications.append)

    def unlock(self, *groups: str) -> None:
        self.broker.load(SECRETS)
        for group in groups:
            self.broker.allow(group)


class SettingsTest(unittest.TestCase):
    def test_defaults(self) -> None:
        settings = Settings.from_environment({})
        self.assertEqual(settings, Settings())
        self.assertEqual(settings.idle_seconds, 1800)
        self.assertEqual(settings.max_seconds, 14400)

    def test_environment(self) -> None:
        settings = Settings.from_environment(
            {
                "BROKER_PORT": "9000",
                "BROKER_IDLE_MINUTES": "0.5",
                "BROKER_MAX_HOURS": "2",
                "BROKER_LOG": "/x/b.log",
                "BROKER_ADMIN_SOCKET": "/x/a.sock",
            }
        )
        self.assertEqual(settings.port, 9000)
        self.assertEqual(settings.idle_seconds, 30)
        self.assertEqual(settings.max_seconds, 7200)
        self.assertEqual(settings.log_file, Path("/x/b.log"))
        self.assertEqual(settings.admin_socket, Path("/x/a.sock"))


class SecretsTest(unittest.TestCase):
    def test_parse(self) -> None:
        secrets = Secrets.parse(SECRETS)
        self.assertEqual([entry.name for entry in secrets.git], ["GIT_1", "GIT_2", "GIT_3", "GIT_4"])
        self.assertEqual(secrets.git[0].username, "token")
        self.assertEqual(secrets.git[3].url, "https://bitbucket.bit.admin.ch")
        self.assertEqual({entry.owner for entry in secrets.gh}, {"SpiGes", "Rundschreiben-Kodierer"})
        self.assertEqual(dict(secrets.others), {"LATER_PHASE_SECRET": "later"})
        self.assertEqual(secrets.summary, "4 Git tokens, 2 GitHub API tokens, 1 other secrets")

    def test_plain_gh_token_has_no_owner(self) -> None:
        secrets = Secrets.parse("GH_TOKEN=t\n")
        self.assertEqual(secrets.gh[0].name, "GH_TOKEN")
        self.assertIsNone(secrets.gh[0].owner)

    def test_errors_give_lines_and_keys_never_values(self) -> None:
        text = """not a key value
lower=x
GIT_1_URL= padded-secret\x20
GIT_2_URL=
GIT_3_URL=http://plain
GIT_3_PASSWORD=p3
GIT_4_URL=https://x
GIT_4_PASSWORD=p4
GIT_4_BEARER=b4
GIT_5_PASSWORD=p5
GH_X_TOKEN=t
GH_Y_OWNER=o
"""
        with self.assertRaises(SecretsFileError) as raised:
            Secrets.parse(text)
        message = str(raised.exception)
        for expected in (
            "line 1: expected KEY=VALUE",
            "line 2: expected KEY=VALUE",
            "line 3: GIT_1_URL starts or ends with a space",
            "line 4: GIT_2_URL is empty",
            "GIT_3_URL: missing, or not an https:// URL",
            "GIT_4: exactly one of GIT_4_PASSWORD and GIT_4_BEARER is expected",
            "GIT_5_URL: missing",
            "GH_X_OWNER: missing",
            "GH_Y_TOKEN: missing",
        ):
            self.assertIn(expected, message)
        for value in ("padded-secret", "p3", "p4", "b4", "p5"):
            self.assertNotIn(value, message)

    def test_empty_file(self) -> None:
        with self.assertRaisesRegex(SecretsFileError, "wrong passphrase"):
            Secrets.parse("# only a comment\n\n")

    def test_secret_values_stay_out_of_repr(self) -> None:
        secrets = Secrets.parse(SECRETS)
        for value in SECRET_VALUES:
            self.assertNotIn(value, repr(secrets))


class GitEntryTest(unittest.TestCase):
    def test_covers(self) -> None:
        entry = GitEntry("GIT_1", "https://github.com/SpiGes", "u", "p", None)
        self.assertTrue(entry.covers("https://github.com/SpiGes"))
        self.assertTrue(entry.covers("https://github.com/spiges/x.git"))
        self.assertFalse(entry.covers("https://github.com/SpiGesEvil/x.git"))
        self.assertFalse(entry.covers("https://github.com"))


class GitCredentialTest(BrokerTestCase):
    def test_locked(self) -> None:
        with self.assertRaises(BrokerError) as raised:
            self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.assertEqual(raised.exception.status, HTTPStatus.LOCKED)
        self.assertEqual(self.notifications, ["Le courtier est verrouillé"])

    def test_closed_group_notifies_once_per_interval(self) -> None:
        self.unlock()
        for _ in range(2):
            with self.assertRaises(BrokerError) as raised:
                self.broker.git_credential(git_request("devops-server.admin.ch"))
            self.assertEqual(raised.exception.status, HTTPStatus.FORBIDDEN)
            self.assertIn("claude_secret allow git", str(raised.exception))
        self.assertEqual(self.notifications, ["Autorisation requise pour git"])
        self.clock.now += access.NOTIFY_INTERVAL_SECONDS
        with self.assertRaises(BrokerError):
            self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.assertEqual(len(self.notifications), 2)

    def test_basic(self) -> None:
        self.unlock("git")
        answer, target = self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.assertEqual(answer, "username=token\npassword=devops-pat\n")
        self.assertEqual(target, "https://devops-server.admin.ch (GIT_1)")

    def test_case_insensitive_organization(self) -> None:
        self.unlock("git")
        answer, _ = self.broker.git_credential(git_request("github.com", "spiges/claude-agent.git"))
        self.assertEqual(answer, "username=x-access-token\npassword=github-spiges\n")

    def test_longest_prefix_wins(self) -> None:
        self.unlock("git")
        answer, _ = self.broker.git_credential(git_request("bitbucket.bit.admin.ch", "scm/p/r.git"))
        self.assertEqual(answer, "capability[]=authtype\nauthtype=Bearer\ncredential=bitbucket-repo\n")
        answer, _ = self.broker.git_credential(git_request("bitbucket.bit.admin.ch", "scm/p/other.git"))
        self.assertEqual(answer, "username=token\npassword=bitbucket-wide\n")

    def test_bearer_needs_authtype(self) -> None:
        self.unlock("git")
        with self.assertRaisesRegex(BrokerError, "Git 2.46"):
            self.broker.git_credential(git_request("bitbucket.bit.admin.ch", "scm/p/r.git", authtype=False))

    def test_refusals(self) -> None:
        self.unlock("git")
        for request, status in (
            (git_request("devops-server.admin.ch.evil"), HTTPStatus.NOT_FOUND),
            (git_request("github.com", "Other/x.git"), HTTPStatus.NOT_FOUND),
            ("protocol=http\nhost=devops-server.admin.ch\n", HTTPStatus.BAD_REQUEST),
            ("protocol=https\n", HTTPStatus.BAD_REQUEST),
        ):
            with self.subTest(request=request), self.assertRaises(BrokerError) as raised:
                self.broker.git_credential(request)
            self.assertEqual(raised.exception.status, status)


class GhTokenTest(BrokerTestCase):
    def test_owner(self) -> None:
        self.unlock("gh")
        self.assertEqual(self.broker.gh_token("spiges"), ("gh-spiges", "spiges (GH_SPIGES_TOKEN)"))
        self.assertEqual(self.broker.gh_token("Rundschreiben-Kodierer")[0], "gh-other")

    def test_unknown_owner(self) -> None:
        self.unlock("gh")
        with self.assertRaises(BrokerError) as raised:
            self.broker.gh_token("Nobody")
        self.assertEqual(raised.exception.status, HTTPStatus.NOT_FOUND)

    def test_without_owner(self) -> None:
        self.unlock("gh")
        with self.assertRaisesRegex(BrokerError, "owners: Rundschreiben-Kodierer, SpiGes"):
            self.broker.gh_token("")

    def test_single_token_and_default_token(self) -> None:
        self.broker.load("GH_TOKEN=default\nGIT_1_URL=https://x\nGIT_1_PASSWORD=p\n")
        self.broker.allow("gh")
        self.assertEqual(self.broker.gh_token(""), ("default", "(no owner) (GH_TOKEN)"))
        self.assertEqual(self.broker.gh_token("AnyOwner")[0], "default")

    def test_no_token(self) -> None:
        self.broker.load("GIT_1_URL=https://x\nGIT_1_PASSWORD=p\n")
        self.broker.allow("gh")
        with self.assertRaisesRegex(BrokerError, "no GitHub API token"):
            self.broker.gh_token("SpiGes")

    def test_invalid_owner(self) -> None:
        self.unlock("gh")
        with self.assertRaises(BrokerError) as raised:
            self.broker.gh_token("SpiGes\n2026-01-01T00:00:00Z fake")
        self.assertEqual(raised.exception.status, HTTPStatus.BAD_REQUEST)


class GroupsTest(BrokerTestCase):
    def test_idle_limit_is_moved_by_each_use(self) -> None:
        self.unlock("git")
        self.clock.now += 29 * 60
        self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.clock.now += 29 * 60
        self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.clock.now += 30 * 60
        with self.assertRaises(BrokerError) as raised:
            self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.assertEqual(raised.exception.status, HTTPStatus.FORBIDDEN)

    def test_maximum_time_even_when_used(self) -> None:
        self.unlock("git")
        for _ in range(15):
            self.clock.now += 15 * 60
            self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.clock.now += 15 * 60
        with self.assertRaises(BrokerError) as raised:
            self.broker.git_credential(git_request("devops-server.admin.ch"))
        self.assertEqual(raised.exception.status, HTTPStatus.FORBIDDEN)

    def test_allow_unknown_group_or_locked(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown group 'gti'"):
            self.broker.allow("gti")
        with self.assertRaisesRegex(ValueError, "locked"):
            self.broker.allow("git")

    def test_revoke(self) -> None:
        self.unlock("git", "gh")
        self.assertEqual(self.broker.revoke("gh"), "Closed: gh")
        self.assertEqual(self.broker.revoke("all"), "Closed: git")
        self.assertEqual(self.broker.revoke("all"), "Closed: no open group")
        with self.assertRaisesRegex(ValueError, "unknown group 'gti'"):
            self.broker.revoke("gti")

    def test_status(self) -> None:
        self.assertEqual(self.broker.status(), "Secret broker: locked\n  git        closed\n  gh         closed")
        self.unlock("git")
        self.clock.now += 10 * 60
        status = self.broker.status()
        self.assertIn("unlocked (4 Git tokens", status)
        self.assertIn("git        open, closes in 20 min (idle limit 20 min, maximum 3 h 50 min)", status)

    def test_load_and_lock_close_every_group(self) -> None:
        self.unlock("git")
        self.broker.load(SECRETS)
        self.assertIn("git        closed", self.broker.status())
        self.broker.allow("git")
        self.assertIn("locked", self.broker.lock())
        self.assertFalse(self.broker.unlocked)


class AdminCommandTest(BrokerTestCase):
    def test_commands(self) -> None:
        self.assertEqual(run_admin_command(self.broker, {"command": "unlocked"}), (False, "locked"))
        ok, message = run_admin_command(self.broker, {"command": "load", "data": SECRETS})
        self.assertTrue(ok, message)
        self.assertEqual(run_admin_command(self.broker, {"command": "unlocked"}), (True, "unlocked"))
        self.assertTrue(run_admin_command(self.broker, {"command": "allow", "args": ["git"]})[0])
        self.assertTrue(run_admin_command(self.broker, {"command": "status"})[0])
        self.assertTrue(run_admin_command(self.broker, {"command": "revoke", "args": ["all"]})[0])
        self.assertEqual(
            run_admin_command(self.broker, {"command": "export", "args": ["LATER_PHASE_SECRET"]}), (True, "later")
        )
        self.assertTrue(run_admin_command(self.broker, {"command": "lock"})[0])

    def test_export_only_type_2_secrets(self) -> None:
        self.assertEqual(run_admin_command(self.broker, {"command": "export", "args": ["X"]})[0], False)
        self.unlock()
        for key in ("GIT_1_PASSWORD", "GH_SPIGES_TOKEN", "MISSING"):
            with self.subTest(key=key):
                ok, message = run_admin_command(self.broker, {"command": "export", "args": [key]})
                self.assertFalse(ok)
                self.assertIn("no secret", message)

    def test_refusals(self) -> None:
        self.assertEqual(run_admin_command(self.broker, {"command": "load", "data": "x"})[0], False)
        self.assertEqual(run_admin_command(self.broker, {"command": "allow"}), (False, "unknown command: allow"))
        self.assertEqual(run_admin_command(self.broker, {"command": "rm", "args": "x"}), (False, "unknown command: rm"))
        self.assertEqual(run_admin_command(self.broker, {"command": 1, "args": [2]}), (False, "unknown command: 1 2"))


@contextmanager
def running_service(broker: Broker) -> Generator[int]:
    server = BrokerHTTPServer(("127.0.0.1", 0), broker)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def http_call(
    port: int, method: str, path: str, body: str | None = None, headers: dict[str, str] | None = None
) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


class ServiceTest(BrokerTestCase):
    def test_requests(self) -> None:
        self.unlock("git", "gh")
        with running_service(self.broker) as port, self.assertLogs("claude-broker", logging.INFO) as logs:
            status, body = http_call(port, "POST", "/v1/git", git_request("devops-server.admin.ch"))
            self.assertEqual((status, body), (200, "username=token\npassword=devops-pat\n"))
            self.assertEqual(http_call(port, "GET", "/v1/token/gh?owner=SpiGes"), (200, "gh-spiges"))
            self.assertEqual(http_call(port, "GET", "/v1/token/gh?owner=SpiGes%0Afake")[0], 400)
            self.assertEqual(http_call(port, "GET", "/v1/token/gh?owner=Nobody")[0], 404)
            self.assertEqual(http_call(port, "GET", "/other")[0], 404)
            self.assertEqual(http_call(port, "POST", "/other", "x")[0], 404)
            self.assertEqual(http_call(port, "POST", "/v1/git", "x", {"Content-Length": "abc"})[0], 400)
            # Announced, not sent: the broker answers on the Content-Length, before the body, so that a client still
            # sending a large body could get a broken pipe, depending on timing.
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.putrequest("POST", "/v1/git")
            connection.putheader("Content-Length", str(claude_broker.MAX_REQUEST_BYTES + 1))
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 413)
            connection.close()
        log = "\n".join(logs.output)
        self.assertIn("git https://devops-server.admin.ch (GIT_1): allowed", log)
        self.assertIn("gh: refused (invalid owner)", log)
        for value in SECRET_VALUES:
            self.assertNotIn(value, log)
        self.assertNotIn("fake", log)


class AdminServiceTest(BrokerTestCase):
    def setUp(self) -> None:
        super().setUp()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(admin_socket=Path(directory.name) / "admin.sock")

    def ctl(self, *args: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = access.run_ctl(list(args), self.settings.admin_socket, "usage", stdin_command="load")
        return code, out.getvalue(), err.getvalue()

    def test_not_ready_and_usage(self) -> None:
        self.assertEqual(self.ctl("status")[0], access.EXIT_NOT_READY)
        self.assertEqual(self.ctl()[0], 2)

    def test_commands_through_the_socket(self) -> None:
        server = access.AdminServer(self.settings.admin_socket, lambda request: run_admin_command(self.broker, request))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual(self.settings.admin_socket.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.ctl("unlocked"), (1, "", ""))
        self.assertEqual(self.ctl("load", stdin=SECRETS)[0], 0)
        self.assertEqual(self.ctl("unlocked")[0], 0)
        code, out, _ = self.ctl("allow", "git")
        self.assertEqual(code, 0)
        self.assertIn("Group 'git' open", out)
        code, _, err = self.ctl("allow", "gti")
        self.assertEqual(code, 1)
        self.assertIn("unknown group", err)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(self.settings.admin_socket))
            client.sendall(b"not json\n[1]\n")
            answers = client.makefile("r", encoding="utf-8").readline()
        self.assertFalse(json.loads(answers)["ok"])


class MainTest(unittest.TestCase):
    def test_usage(self) -> None:
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(claude_broker.main([]), 2)

    def test_ctl(self) -> None:
        with mock.patch.object(claude_broker, "run_ctl", return_value=7) as ctl:
            self.assertEqual(claude_broker.main(["ctl", "status"]), 7)
        self.assertEqual(ctl.call_args.args[0], ["status"])

    def test_serve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environ = {
                "BROKER_PORT": "0",
                "BROKER_LOG": str(Path(directory) / "broker.log"),
                "BROKER_ADMIN_SOCKET": str(Path(directory) / "run" / "admin.sock"),
            }
            with (
                mock.patch.dict("os.environ", environ),
                mock.patch.object(BrokerHTTPServer, "serve_forever") as serve_forever,
            ):
                self.assertEqual(claude_broker.main(["serve"]), 0)
            handler = claude_broker.LOGGER.handlers[-1]
            claude_broker.LOGGER.removeHandler(handler)
            handler.close()
            serve_forever.assert_called_once()
            self.assertTrue((Path(directory) / "run" / "admin.sock").is_socket())
            self.assertIn(
                "started (idle limit 30 min, maximum 4 h 00 min); locked",
                (Path(directory) / "broker.log").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
