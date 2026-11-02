#!/opt/claude-mcp-oracle/bin/python3
"""Oracle MCP server of the agent (see README, "MCP gateway").

Runs in its own container (claude-mcp-oracle, started by `claude_mcp start oracle`), behind the MCP gateway. It holds
the accounts of the non-production SpiGes Oracle databases (REF and TST: SPIGES_ORACLE_<ENV>_USER, _PASSWORD,
_DATASOURCE) and the secret of the technical SIS client (SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET, for the processing
areas), so that none of them enters the agent container. The spiges-sql skill calls it through mcp-call.

- oracle_query           one SELECT or WITH query, in a read-only transaction that is rolled back; CSV result
- oracle_write           one INSERT, UPDATE or DELETE statement, committed only when it touches the expected number of
                         rows, otherwise rolled back
- oracle_processing_run  recreates the processing area of a processing run (Mediator), reads its connection (DAL), runs
                         one read-only query on it, and always drops it; the connection of the area never leaves the
                         server

Controls, which the client can't skip:
- targets ref and tst only; right after the connection, the service must be EDWHPDSRV (the non-production database),
  so that settings pointing to PRD by mistake never run a statement
- a query is checked (one SELECT or WITH, no INTO, no FOR UPDATE, no inline PL/SQL) and runs in a read-only
  transaction that is rolled back; a write is checked (one INSERT, UPDATE or DELETE, WHERE for UPDATE and DELETE, no
  inline PL/SQL) and committed only with the expected number of rows. A function with side effects called from a
  query isn't stopped: only the rights of the account can
- the sessions are tagged claude-agent (client identifier and module, V$SESSION)

The results are JSON objects (CSV text, numbers of rows and columns, messages). A refused statement is a tool error
that starts with "rejected:", a write rolled back for its number of rows one that starts with "rolled back:".

Installed in the image as /usr/local/bin/claude-mcp-oracle, run with the Python of its virtual environment
(/opt/claude-mcp-oracle, with oracledb). Usage:
    claude-mcp-oracle serve --env-file <file> [--port <port>]
"""

import datetime as dt
import decimal
import json
import logging
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, Protocol, cast

from claude_mcp_keycloak import request_token
from mcp_server import McpServer, Tool, ToolError, parse_serve_arguments, read_env_file, serve

ENVIRONMENTS: Final[Mapping[str, tuple[str, str]]] = {"ref": ("REF", "r"), "tst": ("TST", "t")}
# The service shared by the non-production environments (EDWHPD). PRD is PDWHPDSRV, on another server.
EXPECTED_SERVICE: Final = "EDWHPDSRV"
SESSION_TAG: Final = "claude-agent"
DEFAULT_MAX_ROWS: Final = 200
MAX_MAX_ROWS: Final = 10_000
DEFAULT_TIMEOUT_SECONDS: Final = 60
MAX_TIMEOUT_SECONDS: Final = 600
HTTP_TIMEOUT_SECONDS: Final = 600
SPIGES_CLIENT_ID: Final = "BFS.SIS.SpiGes"
SPIGES_CLIENT_SECRET: Final = (
    "SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET"  # noqa: S105  # nosec B105  # the name of the secret
)
IDENTIFIER_PATTERN: Final = re.compile(r"[A-Za-z][A-Za-z0-9_$#]{0,127}")
RUN_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9-]{1,64}")
FIRST_WORD_PATTERN: Final = re.compile(r"[A-Za-z]+")
# INTO: SELECT INTO. FUNCTION, PROCEDURE, PRAGMA: inline PL/SQL in a WITH clause. FOR UPDATE / FOR SHARE: row locks.
FORBIDDEN_IN_QUERY: Final = re.compile(
    r"\b(INTO|FUNCTION|PROCEDURE|PRAGMA|FOR\s+(NO\s+KEY\s+)?UPDATE|FOR\s+(KEY\s+)?SHARE)\b", re.IGNORECASE
)
FORBIDDEN_IN_WRITE: Final = re.compile(r"\b(FUNCTION|PROCEDURE|PRAGMA|BEGIN|DECLARE)\b", re.IGNORECASE)
WHERE_PATTERN: Final = re.compile(r"\bWHERE\b", re.IGNORECASE)
CLOSING_DELIMITERS: Final = {"[": "]", "(": ")", "{": "}", "<": ">"}
MAX_ERROR_BODY: Final = 500

LOGGER: Final = logging.getLogger("mcp-server")


