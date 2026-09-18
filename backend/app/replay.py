from __future__ import annotations

import math

from .optimizer import CompiledDirectives
from .schemas import Battery, HourEntry, HourlyPlanEntry

TOLERANCE = 0.01


def validate_plan(
    plan: list[HourlyPlanEntry], hours: list[HourEntry], battery: Battery, compiled: CompiledDirectives
) -> tuple[float, float, float]:
    if len(plan) != 24 or [entry.hour for entry in plan] != list(range(24)):
        raise ValueError("plan must contain one ordered entry per hour")
    previous = battery.initial_energy_kwh
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0
    for hour, entry in enumerate(plan):
        values = (entry.grid_kwh, entry.solar_used_kwh, entry.battery_kwh, entry.battery_energy_after_kwh)
        if not all(math.isfinite(value) and value >= -TOLERANCE for value in values):
            raise ValueError("plan contains invalid numeric values")
        action = entry.battery_action
        charge = entry.battery_kwh if action == "charge" else 0.0
        discharge = entry.battery_kwh if action == "discharge" else 0.0
        if action == "idle" and entry.battery_kwh > TOLERANCE:
            raise ValueError("idle action must have zero battery_kwh")
        if action == "charge" and hour in compiled.no_charge_hours:
            raise ValueError("charge directive violated")
        if action == "discharge" and hour in compiled.no_discharge_hours:
            raise ValueError("discharge directive violated")
        if charge > battery.max_charge_kwh_per_hour + TOLERANCE or discharge > battery.max_discharge_kwh_per_hour + TOLERANCE:
            raise ValueError("battery rate limit violated")
        if entry.solar_used_kwh > compiled.effective_solar[hour] + TOLERANCE:
            raise ValueError("solar availability violated")
        if entry.grid_kwh < -TOLERANCE or entry.solar_used_kwh < -TOLERANCE:
            raise ValueError("energy value cannot be negative")
        expected_after = previous + charge - discharge
        if abs(entry.battery_energy_after_kwh - expected_after) > TOLERANCE:
            raise ValueError("battery transition violated")
        if entry.battery_energy_after_kwh < compiled.minimum_reserve[hour] - TOLERANCE:
            raise ValueError("battery reserve violated")
        if entry.battery_energy_after_kwh > battery.capacity_kwh + TOLERANCE:
            raise ValueError("battery capacity violated")
        expected_balance = hours[hour].demand_kwh + charge
        actual_balance = entry.grid_kwh + entry.solar_used_kwh + discharge
        if abs(actual_balance - expected_balance) > TOLERANCE:
            raise ValueError("energy balance violated")
        cap = compiled.grid_caps[hour]
        if cap is not None and entry.grid_kwh > cap + TOLERANCE:
            raise ValueError("grid cap violated")
        total_grid += entry.grid_kwh
        total_cost += entry.grid_kwh * hours[hour].tariff_bdt_per_kwh
        peak_grid = max(peak_grid, entry.grid_kwh)
        previous = entry.battery_energy_after_kwh
    if abs(previous - battery.initial_energy_kwh) > TOLERANCE:
        raise ValueError("end-of-day battery neutrality violated")
    return total_grid, total_cost, peak_grid
