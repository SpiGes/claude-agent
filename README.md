# bfs-claude-agent

Docker configuration that runs Claude Code as a disposable containerized agent, currently used on the SpiGes backend, frontend, and specs repositories, with several profiles (dev, qualitycheck) and optional MCP integrations (Confluence Data Center, Azure DevOps Server).

Claude Code is never installed directly on Windows, nor directly inside WSL2: it always runs inside its own disposable container, with access to the file system limited to whatever is explicitly mounted into it.

## Prerequisites

Before this repository can be used, the following must already be in place:

- Docker Desktop, with WSL2 integration enabled for the Ubuntu distribution (Settings, Resources, WSL Integration).
- WSL2 (Ubuntu), with the SpiGes repositories already cloned natively inside it (over HTTPS, not SSH), not on Windows (for performance reason).
- A Personal Access Token dedicated to Git operations against the on-premise Azure DevOps Server, scoped to Code (Read & Write) only — distinct from the read-only `DEVOPS_PAT` used by the Azure DevOps MCP server. See the full setup guide, Chapter 3, for how this token is used on WSL2; for the agent, it's stored in the secrets file of the secret broker (Step 6 below).
- A Claude.ai account with a subscription, from which the Claude Code token is generated (Step 4 below); a separate API key is not needed.
- Optional, only for repositories hosted on GitHub: a dedicated GitHub account for the agent, distinct from the person's own account (see "Optional: GitHub access" below).
- Optional, only for repositories hosted on the Bitbucket Data Center server: a Bitbucket HTTP access token (see "Optional: Bitbucket access" below).

If any of these points is not yet in place, the full setup guide ("Containerized Agent, Native WSL2 Development") should be consulted before continuing here.

## Installation

The minimum steps, all run from WSL2. The reasons behind them are given in "Installation details" below.

### 1. Clone this repository

```bash
git clone <url-of-this-repository> ~/.agents/bfs-claude-agent
```

### 2. Build the images

```bash
docker build -t bfs-claude-agent ~/.agents/bfs-claude-agent
(source ~/.agents/bfs-claude-agent/launch.sh && claude_services_build)
```
The first command builds the image of the agent, the second the images of the service containers (secret broker, MCP gateway, MCP servers, see "Service images"). The agent image is needed again after a change to the `Dockerfile`, to `entrypoint.sh`, to `notifications/agent-notify`, to the scripts under `hooks/`, to `broker/git_credential_broker.py`, `broker/mcp_call.py` or `broker/gh`, or to the certificates under `certs/`; the service images after a change under `services/` or `broker/`, to `notifications/agent-notify`, or to the certificates.

### 3. Create the personal folders

```bash
mkdir -p ~/.claude-agent-homes ~/.agents/shared
cp ~/.agents/bfs-claude-agent/.env.example ~/.claude-agent-homes/.env
```

### 4. Fill in the `.env` file

Generate the Claude Code token (a URL is shown, to be opened in a browser for login):
```bash
docker run -it --rm --entrypoint claude bfs-claude-agent setup-token
```
Then, in `~/.claude-agent-homes/.env`, replace the placeholders of:
- `CLAUDE_CODE_OAUTH_TOKEN`, with the generated token;
- `DEVOPS_COMMIT_NAME` and `DEVOPS_COMMIT_EMAIL`, with the authorship of the agent's commits on the Azure DevOps Server, chosen by each person (e.g. `Claude Agent (Firstname Lastname)` and `firstname.lastname+agent-dev@example.org`).

The GitHub and Bitbucket lines stay commented out unless "Optional: GitHub access" or "Optional: Bitbucket access" below is set up.

### 5. Load the launch functions

```bash
echo 'source ~/.agents/bfs-claude-agent/launch.sh' >> ~/.bashrc
source ~/.bashrc
```

### 6. Create the secrets file

```bash
claude_secret edit
```
The Git tokens are written in the secrets file of the secret broker, opened in an editor (see "Secret broker" below for the format): at least the Azure DevOps Git token from the Prerequisites. The file is then encrypted with a passphrase, asked twice.

### 7. Start the agent

```bash
cd <repository>
claude_dev
claude_secret allow git     # in another WSL2 terminal, when the agent needs Git
```
`claude_dev` starts the secret broker and asks for the passphrase when it's locked.

### Installation details

- **Personal state and secrets live outside this repository**, in `~/.claude-agent-homes` (`AGENT_HOMES_DIR`). Since the `.env` file is not inside the versioned folder at all, rather than a `.gitignore`-protected file within it, it is not exposed to a broad `git add -A`, or to a backup of the repository folder that does not respect ignore rules. It must never be committed or shared.
- **The folders of Step 3 are created by hand**, before the first launch, so that Docker does not create them itself with the wrong owner. For the same reason, the folder of a profile (its slots and its `projects` folder, see "Several agents at the same time") and the mount points inside a slot home are created by `launch.sh` before each `docker run`.
- **Git gets its tokens from the secret broker**, through a credential helper (see "Secret broker"), only while the `git` group is open: the agent container has no `.ssh` mount, no private key, no credential file, and no Git token in its environment. The full setup guide (Chapter 3) documents the one-time setup of the WSL2 host itself, needed for the person's own `git` operations outside the container.
- **`docker --env-file` keeps every character after `=` as is**, trailing spaces and Windows line endings (`\r`) included. A value copied with either of them may break silently.
- **The commit authorship is set per Git server**, through the same pair of variables for each one: `<PREFIX>_COMMIT_NAME` and `<PREFIX>_COMMIT_EMAIL`, with `DEVOPS` for the Azure DevOps Server, `GITHUB` for GitHub, and `BITBUCKET` for the Bitbucket Data Center server. At startup, `entrypoint.sh` writes each pair into `/tmp/gitconfig-<server>`, which the system Git configuration includes only for repositories whose remote is on that server. A server is in use when its pair is set (or, in a build container, when one of its `GIT_CONFIG_KEY_<n>` targets it). The container refuses to start, with an explicit message, when a pair of a server in use is incomplete, or when a value starts or ends with a space or a Windows line ending. Repositories on any other server keep the default `Claude Agent <claude-agent@spiges.local>` authorship.
- **Claude Code updates itself in its own npm prefix**, `/opt/claude-code`: `entrypoint.sh` runs `npm update -g` at each start, and Claude Code also updates itself during a session. `NPM_CONFIG_PREFIX` points npm there, and this folder is the only one installed by the image that the container user can write to; `/usr/local` stays owned by root. Its `bin` folder comes last in the `PATH`. An update lasts until the container stops (`--rm`); the next start updates again.
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

| Token | Repository permissions | Entry of the secrets file | Group |
|---|---|---|---|
| Git token | Contents: Read and write | `GIT_<n>_URL`, `GIT_<n>_USERNAME`, `GIT_<n>_PASSWORD` | `git` |
| API token | Issues: Read and write, Pull requests: Read and write, Contents: Read-only | `GH_<NAME>_TOKEN`, `GH_<NAME>_OWNER` | `gh` |

