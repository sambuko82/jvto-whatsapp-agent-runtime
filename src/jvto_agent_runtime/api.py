from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .decision_engine import build_decision
from .deployment import deployment_gate, verify_deployment_approval
from .feasibility import NotConnectedEvaluator, evaluate_feasibility
from .live_tools import NotConnectedLiveToolAdapter, UnknownToolError, execute_live_tool
from .meta_webhook import normalize_payload, verify_signature, verify_subscription
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
