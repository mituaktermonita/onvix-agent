# Onvix Agent

A pinned Node runtime running OmniRoute, OpenClaw and a token-protected launcher in a Daytona sandbox. **বাংলা installation/update guide: [README-BN.md](README-BN.md).**

## Runtime contract

- Node **24.21.0**, Debian Bookworm slim. Node 20 is not supported by the pinned applications.
- OpenClaw **2026.9.8**; OmniRoute **3.8.51**. All npm dependency resolutions and integrity hashes are recorded in `runtime/package-lock.json`.
- Deployment client: Python 3.11+; `daytona==0.224.0`, `python-dotenv==1.2.1`.
- Image platform currently tested by CI: `linux/amd64`. Do not assume ARM/native-addon compatibility without another build/test run.
- Dedicated OS users and private home directories for the three services. They do not run as root. Root is used only for the supervisor, permissions, state restore/snapshot and process lifecycle.

There is no claim that the old screenshot's `TransformError` was conclusively diagnosed: its primary error/file/line were missing. The previous Node 20 change was unsuitable. This release starts OmniRoute's pinned prebuilt `dist/server-ws.mjs`, the same server entry used by its `serve` command, instead of loading the CLI's much larger runtime TypeScript/tsx command registry. It retains the WebSocket wrapper, unlike running `server.js` alone.

