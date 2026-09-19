"""Out-of-band version bump on the native project row (simulates a concurrent writer)."""
import sqlite3

conn = sqlite3.connect(r"output\nui67-acceptance\run-p2\native-data\data\mangaflow.db")
before = conn.execute("select id, name, version, default_concurrency from projects where name like '%主项目%'").fetchall()
conn.execute("update projects set version = version + 1 where name like '%主项目%'")
conn.commit()
after = conn.execute("select id, name, version, default_concurrency from projects where name like '%主项目%'").fetchall()
conn.close()
print("before:", before)
print("after:", after)