A fine-grained token belongs to a single resource owner: an agent working on the repositories of two organizations gets two Git tokens and two API tokens, one pair per organization.

Metadata: Read-only is added by GitHub automatically; every other permission, including Workflows, stays at No access. Without Workflows, a push that creates or changes a file under `.github/workflows/` is rejected, so any CI change goes through a person.

### Secrets file and `.env` file

In the secrets file (`claude_secret edit`, see "Secret broker"), plain tokens, no base64, one pair per organization:
```
GIT_2_URL=https://github.com/<organization>
GIT_2_USERNAME=x-access-token
GIT_2_PASSWORD=<GitHub Git token of the organization>
GH_<NAME>_TOKEN=<GitHub API token of the organization>
GH_<NAME>_OWNER=<organization>
```
In the `.env` file, the two GitHub lines of `.env.example` are uncommented:
```
GITHUB_COMMIT_NAME=<agent name, e.g. Claude Agent (Firstname Lastname)>
GITHUB_COMMIT_EMAIL=<ID>+<login>@users.noreply.github.com
```
- A Git token is given only for the URLs of its organization (`https://github.com/<organization>/...`, compared without regard to case), so a token never reaches another organization or server. Git sends the path of the repository for GitHub (`credential.https://github.com.useHttpPath`, in `/tmp/gitconfig-broker`).
- `GITHUB_COMMIT_NAME` and `GITHUB_COMMIT_EMAIL` are the authorship of the agent's commits on GitHub, handled like the Azure DevOps pair (see "Installation details"). The email is the agent account's noreply address, shown in its Settings, Emails when "Keep my email addresses private" is enabled; its numeric part is the account's permanent ID, which keeps commits linked to the account even after a rename. GitHub links commits to the account through the email only; the name is displayed as is.
- `gh` gets the API token of the organization from the broker for each call, through a wrapper (`/usr/local/bin/gh`), with no `gh auth login` needed. The wrapper finds the organization in `-R`/`--repo`, `GH_REPO`, a `gh api` path (`repos/<owner>/...`, `orgs/<owner>`), or the `origin` remote of the current repository; without any, the broker answers only when it holds a single API token. `gh` is deliberately not wired into Git (no `gh auth setup-git`), so that Git keeps using its own token only.

### Check

Once the container is started, with the `git` and `gh` groups open (`claude_secret allow git`, `claude_secret allow gh`), from `claude_dev_bash`:
```bash
gh auth status                                  # agent account, with the token of the broker
git -C <github-repo> push --dry-run origin HEAD # authenticates with the Git token
git -C <github-repo> config user.email          # the noreply address
```

## Optional: Bitbucket access

Only needed when the agent works on repositories hosted on the Bitbucket Data Center server (`bitbucket.bit.admin.ch`). There is no agent account on Bitbucket: the token is created by the person, and the agent acts with the permissions of that token. There is no API token either, only Git operations are covered.

### Token

A Bitbucket HTTP access token, created at the narrowest level the person's rights allow:
- Repository: Repository settings, HTTP access tokens (repository admin rights needed)
- Project: Project settings, HTTP access tokens (project admin rights needed)
- Personal: Manage account, HTTP access tokens (covers every repository the person can reach)

Its permission is Repository read, or Repository write when the agent pushes, and it gets an expiration date. Bitbucket shows the token only once, right after its creation.

### Remotes

The repositories are cloned over HTTPS (`https://bitbucket.bit.admin.ch/scm/<project>/<repository>.git`), not SSH: the container has no SSH key, and the token is only sent to HTTPS URLs. The URL never holds a user name or a token: they would be stored in clear in `.git/config`.

### Secrets file and `.env` file

In the secrets file (`claude_secret edit`, see "Secret broker"), one entry per token, with the exact URL of the remote for a repository token (`.git` included):
```
GIT_3_URL=https://bitbucket.bit.admin.ch/scm/<project>/<repository>.git
GIT_3_BEARER=<Bitbucket token>
```
In the `.env` file, the two Bitbucket lines of `.env.example` are uncommented:
```
BITBUCKET_COMMIT_NAME=<agent name, e.g. Claude Agent (Firstname Lastname)>
BITBUCKET_COMMIT_EMAIL=<agent email address>
```
- The broker gives the token whose URL is the longest prefix of the URL of the remote: a repository token is never sent to another repository, nor to another server. Git sends the path of the repository for Bitbucket only (`credential.https://bitbucket.bit.admin.ch.useHttpPath`, in `/tmp/gitconfig-broker`).
- Bitbucket accepts the token as a Bearer token (`GIT_<n>_BEARER`), so no user name is needed.
- `BITBUCKET_COMMIT_NAME` and `BITBUCKET_COMMIT_EMAIL` are the authorship of the agent's commits on Bitbucket, handled like the Azure DevOps pair (see "Installation details").

### Check

Once the container is started, with the `git` group open (`claude_secret allow git`), from `claude_dev_bash`:
```bash
git -C <bitbucket-repo> ls-remote --heads origin        # authenticates with the token
git -C <bitbucket-repo> push --dry-run origin HEAD      # only with Repository write
git -C <bitbucket-repo> config user.email               # BITBUCKET_COMMIT_EMAIL
```

## Optional: speech notifications

The agent tells the person, with a spoken message, when the requested work is finished or when it's blocked on a decision (e.g. "backend: the review of PR 44008 is finished, three points to check"). Intermediate steps, like a build or a test run, aren't announced. The instruction is given in the dev profile `CLAUDE.md`. Two hooks of its `settings.json` add a greeting when a new session starts, and announce the permission prompts, which the agent can't announce itself.

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
- Without access to the voice catalog (no network, wrong proxy), a voice already installed is only checked by the synthesis, with a warning; a missing voice can't be downloaded.

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

## Voice input

The person can dictate prompts with the voice dictation of Claude Code (`/voice`) instead of typing them. It's disabled by default, since it gives the container access to the microphone (see "Security" below), and enabled with `claude_voice_on` (`claude_voice_off` disables it again); WSLg must be active on the host. The choice is kept in `$AGENT_HOMES_DIR/voice-input`, for every terminal, and applies to the next launches of an agent: an agent already running keeps its microphone until it ends.

### How it works

- With `AGENT_VOICE_INPUT=1`, `launch.sh` mounts the WSLg audio socket (`/mnt/wslg/PulseServer`) into the container and sets `PULSE_SERVER`. Only the audio socket is mounted, not the whole `/mnt/wslg` folder, which also holds the display sockets. When the socket isn't found, `launch.sh` prints a warning and starts the agent without voice input.
- The image records through SoX and its PulseAudio backend (`AUDIODRIVER=pulseaudio`), since the container has no ALSA device.
- `launch.sh` enables the dictation with `claude --settings`: `/voice` and `/config` can't keep a setting, since the `settings.json` of the profile is mounted read-only. The mode and the language are therefore set with the variables below, not in a session.
- The audio is streamed to Anthropic for the transcription, as the prompts are; it isn't processed locally. The transcription doesn't count toward the usage limits.

