# Onvix Agent — update, build ও Daytona deployment

এই release পুরোনো Node 20 downgrade সংশোধন করে। `.env` নতুন করে বানাতে হবে না: আপনার **আগের আসল `.env` রাখুন**, বিশেষ করে `STORAGE_ENCRYPTION_KEY` ও `API_KEY_SECRET` পরিবর্তন করবেন না।

## ১. ZIP থেকে লোকাল project update

ZIP-টি **project folder-এর বাইরে** extract করুন। Extract করা `onvix-agent-ready` folder-এ terminal খুলুন। Python 3.11+ ব্যবহার করুন।

```bash
python3 apply_update.py --target /আপনার/লোকাল/onvix-agent --dry-run
python3 apply_update.py --target /আপনার/লোকাল/onvix-agent
```

Windows-এ `python3`-এর বদলে `python` এবং নিজের path ব্যবহার করুন। ZIP-এর ভেতরে source files সরাসরি root-এ আছে—এটি পুরো fresh project folder-ও। Existing project update করলে নিচের নিরাপদ script ব্যবহার করাই ভালো।

Script:

- আগে package-এর প্রতিটি source file-এর SHA-256 মিলিয়ে নেয়;
- যেসব ফাইল replace হবে, সেগুলোর backup project-এর **পাশে আলাদা folder-এ** রাখে;
- `.env`, `.git`, virtual environment ও অচেনা/অতিরিক্ত files মুছে না;
- পুরোনো `.gitignore` rules রেখে প্রয়োজনীয় নতুন rules যোগ করে;
- নিজে Git commit/push করে না, branch পরিবর্তন করে না।

এটি source update, destructive project cleanup নয়। পুরোনো `onvix-agent.zip` থাকলে সেটি এই updater পরিবর্তন করে না; সেটি এখনকার source-এর বিকল্প নয়।

## ২. আপনার লোকাল Git থেকে commit ও push

```bash
cd /আপনার/লোকাল/onvix-agent
git status --short
git diff --check
git diff --stat
git branch --show-current
```

নিচের `git add` শুধু এই release-এর source/configuration files stage করে; `.env` বা অন্য ব্যক্তিগত ফাইল যোগ করবেন না।

```bash
git add Dockerfile .dockerignore .gitignore .env.example .github app deploy.py requirements.txt runtime/package.json runtime/package-lock.json scripts tests README.md README-BN.md docs
git diff --cached --stat
git commit -m "Harden Onvix runtime and Daytona deployment"
git push
```

আপনার স্বাভাবিক Git workflow-এ changes **main-এ পৌঁছাতে হবে**। feature branch হলে PR merge করুন। এই session-এর assistant GitHub-এ আর push/PR করতে পারবে না; এটি আপনার লোকাল terminal-এর নির্দেশনা।

## ৩. Image তৈরি ও পরীক্ষা শেষ হওয়ার জন্য অপেক্ষা করুন

Repository Actions-এ **tested-image** workflow সবুজ না হওয়া পর্যন্ত deploy করবেন না। Workflow:

1. unit tests চালাবে;
2. Docker image build করবে;
3. আলাদা non-root service users দিয়ে আসল OmniRoute, OpenClaw ও launcher চালাবে;
4. supervisor start/stop/backup/restart পরীক্ষা করবে;
5. সফল হলে **একই পরীক্ষিত image** `ghcr.io/mituaktermonita/onvix-agent:sha-<পুরো-commit-SHA>` নামে publish করবে।

Workflow summary থেকে image reference copy করুন। PR merge হলে merge commit-এর image ব্যবহার করুন, feature branch-এর SHA নয়। GHCR package Daytona-র জন্য readable হতে হবে: public package করুন, অথবা Daytona-তে private registry access configure করুন।

## ৪. লোকালে deployment dependencies install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell: `.venv\Scripts\Activate.ps1`।

নিজের existing `.env` এই project root-এ রাখুন। `.env.example` দিয়ে সেটি overwrite করবেন না। Deployment প্রয়োজনীয় value এবং তিনটি আলাদা Telegram topic ID পরীক্ষা করবে। General topic `1` main agent-এর জন্য রাখা আছে।

## ৫. আগে থেকেই থাকা Daytona sandbox আপডেট — প্রথমবার এই ধাপ জরুরি

**শুধু sandbox restart করলে নতুন Docker image আসে না।** এই সংস্করণ ইচ্ছা করে পুরোনো sandbox চুপচাপ delete করে না বা পুরোনো image-কে নতুন বলে চালায় না।

