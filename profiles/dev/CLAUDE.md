# SpiGes development agent instructions

SpiGes is a service of the SIS microservice infrastructure: Angular/ngrx frontend, ASP.NET Core backend, PostgreSQL + Oracle persistence.

## Priority order

1. Correctness, honesty, and technical reliability.
2. The user's explicit request.
3. Project instruction files (CLAUDE.md, rules directories).
4. Style preferences.

If a reliable answer cannot be produced, say so explicitly rather than guessing.

## Language

- Explanations in the user's language unless another language is explicitly requested.
- Code, code comments, and API/code documentation are always written in English.

## Workspace structure

The agent is usually opened directly inside one of the three SpiGes repositories, each
with its own `CLAUDE.md` covering what is specific to it:

- `backend/branch` — .NET backend (SIS-SpiGes)
- `frontend/branch` — Angular frontend (SIS-SpiGes-UI)
- `specs/branch` — design specs (SIS-SpiGes-Specs)

It can also be opened one level up, at the parent of the three, for a task that spans more
than one of them.

## Tool preferences for code analysis and file manipulation

To save tokens, prefer these over a full Read + manual scan/edit when they fit:

- `ast-grep` — structural code search (matches syntax, not just text) across C# and
  TypeScript, e.g. `ast-grep --lang csharp -p '<pattern>'`. Use instead of `rg` + reading
  each hit's file when the goal is a precise code pattern (a method signature, a specific
  call shape), not a plain string.
- `yq` — read or patch one field in a YAML file (notably Helm `values-*.yaml` in the
  gitops repo) without a full Read + Edit round-trip, e.g.
  `yq '.deployment.replicas' spiges/helm/values-ref.yaml`.
- `xmlstarlet` — query or validate an XML file (mapping configs, EA exports, test
  delivery files in the database-writer/DatabaseWriter domain) without reading it in full.
- `fd` — fast file search that respects `.gitignore` and skips `bin/`, `obj/`,
  `node_modules` by default; prefer it over `find` for locating files by name/pattern.
- `tree` — quick, compact overview of a directory's structure when a full recursive
  listing isn't needed.
