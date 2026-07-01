"""Customer response composer.

The one capability that turns `DecisionEnvelope + TripBrief + one compiled release`
into a single customer-ready package response draft:

    package facts + published price + Core route/booking safety + sendable Web link
    → a safe, contract-valid CustomerResponseDraft

It composes the two existing halves verbatim — it authors no data and adds no new
truth source:
- `delivery_adapter.delivery_plan_from_decision` → presentation, the sendable link,
  the Core route gate (route_integrity / effective_instant_book_eligible), and the
  escalate-only handoff floor (handoff / needs_information).
- `customer_sales_executor.CustomerSalesExecutor` → real catalog facts + the published
  per-pax price, both from `<release>/customer-sales/`.

State discipline (all preserved, never invented):
- unknown package          → `package.status=not_found` → handoff.
- price below min / flagged → `price.status=custom_quote_required` → handoff, no number.
- price not requestable     → `price.status=unavailable` (e.g. pax_required) → ask, no number.
- route gap / unknown       → handoff (from the delivery plan), no price surfaced.
- route needs_review        → price may surface WITH the route-validation disclosure.
- availability              → always a live-confirmation disclosure on a surfaced price.
A concrete price is shown ONLY when `message_mode != handoff` and `price.status=priced`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import validate_or_raise
from .customer_sales_executor import AVAILABILITY_DISCLOSURE, CustomerSalesExecutor
from .delivery_adapter import _resolve_pax, delivery_plan_from_decision

RESPONSE_DRAFT_CONTRACT = "customer-response-draft"
_HANDOFF_MAX_LINES = 3


def _money(currency: str | None, amount: int) -> str:
    return f"{currency or 'IDR'} {amount:,}"


# Topics for which a concrete price may be surfaced. Every other topic answers from its
# own scoped rule and never carries a price line ("price only for price-relevant inquiries").
PRICE_RELEVANT_TOPICS = {"price", "booking"}


def _topic_fact(topic: str | None, catalog: dict[str, Any]) -> tuple[str | None, list[str]]:
    """One topic-scoped body line + any live-condition disclosures, drawn only from
    resolved catalog facts. A vehicle question answers vehicle rules, a hotel question
    answers standard overnights, an endpoint question answers package-valid endpoint
    options — never a generic blob. Returns (line, disclosures). Never invents: a field
    with no resolved value yields no line."""
    if catalog.get("status") != "resolved":
        return None, []
    disc: list[str] = []
    ep = catalog.get("endpoint") or {}

    if topic == "route_endpoint":
        parts: list[str] = []
        pickups = ep.get("standard_pickup_options") or []
        if pickups:
            parts.append("Pickup: " + ", ".join(pickups))
        # split classified endpoint options: settled standards vs live-arrangement ones
        opts = ep.get("endpoint_options") or []
        standard = [o["option"] for o in opts if o.get("classification") == "final_jvto_standard"]
        live = [o["option"] for o in opts if o.get("classification") == "live_condition"]
        if not standard:
            standard = ep.get("standard_dropoff_options") or []
        if standard:
            parts.append("Standard finish: " + ", ".join(standard))
        for opt in live:
            disc.append(f"{opt} is a live arrangement confirmed before booking, not a standard endpoint.")
        bt = ep.get("bali_transfer") or {}
        if bt.get("crosses_boundary") and bt.get("note"):
            disc.append(bt["note"])
        # Package/route recommendations (Core's 12-recommendation-rules.json, condition-
        # matched per package) — only the live_condition ones are endpoint-relevant here
        # (e.g. the Ketapang/Bali ferry pre-booking + queue buffer); a final_jvto_standard
        # entry just confirms the route already avoids a risk, nothing to disclose.
        for rec in ep.get("route_recommendations") or []:
            if rec.get("classification") == "live_condition" and rec.get("rule_id") == "ferry_bali_buffer_required":
                disc.append(rec["note"])
        return ("; ".join(parts) if parts else None), disc

    # blue_fire shares the destination_readiness presentation mode (TOPIC_TO_MODE /
    # _disclosures_for both already treat them as equivalent) — a "can we see blue fire at
    # Ijen?" query must not lose the Ijen access-risk disclosure just because module_resolver
    # classified it as blue_fire before it could become destination_readiness.
    if topic in ("destination_readiness", "blue_fire"):
        # Ijen's live access/quota/closure risk (Core's ijen_access_closure_risk rule) was
        # previously consumed only by the internal CLI scenario evaluator and never reached
        # a customer-facing disclosure; it is already condition-matched per package on the
        # same endpoint catalog fact this topic can read.
        for rec in ep.get("route_recommendations") or []:
            if rec.get("classification") == "live_condition" and rec.get("rule_id") == "ijen_access_closure_risk":
                disc.append(rec["note"])
        return None, disc

    if topic == "vehicle":
        veh = catalog.get("vehicle") or {}
        cat = veh.get("vehicle_category")
        return (f"Vehicle: {cat}" if cat else None), disc

    if topic == "hotel":
        room = catalog.get("rooming") or {}
        overnights = room.get("overnights") or []
        # Staging operational notes (why this overnight, what it prepares for) were
        # previously computed by Core (agent-contract/staging-logic.json) but never reached
        # a customer-facing disclosure; already classified per package on this same fact.
        # required_disclosures has no max_text_lines cap (unlike the body), so a multi-stop
        # package's full note list (e.g. 18 notes across 5 staging areas on a 6D5N package)
        # would bury the actual overnight answer — surface only the single most relevant note
        # per staging area, capped at 3 total.
        staging_notes = [n for s in (room.get("staging_notes") or []) for n in (s.get("operational_notes") or [])[:1]]
        disc.extend(staging_notes[:3])
        return ("Standard overnights: " + ", ".join(overnights) if overnights else None), disc

    if topic == "rooming":
        room = catalog.get("rooming") or {}
        return (room.get("rooming_assumption"), disc)

    if topic == "private_tour":
        gd = catalog.get("guide_support") or {}
        return (gd.get("crew_roles"), disc)

    if topic == "inclusions":
        inc = (catalog.get("inclusions") or {}).get("included") or []
        return ("Includes: " + ", ".join(inc[:4]) if inc else None, disc)

    return None, disc


def compose_customer_response(
    release_dir: Path | str,
    decision_envelope: dict[str, Any],
    *,
    trip_brief: dict[str, Any] | None = None,
    query: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose one CustomerResponseDraft from a single compiled release."""
    # 1) Presentation + link + Core route gate + handoff floor (reused verbatim).
    plan = delivery_plan_from_decision(
        release_dir, decision_envelope, trip_brief=trip_brief, query=query, config=config,
    )
    package_key = plan["package_key"]
    pax = _resolve_pax(decision_envelope.get("entities") or {}, trip_brief)

    # 2) Real catalog facts + published price for the SAME package/pax (reused verbatim).
    executor = CustomerSalesExecutor(release_dir)
    catalog = executor.catalog_lookup(package_key)
    pricing = executor.standard_price_lookup(package_key, pax)

    # 3) Unify state (escalate only): the delivery plan already handed off for route/quote/
    #    needs_information; add the catalog/price states it cannot see.
    #    Price is topic-scoped: only a price-relevant inquiry may surface a price OR escalate
    #    because of a price constraint (a vehicle/hotel/endpoint question must not hand off
    #    just because pax is below the price minimum).
    topic = plan.get("topic")
    price_relevant = topic in PRICE_RELEVANT_TOPICS
    plan_handoff = plan["handoff"]["required"]
    price_forces_handoff = price_relevant and pricing["status"] == "custom_quote_required"
    catalog_forces_handoff = catalog["status"] == "not_found"
    needs_handoff = plan_handoff or price_forces_handoff or catalog_forces_handoff

    message_mode = "handoff" if needs_handoff else plan["message_mode"]
    handoff_reason = (
        plan["handoff"]["reason"]
        or (pricing.get("reason") if price_forces_handoff else None)
        or ("package_not_found" if catalog_forces_handoff else None)
    )

    # 4) Disclosures: union of the plan's + the availability disclosure on a surfaced price.
    disclosures = list(plan.get("required_disclosures", []))
    # A concrete price is committal: never surface one on handoff, and never while the
    # envelope still needs information (the delivery plan already stripped it — mirror that
    # here so the composer can't resurface a price before required fields are collected).
    needs_information = decision_envelope.get("intent_status") == "needs_information"
    price_surfaced = price_relevant and (message_mode != "handoff") and not needs_information and pricing["status"] == "priced"
    # A surfaced price always carries an availability disclosure — but the delivery plan
    # already adds one for price-topic answers, so only add ours if none is present.
    if price_surfaced and not any("availability" in d.lower() for d in disclosures):
        disclosures.append(AVAILABILITY_DISCLOSURE)

    # 5) Route safety carries Core's authority (already on the plan).
    route_safety = plan.get("route_integrity")

    # 6) Factual message BODY lines (assembled from resolved facts only — no marketing copy).
    #    Disclosures are NOT duplicated here; they stay in required_disclosures (the model
    #    appends them), so the body honors max_text_lines like the delivery plan's short_facts.
    lines: list[str] = []
    if catalog["status"] == "resolved" and catalog.get("title"):
        lines.append(catalog["title"])
    # Topic-scoped fact: answer the actual question (vehicle rules, standard overnights,
    # package-valid endpoints, …) from resolved catalog facts, with any live-condition
    # disclosures kept in required_disclosures (never asserted as settled fact).
    topic_line, topic_disclosures = _topic_fact(topic, catalog)
    for d in topic_disclosures:
        if d not in disclosures:
            disclosures.append(d)
    if topic_line and not needs_handoff:
        lines.append(topic_line)
    if price_surfaced:
        lines.append(
            f"From {_money(pricing.get('currency'), pricing['per_person'])} per person"
            + (f" for {pricing['pax']} guests (group total {_money(pricing.get('currency'), pricing['group_total'])})" if pricing.get("group_total") else "")
            + " — published standard price."
        )
    elif price_relevant and pricing["status"] == "custom_quote_required":
        lines.append("This request needs a custom quote; a team member will confirm price and availability.")
    primary = plan.get("resolved_primary_link")
    if primary and primary.get("sendable") and primary.get("url"):
        lines.append(f"Details: {primary['url']}")
    if needs_handoff and not any("team member" in l.lower() for l in lines):
        lines.append("A team member will follow up to confirm the details.")
    follow_up = plan.get("follow_up_question")
    if follow_up and not needs_handoff:
        lines.append(follow_up)

    # The composed draft's budget covers its actual body (never fewer lines than it has),
    # floored at the plan's mode budget and capped at the contract maximum.
    body_budget = _HANDOFF_MAX_LINES if message_mode == "handoff" else plan.get("max_text_lines", 4)
    max_text_lines = min(8, max(body_budget, len(lines)))

    draft = {
        "schema_version": "customer-response-draft-v1",
        "release_id": executor.release_id,
        "source_decision_id": decision_envelope.get("decision_id", ""),
        "package_key": package_key,
        "customer_job": None,
        "message_mode": message_mode,
        "package": catalog,
        "price": {**pricing, "surfaced": price_surfaced},
        "route_safety": route_safety,
        "link": primary,
        "visual": plan.get("resolved_visual"),
        "short_facts": plan.get("short_facts", []),
        "module_refs": {
            "general": plan.get("general_module_refs", []),
            "package": plan.get("package_variation_refs", []),
        },
        "required_disclosures": disclosures,
        "follow_up_question": follow_up if not needs_handoff else None,
        "handoff": {"required": needs_handoff, "reason": handoff_reason},
        "draft_lines": lines,
        "draft_text": "\n".join(lines),
        "max_text_lines": max_text_lines,
    }
    validate_or_raise(RESPONSE_DRAFT_CONTRACT, draft)
    return draft
