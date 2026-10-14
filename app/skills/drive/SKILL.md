---
name: drive
description: Upload finished deliverables to this agent's Google Drive folder, or list what is already there.
---

Use this skill to deliver finished work. This helper routes each agent to its configured Drive folder. Agents share an OS user, so this is not a security boundary against hostile agent code.

Upload a regular file inside this agent’s workspace/work directory. The helper streams a resumable upload. It deletes the unchanged local file only after Drive confirms a file ID; failures preserve it. The command prints JSON with the returned ID and, when available, webViewLink:

```
node {{WS}}/skills/drive/drive.mjs {{AGENT}} upload ./work/<file> [drive-name]
```

List files in your folder:

```
node {{WS}}/skills/drive/drive.mjs {{AGENT}} list
```

Do scratch work only in ./work and remove leftovers when the job is finished.
