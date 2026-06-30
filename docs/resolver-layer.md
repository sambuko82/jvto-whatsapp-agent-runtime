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

At chat time the resolver reads **one** vendored directory, `<release>/agent-catalog/`,
not three live upstream roots (Runtime Monolith / PR-1 + PR-2). The build
(`build_release --web-root …`) vendors into that directory:

- Module layer (from knowledge-catalog `customer-sales-release`): `general-modules.json`,
  `package-variations.json`, `module-compatibility.json`.
- Core route gate (from jvto-itinerary-core): `agent-contract/package-customization-boundaries.json`,
  `agent-contract/package-operational-composition.json` (+ the rest of the agent-contract).
- Website capability registries (from `jvto-web/public`): `customer-link-registry.json`,
  `customer-media-registry.json`.
- `catalog-manifest.json`: counts + the module↔core crosswalk-integrity verdict.

`monolith_catalog.load_monolith_catalog(release_root)` is the single reader that loads
all of these into a `MonolithCatalogContext`. For offline unit tests the same files are
also vendored flat under `tests/fixtures/agent_modules/`, and the loaders accept either
a release root (with `agent-catalog/`) or that flat directory.

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
    "dist/releases/<id>",        # ONE compiled release — reads <id>/agent-catalog/ only
    customer_job="J2_price_and_value",
    query="How much for 4 guests?",
    package_key="bali/bromo-ijen-3d2n",
    customer_context={"pax": 4},
)
```

The end-to-end path takes **only the release root** — no jvto-web or jvto-itinerary-core
clone is read during a chat. The Core route gate is always vendored into the release, and
the policy is to fail safe rather than price/booking without it: an unknown/mismatched
`package_key` resolves to `integrity=unknown` → handoff. To use the ungated planner
deliberately, call `build_delivery_plan(..., route_gate=None)` directly.

### API + CLI surface

The supported runtime path is `POST /v1/delivery-plan` (and `jvto-agent delivery-plan`
for parity). Both read only the one local release's `agent-catalog/`.

```http
POST /v1/delivery-plan
{
  "release_dir": "dist/releases/<id>",
  "customer_job": "J2_price_and_value",
  "query": "How much for 4 guests?",
  "package_key": "bali/bromo-ijen-3d2n",
  "customer_context": {"pax": 4}
}
→ 200  a contract-valid DeliveryPlan (delivery-plan.schema.json)
→ 404  release dir not found, or no agent-catalog module layer (not a built release)
```

```bash
jvto-agent delivery-plan --release-dir dist/releases/<id> \
  --customer-job J2_price_and_value --query "How much for 4 guests?" \
  --package-key bali/bromo-ijen-3d2n --customer-context '{"pax": 4}'
```

The endpoint is a pure presentation read: it authors no copy, invents no URL/visual,
sources no live truth, and stays separate from `/v1/decisions` (routing/safety →
DecisionEnvelope) and `/v1/response-plan` (response requirements → ResponsePlan).

### Composed customer response (`/v1/customer-response`)

`response_composer.compose_customer_response(release_dir, decision_envelope, …)` fuses the
DeliveryPlan (presentation + Core route gate + sendable link) with the published catalog +
per-pax price from `<release>/customer-sales/` (via `CustomerSalesExecutor`) into one
contract-valid **CustomerResponseDraft** (`contracts/customer-response-draft.schema.json`) —
package facts + real price + route/booking safety + sendable link + disclosures + factual
`draft_lines`. Exposed as `POST /v1/customer-response` and `jvto-agent customer-response`.

State discipline (preserved, never invented): a concrete price shows **only** when
`message_mode != handoff` AND `price.status=priced`; `custom_quote_required` /
unknown-package (`not_found`) / route-gap escalate to handoff with no number; `needs_review`
surfaces the price WITH a route-validation disclosure; a surfaced price always carries an
availability (live-confirmation) disclosure.

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
