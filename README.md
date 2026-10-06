# bfs-claude-agent

Docker configuration that runs Claude Code as a disposable containerized agent, currently used on the SpiGes backend, frontend, and specs repositories, with several profiles (dev, qualitycheck) and optional MCP integrations (Confluence Data Center, Azure DevOps Server).

Claude Code is never installed directly on Windows, nor directly inside WSL2: it always runs inside its own disposable container, with access to the file system limited to whatever is explicitly mounted into it.

## Prerequisites

Before this repository can be used, the following must already be in place:

- Docker Desktop, with WSL2 integration enabled for the Ubuntu distribution (Settings, Resources, WSL Integration).
- WSL2 (Ubuntu), with the SpiGes repositories already cloned natively inside it (over HTTPS, not SSH), not on Windows (for performance reason).
- A Personal Access Token dedicated to Git operations against the on-premise Azure DevOps Server, scoped to Code (Read & Write) only — distinct from the read-only `DEVOPS_PAT` used by the Azure DevOps MCP server. See the full setup guide, Chapter 3, for how this token is used on both WSL2 and inside the container.
- A Claude.ai account with a subscription, from which the Claude Code token is generated (Step 4 below); a separate API key is not needed.
- Optional, only for repositories hosted on GitHub: a dedicated GitHub account for the agent, distinct from the person's own account (see "Optional: GitHub access" below).

If any of these points is not yet in place, the full setup guide ("Containerized Agent, Native WSL2 Development") should be consulted before continuing here.

## Installation

The minimum steps, all run from WSL2. The reasons behind them are given in "Installation details" below.

### 1. Clone this repository

```bash
git clone <url-of-this-repository> ~/.agents/bfs-claude-agent
```

### 2. Build the image

```bash
docker build -t bfs-claude-agent ~/.agents/bfs-claude-agent
```
Needed again only after a change to the Dockerfile, to `entrypoint.sh`, to `notifications/agent-notify`, or to the certificates under `certs/`.

### 3. Create the personal folders

```bash
mkdir -p ~/.claude-agent-homes/dev ~/.claude-agent-homes/qualitycheck ~/.agents/shared
cp ~/.agents/bfs-claude-agent/.env.example ~/.claude-agent-homes/.env
```

### 4. Fill in the `.env` file

Generate the Claude Code token (a URL is shown, to be opened in a browser for login):
```bash
docker run -it --rm --entrypoint claude bfs-claude-agent setup-token
```
Then, in `~/.claude-agent-homes/.env`, replace the placeholders of:
- `CLAUDE_CODE_OAUTH_TOKEN`, with the generated token;
- `GIT_CONFIG_VALUE_0`, with the Azure DevOps Git token from the Prerequisites, base64-encoded as shown in the file;
- `DEVOPS_COMMIT_NAME` and `DEVOPS_COMMIT_EMAIL`, with the authorship of the agent's commits on the Azure DevOps Server, chosen by each person (e.g. `Claude Agent (Firstname Lastname)` and `firstname.lastname+agent-dev@example.org`).

The GitHub lines stay commented out unless "Optional: GitHub access" below is set up.

### 5. Load the launch functions

```bash
echo 'source ~/.agents/bfs-claude-agent/launch.sh' >> ~/.bashrc
source ~/.bashrc
```

### 6. Start the agent

```bash
cd <repository>
claude_dev
```

### Installation details

