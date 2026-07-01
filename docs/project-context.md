# Project Context (read first each milestone)

Canonical, persistent context for the JVTO WhatsApp agent work. **Read this at the start
of every milestone and update it at the end** — so history, decisions, and open items live
here in the repo, not in chat (containers are ephemeral).

> This file does NOT restate the rules. The **working contract** (ownership boundary, hard
> rules, required validation, git workflow) is [`CLAUDE.md`](../CLAUDE.md). Per-layer detail
> lives in the other `docs/*.md`. This file is the **milestone log + decisions + open items
> + operating protocol**.

## Operating protocol (the completion loop)

Run every milestone as: **inspect → implement → test → real-data check → self-review → fix →
repeat until all gates pass → merge → update this file → final report.**

- **inspect** — read this file + `CLAUDE.md` + the actual code/tests/contracts; treat code as
  source of truth, not prior reports.
- **test** — `python -m pytest` and `python -m jvto_agent_runtime validate-repo` stay green.
- **real-data check** — build a release from the local sibling clones and exercise the change
  end-to-end (not just fixtures). See "Local sibling clones" below.
- **self-review** — review the diff for safety regressions + scope drift before merge.
- **merge** — squash via the designated branch; never push to `main` directly.
- **update this file** — append to the milestone log + decisions + open items, then report.

## Architecture snapshot (4 repos)

| Repo | Role | At build time | At chat time |
|---|---|---|---|
| `knowledge-catalog-jvto-bootstrap` | public knowledge + module layer | source | — |
| `jvto-itinerary-core` | route truth / operational intelligence + `agent-contract/` | source | — |
| `jvto-web` | customer experience: link/media capability registries | source | — |
| `jvto-whatsapp-agent-runtime` (**this**) | integration/runtime: build a release, turn a classified request into safe outputs | builds the release | **reads ONE local release** |

**Runtime Monolith invariant:** at chat time the runtime reads only one compiled release
(`dist/releases/<id>/agent-catalog/` = module layer + Core agent-contract + Web registries).
No `jvto-web` / `jvto-itinerary-core` clone is read during a conversation.

## Local sibling clones (for real-data checks)

`/home/user/knowledge-catalog-jvto-bootstrap`, `/home/user/jvto-itinerary-core`,
`/home/user/jvto-web`. Build + verify:

```bash
python -m jvto_agent_runtime build-release \
  --knowledge-root /home/user/knowledge-catalog-jvto-bootstrap \
  --core-root /home/user/jvto-itinerary-core \
  --web-root /home/user/jvto-web --release-id smoke --overwrite
python -m jvto_agent_runtime validate-release --release-dir dist/releases/smoke
```

## Milestone log (newest first)

