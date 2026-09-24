import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError

from app.auth import AuthenticatedUser, current_user
from app.config import Settings
from app.events import CommentEvents, PubSubCommentEvents
from app.models import (
    Comment,
    CommentCreate,
    CommentId,
    CommentPage,
    CommentUpdate,
    Post,
    PostCreate,
    PostId,
    PostPage,
    PostUpdate,
)
from app.repository import (
    CommentNotFound,
    FirestorePosts,
    NotCommentAuthor,
    NotPostAuthor,
    PostNotFound,
    PostsRepository,
)

logger = logging.getLogger(__name__)


def repository(request: Request) -> PostsRepository:
    return request.app.state.repository


def comment_events(request: Request) -> CommentEvents:
    return request.app.state.comment_events


User = Annotated[AuthenticatedUser, Depends(current_user)]
Repository = Annotated[PostsRepository, Depends(repository)]
Events = Annotated[CommentEvents, Depends(comment_events)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")]


def create_app(
    settings: Settings | None = None,
    posts: PostsRepository | None = None,
    events: CommentEvents | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings if settings is not None else Settings()
        app.state.repository = posts if posts is not None else FirestorePosts(app.state.settings)
        app.state.comment_events = (
            events if events is not None else PubSubCommentEvents(app.state.settings)
        )
        yield

    app = FastAPI(title="Posts API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(PostNotFound)
    async def not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Post not found"})

    @app.exception_handler(NotPostAuthor)
    async def forbidden(request, exc):
        return JSONResponse(
            status_code=403, content={"detail": "Only the author can modify this post"}
        )

    @app.exception_handler(CommentNotFound)
    async def comment_not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "Comment not found"})

    @app.exception_handler(NotCommentAuthor)
    async def comment_forbidden(request, exc):
        return JSONResponse(
            status_code=403, content={"detail": "Only the author can modify this comment"}
        )

    async def storage_unavailable(request, exc):
        logger.error("Firestore request failed", exc_info=exc)
        return JSONResponse(status_code=503, content={"detail": "Post storage unavailable"})

    app.add_exception_handler(GoogleAPICallError, storage_unavailable)
    app.add_exception_handler(DefaultCredentialsError, storage_unavailable)

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok"}

    @app.post("/posts", response_model=Post, status_code=201, tags=["posts"], summary="Add Post")
    def add_post(data: PostCreate, user: User, repo: Repository, response: Response):
        post = repo.create(user.id, data)
        response.headers["Location"] = f"/posts/{post.id}"
        return post

    @app.get("/posts", response_model=PostPage, tags=["posts"], summary="Get all Posts")
    def list_posts(user: User, repo: Repository, limit: PageSize = 20, cursor: Cursor = None):
        return repo.list(limit, cursor)

    @app.get("/posts/{post_id}", response_model=Post, tags=["posts"], summary="Get a specific Post")
    def get_post(post_id: PostId, user: User, repo: Repository):
        return repo.get(post_id)

    @app.get(
        "/users/{user_id}/posts",
        response_model=PostPage,
        tags=["posts"],
        summary="Get all Posts from a user",
    )
    def user_posts(
        user_id: str,
        user: User,
        repo: Repository,
        limit: PageSize = 20,
        cursor: Cursor = None,
    ):
        return repo.list(limit, cursor, author_id=user_id)

    @app.patch("/posts/{post_id}", response_model=Post, tags=["posts"], summary="Update Post")
    def update_post(post_id: PostId, data: PostUpdate, user: User, repo: Repository):
        return repo.update(post_id, user.id, data)

    @app.delete("/posts/{post_id}", status_code=204, tags=["posts"], summary="Delete Post")
    def delete_post(post_id: PostId, user: User, repo: Repository):
        repo.delete(post_id, user.id)
        return Response(status_code=204)

    @app.post(
        "/posts/{post_id}/comments",
        response_model=Comment,
        status_code=201,
        tags=["comments"],
        summary="Add Comment",
    )
    def add_comment(
        post_id: PostId,
        data: CommentCreate,
        user: User,
        repo: Repository,
        events: Events,
        response: Response,
    ):
        comment = repo.create_comment(post_id, user.id, data)
        events.publish_created(post_id, comment, user.email)
        response.headers["Location"] = f"/posts/{post_id}/comments/{comment.id}"
        return comment

    @app.get(
        "/posts/{post_id}/comments",
        response_model=CommentPage,
        tags=["comments"],
        summary="Get comments for a Post",
    )
    def list_comments(
        post_id: PostId, user: User, repo: Repository, limit: PageSize = 20, cursor: Cursor = None
    ):
        return repo.list_comments(post_id, limit, cursor)

    @app.patch(
        "/posts/{post_id}/comments/{comment_id}",
        response_model=Comment,
        tags=["comments"],
        summary="Update Comment",
    )
    def update_comment(
        post_id: PostId, comment_id: CommentId, data: CommentUpdate, user: User, repo: Repository
    ):
        return repo.update_comment(post_id, comment_id, user.id, data)

    @app.delete(
        "/posts/{post_id}/comments/{comment_id}",
        status_code=204,
        tags=["comments"],
        summary="Delete Comment",
    )
    def delete_comment(post_id: PostId, comment_id: CommentId, user: User, repo: Repository):
        repo.delete_comment(post_id, comment_id, user.id)
        return Response(status_code=204)

    return app


app = create_app()
