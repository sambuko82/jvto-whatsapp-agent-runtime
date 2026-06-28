"""Asset Resolver (Phase D / blueprint section 3).

Resolves a visual intent (visual_key) to a real media asset from the website-published
customer-media-registry. The agent must NEVER invent a visual: an asset is only
sendable when its status is not 'to_create' AND it has a concrete url. Today every
asset is status=to_create (no media exists yet), so this resolver correctly reports
nothing as sendable — the planner then sends text + link only.

Acceptance criterion enforced here: "Agent does not invent visuals."
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import read_json

NON_SENDABLE_STATUSES = {"to_create"}


@dataclass(frozen=True)
class MediaRegistry:
    cdn_base: str
    by_key: dict[str, dict[str, Any]]


def load_media_registry(path: Path | str) -> MediaRegistry:
    """Load customer-media-registry.json (file path or directory containing it)."""
    p = Path(path)
    if p.is_dir():
        p = p / "customer-media-registry.json"
    data = read_json(p)
    by_key = {asset["asset_id"]: asset for asset in data.get("assets", [])}
    return MediaRegistry(cdn_base=data.get("cdn_base", ""), by_key=by_key)


@dataclass(frozen=True)
class AssetResolution:
    asset_id: str
    url: str | None
    status: str          # available | to_create | unknown
    sendable: bool
    tier: str | None = None


def resolve_asset(registry: MediaRegistry, visual_key: str | None) -> AssetResolution | None:
    if not visual_key:
        return None
    entry = registry.by_key.get(visual_key)
    if entry is None:
        return AssetResolution(asset_id=visual_key, url=None, status="unknown", sendable=False)
    url = entry.get("url")
    status = entry.get("status", "unknown")
    sendable = bool(url) and status not in NON_SENDABLE_STATUSES
    return AssetResolution(
        asset_id=visual_key,
        url=url if sendable else None,
        status=status,
        sendable=sendable,
        tier=entry.get("tier"),
    )


def resolve_first_sendable(registry: MediaRegistry, visual_keys: list[str]) -> AssetResolution | None:
    resolutions = [resolve_asset(registry, k) for k in visual_keys]
    resolutions = [r for r in resolutions if r is not None]
    for r in resolutions:
        if r.sendable:
            return r
    return resolutions[0] if resolutions else None
