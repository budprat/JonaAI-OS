# Production deployment

A reference deployment for running JonaAI on a single small Linux host: systemd and Python venvs, no containers. It is how the project's own instance runs, with the host's names and accounts left out. Adapt the paths, names and limits to your host.

The unit files, nginx site and env file live on the host, not in this repository. Secrets go in the env file only. Never copy them into the repo or a chat.

## Request path

```text
browser -> Cloudflare Access -> Cloudflare Tunnel -> nginx 127.0.0.1:80 -> uvicorn 127.0.0.1:8080
```

Any reverse proxy and identity-aware gateway can play these roles. The app has built-in support for Cloudflare Access.

- **Cloudflare Access** admits only the accounts you allow. Its token travels as `Cf-Access-Jwt-Assertion`.
- **The tunnel** points your public hostname at `http://localhost:80`. The tunnel encrypts traffic itself, and the origin speaks plain HTTP.
- **nginx** (for example `/etc/nginx/sites-available/jonaai`):
  - refuses requests with no Access token;
  - returns 404 for `/openeo`, `/health`, `/metrics`, `/docs`, `/redoc` and `/openapi.json`;
  - logs paths without query strings, so tokens in URLs stay out of the logs.
- **The app** checks the Access token's signature, audience and issuer (`openeo_ai/access_guard.py`, `JONA_ACCESS_MODE=enforce`).
  - It checks only requests that came through the proxy, meaning they carry `CF-Connecting-IP` or `X-Forwarded-For`.
  - Internal calls from the agent's tools to `/openeo` go straight to `127.0.0.1:8080` and skip the check.

**Bind every listener to loopback.** If the host has no firewall, anything on `0.0.0.0` is public. After deploying, check with `ss -tlnp`.

## Layout

| Path | Contents |
| --- | --- |
| `/opt/jonaai` | Deployed code and `.venv`, owned by root. An rsync copy, not a git checkout |
| `/etc/jonaai/jonaai.env` | All settings and secrets, readable by root and the service group (`0640 root:jonaai`) |
| `/var/lib/jonaai` | State: the service's home, saved results in `.openeo_jobs/`, run logs in `.jona_runs/`, and working job results and tool outputs in `results/` (`RESULT_STORAGE_PATH`, removed after a day) |
| `/etc/systemd/system/jonaai.service` | The backend, which also serves the built frontend from `openeo_ai/frontend/dist` |
| `jonaai-backup.service`, `jonaai-backup.timer` | The nightly backup |

`jonaai.service` runs uvicorn on `openeo_ai.web_interface:asgi_app` (not `app`) with `--proxy-headers --ws-ping-interval 30 --ws-ping-timeout 120`. Recommended hardening:
- run it as a dedicated user, such as `jonaai`;
- `ProtectSystem=strict`, `PrivateTmp`, and no capabilities;
- `MemoryHigh`/`MemoryMax` sized to the host;
- `IPAddressDeny` for the cloud metadata range and any private network the agent shouldn't reach.

## Settings for a small host

The reference host has 1 vCPU and about 2 GB of RAM, shared with other services. Memory is the binding constraint, so these settings are much lower than the code defaults. Every setting is described in `CLAUDE.md` under "Limits".

| Setting | Value | Why |
| --- | --- | --- |
| `OPENEO_MAX_LOAD_ELEMENTS` | 15000000 | Measured at about 20 MB per million elements. A 40M job stalled a 2 GB host |
| `OPENEO_MAX_CONCURRENT_JOBS` | 1 | One core. A synchronous `POST /result` then waits behind any running job, including a timed-out or cancelled one whose computation is still stopping, and gets 503 after 300 s |
| `JONA_SDK_SESSION_TTL_SECONDS`, `JONA_SDK_SESSION_MAX_COUNT` | 300, 2 | Each idle agent session holds about 250 MB |
| `JONA_MAX_QUERY_USD`, `JONA_USER_DAILY_USD`, `JONA_MONTHLY_USD` | 10, 50, 100 | Spend limits |
| `JONA_REQUIRE_TOOL_USER` | true | Tool calls need a signed-in user. Turn it on in any deployment |
| `JONA_ALLOWED_EMAILS` | your accounts | Supabase sign-ups are open by default, so the app checks the email |
| `JONA_ACCESS_MODE` | enforce | Together with `JONA_ACCESS_TEAM_DOMAIN` and `JONA_ACCESS_AUD` |
| `MALLOC_ARENA_MAX` | 2 | Returns memory after jobs |
| `ADMIN_PASSWORD` | unset | Disables the legacy admin login |
| `OPENEO_DEV_MODE` | true | The agent's job tools use Basic Auth against the local OpenEO API, and nginx keeps `/openeo` private. Replace it before exposing `/openeo` |

