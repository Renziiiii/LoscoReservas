"""Simple JWT authentication (assumption A1).

Users are seeded from the LOSCO_USERS env var ("username:password,..." pairs).
Tokens are HS256 JWTs; the password check is a plain comparison because
credentials already live in the environment — the improvement path (hashed
credentials in the DB) is documented in the feature doc.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenBody(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserBody(BaseModel):
    username: str


def authenticate(settings, username: str, password: str) -> bool:
    expected = settings.user_map.get(username)
    return expected is not None and expected == password


def create_access_token(settings, username: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def get_current_user(
    request: Request, token: str | None = Depends(oauth2_scheme)
) -> str:
    """Dependency: the authenticated username, or 401. Accepts the standard
    Bearer header (API clients) or the `losco_session` cookie (browser)."""
    if token is None:
        token = request.cookies.get("losco_session")
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(token, request.app.state.settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    username = payload.get("sub")
    if username not in request.app.state.settings.user_map:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unknown user",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


@router.post("/login", response_model=TokenBody)
def login(request: Request, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """OAuth2 password flow login. Returns a bearer access token."""
    settings = request.app.state.settings
    if not authenticate(settings, form.username, form.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad credentials")
    return TokenBody(access_token=create_access_token(settings, form.username))


@router.get("/me", response_model=UserBody)
def me(username: str = Depends(get_current_user)):
    """Whoami: verifies a token is still valid."""
    return UserBody(username=username)
