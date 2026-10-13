// Launcher: control-bot notification + token-protected setup page (OmniRoute keys -> Start OpenClaw).
import http from "node:http";
import fs from "node:fs";

const e = process.env;
const STATE = e.STATE_DIR;
const CTL = e.CTL_DIR;
const KEYS = `${STATE}/keys.json`;
const topics = { coding: e.TG_TOPIC_CODING, writing: e.TG_TOPIC_WRITING, video: e.TG_TOPIC_VIDEO };

const tg = async (token, method, body) => {
  try {
    const r = await fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    return await r.json();
  } catch (err) {
    console.error(method, err.message);
    return {};
  }
};
const ctlSay = (text, markup) =>
  tg(e.CONTROL_BOT_TOKEN, "sendMessage", { chat_id: e.OWNER_ID, text, ...(markup ? { reply_markup: markup } : {}) });
const status = () => (fs.existsSync(`${CTL}/status`) ? fs.readFileSync(`${CTL}/status`, "utf8").trim() : "unknown");
const hasKeys = () => fs.existsSync(KEYS);
const setupUrl = `${e.LAUNCHER_URL}/?t=${e.LAUNCHER_TOKEN}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const page = (msg = "") => `<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Onvix Agents</title><body style="font:16px system-ui;max-width:520px;margin:24px auto;padding:0 16px">
<h2>Onvix Agents</h2><p>OpenClaw: <b>${esc(status())}</b> ${esc(msg)}</p>
<form method=post action="/start"><input type=hidden name=t value="${esc(e.LAUNCHER_TOKEN)}">
<p>OmniRoute dashboard-এ বানানো তিনটি API key (key-এর নাম আর combo-র নাম: coding, writing, video):</p>
${["coding", "writing", "video"].map((n) => `<label>${n} key<br><input name=${n} type=password required style="width:100%;padding:8px;margin:4px 0 12px"></label>`).join("")}
<button style="padding:10px 18px;font-size:16px">Start OpenClaw</button></form>
<form method=post action="/ctl"><input type=hidden name=t value="${esc(e.LAUNCHER_TOKEN)}">
<p><button name=cmd value=restart>Restart</button> <button name=cmd value=stop>Stop</button></p></form></body>`;

const body = (req) =>
  new Promise((res) => {
    let d = "";
    req.on("data", (c) => (d += c));
    req.on("end", () => res(new URLSearchParams(d)));
  });

function command(cmd) {
  fs.writeFileSync(`${CTL}/cmd`, cmd);
}

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    const form = req.method === "POST" ? await body(req) : url.searchParams;
    if (form.get("t") !== e.LAUNCHER_TOKEN) {
      res.writeHead(403).end("forbidden");
      return;
    }
    const html = (m) => res.writeHead(200, { "content-type": "text/html; charset=utf-8" }).end(page(m));
    if (url.pathname === "/start" && req.method === "POST") {
      const k = { coding: form.get("coding"), writing: form.get("writing"), video: form.get("video") };
      if (!k.coding || !k.writing || !k.video) return html("সব key দিন");
      fs.writeFileSync(KEYS, JSON.stringify(k), { mode: 0o600 });
      fs.writeFileSync(`${STATE}/started`, "1");
      command("start");
      announce();
      return html("— শুরু হচ্ছে, Telegram-এ মেসেজ আসবে");
    }
    if (url.pathname === "/ctl" && req.method === "POST") {
      command(form.get("cmd") === "stop" ? "stop" : "restart");
      return html("— কমান্ড পাঠানো হয়েছে");
    }
    html();
  })
  .listen(8080, "0.0.0.0");

let announcing = false;
async function announce() {
  if (announcing) return;
  announcing = true;
  for (let i = 0; i < 90; i++) {
    await sleep(2000);
    try {
      await fetch("http://127.0.0.1:18789/");
      break;
    } catch {}
  }
  for (const [n, id] of Object.entries(topics)) {
    await tg(e.OPENCLAW_BOT_TOKEN, "sendMessage", {
      chat_id: e.TG_CHAT_ID,
      message_thread_id: Number(id),
      text: `✅ ${n} agent প্রস্তুত। কাজ এখানে লিখুন; একাধিক কাজ দিলে একটার পর একটা করে করব।`,
    });
  }
  await ctlSay("✅ OpenClaw চালু হয়েছে।", controls());
  announcing = false;
}

const controls = () => ({
  inline_keyboard: [
    [
      { text: "🔄 Restart", callback_data: "restart" },
      { text: "⏹ Stop", callback_data: "stop" },
      { text: "ℹ️ Status", callback_data: "status" },
    ],
  ],
});

// boot notification
(async () => {
  if (hasKeys() && fs.existsSync(`${STATE}/started`)) {
    await ctlSay("♻️ Sandbox আবার চালু হয়েছে, OpenClaw নিজে থেকেই শুরু হচ্ছে।", {
      inline_keyboard: [[{ text: "OmniRoute Dashboard", url: e.DASHBOARD_URL }]],
    });
    announce();
  } else {
    await ctlSay("🚀 OmniRoute চালু। Dashboard-এ provider, combo (coding, writing, video) ও তিনটি API key বানান, তারপর Setup & Start চাপুন।", {
      inline_keyboard: [
        [{ text: "OmniRoute Dashboard", url: e.DASHBOARD_URL }],
        [{ text: "▶ Setup & Start", url: setupUrl }],
      ],
    });
  }
})();

// control buttons (owner only)
let offset = 0;
(async () => {
  for (;;) {
    const r = await tg(e.CONTROL_BOT_TOKEN, "getUpdates", { offset, timeout: 30, allowed_updates: ["callback_query", "message"] });
    for (const u of r.result || []) {
      offset = u.update_id + 1;
      const q = u.callback_query;
      if (q && String(q.from.id) === String(e.OWNER_ID)) {
        if (q.data === "restart" || q.data === "stop") command(q.data);
        await tg(e.CONTROL_BOT_TOKEN, "answerCallbackQuery", { callback_query_id: q.id, text: q.data === "status" ? status() : q.data });
      }
    }
    if (!r.ok) await sleep(5000);
  }
})();
