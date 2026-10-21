---
name: work-item-cleanup
description: Clean up at the end of a work item (user story, issue, bug) - check that its pull requests are merged, close its tasks, merge its specs worktree into master, delete its files in /shared, remove its worktrees and branches, and update its memory note. Use when the user says that the work on a work item is finished, or that its pull request is merged, and asks for the clean-up (e.g. "clean up after issue 158985", "the PR is merged, clean up", "la PR est close, tu peux faire le ménage", "nettoie l'US 156044"), or only wants to see what the clean-up would do (dry mode, e.g. "work-item-cleanup 158497 dry", "dry run of the clean-up of US 156044", "nettoyage à blanc de l'issue 158497"). Not for the delivery itself, which is covered by the user-story-delivery skill.
---

# Work item clean-up

The clean-up is done in two steps: an inventory, shown to the user, then the actions the user agrees
with. Nothing is closed, pushed, or deleted before this agreement.

## Dry mode

The dry mode is used when the arguments hold `dry` or `--dry-run`, or when the user asks for a dry
run ("à blanc"). The inventory is done as usual, then the skill shows what would be done and stops:

- The inventory table (step 2) gets a column "would run", with the exact call or command of each
  action: e.g. `devops_work_item_update 159383 System.State=Closed`,
  `git -C /workspace/specs/branch merge --ff-only issue/RC_158497_ks_upload_enterprise_only`,
  `rm -r /shared/158497-ks-upload/`, `git worktree remove /workspace/backend/RC_158497`,
  `git branch -D issue/RC_158497_ks_upload_enterprise_only`
- The items that would be kept are listed with the reason
- No question is asked, and nothing is changed: no work item update, no rebase, merge, or push, no
  deletion, no memory change. Only read operations are run (`devops_*_get`, `gh pr view`,
  `git status`, `git log`, `git worktree list`, `ls`), plus `git fetch --prune`, which only updates
  the local copies of the remote branches
- The report ends with the sentence to use to run the real clean-up (e.g. "work-item-cleanup 158497")

A later request without the dry mode runs the inventory again, since the state may have changed.

## Rules

- **Only what belongs to the work item.** An item is acted on only when it belongs to the work item
  by one of the rules of "Ownership" below. An item that may belong to it, but doesn't match these
  rules, is listed as "kept, to check" and isn't changed. A text match (e.g. a class name found in a
  file, a number found somewhere in a name) is never a proof.
- **Facts from the sources.** The memory note of the work item gives hints (repositories, worktrees,
  pull requests, files); the state is always checked in Azure DevOps (or GitHub) and in git.
