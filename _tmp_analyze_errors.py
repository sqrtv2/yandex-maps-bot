import requests, json
from collections import Counter

s = requests.Session()
s.post('http://88.99.146.218/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)

r = s.get('http://88.99.146.218/api/yandex-search-logs?limit=500')
data = r.json()
logs = data.get('logs', data) if isinstance(data, dict) else data
print('Total logs:', len(logs))

errors = Counter()
for log in logs:
    if log.get('status') == 'failed':
        reason = log.get('error_message') or log.get('fail_reason') or log.get('error') or 'unknown'
        reason = reason[:150]
        errors[reason] += 1

print('\n=== TOP ERROR REASONS (out of {} failed) ==='.format(sum(errors.values())))
for reason, count in errors.most_common(25):
    print('  [{:3d}] {}'.format(count, reason))

statuses = Counter(log.get('status') for log in logs)
print('\n=== STATUS DISTRIBUTION ===')
for status, count in statuses.most_common():
    print('  {}: {}'.format(status, count))

ip = [l for l in logs if l.get('status') == 'in_progress']
print('\n=== IN_PROGRESS TASKS: {} ==='.format(len(ip)))
for t in ip[:10]:
    print('  id={}, profile={}, keyword={}, started={}'.format(
        t.get('id'), t.get('profile_id'), str(t.get('keyword',''))[:40], t.get('started_at','')))

# Also get error logs
r3 = s.get('http://88.99.146.218/api/error-logs?limit=50')
if r3.status_code == 200:
    edata = r3.json()
    elogs = edata.get('logs', edata) if isinstance(edata, dict) else edata
    if elogs:
        print('\n=== RECENT ERROR LOGS ({}) ==='.format(len(elogs)))
        for e in elogs[:15]:
            msg = str(e.get('error_message') or e.get('message', ''))[:120]
            print('  [{}] {} - {}'.format(e.get('created_at','')[:19], e.get('task_type',''), msg))
