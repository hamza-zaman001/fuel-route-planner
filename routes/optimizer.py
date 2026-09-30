"""
Choose where to buy fuel along a route.

Goal: the lowest fuel bill, without silly plans like "stop to buy 1 gallon to
save 3 cents". Each stop therefore carries a small `stop_penalty` (USD, the
value of the time a stop takes); we minimize  fuel cost + penalty x stops.
The reported cost is the real fuel bill only.

Algorithm: dynamic programming over the stations along the route. It relies
on a known result for the "gas station problem" (Khuller, Malekian & Mestre,
"To fill or not to fill", 2007): some optimal plan only ever does one of two
things at a stop,
    * fill the tank completely, or
    * buy just enough fuel to reach the next stop.
So the fuel left when arriving at a station is either 0, or "a full tank minus
the distance driven since the last fill-up" (or since the start). That keeps
the number of states small (stations x stations within range) and makes the
search exact and fast: a few milliseconds for a coast-to-coast trip.

The vehicle starts with a full tank and never buys more than it needs to
reach the destination.
"""

from dataclasses import dataclass
from itertools import pairwise

import numpy as np

_EPS = 1e-9


@dataclass(frozen=True)
class Candidate:
    mile: float  # distance from the start along the route
    price: float  # USD per gallon
    ref: object = None  # opaque reference back to the station


@dataclass(frozen=True)
class FuelStop:
    candidate: Candidate
    gallons: float
    cost: float
    fuel_on_arrival_miles: float


@dataclass(frozen=True)
class FuelPlan:
    stops: list
    total_distance_miles: float
    total_gallons_used: float
    gallons_purchased: float
    total_cost: float


class InfeasibleRouteError(Exception):
    """Raised when a gap between stations is longer than the vehicle's range."""

    def __init__(self, gap_start_mile: float, gap_end_mile: float, range_miles: float):
        self.gap_start_mile = gap_start_mile
        self.gap_end_mile = gap_end_mile
        super().__init__(
            f"No fuel station reachable between mile {gap_start_mile:.0f} and "
            f"mile {gap_end_mile:.0f} (vehicle range is {range_miles:.0f} miles)."
        )


