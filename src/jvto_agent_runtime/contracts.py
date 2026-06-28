from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .utils import read_json


class ContractValidationError(ValueError):
    """Raised when an instance does not satisfy a named JSON Schema contract."""

    def __init__(self, contract: str, errors: list[str]) -> None:
        self.contract = contract
        self.errors = errors
        super().__init__(f"Instance does not satisfy contract {contract!r}: " + "; ".join(errors))


def contracts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "contracts"


def contract_path(name: str) -> Path:
    """Resolve a contract name to its schema file.

    Accepts ``itinerary-core-request``, ``itinerary-core-request.schema``, or
    ``itinerary-core-request.schema.json`` and normalizes to the on-disk path.
    """
    stem = name
    if stem.endswith(".json"):
        stem = stem[: -len(".json")]
    if stem.endswith(".schema"):
        stem = stem[: -len(".schema")]
    return contracts_dir() / f"{stem}.schema.json"


@lru_cache(maxsize=None)
def load_contract(name: str) -> dict[str, Any]:
    return read_json(contract_path(name))


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    # FORMAT_CHECKER enforces `format` keywords (e.g. travel_date `format: "date"`),
    # which draft 2020-12 treats as annotation-only by default. The `date` checker is
    # stdlib-backed, so this adds no dependency.
    return Draft202012Validator(load_contract(name), format_checker=Draft202012Validator.FORMAT_CHECKER)


def iter_contract_errors(name: str, instance: Any) -> list[str]:
    """Return human-readable validation errors (empty list means valid)."""
    validator = _validator(name)
    return [
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    ]


def is_valid(name: str, instance: Any) -> bool:
    return not iter_contract_errors(name, instance)


def validate_or_raise(name: str, instance: Any) -> None:
    errors = iter_contract_errors(name, instance)
    if errors:
        raise ContractValidationError(name, errors)
