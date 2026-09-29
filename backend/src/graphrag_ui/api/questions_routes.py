"""Question-set endpoints (spec 5.3).

Permissions: reads are project:view; set and question mutations are
project:edit_content (curating test content). Audit actions:
question_set.created / question_set.renamed / question_set.archived and
question.created / question.updated / question.forked / question.archived.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from graphrag_ui.api.deps import (
    CurrentUser,
    DbSession,
    ProjectEditContent,
    ProjectView,
    get_current_user,
)
from graphrag_ui.domain.questions import MAX_QUESTION_CHARS
from graphrag_ui.services import questions as questions_service


class SetIn(BaseModel):
    # extra="forbid": a body with unknown keys is a caller bug, not a
    # silently ignored field (same posture as every other body here).
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # A whitespace-only name would render as an empty picker entry.
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


def _uuid_to_str(v: object) -> object:
    # pydantic 2 does not implicitly coerce UUID to str (ProjectOut's validator)
    return str(v) if isinstance(v, uuid.UUID) else v


class SetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime

    @field_validator("id", mode="before")
    @classmethod
    def _id(cls, v: object) -> object:
        return _uuid_to_str(v)


class SetListOut(BaseModel):
    sets: list[SetOut]


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    lineage_id: str
    text: str
    position: int
    created_at: datetime

    @field_validator("id", "lineage_id", mode="before")
    @classmethod
    def _ids(cls, v: object) -> object:
        return _uuid_to_str(v)


class QuestionListOut(BaseModel):
    questions: list[QuestionOut]


def register_questions_routes(app):
    # Same conventions as files_routes: router built inside the function
    # (create_app() is called repeatedly in tests), auth on the router itself.
    router = APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])

    @router.get("/{pid}/question-sets", response_model=SetListOut)
    async def list_sets(project: ProjectView, db: DbSession):
        return SetListOut(
            sets=[SetOut.model_validate(s) for s in await questions_service.list_sets(db, project)]
        )

    @router.post("/{pid}/question-sets", response_model=SetOut, status_code=status.HTTP_201_CREATED)
    async def create_set(
        project: ProjectEditContent, body: SetIn, db: DbSession, user: CurrentUser
    ):
        qs = await questions_service.create_set(db, project, body.name, actor_id=user.id)
        return SetOut.model_validate(qs)

    @router.patch("/{pid}/question-sets/{sid}", response_model=SetOut)
    async def rename_set(
        project: ProjectEditContent, sid: uuid.UUID, body: SetIn, db: DbSession, user: CurrentUser
    ):
        qs = await questions_service.rename_set(db, project, sid, body.name, actor_id=user.id)
        return SetOut.model_validate(qs)

    @router.delete("/{pid}/question-sets/{sid}", status_code=status.HTTP_204_NO_CONTENT)
    async def archive_set(
        project: ProjectEditContent, sid: uuid.UUID, db: DbSession, user: CurrentUser
    ):
        await questions_service.archive_set(db, project, sid, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{pid}/question-sets/{sid}/questions", response_model=QuestionListOut)
    async def list_questions(project: ProjectView, sid: uuid.UUID, db: DbSession):
        questions = await questions_service.live_questions(db, project.id, sid)
        return QuestionListOut(questions=[QuestionOut.model_validate(q) for q in questions])

    @router.post(
        "/{pid}/question-sets/{sid}/questions",
        response_model=QuestionOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_question(
        project: ProjectEditContent,
        sid: uuid.UUID,
        body: QuestionIn,
        db: DbSession,
        user: CurrentUser,
    ):
        q = await questions_service.add_question(db, project, sid, body.text, actor_id=user.id)
        return QuestionOut.model_validate(q)

    @router.patch("/{pid}/question-sets/{sid}/questions/{qid}", response_model=QuestionOut)
    async def edit_question(
        project: ProjectEditContent,
        sid: uuid.UUID,
        qid: uuid.UUID,
        body: QuestionIn,
        db: DbSession,
        user: CurrentUser,
    ):
        q = await questions_service.edit_question(db, project, qid, body.text, actor_id=user.id)
        return QuestionOut.model_validate(q)

    @router.delete(
        "/{pid}/question-sets/{sid}/questions/{qid}", status_code=status.HTTP_204_NO_CONTENT
    )
    async def archive_question(
        project: ProjectEditContent,
        sid: uuid.UUID,
        qid: uuid.UUID,
        db: DbSession,
        user: CurrentUser,
    ):
        await questions_service.archive_question(db, project, qid, actor_id=user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