### Usage

In tap mode (default), with an empty prompt: `Space` starts the recording, `Space` again stops it, and the prompt is sent at once when it has at least three words; a shorter transcript stays in the prompt. `Esc` cancels a recording. A recording also stops after 15 seconds of silence or two minutes. In hold mode, the recording runs while `Space` is held, and the transcript waits for `Enter`.

### Settings

Exported in `~/.bashrc`, before `launch.sh` is sourced:

| Variable | Default | Effect |
|---|---|---|
| `AGENT_VOICE_INPUT` | The choice of `claude_voice_on` / `claude_voice_off`, else `0` | `1` enables the voice input: the audio socket is mounted. With `0`, no audio socket is mounted. An exported value takes precedence over `claude_voice_on` / `claude_voice_off` |
| `AGENT_VOICE_MODE` | `tap` | `tap` or `hold` |
| `AGENT_VOICE_LANGUAGE` | `french` | Dictation language (name or code, e.g. `german`, `de`). It also sets the response language of Claude Code |

### Security

With the voice input enabled, every process of the container can record the microphone for the whole session, not only the dictation of Claude Code, e.g. during a Teams meeting: muting the microphone in Teams doesn't mute it for the other applications. The dev profile `CLAUDE.md` forbids the agent to record it with its own commands, and `settings.json` denies the usual recording commands (`rec`, `sox`, `arecord`, `parec`, `parecord`); a script could still bypass these rules, so an unmounted audio socket (`AGENT_VOICE_INPUT=0`, the default) is the only real barrier. With the voice input enabled, the agent is better closed, or the microphone muted at the system or headset level, during a sensitive meeting.

### Troubleshooting

- `Voice mode could not find a working audio recorder`: the audio socket isn't mounted, e.g. the agent wasn't started from WSL2, or `launch.sh` printed a warning.
- The commands of this section are run in the WSL2 terminal, not in Git Bash: Git Bash turns `/mnt/wslg` into a Windows path, and the mount is then empty.

## Usage

| Command | Effect |
|---|---|
| `claude_dev` | Starts the agent in general development mode (dev profile), with the Confluence and Azure DevOps MCP servers available |
| `claude_dev_bash` | Opens a shell in the same environment, without starting Claude Code; useful for testing dotnet build, git status, and similar commands by hand |
| `claude_qualitycheck` | Starts the agent in review-only mode (qualitycheck profile, read-only, no MCP server) |
| `claude_notify_speech_stop` | Stops the speech relay of the speech notifications (see "Optional: speech notifications"); the next launch of an agent starts it again |
| `claude_voice_on` / `claude_voice_off` | Enables or disables the voice input (microphone) for the next launches of an agent (see "Voice input") |
| `claude_broker` / `claude_broker_stop` | Starts and unlocks the secret broker, or stops it (see "Secret broker"); `claude_dev` starts it when needed |
| `claude_secret allow <group>` | Opens a group: `git`, `gh` (secret broker), `devops` (MCP gateway); also `revoke <group>\|all`, `status`, `pending`, `approve`, `deny`, `log`, `edit` (see "Secret broker", "MCP gateway") |
| `claude_gateway` / `claude_gateway_stop` | Starts or stops the MCP gateway (see "MCP gateway") |
| `claude_services_build [--refresh]` | Builds the images of the service containers; `--refresh` fetches the base image and the Ubuntu packages again (see "Service images") |
| `claude_mcp start\|stop\|restart <server>` | Starts, stops, or restarts the container of an MCP server (`devops`, `jira`, `confluence`), with its token from the secret broker; `start` returns once the server accepts connections (see "MCP gateway") |

Each command starts a new container, removed on exit (--rm); nothing needs to be stopped or cleaned up by hand. Several agents can run at the same time, see "Several agents at the same time" below.

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

## Secret broker

The Git tokens and the GitHub API token aren't given to the agent container at startup. They're kept by a secret broker, a container of its own (`claude-broker`), which hands them out only while their group is open. A group is opened by the person, on the host; the agent can't open one.

### How it works

- **Encrypted secrets file**: `$AGENT_VAULT_DIR/secrets.age` (default `~/.claude-agent-homes/vault`), encrypted with `age` and a passphrase. It's decrypted only inside the broker container, into its memory: the clear text never reaches a disk. `claude_broker` asks for the passphrase when the broker is locked; `claude_dev` calls it at each start.
- **A separate container**: the broker runs from the `broker` service image (see "Service images"), with no capability, no privilege escalation, a read-only file system, the secrets file mounted read-only, and neither the workspace nor the NuGet cache mounted. The agent can't read its memory or its environment, and nothing the agent can change runs in it. It keeps running between the agent sessions, and is locked again when it restarts. After its image is rebuilt, `claude_broker` (and so `claude_dev`) restarts it on the new image, and asks for the passphrase again.
- **Docker network**: the agent containers and the broker share the network `AGENT_NETWORK` (default `bfs-claude-agent`); the agent reaches the broker at `http://claude-broker:8080` (`BROKER_URL`). A container outside this network, e.g. a build container, can't reach it.
- **Groups**: `git` (the Git tokens) and `gh` (the GitHub API token). A group is opened with `claude_secret allow <group>`, which runs the command in the broker with `docker exec`: the agent container has no access to Docker, so it can't open a group by itself. A group closes `BROKER_IDLE_MINUTES` (default 30) after its last use, and `BROKER_MAX_HOURS` (default 4) after it was opened, whichever comes first. Both can be exported in `~/.bashrc`, and apply at the next start of the broker.
- **Clients in the agent container**: Git asks the broker through a credential helper (`git-credential-broker`) when a server requires authentication, and before every request to the Azure DevOps Server, which gets Basic authentication right away (it also offers NTLM, which curl would prefer and which fails with a PAT); `gh` through a wrapper (`/usr/local/bin/gh`) that gets the token for each call. The Git settings are written at startup into `/tmp/gitconfig-broker`, included by the system configuration of the image, only when `BROKER_URL` is set: a container of the image without broker (e.g. a build container) keeps its own Git settings, and the wrapper of `gh` runs the real `gh` as is.
- **Refusals**: when the broker is locked or the group is closed, Git and `gh` fail with a message giving the command to run on the host, and a spoken notification is sent ("courtier: Autorisation requise pour git"), at most once every two minutes.
- **Log**: every request and every command is logged to `$AGENT_BROKER_LOG_DIR/broker.log` (default `~/.claude-agent-homes/broker`), with the URL and the name of the entry, never a secret value. Above 1 MB, the log becomes `broker.log.1` (replacing the previous one).

