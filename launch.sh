# Launch functions for the containerized agent, sourced from ~/.bashrc:
#   source ~/.agents/bfs-claude-agent/launch.sh
# Every path below can be overridden by exporting the variable before this file is sourced.

# Folder holding this repository, derived from this file's own location.
export AGENT_BASE_DIR="${AGENT_BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
export AGENT_HOMES_DIR="${AGENT_HOMES_DIR:-$HOME/.claude-agent-homes}"
export AGENT_ENV_FILE="${AGENT_ENV_FILE:-$AGENT_HOMES_DIR/.env}"
export SHARED_BASE_DIR="${SHARED_BASE_DIR:-$HOME/.agents/shared}"
export AGENT_USER_FILE="${AGENT_USER_FILE:-$AGENT_HOMES_DIR/CLAUDE.user.md}"
export AGENT_NOTIFY_SPEECH="${AGENT_NOTIFY_SPEECH:-1}"
export AGENT_NOTIFY_SPEECH_DIR="${AGENT_NOTIFY_SPEECH_DIR:-$AGENT_HOMES_DIR/notifications/speech}"
# Voice input: the choice made with claude_voice_on / claude_voice_off (file in AGENT_HOMES_DIR), else 0.
export AGENT_VOICE_INPUT="${AGENT_VOICE_INPUT:-$(grep -m1 -sx '[01]' "$AGENT_HOMES_DIR/voice-input" || echo 0)}"
export AGENT_VOICE_MODE="${AGENT_VOICE_MODE:-tap}"
export AGENT_VOICE_LANGUAGE="${AGENT_VOICE_LANGUAGE:-french}"
# Number of agents of one profile that can run at the same time, each in its own slot (see README,
# "Several agents at the same time").
export AGENT_MAX_SLOTS="${AGENT_MAX_SLOTS:-3}"
# Docker volume of the NuGet package cache, shared by the agent and the build containers.
export AGENT_NUGET_VOLUME="${AGENT_NUGET_VOLUME:-bfs-claude-agent-nuget}"
# Build container for VS Code (see README, "Build container for VS Code"): its own env file, and its own home
# in a Docker volume, none of the agent's.
export AGENT_BUILD_ENV_FILE="${AGENT_BUILD_ENV_FILE:-$AGENT_HOMES_DIR/build.env}"
export AGENT_BUILD_HOME_VOLUME="${AGENT_BUILD_HOME_VOLUME:-bfs-claude-agent-build-home}"

# Name that starts each notification of an agent: the name of its workspace folder, or of the folder
# above it when the workspace is a "branch" folder (e.g. backend/branch gives "backend"). AGENT_NOTIFY_NAME
# overrides it.
_claude_agent_notify_name(){
    local name="${PWD##*/}"
    if [ "$name" = "branch" ]; then
        local parent="${PWD%/*}"
        name="${parent##*/}"
    fi
    echo "${AGENT_NOTIFY_NAME:-$name}"
}

# Home folder of one slot of a profile, mounted as /root. A new slot starts from the Claude Code state of
# slot 1 (.claude.json: onboarding done, /workspace trusted; installed plugins), so that its first launch
# doesn't run the onboarding again. The mount points are created here, by the person, since Docker would
# create the missing ones with root as owner. Prints the path of the home folder.
_claude_agent_slot_home(){
    local profile="$1" slot="$2"
    local home="$AGENT_HOMES_DIR/$profile/slots/$slot"
    local reference="$AGENT_HOMES_DIR/$profile/slots/1"

    if [ ! -d "$home" ]; then
        mkdir -p "$home/.claude" || return
        if [ "$slot" != 1 ]; then
            [ ! -f "$reference/.claude.json" ] || cp "$reference/.claude.json" "$home/.claude.json"
            [ ! -d "$reference/.claude/plugins" ] || cp -a "$reference/.claude/plugins" "$home/.claude/"
            echo "New home for slot $slot of the $profile profile, created from slot 1." >&2
        fi
    fi

    mkdir -p "$AGENT_HOMES_DIR/$profile/projects" \
      "$home/.claude/projects" "$home/.claude/rules" "$home/.claude/skills" "$home/.claude/docs" &&
      touch "$home/.claude/CLAUDE.md" "$home/.claude/settings.json" "$home/.claude/mcp.json" \
        "$home/.claude/CLAUDE.user.md" &&
      echo "$home"
}

