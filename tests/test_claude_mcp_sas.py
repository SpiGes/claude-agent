import contextlib
import io
import subprocess
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest import mock

import claude_mcp_sas
from claude_mcp_sas import (
    MAX_OUTPUT_CHARACTERS,
    Completed,
    SasRef,
    limited,
    main,
    remote_path,
    run_process,
    tools,
)
from mcp_server import ToolError

SECRETS = {"SPIGES_SAS_REF_SSH_USER": "tu_test", "SPIGES_SAS_REF_SSH_PASSWORD": "secret-password"}
ASKPASS = "/usr/local/bin/claude-mcp-sas"


class FakeRunner:
    def __init__(self, *results: Completed) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, args: Sequence[str], env: Mapping[str, str]) -> Completed:
        self.calls.append((list(args), dict(env)))
        return self.results.pop(0) if self.results else Completed(0, "", "")


class SasTestCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.known_hosts = folder / "known_hosts"
        self.known_hosts.write_text("pssas10i02.bfi.admin.ch ssh-ed25519 AAAA\n", encoding="utf-8")
        self.drop = folder / "mcp-sas"
        self.drop.mkdir()
        self.outside = folder / "outside.txt"
        self.outside.write_text("x", encoding="utf-8")

    def sas(self, runner: FakeRunner) -> SasRef:
        return SasRef(SECRETS, self.known_hosts, self.drop, ASKPASS, runner)


class RemotePathTest(unittest.TestCase):
    def test_accepted(self) -> None:
        for path in ("SIS2-472-test", "SIS2-472-test/prog.sas", "a/b_c.d", ".hidden"):
            with self.subTest(path=path):
                self.assertEqual(remote_path(path, "p"), path)

    def test_refused(self) -> None:
        for path in ("/etc/passwd", "../x", "a/../../x", "-oProxyCommand=x", "a b", "a;rm", "$HOME/x", "", None, 3):
            with self.subTest(path=path), self.assertRaises(ToolError):
                remote_path(path, "p")


class ExecTest(SasTestCase):
    def test_exec(self) -> None:
        runner = FakeRunner(Completed(0, "hello\n", ""))
        self.assertEqual(self.sas(runner).exec({"command": "echo hello"}), "hello\n[exit status of ssh: 0]")
        args, env = runner.calls[0]
        self.assertEqual(args[0], "ssh")
        self.assertIn(f"UserKnownHostsFile={self.known_hosts}", args)
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertEqual(args[-2:], ["tu_test@pssas10i02.bfi.admin.ch", "mkdir -p ~/agent && cd ~/agent && echo hello"])
        self.assertNotIn("secret-password", " ".join(args))
        self.assertEqual(env["SSH_ASKPASS"], ASKPASS)
        self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
        self.assertEqual(env["SAS_MCP_ASKPASS"], "1")
        self.assertEqual(env["SAS_MCP_PASSWORD"], "secret-password")

    def test_exec_with_stderr_and_failure(self) -> None:
        runner = FakeRunner(Completed(2, "out\n", "no such file\n"))
        self.assertEqual(
            self.sas(runner).exec({"command": "ls x"}), "out\n\n[stderr]\nno such file\n[exit status of ssh: 2]"
        )

    def test_other_server(self) -> None:
        runner = FakeRunner()
        self.sas(runner).exec({"command": "hostname", "server": "sas94"})
        args, _ = runner.calls[0]
        self.assertEqual(args[-2], "tu_test@sas94ref.bfs.admin.ch")
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", args)

    def test_invalid_arguments(self) -> None:
        sas = self.sas(FakeRunner())
        with self.assertRaisesRegex(ToolError, "command is expected"):
            sas.exec({"command": " "})
        with self.assertRaisesRegex(ToolError, "server: one of"):
            sas.exec({"command": "ls", "server": "pssas10i01"})

    def test_missing_known_hosts(self) -> None:
        self.known_hosts.unlink()
        with self.assertRaisesRegex(ToolError, "no known hosts file"):
            self.sas(FakeRunner()).exec({"command": "ls"})

    def test_missing_secrets(self) -> None:
        with self.assertRaisesRegex(ValueError, "SPIGES_SAS_REF_SSH_USER and"):
            SasRef({"SPIGES_SAS_REF_SSH_USER": "x"}, self.known_hosts, self.drop, ASKPASS)


