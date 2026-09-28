# Phase 3 - Breakdown

Goal: the work split into tasks under the right user stories, created in Azure DevOps after the
user's validation.

## Steps

1. A breakdown draft is written to `/shared/<topic>-task-breakdown.md`:
   - the user story that receives the work, with the reasons when several are possible. Common
     development needed by several user stories may be included in one existing user story
   - the new user stories needed, if any, with title and description
   - the tasks, in order of realisation, each with its design chapters, its content, and its
     dependencies
2. The user validates the draft, or asks for changes. A new user story is created only when the
   user accepts it.
3. The work items are created (`devops_work_item_create`) under their user story.

## Order of the user stories (feature scope)

When the work covers a feature, the breakdown draft starts with the order of its user stories:

- a comparison table of the user stories: size, blocking open points, risk
- the dependencies between user stories, and the common development: it's included in an existing
  user story, or in a new one, which is created only when the user accepts it
- the user stories that are ready, and those blocked by open points
- the proposed order, with the reasons

The user validates the order before the tasks of the first user story are written.

## Content of the tasks

- Title and description in the language set for published text (see the user's `CLAUDE.user.md`).
- The description gives the expected behaviour, the components concerned, a "Depends on:" line, and
  the design chapters ("Design: <document>, chapters ...").
- Unit tests are part of each development task. There is no dedicated unit test task.

## Standard set of tasks

- development tasks, assigned to the user
- a developer test on an environment with real data (e.g. REF), when the change can't be fully
  tested locally, assigned to the user
- "Validation tests", unassigned: validation against the acceptance criteria and the functional
  decisions
- "Review implementation", unassigned: review of the pull request against the design document

## End of the phase

The created work items are listed (ID, title, assignee). The user closes the phase.