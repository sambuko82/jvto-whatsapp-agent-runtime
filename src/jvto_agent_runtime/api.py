from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .decision_engine import build_decision
from .validator import validate_release

app = FastAPI(title="JVTO WhatsApp Agent Runtime", version="0.1.0")


class DecisionRequest(BaseModel):
    release_dir: str = Field(..., description="Absolute or service-configured local release path")
    intent: str
    query: str = ""
    entities: dict[str, Any] = Field(default_factory=dict)
    intent_confidence: float = 1.0


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "jvto-whatsapp-agent-runtime"}


@app.post("/v1/decisions")
def decision(request: DecisionRequest) -> dict[str, Any]:
    release_dir = Path(request.release_dir)
    if not release_dir.exists():
        raise HTTPException(status_code=404, detail="Release directory not found")
    return build_decision(release_dir, request.intent, request.query, request.entities, request.intent_confidence)


@app.post("/v1/releases/validate")
def validate(request: dict[str, str]) -> dict[str, Any]:
    release_dir = Path(request["release_dir"])
    repo_root = Path(os.environ.get("JVTO_AGENT_REPO_ROOT", Path.cwd()))
    return validate_release(repo_root, release_dir)


@app.post("/webhooks/meta")
def meta_webhook_placeholder(payload: dict[str, Any]) -> dict[str, Any]:
    # Deliberately non-functional. Authentication, signature validation, conversation lookup,
    # and intent/entity classification must be implemented before production use.
    return {
        "status": "accepted_not_processed",
        "message": "Meta webhook adapter is intentionally a placeholder; route normalized payload to /v1/decisions after signature verification and intent/entity extraction.",
        "received_top_level_keys": sorted(payload.keys()),
    }
