---
name: user-story-delivery
description: Deliver an Azure DevOps user story in four validated phases - clarification of the functional specification, design document, breakdown into tasks (and user stories when needed), and development with unit and integration tests up to a pull request. Use when the user asks to work on a user story or a feature end to end (e.g. "I start working on user story 156828", "on commence l'US 156828", "I start working on feature 137166", "on travaille sur la feature 137166"), or to run one of these phases (e.g. "breaks down the user story in tasks", "découpe l'US en tâches", "start the first task", "commence la première tâche"). Not for a review of someone else's pull request, which is covered by the pr-review skill.
---

# User story delivery

A user story is delivered in four phases. Each phase ends with a validation by the user, and the
next phase starts only when the user asks for it. The work may start at any phase when the previous
ones are already done (e.g. the development of existing tasks).

| Phase | Result | File |
| --- | --- | --- |
| 1 - Functional specification | Specification clear enough to design | [phase-1-functional-specification.md](phase-1-functional-specification.md) |
| 2 - Design | Design document validated by the user | [phase-2-design.md](phase-2-design.md) |
| 3 - Breakdown | Tasks (and user stories) created in Azure DevOps | [phase-3-breakdown.md](phase-3-breakdown.md) |
| 4 - Development | Commits per task, pull request | [phase-4-development.md](phase-4-development.md) |

The file of the current phase is read before the phase starts.

## Precedence

Running this skill is an explicit request for what its phases produce. It takes precedence over the
project defaults "Do not generate unit tests" and "Do not generate a design document". The other
project defaults still apply (e.g. no diagram outside the design document).

## Feature scope

The work may be asked at the level of a feature, above its user stories. Phases 1 to 3 then cover
the whole feature; phase 4 stays per user story (one branch, one pull request, tasks under a single
user story).

- **Starting point.** The feature is read, together with all its user stories, their comments, and
  their linked work items.
- **Phase 1.** A single questions file is written for the feature. Each question gives the user
  stories it concerns.
- **Phase 2.** A single design document covers the feature. Each choice gives the user stories that
  apply it.
- **Phase 3.** The user stories are ordered before any task is created (see
  [phase-3-breakdown.md](phase-3-breakdown.md), "Order of the user stories"). The tasks may then be
  created user story by user story, when each one starts.
- **Phase 4.** One user story at a time, in the validated order.

## Rules for all phases

- **Starting point.** The user story is read with `devops_work_item_get`, together with **all its
  comments** (`devops_work_item_comment_list`), its parent feature, its children, and its linked
  user stories. A decision given in a comment is binding, even when the description says otherwise.
- **Validation.** A phase is closed only by the user. A proposal isn't applied on the assumption
  that it will be accepted.
- **Drafts in /shared.** Only temporary documents that aren't versioned are written to
  `/shared/<topic>.md`, in the user's language: questions to be passed on to the business, and the
  breakdown draft before the work items are created. The functional specification and the design
  document are edited directly in the specs repository. The terminal only gets a short recap.
- **Inconsistencies.** Before an inconsistency is flagged in a work item, the related work items
  (sibling tasks, linked user stories) are read, since the point is often covered elsewhere.
- **Feedback loop.** When a later phase shows that an earlier result is wrong or incomplete (e.g. a
  design choice that doesn't match the code), the deviation is reported with a proposal. After the
  user's validation, the earlier document (specification, design, task description) is updated.
- **Commits of documents.** The functional specification and the design document are committed only
  when the user asks for it.
- **Resuming.** At the start of a session, the state is taken from Azure DevOps (task states, pull
  request), the specs repository, and the branches, not from memory.