# Source Mapping

## Knowledge Catalog intake

Only `okf/bundles/jvto/catalog.json` and concept paths referenced by that catalog are used. A concept must be both public and in an allowed release status (`reviewed`, `qualified`, `verified`, or `published`). The build intentionally ignores curation snapshots, drafts, private source material, and any concept not present in the public catalog.

| Output | Source | Transformation |
|---|---|---|
| `knowledge.ndjson` | `okf/bundles/jvto/catalog.json` + referenced Markdown | Filtered release-eligible concept projection |
| `retrieval-index.json` | Catalog metadata + frontmatter | Minimal lexical/semantic retrieval index |
| public policy / destination / tour content | Markdown concept body | Retained only as customer-facing public text |

## Itinerary Core intake

The agent runtime does not import `input/`, `seed/`, or raw cost data. It reads only generated artifacts and their quality metadata.

| Runtime output | Preferred Core artifact | Fallback | Purpose |
|---|---|---|---|
| package crosswalk | `package-catalog-index.json` | `package-route-map.json` | Package key alignment |
| location crosswalk | `location-alias-registry.json` | none | Customer terms → canonical location identifiers |
| core capabilities | `manifest.json`, `data-readiness-report.json` | none | Data maturity and guardrail context |
| route context | `route-leg-index.json` | legacy numbered route index | Feasibility integration context |
| scenario planning | Core service / contract response | none | Never inferred by the agent runtime |

## Never source from upstream into runtime knowledge

- raw customer data;
- raw booking or finance exports;
- vendor rates or historical cost estimates;
- payment data or receipts;
- secrets, credentials, private operational SOPs;
- generated draft concepts;
- free-form source snapshots that are not part of a public concept or core contract.
