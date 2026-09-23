"""Scenarios: named sets of dispatcher rules switched on and off together.

A QA flow is rarely one mock. "Payment fails after login" is a successful
login, an empty wallet and a 500 from the payment call, all at once — and the
next flow needs a different set. Toggling rules one by one is where mocks get
left on by accident. A scenario names the set, so switching flows is a single
call and switching back is another.

A scenario only refers to rules by host and id; the rules and their fixtures
stay where they are. Deleting a scenario never deletes a rule.

Stored as ``<mock dir>/_scenarios/<name>.json``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ScenarioRuleRef(BaseModel):
    host: str = "*"
    id: str

    @property
    def label(self) -> str:
        return f"{self.host}/{self.id}"


class Scenario(BaseModel):
    version: Literal[1] = 1
    name: str
    description: str | None = None
    rules: list[ScenarioRuleRef] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_scenario_name(value)


def validate_scenario_name(name: str) -> str:
    """Lower-case letters, digits, dot, dash, underscore — a file name, never a path."""
    if not _NAME.match(name):
        raise ValueError(
            f"invalid scenario name `{name}`: use lower-case letters, digits, '.', '-' or '_', "
            "starting with a letter or digit, at most 64 characters"
        )
    return name


class ScenarioStore:
    def __init__(self, mock_dir: str | Path) -> None:
        self.root = Path(mock_dir) / "_scenarios"

    def path(self, name: str) -> Path:
        return self.root / f"{validate_scenario_name(name)}.json"

    def save(self, scenario: Scenario) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(scenario.name)
        path.write_text(scenario.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def get(self, name: str) -> Scenario:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"no scenario named `{name}`")
        return Scenario.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> tuple[list[Scenario], list[str]]:
        if not self.root.is_dir():
            return [], []
        scenarios: list[Scenario] = []
        errors: list[str] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                scenarios.append(Scenario.model_validate(json.loads(path.read_text("utf-8"))))
            except (ValueError, OSError) as exc:
                errors.append(f"{path.name}: {exc}")
        return scenarios, errors

    def remove(self, name: str) -> bool:
        path = self.path(name)
        if not path.is_file():
            return False
        path.unlink()
        return True
