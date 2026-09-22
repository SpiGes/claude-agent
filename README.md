# bfs-claude-agent

Docker configuration that runs Claude Code as a disposable containerized agent, currently used on the SpiGes backend, frontend, and specs repositories, with several profiles (dev, qualitycheck) and optional MCP integrations (Confluence Data Center, Azure DevOps Server). The container mechanism itself, the launch function, the profile system, the optional MCP support, is generic and carries nothing specific to SpiGes. The profiles built on top of it are not: each one's `CLAUDE.md`, and the `rules/`, `skills/`, `docs/` it may declare (see Available profiles below), hold SpiGes-specific content, alongside the three certificates baked into the image.

Claude Code is never installed directly on Windows, nor directly inside WSL2: it always runs inside its own disposable container, with access to the file system limited to whatever is explicitly mounted into it.

## Prerequisites

Before this repository can be used, the following must already be in place:

- Docker Desktop, with WSL2 integration enabled for the Ubuntu distribution (Settings, Resources, WSL Integration).
- WSL2 (Ubuntu), with the SpiGes repositories already cloned natively inside it (over HTTPS, not SSH), not on Windows (for performance reason).
- A Personal Access Token dedicated to Git operations against the on-premise Azure DevOps Server, scoped to Code (Read & Write) only — distinct from the read-only `DEVOPS_PAT` used by the Azure DevOps MCP server. See the full setup guide, Chapter 3, for how this token is used on both WSL2 and inside the container.
- A valid Claude Code token (see Step 2 below). A Claude.ai account with a subscription is enough; a separate API key is not needed.

If any of these points is not yet in place, the full setup guide ("Containerized Agent, Native WSL2 Development") should be consulted before continuing here.

## Installation

### 1. Clone this repository

From WSL2:
```bash
git clone <url-of-this-repository> ~/.agents/bfs-claude-agent
export AGENT_BASE_DIR=~/.agents/bfs-claude-agent
```
(the exact location does not matter; `AGENT_BASE_DIR` is what the launch function actually uses)

### 2. Build the image

```bash
docker build -t bfs-claude-agent "$AGENT_BASE_DIR"
```
This only needs to be done again after a change to the Dockerfile, to entrypoint.sh, or to the certificates under certs/, not after a change to a profile (see below).

### 3. Configure authentication

Personal state and secrets are kept entirely outside this repository, in `AGENT_HOMES_DIR` (default `$HOME/.claude-agent-homes`). It must be created by hand, once, so that Docker does not create it itself with the wrong owner:
```bash
export AGENT_HOMES_DIR="$HOME/.claude-agent-homes"
export AGENT_ENV_FILE="$AGENT_HOMES_DIR/.env"
mkdir -p "$AGENT_HOMES_DIR/dev" "$AGENT_HOMES_DIR/qualitycheck"
cp "$AGENT_BASE_DIR/.env.example" "$AGENT_ENV_FILE"
```
Generate a token, if this has not been done yet:
```bash
docker run -it --rm bfs-claude-agent claude setup-token
```
A URL is shown; it should be opened in a browser, followed by login and the on-screen instructions. The generated token is then written into `$AGENT_ENV_FILE`:
```
CLAUDE_CODE_OAUTH_TOKEN=<the generated token>
```
This file must never be committed or shared. Since it lives outside the versioned repository entirely, rather than as a `.gitignore`-protected file inside it, it is not exposed to a broad `git add -A`, or to a backup of the repository folder that does not respect ignore rules.

The same file must also carry the Git access token from the Prerequisites above, so that the disposable container can authenticate over HTTPS against the on-premise Azure DevOps Server with no `.ssh` mount and no private key involved. This is done through Git's own environment-variable configuration mechanism, which needs no file written inside the container:
```
GIT_CONFIG_COUNT=1
GIT_CONFIG_KEY_0=http.https://devops-server.admin.ch.extraHeader
GIT_CONFIG_VALUE_0=Authorization: Basic <PAT in base64, e.g. via printf ':%s' '<PAT>' | base64 -w0>
```
These lines are appended to `$AGENT_ENV_FILE`, alongside `CLAUDE_CODE_OAUTH_TOKEN`, and reach the container through the same `--env-file` already used below. The full setup guide (Chapter 3) documents the equivalent, one-time setup for the WSL2 host itself, needed for the person's own `git` operations outside the container.

### 4. Load the launch functions

Add the following to `~/.bashrc`:
```bash
export AGENT_BASE_DIR="$HOME/.agents/bfs-claude-agent"
export AGENT_HOMES_DIR="${AGENT_HOMES_DIR:-$HOME/.claude-agent-homes}"
export AGENT_ENV_FILE="${AGENT_ENV_FILE:-$HOME/.claude-agent-homes/.env}"
export SHARED_BASE_DIR="$HOME/.agents/shared"
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
```
Also create the shared folder by hand, once, for the same ownership reason as `AGENT_HOMES_DIR` above:
```bash
mkdir -p ~/.agents/shared
```
Then reload:
```bash
source ~/.bashrc
```

**The workspace is whatever folder is current when the function is called**, not a fixed path baked into the function. Move into the repository (or, for a task spanning several, into the parent folder holding them side by side, see "Working across several repositories" below) before running `claude_dev`.

Extra arguments pass straight through to `claude`; for example, to resume a previous session:
```bash
claude_dev --resume <session-id>
```

## Usage

| Command | Effect |
|---|---|
| `claude_dev` | Starts the agent in general development mode (dev profile), with the Confluence and Azure DevOps MCP servers available |
| `claude_dev_bash` | Opens a shell in the same environment, without starting Claude Code; useful for testing dotnet build, git status, and similar commands by hand |
| `claude_qualitycheck` | Starts the agent in review-only mode (qualitycheck profile, read-only, no MCP server) |

