from __future__ import annotations

import math
from typing import Any

ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}
REQUIRED_SHAPE = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "no_charge_window": {"hours"},
    "no_discharge_window": {"hours"},
    "max_grid_window": {"hours", "max_grid_kwh"},
}


def _safe_noop(note_index: int, reason: str = "Guardrail rejected output; defaulted to no_op.") -> dict:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": reason,
    }


def _valid_hours(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(type(hour) is int and 0 <= hour <= 23 for hour in value)
        and value == sorted(set(value))
    )


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_and_fix(raw: Any, note_count: int, battery_capacity: float) -> list[dict]:
    by_index: dict[int, dict] = {}
    if not isinstance(raw, list):
        raw = []
    for entry in raw:
        try:
            if not isinstance(entry, dict):
                continue
            index = entry.get("note_index")
            if type(index) is not int or not 0 <= index < note_count or index in by_index:
                continue
            directive_type = entry.get("directive_type")
            if directive_type not in ALLOWED_TYPES:
                by_index[index] = _safe_noop(index)
                continue
            if directive_type == "no_op":
                if entry.get("applies") is not False or entry.get("structured_adjustment") is not None:
                    by_index[index] = _safe_noop(index)
                else:
                    by_index[index] = {
                        "note_index": index,
                        "applies": False,
                        "directive_type": "no_op",
                        "structured_adjustment": None,
                        "explanation": str(entry.get("explanation") or "No schedule change applies."),
                    }
                continue
            adjustment = entry.get("structured_adjustment")
            if entry.get("applies") is not True or not isinstance(adjustment, dict):
                by_index[index] = _safe_noop(index)
                continue
            if set(adjustment) != REQUIRED_SHAPE[directive_type] or not _valid_hours(adjustment.get("hours")):
                by_index[index] = _safe_noop(index)
                continue
            if directive_type == "solar_reduction":
                factor = adjustment["factor"]
                valid = _finite_number(factor) and 0 <= factor <= 1
            elif directive_type == "minimum_battery_reserve":
                reserve = adjustment["minimum_energy_kwh"]
                valid = _finite_number(reserve) and 0 <= reserve <= battery_capacity
            elif directive_type == "max_grid_window":
                cap = adjustment["max_grid_kwh"]
                valid = _finite_number(cap) and cap >= 0
            else:
                valid = True
            if not valid:
                by_index[index] = _safe_noop(index)
                continue
            by_index[index] = {
                "note_index": index,
                "applies": True,
                "directive_type": directive_type,
                "structured_adjustment": adjustment,
                "explanation": str(entry.get("explanation") or "Directive applied."),
            }
        except Exception:
            continue
    return [by_index.get(index, _safe_noop(index)) for index in range(note_count)]
