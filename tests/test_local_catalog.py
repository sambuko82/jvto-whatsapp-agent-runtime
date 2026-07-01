"""Committed compact catalog — one checkout, no sibling repos.

Proves the runtime carries the real 16-package data (price tiers, route integrity, web
link status) committed under catalog/, and generates a customer response from it WITHOUT
a build and WITHOUT the sibling upstream repositories.
"""
from pathlib import Path

import pytest

from jvto_agent_runtime.contracts import is_valid
from jvto_agent_runtime.release_builder import build_local_catalog, local_catalog_root
from jvto_agent_runtime.response_composer import compose_customer_response
from jvto_agent_runtime.sales_intelligence import load_customer_sales_config
from jvto_agent_runtime.utils import read_json

REPO = Path(__file__).resolve().parents[1]
CATALOG = local_catalog_root(REPO)
CONFIG = load_customer_sales_config(REPO)


def _env(entities):
    return {
        "intent": "check_price", "intent_status": "ready", "decision_id": "dec_localcat",
        "entities": entities, "feasibility": {"required": False, "status": "not_required"},
        "handoff": {"required": False, "reasons": []},
    }


def test_committed_catalog_carries_real_16_package_data():
    ac = CATALOG / "agent-catalog"
    cs = CATALOG / "customer-sales"
    variations = read_json(ac / "package-variations.json")
    boundaries = read_json(ac / "agent-contract" / "package-customization-boundaries.json")
    tiers = read_json(cs / "standard-price-tiers.json")
    links = read_json(ac / "customer-link-registry.json")["links"]
    assert len({v["package_key"] for v in variations}) == 16          # 16 real packages
    assert len({b["package_key"] for b in boundaries}) == 16          # real route integrity
    assert {b["route_integrity"] for b in boundaries} <= {"clean", "needs_review", "gap"}
    assert any(t.get("pax_tiers") and t["pax_tiers"][0].get("idr_per_person") for t in tiers)  # real price
    assert any(l.get("status") == "existing" and l.get("url") for l in links)  # real web link status


def test_generate_response_from_committed_catalog_without_build():
    # Reads ONLY the committed catalog/ — no dist build, no sibling repos.
    d = compose_customer_response(
        CATALOG, _env({"package_key": "bali/bromo-ijen-3d2n", "number_of_guests": 4}),
        query="how much for 4?", config=CONFIG,
    )
    assert is_valid("customer-response-draft", d)
    assert d["package"]["status"] == "resolved"
    assert d["price"]["status"] == "priced" and d["price"]["per_person"] > 0
    assert d["route_safety"]["status"] in ("clean", "needs_review")
    assert d["link"]["sendable"] and "from-bali/bromo-ijen-3d2n" in d["link"]["url"]


def test_committed_catalog_route_review_and_origin_states():
    # needs_review package surfaces price + a feasibility flag (not a handoff); surabaya twin
    # link stays origin-correct — all from committed data.
    review = compose_customer_response(
        CATALOG, _env({"package_key": "bromo-2d1n", "number_of_guests": 2}), query="how much", config=CONFIG,
    )
    assert review["route_safety"]["status"] == "needs_review"
    assert review["handoff"]["required"] is False
    assert "from-surabaya/bromo-2d1n" in review["link"]["url"]
    assert is_valid("customer-response-draft", review)


# --- regenerator determinism (only when the sibling clones are present) ------

_REAL = [Path("/home/user/knowledge-catalog-jvto-bootstrap"),
         Path("/home/user/jvto-itinerary-core"), Path("/home/user/jvto-web")]


@pytest.mark.skipif(not all(p.exists() for p in _REAL), reason="upstream sibling clones not present")
def test_build_local_catalog_matches_committed(tmp_path):
    out = build_local_catalog(REPO, _REAL[0], _REAL[1], out_dir=tmp_path / "catalog", web_root=_REAL[2])
    # the chat-time files regenerate byte-for-byte (deterministic, no timestamps)
    for rel in ("agent-catalog/package-variations.json", "agent-catalog/customer-link-registry.json",
                "agent-catalog/agent-contract/package-customization-boundaries.json",
                "customer-sales/standard-price-tiers.json"):
        assert (out / rel).read_bytes() == (CATALOG / rel).read_bytes(), rel


