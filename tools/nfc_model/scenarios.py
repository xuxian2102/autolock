"""Scenario schema, standard Autolock cases, and JSON/YAML loading."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import json
from importlib.resources import files
from pathlib import Path

import yaml

from .ferrite import FerriteSheet
from .metal import MetalObject


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str = ""
    door_material: str = "air"
    door_thickness_mm: float = 0.0
    phone_case: str = "Phone-M"
    phone_gap_mm: float = 2.0
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    tilt_x_deg: float = 0.0
    tilt_y_deg: float = 0.0
    metals: tuple[MetalObject, ...] = field(default_factory=tuple)
    ferrite: FerriteSheet = field(default_factory=FerriteSheet)
    l_tolerance_fraction: float = 0.0
    capacitance_tolerance_fraction: float = 0.0
    q_multiplier: float = 1.0
    metal_coefficient_scale: float = 1.0

    @property
    def total_separation_mm(self) -> float:
        return self.door_thickness_mm + self.phone_gap_mm

    def to_dict(self) -> dict:
        result = asdict(self)
        result["metals"] = [metal.to_dict() for metal in self.metals]
        result["ferrite"] = self.ferrite.to_dict()
        return result

    def with_updates(self, **updates) -> "Scenario":
        return replace(self, **updates)


def _from_mapping(data: dict) -> Scenario:
    values = dict(data)
    values["metals"] = tuple(MetalObject(**item) for item in values.get("metals", []))
    values["ferrite"] = FerriteSheet(**values.get("ferrite", {}))
    return Scenario(**values)


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    return _from_mapping(data)


def get_scenario(name: str) -> Scenario:
    normalized = name.upper()
    path = files("nfc_model").joinpath(f"data/scenarios/{normalized}.json")
    if not path.is_file():
        choices = ", ".join(standard_scenario_names())
        raise KeyError(f"unknown scenario {name!r}; choose {choices}")
    return _from_mapping(json.loads(path.read_text(encoding="utf-8")))


def standard_scenario_names() -> list[str]:
    directory = files("nfc_model").joinpath("data/scenarios")
    return sorted(path.name.removesuffix(".json") for path in directory.iterdir() if path.name.endswith(".json"))
