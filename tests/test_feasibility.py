import io
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from jvto_agent_runtime.contracts import ContractValidationError, is_valid, iter_contract_errors
from jvto_agent_runtime.decision_engine import build_decision
from jvto_agent_runtime.feasibility import (
    HttpItineraryCoreEvaluator,
    MissingEntitiesError,
    NotConnectedEvaluator,
    build_itinerary_core_request,
    evaluate_feasibility,
    make_response,
    missing_entities,
)
from jvto_agent_runtime.release_builder import build_release

FULL_ENTITIES: dict[str, Any] = {
    "pickup_location": "Surabaya",
    "dropoff_location": "Bali",
    "requested_destinations": ["Tumpak Sewu", "Bromo", "Ijen"],
    "travel_date": "2026-08-10",
    "number_of_guests": 4,
    "pickup_time": "08:00",
    "duration_days": 4,
}


def _release(release_id: str = "test-feasibility-release") -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        release_id,
        overwrite=True,
    )


def _release_without_capability(release_id: str) -> Path:
    """Build a release, then drop the scenario_feasibility_contract capability."""
    release = _release(release_id)
    caps_path = release / "core-capabilities.json"
    caps = json.loads(caps_path.read_text(encoding="utf-8"))
    caps["available_capabilities"] = [c for c in caps["available_capabilities"] if c != "scenario_feasibility_contract"]
    caps_path.write_text(json.dumps(caps), encoding="utf-8")
    return release


class _FeasibleEvaluator:
    """Stand-in for a connected itinerary-core evaluator that returns feasible."""

    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]:
        return {
            "schema_version": "itinerary-core-response-v1",
            "status": "feasible",
            "source_release_id": source_release_id,
            "known_gaps": [],
            "recommended_package_ids": ["ijen-bromo-madakaripura-3d2n"],
            "alternative_package_ids": [],
            "customer_visible_reasons": ["This route fits comfortably in 4 days."],
            "handoff_required": False,
        }


class _NotFeasibleEvaluator:
    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]:
        return make_response(
            "not_feasible",
            source_release_id,
            customer_visible_reasons=["This route can't be completed in the requested time."],
            handoff_required=False,
        )


class _ConditionalEvaluator:
    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]:
        return make_response(
            "conditional",
            source_release_id,
            recommended_package_ids=["ijen-bromo-madakaripura-3d2n"],
            customer_visible_reasons=["Tight but doable with an early start."],
            handoff_required=False,
        )


class _BrokenEvaluator:
    """Returns a non-contract-valid payload to exercise defensive validation."""

    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]:
        return {"status": "definitely_feasible"}


# --- request builder -------------------------------------------------------


def test_build_request_is_contract_valid():
    request = build_itinerary_core_request(FULL_ENTITIES)
    assert request["schema_version"] == "itinerary-core-request-v1"
    assert is_valid("itinerary-core-request", request), iter_contract_errors("itinerary-core-request", request)


def test_build_request_carries_optional_fields():
    request = build_itinerary_core_request({**FULL_ENTITIES, "package_key": "ijen-bromo-madakaripura-3d2n"})
    assert request["package_key"] == "ijen-bromo-madakaripura-3d2n"
    assert is_valid("itinerary-core-request", request)


def test_build_request_missing_raises_with_fields():
    with pytest.raises(MissingEntitiesError) as exc:
        build_itinerary_core_request({"pickup_location": "Surabaya"})
    assert "dropoff_location" in exc.value.missing
    assert "travel_date" in exc.value.missing


def test_missing_entities_treats_empty_as_missing():
    assert "requested_destinations" in missing_entities({**FULL_ENTITIES, "requested_destinations": []})


def test_build_request_rejects_non_iso_travel_date():
    # format: "date" must be enforced (the validator is built with a FormatChecker).
    with pytest.raises(ContractValidationError):
        build_itinerary_core_request({**FULL_ENTITIES, "travel_date": "next tuesday"})


# --- evaluators ------------------------------------------------------------


