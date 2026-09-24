from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str = Field(min_length=1)
    google_oauth_client_id: str | None = Field(default=None, min_length=1)
    allow_gcloud_dev_tokens: bool = False
    firestore_database: str = "(default)"
    posts_collection: str = Field(default="posts", pattern=r"^[A-Za-z0-9_-]+$")
    comment_events_topic: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_auth_configuration(self):
        if not self.allow_gcloud_dev_tokens and not self.google_oauth_client_id:
            raise ValueError(
                "Set GOOGLE_OAUTH_CLIENT_ID or explicitly enable ALLOW_GCLOUD_DEV_TOKENS"
            )
        if self.allow_gcloud_dev_tokens and self.google_oauth_client_id:
            raise ValueError("Unset GOOGLE_OAUTH_CLIENT_ID when ALLOW_GCLOUD_DEV_TOKENS is enabled")
        return self