def plan_fuel_stops(
    candidates: list,
    total_distance: float,
    range_miles: float,
    mpg: float,
    initial_fuel_miles: float | None = None,
    stop_penalty: float = 0.0,
) -> FuelPlan:
    R, D = range_miles, total_distance
    start_fuel = R if initial_fuel_miles is None else initial_fuel_miles
    if D <= start_fuel + _EPS:
        return _build_plan([], D, mpg)

    stations = _cheapest_per_location(candidates, D)
    n = len(stations)
    x = np.array([c.mile for c in stations], dtype=float)
    price = np.array([c.price for c in stations], dtype=float)
    window_end = np.searchsorted(x, x + R + _EPS, side="right")  # stations reachable from i
    window_start = np.searchsorted(x, x - R - _EPS, side="left")  # stations that can reach j

    # Per station "fill up here" result: the best cost after filling, which
    # arriving state it came from, and how much was bought.
    fill_cost = np.full(n, np.inf)
    fill_from = np.zeros(n, dtype=int)
    fill_buy = np.zeros(n)
    # Best way to arrive at each station with an empty tank (bought just enough).
    empty_cost = np.full(n, np.inf)
    empty_from = np.full((n, 2), -1, dtype=int)  # (station, state index)
    empty_buy = np.zeros(n)

    arrivals = [None] * n  # per station: (cost, fuel, parent_station, parent_state, bought)
    best_final = None  # (score, station, state index, bought)

    for j in range(n):
        # --- Collect every way of arriving at station j. -----------------------
        lo = window_start[j]
        src = np.arange(lo, j)
        src = src[np.isfinite(fill_cost[lo:j])]
        cost = [fill_cost[src], [empty_cost[j]]]
        fuel = [R - (x[j] - x[src]), [0.0]]
        par_st = [src, [empty_from[j, 0]]]
        par_ix = [fill_from[src], [empty_from[j, 1]]]
        bought = [fill_buy[src], [empty_buy[j]]]
        if x[j] <= start_fuel + _EPS:  # straight from the start, no purchase yet
            cost.append([0.0])
            fuel.append([start_fuel - x[j]])
            par_st.append([-1])
            par_ix.append([-1])
            bought.append([0.0])
        cost, fuel = np.concatenate(cost), np.concatenate(fuel)
        par_st, par_ix, bought = np.concatenate(par_st), np.concatenate(par_ix), np.concatenate(bought)
        ok = np.isfinite(cost)
        cost, fuel, par_st, par_ix, bought = cost[ok], fuel[ok], par_st[ok], par_ix[ok], bought[ok]
        arrivals[j] = (cost, fuel, par_st, par_ix, bought)
        if cost.size == 0:
            continue

        p = price[j]
        base = cost - fuel * p  # state cost minus the value of fuel already in the tank

        # --- Finish from here, buying only what is missing. --------------------
        need = D - x[j]
        if need <= R + _EPS:
            buy = np.maximum(0.0, need - fuel)
            score = cost + buy * p + stop_penalty * (buy > _EPS)
            k = int(np.argmin(score))
            if best_final is None or score[k] < best_final[0]:
                best_final = (float(score[k]), j, k, float(buy[k]))

        # --- Option 1: fill the tank here. -------------------------------------
        k = int(np.argmin(base))
        if R - fuel[k] > _EPS:
            fill_cost[j] = base[k] + R * p + stop_penalty
            fill_from[j] = k
            fill_buy[j] = R - fuel[k]

        # --- Option 2: buy just enough to reach each station ahead. ------------
        # Only worth it when the tank alone wouldn't get there.
        ahead = np.arange(j + 1, window_end[j])
        if ahead.size:
            order = np.argsort(fuel, kind="stable")
            sorted_fuel, sorted_base = fuel[order], base[order]
            prefix_best = np.minimum.accumulate(sorted_base)
            is_new_min = sorted_base <= prefix_best
            prefix_arg = order[np.maximum.accumulate(np.where(is_new_min, np.arange(len(order)), 0))]

            d = x[ahead] - x[j]
            count = np.searchsorted(sorted_fuel, d - _EPS, side="left")
            valid = count > 0
            ahead, d, count = ahead[valid], d[valid], count[valid]
            if ahead.size:
                k_arr = prefix_arg[count - 1]
                c = prefix_best[count - 1] + d * p + stop_penalty
                better = c < empty_cost[ahead]
                tgt = ahead[better]
                empty_cost[tgt] = c[better]
                empty_from[tgt, 0] = j
                empty_from[tgt, 1] = k_arr[better]
                empty_buy[tgt] = d[better] - fuel[k_arr[better]]

    if best_final is None:
        raise _infeasible(list(x), D, start_fuel, R)

    # --- Walk back through the chosen states to list the purchases. -----------
    _, j, k, final_buy = best_final
    purchases = []
    if final_buy > _EPS:
        purchases.append((j, final_buy, arrivals[j][1][k]))
    while True:
        _, _, par_st, par_ix, bought = arrivals[j]
        pj, pk = int(par_st[k]), int(par_ix[k])
        if pj < 0:
            break
        purchases.append((pj, float(bought[k]), float(arrivals[pj][1][pk])))
        j, k = pj, pk
    purchases.reverse()

    stops = [
        FuelStop(
            candidate=stations[i],
            gallons=miles_bought / mpg,
            cost=miles_bought / mpg * stations[i].price,
            fuel_on_arrival_miles=arrival,
        )
        for i, miles_bought, arrival in purchases
    ]
    return _build_plan(stops, D, mpg)


def _cheapest_per_location(candidates, total_distance) -> list:
    """Stations sorted by mile marker; where several share a spot (e.g. the
    same town), only the cheapest one can ever be the best choice."""
    best = {}
    for c in candidates:
        if -_EPS <= c.mile <= total_distance + _EPS:
            key = round(c.mile, 2)
            if key not in best or c.price < best[key].price:
                best[key] = c
    return sorted(best.values(), key=lambda c: c.mile)


def _build_plan(stops, total_distance, mpg) -> FuelPlan:
    return FuelPlan(
        stops=stops,
        total_distance_miles=total_distance,
        total_gallons_used=total_distance / mpg,
        gallons_purchased=sum(s.gallons for s in stops),
        total_cost=sum(s.cost for s in stops),
    )


def _infeasible(miles, total, start_fuel, range_miles) -> InfeasibleRouteError:
    points = [0.0] + miles + [total]
    reach = start_fuel
    for a, b in pairwise(points):
        if b - a > reach + _EPS:
            return InfeasibleRouteError(a, b, range_miles)
        reach = range_miles
    return InfeasibleRouteError(0.0, total, range_miles)
