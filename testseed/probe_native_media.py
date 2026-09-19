"""Probe native API + check thumbnail serving and native image fetch path."""
import json
import sqlite3
import urllib.request

base = "http://127.0.0.1:9548/api/v1"
conn = sqlite3.connect(r"output\nui67-acceptance\run-p1-v4\native-data\data\mangaflow.db")
rows = conn.execute(
    "select id, thumbnail_320_key, source from assets where project_id in "
    "(select id from projects where name like '%主项目%') limit 3"
).fetchall()
conn.close()
print("assets:", rows)

for path in ("/health", f"/assets/{rows[0][0]}/thumbnail/320", f"/assets/{rows[0][0]}/content"):
    try:
        with urllib.request.urlopen(base + path, timeout=5) as r:
            print(path, r.status, len(r.read()), r.headers.get("content-type"))
    except Exception as e:  # noqa: BLE001
        print(path, "FAIL", e)