@pytest.mark.skipif(not all(p.exists() for p in _REAL), reason="upstream sibling clones not present")
def test_bad_upstream_path_does_not_destroy_existing_catalog(tmp_path):
    # Build a good catalog, then a rebuild with a bogus knowledge path must FAIL before
    # touching it — the existing catalog stays intact (no empty/degraded default read path).
    out = build_local_catalog(REPO, _REAL[0], _REAL[1], out_dir=tmp_path / "catalog", web_root=_REAL[2])
    before = (out / "agent-catalog/package-variations.json").read_bytes()
    with pytest.raises(FileNotFoundError):
        build_local_catalog(REPO, tmp_path / "nonexistent-knowledge", _REAL[1], out_dir=out, web_root=_REAL[2])
    assert (out / "agent-catalog/package-variations.json").read_bytes() == before
    assert not (out.parent / (out.name + ".staging")).exists()


# --- newly-connected Core facts (12-recommendation-rules.json + staging-logic.json) -------
# Previously computed by Core but consumed only by its internal CLI scenario evaluator or
# never projected downstream at all; now reach the customer response via the same existing
# disclosure mechanism, drawn from the real committed catalog.

def test_ferry_buffer_and_ijen_access_risk_surface_as_disclosures_not_a_price():
    endpoint_q = compose_customer_response(
        CATALOG, _env({"package_key": "tumpak-sewu-bromo-ijen-4d3n"}),
        query="where do we finish, is there a ferry to bali?", config=CONFIG,
    )
    assert is_valid("customer-response-draft", endpoint_q)
    assert any("Ferizy" in d or "queue buffer" in d for d in endpoint_q["required_disclosures"])
    assert endpoint_q["price"]["surfaced"] is False, "an endpoint question must never surface a price"

    readiness_q = compose_customer_response(
        CATALOG, _env({"package_key": "tumpak-sewu-bromo-ijen-4d3n"}),
        query="how hard is ijen, any access issues?", config=CONFIG,
    )
    assert is_valid("customer-response-draft", readiness_q)
    assert any("visitor cap" in d for d in readiness_q["required_disclosures"])
    assert not any("IDR" in d for d in readiness_q["required_disclosures"]), "no rate figure should ever reach the customer"
    assert readiness_q["price"]["surfaced"] is False


def test_a_surabaya_only_package_never_gets_the_ferry_or_ijen_disclosure():
    d = compose_customer_response(
        CATALOG, _env({"package_key": "bromo-1d1n"}),
        query="where do we finish?", config=CONFIG,
    )
    assert not any("Ferizy" in x or "visitor cap" in x for x in d["required_disclosures"])


def test_staging_operational_notes_surface_on_a_hotel_question():
    # bromo-2d1n has a real named overnight (readiness.rooming=available); bromo-1d1n is a
    # day-trip with no overnight, so its whole rooming object (incl. staging_notes) is None.
    d = compose_customer_response(
        CATALOG, _env({"package_key": "bromo-2d1n"}),
        query="where do we stay, what hotel?", config=CONFIG,
    )
    assert is_valid("customer-response-draft", d)
    assert any("jeep pickup" in x or "cold-weather" in x for x in d["required_disclosures"])
    assert d["price"]["surfaced"] is False, "a hotel question must never surface a price"


def test_blue_fire_topic_still_gets_the_ijen_access_risk_disclosure():
    # module_resolver classifies "blue fire" queries as topic=blue_fire (not
    # destination_readiness) even though both share the same presentation mode; the
    # ijen_access_closure_risk disclosure must not be lost for this common phrasing.
    d = compose_customer_response(
        CATALOG, _env({"package_key": "tumpak-sewu-bromo-ijen-4d3n"}),
        query="can we see blue fire at ijen?", config=CONFIG,
    )
    assert is_valid("customer-response-draft", d)
    assert any("cannot be guaranteed" in x for x in d["required_disclosures"]), "existing natural-phenomena disclosure must survive"
    assert any("visitor cap" in x for x in d["required_disclosures"]), "ijen_access_closure_risk must also surface on a blue_fire question"


def test_staging_notes_do_not_explode_into_a_wall_of_disclosures_on_multi_stop_packages():
    # ijen-papuma-tumpak-sewu-bromo-malang-6d5n has 18 operational_notes across 5 staging
    # areas in the committed catalog; required_disclosures has no max_text_lines cap, so
    # emitting every note verbatim would bury the actual overnight answer.
    d = compose_customer_response(
        CATALOG, _env({"package_key": "ijen-papuma-tumpak-sewu-bromo-malang-6d5n"}),
        query="where do we stay, what hotel?", config=CONFIG,
    )
    assert is_valid("customer-response-draft", d)
    assert len(d["required_disclosures"]) <= 5, f"hotel disclosures should be capped, got {len(d['required_disclosures'])}"
