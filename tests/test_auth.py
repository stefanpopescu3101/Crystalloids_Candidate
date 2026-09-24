import time
from typing import Annotated
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from google.auth import crypt, jwt
from pydantic import ValidationError

from app.auth import AuthenticatedUser, current_user
from app.config import Settings


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def auth_client(signing_key):
    app = FastAPI()
    app.state.settings = Settings(
        _env_file=None,
        google_cloud_project="test",
        allow_gcloud_dev_tokens=True,
        google_oauth_client_id=None,
    )

    @app.get("/whoami")
    def whoami(user: Annotated[AuthenticatedUser, Depends(current_user)]):
        return {"author_id": user.id, "email": user.email}

    public_key = (
        signing_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    # Replace only Google's certificate download. Use the real JWT verifier.
    with (
        patch("google.oauth2.id_token._fetch_certs", return_value={"test-key": public_key}),
        TestClient(app) as client,
    ):
        yield client


def signed_token(key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "alice",
        "email": "alice@example.test",
        "email_verified": True,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(crypt.RSASigner(key, key_id="test-key"), claims).decode()


def call(client, token):
    return client.get("/whoami", headers={"Authorization": f"Bearer {token}"})


def test_cli_token_without_audience(auth_client, signing_key):
    response = call(auth_client, signed_token(signing_key))
    assert response.status_code == 200
    assert response.json() == {"author_id": "alice", "email": "alice@example.test"}


def test_cli_audience_is_not_app_audience(auth_client, signing_key):
    assert call(auth_client, signed_token(signing_key, aud="gcloud-client")).status_code == 200


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"iat": 9999999999},
        {"iss": "https://attacker.example"},
        {"sub": ""},
        {"sub": None},
        {"email": ""},
        {"email_verified": False},
    ],
)
def test_invalid_claims_rejected(auth_client, signing_key, claims):
    assert call(auth_client, signed_token(signing_key, **claims)).status_code == 401


def test_wrong_signature_rejected(auth_client):
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert call(auth_client, signed_token(wrong_key)).status_code == 401


def test_access_token_rejected(auth_client):
    assert call(auth_client, "ya29.not-an-id-token").status_code == 401


def test_strict_mode_checks_audience(auth_client, signing_key):
    auth_client.app.state.settings = Settings(
        _env_file=None,
        google_cloud_project="test",
        allow_gcloud_dev_tokens=False,
        google_oauth_client_id="our-app",
    )
    assert call(auth_client, signed_token(signing_key, aud="other-app")).status_code == 401
    assert call(auth_client, signed_token(signing_key, aud="our-app")).status_code == 200


def test_auth_mode_must_be_explicit():
    with pytest.raises(ValidationError, match="explicitly enable"):
        Settings(
            _env_file=None,
            google_cloud_project="test",
            allow_gcloud_dev_tokens=False,
            google_oauth_client_id=None,
        )


def test_conflicting_auth_settings_rejected():
    with pytest.raises(ValidationError, match="Unset GOOGLE_OAUTH_CLIENT_ID"):
        Settings(
            _env_file=None,
            google_cloud_project="test",
            allow_gcloud_dev_tokens=True,
            google_oauth_client_id="our-app",
        )
