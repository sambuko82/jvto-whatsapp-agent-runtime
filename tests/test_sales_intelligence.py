import json
from pathlib import Path

import pytest

from jvto_agent_runtime.contracts import is_valid, iter_contract_errors
from jvto_agent_runtime.decision_engine import build_decision
from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.sales_intelligence import (
    derive_default_customer_job,
    derive_requirement_profile,
    derive_response_plan,
    derive_trip_brief_status,
    load_customer_sales_config,
    merge_trip_brief,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_customer_sales_config(REPO_ROOT)


def _release() -> Path:
    return build_release(
        REPO_ROOT,
        REPO_ROOT / "tests/fixtures/knowledge_catalog",
        REPO_ROOT / "tests/fixtures/itinerary_core",
        "test-sales-release",
        overwrite=True,
    )


# --- customer job (grouping label) -----------------------------------------


def test_default_job_by_intent():
    assert derive_default_customer_job("check_price", None, CONFIG) == "J2_price_and_value"
    assert derive_default_customer_job("plan_itinerary", None, CONFIG) == "J3_route_and_timing"
    assert derive_default_customer_job("query_operational_notice", None, CONFIG) == "J4_live_confirmation"
    assert derive_default_customer_job("get_booking_status", None, CONFIG) == "J5_exception_and_handoff"
    assert derive_default_customer_job("general_greeting", None, CONFIG) == "greeting"
    assert derive_default_customer_job("totally_unknown", None, CONFIG) == "unsupported"


def test_job_override_by_query_keyword():
    assert derive_default_customer_job("query_package_details", None, CONFIG, query="what is included?") == "J2_price_and_value"
    assert derive_default_customer_job("query_package_details", None, CONFIG, query="can we finish in Bali?") == "J3_route_and_timing"
    assert derive_default_customer_job("query_package_details", None, CONFIG, query="tell me about Bromo") == "J1_package_discovery"


# --- requirement profile (functional driver) -------------------------------


def test_requirement_profile_defaults_and_overrides():
    assert derive_requirement_profile("check_price", None, CONFIG) == "standard_price"
    assert derive_requirement_profile("plan_itinerary", None, CONFIG) == "route_validation"
    assert derive_requirement_profile("query_package_details", None, CONFIG, "tell me about Bromo") == "package_recommendation"
    assert derive_requirement_profile("query_package_details", None, CONFIG, "what is included?") == "package_information"
    assert derive_requirement_profile("query_package_details", None, CONFIG, "how much is it?") == "standard_price"


def test_requirement_profile_selected_package_key_flips_to_information():
    brief = {"selected_package_key": "tumpak-sewu-bromo-ijen-4d3n"}
    assert derive_requirement_profile("query_package_details", brief, CONFIG, "any details?") == "package_information"


def test_trip_brief_status_by_profile():
    full_route = {"travel_dates": {"start": "2026-07-19"}, "pax": {"confirmed": 2}, "pickup": {"location": "Surabaya"}, "dropoff": {"location": "Bali"}, "destinations": [{"id": "bromo", "priority": "required"}]}
    assert derive_trip_brief_status(full_route, "route_validation", CONFIG) == "complete"
    assert derive_trip_brief_status({"pax": {"confirmed": 2}}, "route_validation", CONFIG) == "incomplete"
    assert derive_trip_brief_status(None, "general_information", CONFIG) == "not_applicable"
    assert derive_trip_brief_status({"selected_package_key": "x", "pax": {"confirmed": 4}}, "standard_price", CONFIG) == "complete"
    assert derive_trip_brief_status({"pax": {"confirmed": 4}}, "standard_price", CONFIG) == "incomplete"


# --- trip brief merge + invalidation ---------------------------------------


def test_merge_trip_brief_bumps_version_and_validates():
    base = {"schema_version": "trip-brief-v1", "plan_version": 1, "pax": {"confirmed": 2}}
    merged = merge_trip_brief(base, {"pax": {"confirmed": 4}}, CONFIG)
    assert merged["plan_version"] == 2
    assert merged["pax"]["confirmed"] == 4
    assert is_valid("trip-brief", merged), iter_contract_errors("trip-brief", merged)


def test_merge_trip_brief_marks_superseded_on_core_change():
    base = {"schema_version": "trip-brief-v1", "plan_version": 1, "travel_dates": {"start": "2026-07-19"}}
    merged = merge_trip_brief(base, {"travel_dates": {"start": "2026-07-20"}}, CONFIG)
    assert "superseded_pending_revalidation" in merged["active_blockers"]


def test_merge_trip_brief_accepts_selected_package_key():
    merged = merge_trip_brief(None, {"selected_package_key": "tumpak-sewu-bromo-ijen-4d3n"}, CONFIG)
    assert merged["selected_package_key"] == "tumpak-sewu-bromo-ijen-4d3n"
    assert is_valid("trip-brief", merged)


# --- governance / PII guards -----------------------------------------------


def test_contracts_carry_no_raw_pii_keys():
    for name in ("trip-brief", "response-plan"):
        text = (REPO_ROOT / "contracts" / f"{name}.schema.json").read_text(encoding="utf-8").lower()
        for forbidden in ('"email"', '"phone"', '"full_name"', '"booking_reference"', '"payment_detail"', '"credit_card"'):
            assert forbidden not in text, f"{name} must not declare PII field {forbidden}"


def test_response_plan_never_emits_price_value():
    release = _release()
    envelope = build_decision(release, "check_price", "How much is the Bromo Ijen package for 4?", {})
    brief = {"schema_version": "trip-brief-v1", "plan_version": 1, "selected_package_key": "tumpak-sewu-bromo-ijen-4d3n", "pax": {"confirmed": 4}}
    plan = derive_response_plan(envelope, brief, CONFIG, query="How much is the Bromo Ijen package for 4?")
    dumped = json.dumps(plan)
    assert "price_per_person" not in dumped and "group_total" not in dumped
    assert plan["mode"] == "execute_tool"
    assert any(a["type"] == "price_quote" for a in plan["required_actions"])
    assert any("Availability is not yet confirmed" in d for d in plan["required_disclosures"])


# --- correction-specific behavior ------------------------------------------


def test_route_signal_triggers_itinerary_core():
    release = _release()
    envelope = build_decision(release, "query_package_details", "Bromo and Ijen, finish in Bali?", {})
    plan = derive_response_plan(envelope, {"dropoff": {"location": "Bali"}}, CONFIG, query="finish in Bali?", route_signals=["unspecified_endpoint"])
    assert any(a["type"] == "itinerary_core" for a in plan["required_actions"])


def test_will_it_reopen_is_status_query_not_handoff():
    release = _release()
    envelope = build_decision(release, "query_destination_details", "When will Blue Fire at Ijen reopen?", {})
    plan = derive_response_plan(envelope, None, CONFIG, query="When will Blue Fire at Ijen reopen?")
    assert plan["handoff"]["required"] is False
    assert any(a["type"] == "live_check" for a in plan["required_actions"])
    assert any("cannot be guaranteed" in d for d in plan["required_disclosures"])


def test_guarantee_demand_is_handoff():
    release = _release()
    envelope = build_decision(release, "query_destination_details", "Can you guarantee Blue Fire at Ijen?", {})
    plan = derive_response_plan(envelope, None, CONFIG, query="Can you guarantee Blue Fire at Ijen?")
    assert plan["handoff"]["required"] is True
    assert any("cannot be guaranteed" in d for d in plan["required_disclosures"])


def test_inclusion_with_selected_package_needs_no_pax_or_pickup():
    release = _release()
    envelope = build_decision(release, "query_package_details", "What is included in the Bromo Ijen package?", {})
    brief = {"schema_version": "trip-brief-v1", "plan_version": 1, "selected_package_key": "tumpak-sewu-bromo-ijen-4d3n"}
    plan = derive_response_plan(envelope, brief, CONFIG, query="What is included in the Bromo Ijen package?")
    assert plan["mode"] == "answer"
    assert plan["clarifying_question"] is None
    assert [a["type"] for a in plan["required_actions"]] == ["catalog_lookup"]


# --- evaluation cases (synthetic, redacted) --------------------------------


def _load_cases() -> list[dict]:
    path = REPO_ROOT / "tests/customer-sales/evaluation-cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_evaluation_cases(case):
    release = _release()
    envelope = build_decision(release, case["intent"], case.get("query", ""), case.get("entities", {}))
    plan = derive_response_plan(
        envelope,
        case.get("trip_brief"),
        CONFIG,
        query=case.get("query", ""),
        signals=case.get("signals", []),
        route_signals=case.get("route_signals", []),
    )
    assert is_valid("response-plan", plan), iter_contract_errors("response-plan", plan)
    exp = case["expect"]
    if "customer_job" in exp:
        assert plan["customer_job"] == exp["customer_job"], (case["name"], plan["customer_job"])
    if "mode" in exp:
        assert plan["mode"] == exp["mode"], (case["name"], plan["mode"])
    if "trip_brief_status" in exp:
        assert plan["trip_brief_status"] == exp["trip_brief_status"], (case["name"], plan["trip_brief_status"])
    if "handoff_required" in exp:
        assert plan["handoff"]["required"] is exp["handoff_required"], (case["name"], plan["handoff"])
    if exp.get("clarifying_question_present"):
        assert plan["clarifying_question"], (case["name"], "expected a clarifying question")
    if exp.get("clarifying_question_absent"):
        assert plan["clarifying_question"] is None, (case["name"], plan["clarifying_question"])
    plan_types = {a["type"] for a in plan["required_actions"]}
    for action_type in exp.get("has_action_types", []):
        assert action_type in plan_types, (case["name"], action_type, plan["required_actions"])
    for action_type in exp.get("no_action_types", []):
        assert action_type not in plan_types, (case["name"], action_type, plan["required_actions"])
    for needle in exp.get("disclosures_include", []):
        assert any(needle in d for d in plan["required_disclosures"]), (case["name"], needle, plan["required_disclosures"])
    dumped = json.dumps(plan)
    assert "price_per_person" not in dumped and "group_total" not in dumped
