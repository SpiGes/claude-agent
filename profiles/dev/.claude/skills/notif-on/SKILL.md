---
name: notif-on
description: Unmutes the spoken notifications of every agent, muted by /notif-off
disable-model-invocation: true
allowed-tools: Bash(agent-notify *)
---

Result of `agent-notify --on`:

!`agent-notify --on`

The result above is reported to the user in one short sentence, in the language of the conversation.
Nothing else is done.
