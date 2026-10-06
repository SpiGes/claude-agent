---
name: notif-speech-off
description: Mutes the speech notifications of every agent, until /notif-speech-on
disable-model-invocation: true
allowed-tools: Bash(agent-notify *)
---

Result of `agent-notify --speech-off`:

!`agent-notify --speech-off`

The result above is reported to the user in one short sentence, in the language of the conversation.
Nothing else is done.
