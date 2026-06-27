from pathlib import Path

from jvto_agent_runtime.validator import validate_repo


def test_repository_contracts_are_valid():
    repo_root = Path(__file__).resolve().parents[1]
    assert validate_repo(repo_root)["status"] == "pass"