- **Personal state and secrets live outside this repository**, in `~/.claude-agent-homes` (`AGENT_HOMES_DIR`). Since the `.env` file is not inside the versioned folder at all, rather than a `.gitignore`-protected file within it, it is not exposed to a broad `git add -A`, or to a backup of the repository folder that does not respect ignore rules. It must never be committed or shared.
- **The folders of Step 3 are created by hand**, before the first launch, so that Docker does not create them itself with the wrong owner.
- **Git authenticates through environment variables only** (`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`): each entry adds an `Authorization` header for one server only, so the container needs no `.ssh` mount, no private key, and no credential file. They reach the container through `--env-file`, like `CLAUDE_CODE_OAUTH_TOKEN`. The full setup guide (Chapter 3) documents the equivalent, one-time setup for the WSL2 host itself, needed for the person's own `git` operations outside the container.
- **`docker --env-file` keeps every character after `=` as is**, trailing spaces and Windows line endings (`\r`) included. A value copied with either of them may break silently.
- **The commit authorship is set per Git server**, through the same pair of variables for each one: `<PREFIX>_COMMIT_NAME` and `<PREFIX>_COMMIT_EMAIL`, with `DEVOPS` for the Azure DevOps Server and `GITHUB` for GitHub. At startup, `entrypoint.sh` writes each pair into `/tmp/gitconfig-<server>`, which the system Git configuration includes only for repositories whose remote is on that server. The container refuses to start, with an explicit message, when a pair of a server in use is missing, or when a value starts or ends with a space or a Windows line ending. Repositories on any other server keep the default `Claude Agent <claude-agent@spiges.local>` authorship.
- **`claude setup-token` (Step 4) is run with `--entrypoint claude`**, which bypasses `entrypoint.sh`: the token is generated before the `.env` file is filled in, so the authorship checks would otherwise refuse to start.
- **`launch.sh` is sourced, not copied**, so a plain `git pull` on this repository updates the launch functions at the next shell start. It defines `claude_dev`, `claude_dev_bash`, and `claude_qualitycheck` (see Usage), on top of `_claude_agent`, which runs the container for a given profile.
- **Every path is overridable**: `AGENT_HOMES_DIR`, `AGENT_ENV_FILE` (default `$AGENT_HOMES_DIR/.env`), `SHARED_BASE_DIR` (default `~/.agents/shared`), and `AGENT_USER_FILE` (default `$AGENT_HOMES_DIR/CLAUDE.user.md`) can be exported in `~/.bashrc` before `launch.sh` is sourced. `AGENT_BASE_DIR` defaults to the folder `launch.sh` itself lives in, so this repository can be cloned anywhere.
- **The workspace is whatever folder is current when the function is called**, not a fixed path baked into the function. Move into the repository (or, for a task spanning several, into the parent folder holding them side by side, see "Working across several repositories" below) before running `claude_dev`.
- **Extra arguments pass straight through to `claude`**; for example, to resume a previous session:
  ```bash
  claude_dev --resume <session-id>
  ```

## Optional: GitHub access

Only needed when the agent works on repositories hosted on GitHub. The agent then acts through its own GitHub account, never through the person's, with two separate fine-grained Personal Access Tokens, so that neither token can do everything on its own: the Git token can push but cannot open a pull request, the API token can open a pull request but cannot push.

### GitHub organization side

Done once by an organization owner:
- The agent account is invited as a **member** of the organization, not as an outside collaborator: fine-grained tokens cannot reach an organization's repositories for an outside collaborator.
- The agent account is added to a team dedicated to agent accounts (secret, no parent team), and that team is granted **Write** on the repositories the agent works on. The team is then the single point of control over which repositories the agent can reach.
- The organization's base permissions are set to **No permission**, so that the agent account does not silently inherit access to every repository, including a private one created later.
- Fine-grained Personal Access Tokens are allowed in the organization settings. When administrator approval is required, every new token, and every later change to a token's permissions, stays pending, and fails with a 403, until an owner approves it.

### Tokens

Created while signed in as the agent account (Settings, Developer settings, Fine-grained tokens), both with the organization as resource owner, an expiration date, and "All repositories" as repository access (the team above restricts it):

| Token | Repository permissions | Variable in the `.env` file |
|---|---|---|
| Git token | Contents: Read and write | `GIT_CONFIG_KEY_1` / `GIT_CONFIG_VALUE_1` |
| API token | Issues: Read and write, Pull requests: Read and write, Contents: Read-only | `GH_TOKEN` |

Metadata: Read-only is added by GitHub automatically; every other permission, including Workflows, stays at No access. Without Workflows, a push that creates or changes a file under `.github/workflows/` is rejected, so any CI change goes through a person.

### `.env` file

