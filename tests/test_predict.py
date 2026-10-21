import json
import math
import sqlite3
import unittest

import numpy as np
from scipy import sparse

from electiondb.db import SCHEMA
from electiondb.predict import (
    EDUCATION, POPULATION_RACES, RAW_RACES, demographic_swing, population_cells,
    predict_race, _fit_logistic, _matrix, _rating,
)


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
        result = predict_race(self.conn, self.race, model=self.model, signals=self.news)
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

    def test_aggregate_exactly_equals_population_weighted_cohort_predictions(self):
        result = predict_race(self.conn, self.race, model=self.model, signals=self.news)
        self.assertEqual(result["status"], "experimental_forecast")
        self.assertEqual(len(result["groups"]), 32)
        self.assertAlmostEqual(sum(g["weight"] for g in result["groups"]), 1)
        expected = sum(g["population"] * g["projected_dem_share"] for g in result["groups"]) / 4800
        self.assertAlmostEqual(result["dem_share"], expected)
        self.assertAlmostEqual(result["dem_share"] + result["rep_share"], 1)
        self.assertAlmostEqual(result["margin_pp"], (2 * expected - 1) * 100)
        self.assertGreater(result["swing_pp"], 0)
        self.assertEqual(result["news_coefficient_status"], "assumed_prior_not_historically_fitted")
        json.dumps(result, allow_nan=False)

    def test_no_news_preserves_fitted_baseline_for_every_demographic(self):
        result = predict_race(self.conn, self.race, model=self.model, signals=self.empty_news)
        self.assertEqual(result["news_status"], "no_attributed_news_baseline_only")
        self.assertAlmostEqual(result["dem_share"], result["baseline_dem_share"])
        for group in result["groups"]:
            self.assertEqual(group["baseline_dem_share"], group["projected_dem_share"])
            self.assertEqual(group["swing_pp"], 0)

    def test_news_direction_content_and_demographic_sensitivities_change_swing(self):
        positive, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[0], self.news)
        male, _, _ = demographic_swing("white_non_hispanic", "male", EDUCATION[0], self.news)
        college, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[1], self.news)
        negative_news = {**self.news, "difference": -.5, "topics": {"economy": -.7, "abortion": -.5}}
        negative, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[0], negative_news)
        # CES 2016->2018: white college women moved ~1.4x the nation, white
        # non-college women ~0.2x and non-college men ~0.1x.
        self.assertGreater(college, positive)
        self.assertGreater(positive, male)
        self.assertAlmostEqual(positive, -negative)
        self.assertGreater(positive, 0)
        immigration = {**self.news, "difference": 0, "topics": {"immigration": .8},
                       "topic_salience": {"immigration": 1}}
        hispanic, _, _ = demographic_swing("hispanic", "female", EDUCATION[0], immigration)
        white, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[0], immigration)
        self.assertGreater(hispanic, white)

    def test_racial_group_news_moves_only_that_group(self):
        groups = {"hispanic": {"signal": .9, "articles": 100}, "black": {"signal": -.4, "articles": 100}}
        news = {**self.news, "difference": .2, "topics": {}, "topic_salience": {}, "groups": groups}
        base = {**news, "groups": {}}
        hispanic = demographic_swing("hispanic", "male", EDUCATION[0], news)[0] - demographic_swing("hispanic", "male", EDUCATION[0], base)[0]
        black = demographic_swing("black_non_hispanic", "male", EDUCATION[0], news)[0] - demographic_swing("black_non_hispanic", "male", EDUCATION[0], base)[0]
        white = demographic_swing("white_non_hispanic", "male", EDUCATION[0], news)[0] - demographic_swing("white_non_hispanic", "male", EDUCATION[0], base)[0]
        # Deviation from the national signal x 6 pts x evidence strength 100/108.
        self.assertAlmostEqual(hispanic, 6 * .7 * 100 / 108)
        self.assertAlmostEqual(black, 6 * -.6 * 100 / 108)
        self.assertEqual(white, 0)

    def test_group_coverage_scoring(self):
        from electiondb.news import group_features
        self.assertGreater(group_features("Polls show Latino voters moving away from President Trump")["hispanic"]["direction"], 0)
        self.assertLess(group_features("Hispanic voters shift toward Republicans in South Texas")["hispanic"]["direction"], 0)
        self.assertLess(group_features("Black voters sour on Democrats over economy")["black"]["direction"], 0)
        self.assertGreater(group_features("Trump's gains among Latino voters slip ahead of midterms")["hispanic"]["direction"], 0)
        neutral = group_features("A barber shop event sparks civic engagement for Black voters")["black"]
        self.assertIsNone(neutral["direction"])
        self.assertNotIn("hispanic", group_features("The ice storm closed roads"))

    def test_swing_is_bounded_and_sparse_news_has_less_influence(self):
        extreme = {**self.news, "difference": 1000, "topics": {"economy": 1000},
                   "topic_salience": {"economy": 1}, "attributed_article_count": 1_000_000}
        swing, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[0], extreme)
        self.assertLessEqual(abs(swing), 12)
        sparse_news = {**self.news, "attributed_article_count": 1}
        small, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[0], sparse_news)
        strong, _, _ = demographic_swing("white_non_hispanic", "female", EDUCATION[0], self.news)
        self.assertLess(small, strong)

    def test_local_model_changes_district_baseline(self):
        self.add_population("cd:0602")
        second_race = {**self.race, "geography_id": "cd:0602"}
        first = predict_race(self.conn, self.race, model=self.model, signals=self.empty_news)
        second = predict_race(self.conn, second_race, model=self.model, signals=self.empty_news)
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
            predict_race(self.conn, self.race, as_of="2026-11-03", model=self.model, signals=self.news)

    def test_ratings_use_two_party_margin(self):
        self.assertEqual(_rating(1.9), "tossup")
        self.assertEqual(_rating(-3), "lean_r")
        self.assertEqual(_rating(7), "likely_d")
        self.assertEqual(_rating(-15), "safe_r")


if __name__ == "__main__":
    unittest.main()
