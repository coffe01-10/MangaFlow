"""Director command journal API (V02-40). Independent of the workflow router."""

from fastapi import APIRouter, Depends, Query, Response
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    DirectorCommandGroupRead,
    DirectorCommandPropose,
    DirectorUtteranceQueued,
    DirectorUtteranceRequest,
)
from app.services.director_commands import (
    accept_command,
    discard_group,
    get_command_group,
    list_command_groups,
    propose_command_group,
    redo_command,
    reject_command,
    submit_utterance,
    undo_command,
)
from app.services.job_service import enqueue_job

router = APIRouter()


@router.post(
    "/projects/{project_id}/director/utterances",
    response_model=DirectorUtteranceQueued,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def create_director_utterance(
    project_id: str,
    payload: DirectorUtteranceRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    """DIR-01A §2: enqueue a DIRECTOR_PARSE job for one NL utterance."""
    result = submit_utterance(db, project_id, payload.model_dump())
    job = result.pop("_job")
    db.commit()
    job = enqueue_job(db, job)
    result["job_status"] = str(job.status)
    # 契约 §2：幂等重放返回 200（新建入队为 202），客户端以
    # idempotent_replay 区分。
    if result.get("idempotent_replay"):
        response.status_code = http_status.HTTP_200_OK
    return result


@router.post(
    "/projects/{project_id}/director/command-groups",
    response_model=DirectorCommandGroupRead,
)
def create_director_command_group(
    project_id: str,
    payload: DirectorCommandPropose,
    db: Session = Depends(get_db),
) -> dict:
    return propose_command_group(db, project_id, payload.model_dump())


@router.get(
    "/projects/{project_id}/director/command-groups",
    response_model=list[DirectorCommandGroupRead],
)
def list_director_command_groups(
    project_id: str,
    page_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_command_groups(db, project_id, page_id)


@router.get(
    "/projects/{project_id}/director/command-groups/{command_group_id}",
    response_model=DirectorCommandGroupRead,
)
def read_director_command_group(
    project_id: str,
    command_group_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return get_command_group(db, project_id, command_group_id)


@router.post(
    "/projects/{project_id}/director/command-groups/{command_group_id}/discard",
    response_model=DirectorCommandGroupRead,
)
def discard_director_command_group(
    project_id: str,
    command_group_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return discard_group(db, project_id, command_group_id)


@router.post(
    "/projects/{project_id}/director/commands/{command_id}/accept",
    response_model=DirectorCommandGroupRead,
)
def accept_director_command(
    project_id: str,
    command_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return accept_command(db, project_id, command_id)


@router.post(
    "/projects/{project_id}/director/commands/{command_id}/reject",
    response_model=DirectorCommandGroupRead,
)
def reject_director_command(
    project_id: str,
    command_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return reject_command(db, project_id, command_id)


@router.post(
    "/projects/{project_id}/director/commands/{command_id}/undo",
    response_model=DirectorCommandGroupRead,
)
def undo_director_command(
    project_id: str,
    command_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return undo_command(db, project_id, command_id)


@router.post(
    "/projects/{project_id}/director/commands/{command_id}/redo",
    response_model=DirectorCommandGroupRead,
)
def redo_director_command(
    project_id: str,
    command_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return redo_command(db, project_id, command_id)
