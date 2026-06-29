"""Presentation Resolver (Phase D / blueprint sections 3.C–3.F).

Turns resolved modules + resolved link/asset into a DeliveryPlan: a short answer
plan with one relevant general module set, the right package variation refs, one
resolved link intent, an optional resolved visual, one next action, and a WhatsApp
length budget. Includes the quote-eligibility gate (blueprint 3.E).

Design rules (do not break):
- Authors no customer copy beyond short, factual one-liners assembled from existing
  module short_answers. The WhatsApp model writes the final wording.
- Never emits a fabricated URL/visual: it only forwards resolver results.
- Never gives a direct booking CTA on a custom-quote case.
- Output is validated against contracts/delivery-plan.schema.json.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .asset_resolver import MediaRegistry, load_media_registry, resolve_first_sendable as resolve_asset_first
from .contracts import validate_or_raise
from .link_resolver import LinkRegistry, load_link_registry, resolve_first_sendable as resolve_link_first, resolve_link
from .module_resolver import ModuleLayer, ResolvedModules, load_module_layer, resolve_modules

DELIVERY_PLAN_CONTRACT = "delivery-plan"

TOPIC_TO_MODE = {
    "inclusions": "inclusion_explanation",
    "price": "standard_price",
    "private_tour": "private_tour_explanation",
    "vehicle": "vehicle_explanation",
    "rooming": "rooming_explanation",
    "hotel": "hotel_overview",
    "route_endpoint": "route_endpoint_explanation",
    "destination_readiness": "destination_readiness",
    "booking": "booking_start",
    "payment": "quick_answer",
    "cancellation": "quick_answer",
    "blue_fire": "destination_readiness",
    "greeting": "quick_answer",
    "general": "quick_answer",
}
MODE_MAX_LINES = {
    "quick_answer": 3, "package_option": 4, "standard_price": 4, "inclusion_explanation": 4,
    "private_tour_explanation": 3, "vehicle_explanation": 3, "rooming_explanation": 3,
    "hotel_overview": 4, "route_endpoint_explanation": 4, "destination_readiness": 4,
    "live_status": 4, "booking_start": 4, "handoff": 3,
}

# context flags that force a custom quote (blueprint 3.E)
CUSTOM_QUOTE_FLAGS = {
    "own_hotel": "own_hotel",
    "non_standard_rooming": "non_standard_rooming",
    "special_luggage": "special_luggage",
    "custom_route": "custom_route",
    "custom_addon": "custom_addon",
    "non_standard_endpoint": "non_standard_endpoint",
    "discount_exception": "discount_exception",
}


def evaluate_quote_eligibility(topic: str, customer_context: dict[str, Any] | None) -> dict[str, Any]:
    """Blueprint 3.E quote eligibility gate."""
    ctx = customer_context or {}
    if topic != "price":
        return {"status": "not_a_price_request", "reasons": [], "required_disclosure": [], "next_action": None}
    reasons = [label for flag, label in CUSTOM_QUOTE_FLAGS.items() if ctx.get(flag)]
    if reasons:
        return {
            "status": "custom_quote_required",
            "reasons": reasons,
            "required_disclosure": ["This request needs a custom quote; a team member will confirm price and availability."],
            "next_action": "handoff_or_live_quote",
        }
    return {
        "status": "standard_price_eligible",
        "reasons": [],
        "required_disclosure": ["Availability must be confirmed for your date."],
        "next_action": None,
    }


def _short_facts(rm: ResolvedModules, layer: ModuleLayer, pax: int | None) -> list[str]:
    facts: list[str] = []
    var = rm.variation
    if var and var.get("private"):
        facts.append("Private tour")
    if rm.topic == "price":
        facts.append(f"Standard per-person price for {pax} guests" if pax else "Standard per-person price available")
        facts.append("Availability not yet confirmed")
    else:
        for mid in rm.general_module_refs[:2]:
            sa = layer.general.get(mid, {}).get("short_answer")
            if sa:
                facts.append(sa if len(sa) <= 140 else sa[:137] + "...")
    # de-dupe, cap at 4 (schema allows 6)
    out: list[str] = []
    for f in facts:
        if f not in out:
            out.append(f)
    return out[:4]


def _link_intents(rm: ResolvedModules) -> tuple[str | None, str | None]:
    """Pick primary + secondary link intents. Package page leads for price/discovery/booking."""
    page_key = rm.variation["public_page_key"] if rm.variation else None
    booking_key = page_key + "_booking" if page_key else None
    if rm.topic in ("price", "general", "booking") and page_key:
        return page_key, booking_key
    # explainer topics: lead with the module link, package page as secondary
    primary = rm.link_keys[0] if rm.link_keys else page_key
    secondary = page_key if (page_key and page_key != primary) else (rm.link_keys[1] if len(rm.link_keys) > 1 else None)
    return primary, secondary


def build_delivery_plan(
    layer: ModuleLayer,
    link_registry: LinkRegistry,
    media_registry: MediaRegistry,
    *,
    customer_job: str | None = None,
    query: str = "",
    package_key: str | None = None,
    customer_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rm = resolve_modules(layer, customer_job=customer_job, query=query, package_key=package_key, customer_context=customer_context)
    ctx = customer_context or {}
    pax = ctx.get("pax")

    quote = evaluate_quote_eligibility(rm.topic, customer_context)
    handoff_required = quote["status"] == "custom_quote_required"

    mode = "handoff" if handoff_required else TOPIC_TO_MODE.get(rm.topic, "quick_answer")
    if rm.topic == "general" and rm.variation and not handoff_required:
        mode = "package_option"

    primary_intent, secondary_intent = _link_intents(rm)
    # resolve links: primary prefers the chosen intent, then any sendable module link
    primary_link = resolve_link(link_registry, primary_intent) if primary_intent else None
    if primary_link is None or not primary_link.sendable:
        alt = resolve_link_first(link_registry, [k for k in ([primary_intent] if primary_intent else []) + rm.link_keys if k])
        primary_link = alt or primary_link
    # On a custom-quote case, do NOT offer a direct booking CTA (acceptance criterion).
    if handoff_required:
        secondary_intent = None
    secondary_link = resolve_link(link_registry, secondary_intent) if secondary_intent else None

    visual = resolve_asset_first(media_registry, rm.visual_keys)

    follow_up = None
    if mode == "handoff":
        follow_up = None
    elif rm.topic == "route_endpoint" and rm.variation:
        follow_up = "Where would you like to finish (airport, hotel area, or Bali side)?"
    elif not package_key and rm.topic in ("price", "general"):
        follow_up = "Which package and how many guests?"

    disclosures = list(rm.required_disclosures)
    for d in quote["required_disclosure"]:
        if d not in disclosures:
            disclosures.append(d)

    def link_dict(lr):
        if lr is None:
            return None
        return {"link_key": lr.link_key, "url": lr.url, "status": lr.status,
                "content_type": lr.content_type, "sendable": lr.sendable, "fallback_url": lr.fallback_url}

    plan = {
        "schema_version": "delivery-plan-v1",
        "message_mode": mode,
        "topic": rm.topic,
        "package_key": package_key,
        "short_facts": _short_facts(rm, layer, pax),
        "general_module_refs": rm.general_module_refs,
        "package_variation_refs": rm.package_variation_refs,
        "primary_link_intent": primary_intent,
        "secondary_link_intent": secondary_intent,
        "resolved_primary_link": link_dict(primary_link),
        "resolved_secondary_link": link_dict(secondary_link),
        "visual_intent": rm.visual_keys[0] if rm.visual_keys else None,
        "resolved_visual": (
            None if visual is None
            else {"asset_id": visual.asset_id, "url": visual.url, "status": visual.status,
                  "sendable": visual.sendable, "tier": visual.tier}
        ),
        "follow_up_question": follow_up,
        "required_disclosures": disclosures,
        "quote_eligibility": quote,
        "handoff": {"required": handoff_required, "reason": "custom_quote_required" if handoff_required else None},
        "max_text_lines": MODE_MAX_LINES.get(mode, 3),
    }
    validate_or_raise(DELIVERY_PLAN_CONTRACT, plan)
    return plan


def resolve_delivery_plan(
    release_root: Path | str,
    web_public_root: Path | str,
    *,
    customer_job: str | None = None,
    query: str = "",
    package_key: str | None = None,
    customer_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """End-to-end convenience: load the module layer + registries from disk and build a plan."""
    layer = load_module_layer(release_root)
    links = load_link_registry(web_public_root)
    media = load_media_registry(web_public_root)
    return build_delivery_plan(layer, links, media, customer_job=customer_job, query=query,
                               package_key=package_key, customer_context=customer_context)
