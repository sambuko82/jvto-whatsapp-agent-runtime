# JVTO WhatsApp Agent Runtime — Working Contract

> **Start each milestone by reading [`docs/project-context.md`](docs/project-context.md)** —
> the persistent milestone log, decisions, open items, and operating (completion) loop.
> Update it at the end of each milestone. This file is the working contract (rules); that
> file is the running context.

This repository is a **governed integration/runtime boundary**. It does not author
public knowledge, decide route truth, calculate prices, or store customer data. It
builds a version-locked *agent release* from two upstream repos and turns an
already-classified customer request into a safe **decision envelope**.

## Ownership boundary

This repo OWNS: source release locking + validation, public-knowledge projection,
package/destination/location crosswalks, intent→layer routing, tool-access policy,
decision-envelope construction, the feasibility request/response boundary, response
constraints, handoff decisions, and runtime audit metadata.

It does NOT own (do not implement these here):
- public knowledge authoring → `knowledge-catalog-jvto-bootstrap`
- route truth / scenario evaluation → `jvto-itinerary-core`
- live price, availability, booking, payment, hotel, or notice truth → live tools
- raw customer PII, payment data, vendor rates, API secrets

## Hard rules

- **Static knowledge is never live truth.** Never answer price, availability,
  booking, payment, hotel, or operational-notice from released knowledge — those
  require a current, successful live-tool response. The guardrails/constraints in
  every decision envelope enforce this; keep them intact.
- **Feasibility is the itinerary-core contract, not a guess.** Route feasibility
  flows through `contracts/itinerary-core-request.schema.json` →
  `contracts/itinerary-core-response.schema.json`. The runtime builds and validates
  the request; it must not compute route truth itself.
- **`customer_traffic_ready` is never self-set true.** `release-policy.yaml` requires
  external manual deployment approval. A release is always written with
  `customer_traffic_ready: false`; the approval gate is Phase 5 (not yet built).
- **`contracts/*.schema.json` are the source of truth.** When code emits or consumes
  an envelope/request/response, validate against the contract (`contracts.py`).
- **Customer vs internal split.** In an itinerary-core response, only
  `customer_visible_reasons` may reach the WhatsApp model; `known_gaps` is internal.

## Generated releases

- `dist/releases/<id>/` is **gitignored** by design. Only the provenance subset
  (`release-manifest.json`, `source-lock.json`, `validation-report.json`) may be
  committed, and only with explicit approval, via `git add -f`. The data payload
  (`knowledge.ndjson`, retrieval index, crosswalks, capabilities) stays regenerable.

## Committed local catalog (`catalog/`)

- `catalog/` is the **committed compact catalog**: the chat-time read path
  (`agent-catalog/` + the published `customer-sales/` subtree) vendored from the upstream
  repos so a **single checkout serves responses without the sibling repositories**. It is
  deliberately committed (unlike `dist/releases/`), holds the real 16-package data
  (variations, price tiers, route integrity, web link status) + a `provenance.json`, and is
  what `/v1/customer-response` and `jvto-agent customer-response` read by default.
- It is **regenerable, not hand-edited**: refresh it with
  `jvto-agent build-local-catalog --knowledge-root <kc> --core-root <core> --web-root <web>`
  (deterministic, byte-stable). It vendors only the chat-time subset — no `knowledge.ndjson`
  / retrieval index / crosswalks (those remain a full `build-release` concern).

## Required validation (before commit)

```bash
python -m pytest                      # NOT bare `pytest` — see note below
python -m jvto_agent_runtime validate-repo
# When upstream clones are available locally:
python -m jvto_agent_runtime build-release --knowledge-root <kc> --core-root <core> --release-id <id> --overwrite
python -m jvto_agent_runtime validate-release --release-dir dist/releases/<id>
```

> Use `python -m pytest`, not bare `pytest`: in some environments the `pytest` on
> PATH is an isolated tool install without this project's dependencies. `python -m`
> guarantees the interpreter that has `PyYAML`/`jsonschema`/`fastapi` installed.

Install deps once with `pip install -e '.[dev]'`.

## Git workflow

- Develop on a feature branch; never push directly to `main`.
- `main` lands changes as **squash** commits via reviewed PRs.
- Keep `main` green: tests pass and `validate-repo` is clean before merge.

## Roadmap

Integration phases live in `docs/integration-plan.md`. Status: Phase 1 (source-locked
build) done; Phase 2 (itinerary-core feasibility boundary) scaffolded in
`src/jvto_agent_runtime/feasibility.py` + `contracts.py` (see `docs/feasibility-api.md`).
Phases 3–5 (live adapters, Meta webhook, deployment approval) are pending and partly
depend on external systems and on `jvto-itinerary-core` reaching `production_ready`.
