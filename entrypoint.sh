#!/bin/bash
set -e
# Silent update of Claude Code CLI at each container startup.

# In case of failure (unavailable network, etc.), the process continues anyway —

# Never block the startup due to a failed update.
npm update -g @anthropic-ai/claude-code --silent 2>/dev/null || true

# GitHub commit authorship of the person's agent account, included by the system git config
# only for repositories with a github.com remote (see Dockerfile).
if [ -n "$GITHUB_COMMIT_EMAIL" ]; then
    git config --file /tmp/gitconfig-github user.email "$GITHUB_COMMIT_EMAIL"
fi

# Executes the command normally passed to `docker run` (e.g., "claude")
exec "$@"
