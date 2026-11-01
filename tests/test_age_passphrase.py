import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from age_passphrase import main, read_passphrase, run_age

# A program that asks a passphrase on its terminal again and again, as age would after each wrong answer.
ASKS_FOREVER = """#!/usr/bin/env python3
with open("/dev/tty", "r+b", buffering=0) as tty:
    while True:
        tty.write(b"Enter passphrase: ")
        tty.readline()
"""


class ReadPassphraseTest(unittest.TestCase):
    def test_first_line(self) -> None:
        self.assertEqual(read_passphrase(b"secret words\nnext"), b"secret words")
        self.assertEqual(read_passphrase(b"secret\r\n"), b"secret")
        self.assertEqual(read_passphrase(b"no line ending"), b"no line ending")

    def test_empty_is_refused(self) -> None:
        for stream in (b"", b"\n", b"\r\n"):
            with self.subTest(stream=stream), self.assertRaisesRegex(ValueError, "no passphrase"):
                read_passphrase(stream)


@unittest.skipUnless(shutil.which("age"), "age isn't installed")
class RunAgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.clear = self.folder / "secrets"
        self.clear.write_text("KEY=value\n", encoding="utf-8")
        self.encrypted = self.folder / "secrets.age"

    def run_age(self, *args: str, passphrase: bytes = b"right words") -> tuple[int, bytes]:
        output = self.folder / "stdout"
        with output.open("wb") as stream, mock.patch.object(sys, "stdout", stream):
            code = run_age(args, passphrase)
        return code, output.read_bytes()

    def test_encrypt_then_decrypt(self) -> None:
        code, _ = self.run_age("--passphrase", "--output", str(self.encrypted), str(self.clear))
        self.assertEqual(code, 0)
        self.assertNotIn(b"KEY=value", self.encrypted.read_bytes())
        code, decrypted = self.run_age("--decrypt", str(self.encrypted))
        self.assertEqual((code, decrypted), (0, b"KEY=value\n"))

    def test_wrong_passphrase(self) -> None:
        self.run_age("--passphrase", "--output", str(self.encrypted), str(self.clear))
        code, decrypted = self.run_age("--decrypt", str(self.encrypted), passphrase=b"wrong words")
        self.assertEqual((code, decrypted), (1, b""))

    def test_prompts_are_answered_twice_at_most(self) -> None:
        program = self.folder / "asks-forever"
        program.write_text(ASKS_FOREVER, encoding="utf-8")
        program.chmod(0o700)
        self.assertNotEqual(run_age([], b"words", program=str(program)), 0)


class MainTest(unittest.TestCase):
    def run_main(self, args: list[str], stdin: bytes) -> tuple[int, str]:
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin))),
            mock.patch.object(sys, "stderr", stderr),
        ):
            return main(args), stderr.getvalue()

    def test_usage(self) -> None:
        code, message = self.run_main([], b"")
        self.assertEqual(code, 2)
        self.assertIn("Usage", message)

    def test_no_passphrase(self) -> None:
        self.assertEqual(self.run_main(["--decrypt", "x"], b"\n"), (2, "age-passphrase: no passphrase on stdin\n"))

    def test_exit_code_of_age(self) -> None:
        with mock.patch("age_passphrase.run_age", return_value=-9) as run:
            self.assertEqual(self.run_main(["--decrypt", "x"], b"words\n")[0], 1)
        run.assert_called_once_with(["--decrypt", "x"], b"words")
        with mock.patch("age_passphrase.run_age", return_value=0):
            self.assertEqual(self.run_main(["--decrypt", "x"], b"words\n")[0], 0)


if __name__ == "__main__":
    unittest.main()
