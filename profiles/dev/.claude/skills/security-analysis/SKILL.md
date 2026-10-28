---
name: security-analysis
description: Security analysis of a repository, a module, or the dependencies of a SIS/SpiGes project - known vulnerabilities of the dependencies (transitive ones included), secrets in the files and the git history, static analysis of the code against the secure coding rules, and misconfigurations of the Helm charts and Dockerfiles - with a triaged report in /shared. Use when the user asks for a security analysis, a security audit, a vulnerability or dependency check, or a secret scan (e.g. "security analysis of the backend", "analyse de sécurité du module Export", "check the vulnerable packages of the frontend", "y a-t-il des secrets dans l'historique ?"). Not for the pending changes of a branch, covered by the built-in security-review skill, nor for a pull request, covered by the pr-review skill.
---

# Security analysis

The analysis is done in three steps. Each step ends with a message to the user, and the next step
starts only when the user asks for it. It's an analysis: no code, package, or configuration is changed,
and nothing is published.

## Step 1 - Scope

The scope is taken from the request, and stated before the tools run:

- the repository or repositories, and the commit analysed (`git rev-parse HEAD`, branch)
- the part of the code (whole repository, module, project), and whether test projects are included
- the checks: dependencies, secrets, code, configuration (all four by default)

When the request leaves the scope unclear in a way that changes the work (e.g. "the backend" with or
without its git history, a single module or the solution), the user is asked; otherwise the default is
stated and the analysis goes on.

## Step 2 - Analysis and report

All raw outputs are written to the scratchpad directory, in JSON when the tool supports it, and read
completely. The tool versions and the date of the vulnerability data are written down for the report.

### Dependencies

- .NET: `dotnet restore <solution> -p:NuGetAudit=false`, then
  `dotnet list <solution> package --vulnerable --include-transitive --format json`. The output gives, per
  project, the vulnerable package, its resolved version, and the advisories.
- Lock files (`packages.lock.json`, `package-lock.json`, Python requirements):
  `osv-scanner scan source -r <path> --format json --output <scratchpad>/osv.json`. npm dependencies are
  read from the lock file; `npm install`/`npm ci` isn't run for the analysis.
- npm, second source: `npm audit --package-lock-only --json --registry=https://registry.npmjs.org/`, with
  `--omit=dev` in a second run, to separate what is shipped in the bundle from the build tooling. The public
  registry gives the GitHub advisories; the Nexus registry of the `.npmrc` answers with the Sonatype policy
  results (architecture quality, end of life, CVSS thresholds), which are a compliance view, slower, and
  aren't used for the analysis unless the user asks for them.
- For each vulnerable transitive package, the direct package that brings it is found
  (`dotnet nuget why <project> <package>`, `npm explain <package>`), together with the first version of the
  direct package, or of the transitive one, that fixes the advisory.

### Secrets

- Git history: `gitleaks git --redact --report-path <scratchpad>/gitleaks-history.json <repository>`.
- Working tree, untracked files included: `gitleaks dir --redact --report-path <scratchpad>/gitleaks-dir.json <path>`.
- The output is always redacted. A secret is never printed, copied, decoded, or tried; only its file,
  line or commit, and type are kept. A test value, a placeholder, or a public value (e.g. a certificate
  fingerprint) is classified as such after reading the line.

### Code

- `semgrep scan --config p/csharp --metrics=off --json -o <scratchpad>/semgrep-cs.json <path>`, and
  `--config p/typescript --config p/javascript` for the frontend.
- Frontend: `eslint-security <path> -f json -o <scratchpad>/eslint-security.json`, a standalone ESLint with
  the rules of `@microsoft/eslint-plugin-sdl` (Angular sanitizer bypass, trusted URLs, `innerHTML`,
  insecure URLs, `postMessage` to `*`, insecure random) and `eslint-plugin-no-unsanitized`. It ignores the
  ESLint configuration of the project, and doesn't parse the Angular templates: `[innerHTML]`, `[href]`,
  `[src]`, and `[style]` bindings in the `.html` files are listed with `rg` and checked by hand.
- A manual review against `rules/secure-coding.md`, focused on what the tools don't see:
  - authorization of every controller and action (`[Authorize]`, policies, `[AllowAnonymous]`), and the
    filtering by unit of the queries behind them
  - raw SQL and the parameters of the raw SQL helpers
  - parsing of uploaded files (XML settings, ZIP extraction, file names and paths)
  - logging of request content, tokens, or confidential data
  - calls to other services and to SAS (certificate validation, commands built from input)
  - frontend: storage and logging of tokens, URLs built from backend data, guards that are the only
    check of an access rule
- `ast-grep` is used to list a pattern exhaustively (e.g. every controller and its attributes) rather than
  sampling a few files.

### Configuration

- Gitops repository: the chart is rendered for each environment
  (`helm template spiges spiges/helm -f spiges/helm/values.yaml -f spiges/helm/values-<env>.yaml`), and
  `trivy config` is run on the rendered manifests, so that the environment values are taken into account.
- Dockerfiles and other manifests: `trivy config <path>`.

### Triage

Each finding is checked before it's reported:

- **Reachability.** A vulnerable package is only a risk when the vulnerable code is used: the advisory is
  read, and the use of the affected API is searched in the code. A package that is only in a test project
  is reported separately, with a lower severity.
- **Duplicates.** The same advisory found by several tools, or in several projects, is one finding, with
  all its locations.
- **Status.** Each finding gets a status: Confirmed (checked in the code), To be checked (plausible, not
  verified, with what is missing), or False positive (with the reason, kept in the report so that it isn't
  analysed again).
- **Severity.** Critical, High, Medium, or Low, from the advisory or the rule, adjusted to the context
  (exposure of the endpoint, authorization needed, data concerned). An adjustment is explained.

### Report

The report is written in the user's language, to `/shared/<work item id>-<topic>/` when the analysis
belongs to a work item, otherwise to `/shared/security-<repository>-<date>/report.md`, with this structure:

1. Header: scope, repositories and commits, date, tools and versions, date of the vulnerability data
2. Summary: number of findings per severity and status, main risks
3. Findings, ordered from the most to the least severe, each with an ID (e.g. `DEP-1`, `SEC-1`, `CODE-1`,
   `CFG-1`), severity, status, location (`path:line`, package and project, or commit), evidence, risk,
   and recommendation (target version, code change, configuration)
4. Vulnerable dependencies: a table per repository with the package, resolved version, direct or
   transitive (and the direct package that brings it), advisories, fixed version, projects
5. False positives, with the reason
6. Not checked: tools not run, parts of the scope left out, findings without a verified status

The raw tool outputs stay in the scratchpad and aren't copied to `/shared`, since they may contain
redacted secret locations of no use to the user.

### Message to the user

A short recap is given in the terminal: a table of the findings from the most to the least severe (ID,
severity, status, location, one-line summary), followed by the main points not checked. The report
itself isn't repeated.

## Step 3 - Follow-up

The user decides what happens to each finding. Nothing is fixed as part of the analysis:

- A fix is a development request of its own, made on a work item (and its task) as any other change.
- A finding isn't written in a public place (GitHub issue, pull request, or commit of a public
  repository). On request, it's written to an Azure DevOps work item, in tech-english.
- A confirmed secret is reported to the user with its location only; its rotation and the clean-up of
  the history are decided by the user and the owners of the secret.

The report is kept up to date during the discussion: a finding withdrawn, re-rated, or added changes the
report, and the IDs of the existing findings are kept.
