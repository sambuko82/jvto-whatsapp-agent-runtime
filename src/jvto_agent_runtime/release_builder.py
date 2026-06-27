from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .source_loader import aliases_from_core, core_package_ids, load_core_projection, load_public_knowledge, load_upstream_config
from .utils import git_revision, read_yaml, sha256_file, utc_now, write_json


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


def build_release(repo_root: Path, knowledge_root: Path, core_root: Path, release_id: str, overwrite: bool = False) -> Path:
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
        "itinerary_core": {
            "repo": config["upstreams"]["itinerary_core"]["repo"],
            "revision": core_revision,
            "manifest_path": "generated/itinerary-intelligence/manifest.json",
            "manifest_sha256": sha256_file(core_root / "generated/itinerary-intelligence/manifest.json"),
            "readiness_path": "generated/itinerary-intelligence/data-readiness-report.json",
            "readiness_sha256": sha256_file(core_root / "generated/itinerary-intelligence/data-readiness-report.json"),
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

    release_manifest = {
        "schema_version": "agent-release-manifest-v1",
        "release_id": release_id,
        "created_at": utc_now(),
        "status": "integration_candidate" if concepts else "blocked",
        "customer_traffic_ready": False,
        "knowledge_record_count": len(knowledge_records),
        "package_crosswalk": crosswalk["summary"],
        "core_dataset_status": core_capabilities["dataset_status"],
        "blocking_conditions": blocking_conditions,
        "warnings": sorted(knowledge_warnings + core_warnings),
    }
    write_json(release_dir / "release-manifest.json", release_manifest)
    write_json(release_dir / "validation-report.json", {"status": "not_validated", "findings": []})
    return release_dir
