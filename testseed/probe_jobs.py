"""Capture the /projects/{id}/jobs 500 traceback from a temp uvicorn."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

REPO = r"D:\自媒体\漫画工作流"
log = open(REPO + r"\testseed\uvicorn-stderr.log", "wb")
env = dict(os.environ)
env.update(
    DATABASE_URL="sqlite:///D:/自媒体/漫画工作流/testseed/data/mangaflow.db",
    STORAGE_ROOT=r"D:\自媒体\漫画工作流\testseed\storage",
    UPLOAD_ROOT=r"D:\自媒体\漫画工作流\testseed\uploads",
    MANGAFLOW_DISABLE_DOTENV="1",
    QUEUE_ENABLED="false",
)
proc = subprocess.Popen(
    [REPO + r"\.venv\Scripts\python.exe", "-m", "uvicorn", "app.main:app",
     "--app-dir", REPO + r"\apps\api", "--host", "127.0.0.1", "--port", "8100"],
    cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=log,
)
base = "http://127.0.0.1:8100/api/v1"


def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


try:
    for _ in range(40):
        try:
            get("/health")
            break
        except Exception:
            time.sleep(1)
    projects = get("/projects")
    main_id = next(p["id"] for p in projects if "主项目" in p["name"])
    try:
        jobs = get(f"/projects/{main_id}/jobs")
        print("jobs OK:", [j.get("status") for j in jobs])
    except urllib.error.HTTPError as error:
        print("jobs failed:", error.code)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
print(open(REPO + r"\testseed\uvicorn-stderr.log", encoding="utf-8", errors="replace").read()[-3000:])
