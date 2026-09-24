from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from google.auth.exceptions import TransportError

from app.config import Settings
from app.main import create_app
from app.models import Comment, CommentPage, CommentUpdate, Post, PostPage
from app.repository import CommentNotFound, NotCommentAuthor, NotPostAuthor, PostNotFound


class MemoryPosts:
    """Test double only; production always uses Firestore."""

    def __init__(self):
        self.posts = {}
        self.comments = {}

    def create(self, author_id, data):
        now = datetime.now(UTC)
        post = Post(
            id=uuid4().hex, author_id=author_id, created_at=now, updated_at=now, **data.model_dump()
        )
        self.posts[post.id] = post
        return post

    def get(self, post_id):
        if post_id not in self.posts:
            raise PostNotFound
        return self.posts[post_id]

    def list(self, limit, cursor, author_id=None):
        posts = [
            p
            for key, p in sorted(self.posts.items())
            if (cursor is None or key > cursor) and (author_id is None or p.author_id == author_id)
        ]
        return PostPage(
            items=posts[:limit], next_cursor=posts[limit - 1].id if len(posts) > limit else None
        )

    def update(self, post_id, author_id, data):
        post = self.get(post_id)
        if post.author_id != author_id:
            raise NotPostAuthor
        self.posts[post_id] = post.model_copy(
            update={
                **data.model_dump(exclude_unset=True),
                "updated_at": datetime.now(UTC),
            }
        )
        return self.posts[post_id]

    def delete(self, post_id, author_id):
        if self.get(post_id).author_id != author_id:
            raise NotPostAuthor
        del self.posts[post_id]

    def create_comment(self, post_id, author_id, data):
        self.get(post_id)
        now = datetime.now(UTC)
        comment = Comment(
            id=uuid4().hex,
            author_id=author_id,
            created_at=now,
            updated_at=now,
            **data.model_dump(),
        )
        self.comments[(post_id, comment.id)] = comment
        return comment

    def list_comments(self, post_id, limit, cursor):
        self.get(post_id)
        comments = [
            comment
            for (parent_id, comment_id), comment in sorted(self.comments.items())
            if parent_id == post_id and (cursor is None or comment_id > cursor)
        ]
        return CommentPage(
            items=comments[:limit],
            next_cursor=comments[limit - 1].id if len(comments) > limit else None,
        )

    def update_comment(self, post_id, comment_id, author_id, data):
        self.get(post_id)
        key = (post_id, comment_id)
        if key not in self.comments:
            raise CommentNotFound
        comment = self.comments[key]
        if comment.author_id != author_id:
            raise NotCommentAuthor
        self.comments[key] = comment.model_copy(
            update={"body": data.body, "updated_at": datetime.now(UTC)}
        )
        return self.comments[key]

    def delete_comment(self, post_id, comment_id, author_id):
        self.update_comment(post_id, comment_id, author_id, CommentUpdate(body="unchanged"))
        del self.comments[(post_id, comment_id)]


class RecordedCommentEvents:
    def __init__(self):
        self.events = []

    def publish_created(self, post_id, comment, author_email):
        self.events.append((post_id, comment, author_email))


@pytest.fixture
def client():
    settings = Settings(
        _env_file=None,
        allow_gcloud_dev_tokens=False,
        google_cloud_project="test",
        google_oauth_client_id="test-client",
    )
    with TestClient(create_app(settings, MemoryPosts(), RecordedCommentEvents())) as client:
        yield client


@pytest.fixture
def verifier():
    def verify(token, request, audience):
        assert audience == "test-client"
        if token not in {"alice", "bob", "carol"}:
            raise ValueError("Invalid, expired, or wrong-audience token")
        return {"sub": token, "email": f"{token}@example.test", "email_verified": True}

    with patch("app.auth.id_token.verify_oauth2_token", side_effect=verify) as mock:
        yield mock


def headers(user="alice"):
    return {"Authorization": f"Bearer {user}"}


def add(client, user="alice"):
    response = client.post(
        "/posts", headers=headers(user), json={"subject": "Hello", "body": "World"}
    )
    assert response.status_code == 201
    assert response.headers["location"] == f"/posts/{response.json()['id']}"
    return response.json()


def add_comment(client, post_id, user="alice", body="A comment"):
    response = client.post(f"/posts/{post_id}/comments", headers=headers(user), json={"body": body})
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/posts", {"subject": "s", "body": "b"}),
        ("GET", "/posts", None),
        ("GET", "/posts/" + "a" * 32, None),
        ("GET", "/users/alice/posts", None),
        ("PATCH", "/posts/" + "a" * 32, {"body": "b"}),
        ("DELETE", "/posts/" + "a" * 32, None),
    ],
)
def test_every_endpoint_requires_login(client, method, path, payload):
    response = client.request(method, path, json=payload)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_token(client, verifier):
    assert client.get("/posts", headers=headers("invalid")).status_code == 401


def test_verification_service_unavailable(client):
    with patch("app.auth.id_token.verify_oauth2_token", side_effect=TransportError("offline")):
        assert client.get("/posts", headers=headers()).status_code == 503


