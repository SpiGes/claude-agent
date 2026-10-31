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
# Secret broker (see README, "Secret broker"): the Docker network shared by the broker and the agent containers,
# the folder of the encrypted secrets file, the folder of its log, and the life of an open group.
export AGENT_NETWORK="${AGENT_NETWORK:-bfs-claude-agent}"
export AGENT_VAULT_DIR="${AGENT_VAULT_DIR:-$AGENT_HOMES_DIR/vault}"
export AGENT_BROKER_LOG_DIR="${AGENT_BROKER_LOG_DIR:-$AGENT_HOMES_DIR/broker}"
export BROKER_IDLE_MINUTES="${BROKER_IDLE_MINUTES:-30}"
export BROKER_MAX_HOURS="${BROKER_MAX_HOURS:-4}"

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

    # Secret broker (see README, "Secret broker"): the agent reaches it on the Docker network of the agents. It's
    # started and unlocked here when a secrets file exists; without it, or when the unlock is cancelled, the
    # agent starts without any secret of the broker.
    _claude_network || return
    if [ -f "$AGENT_VAULT_DIR/secrets.age" ]; then
        claude_broker || echo "The agent starts without the secrets of the broker (claude_broker unlocks them later)." >&2
    fi

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
      --network "$AGENT_NETWORK" \
      -e BROKER_URL=http://claude-broker:8080 \
      -e HOME=/root \
      -e AGENT_SLOT="$slot" \
      -e NUGET_PACKAGES=/home/$USER/.nuget/packages \
      -v /home/$USER/.nuget/packages:/home/$USER/.nuget/packages \
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

# Docker network of the agent containers and of the secret broker. The broker is reached there by its name,
# claude-broker; containers outside this network (e.g. a build container) can't reach it.
_claude_network(){
    docker network inspect "$AGENT_NETWORK" >/dev/null 2>&1 \
      || docker network create "$AGENT_NETWORK" >/dev/null \
      || { echo "Docker network $AGENT_NETWORK can't be created." >&2; return 1; }
}

_claude_broker_running(){
    [ "$(docker inspect -f '{{.State.Running}}' claude-broker 2>/dev/null)" = "true" ]
}

_claude_broker_ctl(){
    docker exec -i claude-broker claude-broker ctl "$@"
}

# Starts the secret broker (see README, "Secret broker") unless it's running, then unlocks it unless it's
# unlocked: the passphrase of the secrets file is asked here, by age inside the broker container, and the
# decrypted file goes straight to the broker's memory, never to a disk. The broker keeps running between the
# agent sessions; it's locked again when it restarts (e.g. with Docker Desktop).
# Hardened: no capability, no privilege escalation, read-only file system; the secrets file is mounted read-only,
# the workspace isn't mounted at all.
claude_broker(){
    if [ ! -f "$AGENT_VAULT_DIR/secrets.age" ]; then
        echo "No secrets file ($AGENT_VAULT_DIR/secrets.age): create it with claude_secret edit." >&2
        return 1
    fi
    _claude_network || return
    # A broker started from an older image (the image was rebuilt since) is replaced, so that it runs the
    # current code; its secrets are forgotten and the passphrase is asked again.
    if _claude_broker_running && [ "$(docker inspect -f '{{.Image}}' claude-broker)" != "$(docker image inspect -f '{{.Id}}' bfs-claude-agent)" ]; then
        echo "The image was rebuilt: the secret broker is restarted on the new image."
        docker stop claude-broker >/dev/null
    fi
    if ! _claude_broker_running; then
        docker rm claude-broker >/dev/null 2>&1
        mkdir -p "$AGENT_BROKER_LOG_DIR" || return
        local -a notify_args=(-e AGENT_NOTIFY_NAME=courtier)
        if [ -d "$AGENT_NOTIFY_SPEECH_DIR" ]; then
            notify_args+=(-v "$AGENT_NOTIFY_SPEECH_DIR:/notifications/speech" -e AGENT_NOTIFY_SPEECH_DIR=/notifications/speech)
        fi
        docker run -d --name claude-broker --restart unless-stopped \
          --network "$AGENT_NETWORK" \
          --label bfs-claude-agent.role=broker \
          --user $(id -u):$(id -g) \
          --cap-drop ALL --security-opt no-new-privileges \
          --read-only --tmpfs /tmp:mode=1777 \
          -e PYTHONDONTWRITEBYTECODE=1 \
          -e BROKER_IDLE_MINUTES -e BROKER_MAX_HOURS \
          "${notify_args[@]}" \
          -v "$AGENT_VAULT_DIR:/vault:ro" \
          -v "$AGENT_BROKER_LOG_DIR:/log" \
          --entrypoint claude-broker \
          bfs-claude-agent serve >/dev/null || return
        # Waits for the service: `ctl unlocked` exits with 3 until it's ready.
        local i
        for i in 1 2 3 4 5 6 7 8 9 10; do
            _claude_broker_ctl unlocked >/dev/null 2>&1; [ $? -le 1 ] && break
            sleep 0.5
        done
    fi
    _claude_broker_ctl unlocked && return 0
    echo "Secret broker locked: passphrase of $AGENT_VAULT_DIR/secrets.age."
    docker exec -it claude-broker bash -c 'set -o pipefail; age --decrypt /vault/secrets.age | claude-broker ctl load'
}

