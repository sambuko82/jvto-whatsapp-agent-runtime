"""Customer Sales Decision Layer (PR 1).

A pure, deterministic planner that turns an already-built DecisionEnvelope (plus an
optional TripBrief conversation state) into a small, customer-facing ResponsePlan.

Design rules (do not break):
- The DecisionEnvelope stays the single source of truth for system routing
  (feasibility / live_tool_plan / handoff). The ResponsePlan only adds the
  customer-facing instruction and collapses routing into `required_actions`.
- The planner is PURE: it never calls a tool, an adapter, a price source, Itinerary
  Core, or the network. It only decides the next step. Execution happens later.
- It authors no catalog/price/availability/customer data and stores no TripBrief or PII.
- ResponsePlan handoff mirrors the envelope and may only ESCALATE (never downgrade).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import validate_or_raise
from .utils import read_yaml

RESPONSE_PLAN_CONTRACT = "response-plan"
TRIP_BRIEF_CONTRACT = "trip-brief"

_EMPTY = (None, "", [], {})


def load_customer_sales_config(repo_root: Path | str) -> dict[str, Any]:
    base = Path(repo_root) / "config" / "customer-sales"
    return {
        "routing": read_yaml(base / "routing-and-clarification.yaml"),
        "guardrails": read_yaml(base / "guardrails-and-state.yaml"),
    }


def _get_path(obj: dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _contains_any(text: str, needles: list[str]) -> bool:
    low = (text or "").lower()
    return any(n.lower() in low for n in needles)


# --- customer job ----------------------------------------------------------


def derive_default_customer_job(intent: str, trip_brief: dict[str, Any] | None, config: dict[str, Any], query: str = "") -> str:
    routing = config.get("routing", {})
    trip_brief = trip_brief or {}
    stage = trip_brief.get("customer_stage")
    for rule in routing.get("job_overrides", []) or []:
        if rule.get("when_intent") != intent:
            continue
        if rule.get("if_query_contains") and _contains_any(query, rule["if_query_contains"]):
            return rule["then_job"]
        if rule.get("if_stage_in") and stage in rule["if_stage_in"]:
            return rule["then_job"]
    return (routing.get("default_job_by_intent", {}) or {}).get(intent, "unsupported")


# --- requirement profile (the functional driver of required fields/actions) -


def _profile_cfg(profile: str, config: dict[str, Any]) -> dict[str, Any]:
    return (config.get("routing", {}).get("requirement_profiles", {}) or {}).get(profile, {}) or {}


def derive_requirement_profile(intent: str, trip_brief: dict[str, Any] | None, config: dict[str, Any], query: str = "") -> str:
    routing = config.get("routing", {})
    trip_brief = trip_brief or {}
    has_package = trip_brief.get("selected_package_key") not in _EMPTY
    for rule in routing.get("profile_overrides", []) or []:
        if rule.get("when_intent") != intent:
            continue
        if rule.get("if_query_contains") and _contains_any(query, rule["if_query_contains"]):
            return rule["then_profile"]
        if rule.get("if_selected_package_key") and has_package:
            return rule["then_profile"]
    return (routing.get("default_profile_by_intent", {}) or {}).get(intent, "general_information")


# --- trip brief ------------------------------------------------------------


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_trip_brief(base: dict[str, Any] | None, update: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge a TripBrief patch into a base, returning a contract-valid TripBrief.

    Bumps plan_version and, when a core constraint changes, records
    `superseded_pending_revalidation` in active_blockers (the invalidation rule).
    """
    base = base or {"schema_version": "trip-brief-v1", "plan_version": 0}
    trigger_fields = ["travel_dates", "pax", "pickup", "dropoff", "destinations"]
    if config:
        trigger_fields = (config.get("guardrails", {}).get("invalidation", {}) or {}).get("trigger_fields", trigger_fields)
        status_label = (config.get("guardrails", {}).get("invalidation", {}) or {}).get("status_label", "superseded_pending_revalidation")
    else:
        status_label = "superseded_pending_revalidation"

    changed = any(field in update and update[field] != base.get(field) for field in trigger_fields)

    merged = _deep_merge(base, update)
    merged["schema_version"] = "trip-brief-v1"
    merged["plan_version"] = int(base.get("plan_version", 0)) + 1

    if changed and int(base.get("plan_version", 0)) > 0:
        blockers = list(merged.get("active_blockers", []) or [])
        if status_label not in blockers:
            blockers.append(status_label)
        merged["active_blockers"] = blockers

    validate_or_raise(TRIP_BRIEF_CONTRACT, merged)
    return merged