# Runs the container of a profile. The body runs in a subshell, so that the lock of the slot (fd 9) never
# stays open in the person's shell: it's released when the container ends.
_claude_agent()(
    local profile="$1"
    local command="$2"
    shift 2

    # A home from before the slots ($AGENT_HOMES_DIR/<profile> mounted as /root) is moved by the person (see
    # README, "Upgrading to the slots"), never replaced by an empty slot: Claude Code would run the onboarding
    # again, and the memory and the sessions would look lost.
    if [ -e "$AGENT_HOMES_DIR/$profile/.claude" ] || [ -e "$AGENT_HOMES_DIR/$profile/.claude.json" ]; then
        echo "The home of the $profile profile ($AGENT_HOMES_DIR/$profile) is from before the slots: move it first (see README, \"Upgrading to the slots\")." >&2
        return 1
    fi

    # Slot (see README, "Several agents at the same time"): the first free one of the profile, held through
    # a lock on fd 9 until the container ends. Every process started from here inherits fd 9, so a process
    # that outlives the container (the speech relay) must be started with 9>&-.
    if [[ ! "$AGENT_MAX_SLOTS" =~ ^[1-9][0-9]*$ ]]; then
        echo "AGENT_MAX_SLOTS must be a positive number (currently \"$AGENT_MAX_SLOTS\")." >&2
        return 1
    fi
    local slots_dir="$AGENT_HOMES_DIR/$profile/slots" slot="" n
    mkdir -p "$slots_dir" || return
    for ((n = 1; n <= AGENT_MAX_SLOTS; n++)); do
        exec 9>"$slots_dir/$n.lock"
        if flock -n 9; then
            slot=$n
            break
        fi
    done
    if [ -z "$slot" ]; then
        echo "Every slot of the $profile profile is in use ($AGENT_MAX_SLOTS agents, see AGENT_MAX_SLOTS)." >&2
        return 1
    fi
    local home
    home="$(_claude_agent_slot_home "$profile" "$slot")" || return
    [ "$slot" = 1 ] || echo "Agent started in slot $slot of the $profile profile." >&2

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

    # Notifications (see README). The agent name applies to every channel; beyond slot 1 it ends with the
    # slot number, since the agents usually work in the same workspace. Speech: when enabled and a speech
    # engine is available on this host, the relay is started (no effect when it's already running) and its
    # queue folder is mounted.
    local notify_name
    notify_name="$(_claude_agent_notify_name)"
    [ "$slot" = 1 ] || notify_name+=" $slot"
    local -a notify_args=(-e "AGENT_NOTIFY_NAME=$notify_name")
    local relay="$AGENT_BASE_DIR/notifications/speak-relay.sh"
    if [ "$AGENT_NOTIFY_SPEECH" = "1" ]; then
        if "$relay" --check; then
            local log="${XDG_STATE_HOME:-$HOME/.local/state}/speak-relay.log"
            mkdir -p "$AGENT_NOTIFY_SPEECH_DIR" "${log%/*}"
            setsid -f "$relay" "$AGENT_NOTIFY_SPEECH_DIR" >>"$log" 2>&1 </dev/null 9>&-
            notify_args+=(-v "$AGENT_NOTIFY_SPEECH_DIR:/notifications/speech" \
              -e AGENT_NOTIFY_SPEECH_DIR=/notifications/speech)
        else
            echo "Speech notifications disabled: no speech engine found (see README), or set AGENT_NOTIFY_SPEECH=0." >&2
        fi
    fi

    # Voice input (/voice, see README): opt-in (AGENT_VOICE_INPUT=1), since every process of the container
    # can then record the microphone. Only the WSLg audio socket is mounted, not the whole /mnt/wslg folder
    # (display sockets).
    # Dictation is enabled through --settings, since /voice can't write to the settings.json of the
    # profile (read-only mount). AGENT_VOICE_LANGUAGE also sets the response language of Claude Code.
    local -a voice_args=() voice_settings=()
    if [ "$AGENT_VOICE_INPUT" = "1" ]; then
        if [[ ! "$AGENT_VOICE_MODE" =~ ^(hold|tap)$ ]] || [[ ! "$AGENT_VOICE_LANGUAGE" =~ ^[A-Za-z-]*$ ]]; then
            echo "Voice input disabled: AGENT_VOICE_MODE must be hold or tap, AGENT_VOICE_LANGUAGE a language name or code." >&2
        elif [ -S /mnt/wslg/PulseServer ]; then
            voice_args=(-v /mnt/wslg/PulseServer:/mnt/wslg/PulseServer -e PULSE_SERVER=unix:/mnt/wslg/PulseServer)
            if [ "$command" = "claude" ]; then
                local settings="{\"voice\":{\"enabled\":true,\"mode\":\"$AGENT_VOICE_MODE\"}"
                [ -z "$AGENT_VOICE_LANGUAGE" ] || settings+=",\"language\":\"$AGENT_VOICE_LANGUAGE\""
                voice_settings=(--settings "$settings}")
            fi
        else
            echo "Voice input disabled: /mnt/wslg/PulseServer not found (WSLg isn't active)." >&2
        fi
    fi

    # Home: one per slot. Claude Code projects (memory and session transcripts): one folder for every slot of
    # the profile, so that the agents share their memory and a session can be resumed from any slot.
    docker run -it --rm --user $(id -u):$(id -g) \
      -e HOME=/root \
      -e AGENT_SLOT="$slot" \
      -v "$AGENT_NUGET_VOLUME:/var/cache/nuget/packages" \
      --env-file $AGENT_ENV_FILE \
      -v "$home:/root" \
      -v "$AGENT_HOMES_DIR/$profile/projects:/root/.claude/projects" \
      -v $AGENT_BASE_DIR/profiles/$profile/CLAUDE.md:/root/.claude/CLAUDE.md:ro \
      -v $AGENT_BASE_DIR/profiles/$profile/settings.json:/root/.claude/settings.json:ro \
      -v "$AGENT_USER_FILE:/root/.claude/CLAUDE.user.md" \
      "${mcp_mount[@]}" \
      "${context_mounts[@]}" \
      "${notify_args[@]}" \
      "${voice_args[@]}" \
      -v "$PWD:/workspace" \
      -v $SHARED_BASE_DIR:/shared \
      bfs-claude-agent "$command" "${mcp_args[@]}" "${voice_settings[@]}" "$@"
)

