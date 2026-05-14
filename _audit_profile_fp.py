"""Static audit of profile fingerprint consistency (no browser launch).

Checks the BrowserProfile rows for known bad combinations that correlate with
captcha rate spikes. This catches mismatches BEFORE we burn proxy budget on a
runtime check.

Rules enforced:
  R1 UA major must be <= 5 versions behind current Chrome stable.
  R2 platform must match UA OS family (Windows->Win32, Mac->MacIntel,
     Linux->Linux x86_64, Android mobile->Linux armv8l).
  R3 language must look like 'ru-RU', 'en-US', etc. (BCP47).
  R4 viewport must be plausible (>= 320x480, <= 4096x2160, no zero dims).
  R5 mobile flag must agree with UA (Android/Mobile in UA <=> is_mobile=True).
  R6 timezone must be a valid IANA name (heuristic: contains '/').

Usage:
  python _audit_profile_fp.py            # report only
  python _audit_profile_fp.py --quarantine  # set status='quarantined' for failures
"""
from __future__ import annotations
import argparse
import re
import sys
from collections import Counter
from datetime import datetime
from typing import List, Tuple

from app.database import get_db_session
from app.models import BrowserProfile

# Current Chrome stable major as of repo state. Update periodically.
CHROME_CURRENT_MAJOR = 145
CHROME_MIN_ACCEPTABLE_MAJOR = CHROME_CURRENT_MAJOR - 5  # accept 140..145

UA_CHROME_RE = re.compile(r'Chrome/(\d+)\.')
UA_OS_PATTERNS = [
    ('Win32',          re.compile(r'Windows NT', re.I)),
    ('MacIntel',       re.compile(r'Macintosh.*Mac OS X', re.I)),
    ('Linux armv8l',   re.compile(r'Linux.*Android', re.I)),
    ('Linux x86_64',   re.compile(r'X11.*Linux', re.I)),
]
LANG_RE = re.compile(r'^[a-z]{2}(-[A-Z]{2})?$')


def _expected_platform(ua: str) -> str | None:
    for plat, pat in UA_OS_PATTERNS:
        if pat.search(ua or ''):
            return plat
    return None


def _is_mobile_ua(ua: str) -> bool:
    return bool(re.search(r'Mobile|Android', ua or '', re.I))


def audit_profile(p: BrowserProfile) -> List[str]:
    issues: List[str] = []
    ua = p.user_agent or ''

    # R1: Chrome major
    m = UA_CHROME_RE.search(ua)
    if not m:
        issues.append('R1:no-chrome-token-in-ua')
    else:
        major = int(m.group(1))
        if major < CHROME_MIN_ACCEPTABLE_MAJOR:
            issues.append(f'R1:stale-chrome-{major}<{CHROME_MIN_ACCEPTABLE_MAJOR}')
        elif major > CHROME_CURRENT_MAJOR + 1:
            issues.append(f'R1:future-chrome-{major}>{CHROME_CURRENT_MAJOR}')

    # R2: platform
    expected = _expected_platform(ua)
    if expected is None:
        issues.append('R2:unknown-os-in-ua')
    elif (p.platform or '').strip() != expected:
        issues.append(f'R2:platform-mismatch:{p.platform!r}!={expected!r}')

    # R3: language
    lang = (p.language or '').strip()
    if not LANG_RE.match(lang):
        issues.append(f'R3:bad-lang-tag:{lang!r}')

    # R4: viewport
    w, h = p.viewport_width or 0, p.viewport_height or 0
    if w < 320 or h < 480 or w > 4096 or h > 2160:
        issues.append(f'R4:bad-viewport:{w}x{h}')

    # R5: mobile flag
    ua_mobile = _is_mobile_ua(ua)
    if bool(p.is_mobile) != ua_mobile:
        issues.append(f'R5:mobile-flag-mismatch:profile={bool(p.is_mobile)}/ua={ua_mobile}')

    # R6: timezone
    tz = (p.timezone or '').strip()
    if not tz or '/' not in tz:
        issues.append(f'R6:bad-timezone:{tz!r}')

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quarantine', action='store_true',
                        help="Move profiles with issues to status='quarantined'")
    parser.add_argument('--only-active', action='store_true',
                        help='Audit only profiles currently in rotation (warmed/active)')
    args = parser.parse_args()

    with get_db_session() as db:
        q = db.query(BrowserProfile)
        if args.only_active:
            q = q.filter(BrowserProfile.status.in_(['warmed', 'active']))
        profiles = q.all()

        total = len(profiles)
        bad: List[Tuple[BrowserProfile, List[str]]] = []
        rule_counts: Counter = Counter()
        for p in profiles:
            issues = audit_profile(p)
            if issues:
                bad.append((p, issues))
                for it in issues:
                    rule_counts[it.split(':', 1)[0]] += 1

        print(f"Audited {total} profiles, {len(bad)} with issues "
              f"({100*len(bad)/total if total else 0:.1f}%)")
        print('Rule counts:', dict(rule_counts.most_common()))
        for p, issues in bad[:30]:
            print(f"  id={p.id} name={p.name} status={p.status}: {issues}")
        if len(bad) > 30:
            print(f"  ... +{len(bad)-30} more")

        if args.quarantine and bad:
            now = datetime.utcnow()
            quarantined = 0
            for p, _issues in bad:
                if p.status in ('warmed', 'active'):
                    p.status = 'quarantined'
                    p.warmup_completed = False
                    p.updated_at = now
                    quarantined += 1
            db.commit()
            print(f"Quarantined {quarantined} profiles.")
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
