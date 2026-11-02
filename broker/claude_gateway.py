#!/usr/bin/env python3
"""MCP gateway of the agent (see README, "MCP gateway").

Runs in its own container (claude-gateway, started by launch.sh), on the Docker network of the agents and on the
network of each MCP container, which the agent can't reach. It forwards the MCP requests of the agent (streamable
HTTP transport, streamed answers included) to the MCP container of the route, and controls the tool calls
(`tools/call`):

- a tool refused by the policy of the route is refused, as is any write tool of a read-only route (Jira)
- a read tool goes through while the group of the route is open
- any other tool is a write: it goes through while the group is open, and only once the person approved it on the
  host (claude_secret approve), or it's refused after GATEWAY_APPROVAL_SECONDS

The other requests (initialize, notifications, tools/list, the GET stream, DELETE) always go through, so that Claude
Code lists the tools from its start; the tools refused by the policy are removed from the answer of tools/list. The
gateway holds no secret: the tokens are in the MCP containers.

Installed in the image as /usr/local/bin/claude-gateway. Usage:
    claude-gateway serve                                   runs the service (command of the container)
    claude-gateway ctl allow <group>                       opens a group
    claude-gateway ctl revoke <group>|all                  closes a group, or every group
    claude-gateway ctl status                              prints the state of each group and the waiting calls
    claude-gateway ctl pending                             lists the waiting calls, with their full arguments
    claude-gateway ctl approve [<id>] [--tool <minutes>m]  approves a waiting call (the oldest without id); with
                                                           --tool, also the next calls of the same tool by the
                                                           same agent for a while
    claude-gateway ctl deny [<id>]                         refuses a waiting call (the oldest without id)

Every tool call is logged to GATEWAY_LOG with its main arguments only (no content, no secret value).
"""

import http.client
import itertools
import json
import logging
import os
import re
import select
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, cast, override
from urllib.parse import urlsplit

from access import (
    AdminServer,
    Clock,
    Notifier,
    WindowClosedError,
    Windows,
    admin_arguments,
    agent_notifier,
    configure_log,
    format_duration,
    run_ctl,
)

MAX_REQUEST_BYTES: Final = 1024 * 1024
STREAM_CHUNK_BYTES: Final = 64 * 1024
# Request and answer headers of the streamable HTTP transport, forwarded as they are; no other header goes through.
REQUEST_HEADERS: Final = ("Content-Type", "Accept", "Mcp-Session-Id", "Mcp-Protocol-Version", "Last-Event-Id")
RESPONSE_HEADERS: Final = ("Content-Type", "Mcp-Session-Id", "Cache-Control")
SUMMARY_SKIPPED_KEYS: Final = frozenset(
    {"content", "new_content", "content_base64", "text", "body", "description", "comment", "value", "patch"}
)
# Arguments of a waiting write call that `pending` shows by their length only (encoded files, unreadable as text).
DETAILS_ELIDED_KEYS: Final = frozenset({"content_base64"})
SUMMARY_MAX_LENGTH: Final = 160
SUMMARY_MAX_VALUE_LENGTH: Final = 60
# Arguments of a waiting write call shown by `pending`, in full up to this length: the person sees what is written.
DETAILS_MAX_LENGTH: Final = 4000
TOOL_ALLOWANCE_PATTERN: Final = re.compile(r"(\d{1,3})m")
# Container of an agent, named claude-agent-<profile>-<slot> by launch.sh; Docker DNS adds the network to the name.
AGENT_CONTAINER_PATTERN: Final = re.compile(r"claude-agent-([a-z0-9]+)-(\d+)(?:\.|$)")
UNKNOWN_AGENT: Final = "an unknown agent"

LOGGER: Final = logging.getLogger("claude-gateway")


