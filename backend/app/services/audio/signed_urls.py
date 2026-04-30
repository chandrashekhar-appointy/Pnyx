"""
HMAC-signed URLs for serving locally-stored recordings.

GCP returns signed URLs natively; for local-storage deployments we mint our
own short-lived HMAC tokens so an <audio src=...> tag can play without sending
an Authorization header.

Token format: base64url( meeting_id | path | expiry_unix ) . hex_hmac
The HMAC key is AUDIO_SIGNING_KEY (defaults to MASTER_KEY for convenience).
"""

import base64
import hashlib
import hmac
import os
import time
from typing import Optional, Tuple


def _signing_key() -> bytes:
    key = os.getenv("AUDIO_SIGNING_KEY") or os.getenv("MASTER_KEY") or ""
    if not key:
        # Fail closed — refuse to mint URLs we can't verify.
        raise RuntimeError(
            "AUDIO_SIGNING_KEY or MASTER_KEY must be set to sign audio URLs"
        )
    return key.encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def mint_signed_token(meeting_id: str, path: str, ttl_seconds: int = 3600) -> str:
    """Return a token usable in /audio/signed/{token} URLs."""
    expires_at = int(time.time()) + max(60, ttl_seconds)
    body = f"{meeting_id}|{path}|{expires_at}".encode("utf-8")
    sig = hmac.new(_signing_key(), body, hashlib.sha256).digest()
    return f"{_b64encode(body)}.{_b64encode(sig)}"


def verify_signed_token(token: str) -> Optional[Tuple[str, str]]:
    """Return (meeting_id, path) if token is valid and unexpired, else None."""
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64decode(body_b64)
        provided_sig = _b64decode(sig_b64)
    except Exception:
        return None

    expected_sig = hmac.new(_signing_key(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_sig, provided_sig):
        return None

    try:
        meeting_id, path, expires_str = body.decode("utf-8").split("|", 2)
        expires_at = int(expires_str)
    except Exception:
        return None

    if time.time() > expires_at:
        return None

    # Reject path traversal even though signed — defense in depth.
    if ".." in path or path.startswith("/") or "\\" in path:
        return None

    return meeting_id, path
