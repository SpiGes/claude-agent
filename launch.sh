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
# MCP gateway (see README, "MCP gateway"): the prefix of the Docker networks of the MCP servers (one network per
# server, <prefix>-<server>, joined by its container and the gateway only), the folder of its log, and the time given
# to approve a write call. Its groups use the idle and maximum times of the broker.
export AGENT_MCP_NETWORK_PREFIX="${AGENT_MCP_NETWORK_PREFIX:-bfs-claude-agent-mcp}"
export AGENT_GATEWAY_LOG_DIR="${AGENT_GATEWAY_LOG_DIR:-$AGENT_HOMES_DIR/gateway}"
export GATEWAY_APPROVAL_SECONDS="${GATEWAY_APPROVAL_SECONDS:-120}"
# MCP servers started by claude_mcp, and the drop folder of the Confluence server (the files it reads: page bodies and
# attachments), under /shared so that the agent writes there and the person opens them before an approval.
export AGENT_MCP_SERVERS="devops jira confluence keycloak"
# Profiles of the SpiGes Keycloak clients (<profile>=<client id>, no secret), read by the Keycloak MCP server.
export AGENT_KEYCLOAK_CLIENTS_FILE="${AGENT_KEYCLOAK_CLIENTS_FILE:-$SHARED_BASE_DIR/backend-run/keycloak-clients.txt}"
export AGENT_MCP_CONFLUENCE_DIR="${AGENT_MCP_CONFLUENCE_DIR:-$SHARED_BASE_DIR/mcp-confluence}"

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

    # Secret broker (see README, "Secret broker"): the agent reaches it on the Docker network of the agents. It's
    # started and unlocked here when a secrets file exists, after the notifications, so that it gets the speech
    # queue folder from its first start; without the file, or when the unlock is cancelled, the agent starts
    # without any secret of the broker.
    _claude_network || return
    if [ -f "$AGENT_VAULT_DIR/secrets.age" ]; then
        claude_broker || echo "The agent starts without the secrets of the broker (claude_broker unlocks them later)." >&2
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

    # Name of the container: one per profile and slot (the slot lock makes it unique). The MCP gateway finds it from
    # the address of a call (Docker DNS), to tell the person which agent asks for a write. A stopped container left
    # with this name (e.g. after a crash of Docker) is removed; a running one isn't (docker rm without -f).
    local name="claude-agent-$profile-$slot"
    docker rm "$name" >/dev/null 2>&1

    # Home: one per slot. Claude Code projects (memory and session transcripts): one folder for every slot of
    # the profile, so that the agents share their memory and a session can be resumed from any slot.
    docker run -it --rm --name "$name" --user $(id -u):$(id -g) \
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

# Docker network of one MCP server (see README, "MCP gateway"), printed: its only members are the container of the
# server and the gateway. Docker isolates networks from each other, so neither the agent, nor another MCP container,
# nor a container started without --network (default network) reaches the server, which has no authentication of
# its own. Not internal: the server reaches its remote service through it.
_claude_mcp_network(){
    local network="$AGENT_MCP_NETWORK_PREFIX-$1"
    docker network inspect "$network" >/dev/null 2>&1 \
      || docker network create --label bfs-claude-agent.role=mcp-network "$network" >/dev/null \
      || { echo "Docker network $network can't be created." >&2; return 1; }
    echo "$network"
}

# Connects the gateway to the network of an MCP server, unless it's already connected.
_claude_gateway_join(){
    docker inspect -f '{{json .NetworkSettings.Networks}}' claude-gateway | grep -q "\"$1\"" \
      || docker network connect "$1" claude-gateway
}

_claude_running(){
    [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = "true" ]
}

# True when the container runs an image older than the current bfs-claude-agent (the image was rebuilt since).
_claude_outdated(){
    _claude_running "$1" \
      && [ "$(docker inspect -f '{{.Image}}' "$1")" != "$(docker image inspect -f '{{.Id}}' bfs-claude-agent)" ]
}