@dataclass(frozen=True, slots=True)
class Policy:
    """Tools of an MCP server: the read tools, and the tools refused by the gateway; any other tool is a write.

    A read-only server (`writes_allowed=False`) has no write: any tool that isn't a read tool is refused.
    """

    group: str
    label: str
    read_tools: frozenset[str]
    denied_tools: frozenset[str] = frozenset()
    denied_prefixes: tuple[str, ...] = ()
    writes_allowed: bool = True

    def classify(self, tool: str) -> str:
        """Return `denied`, `read` or `write`; an unknown tool is a write, or denied on a read-only server."""
        if tool in self.denied_tools or tool.startswith(self.denied_prefixes):
            return "denied"
        if tool in self.read_tools:
            return "read"
        return "write" if self.writes_allowed else "denied"


# Azure DevOps (mcp-devops-onpremise). Denied: the same tools as the deny rules of profiles/dev/settings.json.
DEVOPS_POLICY: Final = Policy(
    group="devops",
    label="DevOps",
    read_tools=frozenset(
        {
            "devops_code_search",
            "devops_get_item_content_diff",
            "devops_pull_request_get",
            "devops_pull_request_list_thread_comments",
            "devops_pull_request_list_threads",
            "devops_work_item_attachment_get",
            "devops_work_item_comment_list",
            "devops_work_item_get",
            "devops_work_item_query_by_wiql",
            "devops_work_item_search",
            "devops_work_item_type_get",
        }
    ),
    denied_tools=frozenset(
        {
            "devops_pull_request_delete_comment",
            "devops_work_item_comment_delete",
            "devops_work_item_delete",
            "devops_work_item_undelete",
        }
    ),
    denied_prefixes=("devops_repository_", "devops_wiki_"),
)

# Jira (mcp-atlassian --read-only): read only, so every other tool is refused, without approval. The read tools are
# those of the server with the readOnlyHint annotation.
JIRA_POLICY: Final = Policy(
    group="jira",
    label="Jira",
    read_tools=frozenset(
        {
            "jira_batch_get_changelogs",
            "jira_download_attachments",
            "jira_get_agile_boards",
            "jira_get_all_projects",
            "jira_get_board_issues",
            "jira_get_create_fields",
            "jira_get_cross_project_dependencies",
            "jira_get_field_options",
            "jira_get_issue",
            "jira_get_issue_dates",
            "jira_get_issue_development_info",
            "jira_get_issue_images",
            "jira_get_issue_proforma_forms",
            "jira_get_issue_sla",
            "jira_get_issue_watchers",
            "jira_get_issues_development_info",
            "jira_get_link_types",
            "jira_get_proforma_form_details",
            "jira_get_project_components",
            "jira_get_project_epic_hierarchy",
            "jira_get_project_fields",
            "jira_get_project_issue_types",
            "jira_get_project_issues",
            "jira_get_project_versions",
            "jira_get_queue_issues",
            "jira_get_request_type_fields",
            "jira_get_request_types",
            "jira_get_service_desk_for_project",
            "jira_get_service_desk_queues",
            "jira_get_sprint_issues",
            "jira_get_sprints_from_board",
            "jira_get_transitions",
            "jira_get_user_profile",
            "jira_get_worklog",
            "jira_search",
            "jira_search_assignable_users",
            "jira_search_fields",
            "jira_search_projects",
        }
    ),
    writes_allowed=False,
)

# Confluence (mcp-atlassian). The read tools are those of the server with the readOnlyHint annotation. Denied: the same
# tools as the deny rules of profiles/dev/settings.json.
CONFLUENCE_POLICY: Final = Policy(
    group="confluence",
    label="Confluence",
    read_tools=frozenset(
        {
            "confluence_check_content_permissions",
            "confluence_download_attachment",
            "confluence_download_content_attachments",
            "confluence_get_attachments",
            "confluence_get_comments",
            "confluence_get_inline_comments",
            "confluence_get_labels",
            "confluence_get_page",
            "confluence_get_page_children",
            "confluence_get_page_diff",
            "confluence_get_page_history",
            "confluence_get_page_images",
            "confluence_get_page_restrictions",
            "confluence_get_page_template",
            "confluence_get_page_views",
            "confluence_get_space_page_tree",
            "confluence_get_space_permissions",
            "confluence_list_page_templates",
            "confluence_search",
            "confluence_search_user",
        }
    ),
    denied_tools=frozenset(
        {
            "confluence_delete_page",
            "confluence_move_page",
            "confluence_set_page_restrictions",
        }
    ),
)

