import json
from functools import cached_property
from typing import Protocol

from google.cloud import pubsub_v1

from app.config import Settings
from app.models import Comment


class CommentEvents(Protocol):
    def publish_created(self, post_id: str, comment: Comment, author_email: str) -> None: ...


class PubSubCommentEvents:
    def __init__(self, settings: Settings):
        if settings.comment_events_topic is None:
            raise ValueError("COMMENT_EVENTS_TOPIC must be configured to publish comment events")
        self.settings = settings

    @cached_property
    def publisher(self):
        return pubsub_v1.PublisherClient()

    @property
    def topic_path(self) -> str:
        return self.publisher.topic_path(
            self.settings.google_cloud_project, self.settings.comment_events_topic
        )

    def publish_created(self, post_id: str, comment: Comment, author_email: str) -> None:
        event = {
            "event_id": comment.id,
            "event_type": "comment.created",
            "post_id": post_id,
            "comment_id": comment.id,
            "author_email": author_email,
            "body": comment.body,
            "created_at": comment.created_at.isoformat(),
        }
        # Broker acknowledgement is awaited so publishing failures reach the caller.
        self.publisher.publish(self.topic_path, json.dumps(event).encode("utf-8")).result(
            timeout=10
        )
