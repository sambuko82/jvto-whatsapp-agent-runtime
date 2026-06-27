from pathlib import Path

from jvto_agent_runtime.release_builder import build_release
from jvto_agent_runtime.utils import read_json
from jvto_agent_runtime.validator import validate_release


def test_build_release_from_fixture(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    release_dir = build_release(
        repo_root,
        repo_root / "tests/fixtures/knowledge_catalog",
        repo_root / "tests/fixtures/itinerary_core",
        "test-release",
        overwrite=True,
    )
    manifest = read_json(release_dir / "release-manifest.json")
    assert manifest["knowledge_record_count"] == 2
    assert manifest["customer_traffic_ready"] is False
    crosswalk = read_json(release_dir / "package-crosswalk.json")
    assert crosswalk["summary"]["matched"] == 1
    report = validate_release(repo_root, release_dir)
    assert report["status"] == "pass"