# Keycloak (claude-mcp-keycloak): short-lived access tokens, read by the skill scripts through mcp-call; no write.
KEYCLOAK_POLICY: Final = Policy(
    group="keycloak",
    label="Keycloak",
    read_tools=frozenset({"keycloak_profiles", "keycloak_token"}),
    writes_allowed=False,
)

# SAS (claude-mcp-sas): commands on the SAS server of REF. Every command, upload and SAS run is a write, approved one by
# one; a download to the drop folder is a read.
SAS_POLICY: Final = Policy(
    group="sas",
    label="SAS",
    read_tools=frozenset({"sas_get"}),
)

# Oracle (claude-mcp-oracle): read-only queries go through while the group is open; a write and the recreation of a
# processing area (a schema created and dropped, and the run marked as failed when the recreation fails) are approved
# one by one, with the statement shown on the host.
ORACLE_POLICY: Final = Policy(
    group="oracle",
    label="Oracle",
    read_tools=frozenset({"oracle_query"}),
)

POLICIES: Final[Mapping[str, Policy]] = {
    "devops": DEVOPS_POLICY,
    "jira": JIRA_POLICY,
    "confluence": CONFLUENCE_POLICY,
    "keycloak": KEYCLOAK_POLICY,
    "sas": SAS_POLICY,
    "oracle": ORACLE_POLICY,
}


@dataclass(frozen=True, slots=True)
class Route:
    """An MCP server reached through the gateway at /mcp/<name>."""

    name: str
    upstream: str
    policy: Policy


@dataclass(frozen=True, slots=True)
class Settings:
    """Settings of the gateway, read from the environment of its container."""

    port: int = 8090
    routes: tuple[Route, ...] = ()
    idle_seconds: float = 30 * 60
    max_seconds: float = 4 * 3600
    approval_seconds: float = 120
    log_file: Path = Path("/log/gateway.log")
    # On the tmpfs of the gateway container, which only its own processes can reach.
    admin_socket: Path = Path("/tmp/claude-gateway/admin.sock")  # noqa: S108  # nosec B108

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> "Settings":
        """Read the settings from GATEWAY_* variables.

        GATEWAY_PORT, GATEWAY_ROUTES (`<name>=<url>,...`), GATEWAY_IDLE_MINUTES, GATEWAY_MAX_HOURS,
        GATEWAY_APPROVAL_SECONDS, GATEWAY_LOG and GATEWAY_ADMIN_SOCKET.
        """
        defaults = cls()
        return cls(
            port=int(environ.get("GATEWAY_PORT", defaults.port)),
            routes=parse_routes(environ.get("GATEWAY_ROUTES", "")),
            idle_seconds=float(environ.get("GATEWAY_IDLE_MINUTES", defaults.idle_seconds / 60)) * 60,
            max_seconds=float(environ.get("GATEWAY_MAX_HOURS", defaults.max_seconds / 3600)) * 3600,
            approval_seconds=float(environ.get("GATEWAY_APPROVAL_SECONDS", defaults.approval_seconds)),
            log_file=Path(environ.get("GATEWAY_LOG", str(defaults.log_file))),
            admin_socket=Path(environ.get("GATEWAY_ADMIN_SOCKET", str(defaults.admin_socket))),
        )


def parse_routes(text: str) -> tuple[Route, ...]:
    """Parse `<name>=<http url>,...`; every name needs a policy."""
    routes: list[Route] = []
    for item in filter(None, (part.strip() for part in text.split(","))):
        name, _, upstream = item.partition("=")
        policy = POLICIES.get(name)
        if policy is None or urlsplit(upstream).scheme != "http":
            message = f"invalid route '{item}': expected <name>=http://<host>:<port>/<path>, name in {sorted(POLICIES)}"
            raise ValueError(message)
        routes.append(Route(name, upstream, policy))
    return tuple(routes)


