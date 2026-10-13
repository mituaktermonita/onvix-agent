// Runs as root before each OpenClaw start. Writes openclaw.json, agent workspaces, Drive credentials.
import fs from "node:fs";
import path from "node:path";

const e = process.env;
const HOME = "/home/openclaw";
const OC = `${HOME}/.openclaw`;
const keys = JSON.parse(fs.readFileSync("/home/launcher/state/keys.json", "utf8"));

const agents = {
  coding: {
    topic: e.TG_TOPIC_CODING,
    folder: e.GDRIVE_FOLDER_CODING,
    prompt: "You are the coding agent. Build anything the user asks for: apps, scripts, automation, fixes. Work in your workspace, test what you write, and upload finished deliverables to your Google Drive folder using the drive skill.",
    tools: { profile: "coding" },
  },
  writing: {
    topic: e.TG_TOPIC_WRITING,
    folder: e.GDRIVE_FOLDER_WRITING,
    prompt: "You are the writing agent. Help with any kind of writing: articles, emails, scripts, copy, editing, translation. Upload finished documents to your Google Drive folder using the drive skill.",
  },
  video: {
    topic: e.TG_TOPIC_VIDEO,
    folder: e.GDRIVE_FOLDER_VIDEO,
    prompt: "You are the video agent. Your video workflow, tools and rules will be provided later through skills and instructions. Until then, ask before doing anything. Upload finished files to your Google Drive folder using the drive skill.",
  },
};

const MAIN_PROMPT =
  "You are the main Onvix agent. You have exactly three sub-agents: coding (builds anything), writing (any kind of writing), video (video work). Understand the user's request here, delegate each task to the right sub-agent, and report back. Tasks run strictly one at a time in the order received.";

const cfg = {
  gateway: {
    port: 18789,
    bind: "loopback",
    auth: { mode: "token", token: "${OPENCLAW_GATEWAY_TOKEN}" },
    trustedProxies: ["127.0.0.1"],
    controlUi: { allowedOrigins: [e.GATEWAY_URL] },
  },
  // one job at a time across all agents; extra requests wait in a FIFO queue and run in order
  messages: { queue: { mode: "followup", cap: 50, drop: "summarize" } },
  models: { mode: "merge", providers: {} },
  agents: { defaults: { maxConcurrent: 1, subagents: { maxConcurrent: 1 } }, entries: {} },
  channels: {
    telegram: {
      enabled: true,
      botToken: "${TELEGRAM_BOT_TOKEN}",
      dmPolicy: "allowlist",
      allowFrom: [Number(e.OWNER_ID)],
      groupPolicy: "allowlist",
      groups: { [e.TG_CHAT_ID]: { requireMention: false, topics: {} } },
    },
  },
};

const RULES =
  " Rules for every job: follow the user's instructions exactly; do one job at a time (further requests are queued for you automatically); do all scratch work inside ./work; when the job is done, upload the deliverable to Drive (the upload deletes the local copy), delete everything else you created in ./work and any temp files, caches or downloads, and then report the result.";

fs.mkdirSync(`${OC}/workspace-main`, { recursive: true });
cfg.agents.entries.main = {
  name: "Main Agent",
  workspace: `${OC}/workspace-main`,
  agentDir: `${OC}/agents/main/agent`,
  model: "omniroute-coding/coding", // the main agent reuses the coding combo/key
  subagents: { allowAgents: Object.keys(agents) },
};
// General topic (id 1) talks to the main agent; each other topic goes straight to its sub-agent
cfg.channels.telegram.groups[e.TG_CHAT_ID].topics["1"] = { agentId: "main", systemPrompt: MAIN_PROMPT };

for (const [id, a] of Object.entries(agents)) {
  a.prompt += RULES;
  const ws = `${OC}/workspace-${id}`;
  fs.mkdirSync(`${ws}/work`, { recursive: true });
  fs.mkdirSync(`${ws}/skills/drive`, { recursive: true });
  fs.copyFileSync("/opt/app/skills/drive/drive.mjs", `${ws}/skills/drive/drive.mjs`);
  fs.writeFileSync(
    `${ws}/skills/drive/SKILL.md`,
    fs.readFileSync("/opt/app/skills/drive/SKILL.md", "utf8").replaceAll("{{AGENT}}", id).replaceAll("{{WS}}", ws),
  );
  fs.mkdirSync(`${HOME}/.drive`, { recursive: true, mode: 0o700 });
  fs.writeFileSync(
    `${HOME}/.drive/${id}.json`,
    JSON.stringify({
      clientId: e.GDRIVE_CLIENT_ID,
      clientSecret: e.GDRIVE_CLIENT_SECRET,
      refreshToken: e.GDRIVE_REFRESH_TOKEN,
      folderId: a.folder,
    }),
    { mode: 0o600 },
  );

  cfg.models.providers[`omniroute-${id}`] = {
    baseUrl: "http://127.0.0.1:20128/v1",
    apiKey: `\${OMNI_KEY_${id.toUpperCase()}}`,
    api: "openai-completions",
    models: [{ id, name: `${id} combo`, reasoning: false, input: ["text"], contextWindow: 128000, maxTokens: 16000 }],
  };
  cfg.agents.entries[id] = {
    name: `${id[0].toUpperCase()}${id.slice(1)} Agent`,
    workspace: ws,
    agentDir: `${OC}/agents/${id}/agent`,
    model: `omniroute-${id}/${id}`,
    ...(a.tools ? { tools: a.tools } : {}),
  };
  cfg.channels.telegram.groups[e.TG_CHAT_ID].topics[a.topic] = { agentId: id, systemPrompt: a.prompt };
}

fs.mkdirSync(OC, { recursive: true });
fs.writeFileSync(`${OC}/openclaw.json`, JSON.stringify(cfg, null, 2));
// OpenClaw reads ~/.openclaw/.env as a global fallback; holds only the per-agent OmniRoute keys
fs.writeFileSync(
  `${OC}/.env`,
  ["CODING", "WRITING", "VIDEO"].map((n) => `OMNI_KEY_${n}=${keys[n.toLowerCase()]}`).join("\n") + "\n",
  { mode: 0o600 },
);
