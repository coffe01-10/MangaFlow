from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db

router = APIRouter()


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    payload = {"status": "ok", "database": "ok"}
    run_id = get_settings().e2e_run_id
    if run_id:
        payload["e2e_run_id"] = run_id
    return payload
