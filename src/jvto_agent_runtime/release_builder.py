from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .source_loader import aliases_from_core, core_package_ids, load_core_projection, load_public_knowledge, load_upstream_config
from .utils import git_revision, read_json, read_yaml, sha256_file, utc_now, write_json


def _write_ndjson(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _knowledge_record(concept: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_knowledge_id": concept["runtime_knowledge_id"],
        "upstream_concept_id": concept["upstream_concept_id"],
        "entity_type": concept["entity_type"],
        "title": concept["title"],
        "description": concept["description"],
        "text": concept["body"],
        "tags": concept["tags"],
        "catalog_status": concept["catalog_status"],
        "visibility": concept["visibility"],
        "last_verified": concept["last_verified"],
        "citations": concept["citations"],
        "links_to": concept["links_to"],
        "package_key": concept["package_key"],
        "frontmatter": concept["frontmatter"],
        "source": {
            "file": concept["source_file"],
            "sha256": concept["source_sha256"],
        },
    }


def _core_capabilities(artifacts: dict[str, Any], artifact_paths: dict[str, Path], core_revision: str) -> dict[str, Any]:
    manifest = artifacts["manifest"]
    readiness = artifacts["readiness"]
    groups = readiness.get("groups", {}) if isinstance(readiness, dict) else {}
    partial_groups = sorted(
        key for key, value in groups.items() if isinstance(value, dict) and value.get("status") in {"partial", "blocked"}
    )
    missing_data = manifest.get("missing_data", []) if isinstance(manifest, dict) else []
    warnings = manifest.get("warnings", []) if isinstance(manifest, dict) else []
    available = sorted(key for key in artifact_paths if key not in {"manifest", "readiness"})
    return {
        "core_release_id": core_revision,
        "source_mode": manifest.get("source_mode", "unknown"),
        "dataset_status": manifest.get("status", "unknown"),
        "legacy_dataset_mode": manifest.get("legacy_dataset_mode", "unknown"),
        "available_artifacts": available,
        "available_capabilities": [
            "package_resolution" if "package_catalog" in available or "package_route_map" in available else None,
            "location_alias_resolution" if "location_aliases" in available else None,
            "route_context" if "route_legs" in available else None,
            "pickup_dropoff_context" if "pickup_contexts" in available or "dropoff_contexts" in available else None,
            "time_window_context" if "time_window_rules" in available else None,
            "scenario_feasibility_contract" if "package_route_map" in available else None,
        ],
        "known_gaps": {
            "partial_groups": partial_groups,
            "manifest_missing_data": missing_data,
            "manifest_warnings": warnings,
        },
        "restricted_runtime_uses": [
            "final_price_calculation",
            "payment_confirmation",
            "availability_confirmation",
            "raw_cost_disclosure",
            "raw_customer_pii",
        ],
        "artifact_sources": {
            name: {"path": str(path), "sha256": sha256_file(path)} for name, path in sorted(artifact_paths.items())
        },
    }


def _package_crosswalk(concepts: list[dict[str, Any]], artifacts: dict[str, Any]) -> dict[str, Any]:
    core_ids = core_package_ids(artifacts)
    entries: list[dict[str, Any]] = []
    for concept in concepts:
        if concept["entity_type"] != "Tour Package":
            continue
        key = concept.get("package_key")
        if not key:
            entries.append({
                "knowledge_concept_id": concept["upstream_concept_id"],
                "knowledge_package_key": None,
                "itinerary_core_package_id": None,
                "status": "needs_review",
                "reason": "Public Tour Package concept has no package_key.",
            })
            continue
        entries.append({
            "knowledge_concept_id": concept["upstream_concept_id"],
            "knowledge_package_key": key,
            "itinerary_core_package_id": key if key in core_ids else None,
            "status": "matched" if key in core_ids else "knowledge_only",
            "reason": None if key in core_ids else "No same-key package record found in supplied Itinerary Core projection.",
        })
    return {
        "schema_version": "package-crosswalk-v1",
        "entries": sorted(entries, key=lambda item: item["knowledge_concept_id"]),
        "summary": {
            "matched": sum(1 for item in entries if item["status"] == "matched"),
            "knowledge_only": sum(1 for item in entries if item["status"] == "knowledge_only"),
            "needs_review": sum(1 for item in entries if item["status"] == "needs_review"),
            "core_only_count": len(core_ids - {item["knowledge_package_key"] for item in entries if item.get("knowledge_package_key")}),
        },
    }


def _retrieval_index(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for concept in concepts:
        entries.append({
            "runtime_knowledge_id": concept["runtime_knowledge_id"],
            "upstream_concept_id": concept["upstream_concept_id"],
            "entity_type": concept["entity_type"],
            "title": concept["title"],
            "tags": concept["tags"],
            "package_key": concept["package_key"],
            "catalog_status": concept["catalog_status"],
            "last_verified": concept["last_verified"],
        })
    return {"schema_version": "retrieval-index-v1", "entries": sorted(entries, key=lambda item: item["upstream_concept_id"]) }


def _copy_runtime_config(repo_root: Path, release_dir: Path) -> None:
    for name in ("intent-routing.yaml", "guardrails.yaml", "tool-policy.yaml", "data-ownership.yaml"):
        source = repo_root / "config" / name
        destination = release_dir / name.replace(".yaml", ".json")
        write_json(destination, read_yaml(source))


def _project_customer_sales(knowledge_root: Path, config: dict[str, Any], release_dir: Path) -> dict[str, Any]:
    """Project the published Customer Sales Release into the source-locked agent release.

    The runtime authors nothing here: it copies the upstream-published release subset verbatim
    into <release_dir>/customer-sales/ so the executor consumes a source-locked snapshot.
    """
    rel = config["upstreams"]["knowledge_catalog"].get("customer_sales_release_root", "okf/customer-sales-release/jvto")
    src = knowledge_root / rel
    if not src.exists():
        return {"present": False}
    dest = release_dir / "customer-sales"
    dest.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("*.json"))
    for path in files:
        shutil.copyfile(path, dest / path.name)
    manifest_path = dest / "release-manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    return {
        "present": True,
        "source_path": rel,
        "release_id": manifest.get("release_id"),
        "object_count": len(files),
        "manifest_sha256": sha256_file(manifest_path) if manifest_path.exists() else None,
    }


