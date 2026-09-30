import random

from django.test import SimpleTestCase

from routes.optimizer import Candidate, InfeasibleRouteError, plan_fuel_stops


def brute_force_cost(stations, total, range_miles, stop_penalty=0.0):
    """Exhaustive search over integer fuel purchases (mpg = 1) used to verify the optimizer."""
    miles = [0] + [s[0] for s in stations] + [total]
    prices = [None] + [s[1] for s in stations] + [None]
    inf = float("inf")
    best = {range_miles: 0.0}  # fuel on arrival at node -> min cost so far
    for k in range(len(miles) - 1):
        options = {}
        for fuel, cost in best.items():
            max_buy = 0 if prices[k] is None else range_miles - fuel
            for buy in range(max_buy + 1):
                tank = fuel + buy
                left = tank - (miles[k + 1] - miles[k])
                if left < 0:
                    continue
                c = cost + buy * (prices[k] or 0) + (stop_penalty if buy else 0)
                if c < options.get(left, inf):
                    options[left] = c
        best = options
        if not best:
            return None
    return min(best.values())


class OptimizerTests(SimpleTestCase):
    def test_short_trip_needs_no_stops(self):
        plan = plan_fuel_stops([Candidate(100, 3.0)], total_distance=400, range_miles=500, mpg=10)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)
        self.assertAlmostEqual(plan.total_gallons_used, 40)

    def test_buys_only_what_is_needed_to_finish(self):
        plan = plan_fuel_stops([Candidate(400, 3.0)], total_distance=700, range_miles=500, mpg=10)
        self.assertEqual(len(plan.stops), 1)
        # Arrives with 100 miles of fuel, needs 300 to finish -> buys 200 miles = 20 gal.
        self.assertAlmostEqual(plan.stops[0].gallons, 20)
        self.assertAlmostEqual(plan.total_cost, 60)

    def test_prefers_cheaper_station_further_along(self):
        stations = [Candidate(300, 4.0, "pricey"), Candidate(450, 3.0, "cheap")]
        plan = plan_fuel_stops(stations, total_distance=900, range_miles=500, mpg=10)
        self.assertEqual([s.candidate.ref for s in plan.stops], ["cheap"])
        self.assertAlmostEqual(plan.total_cost, 40 * 3.0)

    def test_fills_up_at_cheap_station_before_expensive_stretch(self):
        stations = [Candidate(400, 2.5, "cheap"), Candidate(800, 5.0, "pricey")]
        plan = plan_fuel_stops(stations, total_distance=1100, range_miles=500, mpg=10)
        self.assertEqual([s.candidate.ref for s in plan.stops], ["cheap", "pricey"])
        self.assertAlmostEqual(plan.stops[0].gallons, 40)  # full tank at the cheap one
        self.assertAlmostEqual(plan.stops[1].gallons, 20)  # just enough to finish

    def test_gap_longer_than_range_is_infeasible(self):
        with self.assertRaises(InfeasibleRouteError):
            plan_fuel_stops([Candidate(100, 3.0)], total_distance=1000, range_miles=500, mpg=10)

    def test_matches_brute_force_on_random_routes(self):
        rng = random.Random(42)
        checked = 0
        for _ in range(600):
            range_miles = rng.randint(5, 12)
            total = rng.randint(1, 40)
            penalty = rng.choice([0.0, 0.0, 0.7, 3.0])
            stations = sorted(
                (rng.randint(1, max(1, total - 1)), rng.choice([2.0, 2.5, 3.0, 3.5, 4.0, 5.0]))
                for _ in range(rng.randint(0, 10))
            )
            expected = brute_force_cost(stations, total, range_miles, penalty)
            candidates = [Candidate(m, p) for m, p in stations]
            if expected is None:
                with self.assertRaises(InfeasibleRouteError):
                    plan_fuel_stops(candidates, total, range_miles, mpg=1, stop_penalty=penalty)
                continue
            plan = plan_fuel_stops(candidates, total, range_miles, mpg=1, stop_penalty=penalty)
            self.assertAlmostEqual(plan.total_cost + penalty * len(plan.stops), expected, places=6)
            # The plan is actually drivable: never run dry, never overfill.
            fuel, pos = range_miles, 0
            for stop in plan.stops:
                fuel -= stop.candidate.mile - pos
                self.assertGreaterEqual(fuel, -1e-6)
                self.assertAlmostEqual(fuel, stop.fuel_on_arrival_miles, places=6)
                fuel += stop.gallons
                self.assertLessEqual(fuel, range_miles + 1e-6)
                pos = stop.candidate.mile
            self.assertGreaterEqual(fuel - (total - pos), -1e-6)
            checked += 1
        self.assertGreater(checked, 150)

    def test_stop_penalty_removes_pointless_top_ups(self):
        # Five stations at the same price: the cheapest plan is ambiguous, but a
        # sensible one makes the minimum number of stops.
        stations = [Candidate(m, 3.0) for m in (10, 20, 30, 480, 490)]
        plan = plan_fuel_stops(stations, total_distance=900, range_miles=500, mpg=10, stop_penalty=5)
        self.assertEqual(len(plan.stops), 1)
        self.assertAlmostEqual(plan.total_cost, 40 * 3.0)