# Stops the secret broker: the secrets are forgotten, every group is closed.
claude_broker_stop(){
    if docker stop claude-broker >/dev/null 2>&1; then
        docker rm claude-broker >/dev/null 2>&1
        echo "Secret broker stopped."
    else
        echo "Secret broker not running."
    fi
}

# Commands of the secret broker (see README, "Secret broker"):
#   claude_secret allow <group>       opens a group (git, gh)
#   claude_secret revoke <group>|all  closes a group, or every group
#   claude_secret status              state of the broker and of each group
#   claude_secret log [<lines>]       end of the log of the broker (default 30 lines)
#   claude_secret edit                creates or changes the secrets file, then reloads the broker
claude_secret(){
    local action="${1:-}"
    case "$action" in
        allow|revoke|status)
            if ! _claude_broker_running; then
                echo "Secret broker not running: start it with claude_broker." >&2
                return 1
            fi
            _claude_broker_ctl "$@"
            ;;
        log)
            tail -n "${2:-30}" "$AGENT_BROKER_LOG_DIR/broker.log"
            ;;
        edit)
            _claude_secret_edit
            ;;
        *)
            echo "Usage: claude_secret allow <group> | revoke <group>|all | status | log [<lines>] | edit" >&2
            return 2
            ;;
    esac
}

# Creates or changes the secrets file in a disposable container without network: the file is decrypted to a
# tmpfs, opened with nano, then encrypted again with a passphrase (asked twice) and written back to
# AGENT_VAULT_DIR. The clear text never reaches the disk of the host. A running broker is then locked and
# unlocked again, to load the new file.
_claude_secret_edit(){
    mkdir -p "$AGENT_VAULT_DIR" && chmod 700 "$AGENT_VAULT_DIR" || return
    docker run -it --rm --network none \
      --user $(id -u):$(id -g) \
      --cap-drop ALL --security-opt no-new-privileges \
      --read-only --tmpfs /work:mode=1777 --tmpfs /tmp:mode=1777 \
      -e HOME=/tmp \
      -v "$AGENT_VAULT_DIR:/vault" \
      --entrypoint bash \
      bfs-claude-agent -c '
        set -euo pipefail
        umask 077
        if [ -f /vault/secrets.age ]; then
            age --decrypt --output /work/secrets /vault/secrets.age
        else
            cp /usr/local/share/claude-broker/secrets.example /work/secrets
        fi
        nano /work/secrets
        age --passphrase --output /vault/secrets.age.new /work/secrets
        mv /vault/secrets.age.new /vault/secrets.age
        echo "Secrets file saved."' || return
    if _claude_broker_running; then
        _claude_broker_ctl lock >/dev/null
        claude_broker
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
