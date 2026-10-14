// Build a synthetic config with no real credentials and validate the same renderer used in production.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { randomBytes } from 'node:crypto';
const root = process.argv[2];
if (!root) throw new Error('Fixture directory required');
fs.mkdirSync(`${root}/openclaw`, { recursive: true });
fs.writeFileSync(`${root}/keys.json`, JSON.stringify({ coding: 'sk-test-coding', writing: 'sk-test-writing', video: 'sk-test-video' }), { mode: 0o600 });
Object.assign(process.env, {
  OPENCLAW_HOME: `${root}/openclaw`, KEYS_FILE: `${root}/keys.json`,
  OWNER_ID: '123456789', TG_CHAT_ID: '-1001234567890', TG_TOPIC_CODING: '2', TG_TOPIC_WRITING: '3', TG_TOPIC_VIDEO: '4',
  GATEWAY_URL: 'https://gateway.example.invalid', GDRIVE_CLIENT_ID: 'fixture-client', GDRIVE_CLIENT_SECRET: 'fixture-secret',
  GDRIVE_REFRESH_TOKEN: 'fixture-refresh', GDRIVE_FOLDER_CODING: 'folder-coding', GDRIVE_FOLDER_WRITING: 'folder-writing', GDRIVE_FOLDER_VIDEO: 'folder-video',
});
await import(pathToFileURL(`${process.env.APP_DIR || '/opt/app'}/render-config.mjs`));
fs.writeFileSync(`${root}/gateway-token`, randomBytes(32).toString('hex'), { mode: 0o600 });
