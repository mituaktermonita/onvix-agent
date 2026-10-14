# Validation record — 2026-10-14

This is a tested **source release**, not a prebuilt Docker image or a guarantee about the user's remote services.

## Executed successfully in the authoring sandbox

- **29 Python tests**: deployment preflight, typed not-found handling, immutable image selection, active-volume writer rejection, safe shell quoting, secret persistence, SQLite WAL backup/restore, incomplete snapshots, changed encryption key rejection, restore refusal over local data, updater preservation/backup/checksum checks, and mocked upgrade success/failure paths.
- **6 JavaScript tests**: private atomic files, API-key injection rejection, generated configuration/routing, launcher authentication/request limits/start/stop intent, Drive upload confirmation/failure preservation and path restrictions.
- ShellCheck on `app/*.sh` and `scripts/*.sh`; shell/JavaScript/Python syntax and `git diff --check`.
- Installed the locked npm runtime using Node **24.21.0**. Runtime engine/version verification succeeded for OpenClaw **2026.9.8** and OmniRoute **3.8.51**.
- Started the actual prebuilt OmniRoute server: health endpoint and login returned **200**; unauthenticated `/v1/models` returned **401**.
- Generated the production OpenClaw configuration and ran `openclaw config validate` using the installed version's actual schema. Then disabled Telegram only in the synthetic test fixture and started the gateway: **`/readyz` succeeded**.
- Started the launcher with synthetic credentials: `/healthz` succeeded, unauthenticated page requests returned **403**, authenticated setup requests succeeded.
- Repeated application startup as separate **omniroute/openclaw/launcher OS users**, not root. This caught and fixed an inherited working-directory permission issue (`EACCES`). Test processes use private synthetic home directories, check for occupied ports first, and are terminated as process groups after testing.
- Exercised the real supervisor locally with an empty tmpfs volume: readiness, duplicate-supervisor lock, graceful stop, final snapshot, restart without restoring over newer local state, and another final snapshot. This uses local Linux processes/mounts, **not the Daytona API or a built Docker container**.
- The distributed ZIP is checked for manifest hashes, unsafe/unexpected paths and excluded secrets/dependency caches. Its updater is exercised against a temporary project with an existing `.env` and `.git`.

## Still required before calling the deployment production-verified

1. The packaged GitHub Actions workflow must build and test the **Docker image itself**. Docker was not available in the authoring sandbox; the image was not built or pushed here. The workflow blocks publishing when unit, application boot or supervisor smoke tests fail, and publishes the exact tested candidate.
2. Execute the explicit migration helper against the real Daytona sandbox, then create the replacement sandbox using the successful image reference. The helper's SDK interactions have mock tests, but no real Daytona credentials/API calls were used here.
3. Confirm Daytona image pulling, quota, public preview routing, persistent FUSE volume permissions/rename behavior and snapshot restoration on a real deployment.
4. Test real Telegram bot access/topic permissions, provider keys/models and Google Drive OAuth/upload/download. Application boot tests use synthetic credentials; Drive unit tests mock HTTP.
5. Re-test after dependency/base image updates. Load/cost benchmarking, adversarial agent isolation and a durable global job scheduler are not included.

## Screenshot diagnosis boundary

The screenshot showed the tail of an esbuild `TransformError` under OmniRoute and Node 24.21.0. It did **not** include the primary message or source file/line. Therefore the exact old exception cannot honestly be claimed as reproduced or conclusively fixed. Node 20 was outside both pinned packages' declared engine support. The new runtime is compatible, pinned and tested; using the prebuilt server removes the CLI's TypeScript transform chain from normal server startup. Full original logs are still needed if that old CLI-specific error must be diagnosed independently.

## Published documentation/source used

Version-specific source/docs came from the npm distributions for `openclaw@2026.9.8` and `omniroute@3.8.51`, and the PyPI distribution for `daytona==0.224.0`. See README for exact files and references. External documentation websites and GitHub operations were not available/used for this finished coding session.