def test_not_connected_evaluator_is_contract_valid_and_safe():
    response = NotConnectedEvaluator().evaluate(build_itinerary_core_request(FULL_ENTITIES), source_release_id="r1")
    assert is_valid("itinerary-core-response", response), iter_contract_errors("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert response["handoff_required"] is True


def test_http_evaluator_degrades_to_unavailable_on_transport_error():
    # No server is listening; the adapter must not raise — it degrades safely.
    evaluator = HttpItineraryCoreEvaluator("http://127.0.0.1:9", timeout=0.25)
    response = evaluator.evaluate(build_itinerary_core_request(FULL_ENTITIES), source_release_id="r1")
    assert is_valid("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert any(gap.startswith("itinerary_core_transport_error") for gap in response["known_gaps"])


def test_http_evaluator_degrades_on_contract_violation(monkeypatch):
    # A 200 with a contract-invalid body must degrade safely, not propagate.
    def fake_urlopen(request, timeout=None):
        return io.BytesIO(b'{"status": "definitely_feasible"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    evaluator = HttpItineraryCoreEvaluator("http://itinerary-core.test")
    response = evaluator.evaluate(build_itinerary_core_request(FULL_ENTITIES), source_release_id="r1")
    assert is_valid("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert "itinerary_core_response_contract_violation" in response["known_gaps"]


# --- orchestrator ----------------------------------------------------------


def test_evaluate_feasibility_default_is_unavailable_handoff():
    response = evaluate_feasibility(_release(), FULL_ENTITIES)
    assert is_valid("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert response["handoff_required"] is True


def test_evaluate_feasibility_missing_entities_is_contract_valid():
    response = evaluate_feasibility(_release(), {"pickup_location": "Surabaya"})
    assert is_valid("itinerary-core-response", response)
    assert any(gap.startswith("missing_entity:") for gap in response["known_gaps"])


def test_evaluate_feasibility_malformed_date_degrades_safely():
    # A non-ISO travel_date must not crash the boundary; it degrades to unavailable.
    response = evaluate_feasibility(_release(), {**FULL_ENTITIES, "travel_date": "next tuesday"})
    assert is_valid("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert "request_contract_violation" in response["known_gaps"]


def test_evaluate_feasibility_feasible_passthrough():
    response = evaluate_feasibility(_release(), FULL_ENTITIES, _FeasibleEvaluator())
    assert response["status"] == "feasible"
    assert response["recommended_package_ids"] == ["ijen-bromo-madakaripura-3d2n"]


def test_evaluate_feasibility_capability_unavailable():
    release = _release_without_capability("test-feasibility-no-capability")
    response = evaluate_feasibility(release, FULL_ENTITIES, _FeasibleEvaluator())
    assert is_valid("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert "scenario_feasibility_contract_capability_unavailable" in response["known_gaps"]


def test_evaluate_feasibility_rejects_bad_evaluator_output():
    response = evaluate_feasibility(_release(), FULL_ENTITIES, _BrokenEvaluator())
    assert is_valid("itinerary-core-response", response)
    assert response["status"] == "unavailable"
    assert "evaluator_response_contract_violation" in response["known_gaps"]


# --- decision-engine integration (backward compatible) ---------------------


def test_decision_without_evaluator_unchanged_and_envelope_valid():
    result = build_decision(_release(), "plan_itinerary", "Surabaya to Bali via Bromo and Ijen", FULL_ENTITIES)
    assert result["feasibility"]["status"] == "not_evaluated"
    assert "recommended_package_ids" not in result["feasibility"]
    assert is_valid("decision-envelope", result), iter_contract_errors("decision-envelope", result)


def test_decision_integrates_feasible_evaluator():
    result = build_decision(
        _release(), "plan_itinerary", "Surabaya to Bali via Bromo and Ijen", FULL_ENTITIES, evaluator=_FeasibleEvaluator()
    )
    assert result["feasibility"]["status"] == "feasible"
    assert result["feasibility"]["recommended_package_ids"] == ["ijen-bromo-madakaripura-3d2n"]
    # Internal diagnostics (known_gaps) must never cross into the envelope.
    assert "known_gaps" not in result["feasibility"]
    assert result["intent_status"] == "ready"
    assert is_valid("decision-envelope", result), iter_contract_errors("decision-envelope", result)


def test_decision_handoff_when_evaluator_unavailable():
    result = build_decision(
        _release(), "plan_itinerary", "Surabaya to Bali via Bromo and Ijen", FULL_ENTITIES, evaluator=NotConnectedEvaluator()
    )
    assert result["feasibility"]["status"] == "unavailable"
    assert "known_gaps" not in result["feasibility"]
    assert result["handoff"]["required"] is True
    assert "itinerary_core_handoff_required" in result["handoff"]["reasons"]
    assert result["intent_status"] == "handoff_required"
    assert is_valid("decision-envelope", result)


_QUERY = "Surabaya to Bali via Tumpak Sewu Bromo and Ijen"


def test_decision_not_feasible_triggers_handoff_even_without_flag():
    # An evaluator can report not_feasible without setting handoff_required; the runtime
    # must still refuse to present the route as "ready".
    result = build_decision(_release(), "plan_itinerary", _QUERY, FULL_ENTITIES, evaluator=_NotFeasibleEvaluator())
    assert result["feasibility"]["status"] == "not_feasible"
    assert result["intent_status"] == "handoff_required"
    assert "itinerary_core_route_not_confirmable" in result["handoff"]["reasons"]
    assert is_valid("decision-envelope", result)


def test_decision_conditional_stays_ready_with_caveats():
    result = build_decision(_release(), "plan_itinerary", _QUERY, FULL_ENTITIES, evaluator=_ConditionalEvaluator())
    assert result["feasibility"]["status"] == "conditional"
    assert result["intent_status"] == "ready"
    assert result["handoff"]["required"] is False
    assert result["feasibility"]["customer_visible_reasons"]
    assert is_valid("decision-envelope", result)


def test_decision_capability_unavailable_sets_unavailable_and_handoff():
    release = _release_without_capability("test-feasibility-decision-no-capability")
    result = build_decision(release, "plan_itinerary", "q", FULL_ENTITIES)
    assert result["feasibility"]["status"] == "unavailable"
    assert "itinerary_core_feasibility_capability_unavailable" in result["handoff"]["reasons"]
    assert result["intent_status"] == "handoff_required"
    assert is_valid("decision-envelope", result)
