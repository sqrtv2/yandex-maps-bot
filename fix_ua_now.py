"""Fix all active profiles with wrong user agents."""
import random
import re as _re
from app.database import get_db_session
from app.models import BrowserProfile

WIN_TMPL = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{ver} Safari/537.36"
)
MAC_TMPL1 = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{ver} Safari/537.36"
)
MAC_TMPL2 = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_6) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{ver} Safari/537.36"
)
LINUX_TMPL = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{ver} Safari/537.36"
)

DESKTOP_TEMPLATES = [WIN_TMPL, WIN_TMPL, WIN_TMPL, MAC_TMPL1, MAC_TMPL2, LINUX_TMPL]

MOBILE_TMPL = (
    "Mozilla/5.0 (Linux; Android {android}; {model}) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/{ver} Mobile Safari/537.36"
)

MODELS = [
    "Pixel 7", "Pixel 8", "SM-S928B", "SM-A546B",
    "22101316G", "SM-G998B", "2201117TG", "RMX3630",
]
ANDROIDS = ["13", "14"]
VERSIONS = ["143.0.7544.{p}", "144.0.7612.{p}", "145.0.7632.{p}"]


def gen_ver():
    return random.choice(VERSIONS).format(p=random.randint(40, 120))


with get_db_session() as db:
    all_profiles = db.query(BrowserProfile).filter(
        BrowserProfile.is_active == True,
    ).all()

    desktop_fixed = 0
    mobile_fixed = 0

    for p in all_profiles:
        ua = p.user_agent or ""
        # Skip profiles that already have Chrome 143-145
        if _re.search(r"Chrome/(143|144|145)\.", ua):
            continue

        is_mobile = "Mobile" in ua and "Android" in ua

        if is_mobile:
            p.user_agent = MOBILE_TMPL.format(
                ver=gen_ver(),
                model=random.choice(MODELS),
                android=random.choice(ANDROIDS),
            )
            mobile_fixed += 1
        else:
            if "Mac" in ua:
                tmpl = random.choice([MAC_TMPL1, MAC_TMPL2])
            elif "Linux" in ua:
                tmpl = LINUX_TMPL
            else:
                tmpl = WIN_TMPL
            p.user_agent = tmpl.format(ver=gen_ver())
            desktop_fixed += 1

    total = desktop_fixed + mobile_fixed
    print(f"Fixed {desktop_fixed} desktop + {mobile_fixed} mobile = {total} total")
    db.commit()
    print("Committed")
