"""One-shot read-back verification of the NUI67 seed dataset over the live API."""
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = r"D:\自媒体\漫画工作流"
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
    cwd=REPO, env=env,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
    print("projects:", [p["name"] for p in projects])
    main_id = next(p["id"] for p in projects if "主项目" in p["name"])
    chapters = get(f"/projects/{main_id}/chapters")
    print("chapters:", [c["title"] for c in chapters])
    ch1 = chapters[0]["id"]
    script = get(f"/chapters/{ch1}/script")
    print("script scenes:", len(script.get("scenes", [])), "beats:",
          sum(len(s.get("beats", [])) for s in script.get("scenes", [])))
    characters = get(f"/projects/{main_id}/characters")
    print("character keys:", sorted(characters[0])[:10] if characters else "EMPTY")
    print("characters:", len(characters))
    assets = get(f"/assets?project_id={main_id}")
    print("assets:", len(assets))
    if assets:
        with urllib.request.urlopen(f"{base}/assets/{assets[0]['id']}/thumbnail/320", timeout=10) as r:
            print("thumbnail 320 bytes:", len(r.read()))
    pages = get(f"/chapters/{ch1}/pages")
    print("pages:", len(pages))
    jobs = get(f"/projects/{main_id}/jobs")
    jobs_list = jobs if isinstance(jobs, list) else jobs.get("items", [])
    print("jobs:", [j.get("status") for j in jobs_list])
    usage = get(f"/usage/summary?project_id={main_id}")
    print("usage summary keys:", sorted(usage)[:8])
    providers = get("/providers")
    providers_list = providers if isinstance(providers, list) else providers.get("items", [])
    print("providers:", len(providers_list))
    workflows = get(f"/projects/{main_id}/workflows")
    print("workflows:", len(workflows), "draft nodes:",
          len((workflows[0].get("draft_graph") or {}).get("nodes", [])) if workflows else 0)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
