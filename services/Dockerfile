# check=error=true
# Image of the service containers of the agent (see README, "Service images"): the secret broker, the MCP gateway,
# and the MCP servers. One build target per kind of service, so that each container only holds what it runs:
#
#   base       Python, BIT certificates, broker/ modules      claude-gateway, claude-mcp-keycloak
#   broker     base + age, nano                               claude-broker, claude_secret edit container
#   sas        base + OpenSSH client                          claude-mcp-sas
#   oracle     base + Oracle Instant Client, oracledb          claude-mcp-oracle
#   atlassian  base + mcp-atlassian                           claude-mcp-jira, claude-mcp-confluence
#   devops     base + mcp-devops-onpremise                    claude-mcp-devops
#
# Built from the root of the repository, one target at a time (claude_services_build in launch.sh):
#   docker build -f services/Dockerfile --target <target> -t bfs-claude-services:<target> .
#
# Supply chain: the base image is pinned by tag and digest, the Python packages by version and hash (transitive
# dependencies included, services/<target>/requirements.txt), and the Instant Client by URL and SHA-256. The versions
# are updated through Dependabot (.github/dependabot.yml) or by hand, as described in the README ("Dependencies").
# No secret enters the image: the secrets are handed to the running containers by the secret broker.

# --- Common layer of every service: Python of the distribution, BIT certificates, unprivileged user. Ubuntu 24.04, ---
# --- the distribution of the agent image: same packages (e.g. libaio1t64), same Python, same handling of the BIT ---
# --- certificates. Written in full in FROM (no ARG), so that Dependabot updates its digest. ---
FROM ubuntu:noble@sha256:2198e4c65076e9cb0b3d695219df667c0e3b0072da64ca3c2648aca17dcecaf5 AS os
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*
# Corporate proxy and internal CA certificates, mode 0644 whatever the mode of the clone.
COPY --chmod=0644 certs/bit-proxy-ca.pem /usr/local/share/ca-certificates/bit-proxy-ca.crt
COPY --chmod=0644 certs/nexus-ca.pem /usr/local/share/ca-certificates/nexus-ca.crt
COPY --chmod=0644 certs/swissgov-root.cer /usr/local/share/ca-certificates/swissgov-root.crt
RUN update-ca-certificates
# Default user of the image, without login nor home. launch.sh runs the containers with the account of the person
# (--user), which owns the mounted folders; this user only applies to a container started without --user.
RUN useradd --system --uid 10001 --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin service
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_DIR=/etc/ssl/certs