def _missing_required_fields(trip_brief: dict[str, Any], profile: str, config: dict[str, Any]) -> list[str]:
    required = _profile_cfg(profile, config).get("required", []) or []
    return [path for path in required if _get_path(trip_brief, path) in _EMPTY]


def derive_trip_brief_status(trip_brief: dict[str, Any] | None, profile: str, config: dict[str, Any]) -> str:
    trip_brief = trip_brief or {}
    status_label = (config.get("guardrails", {}).get("invalidation", {}) or {}).get("status_label", "superseded_pending_revalidation")
    if status_label in (trip_brief.get("active_blockers", []) or []):
        return "superseded_pending_revalidation"
    # A profile that requires nothing (general_information / policy_explanation) needs no brief.
    if not (_profile_cfg(profile, config).get("required", []) or []):
        return "not_applicable"
    return "complete" if not _missing_required_fields(trip_brief, profile, config) else "incomplete"


# --- response plan ---------------------------------------------------------


def _attraction_hard_dependency(trip_brief: dict[str, Any], query: str, guardrails: dict[str, Any]) -> bool:
    cfg = guardrails.get("attraction_hard_dependency", {}) or {}
    for dep in trip_brief.get("attraction_dependencies", []) or []:
        if dep.get("priority") == "hard_dependency":
            return True
    return _contains_any(query, cfg.get("trigger_phrases", []) or [])


def _needs_itinerary_core(envelope: dict[str, Any], trip_brief: dict[str, Any], query: str, guardrails: dict[str, Any], route_signals: list[str] | None = None) -> bool:
    triggers = set(guardrails.get("route_validation_triggers", []) or [])
    if any(signal in triggers for signal in (route_signals or [])):
        return True
    if envelope.get("feasibility", {}).get("required"):
        return True
    if _contains_any(query, guardrails.get("connection_keywords", []) or []):
        return True
    if _get_path(trip_brief, "pickup.arrival_time") not in _EMPTY:
        return True
    if _get_path(trip_brief, "dropoff.required_by") not in _EMPTY:
        return True
    if "superseded_pending_revalidation" in (trip_brief.get("active_blockers", []) or []):
        return True
    return False


def _add_action(actions: list[dict[str, Any]], type_: str, reason: str, *, required: bool = True, detail: str | None = None) -> None:
    for existing in actions:
        if existing["type"] == type_ and existing.get("detail") == detail:
            existing["required"] = existing["required"] or required
            return
    actions.append({"type": type_, "required": required, "reason": reason, "detail": detail})


