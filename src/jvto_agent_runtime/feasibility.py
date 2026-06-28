"""Phase 2 — Itinerary Core feasibility boundary.

This module is the runtime side of the route-feasibility contract. It does NOT
decide route truth; that authority belongs to ``jvto-itinerary-core`` (the
deterministic scenario evaluator). The runtime only:

1. turns classified entities into a schema-valid ``itinerary-core-request``;
2. hands that request to a pluggable evaluator adapter;
3. validates the adapter's reply against ``itinerary-core-response``;
4. returns a safe, contract-valid response no matter what fails.

The default :class:`NotConnectedEvaluator` keeps the runtime self-contained:
until a real evaluator is wired in (Phase 2 implementation), feasibility
degrades to ``unavailable`` + handoff rather than fabricating a route claim.

Customer/internal split (per docs/integration-plan.md Phase 2): the response
contract separates ``customer_visible_reasons`` (safe to surface to the
WhatsApp model) from ``known_gaps`` (internal diagnostics — never customer
facing). Keep that boundary when consuming a response.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .contracts import iter_contract_errors, validate_or_raise
from .utils import read_json

REQUEST_CONTRACT = "itinerary-core-request"
RESPONSE_CONTRACT = "itinerary-core-response"
FEASIBILITY_CAPABILITY = "scenario_feasibility_contract"

# Mirrors plan_itinerary.required_entities in config/intent-routing.yaml.
REQUIRED_ENTITY_FIELDS: tuple[str, ...] = (
    "pickup_location",
    "dropoff_location",
    "requested_destinations",
    "travel_date",
    "number_of_guests",
    "pickup_time",
    "duration_days",
)
OPTIONAL_ENTITY_FIELDS: tuple[str, ...] = ("package_key", "arrival_context")

_EMPTY = (None, "", [])


class MissingEntitiesError(ValueError):
    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__("Missing required itinerary entities: " + ", ".join(missing))


def missing_entities(entities: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_ENTITY_FIELDS if entities.get(field) in _EMPTY]


def build_itinerary_core_request(entities: dict[str, Any]) -> dict[str, Any]:
    """Build and validate an itinerary-core request from classified entities.

    Raises :class:`MissingEntitiesError` before validation so callers can ask
    the customer for specific fields instead of surfacing a schema error.
    """
    missing = missing_entities(entities)
    if missing:
        raise MissingEntitiesError(missing)
    request: dict[str, Any] = {
        "schema_version": "itinerary-core-request-v1",
        "pickup_location": entities["pickup_location"],
        "dropoff_location": entities["dropoff_location"],
        "requested_destinations": list(entities["requested_destinations"]),
        "travel_date": entities["travel_date"],
        "number_of_guests": entities["number_of_guests"],
        "pickup_time": entities["pickup_time"],
        "duration_days": entities["duration_days"],
    }
    for field in OPTIONAL_ENTITY_FIELDS:
        if entities.get(field) not in _EMPTY:
            request[field] = entities[field]
    validate_or_raise(REQUEST_CONTRACT, request)
    return request


def make_response(
    status: str,
    source_release_id: str,
    *,
    known_gaps: list[str] | None = None,
    recommended_package_ids: list[str] | None = None,
    alternative_package_ids: list[str] | None = None,
    customer_visible_reasons: list[str] | None = None,
    handoff_required: bool = False,
) -> dict[str, Any]:
    """Construct a contract-valid itinerary-core-response."""
    return {
        "schema_version": "itinerary-core-response-v1",
        "status": status,
        "source_release_id": source_release_id,
        "known_gaps": list(known_gaps or []),
        "recommended_package_ids": list(recommended_package_ids or []),
        "alternative_package_ids": list(alternative_package_ids or []),
        "customer_visible_reasons": list(customer_visible_reasons or []),
        "handoff_required": handoff_required,
    }


def unavailable_response(
    source_release_id: str,
    known_gaps: list[str],
    customer_visible_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """A safe ``unavailable`` + handoff response used for every failure path."""
    return make_response(
        "unavailable",
        source_release_id,
        known_gaps=known_gaps,
        customer_visible_reasons=customer_visible_reasons
        or ["Our team needs to confirm this route — I'll connect you with a human to help."],
        handoff_required=True,
    )


@runtime_checkable
class ItineraryCoreEvaluator(Protocol):
    """Adapter that turns a feasibility request into a feasibility response."""

    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]: ...


class NotConnectedEvaluator:
    """Default scaffold evaluator: no real route logic is wired in yet.

    Returns a contract-valid ``unavailable`` + handoff response so the runtime
    never invents feasibility. Replace with :class:`HttpItineraryCoreEvaluator`
    (or an in-process adapter) once the itinerary-core feasibility API exists.
    """

    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]:
        return unavailable_response(
            source_release_id,
            known_gaps=["itinerary_core_evaluator_not_connected"],
            customer_visible_reasons=[
                "I can't auto-confirm this custom route yet — let me connect you with our team to check it.",
            ],
        )


class HttpItineraryCoreEvaluator:
    """Phase 2 integration point: call a running itinerary-core feasibility API.

    Posts a schema-valid request and expects a schema-valid response. This class
    is intentionally NOT exercised by the test suite (it requires a live
    service). Any transport, decoding, or contract failure degrades to a safe
    ``unavailable`` + handoff response — the runtime never raises into the agent.
    """

    def __init__(self, base_url: str, *, path: str = "/v1/feasibility", timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.timeout = timeout

    def evaluate(self, request: dict[str, Any], *, source_release_id: str) -> dict[str, Any]:
        url = f"{self.base_url}{self.path}"
        payload = json.dumps(request).encode("utf-8")
        http_request = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout) as handle:  # noqa: S310 (configured base_url)
                body = json.loads(handle.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            return unavailable_response(
                source_release_id,
                known_gaps=[f"itinerary_core_transport_error:{type(error).__name__}"],
            )
        errors = iter_contract_errors(RESPONSE_CONTRACT, body)
        if errors:
            return unavailable_response(
                source_release_id,
                known_gaps=["itinerary_core_response_contract_violation", *errors],
            )
        return body


def evaluate_feasibility(
    release_dir: Path | str,
    entities: dict[str, Any],
    evaluator: ItineraryCoreEvaluator | None = None,
) -> dict[str, Any]:
    """Evaluate route feasibility for a release and return a contract response.

    Always returns a schema-valid itinerary-core-response. Never raises for
    missing entities, an unavailable capability, or an evaluator failure — those
    degrade to ``unavailable`` + handoff with the cause recorded in
    ``known_gaps`` (internal, not customer facing).
    """
    release_dir = Path(release_dir)
    manifest = read_json(release_dir / "release-manifest.json")
    capabilities = read_json(release_dir / "core-capabilities.json")
    source_release_id = manifest.get("release_id", "unknown")

    if FEASIBILITY_CAPABILITY not in capabilities.get("available_capabilities", []):
        return unavailable_response(
            source_release_id,
            known_gaps=["scenario_feasibility_contract_capability_unavailable"],
        )

    try:
        request = build_itinerary_core_request(entities)
    except MissingEntitiesError as error:
        return unavailable_response(
            source_release_id,
            known_gaps=[f"missing_entity:{field}" for field in error.missing],
            customer_visible_reasons=["I need a few more trip details before I can check this route."],
        )

    evaluator = evaluator or NotConnectedEvaluator()
    response = evaluator.evaluate(request, source_release_id=source_release_id)
    errors = iter_contract_errors(RESPONSE_CONTRACT, response)
    if errors:
        return unavailable_response(
            source_release_id,
            known_gaps=["evaluator_response_contract_violation", *errors],
        )
    return response
