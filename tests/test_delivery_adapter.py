"""DecisionEnvelope -> DeliveryPlan seam (adapter + /v1/delivery-plan/from-decision + CLI).

Builds a real monolith release from fixtures and feeds realistic DecisionEnvelopes
through the seam, proving it maps inputs correctly and honors the envelope's handoff
as a hard floor while preserving every existing DeliveryPlan safety rule.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from jvto_agent_runtime.api import DeliveryPlanFromDecisionRequest, delivery_plan_from_decision_endpoint
from jvto_agent_runtime.contracts import is_valid
from jvto_agent_runtime.delivery_adapter import delivery_plan_from_decision
from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.sales_intelligence import load_customer_sales_config

REPO_ROOT = Path(__file__).resolve().parents[1]
BALI_PKG = "bali/ijen-bromo-madakaripura-3d2n"
SBY_PKG = "ijen-bromo-madakaripura-3d2n"
CONFIG = load_customer_sales_config(REPO_ROOT)


def _release(release_id: str = "test-adapter") -> Path:
    return build_release(
        REPO_ROOT,
        REPO_ROOT / "tests/fixtures/knowledge_catalog",
        REPO_ROOT / "tests/fixtures/itinerary_core",
        release_id,
        overwrite=True,
        web_root=REPO_ROOT / "tests/fixtures/web_experience",
    )


def _envelope(intent="check_price", status="ready", entities=None, handoff=None, feasibility=None):
    return {
        "schema_version": "decision-envelope-v1",
        "decision_id": "dec_test12345678",
        "release_id": "test",
        "intent": intent,
        "intent_status": status,
        "entities": entities or {},
        "knowledge": {"candidate_ids": [], "retrieval_status": "not_required"},
        "feasibility": feasibility or {"required": False, "status": "not_required"},
        "live_tool_plan": [],
        "response_constraints": [],
        "handoff": handoff or {"required": False, "reasons": []},
        "audit": {"knowledge_release": "x", "core_release": "y", "created_at": "z"},
    }


def test_seam_price_plan_origin_correct():
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("check_price", entities={"package_key": BALI_PKG, "number_of_guests": 4}),
        query="how much", config=CONFIG,
    )
    assert is_valid("delivery-plan", plan)
    assert plan["package_key"] == BALI_PKG
    pl = plan["resolved_primary_link"]
    assert pl and pl["sendable"] and "from-bali" in pl["url"] and "from-surabaya" not in pl["url"]


def test_seam_maps_number_of_guests_to_pax():
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("check_price", entities={"package_key": BALI_PKG, "number_of_guests": 6}),
        query="how much", config=CONFIG,
    )
    assert any("6 guests" in f for f in plan["short_facts"])


def test_seam_honors_envelope_handoff_floor():
    # Envelope decided handoff (e.g. low confidence) -> plan must be handoff, no booking CTA,
    # no price facts, even though the inputs would otherwise yield a standard price plan.
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("check_price", status="handoff_required",
                  entities={"package_key": BALI_PKG, "number_of_guests": 2},
                  handoff={"required": True, "reasons": ["low_intent_confidence"]}),
        query="how much", config=CONFIG,
    )
    assert plan["message_mode"] == "handoff"
    assert plan["handoff"]["required"] is True
    assert plan["handoff"]["reason"] == "low_intent_confidence"
    assert plan["secondary_link_intent"] is None and plan["resolved_secondary_link"] is None
    assert not any("price" in f.lower() for f in plan["short_facts"])
    assert is_valid("delivery-plan", plan)


def test_seam_unsupported_intent_is_handoff():
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("totally_unknown", status="unsupported", handoff={"required": True, "reasons": ["unsupported_intent"]}),
        query="???", config=CONFIG,
    )
    assert plan["message_mode"] == "handoff"
    assert plan["handoff"]["reason"] == "unsupported_intent"
    assert is_valid("delivery-plan", plan)


def test_seam_custom_quote_flag_blocks_booking_cta():
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("check_price", entities={"package_key": BALI_PKG, "pax": 2, "own_hotel": True}),
        query="price", config=CONFIG,
    )
    assert plan["message_mode"] == "handoff"
    assert plan["secondary_link_intent"] is None
    assert plan["quote_eligibility"]["status"] == "custom_quote_required"


def test_seam_single_recommendation_is_selected():
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("plan_itinerary", entities={"number_of_guests": 2},
                  feasibility={"required": True, "status": "feasible", "recommended_package_ids": [BALI_PKG]}),
        query="how much", config=CONFIG,
    )
    assert plan["package_key"] == BALI_PKG


def test_seam_multiple_recommendations_not_auto_selected():
    # Ambiguous recommendation set -> no arbitrary single package; stays discovery (no package_key).
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("plan_itinerary", entities={"number_of_guests": 2},
                  feasibility={"required": True, "status": "feasible", "recommended_package_ids": [BALI_PKG, SBY_PKG]}),
        query="how much", config=CONFIG,
    )
    assert plan["package_key"] is None


def test_seam_entity_package_takes_precedence_over_trip_brief():
    # Per-message entity package wins over a previously-selected one (the customer named a
    # different package this turn). Consistent with the entities-win context rule.
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("check_price", entities={"package_key": SBY_PKG, "pax": 2}),
        trip_brief={"selected_package_key": BALI_PKG}, query="how much", config=CONFIG,
    )
    assert plan["package_key"] == SBY_PKG  # this-turn entity wins
    assert "from-surabaya" in plan["resolved_primary_link"]["url"]


def test_seam_trip_brief_package_used_when_no_entity_package():
    # No package named this turn -> fall back to the accumulated selected package.
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("check_price", entities={"pax": 2}),
        trip_brief={"selected_package_key": BALI_PKG}, query="how much", config=CONFIG,
    )
    assert plan["package_key"] == BALI_PKG


def test_seam_entity_guest_count_preferred_over_tripbrief_pax_object():
    # TripBrief pax is an OBJECT {confirmed: 4}; the per-message entity count must win and
    # must not be shadowed by the object.
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("check_price", entities={"package_key": BALI_PKG, "number_of_guests": 2}),
        trip_brief={"selected_package_key": BALI_PKG, "pax": {"confirmed": 4}}, query="how much", config=CONFIG,
    )
    assert any("2 guests" in f for f in plan["short_facts"])
    assert not any("4 guests" in f for f in plan["short_facts"])


def test_seam_tripbrief_pax_object_normalized():
    # No entity count -> normalize the TripBrief pax object (confirmed) to an int.
    release = _release()
    plan = delivery_plan_from_decision(
        release, _envelope("check_price", entities={"package_key": BALI_PKG}),
        trip_brief={"selected_package_key": BALI_PKG, "pax": {"confirmed": 5}}, query="how much", config=CONFIG,
    )
    assert any("5 guests" in f for f in plan["short_facts"])


def test_seam_handoff_floor_cleans_existing_handoff_plan():
    # Envelope requires handoff AND the resolver already handed off for a custom quote
    # (own_hotel): the floor must still strip price facts and apply the envelope's reason,
    # not early-return and leave a standard-price fact behind.
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("check_price", status="handoff_required",
                  entities={"package_key": BALI_PKG, "pax": 2, "own_hotel": True},
                  handoff={"required": True, "reasons": ["low_intent_confidence"]}),
        query="how much", config=CONFIG,
    )
    assert plan["message_mode"] == "handoff"
    assert plan["handoff"]["reason"] == "low_intent_confidence"  # envelope reason takes precedence
    assert not any("price" in f.lower() for f in plan["short_facts"])  # no standard-price fact on handoff
    assert plan["secondary_link_intent"] is None
    assert is_valid("delivery-plan", plan)


def test_seam_needs_information_does_not_present_price_or_booking_cta():
    # Envelope deliberately withheld a committal answer pending missing fields
    # (intent_status=needs_information, handoff.required=False). The seam must NOT present a
    # standard price or a booking CTA; it downgrades to a non-committal clarify.
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("check_price", status="needs_information",
                  entities={"package_key": BALI_PKG}),  # no pax/date
        query="how much", config=CONFIG,
    )
    assert plan["message_mode"] == "quick_answer"  # not standard_price
    assert plan["secondary_link_intent"] is None and plan["resolved_secondary_link"] is None
    assert not any("price" in f.lower() for f in plan["short_facts"])
    assert plan["follow_up_question"]
    assert plan["handoff"]["required"] is False  # needs_information is a clarify, not a handoff
    assert is_valid("delivery-plan", plan)


def test_seam_needs_information_keeps_informational_modes():
    # A non-committal (informational) plan under needs_information is left as-is.
    release = _release()
    plan = delivery_plan_from_decision(
        release,
        _envelope("query_package_details", status="needs_information", entities={"package_key": BALI_PKG}),
        query="what is included?", config=CONFIG,
    )
    assert plan["message_mode"] == "inclusion_explanation"  # not downgraded
    assert is_valid("delivery-plan", plan)


# --- API endpoint ----------------------------------------------------------

def test_endpoint_from_decision_returns_valid_plan():
    release = _release("test-adapter-api")
    plan = delivery_plan_from_decision_endpoint(DeliveryPlanFromDecisionRequest(
        release_dir=str(release),
        decision_envelope=_envelope("check_price", entities={"package_key": BALI_PKG, "number_of_guests": 4}),
        query="how much",
    ))
    assert is_valid("delivery-plan", plan)
    assert plan["package_key"] == BALI_PKG


def test_endpoint_from_decision_missing_release_404():
    with pytest.raises(HTTPException) as exc:
        delivery_plan_from_decision_endpoint(DeliveryPlanFromDecisionRequest(
            release_dir="/no/such/release", decision_envelope=_envelope(), query="x"))
    assert exc.value.status_code == 404


def test_endpoint_from_decision_incomplete_catalog_404():
    release = _release("test-adapter-incomplete")
    (release / "agent-catalog" / "package-variations.json").unlink()
    with pytest.raises(HTTPException) as exc:
        delivery_plan_from_decision_endpoint(DeliveryPlanFromDecisionRequest(
            release_dir=str(release),
            decision_envelope=_envelope("check_price", entities={"package_key": BALI_PKG}), query="x"))
    assert exc.value.status_code == 404


# --- CLI parity ------------------------------------------------------------

def test_cli_delivery_plan_from_decision(capsys, tmp_path):
    import json
    import sys
    from jvto_agent_runtime.cli import main as cli_main
    release = _release("test-adapter-cli")
    env_path = tmp_path / "envelope.json"
    env_path.write_text(json.dumps(_envelope("check_price", entities={"package_key": BALI_PKG, "number_of_guests": 2})))
    old = sys.argv
    sys.argv = ["jvto-agent", "delivery-plan-from-decision", "--release-dir", str(release),
                "--decision-envelope", str(env_path), "--query", "how much"]
    try:
        cli_main()
    finally:
        sys.argv = old
    plan = json.loads(capsys.readouterr().out)
    assert is_valid("delivery-plan", plan)
    assert "from-bali" in plan["resolved_primary_link"]["url"]
