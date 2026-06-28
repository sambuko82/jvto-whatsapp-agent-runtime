from __future__ import annotations

import argparse
import json
from pathlib import Path

from .decision_engine import build_decision
from .deployment import create_approval, deployment_gate, verify_deployment_approval
from .feasibility import NotConnectedEvaluator, evaluate_feasibility
from .live_tools import NotConnectedLiveToolAdapter, execute_live_tool
from .release_builder import build_release
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
    build.add_argument("--release-id", required=True)
    build.add_argument("--overwrite", action="store_true")

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

    args = parser.parse_args()
    root = _repo_root()
    if args.command == "validate-repo":
        result = validate_repo(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0 if result["status"] == "pass" else 1)
    if args.command == "build-release":
        release_dir = build_release(root, Path(args.knowledge_root), Path(args.core_root), args.release_id, args.overwrite)
        print(str(release_dir))
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


if __name__ == "__main__":
    main()
