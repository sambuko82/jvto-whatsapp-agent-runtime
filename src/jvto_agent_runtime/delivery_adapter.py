"""DecisionEnvelope -> DeliveryPlan seam (presentation adapter).

Connects `/v1/decisions` (a DecisionEnvelope: routing + safety + handoff) to the
presentation layer (`/v1/delivery-plan`: a DeliveryPlan) WITHOUT building full
orchestration. It maps an already-built DecisionEnvelope (+ optional TripBrief) into
the presentation inputs the resolver needs, builds a DeliveryPlan from the one local
release, then honors the envelope's handoff as a hard floor (escalate only, never
downgrade) — so the seam can never present a normal plan when the system already
decided to hand off.

Design rules (do not break):
- The DecisionEnvelope stays the source of truth for routing/safety/handoff; this only
  adds the presentation read on top, mirroring the ResponsePlan escalate-only handoff rule.
- customer_context is a WHITELIST projection (pax + quote-eligibility flags); arbitrary
  entities are never forwarded into the plan.
- Core's recommended package is only auto-selected when unambiguous (exactly one).
- Reads only the one local release (via resolve_delivery_plan); authors nothing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import validate_or_raise
from .presentation_resolver import DELIVERY_PLAN_CONTRACT, MODE_MAX_LINES, resolve_delivery_plan
from .sales_intelligence import derive_default_customer_job

# customer_context keys the presentation resolver understands (quote-eligibility flags).
# pax is handled separately. We never forward arbitrary entities into the plan.
_CONTEXT_FLAGS = (
    "own_hotel", "non_standard_rooming", "special_luggage", "custom_route",
    "custom_addon", "non_standard_endpoint", "discount_exception",
)
_ENVELOPE_HANDOFF_STATUSES = {"unsupported", "handoff_required"}


def _select_package_key(envelope: dict[str, Any], trip_brief: dict[str, Any] | None) -> str | None:
    tb = trip_brief or {}
    for candidate in (tb.get("selected_package_key"), (envelope.get("entities") or {}).get("package_key")):
        if isinstance(candidate, str) and candidate:
            return candidate
    # Core's recommendation is only safe to auto-select when unambiguous (exactly one);
    # multiple recommendations are a discovery situation, not a single-package answer.
    recs = (envelope.get("feasibility") or {}).get("recommended_package_ids") or []
    if len(recs) == 1 and isinstance(recs[0], str) and recs[0]:
        return recs[0]
    return None


def _coerce_pax(value: Any) -> int | None:
    """A guest count as an int. Accepts a plain int (entities) or a TripBrief `pax`
    object {confirmed, tentative, adults, ...} (contracts/trip-brief.schema.json)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, dict):
        for key in ("confirmed", "tentative", "adults"):
            inner = value.get(key)
            if isinstance(inner, int) and not isinstance(inner, bool):
                return inner
    return None


def _resolve_pax(entities: dict[str, Any], trip_brief: dict[str, Any] | None) -> int | None:
    # Per-message entities take precedence over accumulated TripBrief state; the TripBrief
    # `pax` is an object so it must be normalized, not read as a flat int.
    tb = trip_brief or {}
    for candidate in (entities.get("pax"), entities.get("number_of_guests"), tb.get("pax"), tb.get("number_of_guests")):
        pax = _coerce_pax(candidate)
        if pax is not None:
            return pax
    return None


def _project_customer_context(envelope: dict[str, Any], trip_brief: dict[str, Any] | None) -> dict[str, Any]:
    entities = envelope.get("entities") or {}
    ctx: dict[str, Any] = {}
    pax = _resolve_pax(entities, trip_brief)
    if pax is not None:
        ctx["pax"] = pax
    # Flags are flat booleans in both; per-message entities take precedence.
    src = {**(trip_brief or {}), **entities}
    for flag in _CONTEXT_FLAGS:
        if src.get(flag):
            ctx[flag] = True
    return ctx


def _apply_handoff_floor(plan: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    """Honor the envelope's handoff as a hard floor (escalate only, never downgrade).

    Applied whenever the envelope requires handoff — including when the resolver ALREADY
    returned a handoff for another reason (e.g. custom quote): build_delivery_plan only
    strips price facts for route gaps, so an already-handoff plan can still carry a
    standard-price fact and the resolver's reason. We re-apply the cleanup unconditionally:
    handoff mode, no booking CTA, no price facts, and the envelope's reason takes precedence
    (falling back to the plan's existing reason). Re-validates the contract."""
    env_handoff = envelope.get("handoff") or {}
    needs_handoff = bool(env_handoff.get("required")) or envelope.get("intent_status") in _ENVELOPE_HANDOFF_STATUSES
    if not needs_handoff:
        return plan
    reason = (
        (env_handoff.get("reasons") or [None])[0]
        or (plan.get("handoff") or {}).get("reason")
        or f"intent_{envelope.get('intent_status')}"
    )
    floored = dict(plan)
    floored["message_mode"] = "handoff"
    floored["handoff"] = {"required": True, "reason": reason}
    floored["secondary_link_intent"] = None
    floored["resolved_secondary_link"] = None
    floored["short_facts"] = [f for f in plan.get("short_facts", []) if "price" not in f.lower()]
    floored["max_text_lines"] = MODE_MAX_LINES["handoff"]
    validate_or_raise(DELIVERY_PLAN_CONTRACT, floored)
    return floored


def delivery_plan_from_decision(
    release_dir: Path | str,
    decision_envelope: dict[str, Any],
    *,
    trip_brief: dict[str, Any] | None = None,
    query: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a DeliveryPlan from a DecisionEnvelope (+ optional TripBrief) and the one local
    release. The envelope remains authoritative for routing/safety/handoff; this is purely
    the presentation read on top of the existing resolver."""
    intent = decision_envelope.get("intent", "")
    job = derive_default_customer_job(intent, trip_brief, config or {}, query=query)
    package_key = _select_package_key(decision_envelope, trip_brief)
    customer_context = _project_customer_context(decision_envelope, trip_brief)
    plan = resolve_delivery_plan(
        release_dir, customer_job=job, query=query, package_key=package_key, customer_context=customer_context,
    )
    return _apply_handoff_floor(plan, decision_envelope)
