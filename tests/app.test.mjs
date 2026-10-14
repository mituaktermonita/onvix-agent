import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { atomicWrite, validateKeys } from '../app/files.mjs';
import { main as drive } from '../app/skills/drive/drive.mjs';

const root = path.resolve(import.meta.dirname, '..');
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'onvix-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test('atomic private files and dotenv key injection rejection', t => {
  const dir = fixture(t), file = `${dir}/keys.json`;
  fs.writeFileSync(file, 'old', { mode: 0o644 });
  atomicWrite(file, 'new');
  assert.equal(fs.readFileSync(file, 'utf8'), 'new');
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.deepEqual(fs.readdirSync(dir), ['keys.json']);
  for (const value of ['abc\nEVIL=1', '${SECRET}', 'x#comment', ' a-key-123']) {
    assert.throws(() => validateKeys({ coding: value, writing: 'sk-writing', video: 'sk-video' }));
  }
});

test('renderer emits routable explicit fleet, local mode, private keys and agent prompts', t => {
  const dir = fixture(t);
  const run = spawnSync(process.execPath, [`${root}/tests/image-config.mjs`, dir], {
    env: { ...process.env, APP_DIR: `${root}/app` }, encoding: 'utf8',
  });
  assert.equal(run.status, 0, run.stderr);
  const configFile = `${dir}/openclaw/.openclaw/openclaw.json`;
  const config = JSON.parse(fs.readFileSync(configFile, 'utf8'));
  assert.equal(config.gateway.bind, 'lan');
  assert.equal(config.gateway.mode, 'local');
  assert.equal(config.gateway.auth.mode, 'token');
  assert.equal(config.agents.ownership, 'explicit');
  assert.equal(config.bindings[0].agentId, 'main');
  assert.equal(config.channels.telegram.groups['-1001234567890'].topics['2'].agentId, 'coding');
  assert.deepEqual(config.channels.telegram.groupAllowFrom, ['123456789']);
  assert.equal(fs.statSync(configFile).mode & 0o777, 0o600);
  assert.match(fs.readFileSync(`${dir}/openclaw/.openclaw/workspace-coding/AGENTS.md`, 'utf8'), /coding agent/);
});

test('launcher auth, size limit, input validation and persistent stop intent', { timeout: 15000 }, async t => {
  const dir = fixture(t);
  fs.mkdirSync(`${dir}/state`); fs.mkdirSync(`${dir}/ctl`);
  const token = 'test-token-which-is-long-enough-123';
  const child = spawn(process.execPath, [`${root}/app/launcher.mjs`], { env: {
    ...process.env, STATE_DIR: `${dir}/state`, CTL_DIR: `${dir}/ctl`, LAUNCHER_TOKEN: token,
    LAUNCHER_URL: 'https://example.invalid', DISABLE_TELEGRAM: '1', PORT: '0',
  }, stdio: ['ignore', 'pipe', 'pipe'] });
  t.after(async () => { if (child.exitCode === null) { const exited = once(child, 'exit'); child.kill(); await exited; } });
  const port = await new Promise((resolve, reject) => {
    child.stdout.on('data', data => { const m = /port (\d+)/.exec(data.toString()); if (m) resolve(m[1]); });
    child.once('error', reject); child.once('exit', code => reject(new Error(`Launcher exited: ${code}`)));
  });
  const base = `http://127.0.0.1:${port}`;
  assert.equal((await fetch(`${base}/healthz`)).status, 200);
  assert.equal((await fetch(base)).status, 403);
  const page = await fetch(`${base}/?t=${token}`);
  assert.equal(page.status, 200);
  assert.equal(page.headers.get('referrer-policy'), 'no-referrer');
  const post = (route, values) => fetch(`${base}${route}`, { method: 'POST', body: new URLSearchParams({ t: token, ...values }) });
  assert.equal((await post('/start', { coding: 'sk-code\nEVIL=1', writing: 'sk-writing', video: 'sk-video' })).status, 400);
  assert.equal((await post('/start', { coding: 'sk-coding', writing: 'sk-writing', video: 'sk-video' })).status, 200);
  assert.equal(fs.readFileSync(`${dir}/ctl/cmd`, 'utf8'), 'start');
  assert.equal(fs.statSync(`${dir}/state/keys.json`).mode & 0o777, 0o600);
  assert.equal((await post('/ctl', { cmd: 'arbitrary' })).status, 400);
  assert.equal((await post('/ctl', { cmd: 'stop' })).status, 200);
  assert.equal(fs.existsSync(`${dir}/state/started`), false);
  assert.equal(fs.readFileSync(`${dir}/ctl/cmd`, 'utf8'), 'stop');
  assert.equal((await post('/start', { coding: 'x'.repeat(9000) })).status, 413);
});

function driveFixture(t) {
  const home = fixture(t), folder = `${home}/.openclaw/workspace-coding/work`;
  fs.mkdirSync(folder, { recursive: true }); fs.mkdirSync(`${home}/.drive`);
  fs.writeFileSync(`${home}/.drive/coding.json`, JSON.stringify({ clientId: 'fixture', clientSecret: 'fixture', refreshToken: 'fixture', folderId: 'fixture-folder' }));
  const file = `${folder}/result.txt`; fs.writeFileSync(file, 'deliverable');
  return { home, file };
}
function mockUpload(finalResponse) {
  let call = 0;
  return async (url, options) => {
    call++;
    if (call === 1) return Response.json({ access_token: 'fixture' });
    if (call === 2) {
      assert.match(String(url), /uploadType=resumable/);
      return new Response('', { headers: { location: 'https://www.googleapis.com/upload/fixture' } });
    }
    assert.equal(options.method, 'PUT');
    const chunks = [];
    for await (const chunk of options.body) chunks.push(chunk);
    assert.equal(Buffer.concat(chunks).toString(), 'deliverable');
    return finalResponse();
  };
}
test('Drive deletes only a confirmed upload', async t => {
  const { home, file } = driveFixture(t);
  await drive(['coding', 'upload', file], { HOME: home }, mockUpload(() => Response.json({ id: 'confirmed-file' })));
  assert.equal(fs.existsSync(file), false);
});
test('Drive failures preserve the deliverable', async t => {
  for (const response of [() => new Response('', { status: 500 }), () => Response.json({})]) {
    const { home, file } = driveFixture(t);
    await assert.rejects(drive(['coding', 'upload', file], { HOME: home }, mockUpload(response)));
    assert.equal(fs.readFileSync(file, 'utf8'), 'deliverable');
  }
});
test('Drive rejects path traversal before network access', async t => {
  const { home } = driveFixture(t);
  let called = false;
  await assert.rejects(drive(['../../evil', 'list'], { HOME: home }, () => { called = true; }));
  const outside = `${home}/outside.txt`; fs.writeFileSync(outside, 'keep');
  await assert.rejects(drive(['coding', 'upload', outside], { HOME: home }, () => { called = true; }));
  assert.equal(called, false);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'keep');
});