On a host this small, turn off the monitoring worker (`EARTH_INTELLIGENCE_WORKER=false`). Earth-Agent's tools are offered only where its checkout and dependencies exist. Without a model file, the GeoAI tools refuse with `geoai_model_unavailable`.

## Deploying a change

Run the tests first (see the README). Then, from your source checkout:

```bash
# 1. Make sure no query is running: a restart ends an active run.
#    The client is told its run was cut off, but the work is lost.
journalctl -u jonaai --since "-10 min" | tail

# 2. Copy the code. Tests and the frontend source stay behind.
rsync -a --exclude='__pycache__' --exclude='tests/' --exclude='frontend/' openeo_ai/ /opt/jonaai/openeo_ai/
rsync -a --exclude='__pycache__' --exclude='tests/' openeo_app/ /opt/jonaai/openeo_app/
rsync -a scripts/ /opt/jonaai/scripts/

# 3. Frontend changes only: build, then copy the output.
(cd openeo_ai/frontend && npm run build)
rsync -a --delete openeo_ai/frontend/dist/ /opt/jonaai/openeo_ai/frontend/dist/

chown -R root:root /opt/jonaai/openeo_ai /opt/jonaai/openeo_app /opt/jonaai/scripts
systemctl restart jonaai

# 4. Wait for it to come up.
until curl -s -o /dev/null -m 2 http://127.0.0.1:8080/health; do sleep 2; done
```

On a one-core host, a build competes with live services. Check `free -m` and `/proc/loadavg` first, and don't run two builds at once.

**Settings.** Edit the env file and restart.
- The service reads the file only when it starts, so new keys do nothing until a restart. Changing the file needs no `daemon-reload`.
- Write plain `KEY=value` lines. systemd silently skips `export KEY=value`, which works in a shell, so a key can test fine from a shell and still be missing in the service.
- The env file overrides `Environment=` lines, so a test override needs its own `EnvironmentFile=` drop-in. Remove the drop-in afterwards.

**Migrations.** The database is Postgres, for example your own Supabase project (setup: `docs/supabase-setup.md`). Migrations are in `openeo_app/psql/alembic/versions/`. Read a new migration before running `alembic upgrade head` against production.

## Checks after a deploy

```bash
ss -tlnp | grep -v '127.0.0.1\|::1'          # every listener should be on loopback
curl -s http://127.0.0.1:8080/health
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: jonaai.example.org' -H 'CF-Connecting-IP: 192.0.2.1' http://127.0.0.1/
# expect 403: no Access token
```

## Crash telemetry

A native crash (SIGSEGV, SIGABRT, SIGBUS, SIGFPE) writes a Python traceback to `/var/lib/jonaai/jonaai-backend-fault.log`, which survives restarts.
- `kill -USR1 <pid>` writes a dump of every thread there, without stopping the service.
- `JONA_FAULT_LOG` sets another path, or `off`.
- If the file can't be opened, tracebacks go to stderr, which is the journal.
- It turns on when uvicorn imports the app (`openeo_ai/crash_telemetry.py`).

## Restart policy

`Restart=on-failure` restarts crashes, timeouts and OOM kills, but not a clean exit. `deploy/systemd/jonaai.service.d/10-restart.conf` changes it to `Restart=always`; the file explains why and how to install it.

