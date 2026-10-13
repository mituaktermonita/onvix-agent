---
name: drive
description: Upload finished deliverables to this agent's Google Drive folder, or list what is already there.
---

Use this skill to deliver finished work. Each agent has its own Drive folder; you can only reach yours.

Upload a file (the local file is deleted after a successful upload; the command prints JSON with a webViewLink to send to the user):

```
node {{WS}}/skills/drive/drive.mjs {{AGENT}} upload ./work/<file> [drive-name]
```

List files in your folder:

```
node {{WS}}/skills/drive/drive.mjs {{AGENT}} list
```

Do scratch work only in ./work and remove leftovers when the job is finished.
