---
name: notif-speech-voice
description: Chooses the speech voice of every agent, among the voices installed on the host
argument-hint: <voice> [<speaker>] | default
disable-model-invocation: true
allowed-tools: Bash(agent-notify *)
---

Result of `agent-notify --speech-voice $ARGUMENTS`:

!`agent-notify --speech-voice $ARGUMENTS`

The result above is reported to the user in one short sentence, in the language of the conversation.
Nothing else is done.
