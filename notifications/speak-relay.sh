#!/usr/bin/env bash
# Speech relay of the speech notifications, run on the WSL host (not in the container), started by launch.sh.
# Reads the text files that agent-notify drops into the queue folder, in their order of arrival, and speaks
# them through the WSLg audio: with piper when it and its voice model are found, with espeak-ng otherwise.
# Only one relay runs per user: a second one exits at once.
#
# Voice choice from the container (piper only): the relay keeps the list of the installed voices in
# <queue>/.voices, and uses the voice named in <queue>/.voice when there's one (written by
# agent-notify --speech-voice). Since that file comes from the container, only a voice name is accepted
# (letters, digits, _ and -), looked up in the voices folder, never a path; an invalid or missing voice falls
# back to the default one. When the default voice isn't installed, the first installed voice is used instead.
#
# Everything in the queue folder may come from the container, which mustn't make the relay read or write a
# file of the host: a message is first moved to a private folder, and only read when it's a regular file,
# not a symbolic link; .voice is only read when it's a regular file; .voices is written to a new file
# (mktemp) renamed afterwards, never to an existing name.
#
# Usage: speak-relay.sh <queue folder>   runs the relay
#        speak-relay.sh --check          exits with 0 when a speech engine is available
#
# Settings (environment):
#   SPEAK_RELAY_PIPER          piper executable (default ~/.local/bin/piper)
#   SPEAK_RELAY_PIPER_MODEL    default piper voice model, .onnx file (default ~/.local/share/piper-voices/fr_FR-siwis-medium.onnx;
#                              the first voice of the voices folder when it isn't installed)
#   SPEAK_RELAY_PIPER_SPEAKER  speaker id, for a model with several speakers (default: the first one, 0)
#   SPEAK_RELAY_PIPER_VOICES_DIR  folder of the piper voices that can be chosen (default ~/.local/share/piper-voices)
#   SPEAK_RELAY_ESPEAK_VOICE   espeak-ng voice (default fr)
#   SPEAK_RELAY_MAX_AGE        age in seconds above which a message is skipped (default 600)
#
# The whole script is in functions called from its last line, so that bash has read it in full before it
# runs: a git pull that changes this file doesn't disturb a relay already running.

select_engine(){
    piper_bin="${SPEAK_RELAY_PIPER:-$HOME/.local/bin/piper}"
    piper_model="${SPEAK_RELAY_PIPER_MODEL:-$HOME/.local/share/piper-voices/fr_FR-siwis-medium.onnx}"
    piper_speaker="${SPEAK_RELAY_PIPER_SPEAKER:-}"
    voices_dir="${SPEAK_RELAY_PIPER_VOICES_DIR:-$HOME/.local/share/piper-voices}"
    espeak_voice="${SPEAK_RELAY_ESPEAK_VOICE:-fr}"

    if [ ! -f "$piper_model" ]; then
        local model
        for model in "$voices_dir"/*.onnx; do
            if [ -f "$model" ] && [ -f "$model.json" ]; then
                piper_model="$model"
                piper_speaker=""
                break
            fi
        done
    fi
    if [ -x "$piper_bin" ] && [ -f "$piper_model" ] && command -v paplay >/dev/null; then
        engine=piper
    elif command -v espeak-ng >/dev/null; then
        engine=espeak-ng
    else
        return 1
    fi
}

# The lock descriptor (9) is closed for the speech commands, so that a command still running after the relay
# has ended doesn't keep the lock.
speak(){
    if [ "$engine" = piper ]; then
        local model speaker
        { read -r model; read -r speaker; } < <(chosen_voice)
        local -a args=(-m "$model")
        [ -z "$speaker" ] || args+=(-s "$speaker")
        printf '%s' "$1" | "$piper_bin" "${args[@]}" -f "$wav" 2>/dev/null 9>&- && paplay "$wav" 9>&-
    else
        printf '%s' "$1" | espeak-ng -v "$espeak_voice" 9>&-
    fi
}

# Model and speaker to use: the voice chosen in <queue>/.voice when it's valid and installed, the default one
# otherwise. Prints the model path and the speaker id (possibly empty) on two lines.
chosen_voice(){
    local name="" speaker=""
    if [ -f "$queue/.voice" ] && [ ! -L "$queue/.voice" ]; then
        read -r name speaker < "$queue/.voice"
    fi
    if [ -n "$name" ]; then
        if [[ "$name" =~ ^[A-Za-z0-9_-]+$ ]] && [[ -z "$speaker" || "$speaker" =~ ^[0-9]+$ ]] \
            && [ -f "$voices_dir/$name.onnx" ]; then
            printf '%s\n%s\n' "$voices_dir/$name.onnx" "$speaker"
            return
        fi
        echo "$(date '+%F %T') chosen voice not valid or not installed, default voice used" >&2
    fi
    printf '%s\n%s\n' "$piper_model" "$piper_speaker"
}

# Writes <queue>/.voices, read by agent-notify --speech-voices and --speech-voice:
#   engine <piper|espeak-ng>
#   default <voice name> <speaker id or ->
#   voice <voice name> <"<id>:<speaker name>" list, or ->     (one line per installed piper voice)
write_voices_list(){
    local list model name speakers
    list="$(mktemp "$queue/.voices.XXXXXX")" || return
    {
        echo "engine $engine"
        if [ "$engine" = piper ]; then
            name="${piper_model##*/}"
            echo "default ${name%.onnx} ${piper_speaker:--}"
            for model in "$voices_dir"/*.onnx; do
                [ -f "$model.json" ] || continue
                name="${model##*/}"
                speakers="$(python3 -c '