| PR | Milestone | Key outcome |
|---|---|---|
| route-truth-audit | Source-backed route truth + topic-specific responses | Cross-repo: (core) added the missing `bali/ijen-papuma-tumpak-sewu-bromo-5d4n` legacy endpoint row (clean route was force-blocked by `no_standard_endpoints`) + new `agent-contract/standard-route-truth.json` classifying every route field (final_jvto_standard / source_backed_estimate / live_condition / exception / absent) + structured Bali-transfer boundary + audit report; (bootstrap) `endpoint-chains.json` now projects pickups + classified endpoint options + direction-aware `bali_transfer`; (runtime) re-vendored, and `response_composer` answers each topic from its own scoped rule — vehicle→vehicle rule, hotel→standard overnights, endpoint→package-valid pickup/finish options, **price only for price-relevant topics**; live_condition options become disclosures, never static claims. |
| local-catalog | Committed compact catalog | Real 16-package data (agent-catalog/ + customer-sales/) vendored into `catalog/`; `build-local-catalog` regenerator; `/v1/customer-response` + CLI default to it — one checkout generates responses with NO sibling repos / NO build. |
| customer-response | Composed customer-ready draft | `response_composer.compose_customer_response` + `POST /v1/customer-response` + CLI + `customer-response-draft` contract: fuses DeliveryPlan (facts/route gate/link) with published catalog+price into one draft; price surfaced only when not-handoff & priced; all states preserved. |
| (data-map) | Physical data map + G7 | Documented the physical data layer (files/fields/join-keys/gaps); refreshed both stale link-registry fixtures to production (G7), preserving page_missing coverage via a synthetic entry. |
| #15 | DecisionEnvelope→DeliveryPlan seam | `delivery_adapter.delivery_plan_from_decision` + `POST /v1/delivery-plan/from-decision` + CLI; maps envelope→presentation inputs; envelope floor (handoff + needs_information) escalate-only. |
| #14 | Persistent context + completion loop | `docs/project-context.md` (this file) becomes the per-milestone context; `CLAUDE.md` points to it. |
| #13 | DeliveryPlan API | `POST /v1/delivery-plan` + `jvto-agent delivery-plan` CLI; reads one local release; 404 on missing/incomplete release. |
| #11 | Runtime Monolith | Vendor self-contained `agent-catalog/` (PR-1) + single-context reader, drop 3-root resolver (PR-2); package-aware link disambiguation + text-only safety. |
| #10 | P0 route-integrity gate | `route_gate`; Core-authoritative booking (**Option A**); gap/unknown→handoff, needs_review→disclosure. |
| #9 | Phase D resolver | module/link/asset/presentation resolvers → `DeliveryPlan` (resolver-only at the time). |
| #8 | Milestone 1 | Consume Customer Sales Release → `ResolvedCustomerContext`. |
| #6,#7 | Customer Sales decision layer | jobs, `TripBrief`, `ResponsePlan` (+ precision fixes). |
| #1–#5 | Foundation | source-locked build, feasibility boundary, live-tool boundary, Meta webhook edge, deployment-approval scaffold. |

Cross-repo foundation PRs also merged: core (#21 off-sequence legs / instant-book gating),
web (#61 capability registry). Bootstrap module layer (#24).

## Key decisions (durable)

- **Option A** — Core `effective_instant_book_eligible` overrides Bootstrap `booking_mode.instant_book`.
- **Monolith over 3-root** — chat reads one vendored release; upstreams are build-time only.
- **Package-aware link safety** — a `link_key` shared across origins resolves by the known
  `package_key` (correct origin URL); no/non-unique context → `ambiguous`, non-sendable.
  The vendored Web registry is the **only** URL authority (Bootstrap `public_url` is never sent).
- **Dedicated presentation endpoints** — `DeliveryPlan` gets its own `/v1/delivery-plan`
  (raw presentation inputs) and `/v1/delivery-plan/from-decision` (DecisionEnvelope seam),
  separate from `/v1/decisions` (DecisionEnvelope) and `/v1/response-plan` (ResponsePlan).
- **Envelope floor (seam)** — the DecisionEnvelope stays authoritative; the seam may only
  ESCALATE: `handoff`/`unsupported`/`handoff_required` → handoff plan; `needs_information`
  → downgrade a committal price/booking plan to a non-committal clarify. Never downgrades.
- **Entities-win precedence (seam)** — per-message entities beat accumulated TripBrief for
  both package selection and customer_context; TripBrief `pax` is an object (normalize it).
- **Fail safe / fail clean** — unknown route integrity → handoff; missing/incomplete release → 404.

## Open / deferred items

- **#12** (deferred, not blocking) — upstream identity cleanup: make package / public-page
  link identity origin-specific (origin-specific `link_key` in jvto-web + origin-specific
  `public_page_key` in bootstrap) so one identity → one variant → one URL. Runtime guard
  stays as defense-in-depth even after.
- **Pending phases** (per `CLAUDE.md` roadmap / `docs/integration-plan.md`): live adapters,
  Meta send integration, deployment-approval gate (Phase 5) — depend on external systems.

## Next recommended milestone

Wire the **live-confirmation step** into the composed response: when a CustomerResponseDraft
surfaces a price/availability, attach the `live_tool_plan` (availability/price re-check) from
the DecisionEnvelope so the draft carries an explicit "confirm before booking" action — turning
the static draft into a draft + the live checks it still needs. Bounded, in-repo; the live
adapters themselves stay `NotConnected` (Phase 3 external) but the action surface is real.

(Earlier candidate — a `presentation-context` contract for `customer_context` — remains a
smaller optional cleanup.)
