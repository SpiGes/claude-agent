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

# True when one of the GIT_CONFIG_KEY_<n> entries (.env) targets the given URL prefix.
git_server_enabled(){
    local i key
    for ((i = 0; i < ${GIT_CONFIG_COUNT:-0}; i++)); do
        key="GIT_CONFIG_KEY_$i"
        [[ "${!key}" == "http.$1"* ]] && return 0
    done
    return 1
}

# The Azure DevOps Server is always used; GitHub only once its Git token is configured.
configure_authorship DEVOPS /tmp/gitconfig-devops
if git_server_enabled https://github.com/; then
    configure_authorship GITHUB /tmp/gitconfig-github
fi

# Executes the command normally passed to `docker run` (e.g., "claude")
exec "$@"
