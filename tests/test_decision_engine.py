from pathlib import Path

from jvto_agent_runtime.decision_engine import build_decision
from jvto_agent_runtime.release_builder import build_release


def _release() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        "test-decision-release",
        overwrite=True,
    )


def test_itinerary_requires_missing_fields():
    result = build_decision(_release(), "plan_itinerary", "Can we do Bromo and Ijen?", {"pickup_location": "Surabaya"})
    assert result["intent_status"] == "needs_information"
    assert result["feasibility"]["status"] == "unavailable"


def test_itinerary_ready_to_call_core():
    result = build_decision(
        _release(),
        "plan_itinerary",
        "Surabaya to Bali via Tumpak Sewu Bromo and Ijen",
        {
            "pickup_location": "Surabaya",
            "dropoff_location": "Bali",
            "requested_destinations": ["Tumpak Sewu", "Bromo", "Ijen"],
            "travel_date": "2026-08-10",
            "number_of_guests": 4,
            "pickup_time": "08:00",
            "duration_days": 4,
        },
    )
    assert result["intent_status"] == "ready"
    assert result["feasibility"]["required"] is True
    assert result["feasibility"]["status"] == "not_evaluated"
