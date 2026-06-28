# Integration Plan

## Phase 1 — Source-locked build

- Clone both upstream repositories locally at known commits.
- Run `build-release`.
- Resolve every `knowledge_only` package crosswalk item before enabling route recommendations for that package.
- Inspect `core-capabilities.json`; treat every partial group as a response constraint or handoff trigger.

## Phase 2 — Itinerary Core API  *(scaffolded)*

Expose a narrow API around the existing scenario evaluator. The runtime request must satisfy `contracts/itinerary-core-request.schema.json`, and the API must respond with `contracts/itinerary-core-response.schema.json`.

The API should return customer-visible reasons separately from internal diagnostics. Do not expose raw seed rules or costs to the WhatsApp model.

**Status:** the runtime-side boundary is scaffolded — see `docs/feasibility-api.md`.
`src/jvto_agent_runtime/feasibility.py` provides the request builder, a pluggable
`ItineraryCoreEvaluator` interface, a safe default (`NotConnectedEvaluator`), and an
`HttpItineraryCoreEvaluator` integration point; `contracts.py` validates both ends.
Surfaces: `POST /v1/feasibility`, the `feasibility` CLI command, and
`build_decision(..., evaluator=...)`. **To finish Phase 2:** stand up the
itinerary-core feasibility service and connect `HttpItineraryCoreEvaluator` to it.

## Phase 3 — Live adapters  *(scaffolded)*

Implement explicit authenticated adapters for:

1. availability;
2. pricing;
3. booking status;
4. payment status;
5. operational notice;
6. hotel confirmation, if a reliable source exists.

Each adapter must return `contracts/live-tool-response.schema.json`. The agent may state live facts only when the adapter returns a current, successful response.

**Status:** the runtime-side boundary is scaffolded — see `docs/live-tools-api.md`.
`src/jvto_agent_runtime/live_tools.py` provides a `LiveToolAdapter` interface, the safe
default `NotConnectedLiveToolAdapter`, tool-policy enforcement, and the `execute_live_tool`
orchestrator (validated against the contract). Surfaces: `POST /v1/live-tools` and the
`live-tool` CLI command. **To finish Phase 3:** implement one authenticated adapter per
tool against the real transactional systems (requires external credentials).

## Phase 4 — Meta webhook

Implement Meta verification and signature checking in a dedicated edge adapter. It should:

- normalize Meta payloads;
- map conversation state to a privacy-safe customer context reference;
- classify intent and extract entities;
- call `/v1/decisions`;
- call only allowed tools;
- send the final response;
- write audit metadata.

## Phase 5 — Deployment approval

Add an explicit release approval record external to this repository. No static build should set itself customer-ready. Deployment must be a separate operator decision after crosswalk, source, guardrail, and tool integration checks pass.
