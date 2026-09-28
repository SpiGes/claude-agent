# Phase 4 - Development

Goal: each development task implemented, tested, and committed, up to a pull request that the user
can review.

## Branch and worktree

- The branch is named `feature/RC_<user story>_<short_description>` and is based on the up-to-date
  target branch (usually `origin/master`).
- When a worktree is used, it's created next to the main checkout (e.g. `/workspace/backend/RC_<user
  story>`). Its `.git` file is then rewritten with a relative path (e.g.
  `gitdir: ../branch/.git/worktrees/RC_<user story>`), otherwise the user's editor can't find the
  repository. The branch can't be selected in the main checkout while the worktree exists: the user
  opens the worktree folder instead.

## For each task

1. The task and its sibling tasks are read, together with the design chapters it refers to.
2. The code is written following the project rules (e.g. `csharp-coding-conventions`,
   `backend.md`), and the line endings of each file are kept (CRLF in the backend).
3. Unit tests are written following `testing.md`. Integration tests are added when the project has
   a test for the same flow (e.g. real test files under `Resources/`). Existing tests broken by the
   change are adapted, and the reason is given.
4. The full test project of the module is run, not only the new tests (backend: always with
   `-p:NuGetAudit=false`).
5. A recap is given: what was done, deviations from the design (with a proposal), risks, and what
   isn't covered by the tests. The user is asked before committing.
6. One commit per task, following the commit conventions. When a commit turns out to contain the
   work of another task, it's split before push, after the user's agreement.

## Testing honestly

- What is tested with real data or real files is separated from what is tested with generated data
  or mocks.
- A change of behaviour for existing flows (even a minor one, e.g. input that is now rejected) is
  reported, and written in the pull request.

## Pull request

1. The own work is reviewed once more on the full diff against the target branch.
2. The branch is pushed and the pull request is created (`devops_pull_request_write`), linked to the
   user story and to the development tasks, with the chapters:
   - Context: the need, and the design document
   - Main Changes: grouped by task, including compatibility and what isn't covered yet
3. Files changed by the build but not part of the work (e.g. a regenerated API client) aren't
   committed; they're reported to the user.

## End of the phase

The pull request link is given. On the user's request, the development tasks are closed. The
remaining tasks (developer test, validation tests, review) stay open.