from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .customer_sales_executor import CustomerSalesExecutor
from .decision_engine import build_decision
from .delivery_adapter import delivery_plan_from_decision
from .deployment import deployment_gate, verify_deployment_approval
from .feasibility import NotConnectedEvaluator, evaluate_feasibility
from .live_tools import NotConnectedLiveToolAdapter, UnknownToolError, execute_live_tool
from .meta_webhook import normalize_payload, verify_signature, verify_subscription
from .monolith_catalog import catalog_root_for
from .presentation_resolver import resolve_delivery_plan
from .response_composer import compose_customer_response
from .sales_intelligence import derive_response_plan, load_customer_sales_config
from .validator import validate_release

app = FastAPI(title="JVTO WhatsApp Agent Runtime", version="0.1.0")


class DecisionRequest(BaseModel):
    release_dir: str = Field(..., description="Absolute or service-configured local release path")
    intent: str
    query: str = ""
    entities: dict[str, Any] = Field(default_factory=dict)
    intent_confidence: float = 1.0


class FeasibilityRequest(BaseModel):
    release_dir: str = Field(..., description="Absolute or service-configured local release path")
    entities: dict[str, Any] = Field(default_factory=dict)


class LiveToolRequest(BaseModel):
    release_dir: str = Field(..., description="Absolute or service-configured local release path")
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    intent: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "jvto-whatsapp-agent-runtime"}


@app.post("/v1/decisions")
def decision(request: DecisionRequest) -> dict[str, Any]:
    release_dir = Path(request.release_dir)
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    return build_decision(release_dir, request.intent, request.query, request.entities, request.intent_confidence)


class ResponsePlanRequest(BaseModel):
    decision_envelope: dict[str, Any]
    trip_brief: dict[str, Any] | None = None
    query: str = ""
    signals: list[str] = Field(default_factory=list)


@app.post("/v1/response-plan")
def response_plan(request: ResponsePlanRequest) -> dict[str, Any]:
    # Pure planner: turns a DecisionEnvelope (+ optional TripBrief) into a customer-facing
    # ResponsePlan. It calls no tool/adapter and authors no catalog/price/customer data.
    config = load_customer_sales_config(_repo_root())
    return derive_response_plan(
        request.decision_envelope, request.trip_brief, config, query=request.query, signals=request.signals
    )


def _require_built_release(release_dir: Path) -> None:
    """Shared 404 guard for the presentation endpoints: the path must exist and contain a
    built agent-catalog module layer (not just any directory)."""
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    if not (catalog_root_for(release_dir) / "general-modules.json").exists():
        raise HTTPException(status_code=404, detail="Release has no agent-catalog module layer; rebuild the release with build-release")


class DeliveryPlanRequest(BaseModel):
    release_dir: str = Field(..., description="Local monolith release path (reads <release>/agent-catalog/ only)")
    customer_job: str | None = Field(None, description="Optional ResponsePlan job, e.g. J2_price_and_value")
    query: str = ""
    package_key: str | None = None
    customer_context: dict[str, Any] = Field(default_factory=dict)


@app.post("/v1/delivery-plan")
def delivery_plan(request: DeliveryPlanRequest) -> dict[str, Any]:
    # Presentation read of ONE local monolith release: module layer + Web link/media
    # registries + Core route gate, all from <release>/agent-catalog/. No upstream clone
    # is touched. Authors nothing; never invents a URL/visual; never sources live truth.
    # The existing safety rules hold (route-gap/unknown -> handoff; needs_review -> route
    # validation disclosure; custom-quote -> no booking CTA; package-aware link origin).
    release_dir = Path(request.release_dir)
    _require_built_release(release_dir)
    try:
        return resolve_delivery_plan(
            release_dir,
            customer_job=request.customer_job,
            query=request.query,
            package_key=request.package_key or None,  # normalize "" -> None (no-package)
            customer_context=request.customer_context,
        )
    except FileNotFoundError as error:
        # An incomplete agent-catalog (e.g. module layer present but package-variations,
        # module-compatibility, or the Core agent-contract files missing) is a not-a-built-
        # release condition: fail cleanly rather than surface a 500 from the loaders.
        raise HTTPException(status_code=404, detail=f"Incomplete agent-catalog in release; rebuild the release with build-release ({error})") from error


class DeliveryPlanFromDecisionRequest(BaseModel):
    release_dir: str = Field(..., description="Local monolith release path (reads <release>/agent-catalog/ only)")
    decision_envelope: dict[str, Any] = Field(..., description="An already-built DecisionEnvelope (from /v1/decisions)")
    trip_brief: dict[str, Any] | None = None
    query: str = ""


