"""Link Resolver (Phase D / blueprint section 3).

Resolves a link intent (link_key or package public_page_key) to a real URL from the
website-published customer-link-registry. The agent must NEVER invent a URL: if a
link is not 'existing' with a concrete url, this returns a non-sendable result that
carries the status + any fallback, so the planner can choose to omit the link or use
a fallback page instead of fabricating one.

Acceptance criterion enforced here: "Agent does not invent URLs."
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import read_json

SENDABLE_STATUSES = {"existing"}


@dataclass(frozen=True)
class LinkRegistry:
    base_url: str
    by_key: dict[str, dict[str, Any]]               # deduped view (no package context)
    records_by_key: dict[str, list[dict[str, Any]]] = None  # all records per key (for disambiguation)


def load_link_registry(path: Path | str) -> LinkRegistry:
    """Load customer-link-registry.json (file path or directory containing it).

    Some link_keys are duplicated because a package's public_page_key is shared across
    origins (e.g. `bali/…` and the Surabaya `…` variant both map to
    `package_ijen_bromo_madakaripura_3d2n`) while the website carries an origin-specific
    URL per origin (`/from-bali/…` vs `/from-surabaya/…`). We keep every record per key
    (`records_by_key`) so a known package context can pick its own URL, and a deduped
    `by_key` view for context-free lookups: exact-duplicate URLs collapse harmlessly,
    but a key that repeats with a CONFLICTING url is marked `ambiguous` (url=None) there
    so a context-free lookup omits it rather than guessing an origin.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "customer-link-registry.json"
    data = read_json(p)
    records_by_key: dict[str, list[dict[str, Any]]] = {}
    for link in data.get("links", []):
        records_by_key.setdefault(link["link_key"], []).append(link)
    by_key: dict[str, dict[str, Any]] = {}
    for key, records in records_by_key.items():
        if len(records) == 1 or len({r.get("url") for r in records}) == 1:
            by_key[key] = records[0]
        else:  # conflicting duplicate: non-sendable unless disambiguated by package
            by_key[key] = {**records[0], "url": None, "status": "ambiguous"}
    return LinkRegistry(base_url=data.get("base_url", ""), by_key=by_key, records_by_key=records_by_key)


@dataclass(frozen=True)
class LinkResolution:
    link_key: str
    url: str | None
    status: str            # existing | page_missing | prefill_unverified | unmapped | ambiguous | unknown
    content_type: str | None
    sendable: bool
    fallback_url: str | None = None


def _select_record(
    registry: LinkRegistry, link_key: str, package_key: str | None
) -> dict[str, Any] | None | str:
    """Pick the registry record for a link_key. Returns the record, None (unknown key),
    or the sentinel "ambiguous" when a conflicting duplicate cannot be uniquely resolved
    by the supplied package context."""
    records = (registry.records_by_key or {}).get(link_key)
    if records is None:
        # Fall back to the deduped view (e.g. an empty / hand-built registry).
        entry = registry.by_key.get(link_key)
        return entry if entry is not None else None
    if len(records) == 1 or len({r.get("url") for r in records}) == 1:
        return records[0]
    # Conflicting duplicate (origin-specific URLs under one key): disambiguate by the
    # known package identity carried on the registry record. Exactly one match -> safe.
    if package_key:
        matches = [r for r in records if r.get("package_key") == package_key]
        if len(matches) == 1:
            return matches[0]
    return "ambiguous"


def resolve_link(
    registry: LinkRegistry, link_key: str | None, package_key: str | None = None
) -> LinkResolution | None:
    if not link_key:
        return None
    selected = _select_record(registry, link_key, package_key)
    if selected is None:
        return LinkResolution(link_key=link_key, url=None, status="unknown",
                              content_type=None, sendable=False, fallback_url=None)
    if selected == "ambiguous":
        sample = (registry.records_by_key or {}).get(link_key, [{}])[0]
        return LinkResolution(link_key=link_key, url=None, status="ambiguous",
                              content_type=sample.get("content_type"), sendable=False,
                              fallback_url=sample.get("fallback_url"))
    entry = selected
    url = entry.get("url")
    status = entry.get("status", "unknown")
    sendable = bool(url) and status in SENDABLE_STATUSES
    return LinkResolution(
        link_key=link_key,
        url=url if sendable else None,
        status=status,
        content_type=entry.get("content_type"),
        sendable=sendable,
        fallback_url=entry.get("fallback_url"),
    )


def resolve_first_sendable(
    registry: LinkRegistry, link_keys: list[str], package_key: str | None = None
) -> LinkResolution | None:
    """Return the first link that is actually sendable; else the first resolution
    (so the caller can see the gap), else None."""
    resolutions = [resolve_link(registry, k, package_key) for k in link_keys]
    resolutions = [r for r in resolutions if r is not None]
    for r in resolutions:
        if r.sendable:
            return r
    for r in resolutions:
        if r.fallback_url:  # a missing page that has a usable existing fallback
            return r
    return resolutions[0] if resolutions else None
