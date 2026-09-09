# SpiGes — project instructions

Version: v4

SpiGes is a service of the SIS microservice infrastructure: Angular/ngrx frontend, ASP.NET Core backend, PostgreSQL + Oracle persistence.

## Priority order

1. Correctness, honesty, and technical reliability.
2. The user's explicit request.
3. This file and `.claude/rules/`.
4. Style preferences.

If a reliable answer cannot be produced, say so explicitly rather than guessing.

## Workspace Structure

Two separate Git repositories, mounted side-by-side under /workspace:

- /workspace/backend/branch — .NET backend (SIS-SpiGes)
- /workspace/frontend/branch — Angular frontend (SIS-SpiGes-UI)

The TypeScript client used by the frontend is generated from the C# contract

of the backend (see SPIGES_FRONTEND_PATTERNS.md on the frontend side) — a task that
affects a backend DTO often has an impact on the frontend, and vice versa.

Unless otherwise specified, stay in the repository relevant to the request;
only modify the other repository if the task explicitly requires it.

# Build Convention — Agent Environment (Docker Container)

Always use `-p:NuGetAudit=false` with `dotnet build` and `dotnet restore`
in this environment. The NuGet Audit check (online vulnerability check at api.nuget.org) is blocked by the corporate proxy
and increases the build time from ~40s to ~3min. This setting applies ONLY to
this agent environment — never include it in the repository's .csproj files.

## Reference material (read on demand, not preloaded)

- `.claude/docs/SPIGES_SOLUTION_CONTEXT.md` — architecture and business domain hierarchy (`Unit` / `BurGesv` / `EntId` / `UnitDescriptor` / `GroupType` / wave year).
- `.claude/docs/SPIGES_DEFINITIONS.md` — reference C# definitions (enums, `UnitDescriptor`, etc.); backend only, frontend TS equivalents are generated from these contracts.
- `.claude/docs/SPIGES_DESIGN_SPECS_INDEX.md` — index of the design specs in `docs/design-specs/`, with current/draft status per feature.
- `.claude/docs/SpiGes_tests_examples.md` — worked unit test examples illustrating `.claude/rules/testing.md`.
- `.claude/docs/Mermaid_diagram_examples.md` — reference diagram examples (class/ER/sequence) for when a diagram is explicitly requested.
- `.claude/docs/SPIGES_FRONTEND_REFERENCE_EXAMPLE_DataDisclosure.md` — full frontend reference implementation (view-model-selector pattern).
- `.claude/docs/SPIGES_REQUEST_TEMPLATE.md` — suggested structure for a feature/analysis/review request when the user wants to write one explicitly (optional; exploring the code directly is usually enough in agent mode).

Backend and frontend pattern rules, and C# coding conventions, load automatically from `.claude/rules/` when a matching file is opened — see `.claude/rules/backend.md`, `.claude/rules/csharp-coding-conventions.md`, `.claude/rules/frontend.md`, `.claude/rules/testing.md`. Two Skills are available for documentation generation: `doc-confluence` and `doc-html`.

## Language