### Secrets file

Edited with `claude_secret edit`: a disposable container without network decrypts the file to a tmpfs, opens it in `nano`, and encrypts it again with the same passphrase. The passphrase is asked once, on the host (twice for a new file), and handed to `age` through pipes only (`age-passphrase`, since `age` reads a passphrase from a terminal only); a running broker is then reloaded with the new file, without asking it again. The first time, it starts from `broker/secrets.example`. The container ends by itself after 30 minutes, so that an interrupted edit doesn't leave the clear text in its tmpfs.

One `KEY=VALUE` per line, as a docker env file (no quotes, no space around `=`), plain tokens, no base64:

| Entry | Meaning |
|---|---|
| `GIT_<n>_URL` | `https://` prefix of the remotes the token is for, compared without regard to case; the longest prefix that matches the URL of a remote wins |
| `GIT_<n>_PASSWORD`, `GIT_<n>_USERNAME` | Token sent with Basic authentication (Azure DevOps, GitHub); the user name defaults to `token` (`x-access-token` for GitHub) |
| `GIT_<n>_BEARER` | Token sent as a Bearer token (Bitbucket) |
| `GH_<NAME>_TOKEN`, `GH_<NAME>_OWNER` | GitHub API token of `gh` for one organization or account (e.g. `GH_SPIGES_TOKEN` and `GH_SPIGES_OWNER=SpiGes`) |
| `GH_TOKEN` | GitHub API token of `gh` for any other owner (optional) |
| any other key, e.g. `DEVOPS_PAT` | Secret of type 2: never handed out on the network, only exported to the host (`claude-broker ctl export`) for the container of an MCP server (see "MCP gateway") |

### Usage

```bash
claude_secret allow git       # opens the git group
claude_secret status          # state of the broker and time left for each group
claude_secret revoke all      # closes every group
claude_secret log             # end of the log
claude_broker_stop            # stops the broker: secrets forgotten
```

### Moving the tokens out of `.env`

For an installation from before the broker: the Git tokens of `.env` (`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`) and `GH_TOKEN` are written in the secrets file (`claude_secret edit`), as plain tokens (the `GIT_CONFIG_VALUE_<n>` lines hold them base64-encoded, with a user name for Basic), then removed from `.env`. A GitHub token gets the URL of its organization (`GIT_<n>_URL`, `GH_<NAME>_OWNER`). During the move, the headers of `.env` are sent first to GitHub and Bitbucket, and the broker is asked only when a server refuses them; the Azure DevOps Server asks the broker for every operation, so the `git` group stays open while the tokens are moved. The `<PREFIX>_COMMIT_NAME` and `<PREFIX>_COMMIT_EMAIL` pairs stay in `.env`. The image is rebuilt (Step 2 of Installation).

### Limits

- While a group is open, any process of the agent container can ask the broker for its tokens, like Git does: the broker limits how long the tokens are reachable, not who asks within the container.
- A token given to Git or `gh` is in the memory of that process while it runs.
- The broker and the secrets file are protected from the agent container, not from a compromised WSL2 or Windows.

## MCP gateway

The MCP servers of Azure DevOps, Jira, and Confluence don't run in the agent container, and their tokens (`DEVOPS_PAT`, `JIRA_PERSONAL_TOKEN`, `CONFLUENCE_PERSONAL_TOKEN`) aren't in the environment of the agent. Each one runs in a container of its own, with its token only, and the agent reaches them through a gateway that checks every tool call. The same goes for the services written for the agent, as small MCP servers (`broker/mcp_server.py`, standard library only): Keycloak, whose client secrets (`SPIGES_KEYCLOAK_*_CLIENT_SECRET`) only give the skills short-lived tokens, SAS, whose account (`SPIGES_SAS_REF_SSH_USER`, `SPIGES_SAS_REF_SSH_PASSWORD`) only serves the commands of the `sas-ref` skill on the SAS server of REF, and Oracle, whose accounts (`SPIGES_ORACLE_REF_*`, `SPIGES_ORACLE_TST_*`) only serve the checked statements of the `spiges-sql` skill.

```
agent container ──► claude-gateway ──► claude-mcp-devops     (DEVOPS_PAT)                ──► Azure DevOps Server
                   (groups,         ──► claude-mcp-jira       (JIRA_PERSONAL_TOKEN)       ──► Jira Data Center
                    approvals)      ──► claude-mcp-confluence (CONFLUENCE_PERSONAL_TOKEN) ──► Confluence Data Center
                                    ──► claude-mcp-keycloak   (Keycloak client secrets)   ──► Keycloak (REF, TST)
                                    ──► claude-mcp-sas        (SAS account of REF)        ──► SAS server (REF)
                                    ──► claude-mcp-oracle     (Oracle accounts REF, TST)  ──► Oracle (REF, TST), Mediator, DAL
                                        (each on a network of its own, shared with the gateway only)
```

| Server | Group | Tools |
|---|---|---|
| `devops` (`mcp-devops-onpremise`) | `devops` | Read tools go through while the group is open; writes need an approval; deletion, wiki, and repository tools are refused |
| `jira` (`mcp-atlassian --read-only`) | `jira` | Read only: read tools go through while the group is open; any other tool is refused, without approval |
| `confluence` (`mcp-atlassian`) | `confluence` | Read tools go through while the group is open; writes need an approval; page deletion, page moves, and restriction changes are refused |
| `keycloak` (`claude-mcp-keycloak`) | `keycloak` | Read only: `keycloak_token` (short-lived token of a profile) and `keycloak_profiles` go through while the group is open; called by the skill scripts through `mcp-call`, not by Claude Code, so that a token never reaches the conversation |
| `sas` (`claude-mcp-sas`) | `sas` | `sas_get` (copy of a file of the server to the drop folder) goes through while the group is open; `sas_exec`, `sas_put`, and `sas_run` need an approval |
| `oracle` (`claude-mcp-oracle`) | `oracle` | `oracle_query` (one read-only query) goes through while the group is open; `oracle_write` and `oracle_processing_run` need an approval, with the statement shown on the host; called by the `spiges-sql` skill through `mcp-call` |