## Build and validation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/*.test.mjs
shellcheck app/*.sh scripts/*.sh

docker build --build-arg VCS_REF="$(git rev-parse HEAD)" -t onvix:test .
docker run --rm --network none --entrypoint /usr/bin/tini onvix:test -- bash /opt/tests/image-smoke.sh
docker run --rm --network none --tmpfs /vol:rw,size=512m -e ONVIX_DISPOSABLE_TEST=1 --entrypoint /usr/bin/tini onvix:test -- bash /opt/tests/supervisor-smoke.sh
```

Run JavaScript tests with Node 24.21.0. Docker must be installed locally for the last three commands. The supervisor smoke test must **never** be run in a real sandbox: it assumes disposable, empty state.

The `tested-image` workflow builds on PRs and main, tests the candidate, and publishes the **same image without rebuilding** only on main. The deployment reference is `ghcr.io/mituaktermonita/onvix-agent:sha-<full-commit-SHA>` or an image digest. `latest` is published for convenience but rejected by the deployment client. Commit tags are not registry-enforced immutability; use `@sha256:...` for strict content addressing. Debian apt packages and native postinstall downloads are not fully bit-for-bit reproducible, even though npm resolutions are locked. Test every rebuilt image.

## Deployment and upgrades

Fill `.env` from `.env.example` only for a **new** install. Preserve your existing `.env` for an upgrade. Never replace it with the blank example. Generated secrets are saved before use; credentials are not printed and `.env` is excluded from Git and the Docker context. Keep an independent secure backup of it.

```bash
python deploy.py --image ghcr.io/mituaktermonita/onvix-agent:sha-<full-commit-SHA>
```

The image must first be published by a successful workflow and be readable by Daytona (public GHCR package, or separately configured private-registry access). The placeholder SHA must be replaced. Default sandbox/volume: `onvix-agents` / `onvix-agents-data`.

The default resource request is 4 CPUs, 8 GiB RAM, 20 GiB disk; check your Daytona quota and cost. Auto-stop is disabled. The sandbox is public **with application authentication**: OmniRoute password/API key, launcher bearer token, OpenClaw token/device pairing. Do not open additional unauthenticated agent development ports publicly.

An existing sandbox with a different or untracked image is refused, not silently reused or deleted. For an explicit state-preserving upgrade:

```bash
# Finish ongoing work; source must be Started and your ORIGINAL .env present.
python scripts/prepare_upgrade.py --name onvix-agents --volume onvix-agents-data --confirm-stop
# Only after successful preparation, use a new name and the same volume:
python deploy.py --image ghcr.io/mituaktermonita/onvix-agent:sha-<full-commit-SHA> --name onvix-agents-v2 --volume onvix-agents-data
```

Preparation checks the local encryption/API secrets against the source, stops the old writers, takes a new snapshot, and stops (does not delete) the old sandbox. It uploads temporary migration utilities under `/run/onvix-migrate`, not over the installed application. Active source jobs can be interrupted: explicit `--confirm-stop` is required. If any step fails, keep both the source sandbox and volume, inspect the error and do not proceed by deleting data. The old raw `/vol/<service>` layout is supported, but a new quiesced snapshot is recommended before migration.

Do not restart the old sandbox while the new one uses the same volume. The deployment client checks active volume attachments, including legacy sandboxes without labels. This is a preflight check, **not a distributed lock**: never run concurrent deployments against a shared volume. Rollback after data has been upgraded may not be schema-compatible; preserve the original sandbox and independent volume/.env backups. Do not blindly copy new state back into an old version.

## Service lifecycle

1. Runtime versions, secrets and persistent mount are checked before startup.
2. A stored encryption-key fingerprint rejects accidental key changes on subsequent launches. Legacy volumes without a fingerprint still require the original key; the fingerprint cannot recover a lost key.
3. Volume state is restored only once into a fresh local data directory. Existing local state is never overwritten by an older snapshot on supervisor restart.
4. OmniRoute starts first. If its health check fails, startup fails visibly rather than announcing success.
5. The launcher becomes available. OpenClaw starts only after keys are provided, or if its saved desired state says it should run.
6. Before OpenClaw launch, the generated config is validated by the **installed OpenClaw CLI schema**. Its gateway uses `mode: local`, `bind: lan`, token auth and the specific Daytona allowed origin.
7. Readiness, not merely process existence, controls the OpenClaw status/ready notification. A channel outage may leave a live gateway `not-ready`; it is not endlessly killed for an external outage.
8. Crashed processes have bounded exponential restart delays. A supervisor lock prevents duplicate supervisors; child services do not retain its lock descriptor.
9. `bash /opt/app/supervisor.sh --stop` quiesces writers and waits for a final backup. Deploy checks its result before replacing the runtime environment.

Daytona can override an image entrypoint. Therefore `deploy.py` explicitly launches the supervisor in a background SDK session and waits for health checks. A standalone local image starts the supervisor only if a root-readable shell-format `/run/agent.env` exists; otherwise it waits for injection. Do **not** mount an arbitrary dotenv file as executable shell configuration. The deployment client handles safe shell quoting.

Ports: OmniRoute `20128`, launcher `8080`, OpenClaw `18789`. Logs: `/var/log/agent/{omniroute,openclaw,render,backup}.log` and `/var/log/supervisor.out`. Log rotation uses `copytruncate`; a very small amount of output can be lost during truncation. Application healthcheck failures report unhealthy status; Docker HEALTHCHECK alone is not a restart policy.

## Data safety and limitations

- Live databases remain on the local filesystem, not on the Daytona FUSE volume.
- Every five minutes, Python's SQLite online backup API produces each database snapshot **on local disk**, closes it in DELETE journal mode, validates it, and copies the single-file result to the volume. Raw live WAL/SHM files are not copied.
- A snapshot gets a `complete.json` and then a `CURRENT` pointer only after all services were copied. The last three completed snapshots are retained. Incomplete snapshots are not restored; they may need manual cleanup after repeated failures.
- Each database is internally consistent; live snapshots are **not cross-service transactions**. Non-database files may change during a live snapshot. Use a graceful stop/quiesced snapshot before migration. An abrupt kill can lose work since the last completed snapshot (normally up to five minutes; longer if backups fail).
- Unfinished `work` files are preserved. `node_modules`, `.cache`, `.npm`, `logs`, log/temp files and symbolic links are omitted from snapshots. Reinstall project dependencies after restore. These are state snapshots, not full filesystem/VM backups.
- The mount must support basic file writes/rename/permissions; real Daytona FUSE behavior must still be verified on first deployment. Check `backup.log`, free disk/volume space and `/vol/CURRENT`. Large work directories can exceed the bounded backup time; move deliverables to Drive and investigate backup failures before deleting a sandbox.
- No age-based janitor deletes active work or arbitrary `/tmp` content.
- The persistent volume contains sensitive data, including application state/API keys. Access control and an independent backup remain necessary. Root/runtime environment secrets are not included in the image; some rendered provider credentials are part of the persisted application state.
- Main/subagent concurrency settings reduce parallel work, but are **not a durable global FIFO scheduler** across all agent lanes.
- The three OpenClaw agents share one OpenClaw OS user. Separate workspace/Drive folder configuration is routing, **not hostile-agent security isolation**. Use separate sandboxes/accounts for mutually untrusted agents. The Drive helper restricts its upload path and agent names, streams uploads, preserves files on unconfirmed failures, and deletes only an unchanged local file after Drive returns a file ID.
- Google OAuth must allow Drive access to the chosen folders. Telegram topics, bot membership/permissions, provider credentials and the user's real Drive account require an end-to-end acceptance test.

## Version-specific documentation reviewed

Network access in the authoring environment was limited to npm/PyPI. The following **published package documentation and source** were inspected rather than assuming the latest website schema:

- [OpenClaw 2026.9.8 npm metadata](https://registry.npmjs.org/openclaw/2026.9.8): `package.json` engines; bundled `docs/gateway/config-gateway.md`, `docs/gateway/config-agents/entries-and-multi-agent.md`, `docs/gateway/health.md`, `docs/channels/telegram/threads-and-sessions.md` and `access-control.md`. `agents.entries` is valid for this pinned version; it was not incorrectly rewritten to another version's schema.
- [OmniRoute 3.8.51 npm metadata](https://registry.npmjs.org/omniroute/3.8.51): `package.json` engines; `bin/omniroute.mjs`, `bin/cli/commands/serve.mjs`, `dist/server-ws.mjs`, `.env.example`, encryption source. The full production server was started in tests.
- [Daytona 0.224.0 Python distribution](https://pypi.org/project/daytona/0.224.0/): SDK source/docstrings for `Daytona.get/create/list`, `Sandbox.start/stop/get_preview_link`, `Process.exec/execute_session_command`, filesystem upload, volume/resource params and typed not-found errors.

Additional operator references (not fetched during the restricted audit): [Google Drive uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads), [Docker healthchecks](https://docs.docker.com/reference/dockerfile/#healthcheck), [Python SQLite backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

See [docs/VALIDATION.md](docs/VALIDATION.md) for the distinction between tests actually executed and tests still required.