_claude_gateway_ctl(){
    docker exec -i claude-gateway claude-gateway ctl "$@"
}

# Routes of the gateway, one per MCP server: <server>=http://claude-mcp-<server>:8000/mcp,...
_claude_gateway_routes(){
    local name routes=()
    for name in $AGENT_MCP_SERVERS; do
        routes+=("$name=http://claude-mcp-$name:8000/mcp")
    done
    (IFS=,; echo "${routes[*]}")
}

# Starts the MCP gateway (see README, "MCP gateway") unless it's running: on the network of the agents, which reach
# it at http://claude-gateway:8090/mcp/<name>, and on the network of each MCP server created so far (claude_mcp
# connects it to the networks created later). It holds no secret: its groups (devops, jira, confluence, keycloak) and
# the waiting write calls are lost when it restarts, every group is then closed. A gateway started from an older image,
# or with other routes (e.g. a server added to launch.sh), is restarted. It doesn't start the MCP containers
# (claude_mcp), nor is it started by claude_dev.
claude_gateway(){
    _claude_network || return
    if _claude_outdated claude-gateway; then
        echo "The image was rebuilt: the MCP gateway is restarted on the new image."
        docker stop claude-gateway >/dev/null
    elif _claude_running claude-gateway \
      && ! docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' claude-gateway \
        | grep -qxF "GATEWAY_ROUTES=$(_claude_gateway_routes)"; then
        echo "The MCP servers changed: the MCP gateway is restarted with the new routes."
        docker stop claude-gateway >/dev/null
    fi
    if _claude_running claude-gateway; then
        echo "MCP gateway running."
        return 0
    fi
    docker rm claude-gateway >/dev/null 2>&1
    mkdir -p "$AGENT_GATEWAY_LOG_DIR" || return
    local -a notify_args=(-e AGENT_NOTIFY_NAME=passerelle)
    if [ -d "$AGENT_NOTIFY_SPEECH_DIR" ]; then
        notify_args+=(-v "$AGENT_NOTIFY_SPEECH_DIR:/notifications/speech" -e AGENT_NOTIFY_SPEECH_DIR=/notifications/speech)
    fi
    docker run -d --name claude-gateway --restart unless-stopped \
      --network "$AGENT_NETWORK" \
      --label bfs-claude-agent.role=gateway \
      --user $(id -u):$(id -g) \
      --cap-drop ALL --security-opt no-new-privileges \
      --read-only --tmpfs /tmp:mode=1777 \
      -e PYTHONDONTWRITEBYTECODE=1 \
      -e GATEWAY_ROUTES="$(_claude_gateway_routes)" \
      -e GATEWAY_IDLE_MINUTES="$BROKER_IDLE_MINUTES" -e GATEWAY_MAX_HOURS="$BROKER_MAX_HOURS" \
      -e GATEWAY_APPROVAL_SECONDS \
      "${notify_args[@]}" \
      -v "$AGENT_GATEWAY_LOG_DIR:/log" \
      --entrypoint claude-gateway \
      bfs-claude-agent serve >/dev/null || return
    local network
    for network in $(docker network ls --filter label=bfs-claude-agent.role=mcp-network --format '{{.Name}}'); do
        _claude_gateway_join "$network" || return
    done
    echo "MCP gateway started."
}

# Stops the MCP gateway: every group of the gateway is closed, the waiting write calls are refused.
claude_gateway_stop(){
    if docker stop claude-gateway >/dev/null 2>&1; then
        docker rm claude-gateway >/dev/null 2>&1
        echo "MCP gateway stopped."
    else
        echo "MCP gateway not running."
    fi
}

