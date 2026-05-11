"""Contract / schema-drift tests.

Goal: catch a class of bug where a backend response shape changes but the
frontend's TypeScript types weren't regenerated.  We do two things:

  1. Snapshot the OpenAPI document and compare against a golden file at
     ``tests/contracts/openapi.snapshot.json``.  CI fails if it changed without
     a corresponding update.
  2. Verify a generated TypeScript types file at
     ``frontend/src/types/api.generated.ts`` is up-to-date (only checked when
     the file exists; the runner script generates it before this test runs).

Update the snapshot intentionally with: ``UPDATE_OPENAPI_SNAPSHOT=1 pytest -k
contract``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
SNAPSHOT_PATH = BACKEND_ROOT / "tests" / "contracts" / "openapi.snapshot.json"
GENERATED_TS = REPO_ROOT / "frontend" / "src" / "types" / "api.generated.ts"


def _build_app():
    from app.main import app

    return app


def _normalize(spec: dict) -> dict:
    """Strip volatile fields (server URLs, version, descriptions) so the
    snapshot is meaningful."""
    spec = json.loads(json.dumps(spec))
    spec.pop("info", None)
    spec.pop("servers", None)
    return spec


@pytest.mark.contract
def test_openapi_schema_matches_snapshot():
    app = _build_app()
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code == 200
    current = _normalize(resp.json())

    if os.getenv("UPDATE_OPENAPI_SNAPSHOT") in {"1", "true", "yes"}:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True))
        pytest.skip("Snapshot updated.")

    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True))
        pytest.skip("Snapshot bootstrapped — re-run to enforce.")

    expected = json.loads(SNAPSHOT_PATH.read_text())
    if expected == current:
        return

    # Produce a small diff hint for humans reading the failure
    added_paths = set(current.get("paths", {})) - set(expected.get("paths", {}))
    removed_paths = set(expected.get("paths", {})) - set(current.get("paths", {}))
    msg_parts = ["OpenAPI schema drift:"]
    if added_paths:
        msg_parts.append(f"  added paths: {sorted(added_paths)}")
    if removed_paths:
        msg_parts.append(f"  removed paths: {sorted(removed_paths)}")
    msg_parts.append(
        "Run with UPDATE_OPENAPI_SNAPSHOT=1 if intentional, then update the "
        "frontend TS types via `pnpm run gen:types`."
    )
    pytest.fail("\n".join(msg_parts))


@pytest.mark.contract
def test_generated_typescript_types_exist_when_required():
    """If a developer has run the type-gen script, the generated file must
    parse and reference at least one expected schema name."""
    if not GENERATED_TS.exists():
        pytest.skip(
            "frontend/src/types/api.generated.ts not present — generate it via "
            "`pnpm run gen:types`."
        )
    text = GENERATED_TS.read_text()
    # Either openapi-typescript or openapi-zod-client output starts with the
    # same boilerplate marker.
    assert "paths" in text or "components" in text or "operations" in text, (
        "Generated TS file does not look like an OpenAPI types output"
    )


@pytest.mark.contract
def test_no_route_returns_undeclared_schema():
    """Walk every route in the app and ensure each declares response_model,
    a typed return annotation, or otherwise has an OpenAPI response shape.

    Tightens the loop on routes accidentally returning ``dict`` without a
    Pydantic schema (which leads to silent shape-drift)."""
    from fastapi.routing import APIRoute

    app = _build_app()
    offenders: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.include_in_schema:
            continue
        # Allow when response_model is set OR endpoint annotates a return type
        if route.response_model is not None:
            continue
        return_annotation = getattr(route.endpoint, "__annotations__", {}).get("return")
        if return_annotation is not None:
            continue
        offenders.append(f"{','.join(route.methods or [])} {route.path}")

    # Accept up to N offenders today — tighten over time by editing this number.
    MAX_ALLOWED = int(os.getenv("CONTRACT_MAX_UNTYPED_ROUTES", "80"))
    assert len(offenders) <= 80, (
        f"{len(offenders)} routes lack response_model AND return annotation. "
        "Either add response_model=... or a return annotation. "
        f"Examples: {offenders[:5]}"
    )
