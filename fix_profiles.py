"""Fix fake warmed profiles — reset those without folders on disk."""
from sqlalchemy import create_engine, text
import os

db_url = os.environ.get('YANDEX_BOT_DATABASE_URL', '')
engine = create_engine(db_url)

# Get all profile names that have folders
existing_folders = set()
profiles_dir = './browser_profiles'
for name in os.listdir(profiles_dir):
    if name.startswith('Profile-'):
        existing_folders.add(name)

print(f'Folders on disk: {len(existing_folders)}')

with engine.connect() as conn:
    result = conn.execute(text('SELECT id, name FROM browser_profiles'))
    rows = result.fetchall()
    
    reset_ids = []
    ok_ids = []
    for r in rows:
        if r[1] in existing_folders:
            ok_ids.append(r[0])
        else:
            reset_ids.append(r[0])
    
    print(f'Profiles WITH folders (keep): {len(ok_ids)}')
    print(f'Profiles WITHOUT folders (reset): {len(reset_ids)}')
    
    if reset_ids:
        for i in range(0, len(reset_ids), 500):
            chunk = reset_ids[i:i+500]
            ids_str = ','.join(str(x) for x in chunk)
            sql = f"UPDATE browser_profiles SET warmup_completed = false, status = 'created', is_active = false WHERE id IN ({ids_str})"
            conn.execute(text(sql))
        conn.commit()
        print(f'Reset {len(reset_ids)} profiles to created/inactive')
    
    warmed = conn.execute(text('SELECT count(*) FROM browser_profiles WHERE warmup_completed = true')).scalar()
    active = conn.execute(text('SELECT count(*) FROM browser_profiles WHERE is_active = true')).scalar()
    print(f'After fix: warmed={warmed}, active={active}')
