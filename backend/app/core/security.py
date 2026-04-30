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

# For Google, we fetch public keys from their endpoint
GOOGLE_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"

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


async def verify_google_token(token: str) -> Dict[str, Any]:
    """Verify the Google JWT token"""
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
