# Launch functions for the containerized agent, sourced from ~/.bashrc:
#   source ~/.agents/bfs-claude-agent/launch.sh
# Every path below can be overridden by exporting the variable before this file is sourced.

# Folder holding this repository, derived from this file's own location.
export AGENT_BASE_DIR="${AGENT_BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
export AGENT_HOMES_DIR="${AGENT_HOMES_DIR:-$HOME/.claude-agent-homes}"
export AGENT_ENV_FILE="${AGENT_ENV_FILE:-$AGENT_HOMES_DIR/.env}"
export SHARED_BASE_DIR="${SHARED_BASE_DIR:-$HOME/.agents/shared}"
export AGENT_USER_FILE="${AGENT_USER_FILE:-$AGENT_HOMES_DIR/CLAUDE.user.md}"

_claude_agent(){
    local profile="$1"
    local command="$2"
    shift 2

    local -a mcp_mount=()
    local -a mcp_args=()
    local mcp_config="$AGENT_BASE_DIR/profiles/$profile/mcp.json"
    if [ "$command" = "claude" ] && [ -f "$mcp_config" ]; then
        mcp_mount=(-v "$mcp_config:/root/.claude/mcp.json:ro")
        mcp_args=(--mcp-config /root/.claude/mcp.json)
    fi

    local -a context_mounts=()
    for d in rules skills docs; do
        local src="$AGENT_BASE_DIR/profiles/$profile/.claude/$d"
        if [ -d "$src" ]; then
            context_mounts+=(-v "$src:/root/.claude/$d:ro")
        fi
    done

    touch "$AGENT_USER_FILE" 2>/dev/null || true

    docker run -it --rm --user $(id -u):$(id -g) \
      -e HOME=/root \
      -e NUGET_PACKAGES=/home/$USER/.nuget/packages \
      -v /home/$USER/.nuget/packages:/home/$USER/.nuget/packages \
      --env-file $AGENT_ENV_FILE \
      -v "$AGENT_HOMES_DIR/$profile:/root" \
      -v $AGENT_BASE_DIR/profiles/$profile/CLAUDE.md:/root/.claude/CLAUDE.md:ro \
      -v $AGENT_BASE_DIR/profiles/$profile/settings.json:/root/.claude/settings.json:ro \
      -v "$AGENT_USER_FILE:/root/.claude/CLAUDE.user.md" \
      "${mcp_mount[@]}" \
      "${context_mounts[@]}" \
      -v "$PWD:/workspace" \
      -v $SHARED_BASE_DIR:/shared \
      bfs-claude-agent "$command" "${mcp_args[@]}" "$@"
}

claude_dev(){
    _claude_agent dev claude "$@"
}

claude_dev_bash(){
    _claude_agent dev bash "$@"
}

claude_qualitycheck(){
    _claude_agent qualitycheck claude "$@"
}
