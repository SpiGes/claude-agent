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
  unprivileged user namespaces for Chromium's own sandbox:
  `echo '{ "args": ["--no-sandbox"] }' > puppeteer-config.json && mmdc -i diagram.mmd -o diagram.png -p puppeteer-config.json`.
  Without `--no-sandbox`, the browser launch fails with "No usable sandbox!".

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

If the task number is not known from the conversation or the workspace, ask for it rather
than guessing or omitting it.

Keep commits atomic wherever possible: do not mix unrelated changes in a single commit.
If a set of changes covers more than one unrelated concern, split it into separate commits
rather than combining them.

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
