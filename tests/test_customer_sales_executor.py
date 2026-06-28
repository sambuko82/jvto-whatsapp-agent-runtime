import json
from pathlib import Path

from jvto_agent_runtime.contracts import is_valid, iter_contract_errors
from jvto_agent_runtime.customer_sales_executor import CustomerSalesExecutor
from jvto_agent_runtime.decision_engine import build_decision
from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.sales_intelligence import derive_response_plan, load_customer_sales_config
from jvto_agent_runtime.validator import validate_release

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_customer_sales_config(REPO_ROOT)
TUMPAK = "tumpak-sewu-bromo-ijen-4d3n"


def _release() -> Path:
    return build_release(
        REPO_ROOT,
        REPO_ROOT / "tests/fixtures/knowledge_catalog",
        REPO_ROOT / "tests/fixtures/itinerary_core",
        "test-m1-release",
        overwrite=True,
    )


def _brief(**kw):
    base = {"schema_version": "trip-brief-v1", "plan_version": 1}
    base.update(kw)
    return base


# --- release projection + validation ---------------------------------------


def test_release_projects_and_validates_customer_sales():
    release = _release()
    assert (release / "customer-sales" / "standard-price-tiers.json").exists()
    assert (release / "customer-sales" / "package-profiles.json").exists()
    report = validate_release(REPO_ROOT, release)
    assert report["status"] == "pass", report["findings"]


# --- catalog + price executor primitives -----------------------------------


def test_catalog_lookup_resolves_with_inclusions_and_endpoint():
    ex = CustomerSalesExecutor(_release())
    cat = ex.catalog_lookup(TUMPAK)
    assert cat["status"] == "resolved"
    assert cat["title"]
    assert cat["inclusions"]["included"]  # check 4: inclusions present
    # check 5: finish-Bali endpoint chain resolved (ferry/Ketapang/Bali)
    opts = " ".join(cat["endpoint"]["standard_dropoff_options"]).lower()
    assert "ketapang" in opts or "bali" in opts


def test_standard_price_exact_per_person_and_group_total():
    ex = CustomerSalesExecutor(_release())
    p = ex.standard_price_lookup(TUMPAK, 4)
    assert p["status"] == "priced"
    assert p["per_person"] == 4050000          # check 2: exact published per-person
    assert p["group_total"] == 16200000        # check 3: group total = per_person * pax
    assert p["currency"] == "IDR"


def test_standard_price_solo_tier():
    ex = CustomerSalesExecutor(_release())
    p = ex.standard_price_lookup(TUMPAK, 1)
    assert p["status"] == "priced" and p["per_person"] == 8050000


def test_below_minimum_pax_is_custom_quote():
    ex = CustomerSalesExecutor(_release())
    p = ex.standard_price_lookup("bromo-1d1n", 1)  # tiers start at 2 pax
    assert p["status"] == "custom_quote_required"
    assert p["reason"] == "below_minimum_pax"


def test_unavailable_data_never_invented():
    ex = CustomerSalesExecutor(_release())
    cat = ex.catalog_lookup("does-not-exist")
    price = ex.standard_price_lookup("does-not-exist", 4)
    assert cat["status"] == "not_found"
    assert price["status"] == "unavailable"
    assert "per_person" not in price  # check 11: no fabricated numbers


def test_rooming_excluded_for_day_trip_but_present_for_overnight():
    ex = CustomerSalesExecutor(_release())
    assert ex.catalog_lookup("bromo-1d1n")["rooming"] is None      # check 6: day trip, no lodging
    assert ex.catalog_lookup(TUMPAK)["rooming"]["overnights"]      # overnight package has rooming


# --- end-to-end flow: envelope -> response plan -> resolved context ---------


def _resolve(intent, query, trip_brief):
    release = _release()
    envelope = build_decision(release, intent, query, {})
    plan = derive_response_plan(envelope, trip_brief, CONFIG, query=query)
    ctx = CustomerSalesExecutor(release).resolve(plan, trip_brief)
    assert is_valid("resolved-customer-context", ctx), iter_contract_errors("resolved-customer-context", ctx)
    return ctx


def test_end_to_end_price_quote():
    ctx = _resolve("check_price", "How much is the Bromo Ijen package for 4?",
                   _brief(selected_package_key=TUMPAK, pax={"confirmed": 4}))
    assert ctx["pricing_resolved"]["status"] == "priced"
    assert ctx["pricing_resolved"]["group_total"] == 16200000
    assert any("Availability is not yet confirmed" in d for d in ctx["required_disclosures"])
    # check 12: no PII / internal commercial fields leak into the resolved context
    dumped = json.dumps(ctx).lower()
    for forbidden in ("driver_cost", "escort_cost", "supplier", "margin", "vendor", "backoffice_observed", "passport", "customer_phone"):
        assert forbidden not in dumped


def test_end_to_end_inclusion_query():
    ctx = _resolve("query_package_details", "What is included in the Bromo Ijen package?",
                   _brief(selected_package_key=TUMPAK))
    assert ctx["catalog_resolved"]["status"] == "resolved"
    assert ctx["catalog_resolved"]["inclusions"]["included"]


def test_end_to_end_discovery_incomplete_no_price():
    ctx = _resolve("query_package_details", "We want Bromo and Ijen, what are the options?", _brief())
    assert ctx["catalog_resolved"]["status"] in {"incomplete", "not_found"}
    assert ctx["pricing_resolved"]["status"] in {"unavailable", "custom_quote_required"}
    assert "group_total" not in ctx["pricing_resolved"]
