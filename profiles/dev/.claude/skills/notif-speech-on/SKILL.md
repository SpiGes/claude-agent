---
name: notif-speech-on
description: Unmutes the speech notifications of every agent, muted by /notif-speech-off
disable-model-invocation: true
allowed-tools: Bash(agent-notify *)
---

Result of `agent-notify --speech-on`:

!`agent-notify --speech-on`

The result above is reported to the user in one short sentence, in the language of the conversation.
Nothing else is done.