# MCP servers moved out of the agent container (see README, "MCP gateway"): command line, the secrets of type 2 that
# the server gets from the secret broker (names separated by spaces), the port it listens on, then the other arguments
# of docker run (settings, mounts), one per line.
#
# mcp-atlassian runs one service per container (Jira or Confluence), so that each container holds one token. Over
# HTTP, it refuses by default a request that brings no credentials of its own (ALLOW_GLOBAL_CRED_FALLBACK): here, only
# the gateway reaches it, which forwards no credential header. Jira is read only (--read-only, and the gateway refuses
# any other tool). Confluence reads the files of content_file, file_path and file_paths only under its working folder,
# the drop folder of /shared, mounted read only at the same path as in the agent. mcp-atlassian gets its token through
# --env-file (MCP_SECRET_FILE=1): the check of a path and the opening of the file are two steps, so a file of the drop
# folder replaced by a symbolic link in between could make it read its own initial environment.
_claude_mcp_spec(){
    local atlassian="mcp-atlassian --transport streamable-http --host 0.0.0.0 --port 8000"
    case "$1" in
        devops)
            printf '%s\n' "mcp-devops-onpremise --transport streamable-http --host 0.0.0.0 --port 8000" \
              DEVOPS_PAT 8000 \
              -e DEVOPS_API_URL=https://devops-server.admin.ch/DefaultCollection/BFS_SIS
            ;;
        jira)
            printf '%s\n' "$atlassian --read-only" JIRA_PERSONAL_TOKEN 8000 \
              -e JIRA_URL=https://intranet.jira.bfs.admin.ch/jira \
              -e TOOLSETS=all -e ALLOW_GLOBAL_CRED_FALLBACK=true -e MCP_SECRET_FILE=1
            ;;
        keycloak)
            # Server of the agent (broker/claude_mcp_keycloak.py): short-lived tokens of the SpiGes Keycloak clients.
            # The clients file (<profile>=<client id>, no secret) is mounted read only; after a change of the file,
            # claude_mcp restart keycloak.
            local secrets="SPIGES_KEYCLOAK_USER_CLIENT_SECRET SPIGES_KEYCLOAK_SAS_CLIENT_SECRET"
            secrets+=" SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET"
            printf '%s\n' "claude-mcp-keycloak serve --port 8000" "$secrets" 8000 \
              -e MCP_SECRET_FILE=1 -e KEYCLOAK_CLIENTS_FILE=/config/keycloak-clients.txt \
              -v "$AGENT_KEYCLOAK_CLIENTS_FILE:/config/keycloak-clients.txt:ro"
            ;;
        confluence)
            printf '%s\n' "$atlassian" CONFLUENCE_PERSONAL_TOKEN 8000 \
              -e CONFLUENCE_URL=https://intranet.confluence.bfs.admin.ch/confluence \
              -e TOOLSETS=all -e ALLOW_GLOBAL_CRED_FALLBACK=true -e MCP_SECRET_FILE=1 \
              -v "$AGENT_MCP_CONFLUENCE_DIR:/shared/mcp-confluence:ro" -w /shared/mcp-confluence
            ;;
        *)
            echo "Unknown MCP server '$1' (servers: $AGENT_MCP_SERVERS)." >&2
            return 1
            ;;
    esac
}

