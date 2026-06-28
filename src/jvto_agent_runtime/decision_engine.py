from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .feasibility import FEASIBILITY_CAPABILITY, ItineraryCoreEvaluator, evaluate_feasibility
from .utils import read_json, utc_now


def _load_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _match_score(query: str, record: dict[str, Any]) -> int:
    terms = {term for term in query.lower().replace("/", " ").replace("-", " ").split() if len(term) > 2}
    searchable = " ".join([
        record.get("title", ""),
        record.get("description", ""),
        " ".join(record.get("tags", [])),
        record.get("package_key") or "",
        record.get("text", ""),
    ]).lower()
    return sum(1 for term in terms if term in searchable)


def _required_missing(route: dict[str, Any], entities: dict[str, Any]) -> list[str]:
    missing = []
    for field in route.get("required_entities", []):
        value = entities.get(field)
        if value is None or value == "" or value == []:
            missing.append(field)
    return missing


def build_decision(release_dir: Path, intent: str, query: str, entities: dict[str, Any], intent_confidence: float = 1.0, evaluator: ItineraryCoreEvaluator | None = None) -> dict[str, Any]:
    manifest = read_json(release_dir / "release-manifest.json")
    source_lock = read_json(release_dir / "source-lock.json")
    routes = read_json(release_dir / "intent-routing.json")["intents"]
    core = read_json(release_dir / "core-capabilities.json")
    records = _load_ndjson(release_dir / "knowledge.ndjson")

    route = routes.get(intent)
    if route is None:
        return {
            "schema_version": "decision-envelope-v1",
            "decision_id": f"dec_{uuid.uuid4().hex}",
            "release_id": manifest["release_id"],
            "intent": intent,
            "intent_status": "unsupported",
            "entities": entities,
            "knowledge": {"candidate_ids": [], "retrieval_status": "not_required"},
            "feasibility": {"required": False, "status": "not_required"},
            "live_tool_plan": [],
            "response_constraints": ["Do not answer beyond approved scope; offer human handoff."],
            "handoff": {"required": True, "reasons": ["unsupported_intent"]},
            "audit": {"knowledge_release": source_lock["knowledge_catalog"]["revision"], "core_release": source_lock["itinerary_core"]["revision"], "created_at": utc_now()},
        }

    candidates: list[tuple[int, dict[str, Any]]] = []
    if route.get("knowledge_required"):
        for record in records:
            score = _match_score(query, record)
            if score:
                candidates.append((score, record))
    candidates.sort(key=lambda item: (-item[0], item[1]["upstream_concept_id"]))
    candidate_ids = [record["runtime_knowledge_id"] for _, record in candidates[:8]]
    retrieval_status = "not_required" if not route.get("knowledge_required") else ("found" if candidate_ids else "none_found")

    missing = _required_missing(route, entities)
    handoff_reasons: list[str] = []
    status = "ready"
    if intent_confidence < 0.75:
        handoff_reasons.append("low_intent_confidence")
    if route.get("force_handoff"):
        handoff_reasons.append("intent_requires_human_handoff")
    if missing:
        status = "needs_information"
    if route.get("knowledge_required") and retrieval_status == "none_found":
        handoff_reasons.append("approved_knowledge_not_found")
    if route.get("feasibility_required") and "scenario_feasibility_contract" not in core.get("available_capabilities", []):
        handoff_reasons.append("itinerary_core_feasibility_capability_unavailable")

    constraints = [
        "Use only the supplied approved knowledge candidates for factual customer-facing claims.",
        "Do not quote price, availability, booking, payment, or hotel status without a valid live-tool response.",
        "Do not guarantee Blue Fire, weather, sunrise, access, or operational conditions.",
        "Treat Itinerary Core output as required for route-feasibility claims.",
    ]
    if missing:
        constraints.append("Ask only for the missing itinerary fields before feasibility evaluation.")
    if route.get("feasibility_required"):
        constraints.append("Submit a schema-valid itinerary-core request before recommending a custom route.")

    feasibility: dict[str, Any] = {"required": bool(route.get("feasibility_required")), "status": "not_required"}
    if route.get("feasibility_required"):
        capability_available = FEASIBILITY_CAPABILITY in core.get("available_capabilities", [])
        if missing:
            # Incomplete request -> ask for fields (intent_status stays needs_information).
            feasibility["status"] = "unavailable"
        elif not capability_available:
            # Capability genuinely absent -> reflect it (a handoff reason was already added above).
            feasibility["status"] = "unavailable"
        else:
            # Phase 2 seam: with a complete request and the capability present, evaluate now if an
            # evaluator is supplied. Without one the envelope stays at "not_evaluated" (pre-Phase-2).
            feasibility["status"] = "not_evaluated"
            if evaluator is not None:
                result = evaluate_feasibility(release_dir, entities, evaluator)
                feasibility["status"] = result["status"]
                feasibility["recommended_package_ids"] = result.get("recommended_package_ids", [])
                feasibility["alternative_package_ids"] = result.get("alternative_package_ids", [])
                feasibility["customer_visible_reasons"] = result.get("customer_visible_reasons", [])
                feasibility["source_release_id"] = result.get("source_release_id")
                # Never let an unconfirmable route be presented as "ready": couple a not_feasible /
                # unavailable verdict to handoff even if the evaluator did not flag it. "conditional"
                # is a valid ready-with-caveats answer (surfaced via customer_visible_reasons).
                if result.get("handoff_required"):
                    handoff_reasons.append("itinerary_core_handoff_required")
                elif feasibility["status"] in {"not_feasible", "unavailable"}:
                    handoff_reasons.append("itinerary_core_route_not_confirmable")

    if handoff_reasons:
        status = "handoff_required"

    return {
        "schema_version": "decision-envelope-v1",
        "decision_id": f"dec_{uuid.uuid4().hex}",
        "release_id": manifest["release_id"],
        "intent": intent,
        "intent_status": status,
        "entities": entities,
        "knowledge": {"candidate_ids": candidate_ids, "retrieval_status": retrieval_status},
        "feasibility": feasibility,
        "live_tool_plan": route.get("live_tools", []) if not missing else [],
        "response_constraints": constraints,
        "handoff": {"required": bool(handoff_reasons), "reasons": handoff_reasons},
        "audit": {"knowledge_release": source_lock["knowledge_catalog"]["revision"], "core_release": source_lock["itinerary_core"]["revision"], "created_at": utc_now()},
    }
