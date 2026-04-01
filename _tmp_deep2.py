import requests, json
from collections import Counter
from datetime import datetime, timedelta

s = requests.Session()
s.post('http://88.99.146.218/login', data={'username': 'admin', 'password': 'admin123'}, allow_redirects=False)

# Get error logs with more detail
r = s.get('http://88.99.146.218/api/error-logs?limit=200')
data = r.json()
elogs = data.get('logs', data) if isinstance(data, dict) else data
print('Error logs fetched:', len(elogs))

# Group by error message patterns
patterns = Counter()
for e in elogs:
    msg = str(e.get('error_message') or e.get('message', ''))
    # Classify
    if 'not found in search' in msg.lower() or 'site not found' in msg.lower():
        patterns['Site not found in search results'] += 1
    elif 'profile' in msg.lower() and 'not found' in msg.lower():
        patterns['Profile not found'] += 1
    elif 'captcha' in msg.lower() and 'blocked' in msg.lower():
        patterns['Captcha blocked search'] += 1
    elif 'showcaptcha' in msg.lower():
        patterns['Showcaptcha captcha failed'] += 1
    elif 'softtimelimit' in msg.lower():
        patterns['SoftTimeLimitExceeded'] += 1
    elif 'browser' in msg.lower() and ('dead' in msg.lower() or 'died' in msg.lower() or 'crash' in msg.lower()):
        patterns['Browser dead/crashed'] += 1
    elif 'proxy' in msg.lower() and ('tunnel' in msg.lower() or 'connection' in msg.lower()):
        patterns['Proxy connection error'] += 1
    elif 'timeout' in msg.lower() or 'timed out' in msg.lower():
        patterns['Timeout'] += 1
    elif 'foreignkey' in msg.lower():
        patterns['ForeignKey violation'] += 1
    elif 'renderer' in msg.lower():
        patterns['Renderer dead'] += 1
    else:
        # show first 80 chars
        patterns[msg[:80]] += 1

print('\n=== ERROR LOG PATTERNS (last 200 error entries) ===')
for reason, count in patterns.most_common(25):
    print('  [{:3d}] {}'.format(count, reason))

# Sample of last 10 SoftTimeLimitExceeded errors 
print('\n=== SOFT TIME LIMIT ERRORS (detail) ===')
stl_count = 0
for e in elogs:
    msg = str(e.get('error_message') or '')
    if 'softtimelimit' in msg.lower():
        stl_count += 1
        detail = str(e.get('error_detail') or e.get('error_message', ''))[:300]
        print('  [{}] profile={} keyword={} proxy={} dur={}s'.format(
            e.get('created_at','')[:19], e.get('profile_id'), 
            str(e.get('keyword',''))[:30], e.get('proxy_host',''),
            e.get('task_duration','')))
        if stl_count >= 5:
            break

# Timing analysis of completed vs hung
r2 = s.get('http://88.99.146.218/api/yandex-search-logs?limit=1000')
data2 = r2.json()
logs = data2.get('logs', data2) if isinstance(data2, dict) else data2

# Check: how many tasks become in_progress but never get result (watchdog-killed)?
# And what was the hour distribution of completed tasks vs hung tasks?
completed_hours = Counter()
hung_hours = Counter()
for l in logs:
    sa = l.get('started_at', '')
    if not sa or len(sa) < 13:
        continue
    hour = sa[11:13]
    if l.get('status') == 'completed':
        completed_hours[hour] += 1
    elif l.get('status') == 'failed' and 'in_progress' in str(l.get('error_message','')):
        hung_hours[hour] += 1

print('\n=== HOURLY: COMPLETED vs HUNG ===')
print('  Hour | Completed | Hung(watchdog) | Success%')
for h in sorted(set(list(completed_hours.keys()) + list(hung_hours.keys()))):
    c = completed_hours.get(h, 0)
    hu = hung_hours.get(h, 0)
    total = c + hu
    pct = (c / total * 100) if total > 0 else 0
    print('  {}:xx |    {:4d}   |     {:4d}       | {:.0f}%'.format(h, c, hu, pct))

# Check if same profiles keep hanging
hung_tasks = [l for l in logs if l.get('status') == 'failed' and 'in_progress' in str(l.get('error_message',''))]
# check parameters for profile_id since task record profile_id is null
param_profiles = Counter()
for t in hung_tasks:
    p = t.get('parameters', {})
    if isinstance(p, dict):
        pid = p.get('profile_id')
        if pid:
            param_profiles[pid] += 1
print('\n=== PROFILES THAT HANG (from parameters) ===')
for pid, count in param_profiles.most_common(20):
    print('  profile {}: {} hangs'.format(pid, count))

# Check: are hung tasks even starting (do they have started_at)?
has_started = sum(1 for t in hung_tasks if t.get('started_at'))
no_started = sum(1 for t in hung_tasks if not t.get('started_at'))
print('\nHung tasks with started_at: {}'.format(has_started))
print('Hung tasks WITHOUT started_at: {}'.format(no_started))

# Timing: how long between created_at and started_at for hung tasks?
startup_delays = []
for t in hung_tasks:
    ca = t.get('created_at', '')
    sa = t.get('started_at', '')
    if ca and sa:
        try:
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
                try:
                    t1 = datetime.strptime(ca[:26], fmt)
                    break
                except:
                    t1 = None
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
                try:
                    t2 = datetime.strptime(sa[:26], fmt)
                    break
                except:
                    t2 = None
            if t1 and t2:
                startup_delays.append((t2 - t1).total_seconds())
        except:
            pass
if startup_delays:
    startup_delays.sort()
    print('\nStartup delay (created→started) for hung tasks:')
    print('  min: {:.0f}s, max: {:.0f}s, median: {:.0f}s, avg: {:.0f}s'.format(
        min(startup_delays), max(startup_delays), 
        startup_delays[len(startup_delays)//2], sum(startup_delays)/len(startup_delays)))
    print('  >60s: {}, >120s: {}, >300s: {}'.format(
        sum(1 for d in startup_delays if d > 60),
        sum(1 for d in startup_delays if d > 120),
        sum(1 for d in startup_delays if d > 300)))

# Time between started and watchdog kill
execution_times = []
for t in hung_tasks:
    sa = t.get('started_at', '')
    ca = t.get('completed_at', '')
    if sa and ca:
        try:
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
                try:
                    t1 = datetime.strptime(sa[:26], fmt)
                    break
                except:
                    t1 = None
            for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S']:
                try:
                    t2 = datetime.strptime(ca[:26], fmt)
                    break
                except:
                    t2 = None
            if t1 and t2:
                execution_times.append((t2 - t1).total_seconds())
        except:
            pass
if execution_times:
    execution_times.sort()
    print('\nExecution time (started→watchdog_kill) for hung tasks:')
    print('  min: {:.0f}s, max: {:.0f}s, median: {:.0f}s, avg: {:.0f}s'.format(
        min(execution_times), max(execution_times),
        execution_times[len(execution_times)//2], sum(execution_times)/len(execution_times)))
