"""Minimal RS256 JWT helpers so security tests can exercise real token decode.

Real Google JWKS is mocked at network layer (see external_apis.py); these
helpers produce tokens that verify against the mock JWKS.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from jose import jwt

TEST_KID = "test-kid-001"


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class JwtTestKey:
    """Holds a freshly generated RSA keypair plus a JWKS payload that
    verify_google_token() can fetch from the mocked JWKS endpoint."""

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        self.public_key = self.private_key.public_key()
        nums = self.public_key.public_numbers()
        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": TEST_KID,
                    "n": _b64url_uint(nums.n),
                    "e": _b64url_uint(nums.e),
                }
            ]
        }
        self._pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

    def issue(
        self,
        *,
        email: str = "test@appointy.com",
        name: str = "Test User",
        audience: str | None = None,
        issuer: str = "https://accounts.google.com",
        expires_in: int = 3600,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": issuer,
            "sub": email,
            "email": email,
            "email_verified": True,
            "name": name,
            "iat": now,
            "exp": now + expires_in,
        }
        if audience is not None:
            payload["aud"] = audience
        if extra:
            payload.update(extra)
        return jwt.encode(
            payload,
            self._pem.decode("ascii"),
            algorithm="RS256",
            headers={"kid": TEST_KID, "alg": "RS256", "typ": "JWT"},
        )

    def issue_tampered(self, **kwargs: Any) -> str:
        """Return a token with a corrupted signature (last 8 chars scrambled)."""
        token = self.issue(**kwargs)
        head, payload, sig = token.split(".")
        tampered_sig = sig[:-8] + "AAAAAAAA"
        return f"{head}.{payload}.{tampered_sig}"


_singleton: JwtTestKey | None = None


def get_test_key() -> JwtTestKey:
    global _singleton
    if _singleton is None:
        _singleton = JwtTestKey()
    return _singleton


def encoded_jwks_json() -> str:
    return json.dumps(get_test_key().jwks)