- **Not changed by the skill:** the state of the work item itself and of its parent (decided by the
  team), and the gitops `main` (merged on the user's request during the work, pushed by the user).
- **No force.** A worktree with uncommitted changes, or a branch with commits that aren't on the
  remote, is kept and reported. No `--force`, no `reset --hard`.

## Specs workflow

The specs of a work item are written in a worktree of the specs repository, on the branch of the
work item, never directly on the local `master`. The worktree stays as long as the work item isn't
finished. At the end, the branch is merged into `master` and pushed, then the worktree is removed.

So the local `master` of `/workspace/specs/branch` must never be ahead of `origin/master`. If it is,
the clean-up stops before any action, and the commits are reported with the work item each one
belongs to (task number in the message). The user decides what to do.

## Gitops workflow

The gitops changes of a work item are prepared in a worktree of the gitops repository
(`/workspace/gitops/RC_<id>`), on the branch of the work item. The branch is merged into the local
`main` during the work, when the user asks for it (e.g. to test on an environment), and the user
pushes `main` (see the gitops rules of `/workspace/CLAUDE.md`). The skill doesn't merge or push
anything in gitops: it only removes the worktree once all its commits are on `origin/main`.

A commit is on `origin/main` when `git cherry origin/main <branch>` marks it with `-` (same change,
even when the hash differs after a rebase). A commit marked with `+` isn't deployed yet.

## Ownership

The ownership of a branch or worktree is decided by exact rules, never by a substring search.

- **Branch of a pull request.** The source branch of each pull request of the work item
  (`sourceRefName` of `devops_pull_request_get`, `headRefName` of `gh pr view --json headRefName`)
  belongs to the work item. This rule comes first, since it doesn't depend on a naming convention.
- **Branch name (Azure DevOps).** A local branch belongs to the work item when its full name matches
  `^(feature|issue|bug)/RC_<id>_` (e.g. `issue/RC_158497_ks_upload_enterprise_only`). This covers the
  branches without a pull request, like the specs branch.
- **Branch name (GitHub).** A local branch belongs to the issue when its full name matches
  `^feature/<n>-` (e.g. `feature/18-work-item-cleanup-skill` for issue 18, but not
  `feature/180-...` or `feature/RC_156118_...`).
- **Worktree.** A worktree belongs to the work item when its branch does. The worktrees and their
  branches are read with `git worktree list --porcelain` (lines `worktree <path>` and
  `branch refs/heads/<name>`), not guessed from the folder name. The first entry is the main
  checkout (`branch`); it's never removed.
- **Task number.** A commit belongs to the work item when its message starts with `[#<task>]`, where
  `<task>` is one of the child tasks of the work item (`[#<n>]` for a GitHub issue).
- **/shared.** The folder `/shared/<id>-<topic>/` belongs to the work item when its name starts with
  `<id>-` (exact id, followed by a hyphen).

The branch names are listed with `git for-each-ref --format='%(refname:short)' refs/heads` and
filtered with `grep -E` on the anchored patterns above.

## 1. Inventory

1. **Work item.** The work item is read with `devops_work_item_get`, with its child tasks and their
   states. The memory note of the work item is read, when there is one (see `MEMORY.md`).
2. **Pull requests.** The pull requests linked to the work item or its tasks, and the ones named in
   the memory note, are read with `devops_pull_request_get` (`gh pr view` on GitHub), in every
   repository concerned (backend, frontend, other). The state and the source branch of each one are
   noted: completed (merged), active (open), or abandoned (closed).
3. **Specs.** In `/workspace/specs/branch`, `git fetch --prune origin` is run, then:
   - the local `master` is checked: no uncommitted change, and no commit ahead of `origin/master`
     (see "Specs workflow"); otherwise the clean-up stops
   - the specs worktree of the work item is looked for (see "Ownership"). Its commits that aren't on
     `origin/master` are listed; each one must belong to the work item, otherwise the clean-up stops
     and the commits are reported
   - a specs worktree with uncommitted changes is kept and reported
4. **Gitops.** In `/workspace/gitops/branch`, after `git fetch --prune origin`:
   - the gitops worktree of the work item is looked for (see "Ownership"), and its commits are
     checked with `git cherry origin/main <branch>` (see "Gitops workflow")
   - the commits of the work item on the local `main` that aren't on `origin/main` are listed, for
     the user to push
   - commits of the work item that are neither merged nor pushed keep the gitops worktree; they're
     reported, with the choice to merge them (on the user's request) or to drop them
5. **/shared.** The folder `/shared/<id>-<topic>/` is listed with its files. Files outside this
   convention (older work, files named in the memory note) are listed as "kept, to check", with the
   reason they are believed to belong to the work item.
6. **Worktrees and branches.** In every repository under `/workspace/*/branch`, after
   `git fetch --prune origin`, the worktrees and local branches of the work item are found (see
   "Ownership"). For each one: uncommitted changes (`git status --porcelain`), commits that aren't
   on the remote branch, and whether the remote branch still exists.
7. **Open consequences.** The open points of the memory note that concern other work items (e.g. a
   commit to drop from another pull request) are noted for the report.

## 2. Inventory table

The inventory is shown as one table per kind of item (pull requests, tasks, specs, gitops, /shared,
worktrees and branches), with the columns item, state, and proposed action. Stops and blocking
points come first:

- Stop: local commits on the specs `master`, or commits of another work item on the specs branch of
  the work item. No action is proposed until the user has decided.
- A pull request that isn't completed: the items of its repository (worktree, branch) wait. The
  other items can be cleaned up if the user agrees.
- A worktree with uncommitted changes, or a branch with unpushed commits (except the specs branch,
  which is merged in step 3).
- A gitops worktree with commits that aren't on `origin/main`: it's kept until the user has merged
  and pushed them, or decided to drop them.

The user agrees with the whole table or with a part of it.

## 3. Actions

Done in this order, only for the items the user agreed with:

1. **Tasks.** Each open task of the work item (development, validation tests, review, other) is
   closed with `devops_work_item_update` (`System.State` = `Closed`). Tasks already closed aren't
   changed.
2. **Specs.** The specs branch of the work item is merged into `master` and pushed:
   - `git fetch origin`, then in `/workspace/specs/branch`: `git merge --ff-only origin/master`
   - in the specs worktree: `git rebase origin/master`. On a conflict, `git rebase --abort` is run,
     and the merge stops and is reported; the conflict isn't solved by the skill
   - in `/workspace/specs/branch`: `git merge --ff-only <branch>`, then `git push origin master`
   - the specs worktree and its branch are then removed like the others (action 4)
3. **/shared.** The agreed files and folders are deleted, each named explicitly; no wildcard outside
   the folder of the work item.
4. **Worktrees and branches.** Each worktree is removed with `git worktree remove <path>`, without
   `--force`. Its local branch is then deleted with `git branch -D`: the pull requests are squash
   merged, so `git branch -d` doesn't see the branch as merged; the completed pull request (for the
   specs branch, the push of action 2; for the gitops branch, `git cherry` without `+`) is the proof.
   A remote branch that still exists is deleted only when the user asked for it.
5. **Memory.** The memory note of the work item is marked as done, with the date and what is left
   (e.g. a gitops commit to push, a consequence on another work item); its line in `MEMORY.md` is
   updated.

## 4. Report

A short recap is given: what was done, what was kept and why, and what is left to the user (gitops
push, consequences on other work items, state of the work item itself). A spoken notification is
sent at the end.

## GitHub

For an issue of a GitHub repository, there are no tasks and no specs repository: actions 1 and 2 are
skipped. The pull requests are read with `gh pr list --state all --search "<n> in:title"` and
`gh pr view <number> --json state,headRefName,body`; a pull request belongs to the issue when its
title starts with `[#<n>] -`. The issue is closed by its pull request (`Closes #<n>`); this is only
checked, with `gh issue view <n> --json state`.
