"""DecisionEnvelope -> DeliveryPlan seam (presentation adapter).

Connects `/v1/decisions` (a DecisionEnvelope: routing + safety + handoff) to the
presentation layer (`/v1/delivery-plan`: a DeliveryPlan) WITHOUT building full
orchestration. It maps an already-built DecisionEnvelope (+ optional TripBrief) into
the presentation inputs the resolver needs, builds a DeliveryPlan from the one local
release, then honors the envelope's routing state as a hard floor (escalate only, never
downgrade) — handoff forces a handoff plan, and needs_information never presents a
committal price/booking answer. So the seam can never present a normal plan when the
system already decided to hand off or to collect missing fields first.

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
from .presentation_resolver import (
    CUSTOM_QUOTE_FLAGS,
    DELIVERY_PLAN_CONTRACT,
    MODE_MAX_LINES,
    resolve_delivery_plan,
)
from .sales_intelligence import derive_default_customer_job

# customer_context flags the presentation resolver understands (quote-eligibility flags).
# Derived from the resolver's own set so the two can't drift; pax is handled separately.
# We never forward arbitrary entities into the plan.
_CONTEXT_FLAGS = tuple(CUSTOM_QUOTE_FLAGS)
_ENVELOPE_HANDOFF_STATUSES = {"unsupported", "handoff_required"}
# Modes that present a committal price/booking answer; never appropriate when the envelope
# decided more information is needed first (intent_status=needs_information).
_COMMITTAL_MODES = {"standard_price", "booking_start"}


def _select_package_key(envelope: dict[str, Any], trip_brief: dict[str, Any] | None) -> str | None:
    tb = trip_brief or {}
    # Per-message entities take precedence over accumulated TripBrief state (consistent with
    # _project_customer_context): if the customer names a package THIS turn, it wins over a
    # previously-selected one.
    for candidate in ((envelope.get("entities") or {}).get("package_key"), tb.get("selected_package_key")):
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


def _apply_envelope_floor(plan: dict[str, Any], envelope: dict[str, Any]) -> dict[str, Any]:
    """Honor the envelope's routing state on top of the resolver plan (escalate only, never
    downgrade an existing handoff):

    - handoff (handoff.required, or intent_status unsupported/handoff_required): force handoff
      mode, no booking CTA, no price facts; the envelope's reason takes precedence (falling
      back to the plan's existing reason). Applied even when the resolver ALREADY handed off
      for another reason, because build_delivery_plan only strips price facts for route gaps.
    - needs_information: the envelope deliberately withheld a committal answer pending missing
      fields, so never present a standard price / booking CTA — downgrade a committal
      price/booking plan to a non-committal clarify with a follow-up.

    Re-validates the contract after mutating the plan."""
    env_handoff = envelope.get("handoff") or {}
    status = envelope.get("intent_status")
    needs_handoff = bool(env_handoff.get("required")) or status in _ENVELOPE_HANDOFF_STATUSES

    if needs_handoff:
        reason = (
            (env_handoff.get("reasons") or [None])[0]
            or (plan.get("handoff") or {}).get("reason")
            or f"intent_{status}"
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

    if status == "needs_information" and plan["message_mode"] in _COMMITTAL_MODES:
        floored = dict(plan)
        floored["message_mode"] = "quick_answer"
        floored["secondary_link_intent"] = None
        floored["resolved_secondary_link"] = None
        floored["short_facts"] = [f for f in plan.get("short_facts", []) if "price" not in f.lower()]
        floored["max_text_lines"] = MODE_MAX_LINES["quick_answer"]
        if not floored.get("follow_up_question"):
            floored["follow_up_question"] = "Could you share the missing details (e.g. travel date and number of guests) so I can help?"
        validate_or_raise(DELIVERY_PLAN_CONTRACT, floored)
        return floored

    return plan


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
    return _apply_envelope_floor(plan, decision_envelope)
