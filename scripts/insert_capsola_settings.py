"""Insert capsola settings into DB."""
import sys
sys.path.insert(0, '/Users/sqrtv2/Project/PF')

from app.database import get_db_session
from app.models import UserSettings

with get_db_session() as db:
    # capsola_api_key
    existing = db.query(UserSettings).filter(UserSettings.setting_key == 'capsola_api_key').first()
    if not existing:
        s1 = UserSettings(
            setting_key='capsola_api_key',
            setting_value='9f8a1a9b-4322-4b8a-91ec-49192cdbaeb9',
            setting_type='string',
            description='API key Capsola Cloud for Yandex SmartCaptcha',
            category='anticaptcha'
        )
        db.add(s1)
        print('capsola_api_key added to DB')
    else:
        print(f'capsola_api_key already in DB: {existing.setting_value[:8]}...')

    # capsola_enabled
    existing2 = db.query(UserSettings).filter(UserSettings.setting_key == 'capsola_enabled').first()
    if not existing2:
        s2 = UserSettings(
            setting_key='capsola_enabled',
            setting_value='true',
            setting_type='bool',
            description='Enable SmartCaptcha solving via Capsola',
            category='anticaptcha'
        )
        db.add(s2)
        print('capsola_enabled added to DB')
    else:
        print(f'capsola_enabled already in DB: {existing2.setting_value}')

    db.commit()

# Verify
with get_db_session() as db2:
    all_anti = db2.query(UserSettings).filter(UserSettings.category == 'anticaptcha').all()
    print('\nAll anticaptcha settings in DB:')
    for s in all_anti:
        val = s.setting_value
        if 'api_key' in s.setting_key and len(val) > 12:
            val = val[:8] + '...' + val[-4:]
        print(f'  {s.setting_key} = {val}')

print('\nDone!')
