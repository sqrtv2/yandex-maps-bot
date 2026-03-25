import sqlalchemy as sa

engine = sa.create_engine("postgresql://postgres:password@postgres:5432/yandex_maps_bot")
conn = engine.connect()

# Check search targets and their keywords
r = conn.execute(sa.text("SELECT id, domain, keywords, is_active FROM yandex_search_targets WHERE is_active=true"))
print("=== ACTIVE SEARCH TARGETS ===")
for row in r:
    kws = row[2].strip().split("\n") if row[2] else []
    active_kws = [k for k in kws if k.strip()]
    print(f"  ID={row[0]} domain={row[1]} active={row[3]} keywords={len(active_kws)}")
    for kw in active_kws[:5]:
        print(f"    - {kw.strip()[:60]}")
    if len(active_kws) > 5:
        print(f"    ... +{len(active_kws)-5} more")

# Recent position history
print()
r2 = conn.execute(sa.text("""SELECT keyword, domain, found, page, position, absolute_position, clicked, checked_at 
    FROM search_position_history 
    ORDER BY checked_at DESC LIMIT 20"""))
print("=== RECENT POSITION HISTORY ===")
for row in r2:
    kw = row[0][:45] if row[0] else "?"
    dom = row[1] or "?"
    found = "FOUND" if row[2] else "NOT_FOUND"
    print(f"  [{dom}] '{kw}' {found} page={row[3]} pos={row[4]} abs={row[5]} clicked={row[6]} at={row[7]}")

# Aggregate stats per domain
print()
r2b = conn.execute(sa.text("""SELECT domain, 
    COUNT(*) as total,
    SUM(CASE WHEN found=true THEN 1 ELSE 0 END) as found_cnt,
    SUM(CASE WHEN found=false THEN 1 ELSE 0 END) as nf_cnt,
    SUM(CASE WHEN clicked=true THEN 1 ELSE 0 END) as clicked_cnt
    FROM search_position_history 
    WHERE checked_at >= NOW() - INTERVAL '7 days'
    GROUP BY domain"""))
print("=== POSITION STATS (7 DAYS) ===")
for row in r2b:
    print(f"  {row[0]}: total={row[1]} found={row[2]} not_found={row[3]} clicked={row[4]}")

# Check disabled keywords
print()
r3 = conn.execute(sa.text("""SELECT domain, disabled_keywords FROM yandex_search_targets WHERE is_active=true"""))
print("=== DISABLED KEYWORDS ===")
for row in r3:
    if row[1]:
        disabled = [k for k in row[1].strip().split("\n") if k.strip()]
        print(f"  {row[0]}: {len(disabled)} disabled keywords")
        for kw in disabled[:5]:
            print(f"    - {kw.strip()[:60]}")
        if len(disabled) > 5:
            print(f"    ... +{len(disabled)-5} more")
    else:
        print(f"  {row[0]}: 0 disabled keywords")

conn.close()
