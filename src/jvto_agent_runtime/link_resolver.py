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
    by_key: dict[str, dict[str, Any]]


def load_link_registry(path: Path | str) -> LinkRegistry:
    """Load customer-link-registry.json (file path or directory containing it)."""
    p = Path(path)
    if p.is_dir():
        p = p / "customer-link-registry.json"
    data = read_json(p)
    by_key = {link["link_key"]: link for link in data.get("links", [])}
    return LinkRegistry(base_url=data.get("base_url", ""), by_key=by_key)


@dataclass(frozen=True)
class LinkResolution:
    link_key: str
    url: str | None
    status: str            # existing | page_missing | prefill_unverified | unmapped | unknown
    content_type: str | None
    sendable: bool
    fallback_url: str | None = None


def resolve_link(registry: LinkRegistry, link_key: str | None) -> LinkResolution | None:
    if not link_key:
        return None
    entry = registry.by_key.get(link_key)
    if entry is None:
        return LinkResolution(link_key=link_key, url=None, status="unknown",
                              content_type=None, sendable=False, fallback_url=None)
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


def resolve_first_sendable(registry: LinkRegistry, link_keys: list[str]) -> LinkResolution | None:
    """Return the first link that is actually sendable; else the first resolution
    (so the caller can see the gap), else None."""
    resolutions = [resolve_link(registry, k) for k in link_keys]
    resolutions = [r for r in resolutions if r is not None]
    for r in resolutions:
        if r.sendable:
            return r
    for r in resolutions:
        if r.fallback_url:  # a missing page that has a usable existing fallback
            return r
    return resolutions[0] if resolutions else None
