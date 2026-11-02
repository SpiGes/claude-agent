#!/usr/bin/env python3
"""Keycloak MCP server of the agent (see README, "MCP gateway").

Runs in its own container (claude-mcp-keycloak, started by `claude_mcp start keycloak`), behind the MCP gateway. It
holds the client secrets of the SpiGes Keycloak clients, and hands out only short-lived access tokens (client
credentials), so that the secrets never enter the agent container. The skills get a token through `mcp-call`, so
that it stays in a shell variable and never reaches the conversation.

- keycloak_profiles  lists the profiles of the clients file (`<profile>=<client id>` lines)
- keycloak_token     returns an access token for a profile, cached until shortly before its expiry

The client secret of a profile is SPIGES_KEYCLOAK_<PROFILE>_CLIENT_SECRET (profile in upper case, `-` replaced by
`_`) when the secrets file holds it, otherwise SPIGES_KEYCLOAK_USER_CLIENT_SECRET, shared by the user clients.

Installed in the image as /usr/local/bin/claude-mcp-keycloak. Usage:
    claude-mcp-keycloak serve --env-file <file> [--port <port>]
"""

import base64
import binascii
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final, cast

from mcp_server import McpServer, Tool, ToolError, parse_serve_arguments, read_env_file, serve

# Same token endpoint and scope as backend-run/scripts/spiges-token.sh of the backend repository.
KEYCLOAK_ENDPOINT: Final = (
    "https://keycloak-raas.apps.p-szb-ros-shrd-npr-01.cloud.admin.ch"
    "/realms/5513757d-499d-4592-b692-5a84dca819f2/protocol/openid-connect/token"
)
SCOPE: Final = "openid profile roles email"
EXPIRY_MARGIN_SECONDS: Final = 60
REQUEST_TIMEOUT_SECONDS: Final = 30
SHARED_SECRET: Final = "SPIGES_KEYCLOAK_USER_CLIENT_SECRET"  # noqa: S105  # nosec B105  # the name of the secret
PROFILE_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CLIENTS_FILE_VARIABLE: Final = "KEYCLOAK_CLIENTS_FILE"

LOGGER: Final = logging.getLogger("mcp-server")

type TokenRequest = Callable[[Mapping[str, str]], Mapping[str, object]]
type Clock = Callable[[], float]


def read_profiles(path: Path) -> dict[str, str]:
    """Read the `<profile>=<client id>` lines of the clients file; empty lines and lines starting with # are skipped."""
    profiles: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        profile, separator, client_id = text.partition("=")
        if not separator or not profile.strip() or not client_id.strip():
            message = f"invalid line in the clients file {path}: {text}"
            raise ToolError(message)
        profiles[profile.strip()] = client_id.strip()
    return profiles


def token_expiry(token: str) -> float:
    """Return the expiry (epoch seconds) read from the payload of a JWT, or 0 when it can't be read."""
    parts = token.split(".")
    if len(parts) != 3:  # noqa: PLR2004  # header, payload, signature
        return 0.0
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims: object = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, ValueError):
        return 0.0
    expiry = cast("dict[str, object]", claims).get("exp") if isinstance(claims, dict) else None
    return float(expiry) if isinstance(expiry, int | float) else 0.0


