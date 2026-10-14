import http from 'node:http';
import fs from 'node:fs';
import { timingSafeEqual } from 'node:crypto';
import { atomicWrite, validateKeys } from './files.mjs';

const e = process.env;
for (const key of ['STATE_DIR', 'CTL_DIR', 'LAUNCHER_TOKEN', 'LAUNCHER_URL']) {
  if (!e[key]) throw new Error(`Missing ${key}`);
}
if (e.LAUNCHER_TOKEN.length < 24) throw new Error('LAUNCHER_TOKEN must have at least 24 characters');
const STATE = e.STATE_DIR, CTL = e.CTL_DIR, KEYS = `${STATE}/keys.json`;
const topics = { coding: e.TG_TOPIC_CODING, writing: e.TG_TOPIC_WRITING, video: e.TG_TOPIC_VIDEO };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const read = (file, fallback = '') => { try { return fs.readFileSync(file, 'utf8').trim(); } catch { return fallback; } };
const status = () => read(`${CTL}/status`, 'unknown');
const hasKeys = () => { try { validateKeys(JSON.parse(read(KEYS))); return true; } catch { return false; } };
const desired = () => fs.existsSync(`${STATE}/started`);
const setupUrl = `${e.LAUNCHER_URL}/?t=${encodeURIComponent(e.LAUNCHER_TOKEN)}`;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
function authorized(token) {
  const a = Buffer.from(token || ''), b = Buffer.from(e.LAUNCHER_TOKEN);
  return a.length === b.length && timingSafeEqual(a, b);
}

const tg = async (token, method, body) => {
  if (e.DISABLE_TELEGRAM === '1') return { ok: true, result: [] };
  try {
    const r = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(40000),
    });
    const result = await r.json();
    if (!r.ok || !result.ok) console.error(`Telegram ${method} failed (${r.status}, ${result.error_code || 'unknown'})`);
    return result;
  } catch { console.error(`Telegram ${method}: network error/timeout`); return { ok: false }; }
};
const ctlSay = (text, markup) => tg(e.CONTROL_BOT_TOKEN, 'sendMessage', {
  chat_id: e.OWNER_ID, text, ...(markup ? { reply_markup: markup } : {}),
});
const controls = () => ({ inline_keyboard: [[
  { text: '🔄 Restart', callback_data: 'restart' }, { text: '⏹ Stop', callback_data: 'stop' },
  { text: 'ℹ️ Status', callback_data: 'status' },
]] });

const page = (msg = '') => `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Onvix Agents</title><body style="font:16px system-ui;max-width:520px;margin:24px auto;padding:0 16px">
<h2>Onvix Agents</h2><p>OpenClaw: <b>${esc(status())}</b> ${esc(msg)}</p>
<form method=post action="/start"><input type=hidden name=t value="${esc(e.LAUNCHER_TOKEN)}">
<p>OmniRoute dashboard-এ coding, writing, video combo এবং তিনটি API key বানান:</p>
${['coding', 'writing', 'video'].map(n => `<label>${n} key<br><input name=${n} type=password required autocomplete=off style="width:100%;padding:8px;margin:4px 0 12px"></label>`).join('')}
<button>Start OpenClaw</button></form>
<form method=post action="/ctl"><input type=hidden name=t value="${esc(e.LAUNCHER_TOKEN)}">
<p><button name=cmd value=restart>Restart</button> <button name=cmd value=stop>Stop</button></p></form></body>`;

async function body(req) {
  const chunks = []; let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 8192) throw Object.assign(new Error('Request too large'), { status: 413 });
    chunks.push(chunk);
  }
  return new URLSearchParams(Buffer.concat(chunks).toString('utf8'));
}
let announcement = 0;
function command(cmd) {
  if (!['start', 'stop', 'restart'].includes(cmd)) throw new Error('Invalid command');
  if (cmd !== 'stop' && !hasKeys()) throw new Error('Set valid API keys first');
  if (cmd === 'stop') { fs.rmSync(`${STATE}/started`, { force: true }); announcement++; }
  else atomicWrite(`${STATE}/started`, '1');
  atomicWrite(`${CTL}/cmd`, cmd);
  if (cmd !== 'stop') void announce();
}

