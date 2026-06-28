"""Tests for the Phase D resolver layer: module / presentation / link / asset.

Run against the real generated artifacts (vendored under tests/fixtures/agent_modules)
so the tests prove the whole chain works on production-shaped data, including the
hard guarantees: no invented URLs, no invented visuals, no booking CTA on a custom
quote, and a contract-valid DeliveryPlan.
"""
from pathlib import Path

import pytest

from jvto_agent_runtime.asset_resolver import load_media_registry, resolve_asset
from jvto_agent_runtime.contracts import is_valid
from jvto_agent_runtime.link_resolver import load_link_registry, resolve_link
from jvto_agent_runtime.module_resolver import classify_topic, load_module_layer, resolve_modules
from jvto_agent_runtime.presentation_resolver import build_delivery_plan, evaluate_quote_eligibility

FIX = Path(__file__).resolve().parent / "fixtures" / "agent_modules"
BALI_PKG = "bali/bromo-ijen-3d2n"
IJEN_PKG = "ijen-2d1n"


@pytest.fixture(scope="module")
def layer():
    return load_module_layer(FIX)


@pytest.fixture(scope="module")
def links():
    return load_link_registry(FIX)


@pytest.fixture(scope="module")
def media():
    return load_media_registry(FIX)


# --- module resolver -------------------------------------------------------

def test_module_layer_loads_all_modules(layer):
    assert len(layer.general) == 67
    assert len(layer.variations) == 16


@pytest.mark.parametrize("query,expected", [
    ("How much for 4 people?", "price"),
    ("What is included?", "inclusions"),
    ("Is it private?", "private_tour"),
    ("What vehicle will we use?", "vehicle"),
    ("Can we finish in Bali?", "route_endpoint"),
    ("What do we need for Ijen?", "destination_readiness"),
    ("How do I book?", "booking"),
    ("Is blue fire guaranteed?", "blue_fire"),
])
def test_topic_classification(query, expected):
    assert classify_topic(None, query) == expected


def test_resolve_modules_never_references_unknown_module(layer):
    for pkey in layer.variations:
        for topic in ("inclusions", "price", "vehicle", "route_endpoint", "destination_readiness", "booking"):
            rm = resolve_modules(layer, topic=topic, package_key=pkey)
            for mid in rm.general_module_refs:
                assert mid in layer.general, f"{pkey}/{topic}: unknown module {mid}"
            for mid in rm.package_variation_refs:
                assert mid in layer.general


def test_variation_never_without_baseline(layer):
    # inclusion additions must coexist with the all-inclusive baseline general module
    rm = resolve_modules(layer, topic="inclusions", package_key=BALI_PKG)
    assert "inclusion_all_inclusive_baseline" in rm.general_module_refs
    assert "inclusion_east_java_bali_ferry" in rm.package_variation_refs  # Bali = ferry


def test_ijen_disclosures(layer):
    rm = resolve_modules(layer, topic="destination_readiness", package_key=IJEN_PKG)
    joined = " ".join(rm.required_disclosures).lower()
    assert "blue fire" in joined
    assert "health screening" in joined


# --- link resolver (never invent a URL) ------------------------------------

def test_link_existing_package_page(links):
    r = resolve_link(links, "package_bromo_ijen_3d2n")
    assert r.sendable and r.url and r.url.startswith("https://")


def test_link_missing_page_is_not_sendable(links):
    r = resolve_link(links, "what_is_included")  # /guides/* page does not exist yet
    assert r.status == "page_missing"
    assert not r.sendable and r.url is None


def test_unknown_link_key_is_not_sendable(links):
    r = resolve_link(links, "totally_made_up_key")
    assert r.status == "unknown" and not r.sendable and r.url is None


# --- asset resolver (never invent a visual) --------------------------------

def test_all_assets_not_sendable_today(media):
    # audit: no media exists yet -> nothing sendable
    r = resolve_asset(media, "all_inclusive_card")
    assert r.status == "to_create" and not r.sendable and r.url is None


def test_unknown_asset_not_sendable(media):
    r = resolve_asset(media, "no_such_card")
    assert r.status == "unknown" and not r.sendable


# --- quote eligibility -----------------------------------------------------

def test_quote_standard_eligible():
    q = evaluate_quote_eligibility("price", {"pax": 4})
    assert q["status"] == "standard_price_eligible"


def test_quote_custom_required():
    q = evaluate_quote_eligibility("price", {"own_hotel": True, "special_luggage": True})
    assert q["status"] == "custom_quote_required"
    assert "own_hotel" in q["reasons"] and "special_luggage" in q["reasons"]


def test_quote_not_a_price_request():
    assert evaluate_quote_eligibility("vehicle", {})["status"] == "not_a_price_request"


# --- delivery plan end to end ----------------------------------------------

def test_price_delivery_plan_is_contract_valid(layer, links, media):
    plan = build_delivery_plan(layer, links, media, customer_job="J2_price_and_value",
                               query="How much for 4 guests?", package_key=BALI_PKG,
                               customer_context={"pax": 4})
    assert is_valid("delivery-plan", plan)
    assert plan["message_mode"] == "standard_price"
    assert plan["max_text_lines"] == 4
    assert plan["resolved_primary_link"]["sendable"] is True  # package page exists
    assert plan["resolved_visual"] is None or plan["resolved_visual"]["sendable"] is False
    assert any("Availability" in d for d in plan["required_disclosures"])


def test_custom_quote_has_no_booking_cta(layer, links, media):
    plan = build_delivery_plan(layer, links, media, customer_job="J2_price_and_value",
                               query="price for 4", package_key=BALI_PKG,
                               customer_context={"pax": 4, "own_hotel": True})
    assert plan["message_mode"] == "handoff"
    assert plan["handoff"]["required"] is True
    assert plan["secondary_link_intent"] is None  # no booking CTA on custom quote
    assert plan["quote_eligibility"]["status"] == "custom_quote_required"
    assert is_valid("delivery-plan", plan)


def test_every_package_price_plan_is_valid(layer, links, media):
    for pkey in layer.variations:
        plan = build_delivery_plan(layer, links, media, customer_job="J2_price_and_value",
                                   query="how much", package_key=pkey, customer_context={"pax": 2})
        assert is_valid("delivery-plan", plan), pkey
        # never a fabricated URL
        for k in ("resolved_primary_link", "resolved_secondary_link"):
            lr = plan[k]
            if lr is not None and lr["url"] is not None:
                assert lr["sendable"] is True


def test_inclusion_plan_surfaces_package_additions(layer, links, media):
    plan = build_delivery_plan(layer, links, media, query="what is included?", package_key=IJEN_PKG)
    assert plan["message_mode"] == "inclusion_explanation"
    assert "inclusion_ijen_equipment" in plan["package_variation_refs"]
    assert "inclusion_all_inclusive_baseline" in plan["general_module_refs"]
