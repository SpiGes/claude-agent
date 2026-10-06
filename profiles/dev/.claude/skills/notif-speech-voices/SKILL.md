---
name: notif-speech-voices
description: Lists the speech voices installed on the host and the current one
disable-model-invocation: true
allowed-tools: Bash(agent-notify *)
---

Result of `agent-notify --speech-voices`:

!`agent-notify --speech-voices`

The result above is shown to the user as is, translated into the language of the conversation, with a
reminder that a voice is chosen with `/notif-speech-voice <voice> [<speaker>]`. Nothing else is done.
