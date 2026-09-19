"""Probe the generation-workbench endpoint against the dev API."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000/api/v1"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read())


projects = get("/projects")
main_id = next(p["id"] for p in projects if "主项目" in p["name"])
chapters = get(f"/projects/{main_id}/chapters")
pages = get(f"/chapters/{chapters[0]['id']}/pages")
print("page0:", pages[0]["id"], "storyboard_version:", pages[0].get("storyboard_version"))
for page in pages:
    try:
        wb = get(f"/pages/{page['id']}/generation-workbench")
        print("workbench OK page", page["page_number"], "keys:", len(wb))
    except urllib.error.HTTPError as error:
        print("workbench FAIL page", page["page_number"], error.code, error.read().decode("utf-8", "replace")[:300])