# Starts, stops, or restarts the container of an MCP server (see README, "MCP gateway"):
#   claude_mcp start|stop|restart <server>   (servers: devops, jira, confluence, keycloak)
# The container starts without its token, on the network of the server, shared with the gateway only (connected to it
# when it runs). Its token is then read from the secret broker (ctl export, through docker exec) and written
# straight into the tmpfs of the container, through a pipe between two docker exec: no variable, no file on the host,
# nothing in the configuration of the container. The server reads it, the file is removed, and the token only stays in
# the memory of the server. No automatic restart: after a restart of Docker, claude_mcp start is run again.
claude_mcp(){
    local action="${1:-}" name="${2:-}"
    if [[ ! "$action" =~ ^(start|stop|restart)$ ]] || [ -z "$name" ]; then
        echo "Usage: claude_mcp start|stop|restart <server> (servers: $AGENT_MCP_SERVERS)" >&2
        return 2
    fi
    local -a spec
    mapfile -t spec < <(_claude_mcp_spec "$name") || return
    [ "${#spec[@]}" -ge 3 ] || return 1
    local container="claude-mcp-$name"
    if [ "$action" != start ] || _claude_outdated "$container"; then
        if docker stop "$container" >/dev/null 2>&1; then
            docker rm "$container" >/dev/null 2>&1
            echo "MCP server $name stopped."
        elif [ "$action" = stop ]; then
            echo "MCP server $name not running."
        fi
        [ "$action" = stop ] && return 0
    fi
    if _claude_running "$container"; then
        echo "MCP server $name running."
        return 0
    fi
    if ! _claude_broker_running || ! _claude_broker_ctl unlocked; then
        echo "The secret broker isn't running or is locked: run claude_broker first." >&2
        return 1
    fi
    local network
    network="$(_claude_mcp_network "$name")" || return
    docker rm "$container" >/dev/null 2>&1
    # Created here, with the account of the user: a folder that docker run creates for a mount belongs to root.
    if [ "$name" = confluence ]; then
        mkdir -p "$AGENT_MCP_CONFLUENCE_DIR" || return
    fi
    # A missing file would be created by docker run as a folder.
    if [ "$name" = keycloak ] && [ ! -f "$AGENT_KEYCLOAK_CLIENTS_FILE" ]; then
        echo "No Keycloak clients file ($AGENT_KEYCLOAK_CLIENTS_FILE): see AGENT_KEYCLOAK_CLIENTS_FILE." >&2
        return 1
    fi
    # The server waits for its secrets (a file of KEY=value lines, at most a minute), then replaces the shell. With
    # MCP_SECRET_FILE=1, the file is given to the server (--env-file), which loads it, and it's removed once the server
    # listens, so that the secrets are never in the initial environment of the server (/proc/self/environ), which a
    # tool reading files could reach. Otherwise they're exported to the server (export of the line, no evaluation).
    local start_script='
        for i in $(seq 300); do [ -s /tmp/mcp-env ] && break; sleep 0.2; done
        [ -s /tmp/mcp-env ] || { echo "no secret received" >&2; exit 1; }
        [ "${MCP_SECRET_FILE:-0}" = 1 ] && exec $MCP_COMMAND --env-file /tmp/mcp-env
        while IFS= read -r line; do export "$line"; done < /tmp/mcp-env
        rm -f /tmp/mcp-env
        exec $MCP_COMMAND'
    docker run -d --name "$container" \
      --network "$network" \
      --label bfs-claude-agent.role=mcp \
      --user $(id -u):$(id -g) \
      --cap-drop ALL --security-opt no-new-privileges \
      --read-only --tmpfs /tmp:mode=1777 \
      -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
      -e MCP_COMMAND="${spec[0]}" "${spec[@]:3}" \
      --entrypoint sh \
      bfs-claude-agent -c "$start_script" >/dev/null || return
    if _claude_running claude-gateway; then
        _claude_gateway_join "$network" || return
    fi
    # The secrets go from the broker to the tmpfs of the MCP container, as KEY=value lines, without touching the host:
    # no variable, no file. In a subshell with pipefail, so that a failed export isn't hidden by the second command of
    # the pipe; the container is then removed, with the partial file.
    local -a secret_names
    read -ra secret_names <<< "${spec[1]}"
    if ! (
        set -o pipefail
        for secret_name in "${secret_names[@]}"; do
            printf '%s=' "$secret_name"
            _claude_broker_ctl export "$secret_name" || exit 1
        done | docker exec -i "$container" sh -c \
          'umask 077; cat > /tmp/mcp-env.part && mv /tmp/mcp-env.part /tmp/mcp-env'
    ); then
        echo "The secrets ${spec[1]} couldn't be handed to the MCP server $name: are they in the secrets file?" >&2
        docker stop "$container" >/dev/null 2>&1; docker rm "$container" >/dev/null 2>&1
        return 1
    fi
    _claude_mcp_wait "$container" "${spec[2]}" || return
    # The server has loaded its file of settings before it listens: the secrets now only stay in its memory.
    if ! docker exec "$container" rm -f /tmp/mcp-env; then
        echo "The settings file of the MCP server $name couldn't be removed: the server is stopped." >&2
        docker stop "$container" >/dev/null 2>&1; docker rm "$container" >/dev/null 2>&1
        return 1
    fi
    echo "MCP server $name started."
}