class LoginRejectedTest(SasTestCase):
    def test_every_next_call_is_refused(self) -> None:
        for stderr in (
            "tu_test@pssas10i02.bfi.admin.ch: Permission denied (keyboard-interactive).\n",
            "Authentication failed.\n",
            "Received disconnect from 10.0.0.1 port 22:2: Too many authentication failures\n",
        ):
            with self.subTest(stderr=stderr):
                runner = FakeRunner(Completed(255, "", stderr))
                sas = self.sas(runner)
                with self.assertRaisesRegex(ToolError, "AUTHENTICATION FAILED on pssas10i02"):
                    sas.exec({"command": "ls"})
                with self.assertRaisesRegex(ToolError, "a login was rejected before"):
                    sas.exec({"command": "ls", "server": "sas94"})
                self.assertEqual(len(runner.calls), 1)

    def test_other_failure_doesnt_lock(self) -> None:
        for status, stderr in (
            (255, "ssh: connect to host: Connection timed out\n"),
            (1, "scp: agent/x/test.log: Permission denied\n"),
            (2, "ls: cannot open directory '/root': Permission denied\n"),
        ):
            with self.subTest(stderr=stderr):
                runner = FakeRunner(Completed(status, "", stderr))
                sas = self.sas(runner)
                self.assertIn(f"[exit status of ssh: {status}]", sas.exec({"command": "ls"}))
                sas.exec({"command": "ls"})
                self.assertEqual(len(runner.calls), 2)


class PutTest(SasTestCase):
    def test_put(self) -> None:
        (self.drop / "work").mkdir()
        program = self.drop / "work" / "test.sas"
        program.write_text("%put TEST;", encoding="utf-8")
        runner = FakeRunner()
        message = self.sas(runner).put({"files": ["work/test.sas", str(program)], "remote_dir": "SIS2-1-x"})
        self.assertEqual(message, "2 file(s) copied to ~/agent/SIS2-1-x/")
        mkdir, scp = runner.calls
        self.assertEqual(mkdir[0][-1], "mkdir -p ~/agent/SIS2-1-x")
        self.assertEqual(scp[0][0:2], ["scp", "-q"])
        self.assertEqual(
            scp[0][-3:],
            [str(program.resolve()), str(program.resolve()), "tu_test@pssas10i02.bfi.admin.ch:agent/SIS2-1-x/"],
        )

    def test_files_out_of_the_drop_folder_are_refused(self) -> None:
        link = self.drop / "link.txt"
        link.symlink_to(self.outside)
        sas = self.sas(FakeRunner())
        for file in (str(self.outside), "../outside.txt", "link.txt", ".", "missing.txt"):
            with self.subTest(file=file), self.assertRaises(ToolError):
                sas.put({"files": [file], "remote_dir": "x"})
        invalid: tuple[object, ...] = ([], "a.sas", None)
        for files in invalid:
            with self.subTest(files=files), self.assertRaisesRegex(ToolError, "files is expected"):
                sas.put({"files": files, "remote_dir": "x"})
        (self.drop / "a.sas").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ToolError, "remote_dir"):
            sas.put({"files": ["a.sas"], "remote_dir": "../x"})

    def test_failed_copy(self) -> None:
        (self.drop / "a.sas").write_text("", encoding="utf-8")
        runner = FakeRunner(Completed(0, "", ""), Completed(1, "", "scp: disk full\n"))
        with self.assertRaisesRegex(ToolError, "failed with exit code 1: scp: disk full"):
            self.sas(runner).put({"files": ["a.sas"], "remote_dir": "x"})


class GetTest(SasTestCase):
    def test_get(self) -> None:
        runner = FakeRunner()
        message = self.sas(runner).get({"remote_file": "x/test.log", "local_file": "SIS2-1/test.log"})
        target = (self.drop / "SIS2-1" / "test.log").resolve()
        self.assertEqual(message, f"~/agent/x/test.log copied to {target}")
        self.assertTrue(target.parent.is_dir())
        args, _ = runner.calls[0]
        self.assertEqual(args[-2:], ["tu_test@pssas10i02.bfi.admin.ch:agent/x/test.log", str(target)])

    def test_refused(self) -> None:
        sas = self.sas(FakeRunner())
        with self.assertRaises(ToolError):
            sas.get({"remote_file": "x.log", "local_file": "../x.log"})
        with self.assertRaises(ToolError):
            sas.get({"remote_file": "x.log", "local_file": ""})
        with self.assertRaises(ToolError):
            sas.get({"remote_file": "/etc/passwd", "local_file": "x"})