## Backups

`scripts/backup_results.py` runs nightly from `jonaai-backup.timer`.
- It copies saved results and database rows to `JONA_BACKUP_DIR` (default `/mnt/backups/jonaai-backups`).
- It refuses to run unless that directory's parent is a mount point, so a missing volume can't fill the root disk. Add `RequiresMountsFor=` to the unit as well.
- It keeps 14 dated snapshots, and unchanged files are hard-linked.

**Offsite recovery copy (optional).** With the four `JONA_RECOVERY_*` settings, the nightly backup also sends each snapshot to a separate S3-compatible bucket.
- Saved results go by `rclone sync --backup-dir`, so anything deleted or changed upstream is kept in a dated `replaced/` folder for 90 days.
- The database dump goes as one dated object per night.
- Without the settings it behaves exactly as before. With only some of them set, the run fails loudly, after the local snapshot is done.

To set it up:

1. Create a private bucket for recovery, and a read-write token scoped to that bucket only.
2. Write `JONA_RECOVERY_ENDPOINT` (the account endpoint, with no path), `JONA_RECOVERY_BUCKET`, `JONA_RECOVERY_ACCESS_KEY_ID` and `JONA_RECOVERY_SECRET_ACCESS_KEY` as plain `KEY=value` lines to `/etc/jonaai/recovery.env`, mode `600 root:root`. The app runs as `jonaai` and can't read it.
3. Add `EnvironmentFile=/etc/jonaai/recovery.env` to `jonaai-backup.service` only, then run `systemctl daemon-reload`. `systemctl start jonaai-backup` runs it now.
4. Prove the isolation as root, with both env files loaded: `JONA_RECOVERY_ISOLATION_CHECK=1 pytest openeo_ai/tests/test_backup_recovery.py -k refused`. It checks that the app's token can't list the recovery bucket, and the reverse. If it fails, a token isn't scoped to one bucket, and the recovery copy isn't independent of the app.

## Offsite copies of saved results

`JONA_RESULT_COPIES=r2` (with the four `JONA_R2_*` settings) or `directory` (with `JONA_RESULT_COPIES_DIR`) keeps a second copy of each saved result. Startup logs `Result copies on: r2`, or `Result copies off:` with the missing setting.

**Scope the app's token to its one bucket**, then confirm it can't list or read any other bucket on the account. The isolation check in step 4 above tests this.

**Result copies are not a backup.** The running app holds a token that can read, write and delete its bucket. So whatever destroys the local copy through the app can destroy the remote copy too.
- They give a quick per-result restore, and a second failure domain.
- The app's orphan sweep deletes copies whose save id isn't in the database. It refuses, deleting nothing, when that would be every copy or more than a handful, which is what an empty or wrong database looks like. Watch for `Sweep refused` in the journal.
- Only snapshots the app can't reach, such as the nightly backup and the recovery copy, count as a backup.

**Budget.** On Cloudflare R2, the free allowance (10 GB-month of storage, 1M Class A and 10M Class B operations a month) belongs to the account, not the bucket.
- `JONA_R2_HARD_LIMIT_GB`, `JONA_R2_SOFT_LIMIT_GB` and `JONA_R2_USER_LIMIT_GB` (defaults 3, 2 and 1) set JonaAI's share.
- The service counts only its own copies, from the `result_copies` rows, so the hard stop says nothing about the account total. Seeing that total needs an account-level token, which the service must not hold. Use a Cloudflare usage notification instead.
- On a paid R2 plan, going over the allowance is billed, not refused.

## Rolling back Access enforcement

If an allowed account is refused, set `JONA_ACCESS_MODE=observe` and restart. Then read the `[access]` lines in `journalctl -u jonaai` to see why the token was rejected.

## Known gaps

- The element limit applies per load, not per graph. `MemoryMax` is the backstop.
- A cancelled or timed-out job reports so at once, but its computation runs to the end and holds its job slot until then.
- Schedules are stored but the schedule loop isn't started, so they don't fire yet.