def request_token(form: Mapping[str, str]) -> Mapping[str, object]:
    """Post the client credentials to Keycloak, and return its JSON answer, error answers included."""
    request = urllib.request.Request(  # fixed https URL
        KEYCLOAK_ENDPOINT,
        data=urllib.parse.urlencode(form).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
            body: bytes = response.read()
    except urllib.error.HTTPError as error:
        body = error.read()
    except OSError as error:
        message = f"Keycloak can't be reached ({error})"
        raise ToolError(message) from error
    try:
        answer: object = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = "Keycloak returned an answer that isn't JSON"
        raise ToolError(message) from error
    return cast("dict[str, object]", answer) if isinstance(answer, dict) else {}


class KeycloakTokens:
    """Access tokens of the profiles of the clients file, cached by client id."""

    def __init__(
        self,
        secrets: Mapping[str, str],
        clients_file: Path,
        post: TokenRequest = request_token,
        clock: Clock = time.time,
    ) -> None:
        """Keep the client secrets in memory; the clients file is read again at each call."""
        self._secrets = dict(secrets)
        self._clients_file = clients_file
        self._post = post
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, float]] = {}

    def profiles(self, _: Mapping[str, object]) -> str:
        """List the profiles and their client ids, one per line."""
        profiles = read_profiles(self._clients_file)
        return "\n".join(f"{profile}={client_id}" for profile, client_id in sorted(profiles.items()))

    def token(self, arguments: Mapping[str, object]) -> str:
        """Return an access token for the profile of the arguments."""
        profile = arguments.get("profile")
        if not isinstance(profile, str) or not PROFILE_PATTERN.fullmatch(profile):
            message = "the argument profile is expected: lower case letters, digits and -"
            raise ToolError(message)
        profiles = read_profiles(self._clients_file)
        client_id = profiles.get(profile)
        if client_id is None:
            message = f"unknown profile '{profile}' (profiles: {', '.join(sorted(profiles))})"
            raise ToolError(message)
        with self._lock:
            cached = self._cache.get(client_id)
            if cached is not None and cached[1] - EXPIRY_MARGIN_SECONDS > self._clock():
                return cached[0]
        secret = self._secret(profile)
        answer = self._post(
            {
                "grant_type": "client_credentials",
                "scope": SCOPE,
                "client_id": client_id,
                "client_secret": secret,
            }
        )
        token = answer.get("access_token")
        if not isinstance(token, str) or not token:
            error = ": ".join(str(answer[key]) for key in ("error", "error_description") if key in answer)
            message = f"no access token returned for '{client_id}'" + (f" ({error})" if error else "")
            raise ToolError(message)
        with self._lock:
            self._cache[client_id] = (token, token_expiry(token))
        LOGGER.info("token issued for profile %s", profile)
        return token

    def _secret(self, profile: str) -> str:
        own = f"SPIGES_KEYCLOAK_{profile.upper().replace('-', '_')}_CLIENT_SECRET"
        secret = self._secrets.get(own) or self._secrets.get(SHARED_SECRET)
        if not secret:
            message = f"neither {own} nor {SHARED_SECRET} is in the secrets of the server"
            raise ToolError(message)
        return secret


def tools(tokens: KeycloakTokens) -> list[Tool]:
    """Return the tools of the server."""
    return [
        Tool(
            "keycloak_profiles",
            "Lists the Keycloak profiles of the SpiGes clients file, as <profile>=<client id> lines.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            tokens.profiles,
        ),
        Tool(
            "keycloak_token",
            "Returns a short-lived access token (client credentials) for a profile of the SpiGes clients file.",
            {
                "type": "object",
                "properties": {"profile": {"type": "string", "description": "Profile, e.g. bfs, sas, spiges"}},
                "required": ["profile"],
                "additionalProperties": False,
            },
            tokens.token,
        ),
    ]


def main(argv: Sequence[str]) -> int:
    """Run the `serve` command."""
    usage = (__doc__ or "").rsplit("Usage:", 1)[-1].strip()
    if not argv or argv[0] != "serve":
        print(f"usage: {usage}", file=sys.stderr)
        return 2
    try:
        env_file, port = parse_serve_arguments(argv[1:], f"usage: {usage}")
        secrets = read_env_file(env_file)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    clients_file = Path(os.environ.get(CLIENTS_FILE_VARIABLE, "/config/keycloak-clients.txt"))
    tokens = KeycloakTokens(secrets, clients_file)
    serve(McpServer("claude-mcp-keycloak", "1", tools(tokens)), port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
