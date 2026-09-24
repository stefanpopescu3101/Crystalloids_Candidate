from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Subject = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Body = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)]
PostId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
CommentId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class PostCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: Subject
    body: Body


class PostUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: Subject | None = None
    body: Body | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("Supply subject, body, or both")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Fields cannot be null")
        return self


class Post(BaseModel):
    id: PostId
    author_id: str
    created_at: datetime
    updated_at: datetime
    subject: str
    body: str


class PostPage(BaseModel):
    items: list[Post]
    next_cursor: str | None = None


class CommentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Body


class CommentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Body | None = None

    @model_validator(mode="after")
    def require_body(self):
        if self.body is None:
            raise ValueError("Supply a non-null body")
        return self


class Comment(BaseModel):
    id: CommentId
    author_id: str
    created_at: datetime
    updated_at: datetime
    body: str


class CommentPage(BaseModel):
    items: list[Comment]
    next_cursor: str | None = None
