---
name: design-documents
description: Write or update a design document, following a defined methodology (deferred vs open points, chapter structure, text density). Use when the user explicitly asks for a design document, a technical specification, or a similar formal write-up.
---

# Design documents

## Document location and files

- A design document is a Markdown file versioned in the specs repository, updated through
  targeted edits rather than full regeneration.
- Each document has its own folder, grouped by area (e.g. `design/export/export-process/itark-process/`),
  and is named `spec.md`. A document split into sub-documents keeps one `spec.md` per folder
  (e.g. `design/updates-via-email/spec.md`, `design/updates-via-email/events/spec.md`).
- The images of a document are stored in a `resources/` folder next to its `spec.md` (see
  "Image files in design documents" in the `diagrams` skill).

## General rules

- Style: simple technical English with a maximum B2 level, neutral and human-like, passive form
  preferred, class/method/enum names in italics, ASCII characters as much as possible, no
  trailing punctuation in bulleted or numbered lists.
- The document starts with a single top-level title (`# <title>`); chapters are level-2 headings.
- A table of contents must not be written or maintained by hand, because it becomes wrong as soon
  as a chapter is added or renamed — it is expected to come from the publishing platform or a
  generator at delivery time.
- Chapters must be numbered (e.g. `## 4. Scope`), because the numbers are used to navigate and to
  refer to a chapter during a review. Sub-sections aren't numbered. When a chapter is inserted or
  removed, all the following chapters are renumbered, together with every reference to a chapter
  number in the text (e.g. "described in chapter 5").
- A chapter that carries no content for a given feature may be omitted, except `Versions`,
  `Purpose`, and the design point chapters.

## Default structure

Unless another structure is explicitly requested, a design document is organized as follows:
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
- `Versions` is a table with the version, the date, and a short description of the change,
  tracking the releases of the document and not the iterations of its writing, so no line is
  added while a version is still being elaborated. Its columns are `Version | Date | Details`,
  with dates written `YYYY.MM.DD`. Every document has its own `Versions` chapter, sub-documents
  included; a parent document's table describes the changes of the parent document only, not
  those of its sub-documents.
- `References` lists the work items, the related design documents, and the external sources.
- `Purpose` explains what the feature does and why it is needed, without describing the solution.
- `Scope` states what is covered and, above all, what is left out.
- `Solution overview` describes the retained solution as a whole, in about half a page, so that
  the following chapters can be read in any order.
- the design point chapters carry the substance of the document.
- `Changes to the existing system` lists the existing code and behavior that are modified, with
  the associated risk and the way back.
- `Configuration` describes all new parameters, with their meaning and their default value.
- `Deferred design points` lists the points that are decided but not implemented in the first
  version.
- `Open points` lists the questions that are still open, which are mostly business decisions.
- the two diagram chapters carry the diagrams of the design, written and rendered to their
  images following the `diagrams` skill.

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

## Diagrams

Diagrams follow the `diagrams` skill, including the rendering of each diagram to the image
referenced in the document.