Each command starts a new container, removed on exit (--rm); nothing needs to be stopped or cleaned up by hand.

### Working across several repositories at once

Starting the agent one level up, at `~/workspace/spiges`, the parent of the backend, frontend, and specs repositories, is a supported way to work, useful for a task that spans more than one of them (a backend DTO change and its frontend usage, for example). A hand-created `CLAUDE.md`, made only of forced imports, must exist at that parent folder for the three repositories' own project context to load there:
```markdown
@backend/branch/CLAUDE.md
@frontend/branch/CLAUDE.md
@specs/branch/CLAUDE.md
```

### Sharing a one-off document with the agent

The file is placed under `$SHARED_BASE_DIR` (default `~/.agents/shared`), then referenced in the conversation with `@/shared/file-name`.

### A personal preference, shared across every profile

A habit that should apply regardless of which profile is running, for example a keyword that switches a response to technical English, is written once to `$AGENT_USER_FILE` (default `$AGENT_HOMES_DIR/CLAUDE.user.md`), a plain file outside any profile that every profile's `CLAUDE.md` imports automatically. It can be edited directly from the WSL2 host, not only from inside a session.

## Available profiles

- dev (`profiles/dev/`): full read and write access; `git commit`/`git push` ask for confirmation each time. Declares an `mcp.json`, so the Confluence and Azure DevOps MCP servers are available in this profile (see the two MCP appendices in the specs repository). Meant for everyday development work.
- qualitycheck (`profiles/qualitycheck/`): read-only; cannot technically edit a file or commit, enforced through settings.json rather than only requested. Declares no `mcp.json`. Meant for reviewing code with no risk of an accidental change.

A profile is made of two required files, and optional context folders:
- `CLAUDE.md`: describes the role and the project context, and ends with `@CLAUDE.user.md` so a personal addition is always possible on top of it.
- `settings.json`: enforces the actual technical permissions (`deny`/`ask`/`allow`).
- `mcp.json` (optional): declares MCP servers for that profile; whether this file exists is the only thing that decides whether it gets mounted, with no other configuration needed.
- `.claude/rules/`, `.claude/skills/`, `.claude/docs/` (optional): mounted automatically when present.

Changing a profile does not require rebuilding the image (docker build); these files are mounted read-only at every launch, not copied into the image. A plain `git pull` on this repository is enough to apply an update at the next launch.

### Optional MCP integrations (dev profile)

The dev profile's `mcp.json` currently declares two MCP servers, each documented in its own appendix in the specs repository:

- **confluence**: reads and writes Confluence Data Center pages directly from Markdown, instead of the manual copy-paste workflow. Its Personal Access Token cannot be scoped down and keeps the full rights of the account that created it, so `settings.json` adds a compensating boundary: page deletion, page moves, and restriction changes are denied outright, and every other write (create, update, add a comment or attachment, and so on) asks for confirmation first.
- **devops**: reads pull requests, their comment threads, and work items directly from the on-premises Azure DevOps Server. Its Personal Access Token is scoped to read-only, so the write tools already fail at the server; `settings.json` still denies the outright destructive ones (deletion, wiki, repository operations) and asks before every other write, so a failing attempt is caught before the call is even made.

### Adding a new profile

1. Create `profiles/<name>/CLAUDE.md` and `profiles/<name>/settings.json`, and optionally `profiles/<name>/mcp.json` and `.claude/rules|skills|docs`.
2. Create its home directory by hand, before its first launch, for the same ownership reason as `dev` and `qualitycheck` (Step 3 of Installation):
   ```bash
   mkdir -p "$AGENT_HOMES_DIR/<name>"
   ```
3. Add a function to `~/.bashrc`:
   ```bash
   claude_<name>(){
       _claude_agent <name> claude
   }
   ```

## Repository structure

```
bfs-claude-agent/
  Dockerfile
  entrypoint.sh
  certs/
    bit-proxy-ca.pem
    nexus-ca.pem
    swissgov-root.cer
  .env.example
  profiles/
    dev/
      CLAUDE.md
      settings.json
      mcp.json
      .claude/
        rules/        (optional)
        skills/        (optional)
        docs/          (optional)
    qualitycheck/
      CLAUDE.md
      settings.json
```

The three `COPY` instructions for the certificates in the Dockerfile are the one remaining organization-specific part of an otherwise generic image; this is a known, deliberately unaddressed limitation, not an oversight.

Nothing personal or secret lives inside this repository:
- `AGENT_HOMES_DIR` (default `$HOME/.claude-agent-homes`), one subfolder per profile, holds Claude Code's own state (session history, local configuration), plus a shared `CLAUDE.user.md` at its root (see "A personal preference" above).
- `AGENT_ENV_FILE` (default `$AGENT_HOMES_DIR/.env`) holds the real Claude Code token; only `.env.example`, with a placeholder, is committed here.
- `SHARED_BASE_DIR` (default `~/.agents/shared`, where this very file lives) holds ad-hoc documents shared with the agent, outside any project repository.

With no secret and no personal state ever placed inside it, this repository needs no `.gitignore` at all. The image and the launch mechanism (Dockerfile, entrypoint.sh, `_claude_agent`) could serve a different project unchanged; the profiles themselves (`CLAUDE.md`, `rules/`, `skills/`, `docs/`) would not, since they hold SpiGes-specific content.

## Troubleshooting

For any build, network, or permission issue found with this configuration, the full setup guide should be consulted; it documents the difficulties already encountered with this setup (enterprise certificates, the shared NuGet cache, file permissions, and so on) and how each one was solved. The two MCP appendices (Confluence, Azure DevOps) document the difficulties specific to those two integrations separately.
