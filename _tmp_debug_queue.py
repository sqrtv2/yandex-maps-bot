import redis, json, psycopg2

r = redis.Redis(host='redis', port=6379)
print(f"yandex_maps queue len: {r.llen('yandex_maps')}")

# Check unacked
items = list(r.hgetall('unacked').items())
maps_count = 0
for field, value in items:
    try:
        data = json.loads(value.decode())
        if isinstance(data, list) and len(data) >= 3:
            headers = data[2]
            if isinstance(headers, dict):
                routing = headers.get('routing_key', '')
                if 'yandex_maps' in routing:
                    maps_count += 1
    except:
        pass
print(f"Total unacked: {len(items)}")
print(f"yandex_maps unacked: {maps_count}")

conn = psycopg2.connect("postgresql://postgres:password@postgres:5432/yandex_maps_bot")
cur = conn.cursor()
cur.execute("SELECT id, profile_id, created_at, status FROM tasks WHERE task_type='yandex_visit' AND status='pending' ORDER BY created_at DESC LIMIT 10")
print("\nPending tasks in DB:")
for row in cur.fetchall():
    print(f"  #{row[0]} p={row[1]} created={row[2]} status={row[3]}")

# When was the last scheduler run?
cur.execute("SELECT id, status, created_at, completed_at, error_message FROM tasks WHERE task_type='yandex_visit' ORDER BY created_at DESC LIMIT 5")
print("\nLatest yandex_visit tasks:")
for row in cur.fetchall():
    print(f"  #{row[0]} {row[1]} created={row[2]} done={row[3]} err={str(row[4])[:60] if row[4] else None}")
conn.close()