def _vendor_agent_catalog(
    knowledge_root: Path,
    core_root: Path,
    web_root: Path | None,
    config: dict[str, Any],
    release_dir: Path,
) -> dict[str, Any]:
    """Vendor the full chat-time catalog into <release_dir>/agent-catalog/ so the
    delivery-time resolvers read ONE local directory instead of three live roots
    (release + jvto-web clone + jvto-itinerary-core clone).

    Copies are verbatim (the runtime authors nothing here). It writes:
      - the reusable module layer (general-modules / package-variations / module-compatibility)
      - the Core agent-contract projection under agent-contract/
      - (when web_root is supplied) the Web link + media capability registries
      - catalog-manifest.json (counts + module<->core crosswalk integrity)
    """
    kc = config["upstreams"]["knowledge_catalog"]
    core_cfg = config["upstreams"]["itinerary_core"]
    web_cfg = config["upstreams"].get("web_experience", {})

    catalog_dir = release_dir / "agent-catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    # 1) Module layer (flat in the customer sales release root).
    sales_root = knowledge_root / kc.get("customer_sales_release_root", "okf/customer-sales-release/jvto")
    module_files = kc.get("module_layer_files", ["general-modules.json", "package-variations.json", "module-compatibility.json"])
    module_present = True
    for name in module_files:
        src = sales_root / name
        if not src.exists():
            module_present = False
            continue
        shutil.copyfile(src, catalog_dir / name)

    variations = read_json(catalog_dir / "package-variations.json") if (catalog_dir / "package-variations.json").exists() else []
    general = read_json(catalog_dir / "general-modules.json") if (catalog_dir / "general-modules.json").exists() else []
    compat = read_json(catalog_dir / "module-compatibility.json") if (catalog_dir / "module-compatibility.json").exists() else {}

    # 2) Core agent-contract projection (verbatim, under agent-contract/).
    contract_src = core_root / core_cfg.get("agent_contract_root", "generated/itinerary-intelligence/agent-contract")
    contract_dest = catalog_dir / "agent-contract"
    boundaries: list[dict[str, Any]] = []
    contract_files: list[str] = []
    if contract_src.exists():
        contract_dest.mkdir(parents=True, exist_ok=True)
        for src in sorted(contract_src.glob("*.json")):
            shutil.copyfile(src, contract_dest / src.name)
            contract_files.append(src.name)
        bpath = contract_dest / "package-customization-boundaries.json"
        if bpath.exists():
            boundaries = read_json(bpath)

    # 3) Web experience capability registries (optional).
    web_present = False
    web_link_keys: set[str] = set()
    web_links = web_assets = 0
    if web_root is not None:
        public_root = web_root / web_cfg.get("public_root", "public")
        link_name = web_cfg.get("link_registry", "customer-link-registry.json")
        media_name = web_cfg.get("media_registry", "customer-media-registry.json")
        link_src = public_root / link_name
        media_src = public_root / media_name
        if link_src.exists() and media_src.exists():
            shutil.copyfile(link_src, catalog_dir / link_name)
            shutil.copyfile(media_src, catalog_dir / media_name)
            link_data = read_json(catalog_dir / link_name)
            media_data = read_json(catalog_dir / media_name)
            web_link_keys = {l.get("link_key") for l in link_data.get("links", []) if l.get("link_key")}
            web_links = len(link_data.get("links", []))
            web_assets = len(media_data.get("assets", []))
            web_present = True

    # 4) Crosswalk integrity: module variations vs Core agent-contract boundaries must
    #    cover the same package_key set (these two drive routing + booking authority).
    var_keys = {v.get("package_key") for v in variations if v.get("package_key")}
    bound_keys = {b.get("package_key") for b in boundaries if b.get("package_key")}
    aligned = bool(var_keys) and var_keys == bound_keys
    module_only = sorted(var_keys - bound_keys)
    core_only = sorted(bound_keys - var_keys)

    # Web is a capability backlog, not a routing source: a missing public page is a
    # recorded gap (page_missing discipline), never an integrity failure.
    page_keys = [v.get("public_page_key") for v in variations if v.get("public_page_key")]
    web_resolved = sorted(k for k in page_keys if k in web_link_keys) if web_present else []
    web_missing = sorted(k for k in page_keys if k not in web_link_keys) if web_present else []

    manifest = {
        "schema_version": "agent-catalog-manifest-v1",
        "module_layer": {
            "present": module_present,
            "general_modules": len(general),
            "package_variations": len(variations),
            "compatibility_release_id": compat.get("release_id", "unknown") if isinstance(compat, dict) else "unknown",
        },
        "core_agent_contract": {
            "present": bool(contract_files),
            "files": sorted(contract_files),
            "boundary_count": len(boundaries),
        },
        "web_experience": {
            "present": web_present,
            "link_count": web_links,
            "asset_count": web_assets,
            "package_page_links_resolved": web_resolved,
            "package_page_links_missing": web_missing,
        },
        "crosswalk_integrity": {
            "status": "aligned" if aligned else "needs_review",
            "module_variation_count": len(var_keys),
            "core_boundary_count": len(bound_keys),
            "aligned": aligned,
            "module_only": module_only,
            "core_only": core_only,
        },
    }
    write_json(catalog_dir / "catalog-manifest.json", manifest)
    return manifest


