"""Phase 5 — deployment approval gate.

Hard rule: a release is NEVER self-marked `customer_traffic_ready: true`. The release
file always stays `false` (enforced by the builder and the release validator). Customer
traffic is authorized only by an **external operator decision**, expressed as a signed
deployment-approval record, AND only when the deployment gate passes.

`customer_traffic_ready: true` is therefore derivable at runtime from three independent
facts, all required:

1. a deployment-approval record valid against `contracts/deployment-approval.schema.json`;
2. a valid HMAC signature over the record, using the operator key
   `JVTO_DEPLOYMENT_APPROVAL_KEY` (held outside this repo) — the runtime alone cannot mint it;
3. the deployment gate passing (release validates, crosswalk clean, core dataset
   `production_ready`, and the record's fingerprint matches the actual release).

This module computes that determination; it does not write it back into the release.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

from .contracts import iter_contract_errors, validate_or_raise
from .utils import read_json, read_yaml, sha256_file, sha256_text
from .validator import validate_release

APPROVAL_CONTRACT = "deployment-approval"
APPROVAL_KEY_ENV = "JVTO_DEPLOYMENT_APPROVAL_KEY"


def compute_release_fingerprint(release_dir: Path | str) -> str:
    """A content fingerprint over the release's provenance + status files."""
    release_dir = Path(release_dir)
    parts = [sha256_file(release_dir / name) for name in ("source-lock.json", "release-manifest.json")]
    return sha256_text("|".join(parts))


def _signing_payload(release_id: str, fingerprint: str) -> str:
    return f"deployment-approval-v1|{release_id}|{fingerprint}"


def sign_approval(release_id: str, fingerprint: str, key: str) -> str:
    """HMAC-SHA256 over the canonical (release_id, fingerprint) payload."""
    return hmac.new(key.encode("utf-8"), _signing_payload(release_id, fingerprint).encode("utf-8"), hashlib.sha256).hexdigest()


def deployment_gate(repo_root: Path | str, release_dir: Path | str) -> dict[str, Any]:
    """Evaluate whether a release is eligible for deployment approval.

    Reports blocking conditions; it does NOT grant traffic. `ready_for_approval` being
    true only means an operator approval would be honored — it is never sufficient alone.
    """
    repo_root = Path(repo_root)
    release_dir = Path(release_dir)
    blocking: list[str] = []

    manifest = read_json(release_dir / "release-manifest.json")
    if manifest.get("customer_traffic_ready") is not False:
        blocking.append("release_self_marked_customer_traffic_ready")

    report = validate_release(repo_root, release_dir)
    if report.get("status") != "pass":
        blocking.append("release_validation_failed")

    crosswalk = manifest.get("package_crosswalk", {}) or {}
    if crosswalk.get("knowledge_only"):
        blocking.append("package_crosswalk_has_knowledge_only")
    if crosswalk.get("needs_review"):
        blocking.append("package_crosswalk_needs_review")

    if manifest.get("core_dataset_status") != "production_ready":
        blocking.append("core_dataset_not_production_ready")
    if manifest.get("status") != "integration_candidate":
        blocking.append(f"release_status_not_integration_candidate:{manifest.get('status')}")

    policy = read_yaml(repo_root / "config" / "release-policy.yaml").get("release", {})
    return {
        "release_id": manifest.get("release_id"),
        "release_fingerprint": compute_release_fingerprint(release_dir),
        "requires_manual_approval": bool(policy.get("require_manual_deployment_approval", True)),
        "ready_for_approval": not blocking,
        "blocking": sorted(blocking),
    }


def create_approval(
    repo_root: Path | str,
    release_dir: Path | str,
    approved_by: str,
    approved_at: str,
    *,
    key: str | None = None,
    checks_acknowledged: list[str] | None = None,
) -> dict[str, Any]:
    """Mint a signed deployment-approval record (operator tool).

    Requires the operator key (env `JVTO_DEPLOYMENT_APPROVAL_KEY` or explicit `key`); the
    runtime cannot produce a valid record without it. Verification independently enforces
    the gate, so this never bypasses readiness checks.
    """
    key = key if key is not None else os.environ.get(APPROVAL_KEY_ENV)
    if not key:
        raise ValueError(f"Deployment approval key unconfigured ({APPROVAL_KEY_ENV})")
    gate = deployment_gate(repo_root, release_dir)
    record: dict[str, Any] = {
        "schema_version": "deployment-approval-v1",
        "release_id": gate["release_id"],
        "release_fingerprint": gate["release_fingerprint"],
        "approved_by": approved_by,
        "approved_at": approved_at,
        "signature": sign_approval(gate["release_id"], gate["release_fingerprint"], key),
    }
    if checks_acknowledged:
        record["checks_acknowledged"] = list(checks_acknowledged)
    validate_or_raise(APPROVAL_CONTRACT, record)
    return record


def verify_deployment_approval(
    repo_root: Path | str,
    release_dir: Path | str,
    approval: dict[str, Any],
    *,
    key: str | None = None,
) -> dict[str, Any]:
    """Determine customer_traffic_ready for a release given an external approval record.

    Returns `customer_traffic_ready: true` only when the record is contract-valid, its
    signature verifies, its fingerprint matches the live release, and the deployment gate
    passes. The release file is never modified.
    """
    errors = iter_contract_errors(APPROVAL_CONTRACT, approval)
    if errors:
        return {
            "customer_traffic_ready": False,
            "release_id": None,
            "reasons": ["approval_contract_violation", *errors],
            "blocking": [],
            "approved_by": None,
        }

    gate = deployment_gate(repo_root, release_dir)
    reasons: list[str] = []

    if approval["release_id"] != gate["release_id"]:
        reasons.append("release_id_mismatch")
    if approval["release_fingerprint"] != gate["release_fingerprint"]:
        reasons.append("release_fingerprint_mismatch")

    key = key if key is not None else os.environ.get(APPROVAL_KEY_ENV)
    if not key:
        reasons.append("approval_key_unconfigured")
    else:
        expected = sign_approval(approval["release_id"], approval["release_fingerprint"], key)
        if not hmac.compare_digest(approval["signature"].encode("utf-8", "ignore"), expected.encode("ascii")):
            reasons.append("invalid_signature")

    if not gate["ready_for_approval"]:
        reasons.append("deployment_gate_not_ready")

    customer_traffic_ready = not reasons
    return {
        "customer_traffic_ready": customer_traffic_ready,
        "release_id": gate["release_id"],
        "reasons": sorted(reasons),
        "blocking": gate["blocking"],
        "approved_by": approval.get("approved_by") if customer_traffic_ready else None,
    }