@app.post("/v1/delivery-plan/from-decision")
def delivery_plan_from_decision_endpoint(request: DeliveryPlanFromDecisionRequest) -> dict[str, Any]:
    # Seam from /v1/decisions to presentation: maps a DecisionEnvelope (+ optional TripBrief)
    # into presentation inputs and builds a DeliveryPlan from the one local release. The
    # envelope stays authoritative for routing/safety; its handoff is honored as a hard floor.
    release_dir = Path(request.release_dir)
    _require_built_release(release_dir)
    config = load_customer_sales_config(_repo_root())
    try:
        return delivery_plan_from_decision(
            release_dir, request.decision_envelope,
            trip_brief=request.trip_brief, query=request.query, config=config,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Incomplete agent-catalog in release; rebuild the release with build-release ({error})") from error


@app.post("/v1/customer-response")
def customer_response_endpoint(request: DeliveryPlanFromDecisionRequest) -> dict[str, Any]:
    # One customer-ready draft from a single compiled release: package facts + published
    # price + Core route/booking safety + sendable link, with all states preserved. Reads
    # both agent-catalog/ (presentation+route gate) and customer-sales/ (catalog+price).
    release_dir = Path(request.release_dir)
    _require_built_release(release_dir)
    if not (release_dir / "customer-sales" / "release-manifest.json").exists():
        raise HTTPException(status_code=404, detail="Customer Sales Release not found in release dir; rebuild the release with build-release")
    config = load_customer_sales_config(_repo_root())
    try:
        return compose_customer_response(
            release_dir, request.decision_envelope,
            trip_brief=request.trip_brief, query=request.query, config=config,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=f"Incomplete release; rebuild the release with build-release ({error})") from error


class ResolvedContextRequest(BaseModel):
    release_dir: str = Field(..., description="Absolute or service-configured local release path")
    response_plan: dict[str, Any]
    trip_brief: dict[str, Any] | None = None


@app.post("/v1/resolved-context")
def resolved_context(request: ResolvedContextRequest) -> dict[str, Any]:
    # Consume the projected Customer Sales Release to resolve catalog + standard price into a
    # contract-valid ResolvedCustomerContext. Authors nothing; degrades safely; emits no live truth.
    release_dir = Path(request.release_dir)
    if not (release_dir / "customer-sales" / "release-manifest.json").exists():
        raise HTTPException(status_code=404, detail="Customer Sales Release not found in release dir")
    return CustomerSalesExecutor(release_dir).resolve(request.response_plan, request.trip_brief)


@app.post("/v1/feasibility")
def feasibility(request: FeasibilityRequest) -> dict[str, Any]:
    # Returns an itinerary-core-response (contracts/itinerary-core-response.schema.json).
    # The response separates customer_visible_reasons (safe to surface) from
    # known_gaps (internal diagnostics). The default NotConnectedEvaluator yields
    # an `unavailable` + handoff response until a real evaluator is wired in.
    release_dir = Path(request.release_dir)
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    return evaluate_feasibility(release_dir, request.entities, NotConnectedEvaluator())


@app.post("/v1/live-tools")
def live_tool(request: LiveToolRequest) -> dict[str, Any]:
    # Returns a live-tool-response (contracts/live-tool-response.schema.json). The default
    # NotConnectedLiveToolAdapter yields `unavailable` until a real adapter is wired in;
    # the runtime must never source live truth from static knowledge.
    release_dir = Path(request.release_dir)
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    try:
        return execute_live_tool(
            release_dir, request.tool, request.params, intent=request.intent, adapter=NotConnectedLiveToolAdapter()
        )
    except UnknownToolError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/v1/releases/validate")
def validate(request: dict[str, str]) -> dict[str, Any]:
    release_dir = Path(request["release_dir"])
    repo_root = Path(os.environ.get("JVTO_AGENT_REPO_ROOT", Path.cwd()))
    return validate_release(repo_root, release_dir)


def _repo_root() -> Path:
    return Path(os.environ.get("JVTO_AGENT_REPO_ROOT", Path.cwd()))


@app.post("/v1/deployment/gate")
def deployment_gate_endpoint(request: dict[str, str]) -> dict[str, Any]:
    release_dir = Path(request["release_dir"])
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    return deployment_gate(_repo_root(), release_dir)


class DeploymentVerifyRequest(BaseModel):
    release_dir: str
    approval: dict[str, Any]


@app.post("/v1/deployment/verify")
def deployment_verify_endpoint(request: DeploymentVerifyRequest) -> dict[str, Any]:
    # Determines customer_traffic_ready from an external signed approval. The release file
    # is never mutated; true requires a valid signature AND a passing deployment gate.
    release_dir = Path(request.release_dir)
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    return verify_deployment_approval(_repo_root(), release_dir, request.approval)


@app.get("/webhooks/meta")
def meta_verify(request: Request) -> PlainTextResponse:
    # Meta GET subscription handshake. Token comes from JVTO_META_VERIFY_TOKEN.
    params = request.query_params
    challenge = verify_subscription(params.get("hub.mode"), params.get("hub.verify_token"), params.get("hub.challenge"))
    if challenge is None:
        raise HTTPException(status_code=403, detail="Meta webhook verification failed")
    return PlainTextResponse(challenge)


@app.post("/webhooks/meta")
async def meta_webhook(request: Request) -> dict[str, Any]:
    # Edge boundary: verify the HMAC signature (fail-closed) then normalize the payload.
    # It does NOT classify intent/entities or send replies — a downstream classifier must
    # call /v1/decisions, and replies go via the Meta Send API (out of scope, needs creds).
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=403, detail="Invalid or missing webhook signature")
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from error
    messages = normalize_payload(payload)
    return {
        "status": "accepted",
        "normalized_message_count": len(messages),
        "messages": messages,
        "next": "classify intent/entities (upstream of this repo), then POST /v1/decisions; replies go via the Meta Send API (out of scope).",
    }
