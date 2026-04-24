"""A/B metrics: rebrowser vs patchright backend comparison.

Reads two sources:
  1) Redis counters `backend:{backend}:{event}` (set by browser_manager._bump_backend_metric).
     Events: launch_ok, launch_fail.
  2) DB error_logs joined with bot_profiles, grouped by deterministic backend
     hash of profile_name (replicates _pick_backend logic from browser_manager).

Usage:
  docker compose exec api python /app/_check_ab.py
  docker compose exec api python /app/_check_ab.py --hours 6
"""
import argparse
import hashlib
import sys

from app.config import settings


BACKEND_REBROWSER = "rebrowser"
BACKEND_PATCHRIGHT = "patchright"


def pick_backend(profile_name: str, pct: int) -> str:
    if pct <= 0:
        return BACKEND_REBROWSER
    if pct >= 100:
        return BACKEND_PATCHRIGHT
    bucket = int(hashlib.md5(profile_name.encode()).hexdigest()[:8], 16) % 100
    return BACKEND_PATCHRIGHT if bucket < pct else BACKEND_REBROWSER


def show_redis_counters():
    print("=== Redis counters (browser_manager._bump_backend_metric) ===")
    try:
        import redis
        r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        for backend in (BACKEND_REBROWSER, BACKEND_PATCHRIGHT):
            ok = int(r.get(f"backend:{backend}:launch_ok") or 0)
            fail = int(r.get(f"backend:{backend}:launch_fail") or 0)
            total = ok + fail
            rate = (ok / total * 100) if total else 0.0
            print(f"  {backend:12s}  launch_ok={ok:6d}  launch_fail={fail:6d}  "
                  f"total={total:6d}  success={rate:5.1f}%")
    except Exception as e:
        print(f"  Redis error: {e}")


def show_db_split(hours: int):
    print(f"\n=== DB task results split by backend (last {hours}h) ===")
    pct = int(getattr(settings, "browser_backend_patchright_pct", 0))
    print(f"  browser_backend_patchright_pct = {pct}")
    if pct == 0:
        print("  All profiles routed to rebrowser \u2014 no split to compare.")
        return
    if pct == 100:
        print("  All profiles routed to patchright \u2014 no split to compare.")
        return

    try:
        from app.database import SessionLocal
        from sqlalchemy import text
    except Exception as e:
        print(f"  DB import error: {e}")
        return

    sql = text(
        """
        SELECT
            yst.status,
            bp.name AS profile_name
        FROM yandex_search_tasks yst
        JOIN bot_profiles bp ON bp.id = yst.profile_id
        WHERE yst.updated_at >= NOW() - (:hours || ' hours')::interval
          AND yst.status IN ('completed', 'failed', 'not_found')
        """
    )

    counts = {
        BACKEND_REBROWSER: {"completed": 0, "failed": 0, "not_found": 0},
        BACKEND_PATCHRIGHT: {"completed": 0, "failed": 0, "not_found": 0},
    }
    db = SessionLocal()
    try:
        for status, profile_name in db.execute(sql, {"hours": str(hours)}):
            backend = pick_backend(profile_name or "", pct)
            counts[backend][status] = counts[backend].get(status, 0) + 1
    finally:
        db.close()

    for backend, c in counts.items():
        total = c["completed"] + c["failed"] + c["not_found"]
        success = (c["completed"] / total * 100) if total else 0.0
        print(
            f"  {backend:12s}  completed={c['completed']:5d}  "
            f"failed={c['failed']:5d}  not_found={c['not_found']:4d}  "
            f"total={total:5d}  success={success:5.1f}%"
        )

    # Delta
    r = counts[BACKEND_REBROWSER]
    p = counts[BACKEND_PATCHRIGHT]
    rt = r["completed"] + r["failed"] + r["not_found"]
    pt = p["completed"] + p["failed"] + p["not_found"]
    rs = (r["completed"] / rt * 100) if rt else 0
    ps = (p["completed"] / pt * 100) if pt else 0
    delta = ps - rs
    sign = "+" if delta >= 0 else ""
    print(f"\n  patchright vs rebrowser: {sign}{delta:.1f} pp success-rate delta")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24, help="Lookback window in hours")
    args = ap.parse_args()
    show_redis_counters()
    show_db_split(args.hours)