def without_denied_tools(body: bytes, content_type: str, policy: Policy) -> bytes:
    """Remove the tools refused by the policy from an answer to tools/list, JSON or server-sent events.

    Only a convenience (the agent doesn't try tools that would be refused): a body that can't be read is returned as it
    is, the calls of these tools are refused anyway.
    """
    if content_type.startswith("application/json"):
        return _filtered_json(body, policy) or body
    if not content_type.startswith("text/event-stream"):
        return body
    lines = body.decode("utf-8", errors="replace").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("data:"):
            ending = line[len(line.rstrip("\r\n")) :]
            filtered = _filtered_json(line[len("data:") :].strip().encode("utf-8"), policy)
            if filtered is not None:
                lines[index] = "data: " + filtered.decode("utf-8") + ending
    return "".join(lines).encode("utf-8")


def _filtered_json(data: bytes, policy: Policy) -> bytes | None:
    try:
        message: object = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    result = cast("dict[str, object]", message).get("result") if isinstance(message, dict) else None
    tools = cast("dict[str, object]", result).get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        return None
    cast("dict[str, object]", result)["tools"] = [
        tool
        for tool in cast("list[object]", tools)
        if not (
            isinstance(tool, dict) and policy.classify(str(cast("dict[str, object]", tool).get("name"))) == "denied"
        )
    ]
    return json.dumps(message, ensure_ascii=False).encode("utf-8")


def agent_name(address: str, resolve: Callable[[str], tuple[str, list[str], list[str]]] = socket.gethostbyaddr) -> str:
    """Return the name of the agent at an address of the Docker network, e.g. `agent dev 2`, from its container name.

    Several agents (slots) share the gateway: the name tells the person which one asks for a write.
    """
    try:
        host = resolve(address)[0]
    except OSError:
        return f"{UNKNOWN_AGENT} ({address})"
    match = AGENT_CONTAINER_PATTERN.match(host)
    return f"agent {match[1]} {match[2]}" if match else f"{UNKNOWN_AGENT} ({address})"


def json_rpc_method(message: object) -> object:
    """Return the method of a JSON-RPC message, or None."""
    return cast("dict[str, object]", message).get("method") if isinstance(message, dict) else None


def summarize(arguments: object) -> str:
    """Return the main arguments of a tool call (identifiers, short values), for the approval and the log."""
    if not isinstance(arguments, dict):
        return ""
    parts: list[str] = []
    for key, value in cast("dict[str, object]", arguments).items():
        if key in SUMMARY_SKIPPED_KEYS or isinstance(value, dict | list):
            continue
        text = str(value).replace("\n", " ")
        if len(text) > SUMMARY_MAX_VALUE_LENGTH:
            text = text[: SUMMARY_MAX_VALUE_LENGTH - 1] + "…"
        parts.append(f"{key}={text}")
    summary = ", ".join(parts)
    return summary if len(summary) <= SUMMARY_MAX_LENGTH else summary[: SUMMARY_MAX_LENGTH - 1] + "…"