class RunTest(SasTestCase):
    def test_run(self) -> None:
        runner = FakeRunner(Completed(0, "exit code: 0\nTEST a: 1 syscc=0\n", ""))
        output = self.sas(runner).run_sas({"program": "SIS2-1/test.sas"})
        self.assertEqual(output, "exit code: 0\nTEST a: 1 syscc=0\n[exit status of ssh: 0]")
        command = runner.calls[0][0][-1]
        self.assertTrue(
            command.startswith(
                "/appl/sas/ref/metadata/9.4/Lev2/SIS/sas.sh $HOME/agent/SIS2-1/test.sas "
                "-log $HOME/agent/SIS2-1/test.log -NORSASUSER;"
            )
        )

    def test_not_a_program(self) -> None:
        with self.assertRaisesRegex(ToolError, "a .sas file is expected"):
            self.sas(FakeRunner()).run_sas({"program": "test.sh"})


class OutputTest(unittest.TestCase):
    def test_limited(self) -> None:
        self.assertEqual(limited("abc"), "abc")
        text = "x" * 10 + "y" * MAX_OUTPUT_CHARACTERS
        self.assertEqual(limited(text), f"[... first 10 characters cut]\n{'y' * MAX_OUTPUT_CHARACTERS}")


class RunProcessTest(unittest.TestCase):
    def test_run_process(self) -> None:
        result = run_process(["sh", "-c", 'echo "$X"; echo err >&2; exit 3'], {"X": "value", "PATH": "/usr/bin:/bin"})
        self.assertEqual(result, Completed(3, "value\n", "err\n"))

    def test_timeout(self) -> None:
        with (
            mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 1)),
            self.assertRaisesRegex(ToolError, "stopped after 14 minutes"),
        ):
            run_process(["ssh"], {})


class ToolsTest(SasTestCase):
    def test_tools(self) -> None:
        names = [tool.name for tool in tools(self.sas(FakeRunner()), self.drop)]
        self.assertEqual(names, ["sas_exec", "sas_put", "sas_get", "sas_run"])


class MainTest(SasTestCase):
    def test_askpass(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["Password:"], {"SAS_MCP_ASKPASS": "1", "SAS_MCP_PASSWORD": "pw"}), 0)
        self.assertEqual(output.getvalue(), "pw\n")
        self.assertEqual(main(["Password:"], {"SAS_MCP_ASKPASS": "1"}), 1)

    def test_usage_and_settings(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(main([], {}), 2)
            self.assertEqual(main(["serve"], {}), 2)
            env_file = self.drop / "env"
            env_file.write_text("SPIGES_SAS_REF_SSH_USER=x\n", encoding="utf-8")
            self.assertEqual(main(["serve", "--env-file", str(env_file)], {}), 2)
        self.assertIn("usage:", errors.getvalue())
        self.assertIn("SPIGES_SAS_REF_SSH_PASSWORD are expected", errors.getvalue())

    def test_serve(self) -> None:
        env_file = self.drop / "env"
        env_file.write_text("SPIGES_SAS_REF_SSH_USER=x\nSPIGES_SAS_REF_SSH_PASSWORD=y\n", encoding="utf-8")
        environ = {"SAS_KNOWN_HOSTS_FILE": str(self.known_hosts), "SAS_DROP_DIR": str(self.drop)}
        with mock.patch.object(claude_mcp_sas, "serve") as serve:
            self.assertEqual(main(["serve", "--env-file", str(env_file), "--port", "8001"], environ), 0)
        server, port = serve.call_args.args
        self.assertEqual(port, 8001)
        self.assertEqual(
            server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"}), {"jsonrpc": "2.0", "id": 1, "result": {}}
        )


if __name__ == "__main__":
    unittest.main()
