"""Question-set endpoints (spec 5.3).

Permissions: reads are project:view; set and question mutations are
project:edit_content (curating test content). Audit actions:
question_set.created / question_set.archived and question.created /
question.updated / question.forked / question.archived.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from graphrag_ui.api.deps import CurrentUser, DbSession, get_current_user
from graphrag_ui.api.errors import ApiError
from graphrag_ui.api.projects_routes import _forbidden, _project_or_404
from graphrag_ui.domain.permissions import Atom, can
from graphrag_ui.domain.questions import MAX_QUESTION_CHARS
from graphrag_ui.services import questions as questions_service
from graphrag_ui.services.projects import get_member_perms
from graphrag_ui.services.questions import (
    QuestionNotFound,
    QuestionSetNotFound,
    QuestionSetTooLarge,
)


class SetIn(BaseModel):
    # extra="forbid": a body with unknown keys is a caller bug, not a
    # silently ignored field (same posture as every other body here).
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)


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
    async def list_sets(pid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        return SetListOut(
            sets=[SetOut.model_validate(s) for s in await questions_service.list_sets(db, project)]
        )

    @router.post("/{pid}/question-sets", response_model=SetOut, status_code=status.HTTP_201_CREATED)
    async def create_set(pid: uuid.UUID, body: SetIn, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        qs = await questions_service.create_set(db, project, body.name, actor_id=user.id)
        return SetOut.model_validate(qs)

    @router.delete("/{pid}/question-sets/{sid}", status_code=status.HTTP_204_NO_CONTENT)
    async def archive_set(pid: uuid.UUID, sid: uuid.UUID, db: DbSession, user: CurrentUser):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            await questions_service.archive_set(db, project, sid, actor_id=user.id)
        except QuestionSetNotFound:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "question_set_not_found", "question set not found"
            ) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/{pid}/question-sets/{sid}/questions", response_model=QuestionListOut)
    async def list_questions(pid: uuid.UUID, sid: uuid.UUID, db: DbSession, user: CurrentUser):
        await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_view,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            questions = await questions_service.live_questions(db, pid, sid)
        except QuestionSetNotFound:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "question_set_not_found", "question set not found"
            ) from None
        return QuestionListOut(questions=[QuestionOut.model_validate(q) for q in questions])

    @router.post(
        "/{pid}/question-sets/{sid}/questions",
        response_model=QuestionOut,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_question(
        pid: uuid.UUID, sid: uuid.UUID, body: QuestionIn, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            q = await questions_service.add_question(db, project, sid, body.text, actor_id=user.id)
        except QuestionSetNotFound:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "question_set_not_found", "question set not found"
            ) from None
        except QuestionSetTooLarge as e:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST, "question_set_too_large", str(e), e.params
            ) from None
        return QuestionOut.model_validate(q)

    @router.patch("/{pid}/question-sets/{sid}/questions/{qid}", response_model=QuestionOut)
    async def edit_question(
        pid: uuid.UUID,
        sid: uuid.UUID,
        qid: uuid.UUID,
        body: QuestionIn,
        db: DbSession,
        user: CurrentUser,
    ):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            q = await questions_service.edit_question(db, project, qid, body.text, actor_id=user.id)
        except QuestionNotFound:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "question_not_found", "question not found"
            ) from None
        return QuestionOut.model_validate(q)

    @router.delete(
        "/{pid}/question-sets/{sid}/questions/{qid}", status_code=status.HTTP_204_NO_CONTENT
    )
    async def archive_question(
        pid: uuid.UUID, sid: uuid.UUID, qid: uuid.UUID, db: DbSession, user: CurrentUser
    ):
        project = await _project_or_404(db, pid)
        if not can(
            user.global_perms,
            user.is_active,
            Atom.project_edit_content,
            await get_member_perms(db, pid, user.id),
        ):
            raise _forbidden()
        try:
            await questions_service.archive_question(db, project, qid, actor_id=user.id)
        except QuestionNotFound:
            raise ApiError(
                status.HTTP_404_NOT_FOUND, "question_not_found", "question not found"
            ) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    app.include_router(router)
