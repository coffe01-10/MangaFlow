import sqlite3

DB = r"D:\自媒体\漫画工作流-ui-dev\storage\mangaflow.db"
CAND = "9c7394ef-3412-44bc-aca1-e7b168612f5c"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

rows = con.execute(
    "SELECT id, job_type, status, idempotency_key, created_at, finished_at FROM generation_jobs"
    " WHERE job_type = 'QUALITY_INSPECTION' ORDER BY created_at DESC LIMIT 5",
).fetchall()
for r in rows:
    print(dict(r))

print("--- candidate row ---")
c = con.execute(
    "SELECT id, status, is_selected, version, storyboard_version FROM page_candidates WHERE id = ?",
    (CAND,),
).fetchone()
print(dict(c))

print("--- latest inspections ---")
rows2 = con.execute(
    "SELECT category, outcome, storyboard_version, created_at FROM inspection_results"
    " WHERE candidate_id = ? ORDER BY created_at DESC LIMIT 6",
    (CAND,),
).fetchall()
for r in rows2:
    print(dict(r))