The five GitHub lines of `.env.example` are uncommented, and `GIT_CONFIG_COUNT` is raised to 2; with it left at 1, Git silently ignores the GitHub entry:
```
GIT_CONFIG_COUNT=2
GIT_CONFIG_KEY_0=http.https://devops-server.admin.ch.extraHeader
GIT_CONFIG_VALUE_0=Authorization: Basic <Azure DevOps PAT in base64>
GIT_CONFIG_KEY_1=http.https://github.com/.extraHeader
GIT_CONFIG_VALUE_1=Authorization: Basic <GitHub Git token in base64, e.g. via printf 'x-access-token:%s' '<PAT>' | base64 -w0>
GITHUB_COMMIT_NAME=<agent name, e.g. Claude Agent (Firstname Lastname)>
GITHUB_COMMIT_EMAIL=<ID>+<login>@users.noreply.github.com
GH_TOKEN=<GitHub API token, plain, no base64>
```
- `GIT_CONFIG_VALUE_1` applies only to `https://github.com/` URLs, so the Azure DevOps token and the GitHub token never reach the wrong server.
- `GITHUB_COMMIT_NAME` and `GITHUB_COMMIT_EMAIL` are the authorship of the agent's commits on GitHub, handled like the Azure DevOps pair (see "Installation details"), and required once the GitHub Git token is enabled. The email is the agent account's noreply address, shown in its Settings, Emails when "Keep my email addresses private" is enabled; its numeric part is the account's permanent ID, which keeps commits linked to the account even after a rename. GitHub links commits to the account through the email only; the name is displayed as is.
- `GH_TOKEN` is read directly by the GitHub CLI (`gh`), with no `gh auth login` needed. `gh` is deliberately not wired into Git (no `gh auth setup-git`), so that Git keeps using its own token only.

Reading the tokens with `read -rs` and appending them with `printf`, rather than pasting them into an editor, avoids stray spaces and `\r`, and keeps them out of the shell history.

### Check

Once the container is started, from `claude_dev_bash`:
```bash
gh auth status                                  # agent account, via GH_TOKEN
git -C <github-repo> push --dry-run origin HEAD # authenticates with the Git token
git -C <github-repo> config user.email          # the noreply address
```

## Optional: speech notifications

The agent tells the person, with a spoken message, when the requested work is finished or when it's blocked on a decision (e.g. "backend: the review of PR 44008 is finished, three points to check"). Intermediate steps, like a build or a test run, aren't announced. The instruction is given in the dev profile `CLAUDE.md`; a `Notification` hook of its `settings.json` covers the permission prompts, which the agent can't announce itself.

### How it works

- In the container, `agent-notify "<text>"` writes the text to a file in the queue folder, mounted as `/notifications/speech`. The container gets no audio device and no PulseAudio socket: the WSLg audio server also gives access to the microphone, which the agent doesn't need.
- On the WSL2 host, `notifications/speak-relay.sh` reads the queue, in the order of arrival, and speaks each message through the WSLg audio, with piper when it's installed, with espeak-ng otherwise. Messages older than 10 minutes are skipped, so that a backlog isn't read out when the relay starts.
- `launch.sh` starts the relay at each launch of an agent. Only one relay runs per user (`flock` lock): a launch while it's running has no effect. The relay keeps running after the agent ends, until WSL shuts the distribution down.
- Each message starts with the name of the agent workspace: the folder the agent is started from, or the folder above it when that folder is named `branch` (e.g. `backend` for `backend/branch`). Several agents running at the same time can be told apart.
- The feature is enabled by default. When no speech engine is found on the host, `launch.sh` prints a warning, mounts no queue folder, and `agent-notify` does nothing.

### Host prerequisites

From WSL2, once:

```bash
~/.agents/bfs-claude-agent/notifications/install-speech-host.sh
```

The script installs `pulseaudio-utils`, `espeak-ng`, and `pipx` (with `sudo`), then piper (`pipx install piper-tts`) and the French voice `fr_FR-siwis-medium`, and ends with a spoken test sentence. Each step is skipped when it's already done, so the script can be run again.

