"""Milestone 1 — Customer Sales executor.

Consumes a published Customer Sales Release (projected into a source-locked agent release at
`<release_dir>/customer-sales/`) plus a ResponsePlan + TripBrief, and produces a contract-valid
ResolvedCustomerContext carrying ONLY resolved customer-facing facts.

It does not author data: it reads the published release, resolves the selected package, computes
the standard price + group total from published per-pax tiers, and degrades safely
(`unavailable` / `custom_quote_required`) when data is absent. It never invents a price or fact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import validate_or_raise
from .utils import read_json, utc_now

RESOLVED_CONTEXT_CONTRACT = "resolved-customer-context"
SUBDIR = "customer-sales"

# Standard, non-secret assumptions surfaced with every published-standard quote.
PRICE_ASSUMPTIONS = [
    "standard package route",
    "standard accommodation arrangement",
    "no non-standard add-on",
]
AVAILABILITY_DISCLOSURE = "Availability is not yet confirmed for the requested date."


class CustomerSalesExecutor:
    def __init__(self, release_dir: Path | str) -> None:
        self.base = Path(release_dir) / SUBDIR
        self.manifest = read_json(self.base / "release-manifest.json")
        self.release_id = self.manifest.get("release_id", "unknown")
        self.profiles = self._index("package-profiles.json")
        self.price_tiers = self._index("standard-price-tiers.json")
        self.components = self._index("component-matrices.json")
        self.endpoints = self._index("endpoint-chains.json")
        self.accommodation = self._index("accommodation-rules.json")
        self.vehicle = self._index("vehicle-and-luggage-rules.json")
        self.guide = self._index("guide-support-rules.json")

    def _index(self, name: str) -> dict[str, dict[str, Any]]:
        path = self.base / name
        if not path.exists():
            return {}
        return {rec["package_key"]: rec for rec in read_json(path) if rec.get("package_key")}

    # --- capability lookups ----------------------------------------------

    def catalog_lookup(self, package_key: str | None) -> dict[str, Any]:
        if not package_key:
            return {"status": "incomplete", "package_key": None}
        profile = self.profiles.get(package_key)
        if not profile:
            return {"status": "not_found", "package_key": package_key}
        comp = self.components.get(package_key, {})
        endpoint = self.endpoints.get(package_key, {})
        room = self.accommodation.get(package_key, {})
        veh = self.vehicle.get(package_key, {})
        gd = self.guide.get(package_key, {})
        endpoint_available = (endpoint.get("readiness", {}) or {}).get("endpoint_chain") == "available"
        room_available = (room.get("readiness", {}) or {}).get("rooming") == "available"
        return {
            "status": "resolved",
            "package_key": package_key,
            "itinerary_core_package_id": profile.get("itinerary_core_package_id"),
            "title": profile.get("title"),
            "description": profile.get("description"),
            "day_titles": profile.get("day_titles", []),
            "inclusions": {k: comp.get(k) for k in ("included", "excluded", "conditional")} if comp else None,
            "endpoint": {"standard_dropoff_options": endpoint.get("standard_dropoff_options", []),
                         "standard_pickup_options": endpoint.get("standard_pickup_options", []),
                         "pickup_details": endpoint.get("pickup_details", []),
                         "endpoint_options": endpoint.get("endpoint_options", []),
                         "bali_transfer": endpoint.get("bali_transfer"),
                         "finish_details": endpoint.get("finish_details", []),
                         "route_recommendations": endpoint.get("route_recommendations", []),
                         "note": endpoint.get("note")} if endpoint_available else None,
            "rooming": {"overnights": room.get("overnights", []),
                        "rooming_assumption": room.get("rooming_assumption"),
                        "staging_notes": room.get("staging_notes", [])} if room_available else None,
            "vehicle": {"vehicle_category": veh.get("vehicle_category")} if veh.get("vehicle_category") else None,
            "guide_support": {"crew_roles": gd.get("crew_roles"), "language_note": gd.get("language_note")} if gd.get("crew_roles") else None,
            "sourced_from": "published_customer_sales_release",
        }

    def standard_price_lookup(self, package_key: str | None, pax: int | None) -> dict[str, Any]:
        if not package_key:
            return {"status": "unavailable", "reason": "no_selected_package_key", "assumptions": []}
        rec = self.price_tiers.get(package_key)
        if not rec:
            return {"status": "unavailable", "reason": "no_published_price", "assumptions": []}
        if not pax or pax < 1:
            return {"status": "unavailable", "reason": "pax_required", "assumptions": [], "currency": rec.get("currency")}
        tiers = rec.get("pax_tiers", []) or []
        matched = next((t for t in tiers if t["min_pax"] <= pax and (t["max_pax"] is None or pax <= t["max_pax"])), None)
        if matched is None:
            min_pax = min((t["min_pax"] for t in tiers), default=None)
            return {"status": "custom_quote_required", "reason": "below_minimum_pax", "currency": rec.get("currency"),
                    "assumptions": [f"published tiers start at {min_pax} pax"]}
        per_person = int(matched["idr_per_person"])
        return {
            "status": "priced",
            "currency": rec.get("currency", "IDR"),
            "per_person": per_person,
            "group_total": per_person * pax,
            "pax": pax,
            "matched_tier": {"min_pax": matched["min_pax"], "max_pax": matched["max_pax"]},
            "price_type": rec.get("price_type", "published_standard"),
            "assumptions": list(PRICE_ASSUMPTIONS),
            "sourced_from": "published_customer_sales_release",
        }

    # --- orchestration ---------------------------------------------------

    def resolve(self, response_plan: dict[str, Any], trip_brief: dict[str, Any] | None = None) -> dict[str, Any]:
        trip_brief = trip_brief or {}
        package_key = trip_brief.get("selected_package_key")
        pax = (trip_brief.get("pax") or {}).get("confirmed")
        action_types = {a.get("type") for a in response_plan.get("required_actions", []) or []}

        catalog = self.catalog_lookup(package_key) if "catalog_lookup" in action_types or package_key else {"status": "incomplete", "package_key": package_key}
        if "price_quote" in action_types:
            pricing = self.standard_price_lookup(package_key, pax)
        else:
            pricing = {"status": "unavailable", "reason": "not_requested", "assumptions": []}

        disclosures = list(response_plan.get("required_disclosures", []) or [])
        if pricing.get("status") == "priced" and AVAILABILITY_DISCLOSURE not in disclosures:
            disclosures.append(AVAILABILITY_DISCLOSURE)

        handoff = response_plan.get("handoff", {}) or {}
        context = {
            "schema_version": "resolved-customer-context-v1",
            "release_id": self.release_id,
            "source_decision_id": response_plan.get("source_decision_id", ""),
            "selected_package_key": package_key,
            "trip_brief_status": response_plan.get("trip_brief_status", "not_applicable"),
            "catalog_resolved": catalog,
            "pricing_resolved": pricing,
            "required_disclosures": disclosures,
            "handoff": {"required": bool(handoff.get("required")), "reason": handoff.get("reason")},
            "created_at": utc_now(),
        }
        validate_or_raise(RESOLVED_CONTEXT_CONTRACT, context)
        return context
