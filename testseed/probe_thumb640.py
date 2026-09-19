"""Probe both thumbnail sizes and content on the native API."""
import sqlite3
import urllib.request

base = "http://127.0.0.1:9548/api/v1"
conn = sqlite3.connect(r"output\nui67-acceptance\run-p1-v4\native-data\data\mangaflow.db")
rows = conn.execute(
    "select id, thumbnail_320_key, thumbnail_640_key, source from assets limit 3"
).fetchall()
conn.close()
for aid, k320, k640, source in rows:
    for size, key in (("320", k320), ("640", k640)):
        path = f"/assets/{aid}/thumbnail/{size}"
        try:
            with urllib.request.urlopen(base + path, timeout=5) as r:
                print(path, r.status, len(r.read()), r.headers.get("content-type"), "key:", key)
        except Exception as e:  # noqa: BLE001
            print(path, "FAIL", e, "key:", key)
