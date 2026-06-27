from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import as_list, parse_frontmatter, read_json, read_yaml, sha256_file


@dataclass(frozen=True)
class UpstreamPaths:
    knowledge_root: Path
    core_root: Path


def load_upstream_config(repo_root: Path) -> dict[str, Any]:
    return read_yaml(repo_root / "config" / "upstreams.yaml")


def _find_first(root: Path, candidates: list[str]) -> Path | None:
    for relative in candidates:
        candidate = root / relative
        if candidate.exists():
            return candidate
    return None


def load_public_knowledge(knowledge_root: Path, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    section = config["upstreams"]["knowledge_catalog"]
    bundle_root = knowledge_root / section["bundle_root"]
    catalog_path = bundle_root / "catalog.json"
    if not catalog_path.exists():
        raise FileNotFoundError(f"Knowledge catalog not found: {catalog_path}")

    catalog = read_json(catalog_path)
    allowed_statuses = set(section["concept_status_allowlist"])
    allowed_visibility = set(section["visibility_allowlist"])
    concepts: list[dict[str, Any]] = []
    warnings: list[str] = []

    for entry in catalog.get("concepts", []):
        if entry.get("status") not in allowed_statuses:
            continue
        if entry.get("visibility") not in allowed_visibility:
            continue
        relative_path = entry.get("path")
        if not isinstance(relative_path, str):
            warnings.append(f"Catalog concept missing path: {entry.get('id', '<unknown>')}")
            continue
        concept_path = bundle_root / relative_path
        if not concept_path.exists():
            warnings.append(f"Catalog concept path missing: {relative_path}")
            continue
        frontmatter, body = parse_frontmatter(concept_path.read_text(encoding="utf-8"))
        concept_status = frontmatter.get("status", entry.get("status"))
        if concept_status not in allowed_statuses:
            warnings.append(f"Excluded {entry.get('id')} because frontmatter status is {concept_status!r}")
            continue
        if frontmatter.get("visibility", entry.get("visibility")) not in allowed_visibility:
            warnings.append(f"Excluded {entry.get('id')} because frontmatter visibility is not public")
            continue

        public_frontmatter = {
            key: value
            for key, value in frontmatter.items()
            if key not in {"commercial_context", "private_notes", "internal_notes", "raw_source"}
        }
        concept = {
            "runtime_knowledge_id": entry["id"].replace("/", "__"),
            "upstream_concept_id": entry["id"],
            "entity_type": entry.get("type") or frontmatter.get("type") or "Concept",
            "title": entry.get("title") or frontmatter.get("title") or entry["id"],
            "description": entry.get("description") or frontmatter.get("description") or "",
            "tags": entry.get("tags", []),
            "catalog_status": entry.get("status"),
            "visibility": entry.get("visibility"),
            "last_verified": entry.get("last_verified") or frontmatter.get("last_verified") or "",
            "citations": entry.get("citations", []),
            "links_to": entry.get("links_to", []),
            "package_key": frontmatter.get("package_key"),
            "frontmatter": public_frontmatter,
            "body": body,
            "source_file": relative_path,
            "source_sha256": sha256_file(concept_path),
        }
        concepts.append(concept)

    return concepts, catalog, warnings


def load_core_projection(core_root: Path, config: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, Path]]:
    section = config["upstreams"]["itinerary_core"]
    missing_required = [relative for relative in section["required_files"] if not (core_root / relative).exists()]
    if missing_required:
        raise FileNotFoundError("Missing required Itinerary Core files: " + ", ".join(missing_required))

    artifact_paths: dict[str, Path] = {}
    artifacts: dict[str, Any] = {}
    warnings: list[str] = []
    for name, candidates in section["artifact_candidates"].items():
        path = _find_first(core_root, as_list(candidates))
        if path is None:
            warnings.append(f"Optional core artifact unavailable: {name}")
            continue
        artifact_paths[name] = path
        artifacts[name] = read_json(path)

    manifest_path = core_root / "generated/itinerary-intelligence/manifest.json"
    readiness_path = core_root / "generated/itinerary-intelligence/data-readiness-report.json"
    artifacts["manifest"] = read_json(manifest_path)
    artifacts["readiness"] = read_json(readiness_path)
    artifact_paths["manifest"] = manifest_path
    artifact_paths["readiness"] = readiness_path
    return artifacts, warnings, artifact_paths


def flatten_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("packages", "items", "records", "data"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def core_package_ids(artifacts: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for artifact_name in ("package_catalog", "package_route_map"):
        for record in flatten_records(artifacts.get(artifact_name)):
            for key in ("package_id", "package_key", "canonical_package_key", "slug", "id"):
                value = record.get(key)
                if isinstance(value, str) and value:
                    ids.add(value)
    return ids


def aliases_from_core(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    raw = artifacts.get("location_aliases")
    records = flatten_records(raw)
    output: list[dict[str, Any]] = []
    for record in records:
        canonical = record.get("canonical_id") or record.get("node_id") or record.get("location_id") or record.get("id")
        aliases = record.get("aliases") or record.get("labels") or record.get("terms") or []
        if not isinstance(canonical, str):
            continue
        for alias in as_list(aliases):
            if isinstance(alias, str):
                output.append({"alias": alias, "canonical_id": canonical})
        label = record.get("label") or record.get("name")
        if isinstance(label, str):
            output.append({"alias": label, "canonical_id": canonical})
    # Deterministic deduplication
    return sorted({(item["alias"].strip().lower(), item["canonical_id"]): item for item in output}.values(), key=lambda item: (item["alias"].lower(), item["canonical_id"]))