- **mcp-call**: `mcp-call <server> <tool> [<arguments as JSON>]` calls one tool through the gateway from a script, and prints the text of its result (tool error: exit code 1, on stderr). A skill uses it when the result mustn't reach the conversation, e.g. `token=$(mcp-call keycloak keycloak_token '{"profile": "bfs"}')`. It waits 180 seconds for the answer, or `MCP_CALL_TIMEOUT_SECONDS` (at most 3600), e.g. for a call that runs for minutes after its approval.
- **Oracle server**: it holds the accounts of the REF and TST SpiGes Oracle databases, and the secret of the technical SIS client (`SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET`, also in the Keycloak server) for the processing areas. It runs `oracledb` in thick mode, on Oracle Instant Client Basic Light (`/opt/oracle/instantclient`, official download pinned by its SHA-256 in `services/Dockerfile`, free to use and to redistribute under the Oracle Free Use Terms and Conditions), since the SpiGes databases require the native network encryption of Oracle, which the thin mode doesn't support; `oracledb` and its dependencies are in a virtual environment of the `oracle` service image (`/opt/claude-mcp-oracle`, pinned with their hashes in `services/oracle/requirements.txt`). Its controls don't depend on the client: targets `ref` and `tst` only; right after the connection, the service must be `EDWHPDSRV` (non-production), otherwise nothing runs; `oracle_query` accepts one `SELECT` or `WITH` (no `INTO`, `FOR UPDATE`, or inline PL/SQL), run in a read-only transaction that is rolled back; `oracle_write` accepts one `INSERT`, `UPDATE` (with `WHERE`) or `DELETE` (with `WHERE`), committed only when it touches the expected number of rows; `oracle_processing_run` recreates the processing area of a run (Mediator), reads its connection (DAL), runs one read-only query on it, and always drops it, so that the connection of the area never leaves the server. A function with side effects called from a query isn't stopped: only the rights of the account can. The sessions show `claude-agent` in `V$SESSION`.
- **Keycloak server**: it reads the profiles from the clients file (`<profile>=<client id>` lines, no secret), `$AGENT_KEYCLOAK_CLIENTS_FILE` (default `$AGENT_HOMES_DIR/keycloak-clients.txt`), mounted read only: after a change of the file, `claude_mcp restart keycloak`. The file is the list of the clients the agent may get a token for, and the user clients share one secret: it's kept out of the folders mounted in the agent container (not under `/shared`), so that the agent can't add a client to it. The secret of a profile is `SPIGES_KEYCLOAK_<PROFILE>_CLIENT_SECRET`, or else the shared `SPIGES_KEYCLOAK_USER_CLIENT_SECRET`. A token is cached until one minute before its expiry.
- **SAS server**: it runs `ssh` and `scp` to the SAS server of REF (`pssas10i02` by default, or `sas94`, the new server of the migration; hosts and REF SAS command fixed in `broker/claude_mcp_sas.py`), with the account of the secrets file. The host keys are pinned in `$AGENT_SAS_KNOWN_HOSTS_FILE` (default `$AGENT_HOMES_DIR/sas-known-hosts`, a `known_hosts` file), mounted read only and kept out of the folders mounted in the agent container, so that the agent can't change them. The password reaches `ssh` through `SSH_ASKPASS` only, never on a command line. After a rejected login, the server refuses every next call until `claude_mcp restart sas`: the account is a technical account shared with the SpiGes backend, which repeated failures could lock. The remote paths are relative to `~/agent`, without `..`; the local files are read and written only in the drop folder `/shared/mcp-sas` (`$AGENT_MCP_SAS_DIR`, default `$SHARED_BASE_DIR/mcp-sas`), mounted at the same path as in the agent. The local files are never opened by `ssh` or `scp`: the server opens each one itself without following a link, checks the real path of the open file, and copies it through a private temporary folder, so that a link placed in the drop folder meanwhile can't make `scp` read or write another file of the container. A command or a SAS program can run for 14 minutes at most; with the approval time, the `sas` server of `mcp.json` sets `"timeout": 1020000` (17 minutes).

### How it works

- **Containers without coupling**: `claude_gateway` starts the gateway, `claude_mcp start <server>` an MCP server; neither starts the other, and `claude_dev` starts neither. A missing container only makes the calls that need it fail, with the command to run. After a restart of Docker, `claude_mcp start <server>` is run again for each server (the gateway restarts by itself, with every group closed). `claude_gateway` restarts a gateway started from an older image or with other routes, and `claude_mcp start` a server started from an older image.
- **Networks**: the gateway is on the network of the agents (`http://claude-gateway:8090/mcp/<server>`, the servers of `profiles/dev/mcp.json`). Each MCP server has a Docker network of its own, `<AGENT_MCP_NETWORK_PREFIX>-<server>` (default `bfs-claude-agent-mcp-<server>`), whose only members are its container and the gateway; the server reaches its remote service through it. The MCP servers have no authentication: Docker isolates the networks from each other, so neither the agent, nor another MCP container, nor a container started without `--network` reaches them. `claude_mcp start` creates the network and connects the gateway when it runs; `claude_gateway` connects to the networks created before it.
- **Tokens**: the tokens are secrets of type 2 of the secrets file of the broker. The broker never hands them out on the network: `claude_mcp start` reads the secrets of the server through `docker exec` (`claude-broker ctl export`, one or several per server), and writes them as `KEY=value` lines straight into the tmpfs of the MCP container, through a pipe between two `docker exec`. The server reads them and the file is removed: they're neither on a disk nor in the configuration of the container (`docker inspect`), only in the memory of the server.
- **Jira and Confluence servers**: `mcp-atlassian` runs one service per container, so that each container holds one token. Over HTTP, it refuses by default a request without credentials of its own, so that an unknown caller doesn't act with the token of the server: `ALLOW_GLOBAL_CRED_FALLBACK=true` lets it serve the gateway, which is the only one to reach it and forwards no credential header (only the headers of the MCP transport go through). The token reaches `mcp-atlassian` through a file of settings (`--env-file` in the tmpfs, removed by `claude_mcp start` once the server listens), never through its environment: the initial environment of a process (`/proc/self/environ`) is a file, which a tool that reads files could reach.
- **Confluence drop folder**: the page body (`content_file`) and the attachments (`file_path`, `file_paths`) are read by the Confluence server from files. It only reads them under its working folder, `/shared/mcp-confluence` (`$AGENT_MCP_CONFLUENCE_DIR`, default `$SHARED_BASE_DIR/mcp-confluence`), mounted read only at the same path as in the agent: a path outside it is refused by the server, symbolic links included. The `confluence-publish` skill copies the files there before the publication, and they can be opened on the host before an approval.
- **Requests that always go through**: the opening of the session (`initialize`, notifications, `tools/list`), the stream of the server (`GET`), and the end of the session (`DELETE`), so that Claude Code lists the tools from its start. The streamed answers (server-sent events) are forwarded as they come.
- **Tool calls**: a tool refused by the gateway (the deny rules of `settings.json`, and every write of Jira) is removed from the answer of `tools/list`, and refused if it's called anyway; a read tool (an explicit list per server in `broker/claude_gateway.py`) goes through while the group of the server is open; **any other tool, including an unknown or new one, is a write**: it goes through while the group is open, and only once approved on the host. A refusal reaches the agent as a tool error, with the command to run.
- **Approvals**: a write call waits, with a spoken notification ("passerelle: Écriture DevOps à approuver pour agent dev 2 : <tool>"). The agent that asks for it is named after its container (`claude-agent-<profile>-<slot>`, found by the gateway through the Docker DNS), in the notification, in `pending`, and in the log, since several agents can share the gateway (see "Several agents at the same time"). `claude_secret approve` approves the oldest waiting call (`approve <id>` another one); `claude_secret pending` lists them with their full arguments, e.g. the text of a comment, so that the approval is given on what is written (an encoded file, `content_base64`, is shown by its length). `claude_secret approve --tool 5m` also approves the next calls of the same tool by the same agent for 5 minutes, whatever their arguments, e.g. the comments of a review; closing the group (`revoke`, idle or maximum time) ends it; `claude_secret deny` refuses. Without a decision within `GATEWAY_APPROVAL_SECONDS` (default 120), the call is refused. A call that the agent gave up meanwhile (e.g. Esc in Claude Code) isn't forwarded, even once approved. The gateway answers only after the decision, so each server of `mcp.json` sets `"timeout": 180000` (milliseconds): without it, Claude Code gives up an HTTP request after 60 seconds, while the call can still be approved and then run without the agent knowing it. The `timeout` of a server covers `GATEWAY_APPROVAL_SECONDS`, the longest run of a tool, and a margin: a longer `GATEWAY_APPROVAL_SECONDS` needs a longer `timeout`. Claude Code still asks its own confirmation before most writes: it guards against the agent itself, the approval of the gateway against any process of the agent container that would call an MCP server without Claude Code.
- **Groups**: the gateway holds the `devops`, `jira`, and `confluence` groups itself, with the idle and maximum times of the broker (`BROKER_IDLE_MINUTES`, `BROKER_MAX_HOURS`). For these groups, opening a window doesn't hand any token to the agent: it lets the gateway forward the tool calls of the agent.
- **Hardened containers**: no capability, no privilege escalation, read-only file system, no workspace: the Confluence server only gets its drop folder, read only; the SAS server its drop folder, read and write, and its known hosts file, read only. The gateway holds no secret.
- **Log**: every tool call (server, tool, main arguments, decision) to `$AGENT_GATEWAY_LOG_DIR/gateway.log` (default `~/.claude-agent-homes/gateway`), never the content written (text of a comment or page, field values, SQL statements) nor a secret value.

