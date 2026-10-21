import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from electiondb.db import initialize
from electiondb.environment import (PRIOR_WEIGHT, import_polls, national_environment, parse_generic_ballot,
                                    poll_average, turnout_weight)
from electiondb.newseffect import PRIOR_SD, evidence, fit

INPUTS = {"house_two_party_d": {"2024": .48}, "mean_out_party_gain_pp": 5.0,
          "midterms": [{"year": 2018, "president_party": "R", "out_party_gain_pp": 5.0}],
          "turnout_rel": {"white|female|some_college_or_more": {"presidential": 1.2, "midterm": 1.3}}}

WIKITEXT = """==Generic congressional ballot aggregate polls==
{|class="wikitable sortable"
!Source
|-
|[[Decision Desk HQ]]<ref>{{cite web |url=https://example.org/ddhq|title=x}}</ref>
|January 9, 2025 – October 19, 2026
|October 21, 2026
|41.8%
|{{Party shading/Democratic}} |'''49.9%'''
|8.3%
|{{Party shading/Democratic}} |'''Democrats +8.1%'''
|-
|[[Nate Silver|Silver Bulletin]]<ref>{{cite web |url=https://example.org/sb|title=y}}</ref>
|January 9, 2025 – October 19, 2026
|October 20, 2026
|41.3%
|{{Party shading/Democratic}} |'''50.5%'''
|8.2%
|{{Party shading/Democratic}} |'''Democrats +9.2%'''
|-
|colspan=2 |'''Average'''
|October 21, 2026
|42.2%
|{{Party shading/Democratic}} |'''50.9%'''
|6.9%
|'''Democrats +8.7%'''
|-
|}"""


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        initialize(self.conn)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def write_polls(self, rows):
        path = Path(self.tmp.name) / "polls.csv"
        with open(path, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["aggregator", "updated", "dates_administered", "dem", "rep", "url"])
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_parser_reads_aggregators_and_skips_the_average_row(self):
        rows = parse_generic_ballot(WIKITEXT)
        self.assertEqual([r["aggregator"] for r in rows], ["Decision Desk HQ", "Silver Bulletin"])
        self.assertEqual((rows[0]["rep"], rows[0]["dem"], rows[0]["updated"]), (41.8, 49.9, "2026-10-21"))
        self.assertEqual(rows[1]["url"], "https://example.org/sb")

    def test_target_is_half_midterm_prior_half_two_party_poll_average(self):
        import_polls(self.conn, self.write_polls([
            {"aggregator": "A", "updated": "2026-10-20", "dates_administered": "", "dem": 50, "rep": 40, "url": ""},
            {"aggregator": "B", "updated": "2026-10-01", "dates_administered": "", "dem": 45, "rep": 45, "url": ""},
            {"aggregator": "B", "updated": "2026-10-25", "dates_administered": "", "dem": 60, "rep": 30, "url": ""}]))
        polls = poll_average(self.conn, "2026-10-21")
        # The post-cutoff B update is ignored; the latest earlier one is used.
        self.assertAlmostEqual(polls["two_party_d"], (50 / 90 + .5) / 2)
        environment = national_environment(self.conn, "2026-10-21", INPUTS)
        self.assertAlmostEqual(environment["prior_two_party_d"], .53)
        self.assertAlmostEqual(environment["target_two_party_d"], PRIOR_WEIGHT * .53 + (1 - PRIOR_WEIGHT) * polls["two_party_d"])
        self.assertEqual(environment["out_party"], "D")

    def test_without_polls_the_prior_alone_sets_the_target(self):
        environment = national_environment(self.conn, "2026-10-21", INPUTS)
        self.assertEqual(environment["status"], "prior_only_no_polls")
        self.assertAlmostEqual(environment["target_two_party_d"], .53)

    def test_turnout_weights_by_cell_with_neutral_fallback(self):
        self.assertEqual(turnout_weight("white_non_hispanic", "female", "some_college_or_more", "midterm", INPUTS), 1.3)
        self.assertEqual(turnout_weight("hispanic", "male", "high_school_or_less", "midterm", INPUTS), 1.0)


class NewsEffectFitTests(unittest.TestCase):
    def test_fit_recovers_slope_and_shrinks_toward_zero(self):
        points = [{"x": x, "y": 3 * x, "se_pp": .5} for x in (-.4, -.1, .2, .5)]
        result = fit(points)
        self.assertAlmostEqual(result["beta_wls"], 3)
        self.assertLess(result["beta"], 3)
        self.assertGreater(result["beta"], 2.9)
        noisy = fit([{"x": x, "y": 3 * x, "se_pp": 50} for x in (-.4, .5)])
        self.assertLess(noisy["beta"], .2)

    def test_no_usable_points_means_no_effect(self):
        result = fit([{"x": None, "y": 1, "se_pp": 1}])
        self.assertEqual(result["beta"], 0)
        self.assertEqual(result["se"], PRIOR_SD)

    def test_evidence_weight(self):
        self.assertIsNone(evidence({"signal": None, "articles": 3}))
        self.assertAlmostEqual(evidence({"signal": .4, "articles": 8}), .2)


if __name__ == "__main__":
    unittest.main()
