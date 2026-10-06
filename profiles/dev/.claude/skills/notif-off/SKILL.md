---
name: notif-off
description: Mutes the spoken notifications of every agent, until /notif-on
disable-model-invocation: true
allowed-tools: Bash(agent-notify *)
---

Result of `agent-notify --off`:

!`agent-notify --off`

The result above is reported to the user in one short sentence, in the language of the conversation.
Nothing else is done.
