"""
Camoufox warmup pipeline — runs in PARALLEL to existing chromium warmup,
does NOT touch any chromium-warmup code paths or database columns.

State storage:
    JSON file at  Profile-N/_camoufox/_warmup_state.json
    {
      "stage": 0..N,                   # sessions completed
      "sessions_count": int,
      "first_warmup_at": ISO-8601,
      "last_warmup_at":  ISO-8601,
      "completed": bool,               # >= MIN_SESSIONS and >= MIN_HOURS_SPREAD
      "history": [ {ts, sites_visited, sites_failed, elapsed_s, error}, ... ]
    }

Architecture:
  * Celery task `warmup_camoufox_session(profile_id)` selects sites + proxy,
    spawns `core.camoufox_runner` subprocess (pure isolation — no shared
    sync_playwright loop), parses result, updates JSON state.
  * Site list is built locally to avoid importing tasks.warmup (heavy deps);
    the same Russian-popular pool is used.
  * Entirely opt-in: nothing schedules these tasks unless `_warmup_camoufox.py`
    CLI or future beat entry triggers them.
"""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from app.config import settings
from app.models.browser_profile import BrowserProfile
from core.database import get_db_session
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# === Configuration (mirrors chromium warmup defaults) ===
MIN_SESSIONS = 3
MIN_HOURS_SPREAD = 1.0
SITES_PER_SESSION = 12
RUNNER_TIMEOUT_S = 480  # 8 min per session — generous for camoufox FF startup

# Russian-popular pool (yandex deliberately EXCLUDED until cookies seeded
# by neutral browsing — same logic as chromium warmup)
SITE_POOL: List[str] = [
    "https://vk.com",
    "https://mail.ru",
    "https://ok.ru",
    "https://rbc.ru",
    "https://lenta.ru",
    "https://ria.ru",
    "https://tass.ru",
    "https://gazeta.ru",
    "https://kommersant.ru",
    "https://avito.ru",
    "https://ozon.ru",
    "https://wildberries.ru",
    "https://habr.com",
    "https://pikabu.ru",
    "https://sports.ru",
    "https://hh.ru",
    "https://2gis.ru",
    "https://dns-shop.ru",
    "https://mvideo.ru",
    "https://drive2.ru",
    "https://banki.ru",
    "https://auto.ru",
    "https://ivi.ru",
    "https://kp.ru",
    "https://ru.wikipedia.org",
]
# Yandex sites added once profile has at least 1 session of cookies
YANDEX_SITES: List[str] = [
    "https://ya.ru",
    "https://yandex.ru",
    "https://dzen.ru",
    "https://market.yandex.ru",
    "https://pogoda.yandex.ru",
    "https://kinopoisk.ru",
]


def _camoufox_dir(profile_name: str) -> Path:
    """Return Profile-N/_camoufox/ dir (created if missing)."""
    base = Path(settings.browser_user_data_dir) / profile_name / "_camoufox"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _state_path(profile_name: str) -> Path:
    return _camoufox_dir(profile_name) / "_warmup_state.json"


def _load_state(profile_name: str) -> Dict:
    p = _state_path(profile_name)
    if not p.exists():
        return {
            "stage": 0,
            "sessions_count": 0,
            "first_warmup_at": None,
            "last_warmup_at": None,
            "completed": False,
            "history": [],
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"camoufox state read failed for {profile_name}: {e} — resetting")
        return {
            "stage": 0, "sessions_count": 0,
            "first_warmup_at": None, "last_warmup_at": None,
            "completed": False, "history": [],
        }


def _save_state(profile_name: str, state: Dict) -> None:
    p = _state_path(profile_name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _pick_sites(stage: int) -> List[str]:
    pool = list(SITE_POOL)
    # From stage >= 1, mix in yandex sites — by then we have neutral cookies.
    if stage >= 1:
        pool.extend(YANDEX_SITES)
    random.shuffle(pool)
    return pool[:SITES_PER_SESSION]


def _build_proxy_dict(profile: BrowserProfile) -> Optional[Dict]:
    """Build playwright-style proxy dict from profile fields."""
    if not (profile.proxy_host and profile.proxy_port):
        return None
    ptype = (profile.proxy_type or "http").lower()
    proxy = {"server": f"{ptype}://{profile.proxy_host}:{profile.proxy_port}"}
    if profile.proxy_username:
        proxy["username"] = profile.proxy_username
    if profile.proxy_password:
        proxy["password"] = profile.proxy_password
    return proxy


def _run_subprocess(cfg: Dict) -> Dict:
    """Spawn core.camoufox_runner with cfg on stdin; parse __RESULT__ line."""
    payload = json.dumps(cfg)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "core.camoufox_runner"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=RUNNER_TIMEOUT_S,
            cwd=os.getcwd(),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"subprocess timeout >{RUNNER_TIMEOUT_S}s"}

    out = proc.stdout or ""
    err = proc.stderr or ""
    for line in out.splitlines():
        if line.startswith("__RESULT__"):
            try:
                return json.loads(line[len("__RESULT__"):])
            except Exception as e:
                return {"ok": False, "error": f"bad runner json: {e}", "stderr": err[-300:]}
    return {
        "ok": False,
        "error": f"no __RESULT__ in stdout (rc={proc.returncode})",
        "stdout_tail": out[-300:],
        "stderr_tail": err[-300:],
    }


