from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Project
from app.schemas import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter()


@router.get("", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(
        db.scalars(
            select(Project).where(Project.deleted_at.is_(None)).order_by(Project.updated_at.desc())
        )
    )


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    values = payload.model_dump()
    project = Project(
        **values,
        image_model_alias=values["last_image_model_alias"] or "image.nano_banana_2",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)
) -> Project:
    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.version != payload.version:
        raise HTTPException(status_code=409, detail="项目已被其他操作更新，请刷新后重试")

    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    for key, value in changes.items():
        setattr(project, key, value)
    if changes.get("last_image_model_alias"):
        project.image_model_alias = changes["last_image_model_alias"]
    project.version += 1
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_project(project_id: str, db: Session = Depends(get_db)) -> None:
    from app.models import utcnow

    project = db.get(Project, project_id)
    if not project or project.deleted_at is not None:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.deleted_at = utcnow()
    project.version += 1
    db.commit()
