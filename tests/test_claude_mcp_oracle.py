import contextlib
import datetime as dt
import decimal
import io
import json
import unittest
from collections.abc import Mapping, Sequence
from unittest import mock

import claude_mcp_oracle
from claude_mcp_oracle import (
    Account,
    Database,
    OracleTools,
    ProcessingAreas,
    RejectedError,
    RolledBackError,
    csv_line,
    database_errors,
    format_value,
    main,
    parse_connection_string,
    tools,
    validate_query,
    validate_write,
)
from mcp_server import ToolError

SECRETS = {
    "SPIGES_ORACLE_REF_USER": "ref_user",
    "SPIGES_ORACLE_REF_PASSWORD": "ref-password",
    "SPIGES_ORACLE_REF_DATASOURCE": "(DESCRIPTION=(ADDRESS=(HOST=db))(CONNECT_DATA=(SERVICE_NAME=EDWHPDSRV)))",
    "SPIGES_ORACLE_TST_USER": "tst_user",
    "SPIGES_ORACLE_TST_PASSWORD": "tst-password",
    "SPIGES_ORACLE_TST_DATASOURCE": "tst-descriptor",
    "SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET": "client-secret",
}


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self.connection = connection
        self.description: Sequence[Sequence[object]] | None = None
        self.rowcount = 0
        self._rows: list[Sequence[object]] = []

    def execute(self, statement: str) -> None:
        self.connection.statements.append(statement)
        if statement.startswith("SELECT SYS_CONTEXT"):
            self._rows = [(self.connection.service,)]
        elif statement in self.connection.results:
            columns, rows = self.connection.results[statement]
            self.description = [(column,) for column in columns]
            self._rows = list(rows)
        elif self.connection.error is not None and statement == self.connection.failing:
            raise self.connection.error
        else:
            self.rowcount = self.connection.rowcount

    def fetchone(self) -> Sequence[object] | None:
        return self._rows[0] if self._rows else None

    def fetchmany(self, size: int) -> list[Sequence[object]]:
        return self._rows[:size]

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, service: str = "EDWHPDSRV.bfs.admin.ch") -> None:
        self.service = service
        self.statements: list[str] = []
        self.results: dict[str, tuple[list[str], list[Sequence[object]]]] = {}
        self.rowcount = 0
        self.error: Exception | None = None
        self.failing = ""
        self.events: list[str] = []
        self.client_identifier = ""
        self.module = ""
        self.action = ""
        self.call_timeout = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


class OracleError(Exception):
    """Stands for an error of oracledb (its module starts with oracledb)."""


OracleError.__module__ = "oracledb.exceptions"


class FakeConnect:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.accounts: list[tuple[str, str, str]] = []

    def __call__(self, user: str, password: str, data_source: str) -> FakeConnection:
        self.accounts.append((user, password, data_source))
        return self.connection


