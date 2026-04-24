#!/usr/bin/env python3
"""
CLI to fire camoufox warmup tasks for a batch of profiles.

Reads the BrowserProfile table, picks active profiles whose camoufox state file
indicates incomplete warmup (state.completed != True), and dispatches one Celery
`tasks.warmup_camoufox.warmup_camoufox_session` task per profile.

Tasks land on the `warmup_camoufox` queue → handled by celery_camoufox_warmup
worker. NEVER touches existing chromium warmup state.

Usage (inside a container that has DB+Redis access, e.g. celery_warmup):
    python _warmup_camoufox.py                  # default: up to 50 profiles
    python _warmup_camoufox.py --limit 10
    python _warmup_camoufox.py --profile-id 123 # single profile
    python _warmup_camoufox.py --status         # show summary only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.config import settings
from app.database import get_db_session
from app.models.browser_profile import BrowserProfile


def _state_file(profile_name: str) -> Path:
    return Path(settings.browser_user_data_dir) / profile_name / "_camoufox" / "_warmup_state.json"


def _load_state(profile_name: str) -> dict:
    p = _state_file(profile_name)
    if not p.exists():
        return {"stage": 0, "sessions_count": 0, "completed": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"stage": 0, "sessions_count": 0, "completed": False}


def _show_status() -> int:
    with get_db_session() as db:
        profiles = (
            db.query(BrowserProfile)
            .filter(BrowserProfile.is_active == True)
            .order_by(BrowserProfile.id)
            .all()
        )
    by_stage: dict = {}
    completed = 0
    never_started = 0
    for p in profiles:
        st = _load_state(p.name)
        stg = int(st.get("stage", 0))
        by_stage[stg] = by_stage.get(stg, 0) + 1
        if st.get("completed"):
            completed += 1
        if stg == 0:
            never_started += 1
    print(f"=== Camoufox warmup status ({len(profiles)} active profiles) ===")
    print(f"  completed:      {completed}")
    print(f"  never started:  {never_started}")
    print(f"  by stage:       {dict(sorted(by_stage.items()))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="Max profiles to dispatch")
    ap.add_argument("--profile-id", type=int, default=None, help="Single profile id")
    ap.add_argument("--status", action="store_true", help="Show status only, dispatch nothing")
    ap.add_argument("--include-completed", action="store_true",
                    help="Re-warmup profiles already marked completed")
    args = ap.parse_args()

    if args.status:
        return _show_status()

    # Lazy import to avoid celery startup unless needed
    from tasks.warmup_camoufox import warmup_camoufox_session

    with get_db_session() as db:
        q = db.query(BrowserProfile).filter(BrowserProfile.is_active == True)
        if args.profile_id:
            q = q.filter(BrowserProfile.id == args.profile_id)
        else:
            q = q.order_by(BrowserProfile.id)
        profiles = q.all()

    selected = []
    for p in profiles:
        st = _load_state(p.name)
        if not args.include_completed and st.get("completed"):
            continue
        selected.append((p.id, p.name, int(st.get("stage", 0))))
        if len(selected) >= args.limit:
            break

    if not selected:
        print("Nothing to dispatch.")
        return 0

    print(f"Dispatching {len(selected)} camoufox warmup tasks...")
    for pid, name, stage in selected:
        async_result = warmup_camoufox_session.apply_async(
            args=(pid,), queue="warmup_camoufox"
        )
        print(f"  → {name} (id={pid}, current_stage={stage})  task_id={async_result.id}")

    print("Done. Monitor with: docker compose logs -f celery_camoufox_warmup")
    return 0


if __name__ == "__main__":
    sys.exit(main())
