#!/usr/bin/env node
// usage: drive.mjs <agent> upload <file> [name] | list
// Uploads only into this agent's own Drive folder; deletes the local file after a successful upload.
import fs from "node:fs";
import path from "node:path";

const [agent, cmd, file, name] = process.argv.slice(2);
const cfg = JSON.parse(fs.readFileSync(`${process.env.HOME}/.drive/${agent}.json`, "utf8"));

async function token() {
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    body: new URLSearchParams({
      client_id: cfg.clientId,
      client_secret: cfg.clientSecret,
      refresh_token: cfg.refreshToken,
      grant_type: "refresh_token",
    }),
  });
  const j = await r.json();
  if (!j.access_token) throw new Error("token error: " + JSON.stringify(j));
  return j.access_token;
}

const t = await token();
const auth = { Authorization: `Bearer ${t}` };

if (cmd === "upload") {
  const fname = name || path.basename(file);
  const form = new FormData();
  form.append("metadata", new Blob([JSON.stringify({ name: fname, parents: [cfg.folderId] })], { type: "application/json" }));
  form.append("file", new Blob([fs.readFileSync(file)]));
  const r = await fetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink", {
    method: "POST",
    headers: auth,
    body: form,
  });
  const j = await r.json();
  if (!r.ok) throw new Error("upload failed: " + JSON.stringify(j));
  fs.rmSync(file, { force: true });
  console.log(JSON.stringify(j));
} else if (cmd === "list") {
  const q = encodeURIComponent(`'${cfg.folderId}' in parents and trashed=false`);
  const r = await fetch(`https://www.googleapis.com/drive/v3/files?q=${q}&fields=files(id,name,webViewLink)&pageSize=100`, { headers: auth });
  console.log(JSON.stringify(await r.json()));
} else {
  console.error("usage: drive.mjs <agent> upload <file> [name] | list");
  process.exit(1);
}
