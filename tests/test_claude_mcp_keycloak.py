import base64
import io
import json
import tempfile
import unittest
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from unittest import mock

import claude_mcp_keycloak
from claude_mcp_keycloak import KeycloakTokens, main, read_profiles, request_token, token_expiry, tools
from mcp_server import ToolError

CLIENTS = "# profiles\n\nbfs = BFS.Client\nsas=SAS.Client\nspiges=BFS.SIS.SpiGes\nsite-a=Site.A\n"
SECRETS = {
    "SPIGES_KEYCLOAK_USER_CLIENT_SECRET": "user-secret",
    "SPIGES_KEYCLOAK_SAS_CLIENT_SECRET": "sas-secret",
}


def jwt(expiry: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class FakeKeycloak:
    def __init__(self, answers: list[Mapping[str, object]]) -> None:
        self.answers = answers
        self.forms: list[Mapping[str, str]] = []

    def __call__(self, form: Mapping[str, str]) -> Mapping[str, object]:
        self.forms.append(form)
        return self.answers.pop(0)


class KeycloakTestCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clients = folder / "clients.txt"
        self.clients.write_text(CLIENTS, encoding="utf-8")
        self.now = 1_000_000.0

    def tokens(self, keycloak: FakeKeycloak, secrets: Mapping[str, str] = SECRETS) -> KeycloakTokens:
        return KeycloakTokens(secrets, self.clients, keycloak, lambda: self.now)


class ProfilesTest(KeycloakTestCase):
    def test_read_profiles(self) -> None:
        self.assertEqual(
            read_profiles(self.clients),
            {"bfs": "BFS.Client", "sas": "SAS.Client", "spiges": "BFS.SIS.SpiGes", "site-a": "Site.A"},
        )
        self.clients.write_text("bfs=\n", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "invalid line"):
            read_profiles(self.clients)

    def test_profiles_tool(self) -> None:
        self.assertEqual(
            self.tokens(FakeKeycloak([])).profiles({}),
            "bfs=BFS.Client\nsas=SAS.Client\nsite-a=Site.A\nspiges=BFS.SIS.SpiGes",
        )


class TokenTest(KeycloakTestCase):
    def test_token_with_the_secret_of_the_profile_or_the_shared_one(self) -> None:
        keycloak = FakeKeycloak([{"access_token": jwt(2_000_000)}, {"access_token": jwt(2_000_000)}])
        tokens = self.tokens(keycloak)
        with self.assertLogs("mcp-server", "INFO") as logs:
            self.assertEqual(tokens.token({"profile": "sas"}), jwt(2_000_000))
            tokens.token({"profile": "bfs"})
        self.assertEqual(
            keycloak.forms,
            [
                {
                    "grant_type": "client_credentials",
                    "scope": "openid profile roles email",
                    "client_id": "SAS.Client",
                    "client_secret": "sas-secret",
                },
                {
                    "grant_type": "client_credentials",
                    "scope": "openid profile roles email",
                    "client_id": "BFS.Client",
                    "client_secret": "user-secret",
                },
            ],
        )
        self.assertNotIn("secret", "".join(logs.output))

    def test_token_is_cached_until_shortly_before_its_expiry(self) -> None:
        keycloak = FakeKeycloak([{"access_token": jwt(1_000_100)}, {"access_token": "second"}])
        tokens = self.tokens(keycloak)
        first = tokens.token({"profile": "bfs"})
        self.now += 39
        self.assertEqual(tokens.token({"profile": "bfs"}), first)
        self.now += 2
        self.assertEqual(tokens.token({"profile": "bfs"}), "second")
        self.assertEqual(len(keycloak.forms), 2)

    def test_refusals(self) -> None:
        tokens = self.tokens(FakeKeycloak([{"error": "unauthorized_client", "error_description": "bad secret"}, {}]))
        for arguments, message in (
            ({}, "argument profile"),
            ({"profile": "BFS"}, "argument profile"),
            ({"profile": "unknown"}, "unknown profile 'unknown' \\(profiles: bfs, sas, site-a, spiges\\)"),
            ({"profile": "bfs"}, "no access token returned for 'BFS.Client' \\(unauthorized_client: bad secret\\)"),
            ({"profile": "sas"}, "no access token returned for 'SAS.Client'$"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ToolError, message):
                tokens.token(arguments)
        without_secret = self.tokens(FakeKeycloak([]), {})
        with self.assertRaisesRegex(ToolError, "neither SPIGES_KEYCLOAK_SITE_A_CLIENT_SECRET nor"):
            without_secret.token({"profile": "site-a"})

    def test_token_expiry(self) -> None:
        self.assertEqual(token_expiry(jwt(123)), 123.0)
        for token in ("not a jwt", "a.!!!.c", "a." + base64.urlsafe_b64encode(b'"text"').decode() + ".c"):
            with self.subTest(token=token):
                self.assertEqual(token_expiry(token), 0.0)

    def test_tools(self) -> None:
        keycloak = FakeKeycloak([{"access_token": "t"}])
        names = [tool.name for tool in tools(self.tokens(keycloak))]
        self.assertEqual(names, ["keycloak_profiles", "keycloak_token"])
        self.assertEqual(tools(self.tokens(keycloak))[1].handler({"profile": "bfs"}), "t")


class RequestTokenTest(unittest.TestCase):
    def test_answers(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"access_token": "t"}'
        with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.assertEqual(request_token({"client_id": "c"}), {"access_token": "t"})
        request = urlopen.call_args.args[0]
        self.assertEqual((request.full_url, request.data), (claude_mcp_keycloak.KEYCLOAK_ENDPOINT, b"client_id=c"))
        error = urllib.error.HTTPError("u", 401, "x", Message(), io.BytesIO(b'{"error": "e"}'))
        with mock.patch("urllib.request.urlopen", side_effect=error):
            self.assertEqual(request_token({}), {"error": "e"})
        for side_effect, message in ((OSError("down"), "can't be reached"), (None, "isn't JSON")):
            response.__enter__.return_value.read.return_value = b"<html>"
            with (
                self.subTest(message=message),
                mock.patch("urllib.request.urlopen", return_value=response, side_effect=side_effect),
                self.assertRaisesRegex(ToolError, message),
            ):
                request_token({})
        response.__enter__.return_value.read.return_value = b"[1]"
        with mock.patch("urllib.request.urlopen", return_value=response):
            self.assertEqual(request_token({}), {})


class MainTest(unittest.TestCase):
    def test_usage_and_settings(self) -> None:
        with mock.patch("sys.stderr", io.StringIO()) as stderr:
            self.assertEqual(main([]), 2)
            self.assertEqual(main(["serve"]), 2)
            self.assertEqual(main(["serve", "--env-file", "/nonexistent/env"]), 2)
        self.assertIn("claude-mcp-keycloak serve --env-file", stderr.getvalue())

    def test_serve(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / "env"
            env_file.write_text("SPIGES_KEYCLOAK_USER_CLIENT_SECRET=s\n", encoding="utf-8")
            with mock.patch("claude_mcp_keycloak.serve") as serve:
                self.assertEqual(main(["serve", "--env-file", str(env_file), "--port", "9"]), 0)
        server, port = serve.call_args.args
        self.assertEqual(port, 9)
        answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(len(answer["result"]["tools"]), 2)


if __name__ == "__main__":
    unittest.main()