def details(arguments: object) -> str:
    """Return the full arguments of a tool call, indented, for the approval on the host (never logged).

    An encoded file (e.g. `content_base64`) is replaced by its length: unreadable as text, it would push the other
    arguments past the length limit.
    """
    if isinstance(arguments, dict):
        arguments = {
            key: (
                f"({len(value)} characters of base64)"
                if key in DETAILS_ELIDED_KEYS and isinstance(value, str)
                else value
            )
            for key, value in cast("dict[str, object]", arguments).items()
        }
    text = json.dumps(arguments, ensure_ascii=False, indent=2, sort_keys=True)
    if len(text) > DETAILS_MAX_LENGTH:
        text = text[:DETAILS_MAX_LENGTH] + f"\n… (cut, {len(text)} characters in all)"
    return text


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A write tool call, as shown to the person: group and label of its route, tool, arguments, and calling agent."""

    group: str
    label: str
    tool: str
    summary: str
    details: str
    agent: str = UNKNOWN_AGENT


@dataclass(slots=True)
class PendingCall:
    """A write tool call waiting for the decision of the person."""

    number: int
    call: ToolCall
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False

    def describe(self) -> str:
        """Return the line shown by `status`."""
        summary = f" ({self.call.summary})" if self.call.summary else ""
        return f"  {self.number}: {self.call.agent}, {self.call.label} {self.call.tool}{summary}"

    def describe_in_full(self) -> str:
        """Return the lines shown by `pending`: the line of `status`, then the full arguments."""
        arguments = "\n".join(f"      {line}" for line in self.call.details.splitlines())
        return f"{self.describe()}\n{arguments}"


class Approvals:
    """Write calls waiting for an approval, and the tools approved for a while (`approve --tool`)."""

    def __init__(self, timeout_seconds: float, clock: Clock, notifier: Notifier) -> None:
        """Start without any waiting call."""
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._notifier = notifier
        self._lock = threading.Lock()
        self._numbers = itertools.count(1)
        self._pending: dict[int, PendingCall] = {}
        # Tools approved for a while (`approve --tool`), by group, tool, and agent: the approval of a call of one agent
        # doesn't let the calls of another agent through.
        self._allowed_until: dict[tuple[str, str, str], float] = {}

    @property
    def timeout_seconds(self) -> float:
        """Return the time given to approve a write call."""
        return self._timeout_seconds

    def request(self, call: ToolCall) -> tuple[bool, str]:
        """Wait for the decision on a write call; return whether it's approved, and how."""
        key = (call.group, call.tool, call.agent)
        with self._lock:
            if self._clock() < self._allowed_until.get(key, float("-inf")):
                return True, "approved for the tool"
            pending = PendingCall(next(self._numbers), call)
            self._pending[pending.number] = pending
        LOGGER.info("call %s waiting: %s, %s %s (%s)", pending.number, call.agent, call.label, call.tool, call.summary)
        self._notifier(f"Écriture {call.label} à approuver pour {call.agent} : {call.tool}")
        pending.decided.wait(self._timeout_seconds)
        # The decision is read under the lock that `decide` holds: a decision made just after the wait timed out, but
        # before the call left the list, still counts, so that the person is never told the opposite of what happens.
        with self._lock:
            self._pending.pop(pending.number, None)
            decided, approved = pending.decided.is_set(), pending.approved
        if not decided:
            return False, "no approval in time"
        return approved, "approved" if approved else "denied"

    def forget_group(self, group: str) -> None:
        """Remove the tools approved for a while (`approve --tool`) of a group, e.g. when it closes."""
        with self._lock:
            for key in [key for key in self._allowed_until if key[0] == group]:
                del self._allowed_until[key]

    def decide(self, args: Sequence[str], *, approve: bool) -> str:
        """Approve or deny a waiting call: `[<id>]`, plus `[--tool <minutes>m]` for an approval."""
        number, minutes = _decision_arguments(args, allow_tool=approve)
        with self._lock:
            if not self._pending:
                message = "no call is waiting"
                raise ValueError(message)
            if number is None:
                number = min(self._pending)
            pending = self._pending.get(number)
            if pending is None:
                message = f"no waiting call {number} (waiting: {', '.join(map(str, sorted(self._pending)))})"
                raise ValueError(message)
            pending.approved = approve
            pending.decided.set()
            tool = pending.call.tool
            if minutes is not None:
                self._allowed_until[pending.call.group, tool, pending.call.agent] = self._clock() + minutes * 60
        LOGGER.info("call %s %s%s", number, "approved" if approve else "denied", _for_minutes(minutes, tool))
        return f"Call {number} {'approved' if approve else 'denied'}: {tool}{_for_minutes(minutes, tool)}"

    def pending_lines(self, *, in_full: bool = False) -> list[str]:
        """Return the waiting calls, the oldest first: one line each, or with their full arguments."""
        with self._lock:
            calls = [self._pending[number] for number in sorted(self._pending)]
        return [call.describe_in_full() if in_full else call.describe() for call in calls]