### Setup

1. The tokens are added to the secrets file (`claude_secret edit`: `DEVOPS_PAT`, `JIRA_PERSONAL_TOKEN`, `CONFLUENCE_PERSONAL_TOKEN`, `SPIGES_KEYCLOAK_USER_CLIENT_SECRET`, `SPIGES_KEYCLOAK_SAS_CLIENT_SECRET`, `SPIGES_KEYCLOAK_SPIGES_CLIENT_SECRET`, `SPIGES_SAS_REF_SSH_USER`, `SPIGES_SAS_REF_SSH_PASSWORD`, `SPIGES_ORACLE_REF_USER`, `SPIGES_ORACLE_REF_PASSWORD`, `SPIGES_ORACLE_REF_DATASOURCE`, and the same three for `TST`), and removed from `.env`.
2. The Keycloak clients file is placed at `$AGENT_KEYCLOAK_CLIENTS_FILE` (default `~/.claude-agent-homes/keycloak-clients.txt`, on WSL), and the pinned host keys of the SAS servers at `$AGENT_SAS_KNOWN_HOSTS_FILE` (default `~/.claude-agent-homes/sas-known-hosts`).
3. The service images are rebuilt (`claude_services_build`), then: `claude_broker` (unlock), `claude_gateway`, and `claude_mcp start <server>` for each server.
4. A workspace that declares the Jira server in its own `.mcp.json` points it to the gateway: `"jira": {"type": "http", "url": "http://claude-gateway:8090/mcp/jira", "timeout": 180000}`.
5. `claude_dev`; when the gateway or an MCP container started after the agent, `/mcp` in Claude Code reconnects the server.

### Usage

```bash
claude_secret allow devops          # the agent may call the Azure DevOps tools (also: jira, confluence)
claude_secret pending               # waiting write calls, with their full arguments
claude_secret approve               # approves the oldest one
claude_secret approve --tool 5m     # ... and the next calls of the same tool by the same agent for 5 minutes
claude_secret deny                  # refuses the oldest one
claude_mcp restart confluence       # e.g. after a new token in the secrets file
```

## Service images

The secret broker, the MCP gateway and the MCP servers run from images of their own, built from `services/Dockerfile`, not from the image of the agent. Each container only holds what it runs: no .NET SDK, Node, Git, or `gh` next to the secrets, no tool of a service in the agent, and a rebuild of the agent image doesn't restart the services.

| Image (`bfs-claude-services:<target>`) | Content | Containers |
|---|---|---|
| `base` | Python 3.12 of Ubuntu 24.04, BIT certificates, modules of `broker/`, `agent-notify` | `claude-gateway`, `claude-mcp-keycloak` |
| `broker` | `base` + `age`, `nano` | `claude-broker`, the container of `claude_secret edit` |
| `sas` | `base` + OpenSSH client | `claude-mcp-sas` |
| `oracle` | `base` + Oracle Instant Client, `oracledb` | `claude-mcp-oracle` |
| `atlassian` | `base` + `mcp-atlassian` | `claude-mcp-jira`, `claude-mcp-confluence` |
| `devops` | `base` + `mcp-devops-onpremise` | `claude-mcp-devops` |

`claude_services_build` builds every target, from the clone of this repository, and labels each image with the commit it comes from (`org.opencontainers.image.revision`). `claude_broker`, `claude_gateway` and `claude_mcp start` refuse to start when the image of their target is missing, and restart a container that runs an older image of its target. `claude_services_build --refresh` fetches the base image and runs every step again (`--pull --no-cache`), so that the Ubuntu packages get their latest security updates; it's run once a month, and when an advisory concerns a package of Ubuntu.

How the images are built:

- **Pinned inputs**: the base image by tag and digest (`ubuntu:noble@sha256:...`), the Python packages by version and hash, transitive dependencies included (`services/<target>/requirements.txt`, see "Dependencies"), Oracle Instant Client by URL and SHA-256. The Ubuntu packages come from the signed archive of Ubuntu.
- **Multi-stage**: the virtual environments are built in a stage of their own, with `pip install --require-hashes --only-binary=:all: --no-deps` (wheels only, every file checked against its hash, no package outside the lock), then `pip` is removed from them and they're copied into their target. `curl` and `unzip` stay in the download stage of Instant Client. No compiler, `pip` cache or package list reaches an image.
- **Unprivileged**: the default user of the images is `service` (uid 10001, no login, no home); `launch.sh` runs every container with the account of the person, without capability, without privilege escalation, with a read-only file system and a tmpfs for `/tmp`.
- **No secret in an image**: the secrets reach the running containers only, from the secret broker (see "MCP gateway"); no build argument holds one.
- **Small build context**: `.dockerignore` is an allow list of the files that the two Dockerfiles copy; the `.git` folder, `.env`, the profiles and the tests never reach the build.
- **Build checks**: `services/Dockerfile` starts with `# check=error=true`, so that a warning of the Docker build checks fails the build.

