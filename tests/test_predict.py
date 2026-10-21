import json
import math
import sqlite3
import unittest

import numpy as np
from scipy import sparse

from electiondb.db import SCHEMA
from electiondb.predict import (
    EDUCATION, POPULATION_RACES, RAW_RACES, finish_race, group_news_pp, population_cells,
    predict_race, race_inputs, solve_national_swing, win_probability, _fit_logistic, _matrix, _rating,
)

EFFECT = {"coefficient_pp_per_unit": 4.0, "cap_pp": 12.0}


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO sources VALUES('fixture','Fixture','https://example.org','2026-10-21','census','')")
        self.race = {"race_id": "2026-house-CA-01", "cycle": 2026, "office": "house", "state": "CA",
                     "state_fips": "06", "geography_id": "cd:0601", "election_date": "2026-11-03"}
        self.model = {"features": ["intercept", "race:black", "sex:female", "education:some_college_or_more",
                                    "state:06", "district:cd:0601", "district:cd:0602"],
                      "coefficients": [-.4, 1.7, .2, .3, .1, -.3, .5], "validation": {"scope": "fixture"}}
        self.empty_news = {"difference": None, "topics": {}, "topic_salience": {}, "article_count": 0,
                           "attributed_article_count": 0}
        self.news = {"difference": .5, "topics": {"economy": .7, "abortion": .5},
                     "topic_salience": {"economy": .5, "abortion": .5}, "article_count": 30,
                     "attributed_article_count": 30}
        self.add_population()

    def tearDown(self):
        self.conn.close()

    def add_population(self, geo="cd:0601"):
        # Each sex/education total = 600 White + 6*100 nonwhite = 1,200.
        # Of 250 Hispanics, 100 are White and 150 allocated among nonwhite groups.
        for sex in ("male", "female"):
            for education in EDUCATION:
                counts = {r: 600 if r == "white" else 100 for r in RAW_RACES}
                counts.update(white_non_hispanic=500, hispanic=250)
                for race, population in counts.items():
                    self.conn.execute("INSERT INTO demographics VALUES(?,?,?,?,?,?,?,?,?,?)",
                                      (geo, 2024, race, sex, education, population, 10, "25+", "119th", "fixture"))

    def test_population_bridge_is_disjoint_and_conserves_published_total(self):
        cells, coverage = population_cells(self.conn, "cd:0601")
        self.assertEqual(coverage["status"], "complete")
        self.assertEqual(len(cells), 32)
        self.assertEqual({r["race_group"] for r in cells}, set(POPULATION_RACES))
        self.assertAlmostEqual(sum(r["population"] for r in cells), 4800)
        first = {(r["race_group"], r["sex"], r["education"]): r for r in cells}
        self.assertEqual(first["white_non_hispanic", "male", EDUCATION[0]]["population"], 500)
        self.assertEqual(first["hispanic", "male", EDUCATION[0]]["population"], 250)
        self.assertEqual(first["black_non_hispanic", "male", EDUCATION[0]]["population"], 75)
        self.assertTrue(first["black_non_hispanic", "male", EDUCATION[0]]["population_estimated"])

    def test_missing_ethnicity_does_not_silently_match_incompatible_race_schemes(self):
        self.conn.execute("DELETE FROM demographics WHERE race_group='hispanic'")
        result = self.predict(signals=self.news)
        self.assertEqual(result["status"], "missing_population")
        self.assertIsNone(result["dem_share"])
        self.assertEqual(result["groups"], [])

    def test_coarse_education_sums_counts_instead_of_averaging_shares(self):
        self.conn.execute("DELETE FROM demographics WHERE race_group='black' AND sex='male' AND education=?", (EDUCATION[0],))
        for education, count in (("less_than_high_school", 30), ("high_school", 70)):
            self.conn.execute("INSERT INTO demographics VALUES(?,?,?,?,?,?,?,?,?,?)",
                              ("cd:0601", 2024, "black", "male", education, count, 1, "25+", "119th", "fixture"))
        cells, _ = population_cells(self.conn, "cd:0601")
        cell = next(c for c in cells if (c["race_group"], c["sex"], c["education"]) == ("black_non_hispanic", "male", EDUCATION[0]))
        self.assertEqual(cell["population"], 75)

    def predict(self, race=None, signals=None, swing=0.0, center=0.0):
        return predict_race(self.conn, race or self.race, model=self.model, signals=signals or self.empty_news,
                            national_k=1.0, swing_pp=swing, news_center_pp=center)

    def test_aggregate_exactly_equals_voter_weighted_cohort_predictions(self):
        result = self.predict(signals=self.news, swing=4.0)
        self.assertEqual(result["status"], "experimental_forecast")
        self.assertEqual(len(result["groups"]), 32)
        self.assertAlmostEqual(sum(g["weight"] for g in result["groups"]), 1)
        expected = sum(g["weight"] * g["projected_dem_share"] for g in result["groups"])
        self.assertAlmostEqual(result["dem_share"], expected)
        self.assertAlmostEqual(result["dem_share"] + result["rep_share"], 1)
        self.assertAlmostEqual(result["margin_pp"], (2 * expected - 1) * 100)
        self.assertGreater(result["swing_pp"], 0)
        json.dumps(result, allow_nan=False)

    def test_margin_change_is_turnout_plus_environment_plus_news(self):
        news = {**self.news, "groups": {"hispanic": {"signal": .8, "articles": 40}}}
        inputs = race_inputs(self.conn, self.race, model=self.model, signals=news, effect=EFFECT)
        result = finish_race(inputs, 3.0, .5)
        parts = result["turnout_shift_pp"] + result["environment_shift_pp"] + result["news_margin_shift_pp"]
        self.assertAlmostEqual(result["margin_pp"] - result["baseline_margin_pp"], parts)
        self.assertAlmostEqual(result["win_probability_d_without_news"],
                               win_probability(result["margin_pp"] - result["news_margin_shift_pp"], "house"))

    def test_zero_swing_and_no_news_keep_every_cohort_baseline(self):
        result = self.predict()
        self.assertEqual(result["news_status"], "no_group_news")
        self.assertAlmostEqual(result["environment_shift_pp"], 0)
        self.assertAlmostEqual(result["news_margin_shift_pp"], 0)
        # Only the electorate's composition changes between 2024 and the midterm.
        self.assertAlmostEqual(result["margin_pp"] - result["baseline_margin_pp"], result["turnout_shift_pp"])
        for group in result["groups"]:
            self.assertAlmostEqual(group["baseline_dem_share"], group["projected_dem_share"])

    def test_national_swing_follows_measured_group_responsiveness(self):
        groups = {(g["race_group"], g["sex"], g["education"]): g for g in self.predict(swing=5.0)["groups"]}
        college = groups["white_non_hispanic", "female", EDUCATION[1]]["swing_pp"]
        noncollege_men = groups["white_non_hispanic", "male", EDUCATION[0]]["swing_pp"]
        # CES 2016->2018: white college women moved ~1.4x the nation, non-college men ~0.1x.
        self.assertGreater(college, noncollege_men)
        self.assertGreater(noncollege_men, -1e-9)

    def test_group_news_uses_learned_coefficient_and_moves_only_that_group(self):
        news = {"groups": {"hispanic": {"signal": .5, "articles": 8}, "black": {"signal": -.25, "articles": 24}}}
        self.assertAlmostEqual(group_news_pp("hispanic", news, EFFECT)[0], 4.0 * .5 * 8 / 16)
        self.assertAlmostEqual(group_news_pp("black_non_hispanic", news, EFFECT)[0], 4.0 * -.25 * 24 / 32)
        self.assertEqual(group_news_pp("white_non_hispanic", news, EFFECT)[0], 0)
        self.assertEqual(group_news_pp("hispanic", {"groups": {}}, EFFECT)[0], 0)
        self.assertLessEqual(abs(group_news_pp("hispanic", {"groups": {"hispanic": {"signal": 1e6, "articles": 1e6}}}, EFFECT)[0]), 12)

    def test_baseline_hits_the_target_and_news_and_turnout_add_on_top(self):
        self.add_population("cd:0602")
        news = {"groups": {"hispanic": {"signal": .6, "articles": 50}}}
        inputs = [race_inputs(self.conn, {**self.race, "geography_id": geo}, model=self.model, signals=news, effect=EFFECT)
                  for geo in ("cd:0601", "cd:0602")]
        swing, center, detail = solve_national_swing(inputs, .55)
        self.assertEqual(center, 0)
        results = [finish_race(item, swing, center) for item in inputs]
        def national(field, weight):
            totals = [sum(g["population"] * g[weight] for g in r["groups"]) for r in results]
            return sum(t * r[field] for t, r in zip(totals, results)) / sum(totals)
        final = national("dem_share", "turnout")
        self.assertAlmostEqual(final, detail["final_two_party_d"], places=6)
        self.assertAlmostEqual(200 * final - 100, 100 * (2 * .55 - 1) + detail["turnout_effect_margin_pp"] + detail["news_effect_margin_pp"], places=6)
        # Favorable group coverage raises the national result above the target.
        self.assertGreater(detail["news_effect_margin_pp"], 0)

    def test_group_coverage_scoring(self):
        from electiondb.news import group_features
        self.assertGreater(group_features("Polls show Latino voters moving away from President Trump")["hispanic"]["direction"], 0)
        self.assertLess(group_features("Hispanic voters shift toward Republicans in South Texas")["hispanic"]["direction"], 0)
        self.assertLess(group_features("Black voters sour on Democrats over economy")["black"]["direction"], 0)
        self.assertGreater(group_features("Trump's gains among Latino voters slip ahead of midterms")["hispanic"]["direction"], 0)
        neutral = group_features("A barber shop event sparks civic engagement for Black voters")["black"]
        self.assertIsNone(neutral["direction"])
        self.assertNotIn("hispanic", group_features("The ice storm closed roads"))
        # The referendum rule counts negative policy coverage against the White House party.
        raids = "ICE raids spark fear and anger in immigrant neighborhoods"
        self.assertGreater(group_features(raids, "republican")["hispanic"]["direction"], 0)
        self.assertLess(group_features(raids, "democratic")["hispanic"]["direction"], 0)

    def test_local_model_changes_district_baseline(self):
        self.add_population("cd:0602")
        second_race = {**self.race, "geography_id": "cd:0602"}
        first = self.predict()
        second = self.predict(second_race)
        self.assertGreater(second["baseline_dem_share"], first["baseline_dem_share"])

    def test_logistic_fit_recovers_weighted_choices(self):
        # Both samples contain equal raw D/R; weighting moves the fitted probabilities.
        matrix = sparse.csr_matrix(np.array([[1, 0], [1, 0], [1, 1], [1, 1]], dtype=float))
        target = np.array([0, 1, 0, 1])
        weights = np.array([4, 1, 1, 4], dtype=float)
        coef, fit = _fit_logistic(matrix, target, weights, np.array([0, 0], dtype=float))
        self.assertTrue(fit["converged"])
        self.assertAlmostEqual(1 / (1 + math.exp(-coef[0])), .2, places=4)
        self.assertAlmostEqual(1 / (1 + math.exp(-sum(coef))), .8, places=4)

    def test_future_cutoff_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "precede"):
            predict_race(self.conn, self.race, as_of="2026-11-03", model=self.model, signals=self.news, swing_pp=0)

    def test_ratings_use_two_party_margin(self):
        self.assertEqual(_rating(1.9), "tossup")
        self.assertEqual(_rating(-3), "lean_r")
        self.assertEqual(_rating(7), "likely_d")
        self.assertEqual(_rating(-15), "safe_r")


if __name__ == "__main__":
    unittest.main()
