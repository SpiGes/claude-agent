FROM node@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS node-source

FROM mcr.microsoft.com/dotnet/sdk:10.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        git \
        jq \
        python3 \
        python3-pip \
        pipx \
        ripgrep \
        fd-find \
        tree \
        xmlstarlet \
        postgresql-client \
        unzip \
        fonts-liberation \
        libglib2.0-0t64 \
        libnspr4 \
        libnss3 \
        libatk1.0-0t64 \
        libatk-bridge2.0-0t64 \
        libdbus-1-3 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libxcb1 \
        libxkbcommon0 \
        libasound2t64 \
        libatspi2.0-0t64 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd

COPY certs/bit-proxy-ca.pem /usr/local/share/ca-certificates/bit-proxy-ca.crt
COPY certs/nexus-ca.pem /usr/local/share/ca-certificates/nexus-ca.crt
COPY certs/swissgov-root.cer /usr/local/share/ca-certificates/swissgov-root.crt
RUN update-ca-certificates

ENV PIPX_HOME=/opt/pipx
ENV PIPX_BIN_DIR=/usr/local/bin

RUN pipx install mcp-atlassian==0.23.1
RUN pipx install mcp-devops-onpremise==2.1.0
RUN pipx install yq==4.2.0
RUN pipx install semgrep==1.177.0

COPY --from=node-source /usr/local/bin/node /usr/local/bin/node
COPY --from=node-source /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

ENV NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/bit-proxy-ca.crt

RUN npm install -g @anthropic-ai/claude-code
RUN npm install -g @ast-grep/cli
RUN npm install -g @mermaid-js/mermaid-cli
RUN chmod -R 777 $(npm root -g) $(npm config get prefix)/bin

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

ENV DOTNET_NOLOGO=1
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV NUGET_CERT_REVOCATION_MODE=offline
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_DIR=/etc/ssl/certs

RUN git config --system --add safe.directory /backend && \
    git config --system core.fileMode false && \
    git config --system user.name "Claude Agent" && \
    git config --system user.email "claude-agent@spiges.local"

WORKDIR /workspace
