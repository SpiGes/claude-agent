---
name: work-item-cleanup
description: Clean up at the end of a work item (user story, issue, bug) - check that its pull requests are merged, close its tasks, push its specs commits, delete its files in /shared, remove its worktrees and branches, and update its memory note. Use when the user says that the work on a work item is finished, or that its pull request is merged, and asks for the clean-up (e.g. "clean up after issue 158985", "the PR is merged, clean up", "la PR est close, tu peux faire le ménage", "nettoie l'US 156044"), or only wants to see what the clean-up would do (dry mode, e.g. "work-item-cleanup 158497 dry", "dry run of the clean-up of US 156044", "nettoyage à blanc de l'issue 158497"). Not for the delivery itself, which is covered by the user-story-delivery skill.
---

# Work item clean-up

The clean-up is done in two steps: an inventory, shown to the user, then the actions the user agrees
with. Nothing is closed, pushed, or deleted before this agreement.

## Dry mode

The dry mode is used when the arguments hold `dry` or `--dry-run`, or when the user asks for a dry
run ("à blanc"). The inventory is done as usual, then the skill shows what would be done and stops:

- The inventory table (step 2) gets a column "would run", with the exact call or command of each
  action: e.g. `devops_work_item_update 159383 System.State=Closed`,
  `git -C /workspace/specs/branch push origin master`, `rm -r /shared/158497-ks-upload/`,
  `git worktree remove /workspace/backend/RC_158497`, `git branch -D issue/RC_158497_...`
- The items that would be kept are listed with the reason
- No question is asked, and no action is done: no work item update, no push, no deletion, no memory
  change. Only read operations are run (`devops_*_get`, `git fetch`, `git status`, `git log`, `ls`)
- The report ends with the sentence to use to run the real clean-up (e.g. "work-item-cleanup 158497")

A later request without the dry mode runs the inventory again, since the state may have changed.

## Rules

- **Only what belongs to the work item.** An item is acted on only when it clearly belongs to the
  work item (task number in a commit message, work item id in a branch or folder name, link in Azure
  DevOps). An item that may belong to it, but isn't proven to, is listed as "kept, to check". A text
  match alone (e.g. a class name found in a file) isn't a proof.
- **Facts from the sources.** The memory note of the work item gives hints (repositories, worktrees,
  pull requests, files); the state is always checked in Azure DevOps and in git.
- **Not changed by the skill:** the state of the work item itself and of its parent (decided by the
  team), and the gitops repository (the user pushes it).
- **No force.** A worktree with uncommitted changes, or a branch with commits that aren't on the
  remote, is kept and reported.

## 1. Inventory

1. **Work item.** The work item is read with `devops_work_item_get`, with its child tasks and their
   states. The memory note of the work item is read, when there is one (see `MEMORY.md`).
2. **Pull requests.** The pull requests linked to the work item or its tasks, and the ones named in
   the memory note, are read with `devops_pull_request_get`, in every repository concerned (backend,
   frontend, other). The state of each one is noted: completed, active, or abandoned.
3. **Specs.** In `/workspace/specs/branch`, `origin` is fetched and the local commits that aren't on
   `origin/master` are listed. The commits of the work item are the ones whose message holds the
   number of one of its tasks. The other commits belong to other work items and aren't pushed. When
   they come before the commits of the work item, a push of the work item's commits alone isn't
   possible: this is reported, and nothing is reordered without the user's agreement.
4. **Gitops.** In `/workspace/gitops/branch`, the local commits that aren't on `origin/main` and hold
   a task number of the work item are listed, for the user to push.
5. **/shared.** The folder `/shared/<work item id>-<topic>/` is listed with its files. Files outside
   this convention (older work, files named in the memory note) are listed with the reason they are
   believed to belong to the work item.
6. **Worktrees and branches.** In every repository under `/workspace/*/branch`, `git worktree list`
   and the local branches are read. A worktree or branch belongs to the work item when its name holds
   the work item id (e.g. `RC_158985`, `issue/RC_158985_...`, `feature/RC_156044_...`). For each one:
   uncommitted changes (`git status --porcelain`), commits not on the remote branch, and whether the
   remote branch still exists (after `git fetch --prune`). The main checkout (`branch`) is never
   removed.
7. **Open consequences.** The open points of the memory note that concern other work items (e.g. a
   commit to drop from another pull request) are noted for the report.

## 2. Inventory table

The inventory is shown as one table per kind of item (pull requests, tasks, specs, gitops, /shared,
worktrees and branches), with the columns item, state, and proposed action. Blocking points come
first:

- A pull request that isn't completed: the items of its repository (worktree, branch) wait. The
  other items can be cleaned up if the user agrees.
- A worktree with uncommitted changes, or a branch with unpushed commits.
- Specs commits of other work items placed before the ones of the work item.

The user agrees with the whole table or with a part of it.

## 3. Actions

Done in this order, only for the items the user agreed with:

1. **Tasks.** Each open task of the work item (development, validation tests, review, other) is
   closed with `devops_work_item_update` (`System.State` = `Closed`). Tasks already closed aren't
   changed.
2. **Specs.** The commits of the work item are pushed to `origin/master` from the specs repository.
3. **/shared.** The agreed files and folders are deleted, each named explicitly; no wildcard outside
   the folder of the work item.
4. **Worktrees and branches.** Each worktree is removed with `git worktree remove <path>`, without
   `--force`. Its local branch is then deleted with `git branch -D`: the pull requests are squash
   merged, so `git branch -d` doesn't see the branch as merged; the completed pull request is the
   proof. A remote branch that still exists is deleted only when the user asked for it.
5. **Memory.** The memory note of the work item is marked as done, with the date and what is left
   (e.g. a gitops commit to push, a consequence on another work item); its line in `MEMORY.md` is
   updated.

## 4. Report

A short recap is given: what was done, what was kept and why, and what is left to the user (gitops
push, consequences on other work items, state of the work item itself). A spoken notification is
sent at the end.

## GitHub

For an issue of a GitHub repository, there are no tasks and no specs repository: steps 1 and 2 of
the actions are skipped. The issue is closed by its pull request (`Closes #<n>`); it's only checked.
The branch of the pull request is named after the issue number (e.g. `feature/18-...`).
