import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

MASTER_KEY = os.getenv("MASTER_KEY")

if not MASTER_KEY:
    # Fallback to a fixed key if not provided (NOT recommended for production)
    # In this app, we should ensure it's provided.
    # raise ValueError("MASTER_KEY not found in environment variables")
    pass  # Allow for now if not set, but handle in functions

# Initialize Fernet lazily or handle error if key missing
try:
    fernet = Fernet(MASTER_KEY.encode()) if MASTER_KEY else None
except Exception:
    fernet = None


def encrypt_key(plain_text: str) -> str:
    """Encrypt a plain text API key."""
    if not plain_text:
        return ""
    if not fernet:
        raise ValueError("MASTER_KEY is not configured. Cannot encrypt secrets.")
    return fernet.encrypt(plain_text.encode()).decode()


def decrypt_key(encrypted_text: str) -> str:
    """Decrypt an encrypted API key."""
    if not encrypted_text:
        return ""
    if not fernet:
        raise ValueError("MASTER_KEY is not configured. Cannot decrypt secrets.")
    try:
        return fernet.decrypt(encrypted_text.encode()).decode()
    except Exception as e:
        # If decryption fails (e.g. key changed), raise explicit error
        raise ValueError(
            f"Failed to decrypt secret. The MASTER_KEY might have changed: {e}"
        )
