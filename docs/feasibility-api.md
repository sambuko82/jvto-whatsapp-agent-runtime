# Feasibility API (Phase 2)

The runtime side of the route-feasibility contract. This is the seam between an
already-classified `plan_itinerary` request and the deterministic route truth that
lives in `jvto-itinerary-core`. The runtime never decides feasibility itself — it
builds a schema-valid request, delegates to a pluggable evaluator, and validates the
reply.

## Modules

- `src/jvto_agent_runtime/contracts.py` — load + validate instances against the
  JSON Schemas in `contracts/` (`is_valid`, `iter_contract_errors`, `validate_or_raise`).
- `src/jvto_agent_runtime/feasibility.py` — request builder, evaluator interface,
  default + HTTP adapters, and the `evaluate_feasibility` orchestrator.

## Flow

1. `build_itinerary_core_request(entities)` maps classified entities to an
   `itinerary-core-request-v1` and validates it. Missing required fields raise
   `MissingEntitiesError` (so the agent can ask for specific fields, not a schema error).
2. An `ItineraryCoreEvaluator` turns the request into an `itinerary-core-response-v1`.
3. `evaluate_feasibility(release_dir, entities, evaluator=None)` checks the
   `scenario_feasibility_contract` capability on the release, builds the request,
   calls the evaluator, and **always** returns a contract-valid response.

Every failure path (capability unavailable, missing entities, evaluator error,
contract violation) degrades to `status: "unavailable"` + `handoff_required: true`,
with the cause recorded in `known_gaps`. The runtime never raises into the agent.

## Evaluator adapters

- `NotConnectedEvaluator` (default) — returns `unavailable` + handoff. Keeps the
  runtime self-contained until a real evaluator exists. This is the current scaffold.
- `HttpItineraryCoreEvaluator(base_url)` — Phase 2 integration point: POSTs the
  request to a running itinerary-core feasibility API and validates the response.
  Not exercised by the test suite (needs a live service); transport/contract errors
  degrade safely.

To wire in real feasibility, implement the itinerary-core feasibility API (around its
existing scenario evaluator), then pass `HttpItineraryCoreEvaluator(<url>)` (or an
in-process adapter) into `build_decision(..., evaluator=...)` / `evaluate_feasibility`.

## Surfaces

- HTTP: `POST /v1/feasibility` `{ "release_dir": "...", "entities": {...} }` →
  an `itinerary-core-response`.
- CLI: `python -m jvto_agent_runtime feasibility --release-dir <dir> --entities '{...}'`.
- Decision envelope: `build_decision(..., evaluator=...)` fills
  `feasibility.status` (`feasible` / `conditional` / `not_feasible` / `unavailable`)
  and adds `recommended_package_ids`, `customer_visible_reasons`, and
  `source_release_id` to the envelope's `feasibility` block. Without an evaluator the
  envelope stays at `not_evaluated` (the pre-Phase-2 behavior).

## Customer vs internal

The response contract separates `customer_visible_reasons` (safe to surface to the
WhatsApp model) from `known_gaps` (internal diagnostics). Only the former may reach
the customer.
