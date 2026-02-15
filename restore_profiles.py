import psycopg2
import os
import random

conn = psycopg2.connect(host="localhost", port=5432, user="postgres", password="password", dbname="yandex_maps_bot")
cur = conn.cursor()

profiles_dir = "/root/yandex-maps-bot/browser_profiles"
dirs = [d for d in os.listdir(profiles_dir) if d.startswith("Profile-")]

user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
]
viewports = [(1920,929),(1366,657),(1440,789),(1600,789),(1280,913),(1024,657)]
timezones = ["Europe/Moscow","Europe/Moscow","Europe/Samara","Asia/Yekaterinburg"]
platforms = ["Windows","Windows","Windows","MacIntel","Linux x86_64"]

inserted = 0
for d in sorted(dirs, key=lambda x: int(x.replace("Profile-",""))):
    pid = int(d.replace("Profile-",""))
    name = f"Profile-{pid}"
    ua = random.choice(user_agents)
    vw, vh = random.choice(viewports)
    tz = random.choice(timezones)
    lang = "ru-RU"
    plat = random.choice(platforms)
    
    cur.execute("""
        INSERT INTO browser_profiles (id, name, user_agent, viewport_width, viewport_height, 
            timezone, language, platform, status, is_active, warmup_completed,
            warmup_sessions_count, warmup_time_spent, total_sessions, successful_sessions,
            failed_sessions, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
    """, (pid, name, ua, vw, vh, tz, lang, plat, "ready", True, True, 3, 300, 0, 0, 0))
    inserted += 1

cur.execute("SELECT setval('browser_profiles_id_seq', (SELECT MAX(id) FROM browser_profiles))")
conn.commit()
cur.close()
conn.close()
print(f"Inserted {inserted} profiles")