@celery_app.task(
    bind=True,
    name="tasks.warmup_camoufox.warmup_camoufox_session",
    queue="warmup_camoufox",
    max_retries=2,
    soft_time_limit=RUNNER_TIMEOUT_S + 60,
    time_limit=RUNNER_TIMEOUT_S + 120,
    reject_on_worker_lost=False,
)
def warmup_camoufox_session(self, profile_id: int) -> Dict:
    """
    One camoufox warmup session for the given profile.
    Returns dict with stats; updates JSON state on disk.
    """
    t0 = time.time()
    with get_db_session() as db:
        profile = db.query(BrowserProfile).filter(BrowserProfile.id == profile_id).first()
        if not profile:
            return {"ok": False, "error": f"profile {profile_id} not found"}
        if not profile.is_active:
            return {"ok": False, "error": f"profile {profile_id} inactive"}
        profile_name = profile.name
        ua = profile.user_agent
        timezone = profile.timezone or "Europe/Moscow"
        language = profile.language or "ru-RU"
        proxy = _build_proxy_dict(profile)

    if not proxy:
        return {"ok": False, "error": "no proxy on profile"}

    state = _load_state(profile_name)
    if state.get("completed"):
        logger.info(f"🍃 Camoufox warmup already completed for {profile_name}")
        return {"ok": True, "skipped": "already_completed", "state": state}

    stage = int(state.get("stage", 0))
    sites = _pick_sites(stage)
    profile_dir = str(_camoufox_dir(profile_name))

    cfg = {
        "profile_dir": profile_dir,
        "proxy": proxy,
        "sites": sites,
        "user_agent": ua,
        "timezone": timezone,
        "locale": language,
        "headless": True,
    }

    logger.info(
        f"🍃 [camoufox warmup] profile={profile_name} stage={stage} sites={len(sites)} "
        f"proxy={proxy['server']}"
    )
    res = _run_subprocess(cfg)
    elapsed = round(time.time() - t0, 1)

    visited = int(res.get("sites_visited", 0))
    failed = int(res.get("sites_failed", 0))
    ok = bool(res.get("ok")) and visited >= max(3, SITES_PER_SESSION // 3)

    # Update state
    now_iso = datetime.utcnow().isoformat()
    if not state.get("first_warmup_at"):
        state["first_warmup_at"] = now_iso
    state["last_warmup_at"] = now_iso
    state["sessions_count"] = int(state.get("sessions_count", 0)) + 1
    if ok:
        state["stage"] = stage + 1
    history = state.setdefault("history", [])
    history.append({
        "ts": now_iso,
        "stage_after": state["stage"],
        "sites_visited": visited,
        "sites_failed": failed,
        "elapsed_s": elapsed,
        "error": res.get("error"),
    })
    # Trim history
    if len(history) > 50:
        state["history"] = history[-50:]

    # Completion check: enough sessions AND enough wall-clock spread
    if state["stage"] >= MIN_SESSIONS and state.get("first_warmup_at"):
        try:
            spread_h = (
                datetime.utcnow() - datetime.fromisoformat(state["first_warmup_at"])
            ).total_seconds() / 3600.0
            if spread_h >= MIN_HOURS_SPREAD:
                state["completed"] = True
                logger.info(f"🍃✅ Camoufox warmup COMPLETED for {profile_name} "
                            f"(stage={state['stage']}, spread={spread_h:.1f}h)")
        except Exception:
            pass

    _save_state(profile_name, state)

    logger.info(
        f"🍃 [camoufox warmup] profile={profile_name} done: "
        f"ok={ok} visited={visited}/{len(sites)} failed={failed} elapsed={elapsed}s "
        f"new_stage={state['stage']} completed={state['completed']}"
    )

    return {
        "ok": ok,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "stage": state["stage"],
        "completed": state["completed"],
        "sites_visited": visited,
        "sites_failed": failed,
        "elapsed_s": elapsed,
        "error": res.get("error"),
    }