def derive_response_plan(decision_envelope: dict[str, Any], trip_brief: dict[str, Any] | None, config: dict[str, Any], query: str = "", signals: list[str] | None = None, route_signals: list[str] | None = None) -> dict[str, Any]:
    """Build a contract-valid ResponsePlan from a DecisionEnvelope (+ optional TripBrief).

    Pure and deterministic: no tool calls, no I/O beyond the supplied config. The customer
    job is a grouping label; the requirement profile drives required fields, actions, and
    disclosures.
    """
    trip_brief = trip_brief or {}
    signals = signals or []
    routing = config.get("routing", {})
    guardrails = config.get("guardrails", {})
    disclosure_text = guardrails.get("required_disclosures", {}) or {}

    intent = decision_envelope.get("intent", "")
    job = derive_default_customer_job(intent, trip_brief, config, query=query)
    profile = derive_requirement_profile(intent, trip_brief, config, query=query)
    profile_cfg = _profile_cfg(profile, config)
    brief_status = derive_trip_brief_status(trip_brief, profile, config)

    # --- required actions (from the requirement profile; no duplication of the envelope) ---
    actions: list[dict[str, Any]] = []
    for action_type in profile_cfg.get("default_actions", []) or []:
        _add_action(actions, action_type, f"profile_default:{profile}")
    needs_core = _needs_itinerary_core(decision_envelope, trip_brief, query, guardrails, route_signals)
    if needs_core:
        _add_action(actions, "itinerary_core", "route_feasibility_required")
    for tool in decision_envelope.get("live_tool_plan", []) or []:
        _add_action(actions, "live_check", "intent_live_tool_plan", detail=tool)

    # --- disclosures (from the requirement profile) ---
    disclosure_keys: list[str] = list(profile_cfg.get("default_disclosures", []) or [])
    hd = guardrails.get("attraction_hard_dependency", {}) or {}

    def _flag_live_check(reason: str) -> None:
        ra = hd.get("required_action", {}) or {}
        if ra:
            _add_action(actions, ra.get("type", "live_check"), reason, detail=ra.get("detail"))
        for key in hd.get("required_disclosures", []) or []:
            if key not in disclosure_keys:
                disclosure_keys.append(key)

    # Hard dependency (e.g. Blue Fire as the main reason): live-check + no-guarantee, NOT handoff.
    handoff_escalation: str | None = None
    if _attraction_hard_dependency(trip_brief, query, guardrails):
        _flag_live_check("attraction_hard_dependency")
    # A guarantee demand IS a handoff (independent of whether a hard dependency was recorded).
    if _contains_any(query, hd.get("guarantee_phrases", []) or []):
        _flag_live_check("attraction_guarantee_demanded")
        handoff_escalation = "attraction_guarantee_demanded"
    # A status query ("will it reopen?") is a live check + disclosure, never a handoff.
    osq = guardrails.get("operational_status_query", {}) or {}
    if osq and _contains_any(query, osq.get("phrases", []) or []):
        ra = osq.get("required_action", {}) or {}
        if ra:
            _add_action(actions, ra.get("type", "live_check"), "operational_status_query", detail=ra.get("detail"))
        for key in osq.get("required_disclosures", []) or []:
            if key not in disclosure_keys:
                disclosure_keys.append(key)

    disclosures = [disclosure_text.get(key, key) for key in disclosure_keys]

    # --- clarifying question (first missing required field; then connection-time rule) ---
    questions = routing.get("clarification_questions", {}) or {}
    clarifying_question: str | None = None
    if brief_status == "incomplete":
        missing = _missing_required_fields(trip_brief, profile, config)
        if missing:
            clarifying_question = questions.get(missing[0], questions.get("_default"))
    # Connection-time rule: a flight/train/ferry mentioned without an exact time must be asked.
    if clarifying_question is None and needs_core:
        connection_mentioned = _contains_any(query, guardrails.get("connection_keywords", []) or [])
        time_known = (
            _get_path(trip_brief, "pickup.arrival_time") not in _EMPTY
            or _get_path(trip_brief, "dropoff.required_by") not in _EMPTY
        )
        if connection_mentioned and not time_known:
            clarifying_question = questions.get("dropoff.required_by") or questions.get("pickup.arrival_time")

    # --- handoff: mirror envelope, escalate only (never downgrade) ---
    env_handoff = decision_envelope.get("handoff", {}) or {}
    handoff_required = bool(env_handoff.get("required"))
    handoff_reason = (env_handoff.get("reasons") or [None])[0] if handoff_required else None
    hr = guardrails.get("handoff_rules", {}) or {}
    escalating_signals = [s for s in signals if s in (hr.get("mandatory_signals", []) or [])]
    if intent in (hr.get("mandatory_intents", []) or []):
        handoff_required, handoff_reason = True, handoff_reason or f"mandatory_intent:{intent}"
    if escalating_signals:
        handoff_required, handoff_reason = True, handoff_reason or escalating_signals[0]
    if handoff_escalation:
        handoff_required, handoff_reason = True, handoff_reason or handoff_escalation

    # --- mode (handoff > clarify > execute_tool > answer) ---
    tool_action_types = {"price_quote", "itinerary_core", "live_check"}
    if handoff_required:
        mode = "handoff"
    elif clarifying_question is not None:
        mode = "clarify"
    elif any(a["required"] and a["type"] in tool_action_types for a in actions):
        mode = "execute_tool"
    else:
        mode = "answer"

    plan = {
        "schema_version": "response-plan-v1",
        "source_decision_id": decision_envelope.get("decision_id", ""),
        "release_id": decision_envelope.get("release_id", ""),
        "customer_job": job,
        "mode": mode,
        "trip_brief_status": brief_status,
        "approved_knowledge_ids": list(decision_envelope.get("knowledge", {}).get("candidate_ids", []) or []),
        "required_actions": actions,
        "clarifying_question": clarifying_question,
        "required_disclosures": disclosures,
        "handoff": {"required": handoff_required, "reason": handoff_reason},
    }
    validate_or_raise(RESPONSE_PLAN_CONTRACT, plan)
    return plan
