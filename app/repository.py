from datetime import UTC, datetime
from functools import cached_property
from typing import Protocol
from uuid import uuid4

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.config import Settings
from app.models import (
    Comment,
    CommentCreate,
    CommentPage,
    CommentUpdate,
    Post,
    PostCreate,
    PostPage,
    PostUpdate,
)


class PostNotFound(Exception):
    pass


class NotPostAuthor(Exception):
    pass


class CommentNotFound(Exception):
    pass


class NotCommentAuthor(Exception):
    pass


class PostsRepository(Protocol):
    def create(self, author_id: str, data: PostCreate) -> Post: ...
    def get(self, post_id: str) -> Post: ...
    def list(self, limit: int, cursor: str | None, author_id: str | None = None) -> PostPage: ...
    def update(self, post_id: str, author_id: str, data: PostUpdate) -> Post: ...
    def delete(self, post_id: str, author_id: str) -> None: ...
    def create_comment(self, post_id: str, author_id: str, data: CommentCreate) -> Comment: ...
    def list_comments(self, post_id: str, limit: int, cursor: str | None) -> CommentPage: ...
    def update_comment(
        self, post_id: str, comment_id: str, author_id: str, data: CommentUpdate
    ) -> Comment: ...
    def delete_comment(self, post_id: str, comment_id: str, author_id: str) -> None: ...


class FirestorePosts:
    def __init__(self, settings: Settings):
        self.settings = settings

    @cached_property
    def client(self):
        return firestore.Client(
            project=self.settings.google_cloud_project,
            database=self.settings.firestore_database,
        )

    @property
    def collection(self):
        return self.client.collection(self.settings.posts_collection)

    @staticmethod
    def _post(snapshot) -> Post:
        if not snapshot.exists:
            raise PostNotFound
        return Post(id=snapshot.id, **snapshot.to_dict())

    @staticmethod
    def _comment(snapshot) -> Comment:
        if not snapshot.exists:
            raise CommentNotFound
        return Comment(id=snapshot.id, **snapshot.to_dict())

    def _comments(self, post_id: str):
        return self.collection.document(post_id).collection("comments")

    def create(self, author_id: str, data: PostCreate) -> Post:
        now = datetime.now(UTC)
        post = Post(
            id=uuid4().hex,
            author_id=author_id,
            created_at=now,
            updated_at=now,
            **data.model_dump(),
        )
        self.collection.document(post.id).create(post.model_dump(exclude={"id"}))
        return post

    def get(self, post_id: str) -> Post:
        return self._post(self.collection.document(post_id).get())

    def list(self, limit: int, cursor: str | None, author_id: str | None = None) -> PostPage:
        query = self.collection.order_by("__name__")
        if author_id is not None:
            query = query.where(filter=FieldFilter("author_id", "==", author_id))
        if cursor:
            # A document-reference cursor remains usable after that post is deleted.
            query = query.start_after({"__name__": self.collection.document(cursor)})
        posts = [self._post(doc) for doc in query.limit(limit + 1).stream()]
        return PostPage(
            items=posts[:limit],
            next_cursor=posts[limit - 1].id if len(posts) > limit else None,
        )

    def update(self, post_id: str, author_id: str, data: PostUpdate) -> Post:
        reference = self.collection.document(post_id)

        @firestore.transactional
        def change(transaction):
            post = self._post(reference.get(transaction=transaction))
            if post.author_id != author_id:
                raise NotPostAuthor
            changes = data.model_dump(exclude_unset=True)
            changes["updated_at"] = datetime.now(UTC)
            transaction.update(reference, changes)
            return post.model_copy(update=changes)

        return change(self.client.transaction())

    def delete(self, post_id: str, author_id: str) -> None:
        reference = self.collection.document(post_id)

        @firestore.transactional
        def remove(transaction):
            post = self._post(reference.get(transaction=transaction))
            if post.author_id != author_id:
                raise NotPostAuthor
            transaction.delete(reference)

        remove(self.client.transaction())

    def create_comment(self, post_id: str, author_id: str, data: CommentCreate) -> Comment:
        post_reference = self.collection.document(post_id)
        comment_reference = self._comments(post_id).document(uuid4().hex)
        now = datetime.now(UTC)
        comment = Comment(
            id=comment_reference.id,
            author_id=author_id,
            created_at=now,
            updated_at=now,
            **data.model_dump(),
        )

        @firestore.transactional
        def add(transaction):
            self._post(post_reference.get(transaction=transaction))
            transaction.create(comment_reference, comment.model_dump(exclude={"id"}))

        add(self.client.transaction())
        return comment

    def list_comments(self, post_id: str, limit: int, cursor: str | None) -> CommentPage:
        self.get(post_id)
        collection = self._comments(post_id)
        query = collection.order_by("__name__")
        if cursor:
            query = query.start_after({"__name__": collection.document(cursor)})
        comments = [self._comment(doc) for doc in query.limit(limit + 1).stream()]
        return CommentPage(
            items=comments[:limit],
            next_cursor=comments[limit - 1].id if len(comments) > limit else None,
        )

    def update_comment(
        self, post_id: str, comment_id: str, author_id: str, data: CommentUpdate
    ) -> Comment:
        post_reference = self.collection.document(post_id)
        reference = self._comments(post_id).document(comment_id)

        @firestore.transactional
        def change(transaction):
            self._post(post_reference.get(transaction=transaction))
            comment = self._comment(reference.get(transaction=transaction))
            if comment.author_id != author_id:
                raise NotCommentAuthor
            changes = {"body": data.body, "updated_at": datetime.now(UTC)}
            transaction.update(reference, changes)
            return comment.model_copy(update=changes)

        return change(self.client.transaction())

    def delete_comment(self, post_id: str, comment_id: str, author_id: str) -> None:
        post_reference = self.collection.document(post_id)
        reference = self._comments(post_id).document(comment_id)

        @firestore.transactional
        def remove(transaction):
            self._post(post_reference.get(transaction=transaction))
            comment = self._comment(reference.get(transaction=transaction))
            if comment.author_id != author_id:
                raise NotCommentAuthor
            transaction.delete(reference)

        remove(self.client.transaction())
