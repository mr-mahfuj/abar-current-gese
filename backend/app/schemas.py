from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @staticmethod
    def _finite_non_negative(value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("value must be finite and non-negative")
        return value


class HourEntry(StrictModel):
    hour: int
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

    _demand_valid = field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")(
        StrictModel._finite_non_negative
    )


class Battery(StrictModel):
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

    _values_valid = field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    )(StrictModel._finite_non_negative)

    @model_validator(mode="after")
    def validate_relationships(self) -> "Battery":
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be below minimum_energy_kwh")
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        return self


class OptimizeRequest(StrictModel):
    scenario_id: str = Field(min_length=1, max_length=200)
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourEntry] = Field(min_length=24, max_length=24)
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def notes_must_be_non_empty(cls, notes: list[str]) -> list[str]:
        if any(not note.strip() for note in notes):
            raise ValueError("operator notes must be non-empty")
        return notes

    @model_validator(mode="after")
    def validate_hours(self) -> "OptimizeRequest":
        actual = [entry.hour for entry in self.hours]
        if actual != list(range(24)):
            raise ValueError("hours must contain exactly 0 through 23 in order")
        return self


DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


class DirectiveInterpretation(StrictModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: dict | None
    explanation: str = Field(min_length=1, max_length=500)


class HourlyPlanEntry(StrictModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float
    battery_energy_after_kwh: float

    _numbers_valid = field_validator(
        "grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"
    )(StrictModel._finite_non_negative)


class OptimizeResponse(StrictModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str

    _totals_valid = field_validator(
        "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"
    )(StrictModel._finite_non_negative)
