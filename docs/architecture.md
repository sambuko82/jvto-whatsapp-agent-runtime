# Architecture

## Objective

The JVTO WhatsApp agent is a governed decision and communication layer. It is not a general-purpose FAQ bot and is not permitted to infer operational feasibility or transactional facts without the relevant controlled source.

```text
Customer message
  → Meta adapter
  → intent + entity extraction
  → agent runtime decision envelope
      ├─ approved public knowledge release
      ├─ itinerary-core feasibility contract
      └─ authorized live-system tools
  → response generator
  → response audit + optional human handoff
```

## Direction of authority

```text
Original evidence / upstream system
       ↓
Knowledge Catalog curation and public OKF bundle
       ↓
Agent release projection
       ↓
Decision envelope
       ↓
LLM response wording
```

For operational planning, direction is separate:

```text
Operational exports + manual rules
       ↓
jvto-itinerary-core deterministic compiler
       ↓
feasibility request / response contract
       ↓
Agent decision envelope
       ↓
LLM response wording
```

The runtime must not reverse these directions. It must not treat a generated response as a source, nor let a public marketing concept overwrite a deterministic operational warning.

## Layer boundary

| Layer | Authority | Runtime role |
|---|---|---|
| Knowledge Catalog | Public approved claims | Retrieval and response constraints |
| Itinerary Core | Route and feasibility intelligence | Scenario validation, aliases, operational caveats |
| Live systems | Current transactional state | Tool response only |
| Agent runtime | Controlled orchestration | Join bounded outputs, log decision, route handoff |
| LLM | Wording and comprehension | Never source-of-truth or direct database client |

## Release flow

1. Check the upstream repositories out at explicit revisions.
2. Build a release with `build-release`.
3. Validate output and inspect crosswalk mismatches.
4. Keep the release at `customer_traffic_ready: false` until a human deployment approval workflow exists.
5. Deploy the release read-only to the agent runtime.
6. Record release IDs and tool response metadata per decision.
