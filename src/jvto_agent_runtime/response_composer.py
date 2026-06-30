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
    plan_handoff = plan["handoff"]["required"]
    price_forces_handoff = pricing["status"] == "custom_quote_required"
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
    price_surfaced = (message_mode != "handoff") and pricing["status"] == "priced"
    # A surfaced price always carries an availability disclosure — but the delivery plan
    # already adds one for price-topic answers, so only add ours if none is present.
    if price_surfaced and not any("availability" in d.lower() for d in disclosures):
        disclosures.append(AVAILABILITY_DISCLOSURE)

    # 5) Route safety carries Core's authority (already on the plan).
    route_safety = plan.get("route_integrity")

    # 6) Factual draft lines (assembled from resolved facts only — no marketing copy).
    lines: list[str] = []
    if catalog["status"] == "resolved" and catalog.get("title"):
        lines.append(catalog["title"])
    if price_surfaced:
        lines.append(
            f"From {_money(pricing.get('currency'), pricing['per_person'])} per person"
            + (f" for {pricing['pax']} guests (group total {_money(pricing.get('currency'), pricing['group_total'])})" if pricing.get("group_total") else "")
            + " — published standard price."
        )
    elif pricing["status"] == "custom_quote_required":
        lines.append("This request needs a custom quote; a team member will confirm price and availability.")
    primary = plan.get("resolved_primary_link")
    if primary and primary.get("sendable") and primary.get("url"):
        lines.append(f"Details: {primary['url']}")
    for d in disclosures:
        if d not in lines:
            lines.append(d)
    if needs_handoff and not any("team member" in l.lower() for l in lines):
        lines.append("A team member will follow up to confirm the details.")
    follow_up = plan.get("follow_up_question")
    if follow_up and not needs_handoff:
        lines.append(follow_up)

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
        "max_text_lines": _HANDOFF_MAX_LINES if message_mode == "handoff" else plan.get("max_text_lines", 4),
    }
    validate_or_raise(RESPONSE_DRAFT_CONTRACT, draft)
    return draft