- `semgrep` — pattern-based static analysis across languages (C#, TypeScript, YAML),
  useful for the `code-review`/`security-review` skills when a check needs more precision
  than a plain-text `rg` match.
- `ripgrep` (`rg`) stays the default for plain text search when no structural match is
  needed.
- `mmdc` (`@mermaid-js/mermaid-cli`) — renders Mermaid diagram code to PNG/SVG/PDF. Requires
  passing a Puppeteer config file with `--no-sandbox`, since the container has no
  unprivileged user namespaces for Chromium's own sandbox; the config file is written outside
  the repository, so it is never committed by mistake:
  `echo '{ "args": ["--no-sandbox"] }' > /tmp/puppeteer-config.json && mmdc -i diagram.mmd -o diagram.png -p /tmp/puppeteer-config.json`.
  Without `--no-sandbox`, the browser launch fails with "No usable sandbox!". For diagrams in a
  design document, see "Rendering to images" in the `diagrams` skill.
- `file` / `dos2unix` / `unix2dos` — check and fix line endings and encoding. If files are
  CRLF: after a file is rewritten by a script (Python, `sed`, heredoc) rather than the Edit tool,
  its line endings are checked with `file <path>` and restored with `unix2dos <path>` when needed.
  `xxd <path> | head -1` shows the first bytes (e.g. a UTF-8 BOM).
  When a CRLF file is edited from Python, it's read and written with `newline=''`: the default
  newline translation turns `\r\n` into `\n` on read, so a replacement containing `\r\n` silently
  matches nothing.
- `python3` with `openpyxl` — inspect an Excel workbook (worksheets and their visibility, defined
  names, cell values) in a few lines, instead of writing a throwaway .NET project.
- `reportgenerator` — turns the coverlet output of the nunit tests into a readable coverage report,
  when coverage is measured:
  `dotnet test <project> -p:NuGetAudit=false --collect:"XPlat Code Coverage"` then
  `reportgenerator -reports:"**/coverage.cobertura.xml" -targetdir:<scratchpad>/coverage -reporttypes:TextSummary`.
- `helm` — renders a chart of the gitops repository for one environment, to check a change before it's
  pushed:
  `helm template spiges spiges/helm -f spiges/helm/values.yaml -f spiges/helm/values-<env>.yaml --show-only templates/appsettings.yaml`.
  To check that only the intended environments change, the same chart is rendered from `origin/main`
  (`git archive origin/main spiges/helm | tar -x -C <scratchpad>`), and the two renderings are diffed for
  each environment. No cluster access is configured: only `template` is useful.
- `ilspycmd` — decompiles a type of a NuGet package, when the behaviour of a library must be checked
  rather than assumed (retry, default values, filter order):
  `ilspycmd -t <Namespace.Type> ~/.nuget/packages/<package>/<version>/lib/<tfm>/<assembly>.dll`.
- `json5` — reads a JSON file with comments and a BOM (e.g. the backend `appsettings.json`) and prints
  plain JSON, to pipe to `jq`: `json5 appsettings.json | jq '.SpiGes.SurveyPartDatabaseTableNameMapping'`.
  Preferred over hand-written regexes to strip `//` and `/* */` comments.
- `python3` with `yaml` (PyYAML) — reads a rendered Helm manifest or a values file in a script, when `yq`
  alone isn't enough.

## Container environment variables

Skills and their scripts read the variables of the container `.env` file by name (e.g.
`SPIGES_KEYCLOAK_*`, `SPIGES_ORACLE_*`, `SPIGES_BACKEND_SETTINGS_DIR`). A rename breaks them silently
until they're run.

- A variable is renamed, added, or removed only together with the skills and scripts that read it
  and with `.env.example` in the claude-agent repository, in the same change. The readers are listed
  first, e.g. `rg -n '<VARIABLE>' <repositories>/branch/.claude ~/.claude`.
- When a skill fails on a variable that isn't set, the variable names of the environment are listed
  (`env | grep -o '^SPIGES_[A-Z_]*'`, never the values). A mismatch is reported to the user, with the
  readers to update; it's never worked around with a temporary variable on the command line.

## Design documents

Design documents are versioned in the specs repository (SIS-SpiGes-Specs), not here — see
its CLAUDE.md, and `.claude/skills/design-documents/SKILL.md` for the default structure and writing conventions to follow when drafting or
updating one.

## Reference material (read on demand, not preloaded)

- `.claude/docs/SPIGES_SOLUTION_CONTEXT.md` — architecture and business domain hierarchy (`Unit` / `BurGesv` / `EntId` / `UnitDescriptor` / `GroupType` / wave year).
- `.claude/docs/SPIGES_REQUEST_TEMPLATE.md` — preferred format for a feature/analysis/review request, when the user wants to write one explicitly (optional; a short, well-scoped request is usually enough).

## Work modes

Distinguish strictly between:

- **analysis** — explain or assess existing code, design, or behavior. Identify dependencies, implications, assumptions, risks, and open questions when relevant. Do not rewrite content or generate replacement code by default.
- **review** — evaluate existing code, design, tests, or diagrams. Identify issues, risks, inconsistencies, and convention violations; explain why each matters; propose targeted corrections. Do not rewrite wholesale or generate a full replacement implementation by default.
- **development** — generate or modify the requested code, test, document, or diagram. Provide a concrete, usable solution aligned with project conventions.

Interpretation defaults: "analyze" → analysis. "review" → review. "generate" / "implement" / "write" / "create" / "add" / "fix" / "refactor" → development. Never enter development mode from an analysis or review request unless explicitly asked.

## Defaults unless explicitly requested otherwise

- Do not expand a targeted change beyond what was requested; flag possible extensions
  instead of applying them unprompted.
- State low-risk assumptions explicitly rather than asking unnecessary clarifying questions; ask only when proceeding would clearly go in the wrong direction.

## Commit conventions

Structure every commit message as: `[#<task number>] - Explanatory message beginning with
a verb at the third person`.
Example: `[#156397] - Updates and extends tests for the authorized-units trigger`.

The task number is the ID of a work item of type Task, never the ID of its parent (issue,
bug, user story, feature). When the work starts from another type of work item and no task
under it covers the change, a task is proposed before the first commit (title, short
description, parent, assigned to the user) and created after the user's agreement. The pull
request is then linked to the parent work item and to the task(s).

GitHub has no tasks: on GitHub, the commits use the number of the issue instead, and no task is
proposed. The pull request is linked to the issue (e.g. `Closes #4` in its description).

Jira has no tasks either for the repositories hosted on Bitbucket (e.g. sis-sas-toolkit): the commits use the key of
the Jira issue instead (e.g. `[#SIS2-472] - ...`), and no task is proposed. The pull request is created on Bitbucket,
and its description names the Jira issue. Jira is only read by the agent: a comment for the issue is drafted in a file
under `/shared`, in Jira wiki markup, to be pasted by the user in the Text mode of the editor (CRLF line endings,
otherwise the tables are lost).

If the task number is not known from the conversation or the workspace, ask for it rather
than guessing or omitting it.

Keep commits atomic wherever possible: do not mix unrelated changes in a single commit.
If a set of changes covers more than one unrelated concern, split it into separate commits
rather than combining them.

Structure every pull request title as: `[#<id>] - <description>`, on GitHub, Azure DevOps and Bitbucket.
`<id>` is the number of the GitHub issue, of the Azure DevOps bug, user story, or issue, or the key of the
Jira issue the pull request belongs to, never of a task; `<description>` is a short text that sums up the work.
Example: `[#4] - Adds voice input with /voice, through the WSLg audio server`.
A squash merge keeps the title as the message of the merge commit.

**Git hooks.** A commit is never made with `--no-verify`, so that the hooks installed by the user (e.g. pre-commit)
always run. The `.venv` of a repository is the user's, created on the host: its scripts point to host paths and don't
run in the container, so it's never used nor recreated by the agent. When a hook needs the tools of the project, the
agent commits with its own virtual environment, created from the project requirements (e.g. `requirements-dev.txt`)
under `~/.cache/agent-venvs/<repository>`, at the front of the `PATH`. When a hook can't run, the commit isn't made
and the error is reported to the user.

## Files shared with the user

Files that the user reads, copies, or edits are written to `/shared` (mounted from the host), never
left in the scratchpad, which the host can't reach. The scratchpad is only used for temporary files.

- The files of a work item go to `/shared/<work item id>-<topic>/` (e.g.
  `/shared/158985-sis-trigger-endpoint/`), so that they're found and removed at the end of the work
  (`work-item-cleanup` skill) without a text search
- The other files go to `/shared/<topic>/`, or follow the convention of the skill that writes them
  (e.g. `pr-review`)

## Spoken notifications

When the work asked for in a request is finished, or when it's blocked on a decision of the user, a
spoken notification is sent with `agent-notify "<text>"`, as a colleague would do:

- One sentence, in the language of the conversation, saying what was done and its outcome, or what
  is needed (e.g. "J'ai terminé la revue de la PR 44008, trois remarques à valider")
- Not for intermediate steps (build, tests, file reads), and not for a quick answer the user is
  clearly waiting for
- No markdown, paths, or long identifiers, which don't read well aloud

`agent-notify` does nothing when spoken notifications aren't set up on the host. A permission prompt
is announced by a hook, not by the agent. The options of `agent-notify` (mute, voice) are only used
through the `notif-speech-*` skills, at the request of the user, never on the agent's own initiative.

## Voice input

The user may dictate prompts with the voice dictation of Claude Code, through a microphone that the
container can reach. The microphone is never recorded by the agent's own commands (`rec`, `sox`,
`arecord`, `parec`, or any script doing the same), whatever the reason.

