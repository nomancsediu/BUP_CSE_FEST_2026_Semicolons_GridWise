from pulp import (
    LpProblem, LpMinimize, LpVariable, lpSum,
    LpStatus, value, PULP_CBC_CMD
)
from models import HourEntry, BatterySpec, DirectiveEntry, DirectiveType, HourlyPlanEntry

TOLERANCE = 1e-6


def optimize(
    hours: list[HourEntry],
    battery: BatterySpec,
    directives: list[DirectiveEntry],
) -> list[HourlyPlanEntry]:

    # ── pre-process directives ──────────────────────────────────────────────
    solar_factors = {}          # hour → factor
    min_reserve = {}            # hour → minimum_energy_kwh (directive override)
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid_hours = {}         # hour → max_grid_kwh

    for d in directives:
        if not d.applies or d.directive_type == DirectiveType.no_op:
            continue
        adj = d.structured_adjustment
        if d.directive_type == DirectiveType.solar_reduction:
            for h in adj.hours:
                solar_factors[h] = adj.factor
        elif d.directive_type == DirectiveType.minimum_battery_reserve:
            for h in adj.hours:
                min_reserve[h] = max(
                    min_reserve.get(h, 0.0),
                    adj.minimum_energy_kwh
                )
        elif d.directive_type == DirectiveType.no_charge_window:
            no_charge_hours.update(adj.hours)
        elif d.directive_type == DirectiveType.no_discharge_window:
            no_discharge_hours.update(adj.hours)
        elif d.directive_type == DirectiveType.max_grid_window:
            for h in adj.hours:
                max_grid_hours[h] = adj.max_grid_kwh

    # ── effective solar after reduction ────────────────────────────────────
    hours_sorted = sorted(hours, key=lambda x: x.hour)
    effective_solar = {
        h.hour: h.solar_kwh * solar_factors.get(h.hour, 1.0)
        for h in hours_sorted
    }

    # ── LP problem ──────────────────────────────────────────────────────────
    prob = LpProblem("GridWise", LpMinimize)

    cap = battery.capacity_kwh
    init = battery.initial_energy_kwh
    base_min = battery.minimum_energy_kwh
    max_c = battery.max_charge_kwh_per_hour
    max_d = battery.max_discharge_kwh_per_hour

    # decision variables
    grid = {h: LpVariable(f"grid_{h}", lowBound=0) for h in range(24)}
    solar_used = {h: LpVariable(f"solar_{h}", lowBound=0) for h in range(24)}
    charge = {h: LpVariable(f"charge_{h}", lowBound=0) for h in range(24)}
    discharge = {h: LpVariable(f"discharge_{h}", lowBound=0) for h in range(24)}
    bat = {h: LpVariable(f"bat_{h}", lowBound=0, upBound=cap) for h in range(24)}

    # objective: minimize total grid cost
    tariff = {h.hour: h.tariff_bdt_per_kwh for h in hours_sorted}
    prob += lpSum(grid[h] * tariff[h] for h in range(24))

    demand = {h.hour: h.demand_kwh for h in hours_sorted}

    for h in range(24):
        eff_solar = effective_solar[h]
        dem = demand[h]

        # energy balance
        prob += grid[h] + solar_used[h] + discharge[h] == dem + charge[h]

        # solar limit
        prob += solar_used[h] <= eff_solar

        # battery state
        prev = init if h == 0 else bat[h - 1]
        prob += bat[h] == prev + charge[h] - discharge[h]

        # battery bounds
        active_min = max(base_min, min_reserve.get(h, 0.0))
        prob += bat[h] >= active_min
        prob += bat[h] <= cap

        # charge/discharge rate limits
        prob += charge[h] <= max_c
        prob += discharge[h] <= max_d

        # directive hard constraints
        if h in no_charge_hours:
            prob += charge[h] == 0
        if h in no_discharge_hours:
            prob += discharge[h] == 0
        if h in max_grid_hours:
            prob += grid[h] <= max_grid_hours[h]

    # end-of-day battery neutrality
    prob += bat[23] == init

    # ── solve ───────────────────────────────────────────────────────────────
    prob.solve(PULP_CBC_CMD(msg=0))

    if LpStatus[prob.status] != "Optimal":
        raise ValueError(f"Optimizer did not find an optimal solution: {LpStatus[prob.status]}")

    # ── build hourly plan ───────────────────────────────────────────────────
    plan = []
    for h in range(24):
        g = max(0.0, round(value(grid[h]), 6))
        s = max(0.0, round(value(solar_used[h]), 6))
        c = max(0.0, round(value(charge[h]), 6))
        d = max(0.0, round(value(discharge[h]), 6))
        b = max(0.0, round(value(bat[h]), 6))

        if c > TOLERANCE:
            action = "charge"
            bkwh = round(c, 6)
        elif d > TOLERANCE:
            action = "discharge"
            bkwh = round(d, 6)
        else:
            action = "idle"
            bkwh = 0.0

        plan.append(HourlyPlanEntry(
            hour=h,
            grid_kwh=round(g, 4),
            solar_used_kwh=round(s, 4),
            battery_action=action,
            battery_kwh=round(bkwh, 4),
            battery_energy_after_kwh=round(b, 4),
        ))

    return plan
