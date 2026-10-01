# Node.js runtime, copied later instead of installed via apt to control the exact version.
FROM node@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS node-source

# Base image: .NET SDK, needed to build/test the backend (ASP.NET Core, PostgreSQL, Oracle).
# The Ubuntu release (noble = 24.04) is pinned in the tag, so that it only changes on purpose
# (the git PPA below also targets noble).
FROM mcr.microsoft.com/dotnet/sdk:10.0-noble

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        # HTTP client, used for downloads and connectivity checks.
        curl \
        # GPG, used to verify signed packages/keys.
        gnupg \
        # Command-line JSON processor.
        jq \
        # Line ending conversion (`dos2unix`/`unix2dos`), used to keep the CRLF files of the backend
        # unchanged after an edit.
        dos2unix \
        # File type, encoding and line ending detection (e.g. BOM, CRLF checks).
        file \
        # Hex dump (`xxd`), used to inspect the first bytes of a file (e.g. BOM).
        xxd \
        # Binary inspection tools (e.g. `strings`, to look for a name in a compiled assembly).
        binutils \
        # Python runtime, required by pipx and other Python-based tools.
        python3 \
        # Python package installer, dependency of pipx.
        python3-pip \
        # Excel (.xlsx) reading/writing from Python, used to inspect workbooks (e.g. ITAR_K
        # templates: defined names, hidden worksheets, cell values) without a .NET project.
        python3-openpyxl \
        # YAML reading from Python, used to process a rendered Helm manifest or a values file in a script.
        python3-yaml \
        # Installs Python CLI applications in isolated virtual environments (see pipx installs below).
        pipx \
        # Fast text search (`rg`), preferred default for plain-text search.
        ripgrep \
        # Fast file search (`fd`), installed as `fdfind` on Ubuntu, aliased below.
        fd-find \
        # Compact recursive directory listing.
        tree \
        # XML query/validation from the shell.
        xmlstarlet \
        # PostgreSQL client (`psql`).
        postgresql-client \
        # Required by Puppeteer (used by mermaid-cli) to extract the downloaded Chromium archive.
        unzip \
        # Font files: without any installed font, chrome-headless-shell renders shapes but no
        # diagram text (Liberation is metric-compatible with Arial/Times, used across the
        # Puppeteer/Chrome-in-Docker ecosystem for this exact purpose).
        fonts-liberation \
        # --- Runtime shared libraries required by chrome-headless-shell (Puppeteer/mermaid-cli), ---
        # --- used to render Mermaid diagrams to PNG. Chrome itself is not installed as a package; ---
        # --- Puppeteer downloads its own chrome-headless-shell binary at npm install time. ---
        # GLib/GObject/GIO: base object system and low-level I/O used by Chromium.
        libglib2.0-0t64 \
        # NSPR: Netscape Portable Runtime, used by NSS.
        libnspr4 \
        # NSS: cryptography/TLS library used by Chromium (also provides libnssutil3).
        libnss3 \
        # ATK: accessibility toolkit interface exposed by Chromium's rendering engine.
        libatk1.0-0t64 \
        # ATK-Bridge: bridges ATK to the platform's accessibility bus.
        libatk-bridge2.0-0t64 \
        # D-Bus: inter-process communication used internally by Chromium.
        libdbus-1-3 \
        # X11 windowing system libraries linked by Chromium even in headless mode.
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        # GBM: generic buffer management, used for GPU buffer allocation.
        libgbm1 \
        # XCB: low-level X11 client library.
        libxcb1 \
        # xkbcommon: keyboard handling library.
        libxkbcommon0 \
        # ALSA: audio library linked by Chromium.
        libasound2t64 \
        # AT-SPI: assistive technology service provider interface, used by the accessibility bridge.
        libatspi2.0-0t64 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd

# Corporate proxy / internal CA certificates, trusted so HTTPS calls (apt, npm, dotnet, pip, ...)
# work behind the corporate proxy.
COPY certs/bit-proxy-ca.pem /usr/local/share/ca-certificates/bit-proxy-ca.crt
COPY certs/nexus-ca.pem /usr/local/share/ca-certificates/nexus-ca.crt
COPY certs/swissgov-root.cer /usr/local/share/ca-certificates/swissgov-root.crt
RUN update-ca-certificates

# GitHub CLI (`gh`), used by the agent for GitHub API actions (issues, pull requests), authenticated
# through GH_TOKEN (.env). Installed from GitHub's official apt repository, after the corporate CA
# certificates above so the download works behind the proxy. Deliberately not wired into git
# (no `gh auth setup-git`): git operations keep their own dedicated PAT (GIT_CONFIG_*).
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Git, from the "Ubuntu Git Maintainers" PPA instead of the Ubuntu archive: Ubuntu 24.04 provides
# git 2.43, while 2.48 or higher is needed to create worktrees with relative paths
# (worktree.useRelativePaths, set below). With absolute paths, the worktrees can't be opened from
# the host (e.g. in VSCode), since the container paths don't exist there. The PPA signing key is
# checked against its fingerprint; the build fails if the installed git is older than 2.48.
# The fingerprint is a public value, kept in a shell variable rather than an ARG, which the
# Dockerfile linter would report as a secret (SecretsUsedInArgOrEnv).
RUN fingerprint=E1DD270288B4E6030699E45FA1715D88E1DF1F24 \
    && curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x${fingerprint}" \
        | gpg --dearmor -o /etc/apt/keyrings/git-core-ppa.gpg \
    && gpg --show-keys --with-colons /etc/apt/keyrings/git-core-ppa.gpg \
        | grep -q "^fpr:::::::::${fingerprint}:" \
    && chmod go+r /etc/apt/keyrings/git-core-ppa.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/git-core-ppa.gpg] https://ppa.launchpadcontent.net/git-core/ppa/ubuntu noble main" \
        > /etc/apt/sources.list.d/git-core-ppa.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' git)" ge "1:2.48"