- Explanations in the user's language unless another language is explicitly requested.
- Code, code comments, and API/code documentation are always written in English.
- Keyword trigger "en anglais technique": switch to English, neutral tone, passive voice,
  simple technical English (max B2 level), ASCII characters only (no accents, curly quotes,
  em-dashes, emojis), standard contractions (doesn't, don't, shouldn't, won't, couldn't, etc.),
  no trailing punctuation in bulleted/numbered list items. Applies to any deliverable, not only
  design documents (see below, which follows the same style by default).

## Work modes

Distinguish strictly between:

- **analysis** — explain or assess existing code, design, or behavior. Identify dependencies, implications, assumptions, risks, and open questions when relevant. Do not rewrite content or generate replacement code by default.
- **review** — evaluate existing code, design, tests, or diagrams. Identify issues, risks, inconsistencies, and convention violations; explain why each matters; propose targeted corrections. Do not rewrite wholesale or generate a full replacement implementation by default.
- **development** — generate or modify the requested code, test, document, or diagram. Provide a concrete, usable solution aligned with project conventions.

Interpretation defaults: "analyze" → analysis. "review" → review. "generate" / "implement" / "write" / "create" / "add" / "fix" / "refactor" → development. Never enter development mode from an analysis or review request unless explicitly asked.

## Defaults unless explicitly requested otherwise

- Do not generate unit tests.
- Do not generate a design document.
- Do not generate UML or Mermaid diagrams.
- Do not expand a targeted change beyond what was requested; flag possible extensions
  instead of applying them unprompted.
- State low-risk assumptions explicitly rather than asking unnecessary clarifying questions; ask only when proceeding would clearly go in the wrong direction.

## Instruction governance

- If you detect an inconsistency between instructions, files (`CLAUDE.md`, `.claude/rules/`,
  `.claude/docs/`), or prior statements in the conversation, flag it before acting on it
  rather than silently resolving it.
- When instructions exist at several levels (this file, `.claude/rules/`, `.claude/docs/`,
  explicit in-conversation request) and appear to contradict each other, the most specific
  (lowest) level is generally the intended reference point for resolving the contradiction.
  This is a criterion to inform the decision, not a rule to act on automatically: still flag
  the inconsistency first, as above, and wait for my confirmation before proceeding -- do not
  apply the lower-level rule on the assumption that it must be correct.
- For any multi-step procedure (setup, installation, configuration, migration): present one
  step at a time and wait for my confirmation or questions on that step before moving to the
  next one. Do not generate the full procedure upfront unless I explicitly ask for the
  complete version at once.
- If I phrase a request with unusual length or detail, or repeat a similar detailed request,
  suggest formalizing it as a rule in this file or in `.claude/rules/`, as appropriate. This
  is a suggestion only: never add or modify a rule without my explicit confirmation.

## Default technical scope

- Frontend: Angular (standalone components), Angular Material, ngrx, rxjs, Oblique.
- Backend: C#, ASP.NET Core, PostgreSQL, Oracle.
- Documentation / static site: Hugo-Extended, Docsy.

## General code rules

Follow SOLID, DRY, established design patterns, security, maintainability, and consistency with the existing architecture. "Complete code" means what's needed to understand, use, and integrate the change — not unrelated surrounding code. Full C# style detail lives in `.claude/rules/csharp-coding-conventions.md` (loads automatically when editing `.cs` files).

## Diagrams (when explicitly requested)

Provide raw Mermaid source in a plain code block, **without** the `mermaid` language tag after the backticks — kept as copyable source only, no rendered preview. For UML-style diagrams: do not display `CancellationToken`; do not display `Task` (use the underlying type, or nothing if void); omit member/variable types unless omitting them would create ambiguity; use the UML relationship that matches the real design (association, aggregation, composition, dependency, realization, inheritance) rather than simplifying for visual convenience. See `docs/Mermaid_diagram_examples.md` for reference examples.

## Design documents (when explicitly requested)

Generate a design document only when it is explicitly requested.

### General rules

When a design document is requested:
- use a canvas/Artifact;
- write only one authoritative version in it, updated through targeted edits rather than full regeneration;
- write in simple technical English with a maximum B2 level;
- use a neutral, simple, human-like style;
- prefer passive form;
- write class names, method names, and enumeration names in italics;
- use as much as possible ASCII characters;
- in bulleted or numbered lists, do not end each line with punctuation.

A table of contents must not be written or maintained by hand, because it becomes wrong
as soon as a chapter is added or renamed. It is expected to be produced by the publishing
platform, for example by the Confluence table of contents macro, or by a generator at
delivery time.

### Default structure

Unless another structure is explicitly requested, organize the document as follows:
1. Versions;
2. References;
3. Purpose;
4. Scope;
5. Solution overview;
6. one chapter per design point to resolve;
7. Changes to the existing system;
8. Configuration;
9. Open points;
10. Static diagrams;
11. Dynamic diagrams.

The expected content of those chapters is the following:
- `Versions` is a table with the version, the date, and a short description of the change;
- `References` lists the work items, the related design documents, and the external sources;
- `Purpose` explains what the feature does and why it is needed, without describing the solution;
- `Scope` states what is covered and, above all, what is left out;
- `Solution overview` describes the retained solution as a whole, in about half a page, so that the following chapters can be read in any order;
- the design point chapters carry the substance of the document;
- `Changes to the existing system` lists the existing code and behavior that are modified, with the associated risk and the way back;
- `Configuration` describes all new parameters, with their meaning and their default value;
- `Open points` lists the questions that are still open, which are mostly business decisions;
- the two diagram chapters are left empty, because diagrams are generated separately.

A chapter that carries no content for a given feature may be omitted, except `Purpose`
and the design point chapters.

### Design point chapters

Each design point is given its own chapter, named after the question that it answers
rather than after a work item number.

Those chapters must be ordered by dependency, so that a decision which conditions other
decisions is placed first. They must not be ordered by work item number.

Each of those chapters should contain, written as prose and without sub-headings:
- the problem to solve;
- the options that were considered, when there was more than one;
- the retained decision;
- the reason for that decision, and the consequence that is accepted with it.

A decision must be stated in an affirmative form. When a point can't be decided yet, it
belongs to `Open points` and not to a conditional sentence inside a design point chapter.
Keeping the undecided points in a single chapter is what allows the design point chapters
to stay affirmative.

If important information is missing and prevents a precise design explanation, ask targeted clarifying questions.

## Commit conventions

Structure every commit message as: `[#<task number>] - Explanatory message beginning with
a verb at the third person`.
Example: `[#156397] - Updates and extends tests for the authorized-units trigger`.

If the task number is not known from the conversation or the workspace, ask for it rather
than guessing or omitting it.

Keep commits atomic wherever possible: do not mix unrelated changes in a single commit.
If a set of changes covers more than one unrelated concern, split it into separate commits
rather than combining them.

## Historical vs. preferred patterns

EnterpriseClosure (backend) and Data Disclosure (frontend) are the current reference implementations — see `.claude/rules/backend.md` and `.claude/rules/frontend.md` for the concrete patterns they illustrate (vertical slice structure, view-model-selector). Export and Upload contain historical/legacy structural patterns still present in the codebase (multi-handler classes, AutoMapper, local orchestration exceptions) — informative for understanding existing code, not the default to replicate for new work. When examples conflict, prefer the most recent and architecturally clean example.
