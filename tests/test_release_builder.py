from pathlib import Path

from jvto_agent_runtime.contracts import is_valid
from jvto_agent_runtime.monolith_catalog import load_monolith_catalog
from jvto_agent_runtime.presentation_resolver import resolve_delivery_plan
from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.utils import read_json
from jvto_agent_runtime.validator import validate_release


def test_build_release_from_fixture(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    release_dir = build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        "test-release",
        overwrite=True,
        web_root=repo_root / "tests/fixtures/web_experience",
    )
    manifest = read_json(release_dir / "release-manifest.json")
    assert manifest["knowledge_record_count"] == 2
    assert manifest["customer_traffic_ready"] is False
    crosswalk = read_json(release_dir / "package-crosswalk.json")
    assert crosswalk["summary"]["matched"] == 1
    report = validate_release(repo_root, release_dir)
    assert report["status"] == "pass"


def test_build_release_vendors_self_contained_agent_catalog(tmp_path: Path):
    """PR-1: the release vendors one agent-catalog the chat-time resolvers read from,
    so no jvto-web / jvto-itinerary-core clone is needed during a conversation."""
    repo_root = Path(__file__).resolve().parents[1]
    release_dir = build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        "test-monolith",
        overwrite=True,
        web_root=repo_root / "tests/fixtures/web_experience",
    )
    catalog = release_dir / "agent-catalog"
    # module layer + Core agent-contract + Web registries all vendored under one dir
    for name in (
        "general-modules.json", "package-variations.json", "module-compatibility.json",
        "agent-contract/package-customization-boundaries.json",
        "agent-contract/package-operational-composition.json",
        "customer-link-registry.json", "customer-media-registry.json",
        "catalog-manifest.json",
    ):
        assert (catalog / name).exists(), f"missing vendored file: {name}"

    cm = read_json(catalog / "catalog-manifest.json")
    assert cm["crosswalk_integrity"]["status"] == "aligned"
    assert cm["crosswalk_integrity"]["module_variation_count"] == cm["crosswalk_integrity"]["core_boundary_count"] == 16
    assert cm["web_experience"]["present"] is True
    # duplicate origin-sharing link keys are recorded (resolver marks them non-sendable)
    assert "package_ijen_bromo_madakaripura_3d2n" in cm["web_experience"]["link_key_collisions"]

    # source-lock records the web revision + integrity verdict
    lock = read_json(release_dir / "source-lock.json")
    assert lock["web_experience"]["present"] is True
    assert lock["agent_catalog"]["crosswalk_integrity"] == "aligned"

    report = validate_release(repo_root, release_dir)
    assert report["status"] == "pass"


def test_build_release_without_web_is_valid_but_warns(tmp_path: Path):
    """Web is optional: a release built without it still validates (warning, not error),
    recording that link/visual capabilities are unavailable."""
    repo_root = Path(__file__).resolve().parents[1]
    release_dir = build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        "test-noweb",
        overwrite=True,
    )
    cm = read_json(release_dir / "agent-catalog/catalog-manifest.json")
    assert cm["web_experience"]["present"] is False
    assert not (release_dir / "agent-catalog/customer-link-registry.json").exists()
    report = validate_release(repo_root, release_dir)
    assert report["status"] == "pass"  # web absence is a warning, not an error
    assert any(f["severity"] == "warning" for f in report["findings"])

    # A text-only release must still resolve a plan (empty registries, nothing sendable)
    # rather than raising FileNotFoundError on the missing web registries.
    ctx = load_monolith_catalog(release_dir)
    assert ctx.link_registry.by_key == {} and ctx.media_registry.by_key == {}
    plan = resolve_delivery_plan(
        release_dir, customer_job="J2_price_and_value", query="how much for 2",
        package_key="bali/bromo-ijen-3d2n", customer_context={"pax": 2},
    )
    assert is_valid("delivery-plan", plan)
    assert plan["resolved_primary_link"] is None or plan["resolved_primary_link"]["sendable"] is False


def test_chat_reads_only_the_release_no_upstream_clone(tmp_path: Path):
    """PR-2: after a build, a DeliveryPlan resolves from the single release root —
    no jvto-web / jvto-itinerary-core clone is read during the conversation."""
    repo_root = Path(__file__).resolve().parents[1]
    release_dir = build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        "test-chat",
        overwrite=True,
        web_root=repo_root / "tests/fixtures/web_experience",
    )
    # One reader loads the whole chat-time catalog from the release.
    ctx = load_monolith_catalog(release_dir)
    assert ctx.catalog_root == release_dir / "agent-catalog"
    assert len(ctx.module_layer.variations) == 16
    assert len(ctx.route_gate.by_key) == 16
    assert ctx.link_registry.by_key  # web vendored

    # The production resolver needs only the release root (no web/core roots).
    plan = resolve_delivery_plan(
        release_dir, customer_job="J2_price_and_value", query="how much for 4 guests?",
        package_key="bali/bromo-ijen-3d2n", customer_context={"pax": 4},
    )
    assert is_valid("delivery-plan", plan)
    assert plan["route_integrity"]["source"] == "itinerary-core:agent-contract"
