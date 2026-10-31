#!/bin/bash
set -e
# Silent update of Claude Code CLI at each container startup.

# In case of failure (unavailable network, etc.), the process continues anyway —

# Never block the startup due to a failed update.
npm update -g @anthropic-ai/claude-code --silent 2>/dev/null || true

fail(){
    echo "entrypoint: $1" >&2
    echo "entrypoint: see .env.example and the README (Installation, Step 4)." >&2
    exit 1
}

# Commit authorship for one Git server, read from <PREFIX>_COMMIT_NAME and <PREFIX>_COMMIT_EMAIL
# (.env) and written into the file that the system git config includes only for repositories
# whose remote is on that server (see Dockerfile). A missing value, or a value with a leading or
# trailing space or a Windows line ending (kept as is by docker --env-file), stops the startup.
configure_authorship(){
    local prefix="$1" file="$2"
    local name_var="${prefix}_COMMIT_NAME" email_var="${prefix}_COMMIT_EMAIL"
    local name="${!name_var}" email="${!email_var}"

    [ -n "$name" ] || fail "$name_var is not set in the .env file."
    [[ "$name" =~ ^[^[:space:]](.*[^[:space:]])?$ ]] || fail "$name_var starts or ends with a space or a Windows line ending."
    [ -n "$email" ] || fail "$email_var is not set in the .env file."
    [[ "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || fail "$email_var is not a valid email address, or contains a space or a Windows line ending."

    git config --file "$file" user.name "$name"
    git config --file "$file" user.email "$email"
}

# True when the server is used: one of the GIT_CONFIG_KEY_<n> entries (env file of a build container) targets
# its URL prefix, or its authorship pair (<PREFIX>_COMMIT_NAME/EMAIL) is set, as in the agent container, whose
# Git tokens come from the secret broker.
git_server_enabled(){
    local url="$1" prefix="$2" i key name_var="${2}_COMMIT_NAME" email_var="${2}_COMMIT_EMAIL"
    [ -n "${!name_var:-}${!email_var:-}" ] && return 0
    for ((i = 0; i < ${GIT_CONFIG_COUNT:-0}; i++)); do
        key="GIT_CONFIG_KEY_$i"
        [[ "${!key}" == "http.$url"* ]] && return 0
    done
    return 1
}

# The Azure DevOps Server is always used; GitHub and Bitbucket only when they're used (see above).
configure_authorship DEVOPS /tmp/gitconfig-devops
if git_server_enabled https://github.com/ GITHUB; then
    configure_authorship GITHUB /tmp/gitconfig-github
fi
if git_server_enabled https://bitbucket.bit.admin.ch/ BITBUCKET; then
    configure_authorship BITBUCKET /tmp/gitconfig-bitbucket
fi

# Git tokens from the secret broker (see README, "Secret broker"), only in a container that has one (BROKER_URL,
# set by launch.sh for the agent containers); the file is included by the system Git configuration. Git asks the
# broker through git-credential-broker; the path of the repository is sent for Bitbucket, whose tokens are given
# per repository, and for GitHub, whose tokens are given per organization. The Azure DevOps Server also offers
# Negotiate and NTLM, which curl would prefer and which fail with a PAT: Basic is sent right away.
if [ -n "${BROKER_URL:-}" ]; then
    git config --file /tmp/gitconfig-broker credential.helper broker
    git config --file /tmp/gitconfig-broker credential.https://bitbucket.bit.admin.ch.useHttpPath true
    git config --file /tmp/gitconfig-broker credential.https://github.com.useHttpPath true
    git config --file /tmp/gitconfig-broker http.https://devops-server.admin.ch.proactiveAuth basic
fi

# Personal CA certificates, from the folder given by EXTRA_CA_CERTS_DIR (.env), e.g. the self-signed
# certificate of a MinIO running on the developer machine. The container runs as a non-root user,
# so update-ca-certificates can't be used here: the certificates are appended to a copy of the
# system bundle, which the OpenSSL-based tools, Python and .NET read through SSL_CERT_FILE, and to a
# copy of the Node extra certificates. Only *.crt and *.pem files are read; a file without any PEM
# certificate stops the startup.
if [ -n "${EXTRA_CA_CERTS_DIR:-}" ]; then
    [ -d "$EXTRA_CA_CERTS_DIR" ] || fail "EXTRA_CA_CERTS_DIR ($EXTRA_CA_CERTS_DIR) is not a folder."

    bundle=/tmp/ca-certificates.crt
    node_bundle=/tmp/node-extra-ca-certificates.crt
    cp "$SSL_CERT_FILE" "$bundle"
    cp "$NODE_EXTRA_CA_CERTS" "$node_bundle"

    shopt -s nullglob
    for cert in "$EXTRA_CA_CERTS_DIR"/*.crt "$EXTRA_CA_CERTS_DIR"/*.pem; do
        grep -q -- '-----BEGIN CERTIFICATE-----' "$cert" || fail "$cert holds no PEM certificate."
        { echo; cat "$cert"; } | tee -a "$bundle" >> "$node_bundle"
    done
    shopt -u nullglob

    export SSL_CERT_FILE="$bundle" REQUESTS_CA_BUNDLE="$bundle" NODE_EXTRA_CA_CERTS="$node_bundle"
fi

# Executes the command normally passed to `docker run` (e.g., "claude")
exec "$@"