# Code coverage report generator (`reportgenerator`), turns the coverlet output of the backend
# tests into a readable report per file.
RUN dotnet tool install dotnet-reportgenerator-globaltool --version 5.5.11 --tool-path /opt/dotnet-tools \
    && ln -s /opt/dotnet-tools/reportgenerator /usr/local/bin/reportgenerator

# .NET decompiler (`ilspycmd`), used to read the code of a NuGet package (e.g.
# `ilspycmd -t <Namespace.Type> ~/.nuget/packages/<package>/<version>/lib/<tfm>/<assembly>.dll`) when the
# behaviour of a library must be checked rather than assumed.
RUN dotnet tool install ilspycmd --version 11.1.0.9782 --tool-path /opt/dotnet-tools \
    && ln -s /opt/dotnet-tools/ilspycmd /usr/local/bin/ilspycmd

# Helm (`helm template`), renders the charts of the gitops repository per environment
# (values-<env>.yaml), to check a change before it's pushed. Only the client binary is used: no
# cluster access is configured. The archive is checked against its published SHA-256, kept in a
# shell variable for the same linter reason as the git PPA fingerprint below.
RUN helm_version=v3.22.0 \
    && helm_sha256=1e4ab49e429626cf6c6958d914248b78c9730803c2751b87627e171dc800e7bb \
    && curl -fsSL -o /tmp/helm.tgz "https://get.helm.sh/helm-${helm_version}-linux-amd64.tar.gz" \
    && echo "${helm_sha256}  /tmp/helm.tgz" | sha256sum -c - \
    && tar -xzf /tmp/helm.tgz -C /tmp linux-amd64/helm \
    && install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm \
    && rm -rf /tmp/helm.tgz /tmp/linux-amd64

ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

# MCP server for Confluence/Jira integration.
RUN pipx install mcp-atlassian==0.23.1
# MCP server for on-premises Azure DevOps integration.
RUN pipx install mcp-devops-onpremise==2.1.0
# YAML processor, used to read/patch single fields (e.g. Helm values-*.yaml) without a full rewrite.
RUN pipx install yq==4.2.0
# Pattern-based static analysis (C#, TypeScript, YAML), used by the code-review/security-review skills.
RUN pipx install semgrep==1.177.0

# Bring in Node.js and npm from the official Node image instead of an apt-installed version.
COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/bit-proxy-ca.crt

# Claude Code CLI itself — the agent running in this container.
RUN npm install -g @anthropic-ai/claude-code
# The container later runs as a non-root user (--user $(id -u):$(id -g)); only this package's
# own directory needs to stay writable, for Claude Code's runtime self-update to succeed.
# Scoped to @anthropic-ai rather than the whole global node_modules tree, so it stays cheap
# even as more (non-self-updating) global packages are added below.
RUN chmod -R 777 $(npm root -g)/@anthropic-ai $(npm config get prefix)/bin
# Structural code search across C#/TypeScript, matches syntax rather than plain text.
RUN npm install -g @ast-grep/cli@0.45.3
# Renders Mermaid diagram code to PNG/SVG/PDF (`mmdc`).
RUN npm install -g @mermaid-js/mermaid-cli@11.17.0
# JSON5 parser (`json5 <file>` prints plain JSON), used to read JSON files with comments and a BOM
# (e.g. the backend appsettings.json) and pipe them to jq.
RUN npm install -g json5@2.2.3

# Container entrypoint script.
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# .NET build hygiene and telemetry opt-out.
ENV DOTNET_NOLOGO=1
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV NUGET_CERT_REVOCATION_MODE=offline
# Point Python/requests and OpenSSL-based tooling at the system CA bundle (includes the
# corporate proxy certs installed above).
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_DIR=/etc/ssl/certs

# Git safety and default authorship for commits made by the agent.
RUN git config --system --add safe.directory /backend && \
    git config --system core.fileMode false && \
    git config --system worktree.useRelativePaths true && \
    git config --system user.name "Claude Agent" && \
    git config --system user.email "claude-agent@spiges.local"

# Per-server authorship, each applied only to repositories whose remote is on that server:
# - Azure DevOps Server: the person's own agent identity (DEVOPS_COMMIT_NAME/EMAIL, .env)
# - GitHub: the person's GitHub agent account identity (GITHUB_COMMIT_NAME/EMAIL, .env)
# The included files are generated at startup by entrypoint.sh, which also refuses to start when
# a required variable is missing. The default authorship above only remains for other servers.
RUN git config --system includeIf."hasconfig:remote.*.url:https://devops-server.admin.ch/**".path /tmp/gitconfig-devops && \
    git config --system includeIf."hasconfig:remote.*.url:https://github.com/**".path /tmp/gitconfig-github

WORKDIR /workspace
