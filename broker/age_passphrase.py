#!/usr/bin/env python3
"""Runs age with a passphrase read once from stdin (see README, "Secret broker").

age reads a passphrase only from the terminal. This program reads it from the first line of its stdin, runs age on a
pseudo-terminal, and answers its passphrase prompts (two at most: the passphrase, and its confirmation when
encrypting), so that `claude_secret edit` asks the passphrase once for the decryption, the encryption, and the reload
of the broker. The passphrase goes through pipes only: no file, no environment variable, no command-line argument.

The output of age (e.g. the decrypted file with `--decrypt` and no `--output`) goes to the stdout of this program, its
other messages to stderr. Installed in the image as /usr/local/bin/age-passphrase. Usage:
    age-passphrase <age arguments>          e.g. age-passphrase --decrypt /vault/secrets.age
"""

import os
import pty
import re
import sys
from collections.abc import Sequence
from typing import Final

PROMPT_MARKER: Final = b"passphrase"
MAX_PROMPTS: Final = 2
READ_BYTES: Final = 1024
# Cursor moves and line erasures that age writes to the terminal once a prompt is answered.
TERMINAL_CONTROLS: Final = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]|\r")


def read_passphrase(stream: bytes) -> bytes:
    """Return the passphrase: the first line of the input, without its line ending; empty is refused."""
    passphrase = stream.split(b"\n", 1)[0].removesuffix(b"\r")
    if not passphrase:
        message = "no passphrase on stdin"
        raise ValueError(message)
    return passphrase


def run_age(args: Sequence[str], passphrase: bytes, program: str = "age") -> int:
    """Run age on a pseudo-terminal, answer its passphrase prompts, and return its exit code."""
    stdout = os.dup(sys.stdout.fileno())
    pid, terminal = pty.fork()
    if pid == 0:  # pragma: no cover  # the child process, replaced by age
        # The pseudo-terminal is the controlling terminal of age (its /dev/tty, where it asks the passphrase); its
        # output goes to the real stdout, not through the terminal.
        os.dup2(stdout, 1)
        os.execvp(program, [program, *args])  # noqa: S606  # nosec B606  # fixed program, arguments of the host
    os.close(stdout)
    prompts = 0
    pending = b""
    while True:
        try:
            data = os.read(terminal, READ_BYTES)
        except OSError:  # EIO: age ended and closed the terminal
            break
        if not data:
            break
        pending += data
        if PROMPT_MARKER in pending.lower() and pending.rstrip().endswith(b":"):
            prompts += 1
            if prompts > MAX_PROMPTS:
                # e.g. a wrong passphrase asked again: never answered in a loop.
                os.kill(pid, 9)
                break
            os.write(terminal, passphrase + b"\n")
            pending = b""
        elif b"\n" in pending:
            # Messages of age other than the prompts (errors, warnings), shown as they come.
            text, _, pending = pending.rpartition(b"\n")
            _show(text)
    _show(pending)
    os.close(terminal)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def _show(text: bytes) -> None:
    lines = [line for line in TERMINAL_CONTROLS.sub(b"", text).split(b"\n") if line.strip()]
    if lines:
        sys.stderr.buffer.write(b"\n".join(lines) + b"\n")
        sys.stderr.buffer.flush()


def main(argv: Sequence[str]) -> int:
    """Read the passphrase from stdin and run age with the given arguments."""
    if not argv:
        sys.stderr.write(__doc__ or "")
        return 2
    try:
        passphrase = read_passphrase(sys.stdin.buffer.readline())
    except ValueError as error:
        sys.stderr.write(f"age-passphrase: {error}\n")
        return 2
    code = run_age(argv, passphrase)
    return 1 if code < 0 else code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