## Dependencies

The Python packages of the service images are locked per target, in `services/<target>/requirements.txt`, generated with `pip-compile` (pip-tools) from `services/<target>/requirements.in`, which names the direct packages only. A lock holds the exact version and the hashes of every package, transitive ones included.

A new release is adopted once it's at least 14 days old: a release replaced on purpose (e.g. after a compromised publication) is usually found and withdrawn within days. A release that fixes a security advisory is adopted at once, when it's published with an attestation of its origin (trusted publishing on PyPI) and no advisory concerns it; it's then pinned in the `requirements.in` of the target, with the advisory it fixes. The release notes are read before a version is chosen.

A lock is updated in the virtual environment of the clone (see "Development"), where `pip-tools` is installed:

```bash
cd services/<target>
../../.venv/bin/pip-compile --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url \
  --pip-args "--only-binary=:all: --uploaded-prior-to=P14D" -o requirements.txt requirements.in
../../.venv/bin/pip-audit --require-hashes --disable-pip -r requirements.txt
```

`--uploaded-prior-to=P14D` applies the 14 days to every package of the lock, transitive ones included. `pip-audit` checks each version against the advisories of PyPI and OSV; an advisory is checked against the code that runs (is the vulnerable function reached by the service?) before a version is kept. Then the images are rebuilt and the services tested (see "MCP gateway", Setup). An update of `mcp-atlassian` or `mcp-devops-onpremise` also checks the names of their tools: the policies of the gateway (`broker/claude_gateway.py`) and the `deny` and `ask` rules of `profiles/dev/settings.json` refer to them, and a renamed tool would escape them.

## Several agents at the same time

Up to `AGENT_MAX_SLOTS` agents of one profile (default `3`, can be exported in `~/.bashrc` before `launch.sh` is sourced) can run at the same time, usually in the same workspace (e.g. `~/workspace/spiges`), each one in its own worktree for its task. They share one memory, and none of them loses a change written by another one.

### How it works

- **One slot per agent**: `launch.sh` takes the first free slot of the profile, through a lock (`flock -n`) on `$AGENT_HOMES_DIR/<profile>/slots/<n>.lock`, held until the container ends, also after a Ctrl+C. When every slot is in use, the launch stops with a message.
- **One home per slot**: `$AGENT_HOMES_DIR/<profile>/slots/<n>` is mounted as `/root`, so that Claude Code's own state (`.claude.json`, `sessions/`, `history.jsonl`, and so on) is never written by two agents at the same time. A slot home is created on the first launch of the slot, from the `.claude.json` and the plugins of slot 1, so that the onboarding isn't run again.
- **One `projects` folder per profile**: `$AGENT_HOMES_DIR/<profile>/projects` is mounted as `/root/.claude/projects` in every slot. It holds the memory and the session transcripts, so that every agent reads and writes the same memory, and a session can be resumed from any slot. The workspace is always mounted as `/workspace`, so the memory folder is the same whatever the workspace of the host.
- **Memory changes reach the running agents**: Claude Code loads `MEMORY.md` only when a session starts, is resumed, or is cleared. Two hooks of the dev profile (`hooks/agent-memory-sync`) keep a copy of it per session and, at each prompt, add the lines changed since the previous prompt to the context of the agent.
- **No memory change is lost**: Edit and Write refuse to overwrite a file changed since it was read; a shell command has no such check. A hook of the dev profile (`hooks/agent-memory-guard`) refuses the usual shell commands that write into the memory folder (redirection, `sed -i`, `tee`, `mv`, `cp`, a script interpreter), and the dev profile `CLAUDE.md` asks the agent to write memory files only with Edit and Write. Deleting a memory file (`rm`) stays allowed.
- **Agents can be told apart**: the slot is given to the container as `AGENT_SLOT`, and beyond slot 1 the notification name ends with the slot number (e.g. "spiges 2"). The container is named `claude-agent-<profile>-<slot>`, so that the MCP gateway names the agent that asks for a write (e.g. "agent dev 2").
- **`claude_dev_bash` also takes a slot**, since its shell can write to the home: with every slot in use, it doesn't start either.

### Limits

- Only the lines of `MEMORY.md` are sent to the running agents: a change in the content of a memory file that leaves its line unchanged is seen when the file is read, or at the next session.
- The memory hook is a heuristic on the text of the command: it doesn't catch every possible write (e.g. a relative path after a `cd` into the memory folder), and it also refuses a `cp` from the memory folder to another one.
- Two agents can still save the same fact at nearly the same time; the rule of reading `MEMORY.md` again before creating a memory makes it unlikely.
- The rewind history (`file-history/`) belongs to the slot: a session resumed from another slot can't rewind the changes made before.
- Services of the host, e.g. a local Postgres, are shared by every container.
- The secret broker and the MCP gateway are shared by every agent: a group opened with `claude_secret allow` is open for every agent (`claude_secret approve --tool` only covers the agent of the approved call).
- A restart of the MCP gateway (e.g. `claude_gateway` after a rebuild) ends the MCP sessions of every agent: each one reconnects its servers with `/mcp`.
- Two agents that publish the same document to Confluence at the same time share its folder in the drop folder of the Confluence server (`/shared/mcp-confluence/<document name>/`). The same goes for the drop folder of the SAS server (`/shared/mcp-sas`) and for `~/agent` on the SAS server.

### Upgrading to the slots

An installation from before the slots is upgraded once, in this order:

1. **Every agent is closed**: `docker ps --filter ancestor=bfs-claude-agent` must list no container.
2. **The repository is updated** (`git pull` in `~/.agents/bfs-claude-agent`) and **the image is rebuilt** (Step 2 of Installation). The hooks of the dev profile are read from the repository, but their scripts are only in the image: without the rebuild, every prompt and every shell command of the agent shows a hook error (`command not found`).
3. **The home of each profile already used is moved to slot 1**, so that it keeps its memory, its sessions, and its Claude Code state. Until then, `launch.sh` refuses to start an agent of that profile, with a message pointing here. For the dev profile (the same with `qualitycheck` for that profile):
   ```bash
   cd ~/.claude-agent-homes
   cp -a dev dev.backup                             # removed once the agents work
   mv dev dev.slot1
   mkdir -p dev/slots
   mv dev.slot1 dev/slots/1
   mv dev/slots/1/.claude/projects dev/projects
   mkdir dev/slots/1/.claude/projects
   ```
4. **The launch functions are loaded again** in each open terminal (`source ~/.bashrc`, or a new terminal).