class GuardTest(unittest.TestCase):
    def test_accepted_queries(self) -> None:
        for sql in (
            "SELECT 1 FROM DUAL;",
            "  with x as (select 1 a from dual) select a from x",
            "SELECT ';' AS s, \"A;B\" FROM t -- ; DELETE\n",
            "SELECT /* ; INSERT */ q'[it's; ]' FROM dual",
            "SELECT nq'{x;}' FROM dual",
            "SELECT updated_into FROM t",
            "SELECT a$b FROM t WHERE c = 'x' || q'<y;>'",
            "SELECT 'it''s; here' FROM dual",
        ):
            with self.subTest(sql=sql):
                self.assertEqual(validate_query(sql), sql.strip().rstrip(";").rstrip())

    def test_rejected_queries(self) -> None:
        for sql, reason in (
            ("  ;", "empty"),
            ("SELECT 1 FROM dual; DELETE FROM t", "only one statement"),
            ("SELECT q'[x]' FROM dual; DELETE FROM t", "only one statement"),
            ("DELETE FROM t WHERE a = 1", "only SELECT or WITH"),
            ("SELECT a INTO b FROM t", "'INTO'"),
            ("SELECT a FROM t FOR UPDATE", "'FOR UPDATE'"),
            ("WITH FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END; SELECT f FROM dual", "only one statement"),
            ("WITH FUNCTION f RETURN NUMBER IS BEGIN RETURN 1 END SELECT f FROM dual", "'FUNCTION'"),
            ("SELECT E'\\' ; DELETE FROM t", "only one statement"),
            ("SELECT $tag$ ; $tag$ FROM t", "only one statement"),
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(RejectedError, f"^rejected: .*{reason}"):
                validate_query(sql)
        # The alternative quote ends at "]'": the semicolon is inside the literal, so the query is a single one.
        self.assertTrue(validate_query("SELECT q'[x' FROM dual; DELETE FROM t]' FROM dual"))
        # An unterminated literal or alternative quote runs to the end: Oracle refuses the statement itself.
        self.assertTrue(validate_query("SELECT 'unterminated; DELETE FROM t"))
        self.assertTrue(validate_query("SELECT q'"))

    def test_writes(self) -> None:
        for sql in (
            "INSERT INTO t (a) SELECT a FROM t WHERE id = 1",
            "update t set a = 'x;y' where id = 2;",
            "DELETE FROM t WHERE id = 3",
        ):
            with self.subTest(sql=sql):
                self.assertTrue(validate_write(sql))
        for sql, reason in (
            ("SELECT 1 FROM dual", "only INSERT, UPDATE or DELETE"),
            ("UPDATE t SET a = 1", "an UPDATE statement must have a WHERE"),
            ("DELETE FROM t -- WHERE id = 1", "an DELETE statement must have a WHERE"),
            ("BEGIN DELETE FROM t WHERE id = 1; END", "only one statement"),
            ("INSERT INTO t SELECT * FROM t WHERE x = (SELECT 1 FROM dual) AND BEGIN = 1", "'BEGIN'"),
            ("", "empty"),
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(RejectedError, reason):
                validate_write(sql)


class CsvTest(unittest.TestCase):
    def test_format_value(self) -> None:
        for value, text in (
            (None, ""),
            # Oracle DATE and TIMESTAMP values are naive.
            (dt.datetime(2026, 11, 2, 14, 5, 7), "2026-11-02T14:05:07"),  # noqa: DTZ001
            (dt.datetime(2026, 11, 2, 14, 5, 7, 120000), "2026-11-02T14:05:07.12"),  # noqa: DTZ001
            (dt.datetime(2026, 11, 2, tzinfo=dt.UTC), "2026-11-02T00:00:00+00:00"),
            (dt.date(2026, 11, 2), "2026-11-02T00:00:00"),
            (decimal.Decimal("1.50"), "1.50"),
            (decimal.Decimal("1E+3"), "1000"),
            (b"\x00\x01", "<2 bytes>"),
            (42, "42"),
            ("text", "text"),
        ):
            with self.subTest(value=value):
                self.assertEqual(format_value(value), text)

    def test_csv_line(self) -> None:
        self.assertEqual(csv_line(["a", "b,c", 'd"e', "f\ng", ""]), 'a,"b,c","d""e","f\ng",\n')


class ConnectionStringTest(unittest.TestCase):
    def test_parse(self) -> None:
        self.assertEqual(
            parse_connection_string("User Id=PA_1;Password=p=w;Data Source=(DESCRIPTION=(A=B));Pooling=false"),
            Account("PA_1", "p=w", "(DESCRIPTION=(A=B))"),
        )
        self.assertEqual(
            parse_connection_string('uid=u; PWD="a;b"; DataSource=host/svc'), Account("u", "a;b", "host/svc")
        )

    def test_incomplete(self) -> None:
        with self.assertRaises(ToolError) as caught:
            parse_connection_string("User Id=u;Password=secret-password")
        self.assertNotIn("secret-password", str(caught.exception))
        self.assertNotIn("secret-password", repr(Account("u", "secret-password", "d")))


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = FakeConnection()
        self.connect = FakeConnect(self.connection)
        self.areas = mock.create_autospec(ProcessingAreas, instance=True)
        self.areas.token.return_value = "the-token"
        self.areas.recreate.return_value = ("PA_SCHEMA", "RRD")
        self.areas.connection.return_value = (Account("pa_user", "pa-password", "pa-ds"), "RRD")
        self.oracle = OracleTools(SECRETS, Database(self.connect), self.areas)


class QueryTest(DatabaseTestCase):
    def test_query(self) -> None:
        sql = "SELECT a, b FROM t"
        self.connection.results[sql] = (["A", "B"], [(1, "x"), (2, None), (3, "z")])
        result = json.loads(
            self.oracle.query({"environment": "ref", "sql": sql + ";", "schema": "SPIGES", "max_rows": 2})
        )
        self.assertEqual(result, {"csv": "A,B\n1,x\n2,\n", "rows": 2, "columns": 2, "truncated": True})
        self.assertEqual(self.connect.accounts, [("ref_user", "ref-password", SECRETS["SPIGES_ORACLE_REF_DATASOURCE"])])
        self.assertEqual(
            self.connection.statements,
            [
                "SELECT SYS_CONTEXT('USERENV', 'SERVICE_NAME') FROM DUAL",
                "ALTER SESSION SET CURRENT_SCHEMA = SPIGES",
                "SET TRANSACTION READ ONLY",
                sql,
            ],
        )
        self.assertEqual(self.connection.events, ["rollback", "close"])
        self.assertEqual((self.connection.client_identifier, self.connection.module), ("claude-agent", "claude-agent"))
        self.assertEqual(self.connection.call_timeout, 60_000)

    def test_query_not_truncated(self) -> None:
        self.connection.results["SELECT 1 FROM dual"] = (["1"], [(1,)])
        result = json.loads(
            self.oracle.query({"environment": "tst", "sql": "SELECT 1 FROM dual", "timeout_seconds": 5})
        )
        self.assertEqual((result["rows"], result["truncated"]), (1, False))
        self.assertEqual(self.connect.accounts[0][0], "tst_user")
        self.assertEqual(self.connection.call_timeout, 5_000)

    def test_other_service_runs_nothing(self) -> None:
        self.connection.service = "PDWHPDSRV"
        with self.assertRaisesRegex(ToolError, "'PDWHPDSRV' instead of EDWHPDSRV"):
            self.oracle.query({"environment": "ref", "sql": "SELECT 1 FROM dual"})
        self.assertEqual(len(self.connection.statements), 1)
        self.assertEqual(self.connection.events, ["close"])

    def test_rejected_and_invalid_arguments(self) -> None:
        for arguments, message in (
            ({"environment": "prd", "sql": "SELECT 1 FROM dual"}, "environment: one of ref, tst"),
            ({"environment": "ref"}, "sql is expected"),
            ({"environment": "ref", "sql": "DELETE FROM t WHERE a = 1"}, "rejected: only SELECT"),
            ({"environment": "ref", "sql": "SELECT 1 FROM dual", "schema": "A; DROP"}, "invalid schema name"),
            ({"environment": "ref", "sql": "SELECT 1 FROM dual", "max_rows": 10_001}, "max_rows must be"),
            ({"environment": "ref", "sql": "SELECT 1 FROM dual", "timeout_seconds": True}, "timeout_seconds must be"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ToolError, message):
                self.oracle.query(arguments)
        self.assertEqual(self.connect.accounts, [])

    def test_missing_account(self) -> None:
        oracle = OracleTools({}, Database(self.connect), self.areas)
        with self.assertRaisesRegex(ToolError, "SPIGES_ORACLE_REF_USER, _PASSWORD and _DATASOURCE are expected"):
            oracle.query({"environment": "ref", "sql": "SELECT 1 FROM dual"})

    def test_database_error(self) -> None:
        self.connection.error = OracleError("ORA-00942: table or view does not exist")
        self.connection.failing = "SELECT * FROM missing"
        with self.assertRaisesRegex(ToolError, "^OracleError: ORA-00942"):
            self.oracle.query({"environment": "ref", "sql": "SELECT * FROM missing"})
        self.assertEqual(self.connection.events, ["rollback", "close"])


class WriteTest(DatabaseTestCase):
    def test_committed(self) -> None:
        self.connection.rowcount = 1
        sql = "UPDATE t SET a = 1 WHERE id = 2"
        result = json.loads(self.oracle.write({"environment": "ref", "sql": sql, "expect_rows": 1}))
        self.assertEqual(result, {"affected": 1})
        self.assertNotIn("SET TRANSACTION READ ONLY", self.connection.statements)
        self.assertEqual(self.connection.events, ["commit", "rollback", "close"])

    def test_rolled_back(self) -> None:
        self.connection.rowcount = 3
        with self.assertRaisesRegex(RolledBackError, "^rolled back: the statement affected 3 row"):
            self.oracle.write({"environment": "ref", "sql": "DELETE FROM t WHERE a = 1", "expect_rows": 1})
        self.assertEqual(self.connection.events, ["rollback", "close"])

    def test_invalid(self) -> None:
        for arguments, message in (
            ({"environment": "ref", "sql": "DELETE FROM t", "expect_rows": 1}, "must have a WHERE"),
            ({"environment": "ref", "sql": "DELETE FROM t WHERE a = 1"}, "expect_rows is expected"),
            ({"environment": "ref", "sql": "DELETE FROM t WHERE a = 1", "expect_rows": 0}, "expect_rows is expected"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ToolError, message):
                self.oracle.write(arguments)

    def test_database_error(self) -> None:
        sql = "INSERT INTO t (a) VALUES (1)"
        self.connection.error = OracleError("ORA-00001: unique constraint violated")
        self.connection.failing = sql
        with self.assertRaisesRegex(ToolError, "ORA-00001"):
            self.oracle.write({"environment": "ref", "sql": sql, "expect_rows": 1})
        self.assertNotIn("commit", self.connection.events)


class ProcessingRunTest(DatabaseTestCase):
    SQL = "SELECT * FROM ITAR_K"

    def arguments(self, **extra: object) -> dict[str, object]:
        return {"environment": "ref", "run_id": "b59cca02-88bc-45b1", "sql": self.SQL, **extra}

    def test_processing_run(self) -> None:
        self.connection.results[self.SQL] = (["ID"], [(1,)])
        result = json.loads(self.oracle.processing_run(self.arguments()))
        self.assertEqual(result["csv"], "ID\n1\n")
        self.assertEqual(result["messages"], ["processing area PA_SCHEMA created", "processing area PA_SCHEMA dropped"])
        self.areas.recreate.assert_called_once_with("r", "b59cca02-88bc-45b1", "the-token")
        self.areas.drop.assert_called_once_with("r", "PA_SCHEMA", "RRD", "the-token")
        self.assertEqual(self.connect.accounts, [("pa_user", "pa-password", "pa-ds")])

    def test_failed_recreation(self) -> None:
        self.areas.recreate.side_effect = ToolError("POST /api/ProcessingRun/x/recreateProcessingArea: HTTP 500")
        with self.assertRaisesRegex(ToolError, "HTTP 500; WARNING the processing area of run .* may have been created"):
            self.oracle.processing_run(self.arguments())
        self.areas.drop.assert_not_called()

    def test_failed_query_drops_the_area(self) -> None:
        self.connection.error = OracleError("ORA-00942: table or view does not exist")
        self.connection.failing = self.SQL
        with self.assertRaisesRegex(ToolError, "ORA-00942.*; processing area PA_SCHEMA created; .* dropped"):
            self.oracle.processing_run(self.arguments())
        self.areas.drop.assert_called_once()

    def test_unexpected_error_drops_the_area(self) -> None:
        self.areas.connection.side_effect = KeyError("unexpected")
        with self.assertRaises(KeyError):
            self.oracle.processing_run(self.arguments())
        self.areas.drop.assert_called_once()

    def test_failed_drop(self) -> None:
        self.connection.results[self.SQL] = (["ID"], [])
        self.areas.drop.side_effect = ToolError("PUT /api/ProcessingRun/dropProcessingArea: HTTP 502")
        result = json.loads(self.oracle.processing_run(self.arguments()))
        self.assertEqual(
            result["messages"][-1],
            "WARNING failed to drop processing area PA_SCHEMA: PUT /api/ProcessingRun/dropProcessingArea: HTTP 502",
        )

    def test_invalid(self) -> None:
        for arguments, message in (
            (self.arguments(run_id="../x"), "run_id is expected"),
            (self.arguments(sql="DELETE FROM t WHERE a = 1"), "rejected"),
            (self.arguments(environment="local"), "environment"),
        ):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ToolError, message):
                self.oracle.processing_run(arguments)
        self.areas.recreate.assert_not_called()


class ProcessingAreasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.answers: list[object] = []
        self.forms: list[Mapping[str, str]] = []
        self.token_answer: Mapping[str, object] = {"access_token": "the-token"}

    def http(self, method: str, url: str, token: str) -> object:
        self.calls.append((method, url, token))
        return self.answers.pop(0) if self.answers else None

    def post(self, form: Mapping[str, str]) -> Mapping[str, object]:
        self.forms.append(form)
        return self.token_answer

    def areas(self, secret: str = "client-secret") -> ProcessingAreas:
        return ProcessingAreas(secret, self.http, self.post)

    def test_token(self) -> None:
        self.assertEqual(self.areas().token(), "the-token")
        self.assertEqual(self.forms[0]["client_id"], "BFS.SIS.SpiGes")
        self.assertEqual(self.forms[0]["client_secret"], "client-secret")
        with self.assertRaisesRegex(ToolError, "SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET isn't in the secrets"):
            self.areas("").token()
        self.token_answer = {"error": "unauthorized_client"}
        with self.assertRaisesRegex(ToolError, "no access token returned for BFS.SIS.SpiGes"):
            self.areas().token()

    def test_recreate_connection_drop(self) -> None:
        self.answers = [
            {"dbConnectionInformationDto": {"dbConnectionIdentifier": "PA_1", "storageType": 1}},
            {"connectionString": "User Id=PA_1;Password=pw;Data Source=ds", "storageType": "RRD"},
        ]
        areas = self.areas()
        self.assertEqual(areas.recreate("t", "run-1", "tok"), ("PA_1", "1"))
        self.assertEqual(areas.connection("t", "PA_1", "tok"), (Account("PA_1", "pw", "ds"), "RRD"))
        areas.drop("t", "PA_1", "RRD", "tok")
        self.assertEqual(
            self.calls,
            [
                (
                    "POST",
                    "https://mediator-be-t.sis.bfs.admin.ch/api/ProcessingRun/run-1/recreateProcessingArea",
                    "tok",
                ),
                ("GET", "https://dal-be-t.sis.bfs.admin.ch/api/DbConnections/PA_1", "tok"),
                (
                    "PUT",
                    (
                        "https://mediator-be-t.sis.bfs.admin.ch/api/ProcessingRun/dropProcessingArea"
                        "?dbConnectionIdentifier=PA_1&storageType=RRD"
                    ),
                    "tok",
                ),
            ],
        )

    def test_invalid_answers(self) -> None:
        areas = self.areas()
        for answer in (None, {}, {"dbConnectionInformationDto": {"dbConnectionIdentifier": "PA; DROP"}}):
            self.answers = [answer]
            with self.subTest(answer=answer), self.assertRaisesRegex(ToolError, "no valid dbConnectionIdentifier"):
                areas.recreate("r", "run", "tok")
        self.answers = [{"storageType": "RRD"}]
        with self.assertRaisesRegex(ToolError, "no connectionString"):
            areas.connection("r", "PA_1", "tok")


class DatabaseErrorsTest(unittest.TestCase):
    def test_other_errors_go_through(self) -> None:
        unreachable = OSError(-2, "Name or service not known")
        with (
            self.assertRaisesRegex(ToolError, "^the database can't be reached: OSError: .*Name or service"),
            database_errors(),
        ):
            raise unreachable
        bug, kept = ValueError("bug"), ToolError("kept")
        with self.assertRaises(ValueError), database_errors():
            raise bug
        with self.assertRaisesRegex(ToolError, "^kept$"), database_errors():
            raise kept


class MainTest(unittest.TestCase):
    def test_tools(self) -> None:
        oracle = OracleTools(SECRETS, Database(FakeConnect(FakeConnection())), mock.Mock())
        self.assertEqual(
            [tool.name for tool in tools(oracle)], ["oracle_query", "oracle_write", "oracle_processing_run"]
        )

    def test_usage(self) -> None:
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(main([]), 2)
            self.assertEqual(main(["serve", "--env-file", "/nonexistent/env"]), 2)
        self.assertIn("usage:", errors.getvalue())

    def test_serve(self) -> None:
        with (
            mock.patch.object(claude_mcp_oracle, "read_env_file", return_value=SECRETS),
            mock.patch.object(claude_mcp_oracle, "serve") as serve,
        ):
            self.assertEqual(main(["serve", "--env-file", "/tmp/mcp-env", "--port", "8001"]), 0)
        server, port = serve.call_args.args
        self.assertEqual(port, 8001)
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(len(listed["result"]["tools"]), 3)


if __name__ == "__main__":
    unittest.main()
