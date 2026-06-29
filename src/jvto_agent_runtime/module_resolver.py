"""Module Resolver (Phase D / blueprint section 3.B).

Pure, deterministic selector that turns a customer topic (derived from the customer
job or message) + an optional package context into the set of *reusable general
modules* and *package variation refs* that answer it — so the WhatsApp model never
re-authors the private-tour / all-inclusive / vehicle / rooming / Ijen explanations.

Design rules (do not break):
- This module AUTHORS nothing. It only selects module_ids that already exist in the
  knowledge-catalog general-modules / package-variations projection.
- It is PURE: no network, no tool calls, no price/availability computation, no PII.
- A package variation is never used without its general-module baseline (acceptance
  criterion: "no package variation without a general module baseline").
- Unknown package_key or topic degrades gracefully to general modules only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import read_json

# Customer topics this resolver understands. A topic is the stable join key between
# an incoming message/job and the reusable module set that answers it.
TOPICS = (
    "inclusions", "price", "private_tour", "vehicle", "rooming", "hotel",
    "route_endpoint", "destination_readiness", "booking", "payment",
    "cancellation", "blue_fire", "greeting", "general",
)

# topic -> general module_ids that are always relevant for that topic (order = priority).
TOPIC_GENERAL_MODULES: dict[str, list[str]] = {
    "inclusions": ["inclusion_all_inclusive_baseline", "exclusion_standard"],
    "price": ["inclusion_all_inclusive_baseline", "service_private_tour_standard", "service_vehicle_by_pax"],
    "private_tour": ["service_private_tour_standard", "service_crew_language_standard"],
    "vehicle": ["service_vehicle_by_pax"],
    "rooming": ["service_standard_rooming"],
    "hotel": ["service_standard_rooming"],
    "route_endpoint": [],  # filled from package variation endpoints / staging
    "destination_readiness": [],  # filled from package destination_refs
    "booking": ["policy_booking_paths", "policy_anti_fraud"],
    "payment": ["policy_payment_deposit", "policy_anti_fraud", "policy_cancellation_travel_credit"],
    "cancellation": ["policy_cancellation_travel_credit"],
    "blue_fire": ["policy_natural_phenomena", "destination_ijen"],
    "greeting": [],
    "general": ["inclusion_all_inclusive_baseline", "service_private_tour_standard"],
}

# keyword -> topic (first match wins, scanned in this order)
_TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("price", ["how much", "price", "cost", "rate", "per person", "per pax", "budget"]),
    ("blue_fire", ["blue fire", "blue-fire", "bluefire"]),
    ("vehicle", ["vehicle", "car", "mpv", "hiace", "luggage", "suitcase", "transport"]),
    ("rooming", ["room", "twin", "double", "single", "rooming", "bed"]),
    ("hotel", ["hotel", "accommodation", "stay", "overnight", "homestay"]),
    ("private_tour", ["private", "shared", "join", "group tour", "guide", "driver"]),
    ("inclusions", ["include", "included", "inclusion", "what do we get", "all inclusive", "all-inclusive"]),
    ("route_endpoint", ["finish", "end in", "drop", "dropoff", "drop-off", "ketapang", "ferry", "bali", "airport"]),
    ("destination_readiness", ["ijen", "bromo", "tumpak", "madakaripura", "papuma", "difficult", "readiness", "prepare", "hike", "trek"]),
    ("payment", ["deposit", "pay", "payment", "transfer", "installment"]),
    ("cancellation", ["cancel", "refund", "reschedule", "travel credit"]),
    ("booking", ["book", "booking", "reserve", "how do i book", "instant"]),
    ("greeting", ["hello", "hi ", "halo", "good morning", "good evening"]),
]

# customer_job (response-plan vocabulary) -> default topic
_JOB_DEFAULT_TOPIC = {
    "J1_package_discovery": "general",
    "J2_price_and_value": "price",
    "J3_route_and_timing": "route_endpoint",
    "J4_live_confirmation": "booking",
    "J5_exception_and_handoff": "general",
    "greeting": "greeting",
    "unsupported": "general",
}


@dataclass(frozen=True)
class ModuleLayer:
    general: dict[str, dict[str, Any]]
    variations: dict[str, dict[str, Any]]
    compatibility: dict[str, Any]
    release_id: str


def load_module_layer(release_root: Path | str) -> ModuleLayer:
    """Load the general-module / package-variation projection from a directory that
    contains general-modules.json, package-variations.json, module-compatibility.json."""
    base = Path(release_root)
    general = {m["module_id"]: m for m in read_json(base / "general-modules.json")}
    variations = {v["package_key"]: v for v in read_json(base / "package-variations.json")}
    compat = read_json(base / "module-compatibility.json")
    release_id = compat.get("release_id", "unknown")
    return ModuleLayer(general=general, variations=variations, compatibility=compat, release_id=release_id)


def classify_topic(customer_job: str | None = None, query: str = "") -> str:
    low = (query or "").lower()
    for topic, needles in _TOPIC_KEYWORDS:
        if any(n in low for n in needles):
            return topic
    return _JOB_DEFAULT_TOPIC.get(customer_job or "", "general")


def _disclosures_for(topic: str, has_ijen: bool) -> list[str]:
    out: list[str] = []
    if topic == "price":
        out.append("Availability must be confirmed for your date.")
    if topic in ("blue_fire", "destination_readiness") and has_ijen:
        out.append("Sunrise and Ijen blue fire cannot be guaranteed (natural phenomena).")
    if topic == "destination_readiness" and has_ijen:
        out.append("Ijen access depends on authority/safety conditions and any required health screening.")
    if topic in ("vehicle",):
        out.append("Oversized or special luggage needs a live check before it is confirmed.")
    if topic in ("rooming", "hotel"):
        out.append("Exact rooming and upgrades are subject to confirmation.")
    return out


@dataclass(frozen=True)
class ResolvedModules:
    topic: str
    package_key: str | None
    general_module_refs: list[str]
    package_variation_refs: list[str]
    destination_refs: list[str]
    link_keys: list[str]
    visual_keys: list[str]
    required_disclosures: list[str]
    variation: dict[str, Any] | None


def resolve_modules(
    layer: ModuleLayer,
    *,
    customer_job: str | None = None,
    query: str = "",
    topic: str | None = None,
    package_key: str | None = None,
    customer_context: dict[str, Any] | None = None,
) -> ResolvedModules:
    topic = topic or classify_topic(customer_job, query)
    variation = layer.variations.get(package_key) if package_key else None
    tokens = set(variation["destination_tokens"]) if variation else set()
    has_ijen = "ijen" in tokens

    general_ids = list(TOPIC_GENERAL_MODULES.get(topic, []))

    dest_refs: list[str] = []
    var_refs: list[str] = []
    if variation:
        pv = variation["package_variations"]
        dest_refs = list(pv["destination_refs"])
        # destination readiness: add the package's destination modules + ijen policies
        if topic == "destination_readiness":
            general_ids += dest_refs
            if has_ijen:
                general_ids += ["policy_ijen_health_screening", "policy_ijen_monthly_closure"]
        # route/endpoint: add staging modules
        if topic == "route_endpoint":
            general_ids += pv["staging_refs"]
        # inclusion/price: surface the package's inclusion additions
        if topic in ("inclusions", "price", "general"):
            var_refs = list(pv["inclusion_additions"])
        # keep only general refs this package actually carries (baseline guarantee),
        # but always keep service/policy/destination/staging modules even if topic-injected.
        pkg_general = set(variation["general_module_refs"]) | set(dest_refs) | set(pv["staging_refs"])
        general_ids = [m for m in general_ids if m in pkg_general or m in layer.general]
    # de-dupe, drop unknown module ids (never reference a non-existent module)
    seen: set[str] = set()
    general_ids = [m for m in general_ids if m in layer.general and not (m in seen or seen.add(m))]

    # collect link/visual keys from the chosen modules (+ package page when relevant)
    link_keys: list[str] = []
    visual_keys: list[str] = []
    for mid in general_ids + var_refs:
        m = layer.general.get(mid, {})
        if m.get("link_key") and m["link_key"] not in link_keys:
            link_keys.append(m["link_key"])
        if m.get("visual_key") and m["visual_key"] not in visual_keys:
            visual_keys.append(m["visual_key"])

    return ResolvedModules(
        topic=topic,
        package_key=package_key,
        general_module_refs=general_ids,
        package_variation_refs=var_refs,
        destination_refs=dest_refs,
        link_keys=link_keys,
        visual_keys=visual_keys,
        required_disclosures=_disclosures_for(topic, has_ijen),
        variation=variation,
    )
