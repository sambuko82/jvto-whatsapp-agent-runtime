"""API + CLI surface for the Runtime Monolith DeliveryPlan resolver.

These call the endpoint handler function directly (the project has no httpx, so
fastapi.testclient.TestClient is unavailable) and build a real monolith release
from the standard fixtures, proving the supported API path returns a
contract-valid DeliveryPlan generated only from one local release.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from jvto_agent_runtime.api import DeliveryPlanRequest, delivery_plan
from jvto_agent_runtime.cli import main as cli_main
from jvto_agent_runtime.contracts import is_valid
from jvto_agent_runtime.release_builder import build_release

REPO_ROOT = Path(__file__).resolve().parents[1]
BALI_PKG = "bali/ijen-bromo-madakaripura-3d2n"
SBY_PKG = "ijen-bromo-madakaripura-3d2n"


def _release(release_id: str, *, web: bool = True) -> Path:
    return build_release(
        REPO_ROOT,
        REPO_ROOT / "tests/fixtures/knowledge_catalog",
        REPO_ROOT / "tests/fixtures/itinerary_core",
        release_id,
        overwrite=True,
        web_root=(REPO_ROOT / "tests/fixtures/web_experience") if web else None,
    )


def test_delivery_plan_endpoint_returns_contract_valid_plan():
    release = _release("test-api-dp")
    plan = delivery_plan(DeliveryPlanRequest(
        release_dir=str(release), customer_job="J2_price_and_value",
        query="how much for 4 guests?", package_key=BALI_PKG, customer_context={"pax": 4},
    ))
    assert is_valid("delivery-plan", plan)
    assert plan["message_mode"] in ("standard_price", "handoff")
    assert plan["package_key"] == BALI_PKG


def test_delivery_plan_endpoint_origin_correct_links():
    release = _release("test-api-dp-origin")
    bali = delivery_plan(DeliveryPlanRequest(
        release_dir=str(release), customer_job="J2_price_and_value", query="how much",
        package_key=BALI_PKG, customer_context={"pax": 2},
    ))
    bpl = bali["resolved_primary_link"]
    assert bpl and bpl["sendable"] and "from-bali/ijen-bromo-madakaripura-3d2n" in bpl["url"]
    assert "from-surabaya" not in bpl["url"]

    sby = delivery_plan(DeliveryPlanRequest(
        release_dir=str(release), customer_job="J2_price_and_value", query="how much",
        package_key=SBY_PKG, customer_context={"pax": 2},
    ))
    spl = sby["resolved_primary_link"]
    assert spl and spl["sendable"] and "from-surabaya/ijen-bromo-madakaripura-3d2n" in spl["url"]
    assert "from-bali" not in spl["url"]


def test_delivery_plan_endpoint_custom_quote_has_no_booking_cta():
    release = _release("test-api-dp-custom")
    plan = delivery_plan(DeliveryPlanRequest(
        release_dir=str(release), customer_job="J2_price_and_value", query="price",
        package_key=BALI_PKG, customer_context={"pax": 2, "own_hotel": True},
    ))
    assert plan["message_mode"] == "handoff"
    assert plan["handoff"]["required"] is True
    assert plan["secondary_link_intent"] is None
    assert is_valid("delivery-plan", plan)


def test_delivery_plan_endpoint_no_invented_visual():
    release = _release("test-api-dp-visual")
    plan = delivery_plan(DeliveryPlanRequest(
        release_dir=str(release), customer_job="J2_price_and_value", query="how much",
        package_key=BALI_PKG, customer_context={"pax": 2},
    ))
    rv = plan["resolved_visual"]
    assert rv is None or rv["sendable"] is False  # every asset is to_create today


def test_delivery_plan_endpoint_text_only_release_is_safe():
    release = _release("test-api-dp-noweb", web=False)
    plan = delivery_plan(DeliveryPlanRequest(
        release_dir=str(release), customer_job="J2_price_and_value", query="how much",
        package_key=BALI_PKG, customer_context={"pax": 2},
    ))
    assert is_valid("delivery-plan", plan)
    rl = plan["resolved_primary_link"]
    assert rl is None or rl["sendable"] is False  # no web registry -> nothing sendable
    assert plan["resolved_visual"] is None or plan["resolved_visual"]["sendable"] is False


def test_delivery_plan_endpoint_missing_release_path_404():
    with pytest.raises(HTTPException) as exc:
        delivery_plan(DeliveryPlanRequest(release_dir="/no/such/release", query="x"))
    assert exc.value.status_code == 404


def test_delivery_plan_endpoint_release_without_agent_catalog_404(tmp_path: Path):
    # An existing dir that is not a built monolith release must fail cleanly, not 500.
    with pytest.raises(HTTPException) as exc:
        delivery_plan(DeliveryPlanRequest(release_dir=str(tmp_path), query="x"))
    assert exc.value.status_code == 404


def test_cli_delivery_plan_parity(capsys):
    import json
    release = _release("test-cli-dp")
    cli_main_argv = [
        "delivery-plan", "--release-dir", str(release),
        "--customer-job", "J2_price_and_value", "--query", "how much",
        "--package-key", BALI_PKG, "--customer-context", '{"pax": 2}',
    ]
    import sys
    old = sys.argv
    sys.argv = ["jvto-agent", *cli_main_argv]
    try:
        cli_main()
    finally:
        sys.argv = old
    out = capsys.readouterr().out
    plan = json.loads(out)
    assert is_valid("delivery-plan", plan)
    assert "from-bali" in plan["resolved_primary_link"]["url"]
