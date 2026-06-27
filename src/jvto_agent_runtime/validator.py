from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .utils import read_json, read_yaml, write_json


def validate_repo(repo_root: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    for relative in [
        "config/upstreams.yaml",
        "config/data-ownership.yaml",
        "config/intent-routing.yaml",
        "config/tool-policy.yaml",
        "config/guardrails.yaml",
        "config/release-policy.yaml",
    ]:
        path = repo_root / relative
        if not path.exists():
            findings.append({"severity": "error", "message": f"Missing config: {relative}"})
            continue
        try:
            read_yaml(path)
        except Exception as error:
            findings.append({"severity": "error", "message": f"Invalid YAML {relative}: {error}"})
    for schema in (repo_root / "contracts").glob("*.json"):
        try:
            Draft202012Validator.check_schema(read_json(schema))
        except Exception as error:
            findings.append({"severity": "error", "message": f"Invalid schema {schema.name}: {error}"})
    return {"status": "pass" if not findings else "fail", "findings": findings}


def validate_release(repo_root: Path, release_dir: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    required = [
        "knowledge.ndjson", "retrieval-index.json", "package-crosswalk.json", "location-crosswalk.json",
        "core-capabilities.json", "intent-routing.json", "guardrails.json", "tool-policy.json",
        "data-ownership.json", "source-lock.json", "release-manifest.json"
    ]
    for name in required:
        if not (release_dir / name).exists():
            findings.append({"severity": "error", "message": f"Missing release file: {name}"})
    manifest = None
    if not findings:
        try:
            manifest = read_json(release_dir / "release-manifest.json")
            if manifest.get("customer_traffic_ready") is not False:
                findings.append({"severity": "error", "message": "Release must not self-mark customer_traffic_ready."})
            source_lock = read_json(release_dir / "source-lock.json")
            if not source_lock.get("knowledge_catalog", {}).get("revision"):
                findings.append({"severity": "error", "message": "Source lock has no knowledge revision."})
            if not source_lock.get("itinerary_core", {}).get("revision"):
                findings.append({"severity": "error", "message": "Source lock has no core revision."})
            for line in (release_dir / "knowledge.ndjson").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    if record.get("catalog_status") not in {"reviewed", "qualified", "verified", "published"}:
                        findings.append({"severity": "error", "message": f"Non-release knowledge status: {record.get('runtime_knowledge_id')}"})
                    if record.get("visibility") != "public":
                        findings.append({"severity": "error", "message": f"Non-public knowledge record: {record.get('runtime_knowledge_id')}"})
        except Exception as error:
            findings.append({"severity": "error", "message": f"Release parse failure: {error}"})
    result = {"status": "pass" if not findings else "fail", "findings": findings, "release_id": manifest.get("release_id") if manifest else None}
    write_json(release_dir / "validation-report.json", result)
    return result
