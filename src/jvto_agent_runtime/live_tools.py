"""Phase 3 — live transactional tool boundary.

Live truth (price, availability, booking, payment, hotel, operational notice) may
ONLY come from a current, successful live-tool response — never from released static
knowledge. This module is the runtime side of that boundary:

1. it knows the allowed tools and their policy (`config/tool-policy.yaml`, projected
   into each release as `tool-policy.json`);
2. it delegates the actual call to a pluggable `LiveToolAdapter`;
3. it validates every reply against `contracts/live-tool-response.schema.json`.

The default :class:`NotConnectedLiveToolAdapter` keeps the runtime self-contained:
until real authenticated adapters exist (Phase 3 implementation), every call returns a
contract-valid ``unavailable`` response, so the runtime never fabricates a live fact.
Real adapters require external credentials/endpoints and are out of scope for the scaffold.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .contracts import iter_contract_errors, validate_or_raise
from .utils import read_json, utc_now

LIVE_TOOL_RESPONSE_CONTRACT = "live-tool-response"

# Must match the `tool` enum in contracts/live-tool-response.schema.json.
KNOWN_TOOLS: tuple[str, ...] = (
    "pricing",
    "availability",
    "booking_status",
    "payment_status",
    "hotel",
    "operational_notice",
)


class UnknownToolError(ValueError):
    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(f"Unknown live tool: {tool!r}. Known tools: {', '.join(KNOWN_TOOLS)}")


def make_live_tool_response(
    tool: str,
    status: str,
    *,
    source_system: str,
    checked_at: str | None = None,
    valid_until: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a contract-valid live-tool-response."""
    response: dict[str, Any] = {
        "tool": tool,
        "status": status,
        "checked_at": checked_at or utc_now(),
        "source_system": source_system,
        "data": data or {},
    }
    if valid_until is not None:
        response["valid_until"] = valid_until
    return response


def unavailable_tool_response(tool: str, reason: str, *, source_system: str = "not_connected") -> dict[str, Any]:
    """A safe ``unavailable`` response used for every non-success path."""
    return make_live_tool_response(tool, "unavailable", source_system=source_system, data={"reason": reason})


@runtime_checkable
class LiveToolAdapter(Protocol):
    """Adapter that performs a real live-tool call and returns a live-tool-response."""

    def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]: ...


class NotConnectedLiveToolAdapter:
    """Default scaffold adapter: no live system is wired in.

    Returns a contract-valid ``unavailable`` response for every tool so the runtime
    never invents live truth. Replace with authenticated per-tool adapters in Phase 3.
    """

    def call(self, tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return unavailable_tool_response(tool, "live_tool_adapter_not_connected")


def tool_policy_for(release_dir: Path | str, tool: str) -> dict[str, Any]:
    """Return the runtime policy for a tool (confirmation_required, output_must_include, ...)."""
    policy = read_json(Path(release_dir) / "tool-policy.json")
    return (policy.get("live_tools", {}) or {}).get(tool, {})


def execute_live_tool(
    release_dir: Path | str,
    tool: str,
    params: dict[str, Any],
    *,
    intent: str | None = None,
    adapter: LiveToolAdapter | None = None,
) -> dict[str, Any]:
    """Execute one live-tool call and return a contract-valid live-tool-response.

    Enforces the tool-access policy (allowed intents) and never raises into the agent:
    an unknown tool, a disallowed intent, an adapter error, or a contract violation all
    degrade to a safe ``unavailable``/``error`` response.
    """
    if tool not in KNOWN_TOOLS:
        raise UnknownToolError(tool)

    policy = tool_policy_for(release_dir, tool)
    allowed_intents = policy.get("allowed_intents") or []
    if intent is not None and allowed_intents and intent not in allowed_intents:
        return make_live_tool_response(
            tool,
            "error",
            source_system="tool_policy",
            data={"reason": "tool_not_allowed_for_intent", "intent": intent, "allowed_intents": allowed_intents},
        )

    adapter = adapter or NotConnectedLiveToolAdapter()
    try:
        response = adapter.call(tool, params)
    except Exception as error:  # adapters must not be able to crash the runtime
        return unavailable_tool_response(tool, f"adapter_error:{type(error).__name__}", source_system="adapter")

    errors = iter_contract_errors(LIVE_TOOL_RESPONSE_CONTRACT, response)
    if errors:
        return unavailable_tool_response(tool, "live_tool_response_contract_violation", source_system="adapter")
    if response.get("tool") != tool:
        return unavailable_tool_response(tool, "live_tool_response_tool_mismatch", source_system="adapter")
    return response


def validate_live_tool_response(response: dict[str, Any]) -> None:
    """Raise ContractValidationError if a response does not satisfy the contract."""
    validate_or_raise(LIVE_TOOL_RESPONSE_CONTRACT, response)
