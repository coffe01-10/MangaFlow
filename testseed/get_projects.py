"""Get the main project id for web-side URLs."""
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/projects", timeout=10) as r:
    projects = json.loads(r.read())
for p in projects:
    print(p["id"], p["name"])
