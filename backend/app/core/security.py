import os
import httpx
import time
import asyncio
from fastapi import HTTPException, status
from jose import jwt, JWTError
from typing import Dict, Any
import logging

# Configure logger
logger = logging.getLogger(__name__)

# Environment variables
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")

# Opt-in allowlist of OAuth client IDs permitted to authenticate with an OPAQUE
# Google ACCESS token (as opposed to a signed ID token). This exists for the
# Chrome extension, which can only obtain an access token via
# chrome.identity.getAuthToken. Empty/unset = the access-token path is disabled
# (fail closed), so the default behaviour is unchanged.
EXTENSION_OAUTH_CLIENT_IDS = [
    c.strip()
    for c in os.getenv("EXTENSION_OAUTH_CLIENT_IDS", "").split(",")
    if c.strip()
]

# For Google, we fetch public keys from their endpoint
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

# Cache for Google Public Keys
_google_keys_cache = None
_google_keys_expiry = 0
CACHE_TTL = 3600  # 1 hour


async def get_google_public_keys() -> Dict[str, Any]:
    """Fetch Google's public keys for verifying JWTs (Cached with Retry)"""
    global _google_keys_cache, _google_keys_expiry

    current_time = time.time()
    if _google_keys_cache and current_time < _google_keys_expiry:
        return _google_keys_cache

    async with httpx.AsyncClient() as client:
        # Simple retry logic (3 attempts)
        for attempt in range(3):
            try:
                response = await client.get(GOOGLE_CERTS_URL, timeout=10.0)
                response.raise_for_status()
                keys = response.json()

                # Update cache
                _google_keys_cache = keys
                _google_keys_expiry = current_time + CACHE_TTL
                logger.debug("Refreshed Google public keys cache")
                return keys
            except Exception as e:
                logger.warning(
                    f"Failed to fetch Google keys (attempt {attempt + 1}/3): {e}"
                )
                if attempt == 2:
                    # If we have stale cache, use it as fallback rather than failing
                    if _google_keys_cache:
                        logger.warning("Using stale cache due to fetch failure")
                        return _google_keys_cache
                    raise e
                await asyncio.sleep(1)  # Wait before retry

    return {}  # Should not be reached


async def verify_google_access_token(token: str) -> Dict[str, Any]:
    """Validate an opaque Google ACCESS token via the tokeninfo endpoint.

    Only used as a fallback for clients that cannot present a signed ID token
    (the Chrome extension). The token's audience (`aud`) MUST be in the
    EXTENSION_OAUTH_CLIENT_IDS allowlist, and the email must be verified.
    Returns a payload dict shaped like the ID-token path ({email, name, ...}).
    """
    if not EXTENSION_OAUTH_CLIENT_IDS:
        # Access-token auth is not enabled on this server — fail closed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                GOOGLE_TOKENINFO_URL,
                params={"access_token": token},
                timeout=10.0,
            )
    except Exception as e:
        logger.error(f"tokeninfo request failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if resp.status_code != 200:
        # Google returns 400 for an invalid/expired access token.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    info = resp.json()
    aud = info.get("aud") or info.get("azp")
    if aud not in EXTENSION_OAUTH_CLIENT_IDS:
        logger.warning("Access token rejected: aud %r not in allowlist", aud)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token audience not allowed",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # tokeninfo returns string booleans ("true"/"false")
    email_verified = str(info.get("email_verified", "")).lower() == "true"
    email = info.get("email")
    if not email or not email_verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing a verified email",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "email": email,
        "name": info.get("name"),
        "picture": info.get("picture"),
        "aud": aud,
        "_auth_method": "access_token",
    }


async def verify_google_token(token: str) -> Dict[str, Any]:
    """Verify a Google token.

    Tries the signed ID-token (JWT) path first — used by the web frontend.
    Falls back to opaque access-token validation (used by the Chrome extension)
    only when an allowlist is configured.
    """
    try:
        # Get public keys
        jwks = await get_google_public_keys()

        # Verify and decode
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=GOOGLE_CLIENT_ID,
            options={
                "verify_at_hash": False,
            },
        )

        return payload
    except JWTError as e:
        error_msg = str(e)

        # Fallback: the token may be an opaque access token (Chrome extension)
        # rather than a JWT ID token. Only attempt this when explicitly enabled.
        if EXTENSION_OAUTH_CLIENT_IDS:
            try:
                return await verify_google_access_token(token)
            except HTTPException:
                pass  # fall through to the standard JWT error below

        logger.error(f"JWT Verification Error: {error_msg}")

        # provide more descriptive detail for common errors
        detail = "Invalid authentication credentials"
        if "exp" in error_msg.lower() or "expired" in error_msg.lower():
            detail = "Token expired. Please refresh your session."
        elif "aud" in error_msg.lower() or "audience" in error_msg.lower():
            detail = "Token audience mismatch. Check GOOGLE_CLIENT_ID."

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
