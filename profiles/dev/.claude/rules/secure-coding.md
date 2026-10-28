---
paths: ["**/*.cs", "**/*.ts", "**/*.html", "**/*.csproj", "**/Directory.Packages.props", "**/package.json", "**/values*.yaml", "**/templates/*.yaml", "**/Dockerfile", "**/*.py", "**/requirements*.txt", "**/pyproject.toml"]
---

# Secure coding

These rules apply when code is written and when it's reviewed. They complement the stack conventions
(`csharp-coding-conventions.md`, and the `backend.md`/`frontend.md` rules of each repository); a
conflict between them is flagged, not resolved silently.

## 1. Access control

- Every endpoint has an explicit authorization: an `[Authorize]` attribute with a named policy or role, on
  the controller or the action, as the existing controllers. A new `[AllowAnonymous]` or technical
  endpoint (e.g. called by SAS or another SIS service) is a design decision, written in the design
  document and in the pull request.
- Data that belongs to a unit (site, enterprise, canton) is filtered by the authorization of the user
  (e.g. `IUnitAuthorizationPredicateService`) in the query itself, not after loading. The filter fails
  closed: an unknown role or an empty authorization returns nothing, never everything.
- An identifier received from the client (unit, delivery, export, file) is checked against the
  authorization of the user before it's used, even when the frontend only offers allowed values.
- The frontend hides what the user may not do, but never enforces it: every check exists in the backend.

## 2. Input and files

- Command parameters are validated by the FluentValidation validators; business checks stay in the
  handler or service (see the C# conventions).
- XML is parsed with DTD processing prohibited and no external resolver (`DtdProcessing.Prohibit`,
  `XmlResolver = null`), to prevent XXE.
- Archives (ZIP) are extracted with a check of each entry path (no `..`, no absolute path) and of the total
  uncompressed size and entry count (zip slip, zip bomb).
- A file name or path built from user input is never used as is: the file name is replaced or sanitized,
  and the final path is checked to stay under the expected folder.
- Size limits of uploaded files and requests are kept or set explicitly.

## 3. SQL and other interpreters

- SQL is parameterized (EF Core, `FromSql` with interpolated parameters, parameters of the raw SQL
  helpers). A value is never concatenated into SQL. An identifier that can't be a parameter (table,
  schema, column) comes from configuration or an allow-list, never from the request.
- The same applies to shell commands (e.g. SSH to SAS), LDAP filters, and regular expressions built from
  input (escaped, with a timeout).

## 4. Secrets and configuration

- No secret in the code, the tests, the committed `appsettings*.json`, the Helm values, or the logs.
  Secrets come from the environment or the secret store of the deployment.
- Certificate validation is never disabled (`ServerCertificateCustomValidationCallback` returning
  `true`, `NODE_TLS_REJECT_UNAUTHORIZED=0`), not even for a test environment.
- CORS lists explicit origins; no wildcard together with credentials.
- A development-only feature (Swagger, Hangfire dashboard, detailed errors) is restricted to the
  environments that need it, and protected by authorization where it's exposed.

## 5. Data protection and logging

- The logs contain technical identifiers only (delivery, unit, wave, correlation ID). They never contain
  patient or hospital record content, tokens, passwords, connection strings, or full request bodies.
- An error returned to the client never contains a stack trace, SQL, or internal paths; the details go
  to the log.
- Exported or temporary files with confidential data are deleted when they're no longer needed, and are
  never written to a location shared with other tenants or users.

## 6. Frontend (Angular)

- No `innerHTML` binding with data that isn't constant, and no `bypassSecurityTrust*` without a
  documented reason.
- Tokens are handled by the authentication library only; they're never stored by application code in
  `localStorage` or logged to the console.
- A URL opened or downloaded from data received from the backend is checked to be relative or on an
  expected host.
- A changed TypeScript file is checked with `eslint-security <path>`; a new warning is fixed or justified.

## 7. Python

- `subprocess` is called with a list of arguments, never with `shell=True`. A command run on a remote shell
  (e.g. paramiko `exec_command`) quotes every argument with `shlex.quote`.
- SSH connections check the host key (`paramiko.RejectPolicy` with known hosts), never `AutoAddPolicy`.
- No `eval`, `exec`, `pickle`, `marshal`, or `yaml.load` (only `yaml.safe_load`) on data that comes from
  outside the program.
- Archives are extracted member by member, with a check that each path stays under the target folder;
  `tarfile.extractall` uses `filter="data"` where the Python version supports it.
- XML from outside the system is parsed with `defusedxml` when third-party packages are allowed. With the
  standard library only (e.g. the SAS toolkit scripts), `xml.etree` is used on trusted files only.
- A secret is passed through the environment, a file readable by the user only, or `SSH_ASKPASS`; never as
  a command-line argument, which other users of the server can read.
- Temporary files are created with `tempfile` (`mkstemp`, `TemporaryDirectory`), never with a fixed or
  predictable name in a shared folder.
- `bandit` is run on the changed files with the configuration of the project; an excluded check is
  justified by a comment in that configuration.

## 8. Dependencies

- A new package, or a new major version, is justified in the pull request: maintained, with a
  compatible license, from the usual feeds (Nexus, npm registry).
- Before the commit, the changed dependencies are checked for known vulnerabilities, transitive ones
  included: `dotnet list package --vulnerable --include-transitive` for .NET, `osv-scanner` for the lock
  files, `npm audit --package-lock-only --registry=https://registry.npmjs.org/` for npm, and `pip-audit`
  for Python (on the virtual environment, so that the transitive packages are covered). A High or
  Critical advisory on a changed package is reported to the user before the commit.
- A vulnerable transitive package is fixed by upgrading the direct package that brings it when possible,
  otherwise by pinning the fixed version centrally (`Directory.Packages.props`), with a comment that
  gives the advisory.

## 9. Helm and container configuration

- A change of a chart doesn't remove resource limits, nor weaken a `securityContext` where one is set.
  Missing hardening of the existing charts (non-root user, no privilege escalation) is reported as a
  finding of its own, not added on the side of an unrelated change.
- `trivy config` is run on a changed chart or Dockerfile, and its findings are compared with those of the
  target branch (e.g. `origin/main` in the gitops repository), so that only the findings introduced by
  the change are reported for it.