import json, sys
speakers = json.load(open(sys.argv[1])).get("speaker_id_map") or {}
print(" ".join(f"{i}:{n.replace(chr(32), chr(95))}" for n, i in sorted(speakers.items(), key=lambda s: s[1])) or "-")
' "$model.json" 2>/dev/null)" || speakers=-
                echo "voice ${name%.onnx} ${speakers:--}"
            done
        fi
    } > "$list" && chmod 644 "$list" && mv -f "$list" "$queue/.voices"
}

run(){
    queue="$1"
    local max_age="${SPEAK_RELAY_MAX_AGE:-600}"

    exec 9>"${XDG_RUNTIME_DIR:-/tmp}/speak-relay-$(id -u).lock"
    if ! flock -n 9; then
        [ -t 1 ] && echo "speak-relay is already running"
        exit 0
    fi

    select_engine || { echo "speak-relay: neither piper (with its voice model and paplay) nor espeak-ng is available" >&2; exit 1; }
    # Private folder, out of the reach of the container
    private="$(mktemp -d)"
    trap 'rm -rf -- "$private"' EXIT
    wav="$private/speech.wav"
    echo "$(date '+%F %T') watching $queue with $engine"

    shopt -s nullglob
    local file text age voices_stamp="" stamp
    while true; do
        # The list of voices is written again when the voices folder changes (a voice added or removed)
        stamp="$(stat -c %Y "$voices_dir" 2>/dev/null)"
        if [ "$stamp" != "$voices_stamp" ] || [ ! -f "$queue/.voices" ]; then
            write_voices_list
            voices_stamp="$stamp"
        fi
        # Names start with a timestamp: the glob order is the order of arrival
        for file in "$queue"/*.txt; do
            # Moved first: once in the private folder, the container can't replace it with a link anymore
            mv -f -- "$file" "$private/message" 2>/dev/null || continue
            if [ -L "$private/message" ] || [ ! -f "$private/message" ]; then
                echo "$(date '+%F %T') ignored: ${file##*/} isn't a regular file" >&2
                rm -rf -- "$private/message"
                continue
            fi
            text="$(<"$private/message")"
            age=$(( $(date +%s) - $(stat -c %Y "$private/message") ))
            rm -f -- "$private/message"
            [ -n "${text//[[:space:]]/}" ] || continue
            if [ "$age" -gt "$max_age" ]; then
                echo "$(date '+%F %T') skipped (${age}s old): $text"
                continue
            fi
            echo "$(date '+%F %T') $text"
            speak "$text" || echo "$(date '+%F %T') speech failed" >&2
        done
        sleep 1 9>&-
    done
}

main(){
    set -uo pipefail
    case "${1:-}" in
        --check) select_engine ;;
        ""|-*) echo "Usage: speak-relay.sh <queue folder> | --check" >&2; exit 2 ;;
        *) run "$1" ;;
    esac
}

main "$@"; exit
