# Live tools API (Phase 3)

The runtime side of the live transactional boundary. Live truth — price, availability,
booking, payment, hotel, operational notice — may ONLY come from a current, successful
live-tool response, never from released static knowledge. This module enforces that.

## Module

- `src/jvto_agent_runtime/live_tools.py` — known tools, the live-tool-response builder, a
  pluggable `LiveToolAdapter`, the safe default `NotConnectedLiveToolAdapter`, and the
  `execute_live_tool` orchestrator.

## Flow

`execute_live_tool(release_dir, tool, params, intent=None, adapter=None)`:

1. rejects an unknown tool (`UnknownToolError`);
2. enforces the tool-access policy from the release's `tool-policy.json` — if `intent` is
   given and the tool's `allowed_intents` does not include it, returns a contract-valid
   `error` response (`tool_not_allowed_for_intent`);
3. calls the adapter (default `NotConnectedLiveToolAdapter`);
4. validates the reply against `contracts/live-tool-response.schema.json` and checks the
   `tool` matches the request.

Every failure path (adapter exception, contract violation, tool mismatch) degrades to a
contract-valid `unavailable` response. The orchestrator never raises into the agent
(`UnknownToolError` is raised only for a programming/routing mistake and is surfaced as a
400 by the API).

## Adapters

- `NotConnectedLiveToolAdapter` (default) — returns `unavailable` for every tool. Keeps the
  runtime self-contained; the agent can never present a fabricated live fact.
- Real adapters (Phase 3 implementation) — one authenticated adapter per tool (pricing,
  availability, booking_status, payment_status, hotel, operational_notice). These require
  external credentials/endpoints and are out of scope for the scaffold. Each must return a
  `live-tool-response` and should degrade to `unavailable`/`error` on failure.

## Surfaces

- HTTP: `POST /v1/live-tools` `{ "release_dir": "...", "tool": "...", "params": {...}, "intent": "..." }`
  → a `live-tool-response`.
- CLI: `python -m jvto_agent_runtime live-tool --release-dir <dir> --tool pricing --intent check_price --params '{...}'`.

## Presentation policy

`tool_policy_for(release_dir, tool)` returns the tool's policy
(`confirmation_required`, `output_must_include`, `prohibited_output_claims`,
`require_authenticated_customer_context`). The response-generation layer must honor it
when surfacing a live result to the customer — only a `status: "available"` (or relevant)
response with the required fields may back a live claim.
