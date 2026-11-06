"""Purge downloaded files: wis2downloader 0.3.2 never deletes anything itself.

Every PURGE_INTERVAL_MINUTES:
  1. delete files downloaded more than RETENTION_HOURS ago (by modification time);
  2. if MAX_DOWNLOAD_GB > 0 and the pile is still bigger than that, delete the
     oldest files until it fits (a safety cap in case ECMWF sends more than expected);
  3. remove empty directories.
The outcome is written to /data/state/purge.json for the dashboard.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("DOWNLOAD_DIR", "/data/downloads"))
STATE = Path(os.environ.get("STATE_DIR", "/data/state"))
RETENTION_HOURS = float(os.environ.get("RETENTION_HOURS", "36"))
MAX_GB = float(os.environ.get("MAX_DOWNLOAD_GB", "0") or 0)
INTERVAL = float(os.environ.get("PURGE_INTERVAL_MINUTES", "10")) * 60


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"{iso(time.time())} purger: {msg}", flush=True)


def files():
    out = []
    for root, _, names in os.walk(DATA):
        for n in names:
            if n.startswith("."):  # never purge hidden files, e.g. the repo's downloads/.gitkeep
                continue
            p = Path(root) / n
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            out.append((st.st_mtime, st.st_size, p))
    return out


def remove(p):
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False


def remove_empty_dirs():
    for root, dirs, names in os.walk(DATA, topdown=False):
        r = Path(root)
        if r != DATA and not any(r.iterdir()):
            try:
                r.rmdir()
            except OSError:
                pass


def purge_once(totals):
    now = time.time()
    cutoff = now - RETENTION_HOURS * 3600
    all_files = sorted(files())
    aged = [f for f in all_files if f[0] < cutoff]
    kept = [f for f in all_files if f[0] >= cutoff]
    removed_n = removed_b = 0
    for _, size, p in aged:
        if remove(p):
            removed_n += 1
            removed_b += size
    capped_n = 0
    if MAX_GB > 0:
        limit = MAX_GB * 1e9
        total = sum(s for _, s, _ in kept)
        for _, size, p in kept:  # oldest first
            if total <= limit:
                break
            if remove(p):
                total -= size
                capped_n += 1
                removed_n += 1
                removed_b += size
    remove_empty_dirs()
    totals["files"] += removed_n
    totals["bytes"] += removed_b
    status = {
        "last_run": iso(now),
        "next_run": iso(now + INTERVAL),
        "retention_hours": RETENTION_HOURS,
        "max_download_gb": MAX_GB,
        "interval_minutes": INTERVAL / 60,
        "removed_files": removed_n,
        "removed_bytes": removed_b,
        "removed_by_cap": capped_n,
        "total_removed_files": totals["files"],
        "total_removed_bytes": totals["bytes"],
    }
    tmp = STATE / "purge.json.tmp"
    tmp.write_text(json.dumps(status, indent=2))
    tmp.replace(STATE / "purge.json")
    log(f"removed {removed_n} file(s), {removed_b / 1e6:.1f} MB"
        f" ({capped_n} by the {MAX_GB:g} GB cap); retention {RETENTION_HOURS:g} h")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    totals = {"files": 0, "bytes": 0}
    log(f"starting: retention {RETENTION_HOURS:g} h, cap {MAX_GB:g} GB, every {INTERVAL / 60:g} min")
    while True:
        try:
            purge_once(totals)
        except Exception as e:  # keep running; the next pass retries
            log(f"error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