def _decision_arguments(args: Sequence[str], *, allow_tool: bool) -> tuple[int | None, int | None]:
    number: int | None = None
    minutes: int | None = None
    rest = list(args)
    while rest:
        arg = rest.pop(0)
        if arg == "--tool" and allow_tool and rest and (match := TOOL_ALLOWANCE_PATTERN.fullmatch(rest[0])):
            minutes = int(match.group(1))
            rest.pop(0)
        elif arg.isdigit() and number is None:
            number = int(arg)
        else:
            usage = "[<id>] [--tool <minutes>m]" if allow_tool else "[<id>]"
            message = f"invalid argument '{arg}' (expected {usage})"
            raise ValueError(message)
    return number, minutes


def _for_minutes(minutes: int | None, tool: str) -> str:
    return f", and the next calls of {tool} by the same agent for {minutes} min" if minutes is not None else ""


class Gateway:
    """State of the gateway (windows, waiting calls), shared by the service and the commands of the host."""

    def __init__(self, settings: Settings, clock: Clock = time.monotonic, notifier: Notifier | None = None) -> None:
        """Start with every group closed."""
        notify = notifier if notifier is not None else agent_notifier()
        self.routes = {route.name: route for route in settings.routes}
        self.windows = Windows(
            sorted({route.policy.group for route in settings.routes}),
            idle_seconds=settings.idle_seconds,
            max_seconds=settings.max_seconds,
            clock=clock,
            notifier=notify,
            on_close=self._on_close,
        )
        self.approvals = Approvals(settings.approval_seconds, clock, notify)

    def _on_close(self, group: str, reason: str) -> None:
        # A closed group also loses its tools approved for a while: reopening it doesn't bring them back.
        self.approvals.forget_group(group)
        LOGGER.info("group %s closed (%s)", group, reason)

    def check(self, route: Route, request: object, agent: str = UNKNOWN_AGENT) -> str | None:
        """Decide on an MCP request of an agent: None to forward it, or the reason of the refusal of a tool call."""
        if not isinstance(request, dict):
            return "a single JSON-RPC message is expected"
        message = cast("dict[str, object]", request)
        if message.get("method") != "tools/call":
            return None
        params = message.get("params")
        params_map = cast("dict[str, object]", params) if isinstance(params, dict) else {}
        tool = params_map.get("name")
        if not isinstance(tool, str) or not tool:
            return "invalid tool call: no tool name"
        arguments = params_map.get("arguments")
        summary = summarize(arguments)
        kind = route.policy.classify(tool)
        refusal = self._refusal(route, tool, kind, arguments, summary=summary, agent=agent)
        LOGGER.info("%s %s %s (%s) by %s: %s", route.name, kind, tool, summary, agent, refusal or "allowed")
        return refusal

    def _refusal(  # noqa: PLR0913  # the facts of one tool call, logged by the caller too
        self, route: Route, tool: str, kind: str, arguments: object, *, summary: str, agent: str
    ) -> str | None:
        if kind == "denied":
            return f"the tool {tool} is refused by the MCP gateway"
        try:
            self.windows.use(route.policy.group)
        except WindowClosedError as error:
            return str(error)
        if kind == "read":
            return None
        policy = route.policy
        call = ToolCall(policy.group, policy.label, tool, summary, details(arguments), agent)
        approved, how = self.approvals.request(call)
        if approved:
            return None
        if how == "denied":
            return f"the write call {tool} was denied on the host"
        return (
            f"the write call {tool} wasn't approved within {format_duration(self.approvals.timeout_seconds)}: "
            "it needs `claude_secret approve` on the host while it's waiting"
        )

    def status(self) -> str:
        """Return the state of each group and the waiting calls."""
        pending = self.approvals.pending_lines()
        lines = ["MCP gateway:", *self.windows.status_lines()]
        lines += ["Waiting calls:", *pending] if pending else ["No waiting call."]
        return "\n".join(lines)

    def pending(self) -> str:
        """Return the waiting calls, with their full arguments."""
        return "\n".join(self.approvals.pending_lines(in_full=True)) or "No waiting call."

    def allow(self, group: str) -> str:
        """Open a group."""
        message = self.windows.open(group)
        LOGGER.info("group %s opened", group)
        return message

    def revoke(self, group: str) -> str:
        """Close a group, or every group (`all`)."""
        closed = self.windows.revoke(group)
        return "Closed: " + (", ".join(closed) if closed else "no open group")


