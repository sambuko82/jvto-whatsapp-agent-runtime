from __future__ import annotations

import argparse
import json
from pathlib import Path

from .customer_sales_executor import CustomerSalesExecutor
from .decision_engine import build_decision
from .deployment import create_approval, deployment_gate, verify_deployment_approval
from .feasibility import NotConnectedEvaluator, evaluate_feasibility
from .live_tools import NotConnectedLiveToolAdapter, execute_live_tool
from .delivery_adapter import delivery_plan_from_decision
from .presentation_resolver import resolve_delivery_plan
from .release_builder import build_local_catalog, build_release, local_catalog_root
from .response_composer import compose_customer_response
from .sales_intelligence import derive_response_plan, load_customer_sales_config, merge_trip_brief
from .utils import read_json, utc_now, write_json
from .validator import validate_release, validate_repo


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(prog="jvto-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-repo")

    build = sub.add_parser("build-release")
    build.add_argument("--knowledge-root", required=True)
    build.add_argument("--core-root", required=True)
    build.add_argument("--web-root", help="jvto-web checkout root; vendors the link/media capability registries into the release agent-catalog")
    build.add_argument("--release-id", required=True)
    build.add_argument("--overwrite", action="store_true")

    local_cat = sub.add_parser("build-local-catalog", help="Regenerate the committed compact catalog (catalog/) from upstreams")
    local_cat.add_argument("--knowledge-root", required=True)
    local_cat.add_argument("--core-root", required=True)
    local_cat.add_argument("--web-root")
    local_cat.add_argument("--out", help="output dir (default: <repo>/catalog)")

    release = sub.add_parser("validate-release")
    release.add_argument("--release-dir", required=True)

    decide = sub.add_parser("decide")
    decide.add_argument("--release-dir", required=True)
    decide.add_argument("--intent", required=True)
    decide.add_argument("--query", default="")
    decide.add_argument("--entities", default="{}")
    decide.add_argument("--intent-confidence", type=float, default=1.0)
    decide.add_argument("--output")

    feasibility = sub.add_parser("feasibility")
    feasibility.add_argument("--release-dir", required=True)
    feasibility.add_argument("--entities", default="{}")
    feasibility.add_argument("--output")

    live_tool = sub.add_parser("live-tool")
    live_tool.add_argument("--release-dir", required=True)
    live_tool.add_argument("--tool", required=True)
    live_tool.add_argument("--params", default="{}")
    live_tool.add_argument("--intent")
    live_tool.add_argument("--output")

    gate = sub.add_parser("deployment-gate")
    gate.add_argument("--release-dir", required=True)
    gate.add_argument("--output")

    approve = sub.add_parser("create-deployment-approval")
    approve.add_argument("--release-dir", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--output")

    verify_deploy = sub.add_parser("verify-deployment")
    verify_deploy.add_argument("--release-dir", required=True)
    verify_deploy.add_argument("--approval", required=True)
    verify_deploy.add_argument("--output")

    response_plan = sub.add_parser("build-response-plan")
    response_plan.add_argument("--decision-envelope", required=True)
    response_plan.add_argument("--trip-brief")
    response_plan.add_argument("--query", default="")
    response_plan.add_argument("--output")

    merge_brief = sub.add_parser("merge-trip-brief")
    merge_brief.add_argument("--base")
    merge_brief.add_argument("--update", required=True)
    merge_brief.add_argument("--output")

    resolve_ctx = sub.add_parser("resolve-customer-context")
    resolve_ctx.add_argument("--release-dir", required=True)
    resolve_ctx.add_argument("--response-plan", required=True)
    resolve_ctx.add_argument("--trip-brief")
    resolve_ctx.add_argument("--output")

    std_price = sub.add_parser("standard-price")
    std_price.add_argument("--release-dir", required=True)
    std_price.add_argument("--package-key", required=True)
    std_price.add_argument("--pax", type=int, required=True)
    std_price.add_argument("--output")

    delivery = sub.add_parser("delivery-plan")
    delivery.add_argument("--release-dir", required=True)
    delivery.add_argument("--customer-job")
    delivery.add_argument("--query", default="")
    delivery.add_argument("--package-key")
    delivery.add_argument("--customer-context", default="{}", help="JSON object of presentation context, e.g. '{\"pax\": 4}'")
    delivery.add_argument("--output")

    delivery_from = sub.add_parser("delivery-plan-from-decision")
    delivery_from.add_argument("--release-dir", required=True)
    delivery_from.add_argument("--decision-envelope", required=True, help="Path to a DecisionEnvelope JSON file")
    delivery_from.add_argument("--trip-brief", help="Optional path to a TripBrief JSON file")
    delivery_from.add_argument("--query", default="")
    delivery_from.add_argument("--output")

    customer_resp = sub.add_parser("customer-response")
    customer_resp.add_argument("--release-dir", help="defaults to the committed local catalog (catalog/)")
    customer_resp.add_argument("--decision-envelope", required=True, help="Path to a DecisionEnvelope JSON file")
    customer_resp.add_argument("--trip-brief", help="Optional path to a TripBrief JSON file")
    customer_resp.add_argument("--query", default="")
    customer_resp.add_argument("--output")

    args = parser.parse_args()
    root = _repo_root()
    if args.command == "validate-repo":
        result = validate_repo(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "pass" else 1)
    if args.command == "build-release":
        web_root = Path(args.web_root) if args.web_root else None
        release_dir = build_release(
            root, Path(args.knowledge_root), Path(args.core_root), args.release_id, args.overwrite, web_root=web_root
        )
        print(str(release_dir))
        return
    if args.command == "build-local-catalog":
        out = build_local_catalog(
            root, Path(args.knowledge_root), Path(args.core_root),
            out_dir=Path(args.out) if args.out else None,
            web_root=Path(args.web_root) if args.web_root else None,
        )
        print(str(out))
        return
    if args.command == "validate-release":
        result = validate_release(root, Path(args.release_dir))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "pass" else 1)
    if args.command == "decide":
        entities = json.loads(args.entities)
        result = build_decision(Path(args.release_dir), args.intent, args.query, entities, args.intent_confidence)
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "feasibility":
        entities = json.loads(args.entities)
        result = evaluate_feasibility(Path(args.release_dir), entities, NotConnectedEvaluator())
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "live-tool":
        params = json.loads(args.params)
        result = execute_live_tool(
            Path(args.release_dir), args.tool, params, intent=args.intent, adapter=NotConnectedLiveToolAdapter()
        )
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "deployment-gate":
        result = deployment_gate(root, Path(args.release_dir))
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["ready_for_approval"] else 1)
    if args.command == "create-deployment-approval":
        result = create_approval(root, Path(args.release_dir), args.approved_by, utc_now())
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "verify-deployment":
        approval = read_json(Path(args.approval))
        result = verify_deployment_approval(root, Path(args.release_dir), approval)
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["customer_traffic_ready"] else 1)
    if args.command == "build-response-plan":
        config = load_customer_sales_config(root)
        envelope = read_json(Path(args.decision_envelope))
        trip_brief = read_json(Path(args.trip_brief)) if args.trip_brief else None
        result = derive_response_plan(envelope, trip_brief, config, query=args.query)
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "merge-trip-brief":
        config = load_customer_sales_config(root)
        base = read_json(Path(args.base)) if args.base else None
        result = merge_trip_brief(base, read_json(Path(args.update)), config)
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "resolve-customer-context":
        executor = CustomerSalesExecutor(Path(args.release_dir))
        plan = read_json(Path(args.response_plan))
        brief = read_json(Path(args.trip_brief)) if args.trip_brief else None
        result = executor.resolve(plan, brief)
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "standard-price":
        executor = CustomerSalesExecutor(Path(args.release_dir))
        result = executor.standard_price_lookup(args.package_key, args.pax)
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "delivery-plan":
        try:
            customer_context = json.loads(args.customer_context)
        except json.JSONDecodeError as error:
            raise SystemExit(f"--customer-context must be valid JSON: {error}") from error
        if not isinstance(customer_context, dict):
            raise SystemExit("--customer-context must be a JSON object, e.g. '{\"pax\": 4}'")
        result = resolve_delivery_plan(
            Path(args.release_dir),
            customer_job=args.customer_job,
            query=args.query,
            package_key=args.package_key or None,
            customer_context=customer_context,
        )
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "delivery-plan-from-decision":
        envelope = read_json(Path(args.decision_envelope))
        trip_brief = read_json(Path(args.trip_brief)) if args.trip_brief else None
        config = load_customer_sales_config(root)
        result = delivery_plan_from_decision(
            Path(args.release_dir), envelope, trip_brief=trip_brief, query=args.query, config=config
        )
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "customer-response":
        envelope = read_json(Path(args.decision_envelope))
        trip_brief = read_json(Path(args.trip_brief)) if args.trip_brief else None
        config = load_customer_sales_config(root)
        release_dir = Path(args.release_dir) if args.release_dir else local_catalog_root(root)
        result = compose_customer_response(
            release_dir, envelope, trip_brief=trip_brief, query=args.query, config=config
        )
        if args.output:
            write_json(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