def test_crud_and_ownership(client, verifier):
    post = add(client)
    path = f"/posts/{post['id']}"
    assert post["author_id"] == "alice"
    assert post["created_at"] == post["updated_at"]
    assert client.get(path, headers=headers("bob")).json() == post
    assert client.patch(path, headers=headers("bob"), json={"body": "stolen"}).status_code == 403
    assert client.delete(path, headers=headers("bob")).status_code == 403
    updated = client.patch(path, headers=headers(), json={"body": "Edited"})
    assert updated.status_code == 200
    assert updated.json()["body"] == "Edited"
    assert updated.json()["subject"] == post["subject"]
    assert updated.json()["created_at"] == post["created_at"]
    assert updated.json()["updated_at"] >= post["updated_at"]
    response = client.delete(path, headers=headers())
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(path, headers=headers()).status_code == 404
    assert client.patch(path, headers=headers(), json={"body": "x"}).status_code == 404
    assert client.delete(path, headers=headers()).status_code == 404


def test_comment_ownership_for_multiple_users(client, verifier):
    post = add(client, "alice")
    post_path = f"/posts/{post['id']}"
    bob = add_comment(client, post["id"], "bob", "Comment from B")
    carol = add_comment(client, post["id"], "carol", "Comment from C")
    bob_path = f"{post_path}/comments/{bob['id']}"
    carol_path = f"{post_path}/comments/{carol['id']}"

    assert client.patch(bob_path, headers=headers("alice"), json={"body": "No"}).status_code == 403
    assert client.delete(carol_path, headers=headers("bob")).status_code == 403
    assert client.patch(bob_path, headers=headers("carol"), json={"body": "No"}).status_code == 403

    updated = client.patch(bob_path, headers=headers("bob"), json={"body": "Updated by B"})
    assert updated.status_code == 200
    assert updated.json()["body"] == "Updated by B"
    assert updated.json()["author_id"] == "bob"
    page = client.get(f"{post_path}/comments", headers=headers("alice")).json()
    assert {comment["id"] for comment in page["items"]} == {bob["id"], carol["id"]}
    assert client.delete(carol_path, headers=headers("carol")).status_code == 204
    assert (
        client.patch(carol_path, headers=headers("carol"), json={"body": "gone"}).status_code == 404
    )


def test_comment_creation_publishes_author_email(client, verifier):
    post = add(client)
    comment = add_comment(client, post["id"], "bob")
    event = client.app.state.comment_events.events[0]
    assert event[0] == post["id"]
    assert event[1] == Comment.model_validate(comment)
    assert event[2] == "bob@example.test"


def test_comment_input_and_parent_validation(client, verifier):
    post = add(client)
    path = f"/posts/{post['id']}/comments"
    for payload in ({}, {"body": " "}, {"body": None}, {"body": "text", "author_id": "bob"}):
        assert client.post(path, headers=headers(), json=payload).status_code == 422
    assert (
        client.post(
            f"/posts/{'a' * 32}/comments", headers=headers(), json={"body": "x"}
        ).status_code
        == 404
    )
    comment = add_comment(client, post["id"])
    comment_path = f"{path}/{comment['id']}"
    for payload in ({}, {"body": None}, {"author_id": "bob"}):
        assert client.patch(comment_path, headers=headers(), json=payload).status_code == 422


def test_user_filter_and_pagination(client, verifier):
    alice = {add(client)["id"] for _ in range(3)}
    bob = add(client, "bob")["id"]
    seen = []
    params = {"limit": 1}
    while True:
        response = client.get("/users/alice/posts", headers=headers("bob"), params=params)
        assert response.status_code == 200
        page = response.json()
        seen.extend(p["id"] for p in page["items"])
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]
    assert len(seen) == 3
    assert set(seen) == alice
    all_posts = client.get("/posts", headers=headers()).json()["items"]
    assert {p["id"] for p in all_posts} == alice | {bob}
    assert client.get("/users/nobody/posts", headers=headers()).json()["items"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"subject": ""},
        {"body": "   "},
        {"body": None},
        {"author_id": "bob"},
        {"subject": "s", "created_at": "2020-01-01"},
        {"subject": "x" * 201},
    ],
)
def test_reject_invalid_updates(client, verifier, payload):
    post = add(client)
    assert client.patch(f"/posts/{post['id']}", headers=headers(), json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"subject": "s"},
        {"subject": " ", "body": "b"},
        {"subject": "s", "body": "b", "author_id": "bob"},
        {"subject": "s", "body": "b", "updated_at": "2020-01-01"},
        {"subject": "s", "body": "x" * 20001},
    ],
)
def test_reject_invalid_creation(client, verifier, payload):
    assert client.post("/posts", headers=headers(), json=payload).status_code == 422


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "cursor=invalid"])
def test_reject_invalid_pagination(client, verifier, query):
    assert client.get(f"/posts?{query}", headers=headers()).status_code == 422


def test_openapi_and_health(client):
    assert client.get("/health").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/posts"]["post"]["security"] == [{"HTTPBearer": []}]