## Several agents at the same time

Several agents of this profile can run at the same time, in the same workspace, and share one memory.

- Memory files are written only with the Edit and Write tools, never with a shell command (redirection,
  `sed -i`, script): Edit and Write detect a change made by another agent since the file was read. A
  hook refuses the usual shell commands that write into the memory folder.
- Before a new memory is created, `MEMORY.md` is read again from disk: another agent may have saved the
  same fact since the session started. An existing memory is updated rather than duplicated.
- A context block added to a prompt by the `agent-memory-sync` hook lists the lines of `MEMORY.md`
  changed since the previous prompt, by this agent or by another one. It replaces the out-of-date lines
  of the index loaded at the start of the session.
- `AGENT_SLOT` gives the slot of this agent (1 to `AGENT_MAX_SLOTS`). It's only used to tell the agents
  apart, e.g. in a message to the user.

## Instruction governance

- If you detect an inconsistency between instructions, files (project CLAUDE.md, rules
  files, reference docs), or prior statements in the conversation, flag it before acting on it
  rather than silently resolving it.
- When instructions exist at several levels (project instructions, rules files, reference
  docs, explicit in-conversation request) and appear to contradict each other, the most specific
  (lowest) level is generally the intended reference point for resolving the contradiction.
  This is a criterion to inform the decision, not a rule to act on automatically: still flag
  the inconsistency first, as above, and wait for confirmation before proceeding -- do not
  apply the lower-level rule on the assumption that it must be correct.
- For any multi-step procedure (setup, installation, configuration, migration): present one
  step at a time and wait for confirmation or questions on that step before moving to the
  next one. Do not generate the full procedure upfront unless explicitly asked for the
  complete version at once.
- If a request is phrased with unusual length or detail, or a similar detailed request is
  repeated, suggest formalizing it as a rule in the project instructions or rules files, as
  appropriate. This is a suggestion only: never add or modify a rule without explicit
  confirmation.

@CLAUDE.user.md
