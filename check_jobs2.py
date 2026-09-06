import json
import sqlite3
import urllib.request

DB = r"D:\自媒体\漫画工作流-ui-dev\storage\mangaflow.db"
CAND = "9c7394ef-3412-44bc-aca1-e7b168612f5c"
PROJECT = "ce7a4e35-7810-4d10-af18-03308eb37bd0"

with urllib.request.urlopen(f"http://127.0.0.1:8000/api/v1/projects/{PROJECT}/jobs?limit=8") as resp:
    jobs = json.loads(resp.read().decode("utf-8"))
for j in jobs:
    print(j["job_type"], "|", j["status"], "|", j.get("created_at"), "|", j.get("id", "")[:8])

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
print("--- candidate ---")
cols = [r[1] for r in con.execute("PRAGMA table_info(page_candidates)").fetchall()]
sel = [c for c in ("id", "status", "is_selected", "version") if c in cols]
c = con.execute(f"SELECT {', '.join(sel)} FROM page_candidates WHERE id = ?", (CAND,)).fetchone()
print(dict(c))
print("--- inspection rows ---")
for r in con.execute(
    "SELECT category, outcome, storyboard_version, created_at FROM inspection_results"
    " WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 6", (CAND,)
):
    print(dict(r))
