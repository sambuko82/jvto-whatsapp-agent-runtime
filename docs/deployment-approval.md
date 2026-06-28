# Deployment approval gate (Phase 5)

The mechanism that authorizes customer traffic — kept strictly external to the release.

## Invariant

A release is **never** self-marked `customer_traffic_ready: true`. The builder always
writes `false` and the release validator rejects anything else. This module does not change
that: it computes a *runtime determination* and never writes back into the release.

`customer_traffic_ready: true` is derivable only when **all** of these hold:

1. a deployment-approval record valid against `contracts/deployment-approval.schema.json`;
2. a valid HMAC signature over `(release_id, release_fingerprint)` using the operator key
   `JVTO_DEPLOYMENT_APPROVAL_KEY` — held outside this repo, so the runtime alone cannot mint it;
3. the record's `release_fingerprint` matches the live release;
4. the deployment gate passes.

## Deployment gate

`deployment_gate(repo_root, release_dir)` reports `ready_for_approval` + `blocking[]`. It
blocks on: a self-marked release, failed release validation, `knowledge_only` / `needs_review`
package crosswalk entries, `core_dataset_status != production_ready`, or a non
`integration_candidate` status. `ready_for_approval: true` is necessary but never sufficient —
an operator signature is still required.

## Module & surfaces

- `src/jvto_agent_runtime/deployment.py` — `deployment_gate`, `compute_release_fingerprint`,
  `sign_approval`, `create_approval` (operator tool; needs the key), `verify_deployment_approval`.
- CLI: `deployment-gate`, `create-deployment-approval --approved-by <who>`, `verify-deployment --approval <file>`.
- HTTP: `POST /v1/deployment/gate`, `POST /v1/deployment/verify`.

## Operator flow

```bash
export JVTO_DEPLOYMENT_APPROVAL_KEY=...   # operator-held, never committed
python -m jvto_agent_runtime deployment-gate --release-dir <dir>           # must be ready
python -m jvto_agent_runtime create-deployment-approval --release-dir <dir> --approved-by you@org --output approval.json
python -m jvto_agent_runtime verify-deployment --release-dir <dir> --approval approval.json
```

## Current state

With the present upstreams the gate **blocks** (`core_dataset_not_production_ready`): the
itinerary-core dataset is `mvp_seed_outputs_ready`. So even a validly signed approval yields
`customer_traffic_ready: false` until itinerary-core reaches `production_ready` — by design.
