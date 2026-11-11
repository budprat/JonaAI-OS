#!/usr/bin/env python3
# ABOUTME: Nightly copy of JonaAI's saved results and database rows to a second volume, and,
# ABOUTME: when configured, to a recovery bucket meant to be out of the app's reach. Keeps dated snapshots.
"""Run as root from jonaai-backup.service. Destination: JONA_BACKUP_DIR.

Offsite recovery copy (optional). With the four JONA_RECOVERY_* settings, each run also
sends the snapshot to a separate R2 bucket through rclone: the saved results with
`rclone sync --backup-dir`, so a file deleted or changed upstream is moved to a dated
replaced/ folder instead of being lost, and the database dump as one dated object per
night. The settings belong in a root-only file (/etc/jonaai/recovery.env, 600 root:root)
added as a second EnvironmentFile= on jonaai-backup.service only, so the app, which runs
as jonaai, never receives these settings. That makes the backup independent of the app
only if the token is a separate one scoped to the recovery bucket; the token installed
2026-11-09 is the app's own (see docs/deployment.md, Backups). With none of the settings this
script behaves exactly as without the feature.
"""
import datetime
import gzip
import json
import os
import shutil
import subprocess
import sys

import psycopg2

KEEP_DAYS = 14
SOURCE = "/var/lib/jonaai/.openeo_jobs/"
# Rows only: secrets and auth live with Supabase, and api_keys hold hashes.
SKIP_TABLES = {"alembic_version"}

RECOVERY_SETTINGS = ("JONA_RECOVERY_ENDPOINT", "JONA_RECOVERY_BUCKET",
                     "JONA_RECOVERY_ACCESS_KEY_ID", "JONA_RECOVERY_SECRET_ACCESS_KEY")
# Replaced files and database dumps older than this many days are pruned offsite.
RECOVERY_KEEP_DAYS = 90


def dump_tables(path: str) -> int:
    conn = psycopg2.connect(
        host=os.environ["POSTGRESQL_HOST"], port=os.environ["POSTGRESQL_PORT"], user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"], dbname=os.environ["POSTGRES_DB"], sslmode="require", connect_timeout=30,
    )
    conn.set_session(readonly=True)
    total = 0
    with conn.cursor() as cur, gzip.open(path, "wt") as out:
        cur.execute("select table_name from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
        for (table,) in cur.fetchall():
            if table in SKIP_TABLES:
                continue
            # A server-side cursor, so a large table doesn't all land in this process.
            with conn.cursor(name=f"dump_{total}") as rows:
                rows.execute(f'select row_to_json(x) from "{table}" x')
                for (row,) in rows:
                    out.write(json.dumps({"table": table, "row": row}, default=str) + "\n")
                    total += 1
    conn.close()
    return total


def recovery_config(env=os.environ):
    """The recovery remote, or None when the offsite copy is off (a normal state).

    Raises when it is half set up, so the run fails visibly instead of silently skipping.
    """
    values = {name: env.get(name, "").strip() for name in RECOVERY_SETTINGS}
    if not any(values.values()):
        return None
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"offsite recovery copy half set up: {', '.join(missing)} not set")
    endpoint, bucket = values["JONA_RECOVERY_ENDPOINT"], values["JONA_RECOVERY_BUCKET"]
    if not endpoint.startswith("https://") or "/" in endpoint[len("https://"):].strip("/"):
        raise RuntimeError("JONA_RECOVERY_ENDPOINT must be the account endpoint, with no path")
    if "/" in bucket or bucket == env.get("JONA_R2_BUCKET", "").strip():
        raise RuntimeError("JONA_RECOVERY_BUCKET must be a bucket name, and not the app's bucket")
    return {
        "root": f"recovery:{bucket}",
        "env": {
            "RCLONE_CONFIG_RECOVERY_TYPE": "s3",
            "RCLONE_CONFIG_RECOVERY_PROVIDER": "Cloudflare",
            "RCLONE_CONFIG_RECOVERY_ENDPOINT": endpoint,
            "RCLONE_CONFIG_RECOVERY_ACCESS_KEY_ID": values["JONA_RECOVERY_ACCESS_KEY_ID"],
            "RCLONE_CONFIG_RECOVERY_SECRET_ACCESS_KEY": values["JONA_RECOVERY_SECRET_ACCESS_KEY"],
            "RCLONE_CONFIG_RECOVERY_REGION": "auto",
            "RCLONE_CONFIG_RECOVERY_NO_CHECK_BUCKET": "true",
        },
    }