### পুরোনো state/OmniRoute configuration রেখে migration

পুরোনো sandbox `Started` রাখুন। চলমান agent কাজ শেষ হতে দিন। তারপর লোকাল terminal-এ:

```bash
python scripts/prepare_upgrade.py --name onvix-agents --volume onvix-agents-data --confirm-stop
```

এই command:

- local `.env`-এর encryption/API secrets পুরোনো sandbox-এর সঙ্গে মিলিয়ে দেখে;
- application writers বন্ধ করে;
- চলন্ত SQLite ফাইল raw-copy না করে নতুন consistent database snapshots বানায়;
- সফল snapshot-এর পরে পুরোনো sandbox **stop** করে, delete করে না।

**Command ব্যর্থ হলে পরের ধাপে যাবেন না।** পুরোনো sandbox/volume রেখে logs দেখুন। নতুন data, secrets বা volume বানিয়ে সমস্যাটি ঢাকবেন না।

তারপর workflow summary থেকে copy করা **আসল** image reference বসান:

```bash
python deploy.py --image ghcr.io/mituaktermonita/onvix-agent:sha-<FULL_COMMIT_SHA> --name onvix-agents-v2 --volume onvix-agents-data
```

`<FULL_COMMIT_SHA>` লেখা আক্ষরিকভাবে রাখবেন না; পুরো 40-character commit SHA বসাতে হবে। পুরোনো ও নতুন sandbox একই volume ব্যবহার করলে **একসঙ্গে চালু করবেন না**। API-তে অন্য active writer পাওয়া গেলে deployment থেমে যাবে। দুইটি terminal থেকে একসঙ্গে migration/deployment চালাবেন না।

### সম্পূর্ণ নতুন installation

যদি আগে কোনো sandbox/state না থাকে, preparation লাগবে না:

```bash
python deploy.py --image ghcr.io/mituaktermonita/onvix-agent:sha-<FULL_COMMIT_SHA>
```

### একই image আবার deploy/restart

সফল deployment-এর একই `--image`, `--name`, `--volume` দিয়ে একই command আবার চালাতে পারেন। ভিন্ন নতুন image হলে আগের মতো explicit migration করুন এবং নতুন sandbox name দিন। `.env` একই রাখুন।

## ৬. Setup ও প্রথম real test

1. Control bot-এর message থেকে OmniRoute dashboard ও tokenized Setup link খুলুন।
2. Migration-এ আগের settings এলে আগে সেগুলো যাচাই করুন। নতুন setup হলে provider credentials, `coding`, `writing`, `video` combo এবং API keys তৈরি করুন।
3. Setup page-এ keys দিয়ে **Start OpenClaw** চাপুন।
4. OpenClaw-এর `/readyz` সফল হলে তবেই ready message যাবে। Dashboard gateway প্রথমবার device approval চাইলে তা স্বাভাবিক নিরাপত্তা; authentication বন্ধ করবেন না।
5. Telegram-এর তিন topic-এ ছোট test message দিন এবং প্রতিটি Drive folder-এ ছোট test file upload করান।

**এখানে আপনার real Telegram/Drive/provider credentials বা Daytona service-এ end-to-end test করা সম্ভব হয়নি।** `docs/VALIDATION.md`-এ কোন পরীক্ষা করা হয়েছে, কোনটি বাকি, স্পষ্ট লেখা আছে। ভবিষ্যতে কোনো dependency বা external API কখনও ব্যর্থ হবে না—এমন নিশ্চয়তা কোনো package দিতে পারে না।

## সমস্যা হলে কোথায় দেখবেন

Daytona terminal-এ:

```bash
node -v
node /opt/app/verify-runtime.mjs
cat /opt/app/revision
bash /opt/app/healthcheck.sh
cat /run/ctl/status
tail -n 120 /var/log/agent/omniroute.log
tail -n 120 /var/log/agent/openclaw.log
tail -n 120 /var/log/agent/render.log
tail -n 80 /var/log/agent/backup.log
```

Log শেয়ার করার আগে token, API key, ব্যক্তিগত লেখা ও credential মুছে দিন। `TransformError` হলে error-এর **প্রথম message এবং file/line** দরকার, শুধু শেষ stack trace নয়।

Graceful stop + final backup:

```bash
bash /opt/app/supervisor.sh --stop
```

Backup failure দেখালে sandbox delete করবেন না। `.env` এবং volume-এর independent backup রাখুন; volume-এও sensitive application data থাকে। বিস্তারিত architecture, সীমাবদ্ধতা ও documentation references: `README.md`।