claude_dev(){
    _claude_agent dev claude "$@"
}

claude_dev_bash(){
    _claude_agent dev bash "$@"
}

claude_qualitycheck(){
    _claude_agent qualitycheck claude "$@"
}

# Name of the build container of the current workspace: "build-", the name of the workspace folder (or of the
# folder above it for a "branch" folder, as for the notifications), and a short hash of its path, so that two
# workspaces with the same folder name get two containers. Docker accepts only [a-zA-Z0-9_.-] in a name.
_claude_build_name(){
    local name="${PWD##*/}"
    if [ "$name" = "branch" ]; then
        local parent="${PWD%/*}"
        name="${parent##*/}"
    fi
    name="$(printf '%s' "$name" | tr -c 'a-zA-Z0-9_.-' '-')"
    echo "build-$name-$(printf '%s' "$PWD" | sha256sum | cut -c1-6)"
}

# Starts the build container of the current workspace in the background, unless it's already running (see
# README, "Build container for VS Code"). VS Code attaches to it with the Dev Containers extension. It gets the
# workspace as /workspace, like the agent, but none of the agent's settings: its own env file
# (AGENT_BUILD_ENV_FILE: the person's Git tokens and authorship) and its own home, /home/build, in a Docker
# volume (AGENT_BUILD_HOME_VOLUME) shared by the build containers of every workspace, so that the VS Code server
# and its extensions are installed once, outside the file system of the host.
# A stopped container is removed and created again, so that a start always reads the env file again (e.g.
# after a token rotation).
claude_build(){
    local name
    name="$(_claude_build_name)"
    if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]; then
        echo "Build container $name already running for $PWD."
        return 0
    fi
    if [ ! -f "$AGENT_BUILD_ENV_FILE" ]; then
        echo "$AGENT_BUILD_ENV_FILE not found: create it from build.env.example (see README, \"Build container for VS Code\")." >&2
        return 1
    fi
    docker rm "$name" >/dev/null 2>&1

    docker run -d --init --name "$name" \
      --label bfs-claude-agent.role=build \
      --label "bfs-claude-agent.workspace=$PWD" \
      --user $(id -u):$(id -g) \
      --cap-drop ALL --security-opt no-new-privileges \
      -e HOME=/home/build \
      -e AGENT_CONTAINER_ROLE=build \
      --env-file "$AGENT_BUILD_ENV_FILE" \
      -v "$AGENT_BUILD_HOME_VOLUME:/home/build" \
      -v "$AGENT_NUGET_VOLUME:/var/cache/nuget/packages" \
      -v "$PWD:/workspace" \
      -v "$SHARED_BASE_DIR:/shared" \
      bfs-claude-agent sleep infinity >/dev/null || return

    # The entrypoint stops the container when a setting of the env file is missing or malformed (authorship,
    # certificates): its message is shown and the container removed.
    sleep 2
    if [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" != "true" ]; then
        docker logs "$name" >&2
        docker rm "$name" >/dev/null 2>&1
        echo "Build container $name didn't start, see the message above." >&2
        return 1
    fi
    echo "Build container $name started for $PWD: attach VS Code to it (Dev Containers: Attach to Running Container)."
}

