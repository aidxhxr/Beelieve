"""Password hashing, JWT issuance/validation and the auth dependency."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from psycopg import AsyncConnection
from psycopg.rows import DictRow

from app.config import get_settings
from app.db import get_db
from app.models import UserOut

ALGORITHM = "HS256"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

bearer_scheme = HTTPBearer(auto_error=False)


class TokenError(Exception):
    """Raised when a JWT is missing, malformed, expired or forged."""


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verification of a plaintext password against a bcrypt hash."""
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def create_access_token(
    *,
    subject: str,
    role: str,
    secret: str | None = None,
    expires_min: int | None = None,
) -> str:
    """Issue an HS256 JWT for `subject` (user id) with a `role` claim.

    `secret` and `expires_min` default to JWT_SECRET / JWT_EXPIRES_MIN from the
    environment; they are injectable for tests.
    """
    settings = get_settings()
    secret = secret if secret is not None else settings.jwt_secret
    expires_min = expires_min if expires_min is not None else settings.jwt_expires_min
    now = datetime.now(timezone.utc)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=expires_min),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, secret: str | None = None) -> dict[str, Any]:
    """Validate a JWT's signature and expiry; return its claims.

    Raises TokenError on any validation failure.
    """
    settings = get_settings()
    secret = secret if secret is not None else settings.jwt_secret
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    return claims


def _credentials_exception(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> UserOut:
    """Resolve the Bearer token to a live user row. 401 on any failure."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _credentials_exception()
    try:
        claims = decode_access_token(credentials.credentials)
        user_id = UUID(str(claims["sub"]))
    except (TokenError, KeyError, ValueError) as exc:
        raise _credentials_exception("Invalid or expired token") from exc

    cur = await conn.execute(
        """
        SELECT id, email, full_name, locale, role, created_at
        FROM users
        WHERE id = %(user_id)s
        """,
        {"user_id": user_id},
    )
    row = await cur.fetchone()
    if row is None:
        raise _credentials_exception("Unknown user")
    return UserOut.model_validate(row)
