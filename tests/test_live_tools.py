from pathlib import Path
from typing import Any

import pytest

from jvto_agent_runtime.contracts import is_valid, iter_contract_errors
from jvto_agent_runtime.live_tools import (
    KNOWN_TOOLS,
    NotConnectedLiveToolAdapter,
    UnknownToolError,
    execute_live_tool,
    make_live_tool_response,
    tool_policy_for,
)
from jvto_agent_runtime.release_builder import build_release

CONTRACT = "live-tool-response"


def _release(release_id: str = "test-live-tools-release") -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        release_id,
        overwrite=True,
    )


class _AvailablePricingAdapter:
    def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return make_live_tool_response(
            tool, "available", source_system="test-pos", valid_until="2026-08-11T00:00:00Z", data={"note": "from source"}
        )


class _RaisingAdapter:
    def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")


class _BadShapeAdapter:
    def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"tool": tool, "status": "totally_fine"}  # missing required fields, bad status


class _WrongToolAdapter:
    def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return make_live_tool_response("hotel", "available", source_system="test")


# --- helpers / basics ------------------------------------------------------


def test_make_response_is_contract_valid():
    response = make_live_tool_response("pricing", "available", source_system="x")
    assert is_valid(CONTRACT, response), iter_contract_errors(CONTRACT, response)


def test_known_tools_match_contract_enum():
    assert set(KNOWN_TOOLS) == {"pricing", "availability", "booking_status", "payment_status", "hotel", "operational_notice"}


def test_unknown_tool_raises():
    with pytest.raises(UnknownToolError):
        execute_live_tool(_release(), "weather", {})


def test_tool_policy_for_reads_release_policy():
    policy = tool_policy_for(_release(), "pricing")
    assert policy.get("confirmation_required") is True
    assert "check_price" in policy.get("allowed_intents", [])


# --- default (not connected) -----------------------------------------------


def test_not_connected_adapter_is_contract_valid_unavailable():
    response = NotConnectedLiveToolAdapter().call("availability", {})
    assert is_valid(CONTRACT, response)
    assert response["status"] == "unavailable"


def test_execute_default_is_unavailable():
    response = execute_live_tool(_release(), "pricing", {}, intent="check_price")
    assert is_valid(CONTRACT, response)
    assert response["status"] == "unavailable"
    assert response["data"]["reason"] == "live_tool_adapter_not_connected"


# --- policy enforcement ----------------------------------------------------


def test_intent_not_allowed_is_blocked():
    response = execute_live_tool(_release(), "pricing", {}, intent="plan_itinerary")
    assert is_valid(CONTRACT, response)
    assert response["status"] == "error"
    assert response["data"]["reason"] == "tool_not_allowed_for_intent"


def test_allowed_intent_reaches_adapter():
    response = execute_live_tool(_release(), "pricing", {}, intent="check_price", adapter=_AvailablePricingAdapter())
    assert response["status"] == "available"
    assert response["source_system"] == "test-pos"


# --- defensive degradation -------------------------------------------------


def test_adapter_exception_degrades_safely():
    response = execute_live_tool(_release(), "availability", {}, adapter=_RaisingAdapter())
    assert is_valid(CONTRACT, response)
    assert response["status"] == "unavailable"
    assert response["data"]["reason"].startswith("adapter_error")


def test_adapter_contract_violation_degrades_safely():
    response = execute_live_tool(_release(), "availability", {}, adapter=_BadShapeAdapter())
    assert is_valid(CONTRACT, response)
    assert response["status"] == "unavailable"
    assert response["data"]["reason"] == "live_tool_response_contract_violation"


def test_adapter_tool_mismatch_degrades_safely():
    response = execute_live_tool(_release(), "pricing", {}, intent="check_price", adapter=_WrongToolAdapter())
    assert is_valid(CONTRACT, response)
    assert response["status"] == "unavailable"
    assert response["data"]["reason"] == "live_tool_response_tool_mismatch"
