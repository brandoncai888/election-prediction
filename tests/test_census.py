"""Regression checks for Census source formats; fixtures are synthetic."""

import unittest
from pathlib import Path
from unittest.mock import patch

from electiondb.census import (
    CensusDataError,
    EDUCATION_LABELS,
    _bulk_payloads,
    _cell_variables,
    _geography_id,
    _integer,
)


def table_metadata():
    variables = {}
    for sex, first in (("Male", 3), ("Female", 8)):
        for offset, label in enumerate(EDUCATION_LABELS):
            name = f"C15002A_{first + offset:03d}"
            variables[name + "E"] = {"label": f"Estimate!!Total:!!{sex}:!!{label}"}
            variables[name + "M"] = {"label": f"Margin of Error!!Total:!!{sex}:!!{label}"}
    return {"variables": variables}


class CensusFormatTests(unittest.TestCase):
    def test_source_labels_map_eight_joint_cells_and_require_moe(self):
        metadata = table_metadata()
        cells = _cell_variables(metadata, "C15002A")
        self.assertEqual(len(cells), 8)
        self.assertEqual(cells["male", "less_than_high_school"], "C15002A_003E")
        self.assertEqual(cells["female", "bachelors_plus"], "C15002A_011E")
        del metadata["variables"]["C15002A_011M"]
        with self.assertRaises(CensusDataError):
            _cell_variables(metadata, "C15002A")

    def test_119th_congress_and_at_large_geographies_survive_bulk_import(self):
        cells = _cell_variables(table_metadata(), "C15002A")
        header = ["GEO_ID"] + [
            f"C15002A_{suffix}{number:03d}"
            for number in range(1, 12)
            for suffix in ("E", "M")
        ]
        # Characteristic-iteration variants are different populations and must
        # never be accidentally counted a second time.
        identifiers = [
            "0100000US", "0100089US", "0400000US06", "0400089US06",
            "5001900US0601", "5001989US0601", "5001900US0200",
        ]
        raw = ("|".join(header) + "\n" + "\n".join(
            "|".join([geoid] + ["100", "12"] * 11) for geoid in identifiers
        ) + "\n").encode()
        with patch("electiondb.census._cached_bytes", return_value=(raw, {"url": "fixture"})):
            payloads, _ = _bulk_payloads("C15002A", cells, 2024, Path("unused"))
        self.assertEqual({name: len(rows) - 1 for name, rows in payloads.items()},
                         {"national": 1, "states": 1, "districts": 2})
        district_header = payloads["districts"][0]
        rows = [dict(zip(district_header, record)) for record in payloads["districts"][1:]]
        self.assertEqual([_geography_id(row, "districts") for row in rows],
                         ["cd:0601", "cd:0200"])
        self.assertEqual(rows[0]["C15002A_003E"], "100")
        self.assertEqual(rows[0]["C15002A_003M"], "12")

    def test_missing_estimates_and_out_of_scope_geographies_are_not_counts(self):
        for value in (None, "", "-666666666", "-999999999", "**"):
            self.assertIsNone(_integer(value))
        self.assertEqual(_integer("0"), 0)
        self.assertIsNone(_geography_id({"state": "72"}, "states"))
        self.assertIsNone(_geography_id({"state": "06", "congressional district": "ZZ"}, "districts"))


if __name__ == "__main__":
    unittest.main()
