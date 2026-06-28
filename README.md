# JVTO WhatsApp Agent Runtime

A controlled integration and runtime boundary for a future **JVTO WhatsApp Meta agent**.

This repository does not author public knowledge, decide route truth, calculate final prices, or store customer data. It creates a version-locked agent release from two upstream repositories and turns an already-classified customer request into a safe **decision envelope**.

```text
knowledge-catalog-jvto-bootstrap ─┐
                                   ├─> agent release build ─> runtime decision envelope
jvto-itinerary-core ──────────────┘                              │
                                                                   ├─> WhatsApp response generator
Live transactional systems ── queried only at runtime ────────────┘
```

## Repository ownership

This repository owns:

- source release locking and validation;
- public knowledge projection from the JVTO OKF bundle;
- package, destination, and location crosswalks;
- intent-to-layer routing;
- tool-access policy;
- decision-envelope construction;
- response constraints and handoff decisions;
- runtime audit metadata.

It does **not** own:

- customer-facing knowledge authoring (`knowledge-catalog-jvto-bootstrap` owns this);
- operational route truth or scenario evaluation (`jvto-itinerary-core` owns this);
- live price, availability, booking, payment, hotel allocation, or notice truth;
- raw customer PII, payment receipts, API secrets, vendor rates, or internal financial data.

## Upstream model

| Upstream | Runtime use | Not used for |
|---|---|---|
| `sambuko82/knowledge-catalog-jvto-bootstrap` | Approved public concepts, policies, tour descriptions, destination information, trust evidence, response constraints | Raw snapshots, drafts, private source documents |
| `sambuko82/jvto-itinerary-core` | Package crosswalks, aliases, route capability metadata, feasibility request/response contracts, gap awareness | Final quote, payment status, unrestricted raw ops data |
| Transactional systems | Live availability, price, booking/payment/hotel status, operational notices | Static RAG source |

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Validate repository source contracts and tests
make validate
make test

# Build a source-locked release from local clones of both upstreams
python -m jvto_agent_runtime build-release \
  --knowledge-root /path/to/knowledge-catalog-jvto-bootstrap \
  --core-root /path/to/jvto-itinerary-core \
  --release-id agent-release-YYYYMMDD-001

# Validate the generated release
python -m jvto_agent_runtime validate-release \
  --release-dir dist/releases/agent-release-YYYYMMDD-001

# Produce a decision envelope from a classified intent and entities
python -m jvto_agent_runtime decide \
  --release-dir dist/releases/agent-release-YYYYMMDD-001 \
  --intent plan_itinerary \
  --query 'Can we do Tumpak Sewu, Bromo and Ijen from Surabaya and finish in Bali?' \
  --entities '{"pickup_location":"Surabaya","dropoff_location":"Bali","requested_destinations":["Tumpak Sewu","Bromo","Ijen"],"travel_date":"2026-08-10","number_of_guests":4,"pickup_time":"08:00","duration_days":4}'
```

## Build outputs

A release is written to `dist/releases/<release-id>/`:

```text
knowledge.ndjson                 Approved public knowledge only
retrieval-index.json             Search-oriented concept metadata
package-crosswalk.json           Knowledge package keys ↔ itinerary-core package IDs
location-crosswalk.json          Public terms ↔ canonical core locations
core-capabilities.json           Available core artifacts, gaps, restrictions
intent-routing.json              Runtime decision routing rules
guardrails.json                  Response and claim constraints
source-lock.json                 Repository revision + content hash evidence
release-manifest.json            Release status and blocking conditions
validation-report.json           Build and validation findings
```

## Release safety rules

A build may be generated even when it is not safe for customer traffic. Every release is marked `customer_traffic_ready: false` unless an explicit deployment approval step is later implemented.

The runtime must not:

- treat retrieval results as final factual proof;
- return prices from source files or cost components;
- promise Blue Fire, weather, sunrise, access, availability, or final booking state;
- send customer data to the embedding index;
- bypass the itinerary-core feasibility contract for route planning;
- call a live tool outside the intent's tool policy.

## Layout

```text
config/                  Source policy, routing, guardrails, tool and data ownership rules
contracts/               JSON Schema contracts between upstream, runtime, live tools, and LLM
src/                     Build pipeline, source readers, release validation, decision engine, API
scripts/                 Convenience shell entry points
tests/                   Fixtures and automated tests
docs/                    Architecture, source mapping, integration and deployment guidance
data/                    Gitignored local caches, audit logs, and generated source copies
dist/releases/           Generated agent releases (gitignored)
```

## Feasibility boundary (Phase 2)

Route feasibility flows through the itinerary-core contracts, not static knowledge:

```bash
# Evaluate feasibility for a release (default adapter returns unavailable + handoff
# until a real itinerary-core evaluator is connected)
python -m jvto_agent_runtime feasibility \
  --release-dir dist/releases/agent-release-YYYYMMDD-001 \
  --entities '{"pickup_location":"Surabaya","dropoff_location":"Bali","requested_destinations":["Tumpak Sewu","Bromo","Ijen"],"travel_date":"2026-08-10","number_of_guests":4,"pickup_time":"08:00","duration_days":4}'
```

`POST /v1/feasibility` returns an `itinerary-core-response`. `build_decision(...,
evaluator=...)` folds the result into the decision envelope's `feasibility` block.
See `docs/feasibility-api.md` for how to connect a real evaluator.

## Live tools boundary (Phase 3)

Live truth (price, availability, booking, payment, hotel, operational notice) comes only
from a current, successful live-tool response — never from static knowledge:

```bash
# Default adapter returns `unavailable` until a real authenticated adapter is connected
python -m jvto_agent_runtime live-tool \
  --release-dir dist/releases/agent-release-YYYYMMDD-001 \
  --tool pricing --intent check_price --params '{"package_key":"ijen-bromo-madakaripura-3d2n"}'
```

`POST /v1/live-tools` returns a `live-tool-response`. The boundary enforces tool-access
policy and degrades safely. See `docs/live-tools-api.md`.

## Meta integration boundary

`POST /v1/decisions` accepts a pre-classified intent, a customer query, and extracted entities. It returns a decision envelope. A separate Meta webhook adapter should authenticate Meta, normalize message payloads, call the intent/entity classifier, then invoke this endpoint. The response-generation model receives only the decision envelope; it never receives arbitrary repository files or free-form database access.
