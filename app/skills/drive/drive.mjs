#!/usr/bin/env node
// Stream resumable uploads; delete only after Drive confirms the file ID.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export async function main(args = process.argv.slice(2), env = process.env, request = fetch) {
  const [agent, command, file, name] = args;
  if (!['coding', 'writing', 'video'].includes(agent) || !['upload', 'list'].includes(command) || (command === 'upload' && !file)) {
    throw new Error('usage: drive.mjs <coding|writing|video> upload <file> [name] | list');
  }
  const cfg = JSON.parse(fs.readFileSync(`${env.HOME}/.drive/${agent}.json`, 'utf8'));
  if (!cfg.clientId || !cfg.clientSecret || !cfg.refreshToken || !/^[A-Za-z0-9_-]+$/.test(cfg.folderId || '')) {
    throw new Error('Invalid Drive configuration');
  }
  let actualFile, before;
  if (command === 'upload') {
    actualFile = fs.realpathSync(file);
    const root = fs.realpathSync(`${env.HOME}/.openclaw/workspace-${agent}/work`);
    const relative = path.relative(root, actualFile);
    if (!relative || relative.startsWith('..' + path.sep) || relative === '..' || path.isAbsolute(relative)) {
      throw new Error('Only upload files inside this agent workspace/work directory');
    }
    before = fs.statSync(actualFile);
    if (!before.isFile()) throw new Error('Upload requires a regular file');
  }
  const tokenResponse = await request('https://oauth2.googleapis.com/token', {
    method: 'POST', signal: AbortSignal.timeout(30000),
    body: new URLSearchParams({ client_id: cfg.clientId, client_secret: cfg.clientSecret,
      refresh_token: cfg.refreshToken, grant_type: 'refresh_token' }),
  });
  const token = await tokenResponse.json();
  if (!tokenResponse.ok || !token.access_token) throw new Error(`Drive OAuth failed (HTTP ${tokenResponse.status}); refresh credentials`);
  const auth = { Authorization: `Bearer ${token.access_token}` };
  if (command === 'upload') {
    const init = await request('https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&fields=id,name,webViewLink', {
      method: 'POST', signal: AbortSignal.timeout(30000),
      headers: { ...auth, 'content-type': 'application/json', 'X-Upload-Content-Type': 'application/octet-stream', 'X-Upload-Content-Length': String(before.size) },
      body: JSON.stringify({ name: name || path.basename(actualFile), parents: [cfg.folderId] }),
    });
    if (!init.ok || !init.headers.get('location')) throw new Error(`Drive upload initialization failed (HTTP ${init.status}); local file preserved`);
    const location = new URL(init.headers.get('location'));
    if (location.protocol !== 'https:' || location.hostname !== 'www.googleapis.com') throw new Error('Unexpected Drive upload destination');
    const response = await request(location, {
      method: 'PUT', headers: { ...auth, 'content-type': 'application/octet-stream', 'content-length': String(before.size) },
      body: fs.createReadStream(actualFile), duplex: 'half', signal: AbortSignal.timeout(30 * 60 * 1000),
    });
    if (!response.ok) throw new Error(`Drive upload not confirmed (HTTP ${response.status}); local file preserved`);
    const result = await response.json();
    if (!result.id) throw new Error('Drive did not confirm a file ID; local file preserved');
    const after = fs.statSync(actualFile);
    if (before.ino === after.ino && before.size === after.size && before.mtimeMs === after.mtimeMs) fs.unlinkSync(actualFile);
    else result.warning = 'Local file changed during upload and was NOT deleted';
    console.log(JSON.stringify(result));
  } else {
    let pageToken;
    do {
      const query = new URLSearchParams({ q: `'${cfg.folderId}' in parents and trashed=false`, fields: 'nextPageToken,files(id,name,webViewLink)', pageSize: '100', ...(pageToken ? { pageToken } : {}) });
      const response = await request(`https://www.googleapis.com/drive/v3/files?${query}`, { headers: auth, signal: AbortSignal.timeout(30000) });
      if (!response.ok) throw new Error(`Drive listing failed (HTTP ${response.status})`);
      const result = await response.json();
      console.log(JSON.stringify({ files: result.files || [] }));
      pageToken = result.nextPageToken;
    } while (pageToken);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
