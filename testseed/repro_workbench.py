"""Reproduce the generation-workbench 500 via TestClient with lifespan."""
import os
import sys
import traceback

os.environ["DATABASE_URL"] = "sqlite:///D:/自媒体/漫画工作流/testseed/data/mangaflow.db"
os.environ["STORAGE_ROOT"] = r"D:\自媒体\漫画工作流\testseed\storage"
os.environ["UPLOAD_ROOT"] = r"D:\自媒体\漫画工作流\testseed\uploads"
os.environ["MANGAFLOW_DISABLE_DOTENV"] = "1"
os.environ["QUEUE_ENABLED"] = "false"

sys.path.insert(0, r"D:\自媒体\漫画工作流\apps\api")

from fastapi.testclient import TestClient

from app.main import app

with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=True) as client:
    response = client.get("/api/v1/projects")
    print("projects status:", response.status_code, "ct:", response.headers.get("content-type"))
    if response.status_code != 200:
        print(response.text[:500])
        raise SystemExit(1)
    projects = response.json()
    main_id = next(p["id"] for p in projects if "主项目" in p["name"])
    chapters = client.get(f"/api/v1/projects/{main_id}/chapters").json()
    pages = client.get(f"/api/v1/chapters/{chapters[0]['id']}/pages").json()
    try:
        response = client.get(f"/api/v1/pages/{pages[0]['id']}/generation-workbench")
        print("status:", response.status_code)
    except Exception:
        traceback.print_exc()
