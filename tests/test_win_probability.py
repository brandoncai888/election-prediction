import sqlite3
import unittest

import numpy as np
from scipy.special import expit

from electiondb.anchors import anchor_share, parse_district_sheet
from electiondb.db import SCHEMA
from electiondb.predict import _anchor_offset, _rating, simulate_chambers, win_probability, UNCERTAINTY
from electiondb.senate import HOLDOVERS, control, holdover_counts


class WinProbabilityTests(unittest.TestCase):
    def test_anchor_offset_reproduces_actual_result_and_keeps_cohort_order(self):
        eta = np.array([-1.5, 0.2, 1.8])
        weights = np.array([.5, .3, .2])
        delta = _anchor_offset(eta, weights, 0.42)
        anchored = expit(eta + delta)
        self.assertAlmostEqual(float(np.dot(weights, anchored)), 0.42, places=10)
        self.assertTrue(np.all(np.diff(anchored) > 0))

    def test_win_probability_is_symmetric_and_monotone(self):
        self.assertAlmostEqual(win_probability(0, "senate"), .5)
        self.assertAlmostEqual(win_probability(4, "house") + win_probability(-4, "house"), 1)
        self.assertGreater(win_probability(6, "senate"), win_probability(3, "senate"))
        sigma = np.hypot(UNCERTAINTY["national_sd_margin_pp"], UNCERTAINTY["race_sd_margin_pp"]["senate"])
        self.assertAlmostEqual(win_probability(sigma, "senate"), 0.8413, places=3)

    def test_rating_tiers_follow_win_probability(self):
        self.assertEqual(_rating(1, .52), "tossup")
        self.assertEqual(_rating(3, .60), "tilt_d")
        self.assertEqual(_rating(-5, .25), "lean_r")
        self.assertEqual(_rating(-9, .10), "likely_r")
        self.assertEqual(_rating(20, .99), "safe_d")

    def test_senate_holdovers_complete_the_chamber(self):
        counts = holdover_counts()
        self.assertEqual(len(HOLDOVERS), 65)
        self.assertEqual(counts["D"] + counts["R"], 65)
        self.assertEqual((counts["D"], counts["R"]), (34, 31))
        self.assertFalse([h for h in HOLDOVERS if h[4] == 2026])
        self.assertNotIn(("Florida", 3), {(h[0], h[3]) for h in HOLDOVERS})
        self.assertEqual(control(51), "D")
        self.assertEqual(control(50), "R")  # Vice President breaks ties

    def test_simulation_adds_holdovers_to_senate_totals(self):
        races = [{"office": "senate", "margin_pp": 30.0}] * 20 + [{"office": "senate", "margin_pp": -30.0}] * 15
        result = simulate_chambers(races, simulations=2000, seed=1)["senate"]
        self.assertAlmostEqual(result["expected_dem_seats"], 54, delta=0.5)
        self.assertGreater(result["p_dem_control"], .95)
        self.assertAlmostEqual(sum(result["distribution"].values()), 1)

    def test_district_sheet_parser_and_anchor_lookup(self):
        lines = ["Calculated by The Downballot,,,", "District,Incumbent,Party,2024", ",,,Harris,Trump,Total"]
        from electiondb.races import STATES
        for state, info in STATES.items():
            for n in range(1, info["house_seats"] + 1):
                label = "AL" if info["house_seats"] == 1 else f"{n:02d}"
                lines.append(f'{state}-{label},X,(R),"1,000","3,000","4,100"')
        parsed = parse_district_sheet("\n".join(lines))
        self.assertEqual(len(parsed), 435)
        self.assertEqual(parsed["cd:0200"], (1000, 3000))
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO sources VALUES('downballot-pres-cd-2026maps','x','https://x','t','k','')")
        conn.execute("INSERT INTO election_results VALUES('cd:0601',2024,'president',40,60,0,'2026_map','downballot-pres-cd-2026maps')")
        share, meta = anchor_share(conn, {"geography_id": "cd:0601", "office": "house"})
        self.assertAlmostEqual(share, .4)
        self.assertEqual(meta["boundary"], "2026_map")
        self.assertIsNone(anchor_share(conn, {"geography_id": "state:06", "office": "senate"})[0])


if __name__ == "__main__":
    unittest.main()
