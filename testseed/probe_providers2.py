"""Dump shapes of /providers and /models payloads from the native API."""
import json
import urllib.request

base = "http://127.0.0.1:5078/api/v1"
for path in ("/providers", "/models"):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        data = json.loads(r.read())
    print("==", path, "type:", type(data).__name__, "count:", len(data) if isinstance(data, list) else "?")
    if isinstance(data, list) and data:
        first = data[0]
        print("first keys:", sorted(first))
        print(json.dumps(first, ensure_ascii=False)[:600])
