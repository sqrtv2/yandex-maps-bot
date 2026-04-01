import requests, json
from collections import Counter
from datetime import datetime

s = requests.Session()
s.post('http://88.99.146.218/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)

# 1. Get all today's failed tasks with "watchdog" reason - look for patterns
r = s.get('http://88.99.146.218/api/yandex-search-logs?limit=1000')
data = r.json()
logs = data.get('logs', data) if isinstance(data, dict) else data

watchdog_tasks = [l for l in logs if l.get('status') == 'failed' and 'in_progress' in str(l.get('error_message',''))]
print('=== WATCHDOG KILLED TASKS (hung >10min): {} ==='.format(len(watchdog_tasks)))

# Check if certain profiles hang more
profile_hangs = Counter(l.get('profile_id') for l in watchdog_tasks)
print('\nTop hanging profiles:')
for pid, count in profile_hangs.most_common(15):
    print('  profile {}: {} hangs'.format(pid, count))

# Check if certain domains/keywords hang more  
domain_hangs = Counter()
keyword_hangs = Counter()
for l in watchdog_tasks:
    d = l.get('domain') or l.get('target_domain') or ''
    k = l.get('keyword') or ''
    if d: domain_hangs[d] += 1
    if k: keyword_hangs[k[:50]] += 1

print('\nHanging by domain:')
for d, count in domain_hangs.most_common(10):
    print('  {}: {}'.format(d, count))

print('\nTop hanging keywords:')
for k, count in keyword_hangs.most_common(10):
    print('  "{}": {}'.format(k, count))

# Check timing patterns
print('\nHanging task timing (started_at):')
hours = Counter()
for l in watchdog_tasks:
    sa = l.get('started_at', '')
    if sa and len(sa) > 13:
        hours[sa[11:13]] += 1
for h, count in sorted(hours.items()):
    print('  {}:xx - {} tasks'.format(h, count))

# 2. Check what fields are available in log entries
if watchdog_tasks:
    print('\n=== SAMPLE WATCHDOG TASK (all fields) ===')
    print(json.dumps(watchdog_tasks[0], indent=2, ensure_ascii=False, default=str))
    print('\n=== SAMPLE WATCHDOG TASK #2 ===')
    if len(watchdog_tasks) > 5:
        print(json.dumps(watchdog_tasks[5], indent=2, ensure_ascii=False, default=str))

# 3. Compare completed vs hung: durations
completed = [l for l in logs if l.get('status') == 'completed']
print('\n=== COMPLETED TASKS: {} ==='.format(len(completed)))
if completed:
    durations = []
    for l in completed:
        sa = l.get('started_at', '')
        ca = l.get('completed_at') or l.get('finished_at') or l.get('updated_at', '')
        if sa and ca:
            try:
                # try parsing
                fmt1 = '%Y-%m-%dT%H:%M:%S.%f'
                fmt2 = '%Y-%m-%dT%H:%M:%S'
                for fmt in [fmt1, fmt2]:
                    try:
                        t1 = datetime.strptime(sa[:26], fmt)
                        break
                    except:
                        t1 = None
                for fmt in [fmt1, fmt2]:
                    try:
                        t2 = datetime.strptime(ca[:26], fmt)
                        break
                    except:
                        t2 = None
                if t1 and t2:
                    durations.append((t2 - t1).total_seconds())
            except:
                pass
    if durations:
        durations.sort()
        print('  Duration stats (seconds):')
        print('    min: {:.0f}, max: {:.0f}, median: {:.0f}, avg: {:.0f}'.format(
            min(durations), max(durations), durations[len(durations)//2], sum(durations)/len(durations)))
        print('    >300s: {}, >600s: {}'.format(
            sum(1 for d in durations if d > 300),
            sum(1 for d in durations if d > 600)))
    print('  Sample completed task:')
    print(json.dumps(completed[0], indent=2, ensure_ascii=False, default=str))

# 4. Currently in_progress tasks - how old?
ip = [l for l in logs if l.get('status') == 'in_progress']
print('\n=== CURRENTLY IN_PROGRESS: {} ==='.format(len(ip)))
now = datetime.utcnow()
for t in ip:
    sa = t.get('started_at', '')
    age_s = '?'
    if sa:
        try:
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
                try:
                    t1 = datetime.strptime(sa[:26], fmt)
                    age_s = '{:.0f}s'.format((now - t1).total_seconds())
                    break
                except:
                    pass
        except:
            pass
    print('  id={}, profile={}, age={}, keyword={}'.format(
        t.get('id'), t.get('profile_id'), age_s, str(t.get('keyword',''))[:40]))

# 5. Check target stats for success rates
r2 = s.get('http://88.99.146.218/api/yandex-search-targets')
if r2.status_code == 200:
    targets = r2.json()
    if isinstance(targets, dict):
        targets = targets.get('targets', targets.get('data', []))
    print('\n=== SEARCH TARGETS ===')
    for t in targets[:10]:
        print('  id={}, domain={}, active={}, keywords={}'.format(
            t.get('id'), t.get('domain','')[:30], t.get('is_active'), t.get('keyword_count', '?')))
