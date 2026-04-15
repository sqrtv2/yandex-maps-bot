"""Fix duplicate profile names in the database.

Multiple profiles share the same name (e.g., Profile-26039 has 5 profiles with IDs
26239, 26339, 26439, 26639, 26839). This causes Chrome crashes because two tasks
may try to open the same user-data-dir simultaneously.

Fix: Rename duplicate profiles to Profile-{actual_id}, keeping one per group with
the original name (preferring the profile whose ID matches the name-number).
"""
import psycopg2
import os

DB_URL = os.environ.get('YANDEX_BOT_DATABASE_URL', 'postgresql://postgres:password@postgres:5432/yandex_maps_bot')


def fix_duplicates():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = True
    cur = conn.cursor()

    total_renamed = 0
    for iteration in range(10):
        cur.execute("""
            SELECT name, array_agg(id ORDER BY id) as ids
            FROM browser_profiles
            GROUP BY name
            HAVING COUNT(*) > 1
            ORDER BY name
        """)
        duplicates = cur.fetchall()
        if not duplicates:
            break
        print(f"Iteration {iteration}: {len(duplicates)} duplicate groups")

        fixed_this_round = 0
        for name, ids in duplicates:
            try:
                name_num = int(name.replace('Profile-', ''))
            except ValueError:
                name_num = None

            keeper_id = name_num if name_num in ids else ids[0]

            for pid in ids:
                if pid == keeper_id:
                    continue
                new_name = f"Profile-{pid}"
                cur.execute("SELECT COUNT(*) FROM browser_profiles WHERE name = %s AND id != %s", (new_name, pid))
                if cur.fetchone()[0] > 0:
                    # Fallback: use suffix
                    new_name = f"Profile-{pid}r"
                    cur.execute("SELECT COUNT(*) FROM browser_profiles WHERE name = %s AND id != %s", (new_name, pid))
                    if cur.fetchone()[0] > 0:
                        print(f"  SKIP: {name} id={pid} (no available name)")
                        continue
                cur.execute("UPDATE browser_profiles SET name = %s WHERE id = %s", (new_name, pid))
                total_renamed += 1
                fixed_this_round += 1
                print(f"  RENAME: {name} id={pid} -> {new_name}")

        print(f"  Fixed {fixed_this_round} in this iteration")
        if fixed_this_round == 0:
            break

    print(f"\nTotal renamed: {total_renamed}")

    # Verify no duplicates remain
    cur.execute("SELECT name, COUNT(*) FROM browser_profiles GROUP BY name HAVING COUNT(*) > 1")
    remaining = cur.fetchall()
    if remaining:
        print(f"WARNING: {len(remaining)} duplicate names still remain!")
        for row in remaining:
            print(f"  {row[0]}: {row[1]} profiles")
    else:
        print("All profile names are now unique.")
        # Add unique constraint
        try:
            cur.execute("ALTER TABLE browser_profiles ADD CONSTRAINT browser_profiles_name_unique UNIQUE (name)")
            print("Added UNIQUE constraint on browser_profiles.name")
        except Exception as e:
            print(f"Could not add constraint: {e}")

    cur.close()
    conn.close()


if __name__ == '__main__':
    fix_duplicates()
