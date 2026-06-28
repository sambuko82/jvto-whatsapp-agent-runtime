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

## Phase 4 — Meta webhook  *(edge scaffolded)*

Implement Meta verification and signature checking in a dedicated edge adapter. It should:

- normalize Meta payloads;
- map conversation state to a privacy-safe customer context reference;
- classify intent and extract entities;
- call `/v1/decisions`;
- call only allowed tools;
- send the final response;
- write audit metadata.

**Status:** the security edge is scaffolded — see `docs/meta-webhook.md`.
`src/jvto_agent_runtime/meta_webhook.py` + the `GET`/`POST /webhooks/meta` endpoints handle
subscription verification, fail-closed HMAC signature checking, and PII-safe payload
normalization (raw sender id → opaque `context_ref`). **Still external/out of scope** (per
the ownership boundary): intent/entity classification, the Send API reply path, and durable
conversation state. Config via `JVTO_META_VERIFY_TOKEN` / `JVTO_META_APP_SECRET` /
`JVTO_META_CONTEXT_SALT` (environment only).

## Phase 5 — Deployment approval  *(scaffolded)*

Add an explicit release approval record external to this repository. No static build should set itself customer-ready. Deployment must be a separate operator decision after crosswalk, source, guardrail, and tool integration checks pass.

**Status:** scaffolded — see `docs/deployment-approval.md`.
`src/jvto_agent_runtime/deployment.py` provides the deployment gate, an external
signed-approval record (`contracts/deployment-approval.schema.json`, HMAC with the
operator-held `JVTO_DEPLOYMENT_APPROVAL_KEY`), and `verify_deployment_approval` —
the only path to `customer_traffic_ready: true`, requiring a valid signature AND a passing
gate AND a matching release fingerprint. The release file is never mutated. Surfaces:
`deployment-gate` / `create-deployment-approval` / `verify-deployment` CLI commands and
`POST /v1/deployment/{gate,verify}`. With the current upstreams the gate blocks on
`core_dataset_not_production_ready`, so traffic stays disabled until itinerary-core reaches
`production_ready`.