## Available profiles

- dev (`profiles/dev/`): full read and write access; `git push` (including `--force-with-lease`), `git rebase`, and `git reset --keep` ask for confirmation each time, and `git push --force`/`-f` and `git reset --hard` are denied. The read-only dependency checks of the SDKs are allowed (`dotnet list`, `dotnet nuget why`, `npm audit --package-lock-only`, `npm explain`), and `npm audit fix` is denied. Declares an `mcp.json`, so the Confluence and Azure DevOps MCP servers are available in this profile (see the two MCP appendices in the specs repository). Meant for everyday development work.
- qualitycheck (`profiles/qualitycheck/`): read-only; cannot technically edit a file or commit, enforced through settings.json rather than only requested. Declares no `mcp.json`. Meant for reviewing code with no risk of an accidental change.

A profile is made of two required files, and optional context folders:
- `CLAUDE.md`: describes the role and the project context, and ends with `@CLAUDE.user.md` so a personal addition is always possible on top of it.
- `settings.json`: enforces the actual technical permissions (`deny`/`ask`/`allow`).
- `mcp.json` (optional): declares MCP servers for that profile; whether this file exists is the only thing that decides whether it gets mounted, with no other configuration needed.
- `.claude/rules/`, `.claude/skills/`, `.claude/docs/` (optional): mounted automatically when present.

Changing a profile does not require rebuilding the image (docker build); these files are mounted read-only at every launch, not copied into the image. A plain `git pull` on this repository is enough to apply an update at the next launch.

### Optional MCP integrations (dev profile)

The dev profile's `mcp.json` currently declares three MCP servers; confluence and devops are documented in their own appendix in the specs repository:

- **confluence**: reads and writes Confluence Data Center pages directly from Markdown, instead of the manual copy-paste workflow. Its Personal Access Token cannot be scoped down and keeps the full rights of the account that created it, so `settings.json` adds a compensating boundary: page deletion, page moves, and restriction changes are denied outright, and every other write (create, update, add a comment or attachment, and so on) asks for confirmation first. It doesn't run in the agent container either: the agent reaches it through the MCP gateway (see "MCP gateway"), which refuses the same tools and lets every other write through only once approved on the host.
- **devops**: reads and writes pull requests, their comment threads, and work items of the on-premises Azure DevOps Server. It doesn't run in the agent container: the agent reaches it through the MCP gateway (see "MCP gateway"), which lets the read tools through while the `devops` group is open, and every write tool only once approved on the host. `settings.json` still denies the outright destructive tools (deletion, wiki, repository operations) and asks before the other writes.
- **sas**: runs commands, uploads, downloads, and SAS programs on the SAS server of REF, for the local `sas-ref` skill. It doesn't run in the agent container: the agent reaches it through the MCP gateway (see "MCP gateway"), which lets `sas_get` through while the `sas` group is open, and every other tool only once approved on the host; `settings.json` also asks for a confirmation of `sas_exec`, `sas_put`, and `sas_run`.

### Adding a new profile

1. Create `profiles/<name>/CLAUDE.md` and `profiles/<name>/settings.json`, and optionally `profiles/<name>/mcp.json` and `.claude/rules|skills|docs`.
2. Add a function to `launch.sh`, next to the existing ones:
   ```bash
   claude_<name>(){
       _claude_agent <name> claude "$@"
   }
   ```

## Repository structure

```
bfs-claude-agent/
  Dockerfile                 (image of the agent)
  .dockerignore              (allow list of the build context of both Dockerfiles)
  entrypoint.sh
  launch.sh
  services/
    Dockerfile               (images of the service containers, one target each, see "Service images")
    atlassian/               (requirements.in, requirements.txt: locked Python packages, see "Dependencies")
    devops/
    oracle/
  notifications/
    agent-notify             (copied into the image)
    speak-relay.sh           (run on the WSL2 host)
    install-speech-host.sh   (run once on the WSL2 host)
  hooks/
    agent-memory-sync        (copied into the image)
    agent-memory-guard       (copied into the image)
  broker/                    (copied into the service images; the clients into the agent image)
    access.py                (windows, notifications and commands of the host, shared by the broker and the gateway)
    claude_broker.py         (service of the secret broker, /usr/local/bin/claude-broker)
    claude_gateway.py        (service of the MCP gateway, /usr/local/bin/claude-gateway)
    git_credential_broker.py (Git credential helper of the agent, /usr/local/bin/git-credential-broker)
    gh                       (wrapper of the GitHub CLI)
    secrets.example          (template of the secrets file)
  certs/
    bit-proxy-ca.pem
    nexus-ca.pem
    swissgov-root.cer
  .env.example
  pyproject.toml             (checks of the Python code, see "Development")
  requirements-dev.txt
  .pre-commit-config.yaml
  tests/                     (tests of broker/)
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
- `AGENT_HOMES_DIR` (default `$HOME/.claude-agent-homes`), one subfolder per profile, holds Claude Code's own state: `slots/<n>/`, the home of each slot (local configuration, session history), and `projects/`, shared by the slots (memory, session transcripts), see "Several agents at the same time". It also holds a shared `CLAUDE.user.md` at its root (see "A personal preference" above).
- `AGENT_ENV_FILE` (default `$AGENT_HOMES_DIR/.env`) holds the real Claude Code token and the commit authorship of each server; only `.env.example`, with placeholders, is committed here.
- `AGENT_VAULT_DIR` (default `$AGENT_HOMES_DIR/vault`) holds the encrypted secrets file of the secret broker (Git tokens, GitHub API token), and `AGENT_BROKER_LOG_DIR` (default `$AGENT_HOMES_DIR/broker`) its log; only `broker/secrets.example` is committed here.
- `SHARED_BASE_DIR` (default `~/.agents/shared`) holds ad-hoc documents shared with the agent, outside any project repository.

## Development

The Python code of the image (`broker/`) and its tests (`tests/`) follow the same checks as the Python scripts of the SAS toolkit, set in `pyproject.toml`: black, ruff with every rule (each exception justified), mypy and pyright in strict mode, bandit, and the tests (unittest style, run with pytest) with a coverage of at least 90 %. The code uses the Python of the image (3.12) and the standard library only; the tools are only installed in a virtual environment of the clone, never in the image:

```bash
python3 -m venv --upgrade-deps .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pre-commit install      # runs every check before each commit (.pre-commit-config.yaml)
.venv/bin/pre-commit run --all-files
```

The versions in `requirements-dev.txt` and in `.pre-commit-config.yaml` are kept in line.

## Troubleshooting

For any build, network, or permission issue found with this configuration, the full setup guide should be consulted; it documents the difficulties already encountered with this setup (enterprise certificates, the shared NuGet cache, file permissions, and so on) and how each one was solved. The two MCP appendices (Confluence, Azure DevOps) document the difficulties specific to those two integrations separately.
