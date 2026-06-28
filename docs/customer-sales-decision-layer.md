# Customer Sales Decision Layer (PR 1)

A pure decision layer that sits on top of the existing runtime so the agent can reason about
an evolving sales conversation. It decides *what to do next*; it does not execute tools and it
does not author or store catalog/price/availability/customer data.

```text
Customer message
      ↓ (intent + entities classified upstream)
DecisionEnvelope  ← existing runtime (routing, knowledge, feasibility, live_tool_plan, handoff)
      ↓  + TripBrief (conversation state)
ResponsePlan      ← this layer (pure, deterministic)
      ↓
Tool executor (later PRs) → validated results → response context → LLM reply
```

## What it is (and is not)

- **Is:** customer-job classification, TripBrief merge/validate/status, and a small
  `ResponsePlan` derived from the `DecisionEnvelope`.
- **Is not:** a second router. The envelope stays the single source of truth for
  feasibility / live tools / handoff. The plan *mirrors* the envelope's handoff and may only
  **escalate** it, never downgrade.
- **Does not:** call any tool/adapter, compute price, or author catalog/availability data;
  store TripBrief as a customer database or any raw PII.

## Customer jobs (grouping over intents)

`config/customer-sales/routing-and-clarification.yaml` maps each intent to a **default** job,
then override rules shift it by topic/stage (one intent can serve several jobs):

| Job | Meaning | Default intents |
|---|---|---|
| J1_package_discovery | find a suitable standard package | query_package_details, query_destination_details, query_policy* |
| J2_price_and_value | price, inclusions, value | check_price (+ package/policy by topic) |
| J3_route_and_timing | route, endpoint, connection validation | plan_itinerary (+ package by topic) |
| J4_live_confirmation | availability / current status | check_availability, query_operational_notice |
| J5_exception_and_handoff | booking/payment status, complaints, refunds | get_booking_status, get_payment_status, complaint_or_refund, human_handoff_request |

`*query_policy` is cross-job (→ J2 pre-booking payment, → J5 post-booking cancel/refund).

## TripBrief (`contracts/trip-brief.schema.json`)

The evolving trip state: dates, pax, pickup/dropoff, destinations, attraction dependencies,
rooming, luggage, open questions, blockers. The runtime may receive/merge/validate/patch it
(`merge_trip_brief`) but **never persists** it — durable state lives in an external
conversation service keyed by `context_ref`. No name/email/phone/booking/payment fields.
Any change to a core field (date/pax/pickup/dropoff/destinations) marks the brief
`superseded_pending_revalidation`.

## ResponsePlan (`contracts/response-plan.schema.json`)

Small, customer-facing instruction: `customer_job`, `mode`
(`answer|clarify|execute_tool|handoff`), `trip_brief_status`, `approved_knowledge_ids`,
`required_actions[]` (`catalog_lookup|price_quote|itinerary_core|live_check`, each with a
reason), `clarifying_question`, `required_disclosures`, and `handoff{required,reason}`.
`required_actions` collapses what would otherwise duplicate the envelope's routing fields.

## Guardrails (`config/customer-sales/guardrails-and-state.yaml`)

- **Attraction hard-dependency** (e.g. "Blue Fire is the main reason") ⇒ mandatory `live_check`
  action + no-guarantee disclosure. Handoff only when a guarantee is demanded, the route needs
  a redesign outside the standard package, or access can't be confirmed — **not** automatically.
- **Itinerary Core triggers** are narrow: flight/train/ferry/airport deadline, non-standard
  pickup/dropoff, unspecified endpoint, changed sequence/duration, own-hotel staging change,
  partial service, or a changed core constraint. Fatigue/child/mobility ⇒ guidance/clarify;
  handoff only for serious medical/mobility.
- **Connection-time rule:** a connection mentioned without an exact time ⇒ clarify the time.
- **Handoff rules:** mandatory intents + caller-supplied signals (payment discrepancy, medical,
  cancellation/OTA, custom route, third-party, post-booking change, etc.); standard
  package/price/inclusion/booking questions stay agent-resolvable.

## Surfaces

- CLI: `build-response-plan --decision-envelope <file> [--trip-brief <file>] [--query ...]`,
  and `merge-trip-brief --base <file> --update <file>`.
- HTTP: `POST /v1/response-plan` `{decision_envelope, trip_brief?, query?, signals?}` → a
  `response-plan`.

## Research provenance

Derived from five customer-research artifacts (Evidence Extraction, Inquiry Ledger, Pattern
Register, Working Source Model, Blind-Spot Review). The raw files and inquiry ledger are kept
in an external private store and are **not** committed. Only derived config, contracts, and the
fully-redacted **synthetic** evaluation cases (`tests/customer-sales/evaluation-cases.jsonl`)
live here.

## Deferred (later PRs)

Sales Catalog & Price Quote adapters + result schemas + endpoints, Itinerary Core evaluator
wiring, live tool execution, and any persistent conversation store — built once upstream
publishes those sources. No empty adapters now.
