"""Route-integrity gate (Phase D follow-up / P0).

Reads jvto-itinerary-core's agent-contract boundary files and exposes, per package_key,
the route integrity + authoritative booking eligibility the runtime must honor before
producing a DeliveryPlan. Core is the authoritative owner of route truth and booking
eligibility (Option A): `effective_instant_book_eligible` here overrides Bootstrap's
advisory `booking_mode.instant_book`.

Pure/deterministic: reads JSON, no network, no PII, no price.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import read_json

# files live either in a flat dir (test fixtures) or under agent-contract/ (a core checkout)
_BOUNDARIES = "package-customization-boundaries.json"
_COMPOSITION = "package-operational-composition.json"


@dataclass(frozen=True)
class RouteGateEntry:
    package_key: str
    integrity: str  # clean | needs_review | gap | unknown
    effective_instant_book_eligible: bool
    flags: dict[str, Any]
    operational_movements_pending: int


@dataclass(frozen=True)
class RouteGate:
    by_key: dict[str, RouteGateEntry]

    def get(self, package_key: str | None) -> RouteGateEntry | None:
        if not package_key:
            return None
        entry = self.by_key.get(package_key)
        if entry is not None:
            return entry
        # Unknown package = unroutable from the runtime's perspective: fail safe to gap.
        return RouteGateEntry(
            package_key=package_key, integrity="unknown",
            effective_instant_book_eligible=False, flags={}, operational_movements_pending=0,
        )


def _resolve(path: Path, name: str) -> Path:
    p = Path(path)
    if p.is_dir():
        nested = p / "agent-contract" / name
        return nested if nested.exists() else p / name
    return p


def load_route_gate(agent_contract_root: Path | str) -> RouteGate:
    """Load the route gate from a directory containing the two agent-contract files
    (either flat, or under an `agent-contract/` subdir)."""
    base = Path(agent_contract_root)
    boundaries = read_json(_resolve(base, _BOUNDARIES))
    composition = {c["package_key"]: c for c in read_json(_resolve(base, _COMPOSITION))}
    by_key: dict[str, RouteGateEntry] = {}
    for b in boundaries:
        key = b["package_key"]
        comp = composition.get(key, {})
        by_key[key] = RouteGateEntry(
            package_key=key,
            integrity=b.get("route_integrity", "unknown"),
            effective_instant_book_eligible=bool(b.get("effective_instant_book_eligible", False)),
            flags=comp.get("route_review_flags", {}),
            operational_movements_pending=comp.get("operational_movements_pending", 0),
        )
    return RouteGate(by_key=by_key)
