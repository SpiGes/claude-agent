#!/bin/bash
set -e
# Silent update of Claude Code CLI at each container startup.

# In case of failure (unavailable network, etc.), the process continues anyway —

# Never block the startup due to a failed update.
npm update -g @anthropic-ai/claude-code --silent 2>/dev/null || true

# Executes the command normally passed to `docker run` (e.g., "claude")
exec "$@"
