#!/usr/bin/env bash
# Installs the host prerequisites of the speech notifications, run once on the WSL2 host (not in the
# container): the audio tools, espeak-ng, piper, and a piper voice. Each step is skipped when it's already
# done, so the script can be run again, e.g. to add another voice.
#
# Usage: install-speech-host.sh [--voice <name>] [--proxy <url>] [--force] [--no-test]
#   --voice <name>   piper voice to download (default fr_FR-siwis-medium), e.g. de_DE-thorsten-medium
#   --proxy <url>    proxy for the piper installation and the voice download, e.g. http://proxy.example:8080;
#                    it takes precedence over any other proxy setting. A URL with credentials is kept in the
#                    shell history: the proxy settings below are preferable in that case
#   --force          downloads the voice again, even when its files are complete
#   --no-test        doesn't speak the test sentence at the end
#
# pip doesn't read the proxy settings of apt, and the voice download (curl) doesn't read the ones of pip: it
# only reads https_proxy. Without --proxy, the proxy is taken from https_proxy, else from a pip.conf, else
# from apt, and given to both. In every case, the proxy only applies to the piper installation and the voice
# download, with the system CA bundle, since the corporate proxy re-signs the HTTPS traffic. No configuration
# file is changed.
#
# The files of the voice are checked against the MD5 checksums of the piper voice catalog at each run, and
# downloaded again when they're damaged (e.g. an incomplete download); the voice is then checked by a
# synthesis, also with --no-test.
set -euo pipefail

voice=fr_FR-siwis-medium
test_speech=1
proxy=""
force=0
while [ $# -gt 0 ]; do
    case "$1" in
        --voice) voice="${2:?--voice needs a voice name}"; shift 2 ;;
        --proxy) proxy="${2:?--proxy needs a proxy URL}"; shift 2 ;;
        --force) force=1; shift ;;
        --no-test) test_speech=0; shift ;;
        *) echo "Usage: install-speech-host.sh [--voice <name>] [--proxy <url>] [--force] [--no-test]" >&2; exit 2 ;;
    esac
done

relay="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/speak-relay.sh"
voices_dir="$HOME/.local/share/piper-voices"
piper_bin="$HOME/.local/bin/piper"

step(){ echo; echo "== $1"; }

step "WSLg audio server"
if [ -e /mnt/wslg/PulseServer ]; then
    echo "found"
else
    echo "/mnt/wslg/PulseServer not found: WSLg isn't active, no sound can be played (see 'wsl --version' on Windows)" >&2
    exit 1
fi

step "System packages (pulseaudio-utils, espeak-ng, pipx)"
missing=()
for package in pulseaudio-utils espeak-ng pipx; do
    dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed" || missing+=("$package")
