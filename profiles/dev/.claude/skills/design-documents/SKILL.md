---
name: design-documents
description: Write or update a design document, following a defined methodology (deferred vs open points, chapter structure, text density). Use when the user explicitly asks for a design document, a technical specification, or a similar formal write-up.
---

# Design documents — general methodology

- Use a canvas/Artifact; write only one authoritative version in it, updated through targeted edits rather than full regeneration.
- Style: simple technical English with a maximum B2 level, neutral and human-like, passive form preferred, class/method/enum names in italics, ASCII characters as much as possible, no trailing punctuation in bulleted or numbered lists.
- A table of contents must not be written or maintained by hand, because it becomes wrong as soon as a chapter is added or renamed — it is expected to come from the publishing platform or a generator at delivery time.
- Chapters must be numbered (e.g. `## 4. Scope`), because the numbers are used to navigate and to refer to a chapter during a review. Sub-sections aren't numbered. When a chapter is inserted or removed, all the following chapters must be renumbered.
- A chapter that carries no content for a given feature may be omitted, except `Purpose` and the design point chapters.

## Deferred design points vs. open points

A design document doesn't have to match the implementation one to one. Two different chapters
are used for what isn't covered yet, and they must not be mixed:

- **Deferred design points** — decided, but scheduled for later, because problems appear if it's never done (e.g. unbounded data growth, a capability that degrades silently, a missing guard). Describe it precisely enough to be implemented later, together with what the first version does instead.
- **Open points** — may never be resolved without consequence (e.g. a business question still discussed, an option kept for a possible future need). Nothing degrades if the answer never comes.

Test to apply: what happens if the point is never treated? If something breaks or grows out
of control, it's deferred. If nothing happens, it's open.

## Density of the text

Write to the point. A design document is a technical document, not an essay. Prefer short
sentences and factual statements over explanatory or persuasive prose. Say a thing once, and
drop the connectives, the reformulations, and the sentences that only announce what follows.
This applies to every chapter, and most of all to `Purpose` and `Scope`.

It doesn't apply to the reason and the consequence of a decision — those two carry the value
of the document and stay complete, even when they cost more words than the decision itself.

## Design point chapters

Each design point gets its own chapter, named after the question it answers rather than after
a work item number. Chapters must be ordered by dependency (a decision that conditions other
decisions comes first), not by work item number.

Each chapter should contain: the problem to solve; the options considered, when there was
more than one; the retained decision; the reason for that decision and the consequence
accepted with it.

Short chapters are written as prose, without sub-headings. When options are numerous or
weighty, split into one sub-heading per option (`Choice 1 - ...` to `Choice n - ...`), followed
by a `Choice and rationale` sub-heading. Each option sub-heading describes the mechanism, what
it brings, and what it costs, so a reader can weigh the options before reading the decision.

A decision must be stated in affirmative form. When a point can't be decided yet, or is decided
but not implemented yet, it belongs to `Open points` or `Deferred design points`, not to a
conditional sentence inside a design point chapter — that's what keeps design point chapters
affirmative.

If important information is missing and prevents a precise design explanation, ask targeted
clarifying questions.

# SpiGes specs — project instructions

SpiGes is a service of the SIS microservice infrastructure: Angular/ngrx frontend, ASP.NET Core backend, PostgreSQL + Oracle persistence. This repository (SIS-SpiGes-Specs) versions the design specs and related documents for both the backend (SIS-SpiGes) and the frontend (SIS-SpiGes-UI).

## Reference material (read on demand, not preloaded)

- `.claude/docs/SPIGES_SOLUTION_CONTEXT.md` — architecture and business domain hierarchy (`Unit` / `BurGesv` / `EntId` / `UnitDescriptor` / `GroupType` / wave year). This knowledge is not derivable from the code alone; the same file is also kept in the backend and frontend repositories, since it applies to all three.
- `.claude/docs/SPIGES_REQUEST_TEMPLATE.md` — preferred format for a feature/analysis/review request, when the user wants to write one explicitly (optional; a short, well-scoped request is usually enough). Describes how to phrase a request, not the code itself, so it is not derivable from the code; the same file is also kept in the backend and frontend repositories, since a request can span more than one of them.

## Default technical scope

- Documentation / static site: Hugo-Extended, Docsy.

## Diagrams — SpiGes-specific

- Use current Mermaid syntax without restriction, since the code is only rendered by the claude.ai preview and by mermaid.live, which both follow the latest version.
- A diagram that uses subgraphs should start with the directive
  `%%{init: {'themeVariables': {'clusterBkg': 'transparent', 'clusterBorder': '#9e9e9e'}}}%%`,
  which removes the default background of the blocks and keeps only their border. No theme is
  fixed, so the rendering still follows the light or dark mode of the viewer.
- A Markdown image reference to the exported diagram must be added right after the code
  block, so the diagram also appears where Mermaid isn't rendered (e.g. Confluence). The
  image is placed in a folder named after the document, with a file name of the form
  `diagram-01-short-name.png`, and the spaces of the path are written as `%20` in the
  reference.
- For UML-style diagrams describing backend types: do not display `CancellationToken`; do
  not display `Task` (use the underlying type, or nothing if void); omit member/variable
  types unless omitting them would create ambiguity.

## Design documents — default structure

Unless another structure is explicitly requested, organize a design document as follows:
1. Versions
2. References
3. Purpose
4. Scope
5. Solution overview
6. one chapter per design point to resolve
7. Changes to the existing system
8. Configuration
9. Deferred design points
10. Open points
11. Static diagrams
12. Dynamic diagrams

Expected content of those chapters:
- `Versions` is a table with the version, the date, and a short description of the change, tracking the releases of the document and not the iterations of its writing, so no line is added while a version is still being elaborated.
- `References` lists the work items, the related design documents, and the external sources.
- `Purpose` explains what the feature does and why it is needed, without describing the solution.
- `Scope` states what is covered and, above all, what is left out.
- `Solution overview` describes the retained solution as a whole, in about half a page, so that the following chapters can be read in any order.
- the design point chapters carry the substance of the document.
- `Changes to the existing system` lists the existing code and behavior that are modified, with the associated risk and the way back.
- `Configuration` describes all new parameters, with their meaning and their default value.
- `Deferred design points` lists the points that are decided but not implemented in the first version.
- `Open points` lists the questions that are still open, which are mostly business decisions.
- the two diagram chapters are left empty, because diagrams are generated separately.

This structure follows the same deferred-vs-open distinction and general design-document
methodology described in the generic `design-documents` skill at the agent level; only the
chapter list above is SpiGes-specific.