# Waits until the MCP server of a container accepts connections on its port (at most a minute), so that an agent
# started right after claude_mcp start finds it ready. The check runs inside the container (docker exec), since the
# host doesn't reach the network of the server.
_claude_mcp_wait(){
    local container="$1" port="$2" i
    local probe='import socket, sys
try:
    socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=1).close()
except OSError:
    sys.exit(1)'
    for i in $(seq 60); do
        if ! _claude_running "$container"; then
            echo "The MCP server in $container stopped while starting: see docker logs $container." >&2
            return 1
        fi
        docker exec "$container" python3 -c "$probe" "$port" 2>/dev/null && return 0
        sleep 1
    done
    echo "The MCP server in $container doesn't accept connections after a minute: see docker logs $container." >&2
    return 1
}

# Commands of the secret broker and of the MCP gateway (see README, "Secret broker", "MCP gateway"):
#   claude_secret allow <group>           opens a group: git, gh (broker); devops, jira, confluence, keycloak (gateway)
#   claude_secret revoke <group>|all      closes a group, or every group of both
#   claude_secret status                  state of the broker and of the gateway, and the waiting write calls
#   claude_secret pending                 waiting write calls of the gateway
#   claude_secret approve [<id>] [--tool <minutes>m]
#                                         approves a waiting write call (the oldest without id); with --tool, also the
#                                         next calls of the same tool for a while
#   claude_secret deny [<id>]             refuses a waiting write call (the oldest without id)
#   claude_secret log [<lines>]           end of the logs of the broker and of the gateway (default 30 lines each)
#   claude_secret edit                    creates or changes the secrets file, then reloads the broker
claude_secret(){
    local action="${1:-}"
    case "$action" in
        allow|revoke)
            case "${2:-}" in
                git|gh) _claude_secret_on broker "$@" ;;
                all)
                    if ! _claude_running claude-broker && ! _claude_running claude-gateway; then
                        echo "Neither the secret broker nor the MCP gateway is running: nothing to $action." >&2
                        return 1
                    fi
                    _claude_running claude-broker && _claude_broker_ctl "$@"
                    _claude_running claude-gateway && _claude_gateway_ctl "$@"
                    return 0
                    ;;
                *) _claude_secret_on gateway "$@" ;;
            esac
            ;;
        status)
            if _claude_running claude-broker; then _claude_broker_ctl status; else echo "Secret broker: not running"; fi
            if _claude_running claude-gateway; then _claude_gateway_ctl status; else echo "MCP gateway: not running"; fi
            ;;
        pending|approve|deny)
            _claude_secret_on gateway "$@"
            ;;
        log)
            echo "--- broker"; tail -n "${2:-30}" "$AGENT_BROKER_LOG_DIR/broker.log" 2>/dev/null
            echo "--- gateway"; tail -n "${2:-30}" "$AGENT_GATEWAY_LOG_DIR/gateway.log" 2>/dev/null
            return 0
            ;;
        edit)
            _claude_secret_edit
            ;;
        *)
            echo "Usage: claude_secret allow <group> | revoke <group>|all | status | pending | approve [<id>] [--tool <minutes>m] | deny [<id>] | log [<lines>] | edit" >&2
            return 2
            ;;
    esac
}

