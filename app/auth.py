from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

bearer = HTTPBearer(
    auto_error=False,
    description="Google ID token (use gcloud auth print-identity-token for CLI development)",
)


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AuthenticatedUser:
    unauthorized = HTTPException(
        status_code=401,
        detail="A valid Google ID token is required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        settings = request.app.state.settings
        # CLI development tokens are not scoped to our app. This explicit opt-in
        # skips only audience validation; Google signature, time and issuer checks remain.
        audience = None if settings.allow_gcloud_dev_tokens else settings.google_oauth_client_id
        claims = id_token.verify_oauth2_token(
            credentials.credentials,
            GoogleRequest(),
            audience=audience,
        )
    except TransportError as exc:
        raise HTTPException(503, "Google token verification temporarily unavailable") from exc
    except (ValueError, GoogleAuthError) as exc:
        raise unauthorized from exc
    subject = claims.get("sub")
    email = claims.get("email")
    if not isinstance(subject, str) or not subject or not isinstance(email, str) or not email:
        raise unauthorized
    if claims.get("email_verified") is not True:
        raise unauthorized
    return AuthenticatedUser(id=subject, email=email)
