"""Reproduce the jobs 500 in-process against the seeded DB."""
import os
import sys
import traceback

os.environ["DATABASE_URL"] = "sqlite:///D:/自媒体/漫画工作流/testseed/data/mangaflow.db"
os.environ["STORAGE_ROOT"] = r"D:\自媒体\漫画工作流\testseed\storage"
os.environ["UPLOAD_ROOT"] = r"D:\自媒体\漫画工作流\testseed\uploads"
os.environ["MANGAFLOW_DISABLE_DOTENV"] = "1"
os.environ["QUEUE_ENABLED"] = "false"

sys.path.insert(0, r"D:\自媒体\漫画工作流\apps\api")

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app.models import GenerationJob, Project
from app.api.routes.workflow.jobs import _job_reads

with Session(engine) as db:
    project = db.scalars(select(Project).where(Project.name.contains("主项目"))).first()
    jobs = list(db.scalars(select(GenerationJob).where(GenerationJob.project_id == project.id)))
    print("jobs in db:", len(jobs))
    try:
        reads = _job_reads(db, jobs)
        print("serialized OK:", [(j.status, j.estimated_cost) for j in reads])
    except Exception:
        traceback.print_exc()
