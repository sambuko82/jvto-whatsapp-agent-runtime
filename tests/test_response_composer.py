"""Customer response composer — proportional validation.

One normal case + a custom-quote/handoff case + a route-review case + a correct-origin
link case + one real release build. Plus the unknown-package state and the API/CLI
surface. The composer fuses the delivery plan (presentation + route gate + link) with the
published catalog + price; tests assert states are preserved, never invented.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from jvto_agent_runtime.api import DeliveryPlanFromDecisionRequest, customer_response_endpoint
from jvto_agent_runtime.contracts import is_valid
from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.response_composer import compose_customer_response
from jvto_agent_runtime.sales_intelligence import load_customer_sales_config

REPO = Path(__file__).resolve().parents[1]
CONFIG = load_customer_sales_config(REPO)
CLEAN_PKG = "tumpak-sewu-bromo-ijen-4d3n"   # fixture: clean + priced + existing link
MIN2_PKG = "bromo-1d1n"                      # fixture: price tiers start at 2 pax


def _release(rid="test-compose"):
    return build_release(
        REPO, REPO / "tests/fixtures/knowledge_catalog", REPO / "tests/fixtures/itinerary_core",
        rid, overwrite=True, web_root=REPO / "tests/fixtures/web_experience",
    )


def _envelope(intent="check_price", status="ready", entities=None, handoff=None, feasibility=None):
    return {
        "schema_version": "decision-envelope-v1", "decision_id": "dec_compose1234",
        "release_id": "test", "intent": intent, "intent_status": status,
        "entities": entities or {}, "knowledge": {"candidate_ids": [], "retrieval_status": "not_required"},
        "feasibility": feasibility or {"required": False, "status": "not_required"},
        "live_tool_plan": [], "response_constraints": [],
        "handoff": handoff or {"required": False, "reasons": []},
        "audit": {"knowledge_release": "x", "core_release": "y", "created_at": "z"},
    }


def test_normal_case_fuses_facts_price_route_link():
    rel = _release()
    d = compose_customer_response(
        rel, _envelope(entities={"package_key": CLEAN_PKG, "number_of_guests": 4}),
        query="how much", config=CONFIG,
    )
    assert is_valid("customer-response-draft", d)
    assert d["package"]["status"] == "resolved" and d["package"]["title"]
    assert d["price"]["status"] == "priced" and d["price"]["surfaced"] is True
    assert d["price"]["per_person"] > 0 and d["price"]["group_total"] == d["price"]["per_person"] * 4
    assert d["route_safety"]["status"] == "clean"
    assert d["link"]["sendable"] and d["link"]["url"].startswith("https://")
    assert d["handoff"]["required"] is False
    assert any("per person" in l for l in d["draft_lines"])
    assert any("Availability" in l for l in d["required_disclosures"])


def test_custom_quote_handoff_does_not_surface_a_price():
    # pax below the published minimum -> price.custom_quote_required -> composer escalates to
    # handoff and surfaces NO concrete number (a state the delivery plan alone would miss).
    rel = _release()
    d = compose_customer_response(
        rel, _envelope(entities={"package_key": MIN2_PKG, "number_of_guests": 1}),
        query="how much", config=CONFIG,
    )
    assert d["price"]["status"] == "custom_quote_required"
    assert d["price"]["surfaced"] is False
    assert d["message_mode"] == "handoff" and d["handoff"]["required"] is True
    assert not any("per person" in l for l in d["draft_lines"])
    assert is_valid("customer-response-draft", d)


def test_quote_flag_handoff_blocks_price_and_booking():
    rel = _release()
    d = compose_customer_response(
        rel, _envelope(entities={"package_key": CLEAN_PKG, "pax": 2, "own_hotel": True}),
        query="price", config=CONFIG,
    )
    assert d["message_mode"] == "handoff" and d["handoff"]["required"] is True
    assert d["price"]["surfaced"] is False
    assert is_valid("customer-response-draft", d)


def test_needs_information_does_not_surface_price():
    # check_price with package + guests but the envelope is needs_information (e.g. missing
    # travel_date): the seam stripped the price; the composer must NOT resurface it.
    rel = _release()
    d = compose_customer_response(
        rel, _envelope(status="needs_information", entities={"package_key": CLEAN_PKG, "number_of_guests": 4}),
        query="how much", config=CONFIG,
    )
    assert d["price"]["surfaced"] is False
    assert not any("per person" in l for l in d["draft_lines"])
    assert d["message_mode"] != "standard_price"
    assert d["follow_up_question"]   # ask for the missing fields instead
    assert is_valid("customer-response-draft", d)


def test_draft_lines_within_budget():
    # Across every shaped case, draft_lines must never exceed max_text_lines.
    rel = _release()
    cases = [
        _envelope(entities={"package_key": CLEAN_PKG, "number_of_guests": 4}),
        _envelope(entities={"package_key": MIN2_PKG, "number_of_guests": 1}),
        _envelope(entities={"package_key": CLEAN_PKG, "pax": 2, "own_hotel": True}),
        _envelope(status="needs_information", entities={"package_key": CLEAN_PKG, "number_of_guests": 4}),
        _envelope(entities={"package_key": "does/not-exist", "number_of_guests": 2}),
    ]
    for env in cases:
        d = compose_customer_response(rel, env, query="how much", config=CONFIG)
        assert len(d["draft_lines"]) <= d["max_text_lines"], (env["entities"], d["draft_lines"])
        assert is_valid("customer-response-draft", d)


def test_correct_origin_link():
    rel = _release()
    d = compose_customer_response(
        rel, _envelope(entities={"package_key": MIN2_PKG, "number_of_guests": 3}),
        query="how much", config=CONFIG,
    )
    assert "from-surabaya/bromo-1d1n" in d["link"]["url"]


def test_unknown_package_preserves_not_found_handoff():
    rel = _release()
    d = compose_customer_response(
        rel, _envelope(entities={"package_key": "does/not-exist", "number_of_guests": 2}),
        query="how much", config=CONFIG,
    )
    assert d["package"]["status"] == "not_found"
    assert d["handoff"]["required"] is True
    assert d["price"]["surfaced"] is False
    assert is_valid("customer-response-draft", d)


def test_api_endpoint_and_missing_release():
    rel = _release("test-compose-api")
    d = customer_response_endpoint(DeliveryPlanFromDecisionRequest(
        release_dir=str(rel),
        decision_envelope=_envelope(entities={"package_key": CLEAN_PKG, "number_of_guests": 2}),
        query="how much"))
    assert is_valid("customer-response-draft", d)
    with pytest.raises(HTTPException) as exc:
        customer_response_endpoint(DeliveryPlanFromDecisionRequest(
            release_dir="/no/such/release", decision_envelope=_envelope(), query="x"))
    assert exc.value.status_code == 404


# --- one real release build (route-review case on real data) ----------------

_REAL = [Path("/home/user/knowledge-catalog-jvto-bootstrap"),
         Path("/home/user/jvto-itinerary-core"), Path("/home/user/jvto-web")]


@pytest.mark.skipif(not all(p.exists() for p in _REAL), reason="upstream sibling clones not present")
def test_real_build_route_review_case():
    rel = build_release(REPO, _REAL[0], _REAL[1], "test-compose-real", overwrite=True, web_root=_REAL[2])
    # bali/bromo-ijen-3d2n is route_integrity=needs_review in the real contract: price still
    # surfaces (needs_review is NOT a handoff) but route_safety flags a feasibility check.
    d = compose_customer_response(
        rel, _envelope(entities={"package_key": "bali/ijen-bromo-madakaripura-3d2n", "number_of_guests": 4}),
        query="how much", config=CONFIG,
    )
    assert is_valid("customer-response-draft", d)
    review = compose_customer_response(
        rel, _envelope(entities={"package_key": "bali/bromo-ijen-3d2n", "number_of_guests": 4}),
        query="how much", config=CONFIG,
    )
    assert review["route_safety"]["status"] == "needs_review"
    assert review["route_safety"]["requires_feasibility"] is True
    assert review["handoff"]["required"] is False        # needs_review is surfaced, not a handoff
    assert review["price"]["status"] == "priced" and review["price"]["surfaced"] is True
    assert "from-bali/bromo-ijen-3d2n" in review["link"]["url"]
    assert is_valid("customer-response-draft", review)


# --- topic-specific responses (against the committed catalog with real 16-pkg data) ---

CATALOG = REPO / "catalog"


def _compose(query, package_key=CLEAN_PKG, pax=4):
    return compose_customer_response(
        CATALOG, _envelope(intent="ask_question", entities={"package_key": package_key, "number_of_guests": pax}),
        query=query, config=CONFIG,
    )


def test_vehicle_question_answers_vehicle_rule_and_no_price():
    d = _compose("what vehicle do we get?")
    assert d["package"]["status"] == "resolved"
    assert any(l.startswith("Vehicle:") for l in d["draft_lines"]), d["draft_lines"]
    # price is NOT price-relevant here: no price line, price not surfaced, no endpoint blob
    assert d["price"]["surfaced"] is False
    assert not any("per person" in l for l in d["draft_lines"])
    assert not any("Standard finish" in l for l in d["draft_lines"])
    assert is_valid("customer-response-draft", d)


def test_hotel_question_answers_standard_overnights_no_price():
    d = _compose("which hotel do we stay in overnight?")
    assert any(l.startswith("Standard overnights:") for l in d["draft_lines"]), d["draft_lines"]
    assert d["price"]["surfaced"] is False
    assert not any("per person" in l for l in d["draft_lines"])
    assert is_valid("customer-response-draft", d)


def test_endpoint_question_answers_package_valid_endpoints():
    # CLEAN_PKG (tumpak-sewu-bromo-ijen-4d3n) finishes at Ketapang (standard) with
    # "Bali with additional transfer" as a LIVE arrangement, not a settled endpoint.
    d = _compose("where do we finish / get dropped off?")
    body = " ".join(d["draft_lines"])
    assert "Pickup:" in body and "Standard finish:" in body, d["draft_lines"]
    assert "Ketapang Harbor" in body
    # the live_condition option must be a disclosure, never asserted as a standard endpoint
    assert any("live arrangement" in disc.lower() for disc in d["required_disclosures"])
    assert not any("Bali with additional transfer" in l for l in d["draft_lines"])
    assert d["price"]["surfaced"] is False
    assert is_valid("customer-response-draft", d)


def test_endpoint_question_bali_origin_pickup_and_from_bali_boundary():
    d = _compose("where is the pickup and dropoff?", package_key="bali/ijen-bromo-madakaripura-3d2n")
    body = " ".join(d["draft_lines"])
    assert "Bali hotel area pickup (origin)" in body, d["draft_lines"]
    # Bali-origin crosses the boundary at the START and finishes in Surabaya (not "finish in Bali")
    assert any("finishes in Surabaya" in disc for disc in d["required_disclosures"])
    assert is_valid("customer-response-draft", d)


def test_price_only_surfaces_for_price_topic():
    priced = _compose("how much is it?")           # price topic
    vehicle = _compose("what vehicle do we get?")  # non-price topic, same package+pax
    assert priced["price"]["surfaced"] is True and any("per person" in l for l in priced["draft_lines"])
    assert vehicle["price"]["surfaced"] is False and not any("per person" in l for l in vehicle["draft_lines"])
