# Presentation / Resolver Layer (Phase D)

Turns an already-classified customer request into a **DeliveryPlan**: a short answer
plan with the right reusable modules, one resolved link, an optional resolved visual,
and one next action. It selects and resolves — it never authors customer copy, never
invents a URL or visual, and never computes price/availability.

```
customer_job + query + package_key + context
        │
        ▼  module_resolver      → which general modules + package variation refs
        ▼  link_resolver        → link_key → real URL (or "not sendable")
        ▼  asset_resolver       → visual_key → real asset (or "not sendable")
        ▼  presentation_resolver→ DeliveryPlan (quote gate + mode + length budget)
        ▼  contracts/delivery-plan.schema.json (validated)
```

## Modules

| Module | Role |
|---|---|
| `module_resolver.py` | Classify topic, select general modules + package variation refs. A variation is never used without its general-module baseline. |
| `link_resolver.py` | Resolve a link intent to a URL from `customer-link-registry.json`. Returns non-sendable (url=None) for missing/unknown links — **never invents a URL**. |
| `asset_resolver.py` | Resolve a visual intent to an asset from `customer-media-registry.json`. Today every asset is `to_create`, so nothing is sendable — **never invents a visual**. |
| `presentation_resolver.py` | Build the DeliveryPlan: message mode, length budget, quote-eligibility gate, link/visual wiring, disclosures, handoff. No booking CTA on a custom-quote case. |

## Inputs (data contracts it consumes)

- Module layer (knowledge-catalog `customer-sales-release`): `general-modules.json`,
  `package-variations.json`, `module-compatibility.json`.
- Website registries (`jvto-web/public`): `customer-link-registry.json`,
  `customer-media-registry.json`.

For offline tests these are vendored under `tests/fixtures/agent_modules/`.

## Acceptance criteria enforced (blueprint §3.F)

- No invented URLs (`link_resolver`) — only `status=existing` links are sendable.
- No invented visuals (`asset_resolver`) — `to_create` assets are never sendable.
- No direct booking CTA on a custom-quote case (`presentation_resolver`).
- No package variation without a general-module baseline (`module_resolver`).
- DeliveryPlan validates against `contracts/delivery-plan.schema.json`.

## Usage

```python
from jvto_agent_runtime.presentation_resolver import resolve_delivery_plan

plan = resolve_delivery_plan(
    release_root=".../okf/customer-sales-release/jvto",
    web_public_root=".../jvto-web/public",
    core_agent_contract_root=".../itinerary-core/generated/itinerary-intelligence",  # REQUIRED (fail-safe)
    customer_job="J2_price_and_value",
    query="How much for 4 guests?",
    package_key="bali/bromo-ijen-3d2n",
    customer_context={"pax": 4},
)
```

`core_agent_contract_root` is **required** on the end-to-end path: the policy is to fail
safe rather than price/booking without Core's route authority. An unknown/mismatched
`package_key` (even when a gate dict is supplied) resolves to `integrity=unknown` →
handoff. To use the ungated planner deliberately, call `build_delivery_plan(...,
route_gate=None)` directly.

Note: this layer adds presentation on top of the existing `ResponsePlan`/`DecisionEnvelope`;
it does not replace system routing, which stays the DecisionEnvelope's job.

## Route-integrity gate + booking authority (P0)

`route_gate.py` loads jvto-itinerary-core's agent-contract
(`package-customization-boundaries.json` + `package-operational-composition.json`) and,
per `package_key`, exposes `route_integrity` + `effective_instant_book_eligible`.
`build_delivery_plan(..., route_gate=...)` applies:

| Core signal | DeliveryPlan effect |
|---|---|
| `route_integrity == gap` / unknown package | `message_mode=handoff`, no standard price (price facts dropped), no booking CTA (`secondary_link_intent=None`), `quote_eligibility=custom_quote_required`, route-gap disclosure. |
| `effective_instant_book_eligible == false` | Same handoff/no-CTA (reason `instant_book_gated_by_core`). |
| `route_integrity == needs_review` | Standard price still allowed, but a route-validation disclosure is added and `route_integrity.requires_feasibility=true` (no "route confirmed" claim). |
| `route_integrity == clean` | Normal plan. |

**Booking authority = Core (Option A).** Core's `effective_instant_book_eligible` is the
single authoritative booking-eligibility contract; it **overrides** Bootstrap's advisory
`booking_mode.instant_book`. If Core says false, the runtime gives no booking CTA and
hands off even when Bootstrap says `instant_book: true`. Bootstrap keeps its value as
business-intent only. Unknown packages fail safe to handoff.
