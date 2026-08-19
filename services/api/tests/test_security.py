"""Pure-logic tests for password hashing and JWT round-trips (no DB)."""

from __future__ import annotations

import time

import pytest

from app.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

SECRET = "unit-test-secret"


class TestPasswordHashing:
    def test_hash_and_verify_round_trip(self) -> None:
        hashed = hash_password("hunter2hunter2")
        assert hashed != "hunter2hunter2"
        assert hashed.startswith("$2")  # bcrypt
        assert verify_password("hunter2hunter2", hashed)

    def test_wrong_password_rejected(self) -> None:
        hashed = hash_password("correct-horse-battery")
        assert not verify_password("incorrect-horse", hashed)

    def test_hashes_are_salted(self) -> None:
        assert hash_password("same-password") != hash_password("same-password")

    def test_garbage_hash_rejected(self) -> None:
        assert not verify_password("anything", "not-a-bcrypt-hash")


class TestJwtRoundTrip:
    def test_round_trip_preserves_claims(self) -> None:
        token = create_access_token(
            subject="8d7f3c1e-0000-4000-8000-000000000042",
            role="beekeeper",
            secret=SECRET,
            expires_min=5,
        )
        claims = decode_access_token(token, secret=SECRET)
        assert claims["sub"] == "8d7f3c1e-0000-4000-8000-000000000042"
        assert claims["role"] == "beekeeper"
        assert claims["exp"] > time.time()
        assert claims["iat"] <= time.time() + 1

    def test_admin_role_claim(self) -> None:
        token = create_access_token(subject="u1", role="admin", secret=SECRET, expires_min=5)
        assert decode_access_token(token, secret=SECRET)["role"] == "admin"

    def test_wrong_secret_rejected(self) -> None:
        token = create_access_token(subject="u1", role="beekeeper", secret=SECRET, expires_min=5)
        with pytest.raises(TokenError):
            decode_access_token(token, secret="a-different-secret")

    def test_expired_token_rejected(self) -> None:
        token = create_access_token(subject="u1", role="beekeeper", secret=SECRET, expires_min=-1)
        with pytest.raises(TokenError):
            decode_access_token(token, secret=SECRET)

    def test_malformed_token_rejected(self) -> None:
        with pytest.raises(TokenError):
            decode_access_token("not.a.jwt", secret=SECRET)

    def test_tampered_payload_rejected(self) -> None:
        token = create_access_token(subject="u1", role="beekeeper", secret=SECRET, expires_min=5)
        header, payload, signature = token.split(".")
        tampered = f"{header}.{payload[:-2]}AA.{signature}"
        with pytest.raises(TokenError):
            decode_access_token(tampered, secret=SECRET)