# Stops and removes the build container of the current workspace. The volumes of its home and of the NuGet
# cache are kept.
claude_build_stop(){
    local name
    name="$(_claude_build_name)"
    if docker stop "$name" >/dev/null 2>&1; then
        docker rm "$name" >/dev/null 2>&1
        echo "Build container $name stopped."
    else
        echo "No build container running for $PWD."
    fi
}

# Stops the speech relay of the speech notifications, e.g. to apply a new setting (voice, speaker); the next
# launch of an agent starts it again.
claude_notify_speech_stop(){
    # Matches only the relay process itself ("bash <path>/notifications/speak-relay.sh <queue>"), not e.g. an
    # editor that has the script open
    if pkill -f '^(/usr/bin/)?bash [^ ]*/notifications/speak-relay\.sh '; then
        echo "Speech relay stopped."
    else
        echo "No speech relay running."
    fi
}

# Enable or disable the voice input (microphone) for the next launches of an agent, in every terminal: the
# choice is kept in $AGENT_HOMES_DIR/voice-input. An agent already running keeps its microphone until it ends.
claude_voice_on(){
    _claude_voice_set 1 "Voice input enabled for the next launches of an agent."
}

claude_voice_off(){
    _claude_voice_set 0 "Voice input disabled for the next launches of an agent; an agent already running keeps it until it ends."
}

_claude_voice_set(){
    mkdir -p "$AGENT_HOMES_DIR" && echo "$1" > "$AGENT_HOMES_DIR/voice-input" || return
    export AGENT_VOICE_INPUT="$1"
    echo "$2"
}