- WSLg must be active: the script stops when `/mnt/wslg/PulseServer` doesn't exist.
- The voice is downloaded with curl, which shows the progress and gives up when the transfer stalls. Its files are checked against the MD5 checksums of the piper voice catalog at each run, and downloaded again when they're damaged (e.g. an incomplete download); `--force` downloads them again in any case. The voice is then checked by a synthesis, also with `--no-test`.
- pip doesn't read the proxy settings of apt, and the voice download (curl) doesn't read the ones of pip. A proxy can be given with `--proxy <url>`, which takes precedence over any other setting; otherwise, the script takes it from `https_proxy`, else from a `pip.conf`, else from apt, and gives it to both. In every case, the proxy only applies to the piper installation and the voice download, with the system CA bundle, since the corporate proxy re-signs the HTTPS traffic. No configuration file is changed. A proxy URL with credentials given with `--proxy` is kept in the shell history: a `pip.conf` or the apt settings are preferable in that case.
- Another voice (e.g. `de_DE-thorsten-medium`, `fr_FR-tom-medium`, see the piper documentation) is added with `install-speech-host.sh --voice <name>`; it can then be chosen from an agent (see "Choosing the voice" below), or made the default voice with `SPEAK_RELAY_PIPER_MODEL`.
- espeak-ng alone is enough, but its voice is robotic. piper is used as soon as `~/.local/bin/piper`, a voice, and `paplay` are found.

### Settings

Exported in `~/.bashrc`, before `launch.sh` is sourced:

| Variable | Default | Effect |
|---|---|---|
| `AGENT_NOTIFY_SPEECH` | `1` | `0`: `launch.sh` neither starts the relay nor mounts the queue folder, so the agents launched afterwards don't notify. A relay already running and the agents launched before aren't affected (see `claude_notify_speech_stop`) |
| `AGENT_NOTIFY_SPEECH_DIR` | `$AGENT_HOMES_DIR/notifications/speech` | Speech queue folder on the host |
| `AGENT_NOTIFY_NAME` | Workspace name | Name that starts each message |
| `SPEAK_RELAY_PIPER_MODEL` | `~/.local/share/piper-voices/fr_FR-siwis-medium.onnx` | Default piper voice model; when it isn't installed, the first voice of `SPEAK_RELAY_PIPER_VOICES_DIR` is used |
| `SPEAK_RELAY_PIPER_VOICES_DIR` | `~/.local/share/piper-voices` | Folder of the piper voices that can be chosen from an agent |
| `SPEAK_RELAY_PIPER_SPEAKER` | First speaker (`0`) | Speaker id, for a model with several speakers (e.g. `1` for Pierre in `fr_FR-upmc-medium`, whose speaker `0` is Jessica); the ids are in the `speaker_id_map` of the model `.onnx.json` file |
| `SPEAK_RELAY_PIPER` | `~/.local/bin/piper` | piper executable |
| `SPEAK_RELAY_ESPEAK_VOICE` | `fr` | espeak-ng voice |
| `SPEAK_RELAY_MAX_AGE` | `600` | Age in seconds above which a message is skipped |

Two hooks of the dev profile speak a fixed text, set in the `.env` file, since the hooks run in the container:

| Variable | Default | Spoken when |
|---|---|---|
| `AGENT_NOTIFY_GREETING_TEXT` | `Bonjour, je suis prêt` | A new session starts (not on `--resume`, `/clear`, or a compaction). An empty value disables it |
| `AGENT_NOTIFY_PERMISSION_TEXT` | `Autorisation requise` | The agent waits for a permission |