done
if [ ${#missing[@]} -eq 0 ]; then
    echo "already installed"
else
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
fi

step "Proxy"
# Prints the proxy of the first pip.conf that sets one, the user files first, as pip reads them
pip_conf_proxy(){
    local file
    for file in "$HOME/.config/pip/pip.conf" "$HOME/.pip/pip.conf" /etc/pip.conf; do
        [ -f "$file" ] || continue
        sed -nE 's/^[[:space:]]*proxy[[:space:]]*=[[:space:]]*([^[:space:]]+).*/\1/p' "$file" | head -1 | grep . && return 0
    done
    return 1
}
# The proxy values aren't printed: they may hold credentials
use_proxy(){
    # PIP_PROXY takes precedence over a pip.conf; https_proxy is read by the voice download
    export PIP_PROXY="$1" https_proxy="$1" http_proxy="$1"
    export PIP_CERT="${PIP_CERT:-/etc/ssl/certs/ca-certificates.crt}"
}
env_proxy="${https_proxy:-${HTTPS_PROXY:-}}"
if [ -n "$proxy" ]; then
    use_proxy "$proxy"
    echo "the proxy given with --proxy is used for this installation"
elif [ -n "$env_proxy" ]; then
    use_proxy "$env_proxy"
    echo "the proxy of https_proxy is used for this installation"
elif conf_proxy="$(pip_conf_proxy)"; then
    use_proxy "$conf_proxy"
    echo "the proxy of pip.conf is used for this installation"
else
    apt_http_proxy="" apt_https_proxy=""
    eval "$(apt-config shell apt_http_proxy Acquire::http::Proxy apt_https_proxy Acquire::https::Proxy)"
    apt_proxy="${apt_https_proxy:-$apt_http_proxy}"
    if [ -n "$apt_proxy" ]; then
        use_proxy "$apt_proxy"
        echo "the proxy of apt is used for this installation"
    else
        echo "no proxy found: direct connection"
    fi
fi

step "piper"
if [ -x "$piper_bin" ]; then
    echo "already installed ($piper_bin)"
else
    pipx install piper-tts
fi

step "Voice $voice"
# The files of the voice and their MD5 checksums are read from the piper voice catalog. A file is downloaded
# when it's missing or its checksum differs (e.g. after an incomplete download), or with --force; curl shows
# the progress, and gives up when the transfer stalls instead of waiting forever.
temp_files=()
trap 'rm -f -- "${temp_files[@]}"' EXIT
voices_url=https://huggingface.co/rhasspy/piper-voices/resolve/main
curl_args=(-fL --retry 3 --connect-timeout 30 --speed-limit 1000 --speed-time 60)
md5_of(){ md5sum < "$1" | cut -d' ' -f1; }

catalog="$(mktemp)"; temp_files+=("$catalog")
voice_files=""
echo "reading the voice catalog"
if ! curl "${curl_args[@]}" -sS -o "$catalog" "$voices_url/voices.json"; then
    # Without the catalog, a voice already there is only checked by the synthesis below
    if [ "$force" = 0 ] && [ -f "$voices_dir/$voice.onnx" ] && [ -f "$voices_dir/$voice.onnx.json" ]; then
        echo "warning: the voice catalog can't be read, the checksums of the voice files aren't checked" >&2
    else
        echo "The voice catalog can't be read ($voices_url/voices.json): the voice can't be downloaded." >&2
        exit 1
    fi
elif ! voice_files="$(python3 - "$catalog" "$voice" <<'PY'
import json, sys
entry = json.load(open(sys.argv[1])).get(sys.argv[2])
if not entry:
    sys.exit(1)
for path, file in entry["files"].items():
    if path.endswith((".onnx", ".onnx.json")):
        print(path, file["md5_digest"])
PY
)"; then
    echo "The voice $voice isn't in the piper voice catalog ($voices_url/voices.json)." >&2
    exit 1
fi

mkdir -p "$voices_dir"
while read -r path md5; do
    [ -n "$path" ] || continue
    file="${path##*/}"
    target="$voices_dir/$file"
    if [ "$force" = 0 ] && [ -f "$target" ] && [ "$(md5_of "$target")" = "$md5" ]; then
        echo "$file: already downloaded"
        continue
    fi
    echo "$file: downloading"
    partial="$target.partial"; temp_files+=("$partial")
    curl "${curl_args[@]}" --progress-bar -o "$partial" "$voices_url/$path"
    if [ "$(md5_of "$partial")" != "$md5" ]; then
        echo "$file: the downloaded file is damaged (checksum mismatch), the script can be run again" >&2
        exit 1
    fi
    mv -f "$partial" "$target"
done <<< "$voice_files"

# Checked by a synthesis too, which also proves that piper and the model work together
wav="$(mktemp --suffix .wav)"; temp_files+=("$wav")
piper_errors="$(mktemp)"; temp_files+=("$piper_errors")
if echo "Les notifications vocales sont prêtes." | "$piper_bin" -m "$voices_dir/$voice.onnx" -f "$wav" 2>"$piper_errors"; then
    echo "voice checked: piper reads it"
else
    tail -1 "$piper_errors" >&2
    echo "piper can't read the voice $voice. It can be downloaded again with:" >&2
    echo "  install-speech-host.sh --voice $voice --force" >&2
    exit 1
fi

step "Check"
default_model="$voices_dir/fr_FR-siwis-medium.onnx"
model="${SPEAK_RELAY_PIPER_MODEL:-$default_model}"
if [ "$voices_dir/$voice.onnx" != "$model" ]; then
    echo "The default voice of the relay is $model. $voice can be chosen from an agent with"
    echo "/notif-speech-voice $voice, or made the default voice by adding before launch.sh is sourced in ~/.bashrc:"
    echo "  export SPEAK_RELAY_PIPER_MODEL=\"$voices_dir/$voice.onnx\""
fi
"$relay" --check || { echo "no speech engine found by the relay" >&2; exit 1; }
echo "speech engine found by the relay"

if [ "$test_speech" = 1 ]; then
    paplay "$wav"
    echo "test sentence spoken with $voice"
fi

echo
echo "Done. A running relay must be stopped (claude_notify_speech_stop) to take a new setting into account;"
echo "the next launch of an agent starts it again."
