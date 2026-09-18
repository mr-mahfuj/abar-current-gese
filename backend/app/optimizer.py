from __future__ import annotations

from dataclasses import dataclass

import pulp

from .schemas import Battery, DirectiveInterpretation, HourEntry, HourlyPlanEntry

EPSILON = 1e-7


@dataclass(frozen=True)
class CompiledDirectives:
    effective_solar: list[float]
    minimum_reserve: list[float]
    no_charge_hours: set[int]
    no_discharge_hours: set[int]
    grid_caps: list[float | None]


def compile_directives(
    hours: list[HourEntry], battery: Battery, directives: list[DirectiveInterpretation]
) -> CompiledDirectives:
    effective_solar = [entry.solar_kwh for entry in hours]
    minimum_reserve = [battery.minimum_energy_kwh] * 24
    no_charge_hours: set[int] = set()
    no_discharge_hours: set[int] = set()
    grid_caps: list[float | None] = [None] * 24
    for directive in directives:
        if not directive.applies or directive.structured_adjustment is None:
            continue
        adjustment = directive.structured_adjustment
        directive_type = directive.directive_type
        for hour in adjustment["hours"]:
            if directive_type == "solar_reduction":
                effective_solar[hour] = hours[hour].solar_kwh * float(adjustment["factor"])
            elif directive_type == "minimum_battery_reserve":
                minimum_reserve[hour] = max(minimum_reserve[hour], float(adjustment["minimum_energy_kwh"]))
            elif directive_type == "no_charge_window":
                no_charge_hours.add(hour)
            elif directive_type == "no_discharge_window":
                no_discharge_hours.add(hour)
            elif directive_type == "max_grid_window":
                cap = float(adjustment["max_grid_kwh"])
                grid_caps[hour] = cap if grid_caps[hour] is None else min(grid_caps[hour], cap)
    return CompiledDirectives(effective_solar, minimum_reserve, no_charge_hours, no_discharge_hours, grid_caps)


def solve(
    hours: list[HourEntry], battery: Battery, directives: list[DirectiveInterpretation]
) -> tuple[list[HourlyPlanEntry], CompiledDirectives]:
    compiled = compile_directives(hours, battery, directives)
    problem = pulp.LpProblem("gridwise_energy", pulp.LpMinimize)
    grid = {hour: pulp.LpVariable(f"grid_{hour}", lowBound=0) for hour in range(24)}
    solar = {
        hour: pulp.LpVariable(f"solar_{hour}", lowBound=0, upBound=compiled.effective_solar[hour])
        for hour in range(24)
    }
    charge = {
        hour: pulp.LpVariable(
            f"charge_{hour}", lowBound=0,
            upBound=0 if hour in compiled.no_charge_hours else battery.max_charge_kwh_per_hour,
        ) for hour in range(24)
    }
    discharge = {
        hour: pulp.LpVariable(
            f"discharge_{hour}", lowBound=0,
            upBound=0 if hour in compiled.no_discharge_hours else battery.max_discharge_kwh_per_hour,
        ) for hour in range(24)
    }
    energy = {
        hour: pulp.LpVariable(
            f"energy_{hour}", lowBound=compiled.minimum_reserve[hour], upBound=battery.capacity_kwh
        ) for hour in range(24)
    }
    problem += pulp.lpSum(
        grid[hour] * hours[hour].tariff_bdt_per_kwh + EPSILON * (charge[hour] + discharge[hour])
        for hour in range(24)
    )
    for hour in range(24):
        problem += grid[hour] + solar[hour] + discharge[hour] == hours[hour].demand_kwh + charge[hour]
        previous = battery.initial_energy_kwh if hour == 0 else energy[hour - 1]
        problem += energy[hour] == previous + charge[hour] - discharge[hour]
        if compiled.grid_caps[hour] is not None:
            problem += grid[hour] <= compiled.grid_caps[hour]
    problem += energy[23] == battery.initial_energy_kwh
    status = problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=20))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError("energy schedule is infeasible")

    plan: list[HourlyPlanEntry] = []
    previous_energy = battery.initial_energy_kwh
    for hour in range(24):
        charge_value = max(0.0, float(charge[hour].value() or 0.0))
        discharge_value = max(0.0, float(discharge[hour].value() or 0.0))
        net_battery = charge_value - discharge_value
        if net_battery > EPSILON:
            action, amount = "charge", net_battery
        elif net_battery < -EPSILON:
            action, amount = "discharge", -net_battery
        else:
            action, amount = "idle", 0.0
        after = previous_energy + net_battery
        plan.append(HourlyPlanEntry(
            hour=hour,
            grid_kwh=max(0.0, float(grid[hour].value() or 0.0)),
            solar_used_kwh=max(0.0, float(solar[hour].value() or 0.0)),
            battery_action=action,
            battery_kwh=amount,
            battery_energy_after_kwh=after,
        ))
        previous_energy = after
    return plan, compiled
