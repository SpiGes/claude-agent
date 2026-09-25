---
name: pr-review
description: Review a pull request hosted on Azure DevOps or GitHub (optionally with its linked work item or issue), write the review to a Markdown file in /shared, then, after the user has selected the findings to keep, write succinct PR comments to a second file, and publish them only when the user says so. Use when the user asks for a review of a pull request by number or URL (e.g. "fais une review de la PR 43901"). Not for reviewing the local diff, which is covered by the code-review skill.
---

# Pull request review

The review is done in four steps. Each step ends with a message to the user, and the next step
starts only when the user asks for it. Only the gathering of the changes (step 1) and the
publication (step 4) depend on the platform.

## Platform detection

The platform is taken from the pull request URL when one is given, otherwise from the remote of the
repository concerned:

- `https://devops-server.admin.ch/...`: Azure DevOps, through the `devops` MCP server
- `https://github.com/...`: GitHub, through the GitHub CLI (`gh`)

When neither the URL nor the repository makes it clear, the user is asked.

## Step 1 - Review

### Gathering the changes - Azure DevOps

- The pull request is read with `devops_pull_request_get`; when only a work item is given, the pull
  request is found in its links (`devops_work_item_get`), together with the repository ID.
- The changed files are listed with `devops_repository_diffs_commits` when the tool is available.
  Otherwise, the diff is computed from the local clone (see below), between the merge base and
  `lastMergeSourceCommit`.
- The linked work items are read with `devops_work_item_get`.

### Gathering the changes - GitHub

- The pull request is read with
  `gh pr view <n> -R <owner>/<repo> --json number,title,body,url,baseRefName,headRefName,baseRefOid,headRefOid,files,closingIssuesReferences`.
- The diff is read with `gh pr diff <n> -R <owner>/<repo>`, or computed from the local clone (see
  below), between the merge base and `headRefOid`.
- The linked issues (`closingIssuesReferences`) are read with `gh issue view <number>`. A reference
  to an Azure DevOps work item in the title or body (e.g. `AB#157789`) is read with
  `devops_work_item_get`.

### Gathering the changes - both platforms

- When the diff is computed locally, the local clone is the one in the workspace whose remote has
  the source branch (`git ls-remote`). The two commits are fetched, and the working tree and the
  current branch of the local clone are never changed.
- The full diff is saved in the scratchpad directory and read completely, not sampled.
- Every point that is flagged is checked against the complete file at the head of the pull request,
  not only against the diff hunk.

### Two habits that are always applied

- **The fix is linked to the work item or issue.** When the pull request is linked to a bug, a user
  story, or an issue, it is checked that the change actually solves it, going back to the root cause
  when needed. This includes related pull requests in other repositories (found in the links) and
  package versions (e.g. which tag first contains a fix in a sibling service).
- **What was checked is separated from what wasn't.** Anything that wasn't verified (build, tests,
  runtime behaviour, environment settings, call paths) is listed explicitly, and never presented as
  a fact.

### Review file

The review is written to `/shared/review-pr-<id>.md`, in the user's language unless another
language is requested, with this structure:

1. Header: platform, repository, source and target branches, linked work item or issue, reviewed
   commits, date, method
2. Summary: verdict on the fix and on the pull request as a whole
3. Analysis of the fix against the work item or issue (root cause, fix, side effects)
4. Findings, ordered from the most to the least important, each with:
   - a severity: Blocking, Important, Minor, or To be checked
   - the location, as `path:line` at the head of the pull request
   - the problem, the risk, and a concrete suggestion (with a code snippet when it helps)
5. Points checked without a remark (optional)
6. Not checked

For a GitHub pull request, `<id>` is `<repo>-<number>` (e.g. `claude-agent-12`), since GitHub numbers
are only unique within a repository.

### Message to the user

A short recap is given in the terminal: a table of the findings ordered from the most to the least
important (number, severity, location, one-line summary), followed by the main points that weren't
checked. The review file itself isn't repeated.

## Step 2 - Selection

The user selects the findings to keep (e.g. "je retiens 3.1, 3.2 et 3.4"), and may ask for changes
to the review file. No comment is written before this selection. When the review file is
regenerated with the selected findings only, the new numbering is given to the user.

## Step 3 - Comments file

The comments are written to `/shared/pr-<id>-comments.md`, one per selected finding, plus a general
comment when a finding doesn't belong to a specific line.

- Each comment gives its type (general or inline), the file path relative to the repository root,
  and the line number at the head of the pull request. The line is checked (`git show
  <head>:<path>`) to contain the code the comment refers to.
- On GitHub, an inline comment can only be placed on a line that is part of the diff (added line, or
  context line of a changed hunk). This is checked against the diff; a finding on another line is
  moved to the general comment, with its `path:line` written in the text.
- The text of each comment is written in a fenced `markdown` block (four backticks when the comment
  contains a code block), exactly as it will appear in the pull request.
- Comments are written in English.
- Comments are succinct and go to the point: two to four lines, no thanks, no restating of the root
  cause or of the review, one concrete suggestion or question.

The user may then edit the file.

## Step 4 - Publication

Comments are published only when the user explicitly says so (e.g. "publie", "publish").

### Before publishing - both platforms

1. The comments file is read again, since the user may have changed it. Obvious problems (broken
   Markdown, unclosed code block, wrong file path) are reported before anything is published; the
   user's wording is kept as is.
2. If the head commit of the pull request changed since the review, the user is told, and the line
   numbers are checked again before publishing.
3. The user is reminded which account the comments will be published with (see below).

### Publishing - Azure DevOps

- Account: the user's own account (the PAT of the `devops` MCP server).
- Each comment is posted with `devops_pull_request_create_comment`: without `file_path` for a
  general comment, with `file_path` and `line_number` for an inline comment.
- The result is checked with `devops_pull_request_list_threads`, and the thread IDs and locations
  are reported to the user.

### Publishing - GitHub

- Account: the agent's GitHub account (the one of `GH_TOKEN`), not the user's own account.
- The general comment and all inline comments are posted together as a single review, so that only
  one notification is sent:
  `gh api repos/<owner>/<repo>/pulls/<n>/reviews --method POST --input <file.json>`, where the JSON
  file (written to the scratchpad directory) contains `commit_id` (the head commit), `event:
  "COMMENT"`, `body` (the general comment), and `comments` (one entry per inline comment, with
  `path`, `line`, `side: "RIGHT"`, and `body`).
- The event is always `COMMENT`: a pull request is never approved, and changes are never requested,
  unless the user explicitly asks for it.
- The result is checked with `gh api repos/<owner>/<repo>/pulls/<n>/comments` and
  `gh api repos/<owner>/<repo>/pulls/<n>/reviews`, and the review ID, the comment IDs, and their
  locations are reported to the user.

Existing comments, threads, and reviews of other people are never changed, resolved, or deleted.