# Runs a command in the broker or the gateway, with a message when it isn't running.
_claude_secret_on(){
    local service="$1"
    shift
    if ! _claude_running "claude-$service"; then
        echo "The $service isn't running: start it with claude_$service." >&2
        return 1
    fi
    if [ "$service" = broker ]; then _claude_broker_ctl "$@"; else _claude_gateway_ctl "$@"; fi
}

# Creates or changes the secrets file in a disposable container without network: the file is decrypted to a tmpfs,
# opened with nano, then encrypted again and written back to AGENT_VAULT_DIR. The clear text never reaches the disk
# of the host. The passphrase is asked once, on the host (twice for a new file), and handed to age through pipes only
# (age-passphrase): no file, no variable of a container. It serves the decryption, the encryption, and the reload of a
# running broker. When an existing file is left unchanged, nothing is written nor reloaded.
_claude_secret_edit(){
    mkdir -p "$AGENT_VAULT_DIR" && chmod 700 "$AGENT_VAULT_DIR" || return
    local passphrase confirmation
    if [ -f "$AGENT_VAULT_DIR/secrets.age" ]; then
        read -rsp "Passphrase of $AGENT_VAULT_DIR/secrets.age: " passphrase; echo
    else
        read -rsp "New passphrase of the secrets file: " passphrase; echo
        read -rsp "Same passphrase again: " confirmation; echo
        if [ "$passphrase" != "$confirmation" ]; then
            echo "The two passphrases differ: nothing is written." >&2
            return 1
        fi
    fi
    if [ -z "$passphrase" ]; then
        echo "Empty passphrase: nothing is written." >&2
        return 1
    fi
    # The container only sleeps; each step runs in it with docker exec. It ends by itself after 30 minutes, so that the
    # clear text of its tmpfs doesn't outlive an interrupted edit, and it's removed at the end of the edit.
    local container="claude-secret-edit-$$" rc=0
    docker run -d --rm --name "$container" --network none \
      --user $(id -u):$(id -g) \
      --cap-drop ALL --security-opt no-new-privileges \
      --read-only --tmpfs /work:mode=1777 --tmpfs /tmp:mode=1777 \
      -e HOME=/tmp \
      -v "$AGENT_VAULT_DIR:/vault" \
      --entrypoint sleep \
      bfs-claude-agent 1800 >/dev/null || return
    (
        trap 'docker stop -t 0 "$container" >/dev/null 2>&1' EXIT
        _claude_secret_edit_steps "$container" "$passphrase"
    ) || rc=$?
    if [ "$rc" = 10 ]; then
        echo "No change: the secrets file is left as it is."
        return 0
    fi
    [ "$rc" = 0 ] || return "$rc"
    echo "Secrets file saved."
    if _claude_broker_running; then
        printf '%s\n' "$passphrase" | docker exec -i claude-broker bash -c \
          'set -o pipefail; age-passphrase --decrypt /vault/secrets.age | claude-broker ctl load'
    fi
}

# Steps of claude_secret edit in its container: decryption, nano, encryption. Exit code 10 when the file is left
# unchanged.
_claude_secret_edit_steps(){
    local container="$1" passphrase="$2" before=""
    if docker exec "$container" test -f /vault/secrets.age; then
        printf '%s\n' "$passphrase" | docker exec -i "$container" sh -c \
          'umask 077; age-passphrase --decrypt --output /work/secrets /vault/secrets.age' || return
        before="$(docker exec "$container" sha256sum /work/secrets)" || return
    else
        docker exec "$container" sh -c 'umask 077; cp /usr/local/share/claude-broker/secrets.example /work/secrets' \
          || return
    fi
    docker exec -it "$container" nano /work/secrets || return
    [ "$(docker exec "$container" sha256sum /work/secrets)" = "$before" ] && return 10
    printf '%s\n' "$passphrase" | docker exec -i "$container" sh -c \
      'set -e
       umask 077
       age-passphrase --passphrase --output /vault/secrets.age.new /work/secrets
       mv /vault/secrets.age.new /vault/secrets.age' || return
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
