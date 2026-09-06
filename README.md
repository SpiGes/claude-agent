# spiges (agent)

Docker configuration that runs Claude Code, as an agent, on the SpiGes backend and frontend repositories, with several profiles (dev, qualitycheck), without ever installing Claude Code directly on Windows or inside WSL2.

## Prerequisites

Before this repository can be used, the following must already be in place:

- Docker Desktop, with WSL2 integration enabled for the Ubuntu distribution (Settings, Resources, WSL Integration).
- WSL2 (Ubuntu), with the SpiGes repositories already cloned natively inside it, not on D:\...:
  ```
  ~/workspace/spiges/backend/branch
  ~/workspace/spiges/frontend/branch
  ```
- A valid Claude Code token (see Step 2 below). A Claude.ai account with a subscription is enough; a separate API key is not needed.

If any of these three points is not yet in place, the full setup guide should be consulted before continuing here.

## Installation

### 1. Clone this repository

From WSL2:
```bash
git clone <url-of-this-repository> ~/.agents/spiges
```
(the exact location does not matter; `~/.agents/spiges` is only an example)

### 2. Configure authentication

```bash
cd ~/.agents/spiges
cp .env.example .env
```
Then generate a token, if this has not been done yet:
```bash
docker build -t spiges-claude-agent .
docker run -it --rm spiges-claude-agent claude setup-token
```
A URL is shown; it should be opened in a browser, followed by login and the on-screen instructions. The generated token is then copied into `.env`:
```
CLAUDE_CODE_OAUTH_TOKEN=<the generated token>
```
This `.env` file must never be committed or shared; it is already excluded by `.gitignore`.

### 3. Build the image

```bash
docker build -t spiges-claude-agent .
```
This only needs to be done again after a change to the Dockerfile, to entrypoint.sh, or to the certificates under certs/, not after a change to a profile (see below).

### 4. Load the launch functions

Add the following to `~/.bashrc`:
```bash
AGENT_BASE_DIR=~/.agents
SPIGES_AGENT_BASE_DIR=$AGENT_BASE_DIR/spiges
SHARED_BASE_DIR=$AGENT_BASE_DIR/shared

_claude_agent(){
    local profile="$1"
    local command="$2"
    shift 2

    docker run -it --rm --user $(id -u):$(id -g) \
      -e HOME=/root \
      -e NUGET_PACKAGES=/home/$USER/.nuget/packages \
      -v /home/$USER/.nuget/packages:/home/$USER/.nuget/packages \
      --env-file $SPIGES_AGENT_BASE_DIR/.env \
      -v $SPIGES_AGENT_BASE_DIR/claude-home-$profile:/root \
      -v $SPIGES_AGENT_BASE_DIR/profiles/$profile/CLAUDE.md:/root/.claude/CLAUDE.md:ro \
      -v $SPIGES_AGENT_BASE_DIR/profiles/$profile/settings.json:/root/.claude/settings.json:ro \
      -v ~/workspace/spiges:/workspace \
      -v $SHARED_BASE_DIR:/shared \
      spiges-claude-agent "$command" "$@"
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
`~/workspace/spiges` should be adjusted if the repositories are cloned somewhere else. Then reload:
```bash
source ~/.bashrc
```

Extra arguments pass straight through to `claude`; for example, to resume a previous session:
```bash
claude_dev --resume <session-id>
```

## Usage

| Command | Effect |
|---|---|
| `claude_dev` | Starts the agent in general development mode (dev profile) |
| `claude_dev_bash` | Opens a shell in the same environment, without starting Claude Code; useful for testing dotnet build, git status, and similar commands by hand |
| `claude_qualitycheck` | Starts the agent in review-only mode (qualitycheck profile, read-only) |

Each command starts a new container, removed on exit (--rm); nothing needs to be stopped or cleaned up by hand.

### Sharing a one-off document with the agent

The file is placed under `$AGENT_BASE_DIR/shared/`, then referenced in the conversation with `@/shared/file-name`.

## Available profiles

- dev (`profiles/dev/`): full read and write access; `git commit`/`git push` ask for confirmation each time. Meant for everyday development work.
- qualitycheck (`profiles/qualitycheck/`): read-only; cannot technically edit a file or commit, enforced through settings.json rather than only requested. Meant for reviewing code with no risk of an accidental change.

A profile is made of two files:
- CLAUDE.md: describes the role and the project context.
- settings.json: enforces the actual technical permissions.

Changing a profile does not require rebuilding the image (docker build); these files are mounted read-only at every launch, not copied into the image. A plain `git pull` on this repository is enough to apply an update at the next launch.

### Adding a new profile

1. Create `profiles/<name>/CLAUDE.md` and `profiles/<name>/settings.json`.
2. Add a function to `~/.bashrc`:
   ```bash
   claude_<name>(){
       _claude_agent <name> claude
   }
   ```

## Repository structure

```
spiges/
  .gitignore
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
    qualitycheck/
      CLAUDE.md
      settings.json
  claude-home-dev/              (created locally on first launch, never committed)
  claude-home-qualitycheck/     (created locally on first launch, never committed)
```

`claude-home-*/` holds personal authentication and history data. It is specific to each user, ignored by Git, and should never be shared, even though its initial content came from a shared profile.

## Troubleshooting

For any build, network, or permission issue found with this configuration, the full setup guide should be consulted; it documents the difficulties already encountered with this setup (enterprise certificates, the shared NuGet cache, file permissions, and so on) and how each one was solved.