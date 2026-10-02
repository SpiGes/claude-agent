# Phase 4 - Development

Goal: each development task implemented, tested, and committed, up to a pull request that the user
can review.

## Branch and worktree

- The branch is named `feature/RC_<user story>_<short_description>` and is based on the up-to-date
  target branch (usually `origin/master`).
- When a worktree is used, it's created next to the main checkout (e.g. `/workspace/backend/RC_<user
  story>`). Its `.git` file must hold a relative path (e.g.
  `gitdir: ../branch/.git/worktrees/RC_<user story>`), otherwise the user's editor can't find the
  repository. The image sets `worktree.useRelativePaths=true` (git 2.48 or higher), so this is only
  checked after `git worktree add`; the file is rewritten by hand only when the path is absolute.
  The branch can't be selected in the main checkout while the worktree exists: the user opens the
  worktree folder instead.

## For each task

1. The task and its sibling tasks are read, together with the design chapters it refers to.
2. The code is written following the project rules (e.g. `csharp-coding-conventions`,
   `backend.md`), and the line endings of each file are kept (CRLF in the backend).
3. Unit tests are written following `testing.md`. Integration tests are added when the project has
   a test for the same flow (e.g. real test files under `Resources/`). Existing tests broken by the
   change are adapted, and the reason is given.
4. When the task changes the gitops repository, the compatibility with every environment is checked
   before the commit:
   - the `imageTag` of each `values-<env>.yaml` shows which build runs where (master release, pull
     request build, feature or epic branch)
   - a setting that refers to types or code of an epic (e.g. new enum keys in a mapping) is treated as
     breaking for the environments that don't run that epic, even when master seems to know these
     types (they may be a leftover of a bad merge), and is restricted to the environments running the
     epic
   - the chart is rendered with `helm template` for each environment and compared with `origin/main`:
     only the intended environments change
5. The full test project of the module is run, not only the new tests (backend: always with
   `-p:NuGetAudit=false`). In the backend, code coverage is collected in the same run
   (`--collect:"XPlat Code Coverage"`), then summarized with `reportgenerator`
   (`-reporttypes:TextSummary`, output in the scratchpad).
6. A recap is given: what was done, deviations from the design (with a proposal), risks, what
   isn't covered by the tests, and, in the backend, the coverage of the classes changed by the task
   (line and branch rates, per class). No target rate is defined: the rate is reported, not judged,
   and the user decides whether more tests are needed. The coverage is given in the recap only, not
   in the pull request. The user is asked before committing.
7. One commit per task, following the commit conventions. When a commit turns out to contain the
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
   - Compatibility: what changes per environment (gitops), deployment order between the backend and
     the gitops change, and the settings restricted to some environments
3. Files changed by the build but not part of the work (e.g. a regenerated API client) aren't
   committed; they're reported to the user.

## Review of the pull request

1. The review comments are read with `devops_pull_request_list_threads`, and each one is discussed with
   the user before any change.
2. The fixes are committed under the task they belong to. A change that goes beyond the task gets its
   own task under the user story, created after the user's agreement.
3. Each comment is answered, in english:
   - "Done" or "Fixed", depending on the case, with a short explanation only when the change is complex
   - when the comment isn't addressed, the reason
   - no commit is mentioned (hash or message), since the hashes change with each rebase
4. The thread status is set accordingly: Fixed for an applied change, Pending for a change waiting for
   a decision, Won't fix for a comment that isn't addressed.
5. The branch is pushed after the fixes; the replies are posted once the push is done.

## End of the phase

The pull request link is given. On the user's request, the development tasks are closed. The
remaining tasks (developer test, validation tests, review) stay open.
