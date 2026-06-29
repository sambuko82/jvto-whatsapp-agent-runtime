"""Monolith catalog context (PR-2 / Runtime Monolith).

At customer-chat time the agent reads ONE local compiled release — not three live
upstream roots (the release + a jvto-web clone + a jvto-itinerary-core clone). PR-1
vendors everything the delivery-time resolvers need into `<release>/agent-catalog/`;
this module is the single reader that loads that one directory and exposes the module
layer, the link/media capability registries, and the Core route gate together.

The four loaders are unchanged and still path-based; this just points all of them at
the same vendored `agent-catalog/` directory. There is no upstream clone access here.

Pure/deterministic: reads JSON from disk, no network, no PII, no price.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .asset_resolver import MediaRegistry, load_media_registry
from .link_resolver import LinkRegistry, load_link_registry
from .module_resolver import ModuleLayer, load_module_layer
from .route_gate import RouteGate, load_route_gate
from .utils import read_json

AGENT_CATALOG_DIRNAME = "agent-catalog"
CATALOG_MANIFEST = "catalog-manifest.json"


@dataclass(frozen=True)
class MonolithCatalogContext:
    """Everything the presentation resolver needs, loaded from one release directory."""
    release_root: Path
    catalog_root: Path
    module_layer: ModuleLayer
    link_registry: LinkRegistry
    media_registry: MediaRegistry
    route_gate: RouteGate
    manifest: dict[str, Any]


def catalog_root_for(release_root: Path | str) -> Path:
    """Resolve the agent-catalog directory inside a release.

    Accepts either a release root (containing `agent-catalog/`) or the agent-catalog
    directory itself, so a built release and a flat fixture both work.
    """
    base = Path(release_root)
    nested = base / AGENT_CATALOG_DIRNAME
    if nested.is_dir():
        return nested
    return base


def load_monolith_catalog(release_root: Path | str) -> MonolithCatalogContext:
    """Load the self-contained chat-time catalog from one release directory.

    Reads only `<release>/agent-catalog/` (the module layer, the Web link/media
    registries, and the Core agent-contract under `agent-contract/`). No jvto-web or
    jvto-itinerary-core clone is touched.
    """
    base = Path(release_root)
    catalog = catalog_root_for(base)
    manifest_path = catalog / CATALOG_MANIFEST
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    return MonolithCatalogContext(
        release_root=base,
        catalog_root=catalog,
        module_layer=load_module_layer(catalog),
        link_registry=load_link_registry(catalog),
        media_registry=load_media_registry(catalog),
        route_gate=load_route_gate(catalog),
        manifest=manifest,
    )