def push_offsite(snapshot: str, stamp: str, config: dict, run=subprocess.run) -> None:
    """Send one snapshot to the recovery bucket. Raises if a copy step fails."""
    env = {name: os.environ[name] for name in ("PATH", "HOME") if name in os.environ}
    env.update(config["env"])
    root = config["root"]

    def rclone(*args, check=True):
        result = run(["rclone", "-q", *args], env=env, capture_output=True, text=True, timeout=3600)
        if check and result.returncode != 0:
            raise RuntimeError(f"rclone {args[0]} exited {result.returncode}: {(result.stderr or '').strip()[-300:]}")
        return result

    rclone("sync", os.path.join(snapshot, "files/"), f"{root}/files", "--backup-dir", f"{root}/replaced/{stamp}")
    rclone("copyto", os.path.join(snapshot, "tables.jsonl.gz"), f"{root}/database/{stamp}.jsonl.gz")

    # Prune by the date in the name, not the file's own date: an old file deleted today
    # must stay in replaced/ for the full RECOVERY_KEEP_DAYS.
    cutoff = (datetime.datetime.strptime(stamp, "%Y-%m-%d") - datetime.timedelta(days=RECOVERY_KEEP_DAYS)).strftime("%Y-%m-%d")
    for folder, is_dir in (("replaced", True), ("database", False)):
        listing = rclone("lsf", f"{root}/{folder}", "--dirs-only" if is_dir else "--files-only", check=False)
        for name in (listing.stdout or "").split():
            if name[:10] < cutoff:
                if is_dir:
                    rclone("purge", f"{root}/{folder}/{name.rstrip('/')}", check=False)
                else:
                    rclone("deletefile", f"{root}/{folder}/{name}", check=False)


def main() -> int:
    root = os.environ.get("JONA_BACKUP_DIR", "/mnt/backups/jonaai-backups")
    if not os.path.ismount(os.path.dirname(root)):
        print(f"{os.path.dirname(root)} isn't mounted; refusing to back up to the root disk", file=sys.stderr)
        return 1
    os.makedirs(root, mode=0o700, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    final = os.path.join(root, stamp)
    # Built aside and moved at the end, so a failed run never looks like a snapshot.
    target = os.path.join(root, f".partial-{stamp}")
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, mode=0o700)

    snapshots = sorted(name for name in os.listdir(root) if name != stamp and len(name) == 10 and name[0].isdigit())
    link = [f"--link-dest={os.path.join(root, snapshots[-1], 'files')}"] if snapshots else []
    copied = subprocess.run(["rsync", "-a", "--delete", *link, SOURCE, os.path.join(target, "files/")])
    # 24 means a file vanished while copying, which is normal in a live results folder.
    if copied.returncode not in (0, 24):
        raise RuntimeError(f"rsync exited with {copied.returncode}")
    rows = dump_tables(os.path.join(target, "tables.jsonl.gz"))
    shutil.rmtree(final, ignore_errors=True)
    os.rename(target, final)

    for name in snapshots[:-KEEP_DAYS]:
        shutil.rmtree(os.path.join(root, name))
    print(f"backup {stamp}: {rows} rows; {len(snapshots) + 1} snapshots before pruning")

    # After the local snapshot is complete, so an offsite failure never costs the local copy.
    try:
        config = recovery_config()
        if config is not None:
            push_offsite(final, stamp, config)
            print(f"offsite recovery copy {stamp}: done")
    except Exception as exc:  # noqa: BLE001 — reported as a failed run, local copy kept
        print(f"offsite recovery copy {stamp} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