def build_release(
    repo_root: Path,
    knowledge_root: Path,
    core_root: Path,
    release_id: str,
    overwrite: bool = False,
    web_root: Path | None = None,
) -> Path:
    config = load_upstream_config(repo_root)
    concepts, catalog, knowledge_warnings = load_public_knowledge(knowledge_root, config)
    artifacts, core_warnings, artifact_paths = load_core_projection(core_root, config)

    release_dir = repo_root / "dist" / "releases" / release_id
    if release_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Release already exists: {release_dir}")
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)

    knowledge_records = [_knowledge_record(concept) for concept in concepts]
    _write_ndjson(release_dir / "knowledge.ndjson", knowledge_records)
    write_json(release_dir / "retrieval-index.json", _retrieval_index(concepts))
    write_json(release_dir / "package-crosswalk.json", _package_crosswalk(concepts, artifacts))
    write_json(release_dir / "location-crosswalk.json", {"schema_version": "location-crosswalk-v1", "entries": aliases_from_core(artifacts)})

    knowledge_revision = git_revision(knowledge_root)
    core_revision = git_revision(core_root)
    core_capabilities = _core_capabilities(artifacts, artifact_paths, core_revision)
    write_json(release_dir / "core-capabilities.json", core_capabilities)
    _copy_runtime_config(repo_root, release_dir)
    customer_sales = _project_customer_sales(knowledge_root, config, release_dir)
    agent_catalog = _vendor_agent_catalog(knowledge_root, core_root, web_root, config, release_dir)

    web_cfg = config["upstreams"].get("web_experience", {})
    web_lock: dict[str, Any] = {"present": agent_catalog["web_experience"]["present"]}
    if web_root is not None and agent_catalog["web_experience"]["present"]:
        web_lock.update({
            "repo": web_cfg.get("repo"),
            "revision": git_revision(web_root),
            "link_registry_sha256": sha256_file(release_dir / "agent-catalog" / web_cfg.get("link_registry", "customer-link-registry.json")),
            "media_registry_sha256": sha256_file(release_dir / "agent-catalog" / web_cfg.get("media_registry", "customer-media-registry.json")),
        })

    source_lock = {
        "schema_version": "source-lock-v1",
        "created_at": utc_now(),
        "knowledge_catalog": {
            "repo": config["upstreams"]["knowledge_catalog"]["repo"],
            "revision": knowledge_revision,
            "catalog_path": "okf/bundles/jvto/catalog.json",
            "catalog_sha256": sha256_file(knowledge_root / "okf/bundles/jvto/catalog.json"),
            "concept_count_in_catalog": catalog.get("concept_count"),
        },
        "customer_sales_release": customer_sales,
        "itinerary_core": {
            "repo": config["upstreams"]["itinerary_core"]["repo"],
            "revision": core_revision,
            "manifest_path": "generated/itinerary-intelligence/manifest.json",
            "manifest_sha256": sha256_file(core_root / "generated/itinerary-intelligence/manifest.json"),
            "readiness_path": "generated/itinerary-intelligence/data-readiness-report.json",
            "readiness_sha256": sha256_file(core_root / "generated/itinerary-intelligence/data-readiness-report.json"),
        },
        "web_experience": web_lock,
        "agent_catalog": {
            "crosswalk_integrity": agent_catalog["crosswalk_integrity"]["status"],
            "module_variation_count": agent_catalog["crosswalk_integrity"]["module_variation_count"],
            "core_boundary_count": agent_catalog["crosswalk_integrity"]["core_boundary_count"],
        },
    }
    write_json(release_dir / "source-lock.json", source_lock)

    crosswalk = _package_crosswalk(concepts, artifacts)
    blocking_conditions = [
        "Live transactional adapters are not part of this static release.",
        "Manual deployment approval is required before customer traffic.",
    ]
    if not concepts:
        blocking_conditions.append("No release-eligible public knowledge concepts were found.")
    if crosswalk["summary"]["knowledge_only"]:
        blocking_conditions.append("Some public package concepts have no matching Itinerary Core package ID.")
    if core_capabilities["dataset_status"] != "production_ready":
        blocking_conditions.append("Itinerary Core dataset status is not production_ready; respect recorded gaps and handoff rules.")
    if not customer_sales.get("present"):
        blocking_conditions.append("Customer Sales Release not found in knowledge upstream; catalog/price lookups unavailable.")
    if agent_catalog["crosswalk_integrity"]["status"] != "aligned":
        blocking_conditions.append("Agent catalog module<->core crosswalk is not aligned; affected packages need WhatsApp-assisted validation.")
    if not agent_catalog["web_experience"]["present"]:
        blocking_conditions.append("No web experience registry vendored; link/visual capabilities are unavailable (text-only responses).")

    release_manifest = {
        "schema_version": "agent-release-manifest-v1",
        "release_id": release_id,
        "created_at": utc_now(),
        "status": "integration_candidate" if concepts else "blocked",
        "customer_traffic_ready": False,
        "knowledge_record_count": len(knowledge_records),
        "package_crosswalk": crosswalk["summary"],
        "core_dataset_status": core_capabilities["dataset_status"],
        "customer_sales_release": customer_sales,
        "agent_catalog": {
            "crosswalk_integrity": agent_catalog["crosswalk_integrity"]["status"],
            "module_variations": agent_catalog["module_layer"]["package_variations"],
            "core_boundaries": agent_catalog["core_agent_contract"]["boundary_count"],
            "web_experience_present": agent_catalog["web_experience"]["present"],
        },
        "blocking_conditions": blocking_conditions,
        "warnings": sorted(knowledge_warnings + core_warnings),
    }
    write_json(release_dir / "release-manifest.json", release_manifest)
    write_json(release_dir / "validation-report.json", {"status": "not_validated", "findings": []})
    return release_dir
