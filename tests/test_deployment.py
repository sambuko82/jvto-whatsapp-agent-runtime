from pathlib import Path

from jvto_agent_runtime.deployment import (
    compute_release_fingerprint,
    create_approval,
    deployment_gate,
    sign_approval,
    verify_deployment_approval,
)
from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.utils import read_json, write_json

REPO_ROOT = Path(__file__).resolve().parents[1]
KEY = "test-deployment-key"
WHEN = "2026-06-28T00:00:00Z"


def _release(release_id: str = "test-deployment-release") -> Path:
    return build_release(
        REPO_ROOT,
        REPO_ROOT / "tests/fixtures/knowledge_catalog",
        REPO_ROOT / "tests/fixtures/itinerary_core",
        release_id,
        overwrite=True,
    )


def _ready_release(release_id: str) -> Path:
    """A release that passes the deployment gate (core marked production_ready)."""
    release = _release(release_id)
    manifest_path = release / "release-manifest.json"
    manifest = read_json(manifest_path)
    manifest["core_dataset_status"] = "production_ready"
    write_json(manifest_path, manifest)
    return release


# --- fingerprint -----------------------------------------------------------


def test_fingerprint_is_stable_and_content_bound():
    release = _release("test-deployment-fp")
    fp1 = compute_release_fingerprint(release)
    fp2 = compute_release_fingerprint(release)
    assert fp1 == fp2
    manifest_path = release / "release-manifest.json"
    manifest = read_json(manifest_path)
    manifest["warnings"] = ["changed"]
    write_json(manifest_path, manifest)
    assert compute_release_fingerprint(release) != fp1


# --- gate ------------------------------------------------------------------


def test_gate_blocks_when_core_not_production_ready():
    gate = deployment_gate(REPO_ROOT, _release("test-deployment-gate-block"))
    assert gate["ready_for_approval"] is False
    assert "core_dataset_not_production_ready" in gate["blocking"]
    assert gate["requires_manual_approval"] is True


def test_gate_ready_when_conditions_met():
    gate = deployment_gate(REPO_ROOT, _ready_release("test-deployment-gate-ready"))
    assert gate["ready_for_approval"] is True
    assert gate["blocking"] == []


# --- grant path ------------------------------------------------------------


def test_valid_approval_grants_traffic_ready():
    release = _ready_release("test-deployment-grant")
    approval = create_approval(REPO_ROOT, release, "ops@jvto", WHEN, key=KEY)
    result = verify_deployment_approval(REPO_ROOT, release, approval, key=KEY)
    assert result["customer_traffic_ready"] is True
    assert result["reasons"] == []
    assert result["approved_by"] == "ops@jvto"


def test_release_file_is_never_mutated_to_true():
    release = _ready_release("test-deployment-no-mutate")
    approval = create_approval(REPO_ROOT, release, "ops@jvto", WHEN, key=KEY)
    verify_deployment_approval(REPO_ROOT, release, approval, key=KEY)
    # The on-disk release must still be false — traffic readiness is a runtime determination.
    assert read_json(release / "release-manifest.json")["customer_traffic_ready"] is False


# --- block paths -----------------------------------------------------------


def test_gate_not_ready_blocks_even_with_valid_signature():
    release = _release("test-deployment-gate-not-ready")  # core not production_ready
    approval = create_approval(REPO_ROOT, release, "ops@jvto", WHEN, key=KEY)
    result = verify_deployment_approval(REPO_ROOT, release, approval, key=KEY)
    assert result["customer_traffic_ready"] is False
    assert "deployment_gate_not_ready" in result["reasons"]


def test_invalid_signature_blocks():
    release = _ready_release("test-deployment-bad-sig")
    approval = create_approval(REPO_ROOT, release, "ops@jvto", WHEN, key=KEY)
    approval["signature"] = sign_approval(approval["release_id"], approval["release_fingerprint"], "wrong-key")
    result = verify_deployment_approval(REPO_ROOT, release, approval, key=KEY)
    assert result["customer_traffic_ready"] is False
    assert "invalid_signature" in result["reasons"]


def test_fingerprint_mismatch_blocks_after_tamper():
    release = _ready_release("test-deployment-tamper")
    approval = create_approval(REPO_ROOT, release, "ops@jvto", WHEN, key=KEY)
    manifest_path = release / "release-manifest.json"
    manifest = read_json(manifest_path)
    manifest["warnings"] = ["post-approval change"]  # neutral change -> fingerprint shifts, gate still ready
    write_json(manifest_path, manifest)
    result = verify_deployment_approval(REPO_ROOT, release, approval, key=KEY)
    assert result["customer_traffic_ready"] is False
    assert "release_fingerprint_mismatch" in result["reasons"]


def test_missing_key_blocks():
    release = _ready_release("test-deployment-no-key")
    approval = create_approval(REPO_ROOT, release, "ops@jvto", WHEN, key=KEY)
    result = verify_deployment_approval(REPO_ROOT, release, approval, key="")
    assert result["customer_traffic_ready"] is False
    assert "approval_key_unconfigured" in result["reasons"]


def test_bad_shape_blocks():
    release = _ready_release("test-deployment-bad-shape")
    result = verify_deployment_approval(REPO_ROOT, release, {"schema_version": "deployment-approval-v1"}, key=KEY)
    assert result["customer_traffic_ready"] is False
    assert "approval_contract_violation" in result["reasons"]
