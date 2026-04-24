"""End-to-end test that invokes the REAL production task synchronously.

Runs `tasks.yandex_search.yandex_search_click_task` via Celery's `.apply()`
(eager, in-process) so we exercise the exact same code path as production —
selectors, captcha handling, proxy, everything.

Usage (inside celery_yandex_search container):
    docker exec yandex-maps-bot-celery_yandex_search-1 \
        python /app/_test_full_search.py [profile_id] [target_id]

If profile_id is omitted → newest warmed profile.
If target_id is omitted  → lowest-id active target.
"""
from __future__ import annotations

import logging
import random
import sys
import time
import traceback
from datetime import datetime

LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT, stream=sys.stdout, force=True)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("rebrowser_playwright").setLevel(logging.WARNING)
log = logging.getLogger("e2e")


def banner(msg: str) -> None:
    log.info("─" * 74)
    log.info(f"▶ {msg}")
    log.info("─" * 74)


def pick_profile(db, profile_id_arg: int):
    from app.models import BrowserProfile
    q = db.query(BrowserProfile).filter(
        BrowserProfile.is_active == True,
        BrowserProfile.warmup_completed == True,
    )
    if profile_id_arg:
        return q.filter(BrowserProfile.id == profile_id_arg).first()
    return q.order_by(BrowserProfile.last_used_at.desc().nullslast()).first()


def pick_target(db, target_id_arg: int):
    from app.models import YandexSearchTarget
    tq = db.query(YandexSearchTarget).filter(YandexSearchTarget.is_active == True)
    if target_id_arg:
        return tq.filter(YandexSearchTarget.id == target_id_arg).first()
    return tq.order_by(YandexSearchTarget.id).first()


def pick_keyword(target) -> str:
    kws = [k.strip() for k in (target.keywords or "").splitlines() if k.strip()]
    if not kws:
        raise RuntimeError(f"Target {target.id} has no keywords")
    return random.choice(kws)


def main() -> int:
    profile_id_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    target_id_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    banner("STEP 0: DB lookup")
    from app.database import SessionLocal
    from tasks.yandex_search import yandex_search_click_task

    db = SessionLocal()
    try:
        profile = pick_profile(db, profile_id_arg)
        if not profile:
            log.error("❌ No warmed profile in DB")
            return 2
        target = pick_target(db, target_id_arg)
        if not target:
            log.error("❌ No active YandexSearchTarget")
            return 2
        keyword = pick_keyword(target)

        log.info(
            f"📂 Profile: id={profile.id} name={profile.name} "
            f"mobile={profile.is_mobile} status={profile.status} "
            f"warmup_completed={profile.warmup_completed} "
            f"last_used_at={profile.last_used_at}"
        )
        log.info(
            f"🎯 Target:  id={target.id} domain='{target.domain}' "
            f"max_pages={target.max_search_pages} "
            f"keyword='{keyword}' (from {len((target.keywords or '').splitlines())} total)"
        )
    finally:
        db.close()

    banner("STEP 1: enqueue production task (real worker will pick it up)")
    t0 = time.monotonic()
    async_result = yandex_search_click_task.apply_async(
        args=(profile.id, target.id, keyword, None, None),
        queue="yandex_search",
    )
    log.info(f"📬 Enqueued task_id={async_result.id} on queue 'yandex_search'; "
             f"waiting up to 240s for result…")

    try:
        payload = async_result.get(timeout=240, propagate=False)
    except Exception as e:
        elapsed = time.monotonic() - t0
        log.error(f"💀 .get() raised after {elapsed:.1f}s: {type(e).__name__}: {e}")
        return 99

    elapsed = time.monotonic() - t0
    log.info(f"📦 Task finished: state={async_result.state}, elapsed={elapsed:.1f}s")

    if async_result.failed():
        log.error(f"❌ Task FAILED: {payload!r}")
        if async_result.traceback:
            log.error(async_result.traceback)
        return 3

    payload = payload or {}
    banner("STEP 2: return payload")
    for k in ("status", "profile_id", "keyword", "domain", "page_found",
              "position", "browse_time", "total_time", "error", "fail_reason"):
        if k in payload:
            log.info(f"   {k:<13} = {payload[k]!r}")

    status = (payload.get("status") or "").lower()
    if status == "completed" and payload.get("page_found"):
        log.info(f"🎉 TEST PASS: found '{target.domain}' at page "
                 f"{payload['page_found']} pos {payload['position']}")
        return 0
    if status == "not_found":
        log.warning(f"⚠️ TEST FAIL: domain '{target.domain}' not found in SERP")
        return 4
    log.error(f"❌ TEST FAIL: unexpected status={status}")
    return 5


if __name__ == "__main__":
    try:
        rc = main()
    except KeyboardInterrupt:
        log.warning("Interrupted")
        rc = 130
    except Exception as e:
        log.error(f"💀 UNHANDLED: {type(e).__name__}: {e}")
        log.error(traceback.format_exc())
        rc = 99
    log.info(f"=== EXIT rc={rc} at {datetime.utcnow().isoformat()}Z ===")
    sys.exit(rc)