class RejectedError(ToolError):
    """A statement refused by the guard."""

    def __init__(self, reason: str) -> None:
        """Prefix the reason, so that the client tells a refusal from a failure."""
        super().__init__(f"rejected: {reason}")


class RolledBackError(ToolError):
    """A write rolled back because it didn't touch the expected number of rows."""

    def __init__(self, reason: str) -> None:
        """Prefix the reason, so that the client tells a rollback from a failure."""
        super().__init__(f"rolled back: {reason}")


# --- Guard -----------------------------------------------------------------------------------------------------------


def _identifier_part(text: str, index: int) -> bool:
    return index >= 0 and (text[index].isalnum() or text[index] in "_$#")


def _index_after(text: str, end: str, start: int) -> int:
    index = text.find(end, start)
    return len(text) if index < 0 else index + len(end)


def _index_after_quoted(text: str, quote: str, start: int) -> int:
    """Return the index after a quoted text, a doubled quote being an escaped quote."""
    i = start
    while i < len(text):
        if text[i] == quote:
            if i + 1 >= len(text) or text[i + 1] != quote:
                return i + 1
            i += 1
        i += 1
    return len(text)


def _starts_prefixed_quote(text: str, index: int, prefix: str) -> bool:
    end = index + len(prefix)
    return (
        not _identifier_part(text, index - 1)
        and end < len(text)
        and text[index:end].upper() == prefix
        and text[end] == "'"
    )


def strip_comments_and_literals(sql: str) -> str:
    """Replace the comments, literals and quoted identifiers of Oracle SQL with spaces.

    Besides the standard literals, it knows the alternative quoting (q'[...]', nq'[...]'): read as a standard literal,
    it would end too early or too late and hide code from the checks (e.g. a second statement). The syntaxes of
    Postgres (E'...', $tag$...$tag$) aren't literals for Oracle: read as literals, they would hide code too.
    """
    result: list[str] = []
    i = 0
    while i < len(sql):
        if sql.startswith("--", i):
            i = _index_after(sql, "\n", i + 2)
        elif sql.startswith("/*", i):
            i = _index_after(sql, "*/", i + 2)
        elif _starts_prefixed_quote(sql, i, "Q") or _starts_prefixed_quote(sql, i, "NQ"):
            delimiter_index = sql.index("'", i) + 1
            if delimiter_index >= len(sql):
                i = len(sql)
            else:
                delimiter = sql[delimiter_index]
                i = _index_after(sql, CLOSING_DELIMITERS.get(delimiter, delimiter) + "'", delimiter_index + 1)
        elif sql[i] in "'\"":
            i = _index_after_quoted(sql, sql[i], i + 1)
        else:
            result.append(sql[i])
            i += 1
            continue
        result.append(" ")
    return "".join(result)


def _statement(sql: str) -> tuple[str, str, str]:
    """Return the statement without its trailing semicolon, its code without literals, and its first word."""
    statement = sql.strip().rstrip(";").rstrip()
    if not statement:
        message = "the statement is empty"
        raise RejectedError(message)
    code = strip_comments_and_literals(statement)
    if ";" in code:
        message = "only one statement is accepted"
        raise RejectedError(message)
    first = FIRST_WORD_PATTERN.search(code)
    return statement, code, first.group(0).upper() if first else ""


def validate_query(sql: str) -> str:
    """Return a query that is one SELECT or WITH, without a forbidden clause; raise RejectedError otherwise."""
    query, code, first_word = _statement(sql)
    if first_word not in {"SELECT", "WITH"}:
        message = "only SELECT or WITH queries are accepted"
        raise RejectedError(message)
    if forbidden := FORBIDDEN_IN_QUERY.search(code):
        message = f"the clause '{forbidden.group(0).upper()}' isn't accepted"
        raise RejectedError(message)
    return query


def validate_write(sql: str) -> str:
    """Return one INSERT, UPDATE or DELETE statement, with a WHERE for an UPDATE or a DELETE, without inline PL/SQL."""
    statement, code, first_word = _statement(sql)
    if first_word not in {"INSERT", "UPDATE", "DELETE"}:
        message = "only INSERT, UPDATE or DELETE statements are accepted"
        raise RejectedError(message)
    if first_word in {"UPDATE", "DELETE"} and not WHERE_PATTERN.search(code):
        message = f"an {first_word} statement must have a WHERE clause"
        raise RejectedError(message)
    if forbidden := FORBIDDEN_IN_WRITE.search(code):
        message = f"the clause '{forbidden.group(0).upper()}' isn't accepted"
        raise RejectedError(message)
    return statement


