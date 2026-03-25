"""Send test search tasks locally (no proxy, headless=off)."""
import os
os.environ['YANDEX_BOT_DATABASE_URL'] = 'postgresql://postgres:password@127.0.0.1:15432/yandex_maps_bot'
os.environ['YANDEX_BOT_REDIS_HOST'] = 'localhost'
os.environ['YANDEX_BOT_REDIS_PORT'] = '6379'
os.environ['YANDEX_BOT_BROWSER_HEADLESS'] = 'false'

from tasks.yandex_search import yandex_search_click_task

# tareksa.ru, target_id=6
tasks = [
    {'profile_id': 10756, 'target_id': 6, 'keyword': 'кварцевый песок', 'search_params': {'no_proxy': True}},
    {'profile_id': 10757, 'target_id': 6, 'keyword': 'кварцевый песок купить', 'search_params': {'no_proxy': True}},
    {'profile_id': 10758, 'target_id': 6, 'keyword': 'мытый кварцевый песок', 'search_params': {'no_proxy': True}},
]

for t in tasks:
    print(f"Sending task: profile={t['profile_id']}, keyword='{t['keyword']}'")
    yandex_search_click_task.apply_async(
        kwargs=t,
        queue='yandex_search',
    )
    print(f"  ✅ Sent!")

print(f"\n🚀 {len(tasks)} tasks sent to yandex_search queue")
