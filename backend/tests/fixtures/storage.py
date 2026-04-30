"""Filesystem-backed StorageService stub used by tests.

Avoids any GCP calls — STORAGE_TYPE=local is enforced through env, and the
storage root is set to a per-test ``tmp_path``.
"""

from __future__ import annotations

import os
from pathlib import Path


def configure_local_storage(tmp_root: Path) -> None:
    tmp_root = Path(tmp_root)
    tmp_root.mkdir(parents=True, exist_ok=True)
    os.environ["STORAGE_TYPE"] = "local"
    os.environ["LOCAL_RECORDINGS_DIR"] = str(tmp_root)
    os.environ.setdefault("AUDIO_SIGNING_KEY", "dGVzdC1hdWRpby1zaWduaW5nLWtleS0xMjM=")