# --- CSV -------------------------------------------------------------------------------------------------------------


def format_value(value: object) -> str:
    """Format a value of a row as the spiges-sql client does: invariant, ISO dates, binary data by its length."""
    if value is None:
        return ""
    if isinstance(value, dt.date):
        return _format_date(value)
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return str(value)


def _format_date(value: dt.date) -> str:
    """Return a date as yyyy-MM-ddTHH:mm:ss, with the fraction of second without its trailing zeros."""
    if not isinstance(value, dt.datetime):
        return f"{value:%Y-%m-%d}T00:00:00"
    if value.tzinfo is not None:
        return value.isoformat()
    fraction = f"{value.microsecond:06d}".rstrip("0")
    return f"{value:%Y-%m-%dT%H:%M:%S}" + (f".{fraction}" if fraction else "")


def csv_line(values: Sequence[str]) -> str:
    """Return one CSV line (RFC 4180, comma separator, LF line ending)."""

    def escape(value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"' if any(c in value for c in ',"\r\n') else value

    return ",".join(escape(value) for value in values) + "\n"


# --- Database --------------------------------------------------------------------------------------------------------


class Cursor(Protocol):
    """The part of an oracledb cursor the server uses."""

    @property
    def description(self) -> Sequence[Sequence[object]] | None: ...  # noqa: D102

    @property
    def rowcount(self) -> int: ...  # noqa: D102

    def execute(self, statement: str) -> object: ...  # noqa: D102

    def fetchone(self) -> Sequence[object] | None: ...  # noqa: D102

    def fetchmany(self, size: int) -> list[Sequence[object]]: ...  # noqa: D102

    def close(self) -> None: ...  # noqa: D102


class Connection(Protocol):
    """The part of an oracledb connection the server uses."""

    client_identifier: str
    module: str
    action: str
    call_timeout: int

    def cursor(self) -> Cursor: ...  # noqa: D102

    def commit(self) -> None: ...  # noqa: D102

    def rollback(self) -> None: ...  # noqa: D102

    def close(self) -> None: ...  # noqa: D102


type Connect = Callable[[str, str, str], Connection]


@dataclass(frozen=True, slots=True)
class Account:
    """An Oracle account: user, password, and data source (connect descriptor). Never printed."""

    user: str
    password: str = field(repr=False)
    data_source: str


def connect_oracle(user: str, password: str, data_source: str) -> Connection:  # pragma: no cover - needs a database
    """Open an Oracle connection in thin mode (no Oracle client)."""
    import oracledb  # noqa: PLC0415  # only in the virtual environment of the server

    # NUMBER as Decimal (exact, as the client prints it), LOBs as str and bytes.
    oracledb.defaults.fetch_decimals = True
    oracledb.defaults.fetch_lobs = False
    return cast("Connection", oracledb.connect(user=user, password=password, dsn=data_source))


def parse_connection_string(text: str) -> Account:
    """Read an ODP.NET connection string (`User Id=...;Password=...;Data Source=...`) as returned by the DAL."""
    values: dict[str, str] = {}
    for part in re.findall(r'(?:[^;"]|"[^"]*")+', text):
        key, separator, value = part.partition("=")
        if separator:
            values[re.sub(r"\s+", " ", key.strip().lower())] = value.strip().strip('"')
    user = values.get("user id") or values.get("uid") or values.get("user")
    password = values.get("password") or values.get("pwd")
    data_source = values.get("data source") or values.get("datasource")
    if not user or not password or not data_source:
        # Never the text itself: it holds the password.
        message = "the connection of the processing area returned by the DAL is incomplete"
        raise ToolError(message)
    return Account(user, password, data_source)


@dataclass(frozen=True, slots=True)
class Statement:
    """A checked statement and its settings."""

    sql: str
    schema: str | None
    timeout_seconds: int


class Database:
    """Opens the sessions: service checked, tagged, schema set, read-only transaction for a query."""

    def __init__(self, connect: Connect) -> None:
        """Open the connections with the given function."""
        self._connect = connect

    @contextmanager
    def session(self, account: Account, statement: Statement, *, read_only: bool) -> Generator[Connection]:
        """Yield an open connection whose transaction is rolled back at the end, unless the caller committed it."""
        connection = self._connect(account.user, account.password, account.data_source)
        try:
            connection.call_timeout = statement.timeout_seconds * 1000
            cursor = connection.cursor()
            try:
                cursor.execute("SELECT SYS_CONTEXT('USERENV', 'SERVICE_NAME') FROM DUAL")
                row = cursor.fetchone()
                service = str(row[0]) if row and row[0] is not None else ""
                if service.split(".")[0].upper() != EXPECTED_SERVICE:
                    message = (
                        f"connected to the service '{service}' instead of {EXPECTED_SERVICE}: the connection is closed "
                        "without running the statement"
                    )
                    raise ToolError(message)
                connection.client_identifier = SESSION_TAG
                connection.module = SESSION_TAG
                connection.action = "spiges-sql"
                if statement.schema is not None:
                    cursor.execute(f"ALTER SESSION SET CURRENT_SCHEMA = {statement.schema}")
                if read_only:
                    # The first statement of the transaction: otherwise Oracle refuses it, and nothing runs.
                    cursor.execute("SET TRANSACTION READ ONLY")
            finally:
                cursor.close()
            try:
                yield connection
            finally:
                connection.rollback()
        finally:
            connection.close()

    def query(self, account: Account, statement: Statement, max_rows: int) -> dict[str, object]:
        """Run a query in a read-only transaction, rolled back, and return its CSV."""
        with self.session(account, statement, read_only=True) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(statement.sql)
                description = cursor.description or []
                lines = [csv_line([str(column[0]) for column in description])]
                rows = cursor.fetchmany(max_rows + 1)
            finally:
                cursor.close()
        truncated = len(rows) > max_rows
        lines.extend(csv_line([format_value(value) for value in row]) for row in rows[:max_rows])
        return {
            "csv": "".join(lines),
            "rows": min(len(rows), max_rows),
            "columns": len(description),
            "truncated": truncated,
        }

    def write(self, account: Account, statement: Statement, expected_rows: int) -> dict[str, object]:
        """Run a write, committed only when it touches the expected number of rows."""
        with self.session(account, statement, read_only=False) as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(statement.sql)
                affected = cursor.rowcount
            finally:
                cursor.close()
            if affected != expected_rows:
                message = f"the statement affected {affected} row(s) instead of the expected {expected_rows}"
                raise RolledBackError(message)
            connection.commit()
        return {"affected": affected}


@contextmanager
def database_errors() -> Generator[None]:
    """Turn an error of oracledb (e.g. ORA-00942) into a tool error with its message, which holds no password."""
    try:
        yield
    except ToolError:
        raise
    except Exception as error:
        if not type(error).__module__.startswith("oracledb"):
            raise
        message = f"{type(error).__name__}: {error}"
        raise ToolError(message) from error


# --- Processing areas ------------------------------------------------------------------------------------------------

type HttpCall = Callable[[str, str, str], object]
type TokenRequest = Callable[[Mapping[str, str]], Mapping[str, object]]


def http_call(method: str, url: str, token: str) -> object:  # pragma: no cover - needs the SIS services
    """Send a request with a bearer token, and return its JSON answer (None for an empty one)."""
    request = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {token}"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310  # nosec B310
            body: bytes = response.read()
    except urllib.error.HTTPError as error:
        # The answer may repeat the request, never the token, which is only in a header.
        text = error.read().decode("utf-8", errors="replace").strip()[:MAX_ERROR_BODY]
        message = f"{method} {urllib.parse.urlsplit(url).path}: HTTP {error.code} {text}".strip()
        raise ToolError(message) from error
    except OSError as error:
        message = f"{method} {urllib.parse.urlsplit(url).path}: the service can't be reached ({error})"
        raise ToolError(message) from error
    return json.loads(body) if body.strip() else None


class ProcessingAreas:
    """Recreates, reads the connection of, and drops a processing area, through Mediator and the DAL."""

    def __init__(self, client_secret: str, http: HttpCall = http_call, post: TokenRequest = request_token) -> None:
        """Keep the secret of the technical SIS client in memory."""
        self._client_secret = client_secret
        self._http = http
        self._post = post

    def token(self) -> str:
        """Return an access token of the technical SIS client (client credentials); never printed."""
        if not self._client_secret:
            message = f"{SPIGES_CLIENT_SECRET} isn't in the secrets of the server"
            raise ToolError(message)
        answer = self._post(
            {
                "grant_type": "client_credentials",
                "scope": "openid profile roles email",
                "client_id": SPIGES_CLIENT_ID,
                "client_secret": self._client_secret,
            }
        )
        token = answer.get("access_token")
        if not isinstance(token, str) or not token:
            message = f"no access token returned for {SPIGES_CLIENT_ID}"
            raise ToolError(message)
        return token

    def recreate(self, letter: str, run_id: str, token: str) -> tuple[str, str]:
        """Recreate the area of a run; return its schema and storage type."""
        url = f"https://mediator-be-{letter}.sis.bfs.admin.ch/api/ProcessingRun/{run_id}/recreateProcessingArea"
        answer = self._http("POST", url, token)
        dto = cast("dict[str, object]", answer).get("dbConnectionInformationDto") if isinstance(answer, dict) else None
        schema = cast("dict[str, object]", dto).get("dbConnectionIdentifier") if isinstance(dto, dict) else None
        if not isinstance(schema, str) or not IDENTIFIER_PATTERN.fullmatch(schema):
            message = "the answer of Mediator has no valid dbConnectionIdentifier"
            raise ToolError(message)
        return schema, str(cast("dict[str, object]", dto).get("storageType", "RRD"))

    def connection(self, letter: str, schema: str, token: str) -> tuple[Account, str]:
        """Return the account of an area, and its storage type."""
        answer = self._http("GET", f"https://dal-be-{letter}.sis.bfs.admin.ch/api/DbConnections/{schema}", token)
        values = cast("dict[str, object]", answer) if isinstance(answer, dict) else {}
        text = values.get("connectionString")
        if not isinstance(text, str):
            message = "the answer of the DAL has no connectionString"
            raise ToolError(message)
        return parse_connection_string(text), str(values.get("storageType", "RRD"))

    def drop(self, letter: str, schema: str, storage_type: str, token: str) -> None:
        """Drop an area."""
        query = urllib.parse.urlencode({"dbConnectionIdentifier": schema, "storageType": storage_type})
        self._http(
            "PUT", f"https://mediator-be-{letter}.sis.bfs.admin.ch/api/ProcessingRun/dropProcessingArea?{query}", token
        )


# --- Tools -----------------------------------------------------------------------------------------------------------


def _bounded(arguments: Mapping[str, object], name: str, default: int, maximum: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        message = f"{name} must be a number between 1 and {maximum}"
        raise ToolError(message)
    return value


class OracleTools:
    """The tools of the server."""

    def __init__(self, secrets: Mapping[str, str], database: Database, areas: ProcessingAreas) -> None:
        """Keep the accounts in memory."""
        self._secrets = dict(secrets)
        self._database = database
        self._areas = areas

    def query(self, arguments: Mapping[str, object]) -> str:
        """Run one read-only query."""
        environment = self._environment(arguments)
        statement = self._statement(arguments, validate_query)
        max_rows = _bounded(arguments, "max_rows", DEFAULT_MAX_ROWS, MAX_MAX_ROWS)
        LOGGER.info("query on %s", environment)
        with database_errors():
            result = self._database.query(self._account(environment), statement, max_rows)
        return json.dumps(result, ensure_ascii=False)

    def write(self, arguments: Mapping[str, object]) -> str:
        """Run one write, committed only with the expected number of rows."""
        environment = self._environment(arguments)
        statement = self._statement(arguments, validate_write)
        expected_rows = arguments.get("expect_rows")
        if not isinstance(expected_rows, int) or isinstance(expected_rows, bool) or expected_rows < 1:
            message = "expect_rows is expected: the exact number of rows (at least 1) the statement must touch"
            raise ToolError(message)
        LOGGER.info("write on %s", environment)
        with database_errors():
            result = self._database.write(self._account(environment), statement, expected_rows)
        LOGGER.info("write on %s committed, %s row(s)", environment, result["affected"])
        return json.dumps(result)

    def processing_run(self, arguments: Mapping[str, object]) -> str:
        """Recreate the area of a run, run one read-only query on it, and always drop it."""
        environment = self._environment(arguments)
        run_id = arguments.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
            message = "run_id is expected: the identifier of the processing run"
            raise ToolError(message)
        statement = self._statement(arguments, validate_query)
        max_rows = _bounded(arguments, "max_rows", DEFAULT_MAX_ROWS, MAX_MAX_ROWS)
        letter = ENVIRONMENTS[environment][1]
        token = self._areas.token()
        messages: list[str] = []
        try:
            schema, storage_type = self._areas.recreate(letter, run_id, token)
        except ToolError as error:
            # Mediator may have created the area without the server knowing its schema, which can't be dropped then.
            message = (
                f"{error}; WARNING the processing area of run {run_id} may have been created: check it and drop it "
                "by hand"
            )
            raise ToolError(message) from error
        LOGGER.info("processing area %s of run %s created on %s", schema, run_id, environment)
        messages.append(f"processing area {schema} created")
        dropped = False
        try:
            with database_errors():
                account, storage_type = self._areas.connection(letter, schema, token)
                result = self._database.query(account, statement, max_rows)
        except ToolError as error:
            messages.append(self._drop(letter, schema, storage_type, token))
            dropped = True
            message = f"{error}; {'; '.join(messages)}"
            raise ToolError(message) from error
        finally:
            # Also after an unexpected error: the area is always dropped.
            if not dropped:
                messages.append(self._drop(letter, schema, storage_type, token))
        return json.dumps({**result, "messages": messages}, ensure_ascii=False)

    def _drop(self, letter: str, schema: str, storage_type: str, token: str) -> str:
        try:
            self._areas.drop(letter, schema, storage_type, token)
        except ToolError as error:
            LOGGER.warning("processing area %s not dropped: %s", schema, error)
            return f"WARNING failed to drop processing area {schema}: {error}"
        LOGGER.info("processing area %s dropped", schema)
        return f"processing area {schema} dropped"

    @staticmethod
    def _environment(arguments: Mapping[str, object]) -> str:
        environment = arguments.get("environment")
        if not isinstance(environment, str) or environment not in ENVIRONMENTS:
            message = f"environment: one of {', '.join(ENVIRONMENTS)} is expected"
            raise ToolError(message)
        return environment

    @staticmethod
    def _statement(arguments: Mapping[str, object], validate: Callable[[str], str]) -> Statement:
        sql = arguments.get("sql")
        if not isinstance(sql, str):
            message = "sql is expected"
            raise ToolError(message)
        schema = arguments.get("schema")
        if schema is not None and (not isinstance(schema, str) or not IDENTIFIER_PATTERN.fullmatch(schema)):
            message = f"invalid schema name '{schema}'"
            raise ToolError(message)
        timeout = _bounded(arguments, "timeout_seconds", DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS)
        return Statement(validate(sql), schema, timeout)

    def _account(self, environment: str) -> Account:
        prefix = f"SPIGES_ORACLE_{ENVIRONMENTS[environment][0]}"
        values = [self._secrets.get(f"{prefix}_{suffix}", "") for suffix in ("USER", "PASSWORD", "DATASOURCE")]
        if not all(values):
            message = f"{prefix}_USER, _PASSWORD and _DATASOURCE are expected in the secrets of the server"
            raise ToolError(message)
        return Account(*values)


def _schema(*, write: bool = False, processing_run: bool = False) -> dict[str, object]:
    properties: dict[str, object] = {
        "environment": {"type": "string", "enum": list(ENVIRONMENTS)},
        "sql": {"type": "string", "description": "One statement, without a second one"},
        "schema": {"type": "string", "description": "Schema used for unqualified names (CURRENT_SCHEMA)"},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_SECONDS},
    }
    required = ["environment", "sql"]
    if write:
        properties["expect_rows"] = {"type": "integer", "minimum": 1}
        required.append("expect_rows")
    else:
        properties["max_rows"] = {"type": "integer", "minimum": 1, "maximum": MAX_MAX_ROWS}
    if processing_run:
        properties["run_id"] = {"type": "string"}
        required.append("run_id")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def tools(oracle: OracleTools) -> list[Tool]:
    """Return the tools of the server."""
    return [
        Tool(
            "oracle_query",
            "Runs one SELECT or WITH query on the REF or TST SpiGes Oracle database, in a read-only transaction that "
            "is rolled back. Returns a JSON object: csv, rows, columns, truncated.",
            _schema(),
            oracle.query,
        ),
        Tool(
            "oracle_write",
            "Runs one INSERT, UPDATE or DELETE statement on the REF or TST SpiGes Oracle database, committed only when "
            "it touches exactly expect_rows rows, otherwise rolled back. Returns a JSON object: affected.",
            _schema(write=True),
            oracle.write,
        ),
        Tool(
            "oracle_processing_run",
            "Recreates the processing area of a processing run, runs one read-only query on it (the business objects "
            "are synonyms), and always drops it. Returns a JSON object: csv, rows, columns, truncated, messages.",
            _schema(processing_run=True),
            oracle.processing_run,
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
    oracle = OracleTools(secrets, Database(connect_oracle), ProcessingAreas(secrets.get(SPIGES_CLIENT_SECRET, "")))
    serve(McpServer("claude-mcp-oracle", "1", tools(oracle)), port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