def run_admin_command(gateway: Gateway, request: Mapping[str, object]) -> tuple[bool, str]:
    """Run a command of the host (`ctl`); return whether it succeeded and its message."""
    command = request.get("command")
    args = admin_arguments(request)
    handlers: dict[str, Callable[[], str]] = {
        "status": gateway.status,
        "pending": gateway.pending,
        "approve": lambda: gateway.approvals.decide(args, approve=True),
        "deny": lambda: gateway.approvals.decide(args, approve=False),
    }
    with_group: dict[str, Callable[[str], str]] = {"allow": gateway.allow, "revoke": gateway.revoke}
    try:
        if isinstance(command, str) and command in with_group and len(args) == 1:
            return True, with_group[command](args[0])
        if isinstance(command, str) and command in handlers and (args == [] or command in {"approve", "deny"}):
            return True, handlers[command]()
    except ValueError as error:
        return False, str(error)
    return False, f"unknown command: {command} {' '.join(args)}".rstrip()


class GatewayHTTPServer(ThreadingHTTPServer):
    """HTTP service of the agent network: the MCP servers of type `http` of the agent point here."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], gateway: Gateway) -> None:
        """Serve `gateway` at `address`."""
        self.gateway = gateway
        super().__init__(address, GatewayHandler)


class GatewayHandler(BaseHTTPRequestHandler):
    """Requests of the agent at /mcp/<name>, forwarded to the MCP container of the route."""

    server_version = "claude-gateway"
    protocol_version = "HTTP/1.1"

    @property
    def _gateway(self) -> Gateway:
        server = self.server
        if not isinstance(server, GatewayHTTPServer):  # pragma: no cover - set by GatewayHTTPServer
            message = "the handler needs a GatewayHTTPServer"
            raise TypeError(message)
        return server.gateway

    def do_POST(self) -> None:
        """Forward an MCP message, after the check of a tool call."""
        route = self._route()
        if route is None:
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply_before_body(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if not 0 <= length <= MAX_REQUEST_BYTES:
            self._reply_before_body(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
            return
        body = self.rfile.read(length)
        try:
            request: object = json.loads(body)
        except json.JSONDecodeError:
            self._reply_text(HTTPStatus.BAD_REQUEST, "invalid JSON")
            return
        method = json_rpc_method(request)
        agent = agent_name(self.client_address[0]) if method == "tools/call" else UNKNOWN_AGENT
        refusal = self._gateway.check(route, request, agent)
        if refusal is not None:
            self._reply_tool_error(request, refusal)
            return
        if method == "tools/call" and self._client_gone():
            # e.g. a write approved after the agent gave the call up (Esc in Claude Code): never run unknown to it.
            LOGGER.info("%s call not forwarded: the agent closed its connection while the call was waiting", route.name)
            self.close_connection = True
            return
        lists_tools = isinstance(request, dict) and cast("dict[str, object]", request).get("method") == "tools/list"
        self._forward(route, body, filter_tools=lists_tools)

    def do_GET(self) -> None:
        """Forward the stream of the server (server-sent events)."""
        route = self._route()
        if route is not None:
            self._forward(route, None)

    def do_DELETE(self) -> None:
        """Forward the end of the session."""
        route = self._route()
        if route is not None:
            self._forward(route, None)

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Log nothing: the tool calls are logged by the gateway, without the default output of http.server."""

    def _route(self) -> Route | None:
        name = self.path.removeprefix("/mcp/").split("?", 1)[0]
        route = self._gateway.routes.get(name) if self.path.startswith("/mcp/") else None
        if route is None:
            self._reply_before_body(HTTPStatus.NOT_FOUND, "unknown path")
        return route

    def _client_gone(self) -> bool:
        """Return whether the agent closed its connection: a closed socket is readable, with nothing to read."""
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            return bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def _forward(self, route: Route, body: bytes | None, *, filter_tools: bool = False) -> None:
        upstream = urlsplit(route.upstream)
        headers = {name: value for name in REQUEST_HEADERS if (value := self.headers.get(name)) is not None}
        connection = http.client.HTTPConnection(upstream.hostname or "", upstream.port or 80)
        try:
            connection.request(self.command, upstream.path or "/", body=body, headers=headers)
            response = connection.getresponse()
        except OSError as error:
            connection.close()
            message = f"MCP server {route.name} unreachable ({error}): run `claude_mcp start {route.name}` on the host"
            self._reply_text(HTTPStatus.BAD_GATEWAY, message)
            return
        try:
            if filter_tools:
                self._relay_tools(response, route.policy)
            else:
                self._relay(response)
        except OSError:
            # The agent closed the connection, e.g. at the end of a GET stream.
            self.close_connection = True
        finally:
            connection.close()

    def _relay(self, response: http.client.HTTPResponse) -> None:
        self.send_response(response.status)
        for name in RESPONSE_HEADERS:
            if (value := response.getheader(name)) is not None:
                self.send_header(name, value)
        length = response.getheader("Content-Length")
        if length is not None:
            data = response.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        # Streamed answer (server-sent events): forwarded chunk by chunk, as it comes.
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while chunk := response.read1(STREAM_CHUNK_BYTES):
            self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")

    def _relay_tools(self, response: http.client.HTTPResponse, policy: Policy) -> None:
        # An answer to tools/list is short and ends with the answer (also as server-sent events): it's read in full,
        # then sent without the tools refused by the policy.
        content_type = response.getheader("Content-Type") or ""
        data = without_denied_tools(response.read(), content_type, policy)
        self.send_response(response.status)
        for name in RESPONSE_HEADERS:
            if (value := response.getheader(name)) is not None:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _reply_tool_error(self, request: object, reason: str) -> None:
        request_id = cast("dict[str, object]", request).get("id") if isinstance(request, dict) else None
        answer = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": f"MCP gateway: {reason}"}], "isError": True},
        }
        self._reply(HTTPStatus.OK, "application/json", json.dumps(answer).encode("utf-8"))

    def _reply_text(self, status: HTTPStatus, text: str) -> None:
        self._reply(status, "text/plain; charset=utf-8", text.encode("utf-8"))

    def _reply_before_body(self, status: HTTPStatus, text: str) -> None:
        """Reply without reading the body of the request, then close the connection.

        On a kept-alive connection, the unread body would be read as the start of the next request (400 "Bad request
        syntax" instead of this answer).
        """
        self.close_connection = True
        self._reply(status, "text/plain; charset=utf-8", text.encode("utf-8"), close=True)

    def _reply(self, status: HTTPStatus, content_type: str, data: bytes, *, close: bool = False) -> None:
        self.send_response(status)
        if close:
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(settings: Settings) -> None:
    """Run the service and the commands of the host until the container stops."""
    configure_log(LOGGER, settings.log_file)
    gateway = Gateway(settings)
    admin = AdminServer(settings.admin_socket, lambda request: run_admin_command(gateway, request))
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    LOGGER.info(
        "started (routes %s; idle limit %s, maximum %s, approval %s)",
        ", ".join(f"{route.name}={route.upstream}" for route in settings.routes) or "none",
        format_duration(settings.idle_seconds),
        format_duration(settings.max_seconds),
        format_duration(settings.approval_seconds),
    )
    # Every interface of the container: the gateway is only reachable on the Docker network of the agents.
    GatewayHTTPServer(("0.0.0.0", settings.port), gateway).serve_forever()  # noqa: S104  # nosec B104


def main(argv: Sequence[str] | None = None) -> int:
    """Run `serve` or `ctl <command>`."""
    args = list(sys.argv[1:] if argv is None else argv)
    settings = Settings.from_environment()
    if args[:1] == ["serve"]:
        serve(settings)
        return 0
    if args[:1] == ["ctl"]:
        return run_ctl(args[1:], settings.admin_socket, __doc__ or "")
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
