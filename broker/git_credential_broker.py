#!/usr/bin/env python3
"""Git credential helper of the agent container: asks the secret broker for the token of the URL.

Installed in the image as /usr/local/bin/git-credential-broker, and set as `credential.helper` by entrypoint.sh when
the container has a broker (see README, "Secret broker"). Without BROKER_URL, it answers nothing, and Git goes on with
its other settings. When the broker refuses (group closed, broker locked, no token for the URL) or can't be reached,
the reason is printed to stderr, and `quit=1` stops Git from trying anything else, such as a prompt.
"""

import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Final, TextIO
from urllib.parse import urlsplit

TIMEOUT_SECONDS: Final = 10


def ask_broker(broker_url: str, request: str) -> tuple[str | None, str]:
    """Send the `get` request of Git to the broker; return its answer, or None and the reason of the refusal."""
    endpoint = broker_url.rstrip("/") + "/v1/git"
    if urlsplit(endpoint).scheme not in {"http", "https"}:
        return None, f"BROKER_URL isn't an http(s) URL: {broker_url}"
    # No proxy: the broker is on the Docker network of the agents.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # The scheme is checked above.
    http_request = urllib.request.Request(endpoint, data=request.encode("utf-8"), method="POST")  # noqa: S310
    try:
        with opener.open(http_request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
            answer: bytes = response.read()
            return answer.decode("utf-8"), ""
    except urllib.error.HTTPError as error:
        return None, error.read().decode("utf-8", errors="replace").strip()
    except OSError as error:
        return None, f"secret broker unreachable at {broker_url} ({error}): run `claude_broker` on the host"


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] = os.environ,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    """Answer the `get` action of the Git credential protocol; `store` and `erase` do nothing."""
    args = list(sys.argv[1:] if argv is None else argv)
    broker_url = environ.get("BROKER_URL")
    if args[:1] != ["get"] or not broker_url:
        return 0
    answer, reason = ask_broker(broker_url, stdin.read())
    if answer is not None:
        stdout.write(answer)
    else:
        print(f"git-credential-broker: {reason}", file=stderr)
        print("quit=1", file=stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