# --- Build stage of the virtual environments: venv and pip only live here. Wheels only (--only-binary), so no ---
# --- compiler is needed; every file is checked against its hash (--require-hashes). Each environment is built at ---
# --- its final path, then copied as is into its target. ---
FROM os AS venv-builder
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/*

FROM venv-builder AS venv-oracle
COPY --chmod=0644 services/oracle/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/claude-mcp-oracle \
    && /opt/claude-mcp-oracle/bin/pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: \
        -r /tmp/requirements.txt \
    && /opt/claude-mcp-oracle/bin/pip uninstall -y pip

FROM venv-builder AS venv-atlassian
COPY --chmod=0644 services/atlassian/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/mcp-atlassian \
    && /opt/mcp-atlassian/bin/pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: \
        -r /tmp/requirements.txt \
    && /opt/mcp-atlassian/bin/pip uninstall -y pip

FROM venv-builder AS venv-devops
COPY --chmod=0644 services/devops/requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/mcp-devops-onpremise \
    && /opt/mcp-devops-onpremise/bin/pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: \
        -r /tmp/requirements.txt \
    && /opt/mcp-devops-onpremise/bin/pip uninstall -y pip

# --- Download stage of Oracle Instant Client (Basic Light): curl and unzip stay out of the oracle target. Official ---
# --- download of Oracle, checked against its SHA-256 (published on the download page); free to use and to ---
# --- redistribute under the Oracle Free Use Terms and Conditions. The values are public: they're kept in shell ---
# --- variables rather than ARGs, which the Dockerfile checks would report as secrets (SecretsUsedInArgOrEnv). ---
FROM os AS instantclient
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip \
    && rm -rf /var/lib/apt/lists/*
RUN url=https://download.oracle.com/otn_software/linux/instantclient/2326400/instantclient-basiclite-linux.x64-23.26.4.0.0.zip \
    && sha256=d049e593bba0041feb15c14ea894e2f1a437377462b99d9bdb6b7ab30b2dd660 \
    && curl -fsSL --proto '=https' --tlsv1.2 -o /tmp/instantclient.zip "$url" \
    && echo "${sha256}  /tmp/instantclient.zip" | sha256sum -c - \
    && mkdir -p /opt/oracle \
    && unzip -q /tmp/instantclient.zip -d /opt/oracle \
    && mv /opt/oracle/instantclient_* /opt/oracle/instantclient \
    && rm /tmp/instantclient.zip

# --- age (secrets file of the broker): the official release binary instead of the Ubuntu package, whose binary ---
# --- is built with Go 1.22 and its vulnerable standard library. BuildKit downloads the archive and checks its ---
# --- SHA-256 (the build fails on a mismatch); age isn't compiled here, since Go modules can't be downloaded from ---
# --- the corporate network. A new version is checked by hand before its hash is changed: Sigsum proof of the ---
# --- release (.proof file) and advisories of the Go version it's built with (README, "Dependencies"). ---
FROM os AS age-release
ADD --checksum=sha256:cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10 \
    https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz /tmp/age.tar.gz
RUN tar -xzf /tmp/age.tar.gz -C /tmp age/age age/LICENSE

# --- base: the MCP gateway and the Keycloak MCP server; the other targets add to it. ---
FROM os AS base
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="bfs-claude-services" \
      org.opencontainers.image.description="Service containers of the containerized Claude Code agent" \
      org.opencontainers.image.source="https://github.com/SpiGes/claude-agent" \
      org.opencontainers.image.revision="${VCS_REF}"
# Spoken notifications of the broker and the gateway (queued for the speech relay of the WSL host, see README).
COPY --chmod=0755 notifications/agent-notify /usr/local/bin/agent-notify
# Python modules of the services (see broker/ and README, "Secret broker", "MCP gateway"). They stay together in
# /opt/claude-agent/broker, so that each command finds the shared modules (access.py, mcp_server.py) next to it; the
# commands are links to them. The folder is created first: a folder created by COPY --chmod takes the mode of the files
# (0644, which an unprivileged user can't enter).
RUN install -d -m 0755 /opt/claude-agent/broker
COPY --chmod=0644 broker/access.py broker/mcp_server.py /opt/claude-agent/broker/
COPY --chmod=0755 broker/claude_broker.py broker/claude_gateway.py broker/age_passphrase.py \
    broker/claude_mcp_keycloak.py broker/claude_mcp_sas.py broker/claude_mcp_oracle.py /opt/claude-agent/broker/
RUN ln -s /opt/claude-agent/broker/claude_broker.py /usr/local/bin/claude-broker \
    && ln -s /opt/claude-agent/broker/claude_gateway.py /usr/local/bin/claude-gateway \
    && ln -s /opt/claude-agent/broker/age_passphrase.py /usr/local/bin/age-passphrase \
    && ln -s /opt/claude-agent/broker/claude_mcp_keycloak.py /usr/local/bin/claude-mcp-keycloak \
    && ln -s /opt/claude-agent/broker/claude_mcp_sas.py /usr/local/bin/claude-mcp-sas \
    && ln -s /opt/claude-agent/broker/claude_mcp_oracle.py /usr/local/bin/claude-mcp-oracle
USER service
WORKDIR /tmp
# Smoke test, as the unprivileged user: every command and module is reachable.
RUN for command in agent-notify claude-broker claude-gateway age-passphrase claude-mcp-keycloak claude-mcp-sas; do \
        test -x "$(command -v "$command")" || { echo "$command not reachable" >&2; exit 1; }; \
    done \
    && test -r /opt/claude-agent/broker/access.py && test -r /opt/claude-agent/broker/mcp_server.py

# --- broker: the secret broker and the container of claude_secret edit. age (age-release stage) encrypts the ---
# --- secrets file with a passphrase (no plugin); nano edits it in the disposable container. ---
FROM base AS broker
USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends nano \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -m 0755 /usr/local/share/claude-broker /usr/local/share/doc/age
COPY --from=age-release --chmod=0755 /tmp/age/age /usr/local/bin/age
COPY --from=age-release --chmod=0644 /tmp/age/LICENSE /usr/local/share/doc/age/LICENSE
COPY --chmod=0644 broker/secrets.example /usr/local/share/claude-broker/secrets.example
USER service
RUN age --version >/dev/null && test -r /usr/local/share/claude-broker/secrets.example

# --- sas: the SAS MCP server runs ssh and scp; the password reaches them through SSH_ASKPASS only. ---
FROM base AS sas
USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
USER service
RUN ssh -V

# --- oracle: oracledb runs in thick mode, since the SpiGes databases require the native network encryption of ---
# --- Oracle, which the thin mode doesn't support. Instant Client looks for libaio.so.1, named libaio.so.1t64 since ---
# --- Ubuntu 24.04; its folder is registered with ldconfig, so that its libraries find each other (e.g. libnnz.so). ---
FROM base AS oracle
USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends libaio1t64 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=instantclient /opt/oracle/instantclient /opt/oracle/instantclient
RUN ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 \
    && echo /opt/oracle/instantclient > /etc/ld.so.conf.d/oracle-instantclient.conf \
    && ldconfig
COPY --from=venv-oracle /opt/claude-mcp-oracle /opt/claude-mcp-oracle
USER service
# Smoke test: the command is reachable, and oracledb loads the Instant Client (thick mode), without any connection.
RUN test -x "$(command -v claude-mcp-oracle)" \
    && /opt/claude-mcp-oracle/bin/python3 -c "import oracledb; oracledb.init_oracle_client()"

# --- atlassian: the Jira and Confluence MCP servers (one service per container). ---
FROM base AS atlassian
USER root
COPY --from=venv-atlassian /opt/mcp-atlassian /opt/mcp-atlassian
RUN ln -s /opt/mcp-atlassian/bin/mcp-atlassian /usr/local/bin/mcp-atlassian
USER service
RUN mcp-atlassian --help >/dev/null

# --- devops: the Azure DevOps MCP server. ---
FROM base AS devops
USER root
COPY --from=venv-devops /opt/mcp-devops-onpremise /opt/mcp-devops-onpremise
RUN ln -s /opt/mcp-devops-onpremise/bin/mcp-devops-onpremise /usr/local/bin/mcp-devops-onpremise
USER service
RUN test -x "$(command -v mcp-devops-onpremise)" && /opt/mcp-devops-onpremise/bin/python3 -c "import mcp_devops"
