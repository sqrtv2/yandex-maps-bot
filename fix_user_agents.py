"""One-time script to fix all profiles with bad user agents (Firefox, Safari, old Chrome).
Updates them to modern Chrome 143-145 UAs matching installed Playwright Chromium."""
import random
import sys

TEMPLATES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
]
VERSIONS = ["143.0.7544.{p}", "144.0.7612.{p}", "145.0.7632.{p}"]

MOBILE_TMPL = "Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Mobile Safari/537.36"
MODELS = ["Pixel 7", "Pixel 8", "SM-S928B", "SM-A546B", "22101316G", "SM-G998B", "2201117TG", "RMX3630"]
ANDROIDS = ["13", "14"]


def gen_version():
    return random.choice(VERSIONS).format(p=random.randint(40, 120))


from app.database import get_db_session
from app.models import BrowserProfile

with get_db_session() as db:
    bad = db.query(BrowserProfile).filter(
        BrowserProfile.is_active == True,
        ~BrowserProfile.user_agent.like("%Chrome/14%")
    ).all()

    desktop_bad = [p for p in bad if not (p.user_agent and "Mobile" in p.user_agent and "Android" in p.user_agent)]
    mobile_bad = [p for p in bad if p.user_agent and "Mobile" in p.user_agent and "Android" in p.user_agent]

    for p in desktop_bad:
        if p.user_agent and "Mac" in p.user_agent:
            tmpl = random.choice(TEMPLATES[1:3])
        elif p.user_agent and "Linux" in p.user_agent:
            tmpl = TEMPLATES[3]
        else:
            tmpl = TEMPLATES[0]
        p.user_agent = tmpl.format(ver=gen_version())

    for p in mobile_bad:
        p.user_agent = MOBILE_TMPL.format(
            ver=gen_version(),
            model=random.choice(MODELS),
            android=random.choice(ANDROIDS),
        )

    print(f"Fixed {len(desktop_bad)} desktop + {len(mobile_bad)} mobile profiles")
    db.commit()
    print("Committed to database")