A text can't contain an apostrophe (`'`): bash reads it as a quote in the hook command, which then fails.

The relay reads its settings when it starts: after a change, it's stopped with `claude_notify_speech_stop` (defined in `launch.sh`) and started again by the next launch of an agent.

### Choosing the voice

From a session of the dev profile, `/notif-speech-voices` lists the piper voices installed on the host, with their speakers, and the current voice; `/notif-speech-voice <voice> [<speaker>]` chooses one (the speaker is given by id or by name, e.g. `/notif-speech-voice fr_FR-upmc-medium pierre`), and `/notif-speech-voice default` goes back to the default voice of the host (`SPEAK_RELAY_PIPER_MODEL`, `SPEAK_RELAY_PIPER_SPEAKER`).

- The relay writes the list of the voices of `SPEAK_RELAY_PIPER_VOICES_DIR` to `.voices` in the speech queue folder, when it starts and whenever a voice is added or removed. The choice is written to `.voice` in the same folder, and read by the relay for each message: it applies at once, to every agent, and is kept until it's changed.
- Since `.voice` comes from the container, the relay only accepts a voice name (letters, digits, `_` and `-`) found in its voices folder, never a path: an invalid or missing voice falls back to the default one, with a line in the relay log.
- More generally, the relay never follows a symbolic link of the queue folder, so that the container can't make it read or write a file of the host: a message that isn't a regular file is ignored, and `.voices` is always written to a new file.
- A voice is installed on the host only (`install-speech-host.sh --voice <name>`), never from an agent. With espeak-ng instead of piper, the voice can't be chosen.

### Muting

From a session of the dev profile, `/notif-speech-off` mutes the speech notifications and `/notif-speech-on` unmutes them. The mute is global: `agent-notify --speech-off` writes a `.muted` file to the speech queue folder, shared by every agent of the person, and `agent-notify` queues nothing while it exists. It applies at once to the agents already running, covers the greeting and the permission prompts, and is kept until `/notif-speech-on`, also after a restart. The relay keeps running, idle.

`AGENT_NOTIFY_SPEECH` and the mute work at two levels: the first decides, at each launch, whether an agent is connected to the relay at all; the second silences every connected agent without disconnecting it.

### Check

- `pgrep -af speak-relay` shows a single relay
- `tail -f ~/.local/state/speak-relay.log` shows each message, and the skipped ones
- From `claude_dev_bash`: `agent-notify "Test"`

## Usage

| Command | Effect |
|---|---|
| `claude_dev` | Starts the agent in general development mode (dev profile), with the Confluence and Azure DevOps MCP servers available |
| `claude_dev_bash` | Opens a shell in the same environment, without starting Claude Code; useful for testing dotnet build, git status, and similar commands by hand |
| `claude_qualitycheck` | Starts the agent in review-only mode (qualitycheck profile, read-only, no MCP server) |
| `claude_notify_speech_stop` | Stops the speech relay of the speech notifications (see "Optional: speech notifications"); the next launch of an agent starts it again |

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

### Trusting a personal certificate

A certificate that only one person needs, for example the self-signed certificate of the MinIO used by a local run of the SpiGes backend, isn't added to `certs/`: it would be trusted in every image. It's placed in a folder under `$SHARED_BASE_DIR` instead (e.g. `backend-run/certs/`, in PEM format, `*.crt` or `*.pem`), and that folder is given in the `.env` file as `EXTRA_CA_CERTS_DIR` (container path, e.g. `/shared/backend-run/certs`). At each start, the entrypoint appends these certificates to a copy of the system bundle, used by .NET, curl, OpenSSL, and Python, and to a copy of the Node extra certificates. No rebuild is needed; a new or changed certificate is taken into account at the next start.

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
3. Add a function to `launch.sh`, next to the existing ones:
   ```bash
   claude_<name>(){
       _claude_agent <name> claude "$@"
   }
   ```

## Repository structure

```
bfs-claude-agent/
  Dockerfile
  entrypoint.sh
  launch.sh
  notifications/
    agent-notify      (copied into the image)
    speak-relay.sh    (run on the WSL2 host)
    install-speech-host.sh   (run once on the WSL2 host)
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

Nothing personal or secret lives inside this repository:
- `AGENT_HOMES_DIR` (default `$HOME/.claude-agent-homes`), one subfolder per profile, holds Claude Code's own state (session history, local configuration), plus a shared `CLAUDE.user.md` at its root (see "A personal preference" above).
- `AGENT_ENV_FILE` (default `$AGENT_HOMES_DIR/.env`) holds the real Claude Code token, the Git tokens, and, when GitHub is used, the agent account's GitHub API token and commit email; only `.env.example`, with placeholders, is committed here.
- `SHARED_BASE_DIR` (default `~/.agents/shared`) holds ad-hoc documents shared with the agent, outside any project repository.

## Troubleshooting

For any build, network, or permission issue found with this configuration, the full setup guide should be consulted; it documents the difficulties already encountered with this setup (enterprise certificates, the shared NuGet cache, file permissions, and so on) and how each one was solved. The two MCP appendices (Confluence, Azure DevOps) document the difficulties specific to those two integrations separately.
