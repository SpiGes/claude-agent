#!/usr/bin/env bash
# Installs the host prerequisites of the speech notifications, run once on the WSL2 host (not in the
# container): the audio tools, espeak-ng, piper, and a piper voice. Each step is skipped when it's already
# done, so the script can be run again, e.g. to add another voice.
#
# Usage: install-speech-host.sh [--voice <name>] [--no-test]
#   --voice <name>   piper voice to download (default fr_FR-siwis-medium), e.g. de_DE-thorsten-medium
#   --no-test        doesn't speak the test sentence at the end
#
# pip doesn't read the proxy settings of apt. When no proxy is given to pip (https_proxy, or a pip.conf), the
# proxy of apt is used for the piper installation and the voice download only, with the system CA bundle,
# since the corporate proxy re-signs the HTTPS traffic. No configuration file is changed.
set -euo pipefail

voice=fr_FR-siwis-medium
test_speech=1
while [ $# -gt 0 ]; do
    case "$1" in
        --voice) voice="${2:?--voice needs a voice name}"; shift 2 ;;
        --no-test) test_speech=0; shift ;;
        *) echo "Usage: install-speech-host.sh [--voice <name>] [--no-test]" >&2; exit 2 ;;
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

step "Proxy for pip"
pip_has_proxy(){
    [ -n "${https_proxy:-}${HTTPS_PROXY:-}" ] && return 0
    local file
    for file in /etc/pip.conf "$HOME/.config/pip/pip.conf" "$HOME/.pip/pip.conf"; do
        [ -f "$file" ] && grep -qE '^[[:space:]]*proxy[[:space:]]*=' "$file" && return 0
    done
    return 1
}
if pip_has_proxy; then
    echo "already set for pip"
else
    apt_http_proxy="" apt_https_proxy=""
    eval "$(apt-config shell apt_http_proxy Acquire::http::Proxy apt_https_proxy Acquire::https::Proxy)"
    apt_proxy="${apt_https_proxy:-$apt_http_proxy}"
    if [ -n "$apt_proxy" ]; then
        # The value isn't printed: it may hold credentials
        export https_proxy="$apt_proxy" http_proxy="$apt_proxy"
        export PIP_CERT="${PIP_CERT:-/etc/ssl/certs/ca-certificates.crt}"
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
if [ -f "$voices_dir/$voice.onnx" ] && [ -f "$voices_dir/$voice.onnx.json" ]; then
    echo "already downloaded ($voices_dir)"
else
    mkdir -p "$voices_dir"
    "$(pipx environment --value PIPX_LOCAL_VENVS)/piper-tts/bin/python" -m piper.download_voices \
        --download-dir "$voices_dir" "$voice"
fi

step "Check"
default_model="$voices_dir/fr_FR-siwis-medium.onnx"
model="${SPEAK_RELAY_PIPER_MODEL:-$default_model}"
if [ "$voices_dir/$voice.onnx" != "$model" ]; then
    echo "The relay uses $model. To use $voice, add before launch.sh is sourced in ~/.bashrc:"
    echo "  export SPEAK_RELAY_PIPER_MODEL=\"$voices_dir/$voice.onnx\""
fi
"$relay" --check || { echo "no speech engine found by the relay" >&2; exit 1; }
echo "speech engine found by the relay"

if [ "$test_speech" = 1 ]; then
    wav="$(mktemp --suffix .wav)"
    trap 'rm -f -- "$wav"' EXIT
    echo "Les notifications vocales sont prêtes." | "$piper_bin" -m "$voices_dir/$voice.onnx" -f "$wav" 2>/dev/null
    paplay "$wav"
    echo "test sentence spoken with $voice"
fi

echo
echo "Done. A running relay must be stopped (claude_notify_speech_stop) to take a new setting into account;"
echo "the next launch of an agent starts it again."
