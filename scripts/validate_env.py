#!/usr/bin/env python3
"""
Pre-deploy environment validator.

Usage:
    python scripts/validate_env.py --env backend/.env.prod
    python scripts/validate_env.py --env backend/.env --skip-prod-checks

Exits 0 on PASS, 1 on FAIL. Designed to run in CI before a deploy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# ----------------------------------------------------------------------------
# Required & forbidden variables
# ----------------------------------------------------------------------------

# Vars that must be set and non-empty for ANY deployment.
ALWAYS_REQUIRED = [
    "DATABASE_URL",
    "GOOGLE_CLIENT_ID",
    "MASTER_KEY",
]

# Additional vars required when ENVIRONMENT=production.
PROD_REQUIRED = [
    "ENVIRONMENT",
    "CORS_ALLOWED_ORIGINS",
    "GROQ_API_KEY",
    "ELEVENLABS_API_KEY",
    "GEMINI_API_KEY",
    "STORAGE_TYPE",
    "ADMIN_EMAILS",
    "CALENDAR_OAUTH_REDIRECT_URI",
    "CALENDAR_EMAIL_START_MEETING_URL",
    "CALENDAR_OAUTH_FRONTEND_SETTINGS_URL",
]

# Vars that, if present, must be a URL — checked for double-slashes etc.
URL_VARS = [
    "DATABASE_URL",
    "CALENDAR_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_FRONTEND_SETTINGS_URL",
    "CALENDAR_EMAIL_START_MEETING_URL",
    "RECALL_WEBHOOK_URL",
    "AUDIO_MERGE_SERVICE_URL",
    "STREAMING_ALERT_WEBHOOK_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
]

# Literal strings that mean "unset". Catch RAZORPAY_KEY_ID=null etc.
SENTINEL_UNSET_VALUES = {"null", "None", "undefined", "TODO", "CHANGEME", "xxx", "<set me>"}

# Forbidden in production.
LOCALHOST_PATTERNS = [r"localhost", r"127\.0\.0\.1", r"\.local(?!host)"]


# ----------------------------------------------------------------------------
# .env parsing — accept the leading-whitespace style used in this repo
# ----------------------------------------------------------------------------

ENV_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")


def parse_env(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = ENV_LINE.match(raw)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # Strip trailing inline comment (only if quoted/safe)
        if "#" in val and not val.startswith(("'", '"')):
            val = val.split("#", 1)[0].strip()
        # Strip outer quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------


def check_required(env: Dict[str, str], names: List[str], where: str) -> List[str]:
    errors = []
    for name in names:
        v = env.get(name, "")
        if not v:
            errors.append(f"{where}: {name} is missing or empty")
        elif v.strip() in SENTINEL_UNSET_VALUES or v.strip().lower() == "null":
            errors.append(f"{where}: {name} is set to a sentinel value ('{v}')")
    return errors


def check_urls(env: Dict[str, str]) -> List[str]:
    errors = []
    for name in URL_VARS:
        v = env.get(name)
        if not v:
            continue
        # Strip protocol then check for accidental // in path
        m = re.match(r"^([a-zA-Z][a-zA-Z+\-.]*://)([^?#]*)", v)
        if m:
            after_proto = m.group(2)
            if "//" in after_proto:
                errors.append(
                    f"{name}: contains a double slash in the path: {v!r}"
                )
        else:
            if not v.startswith("/"):
                errors.append(f"{name}: not a valid URL: {v!r}")
    return errors


def check_no_localhost_in_prod(env: Dict[str, str]) -> List[str]:
    if env.get("ENVIRONMENT", "").lower() != "production":
        return []
    errors = []
    for name, value in env.items():
        if name in {"REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"}:
            # These are container-internal, localhost OK
            continue
        for pat in LOCALHOST_PATTERNS:
            if re.search(pat, value, re.IGNORECASE):
                errors.append(f"{name}: production env contains forbidden host: {value!r}")
                break
    # CORS must not include localhost
    cors = env.get("CORS_ALLOWED_ORIGINS", "")
    if "localhost" in cors:
        errors.append(f"CORS_ALLOWED_ORIGINS includes localhost: {cors!r}")
    return errors


def check_gcp_credentials(env: Dict[str, str], env_dir: Path) -> List[str]:
    if env.get("STORAGE_TYPE", "").lower() != "gcp":
        return []
    cred_path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not cred_path:
        return ["STORAGE_TYPE=gcp but GOOGLE_APPLICATION_CREDENTIALS not set"]
    candidate = Path(cred_path)
    if not candidate.is_absolute():
        candidate = env_dir / candidate
    if not candidate.is_file():
        return [f"GCP credentials file not found at {candidate}"]
    try:
        with candidate.open() as f:
            data = json.load(f)
        if data.get("type") != "service_account":
            return [f"GCP credentials file is not a service_account: {candidate}"]
        if not data.get("private_key"):
            return [f"GCP credentials file missing private_key: {candidate}"]
    except json.JSONDecodeError as e:
        return [f"GCP credentials file is not valid JSON: {e}"]
    return []


def check_secrets_strength(env: Dict[str, str]) -> List[str]:
    errors = []
    master_key = env.get("MASTER_KEY", "")
    if master_key and len(master_key) < 32:
        errors.append(f"MASTER_KEY too short ({len(master_key)} chars, need >=32)")
    return errors


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a .env file before deploy.")
    parser.add_argument("--env", default="backend/.env.prod", help="Path to .env file")
    parser.add_argument(
        "--skip-prod-checks",
        action="store_true",
        help="Skip production-specific requirement checks",
    )
    args = parser.parse_args()

    env_path = Path(args.env).resolve()
    if not env_path.is_file():
        print(f"FAIL: env file not found: {env_path}")
        return 1

    env = parse_env(env_path)
    env_dir = env_path.parent

    errors: List[str] = []
    errors += check_required(env, ALWAYS_REQUIRED, "always required")
    if not args.skip_prod_checks and env.get("ENVIRONMENT", "").lower() == "production":
        errors += check_required(env, PROD_REQUIRED, "production required")
    errors += check_urls(env)
    errors += check_no_localhost_in_prod(env)
    errors += check_gcp_credentials(env, env_dir)
    errors += check_secrets_strength(env)

    print(f"Validating: {env_path}")
    print(f"Variables loaded: {len(env)}")
    print(f"Environment: {env.get('ENVIRONMENT', '<unset>')}")
    print(f"Storage: {env.get('STORAGE_TYPE', '<unset>')}")
    print()

    if errors:
        print(f"FAIL: {len(errors)} issue(s)")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("PASS: env file looks deploy-ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
