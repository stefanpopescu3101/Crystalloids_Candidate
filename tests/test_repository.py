from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.models import PostUpdate
from app.repository import FirestorePosts, NotPostAuthor, PostNotFound


@pytest.fixture
def storage():
    repo = FirestorePosts(
        Settings(
            _env_file=None,
            allow_gcloud_dev_tokens=False,
            google_cloud_project="test",
            google_oauth_client_id="test",
        )
    )
    repo.client = MagicMock()
    reference = repo.collection.document.return_value
    snapshot = reference.get.return_value
    snapshot.exists = True
    snapshot.id = "a" * 32
    now = datetime.now(UTC)
    snapshot.to_dict.return_value = {
        "author_id": "alice",
        "subject": "Hello",
        "body": "World",
        "created_at": now,
        "updated_at": now,
    }
    # Exercise repository logic while leaving SDK transaction retries to Google.
    with patch("app.repository.firestore.transactional", side_effect=lambda fn: fn):
        yield repo, reference


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_storage_rejects_non_author_before_writing(storage, operation):
    repo, reference = storage
    with pytest.raises(NotPostAuthor):
        if operation == "update":
            repo.update("a" * 32, "bob", PostUpdate(body="stolen"))
        else:
            repo.delete("a" * 32, "bob")
    transaction = repo.client.transaction.return_value
    reference.get.assert_called_once_with(transaction=transaction)
    transaction.update.assert_not_called()
    transaction.delete.assert_not_called()


def test_update_preserves_author_and_creation_date(storage):
    repo, reference = storage
    result = repo.update("a" * 32, "alice", PostUpdate(body="Edited"))
    transaction = repo.client.transaction.return_value
    changes = transaction.update.call_args.args[1]
    assert set(changes) == {"body", "updated_at"}
    assert result.body == "Edited"
    assert result.author_id == "alice"
    assert result.created_at == reference.get.return_value.to_dict.return_value["created_at"]


def test_delete_missing_post_does_not_write(storage):
    repo, reference = storage
    reference.get.return_value.exists = False
    with pytest.raises(PostNotFound):
        repo.delete("a" * 32, "alice")
    repo.client.transaction.return_value.delete.assert_not_called()
