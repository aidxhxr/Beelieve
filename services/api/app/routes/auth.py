"""Authentication endpoints: register, login, current user."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow

from app.config import get_settings
from app.db import get_db
from app.models import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.security import create_access_token, get_current_user, hash_password, verify_password

logger = logging.getLogger("beelieve.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: UserOut) -> TokenResponse:
    settings = get_settings()
    token = create_access_token(subject=str(user.id), role=user.role)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expires_min * 60,
        user=user,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> TokenResponse:
    """Create a beekeeper account and return a session token."""
    try:
        cur = await conn.execute(
            """
            INSERT INTO users (email, password_hash, full_name, locale)
            VALUES (%(email)s, %(password_hash)s, %(full_name)s, %(locale)s)
            RETURNING id, email, full_name, locale, role, created_at
            """,
            {
                "email": payload.email.lower(),
                "password_hash": hash_password(payload.password),
                "full_name": payload.full_name,
                "locale": payload.locale,
            },
        )
        row = await cur.fetchone()
    except UniqueViolation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        ) from None
    assert row is not None  # RETURNING always yields one row on success
    user = UserOut.model_validate(row)
    logger.info("user registered", extra={"user_id": str(user.id)})
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    conn: AsyncConnection[DictRow] = Depends(get_db),
) -> TokenResponse:
    """Verify email + password against the users table and issue a JWT."""
    cur = await conn.execute(
        """
        SELECT id, email, password_hash, full_name, locale, role, created_at
        FROM users
        WHERE email = %(email)s
        """,
        {"email": payload.email.lower()},
    )
    row = await cur.fetchone()
    if row is None or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = UserOut.model_validate(row)
    logger.info("user logged in", extra={"user_id": str(user.id)})
    return _token_response(user)


@router.get("/me", response_model=UserOut)
async def me(user: UserOut = Depends(get_current_user)) -> UserOut:
    """Return the authenticated user's profile."""
    return user