const server = http.createServer(async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");
  try {
    const url = new URL(req.url, 'http://internal');
    if (url.pathname === '/healthz' && req.method === 'GET') return res.writeHead(200).end('ok');
    if (!['GET', 'POST'].includes(req.method)) return res.writeHead(405).end('method not allowed');
    if (req.method === 'POST' && !(req.headers['content-type'] || '').startsWith('application/x-www-form-urlencoded')) {
      return res.writeHead(415).end('form required');
    }
    if (req.method === 'POST' && Number(req.headers['content-length']) > 8192) return res.writeHead(413).end('request too large');
    const form = req.method === 'POST' ? await body(req) : url.searchParams;
    if (!authorized(form.get('t'))) return res.writeHead(403).end('forbidden');
    const html = msg => res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }).end(page(msg));
    if (url.pathname === '/start' && req.method === 'POST') {
      const keys = validateKeys(Object.fromEntries(['coding', 'writing', 'video'].map(n => [n, form.get(n)])));
      atomicWrite(KEYS, JSON.stringify(keys));
      command('start');
      return html('— শুরু হচ্ছে; readiness পরীক্ষা শেষে Telegram-এ জানাব।');
    }
    if (url.pathname === '/ctl' && req.method === 'POST') {
      if (!['restart', 'stop'].includes(form.get('cmd'))) return res.writeHead(400).end('invalid command');
      command(form.get('cmd')); return html('— কমান্ড পাঠানো হয়েছে');
    }
    if (url.pathname === '/' && req.method === 'GET') return html('');
    res.writeHead(404).end('not found');
  } catch (err) {
    console.error('Launcher request rejected:', err.code || err.status || 'invalid request');
    if (!res.headersSent) res.writeHead(err.status || 400).end('Invalid request or unavailable state; check logs.');
  }
});
server.requestTimeout = 15000;
server.headersTimeout = 10000;
server.listen(Number(e.PORT || 8080), '0.0.0.0', () => console.log(`Launcher listening on port ${server.address().port}`));

async function announce() {
  const generation = ++announcement;
  // Do not mistake the previous gateway instance for a completed restart.
  await sleep(4000);
  for (let i = 0; i < 90; i++) {
    if (generation !== announcement || !desired()) return;
    if (!fs.existsSync(`${CTL}/cmd`) && !fs.existsSync(`${CTL}/cmd.processing`) && status() === 'running') {
      try {
        const r = await fetch('http://127.0.0.1:18789/readyz', { signal: AbortSignal.timeout(3000) });
        if (r.ok && generation === announcement && desired()) {
          for (const [name, id] of Object.entries(topics)) await tg(e.OPENCLAW_BOT_TOKEN, 'sendMessage', {
            chat_id: e.TG_CHAT_ID, message_thread_id: Number(id), text: `✅ ${name} agent প্রস্তুত। কাজ এখানে লিখুন।`,
          });
          await ctlSay('✅ OpenClaw readiness check সফল।', controls()); return;
        }
      } catch {}
    }
    await sleep(2000);
  }
  if (generation === announcement && desired()) await ctlSay('⚠️ OpenClaw এখনও ready নয়। Status এবং /var/log/agent/openclaw.log পরীক্ষা করুন।', controls());
}

if (e.DISABLE_TELEGRAM !== '1') {
  void (async () => {
    await ctlSay('Onvix launcher চালু। OmniRoute-এ provider, combo এবং API key সেট করুন।', {
      inline_keyboard: [[{ text: 'OmniRoute Dashboard', url: e.DASHBOARD_URL }], [{ text: 'Setup & Start', url: setupUrl }]],
    });
    if (hasKeys() && desired()) void announce();
  })();
  void (async () => {
    let offset = Number(read(`${STATE}/telegram-offset`, '0')) || 0;
    for (;;) {
      const r = await tg(e.CONTROL_BOT_TOKEN, 'getUpdates', { offset, timeout: 30, allowed_updates: ['callback_query'] });
      for (const update of Array.isArray(r.result) ? r.result : []) {
        offset = update.update_id + 1;
        const q = update.callback_query;
        if (q && String(q.from.id) === String(e.OWNER_ID)) {
          try {
            if (['restart', 'stop'].includes(q.data)) command(q.data);
            await tg(e.CONTROL_BOT_TOKEN, 'answerCallbackQuery', { callback_query_id: q.id, text: q.data === 'status' ? status() : q.data });
          } catch { await ctlSay('⚠️ কমান্ড ব্যর্থ। আগে Setup-এ সঠিক API key দিন।'); }
        }
        atomicWrite(`${STATE}/telegram-offset`, String(offset));
      }
      if (!r.ok) await sleep(5000);
    }
  })().catch(() => { console.error('Control polling failed'); process.exit(1); });
}
