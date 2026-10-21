"""How strongly each demographic cell responds to a national swing.

Estimated from the CES cumulative file (2006-2024) by
``tools/ces_elasticity_analysis.py``. For each race x sex x education cell, the
ratio is that cell's change in Democratic House share divided by the national
change. The production value is the 2016->2018 midterm (the only midterm under
a Republican president with a CES baseline), shrunk toward the race group's
ratio and the race ratios toward 1.0. Example: white college women moved 1.36x
the nation in 2018; white non-college men about 0.09x.

Rebuild:
    python tools/download_ces_all_years.py <cache_dir>
    python -I tools/ces_elasticity_analysis.py <ces_csv> <research_dir> <out_dir>
    python main.py build-elasticity --summary <out_dir>/midterm_ratio_summary.csv
"""
from __future__ import annotations

import csv
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from .db import ROOT, utc_now

ARTIFACT = ROOT / "data" / "model" / "demographic_elasticity.json"
DEFAULT_SUMMARY = ROOT / "data" / "cache" / "research" / "ces_elasticity" / "outputs" / "midterm_ratio_summary.csv"
# The model's eight population race groups map onto five CES race groups.
RACE5 = {"white_non_hispanic": "white", "black_non_hispanic": "black", "hispanic": "hispanic",
         "asian_non_hispanic": "asian"}


def race5(population_race):
    return RACE5.get(population_race, "other")


def build_elasticity(summary_path=DEFAULT_SUMMARY, artifact_path=ARTIFACT):
    summary_path = Path(summary_path)
    rows = list(csv.DictReader(summary_path.open(encoding="utf-8")))
    cells, races = {}, {}
    for row in rows:
        value = row.get("recommended_ratio_R_midterm")
        if not value:
            continue
        item = {"ratio": float(value), "se": float(row["recommended_ratio_R_midterm_se"] or 0),
                "ratio_2018_raw": float(row["ratio_2018_R_pres"]) if row.get("ratio_2018_R_pres") else None,
                "pooled_4_midterms": float(row["pooled_ratio_eb"]) if row.get("pooled_ratio_eb") else None}
        if row["level"] == "cell20":
            cells[row["group"]] = item
        elif row["level"] == "race5":
            races[row["group"]] = item
    if len(cells) != 20:
        raise ValueError(f"Expected 20 race x sex x education cells, found {len(cells)}")
    artifact = {"source": "CES cumulative 2006-2024 v10 (Berkeley SDA), House two-party vote, age 25+",
                "method": "Cell shift / national shift, 2016->2018 (Republican president midterm), empirical-Bayes shrinkage toward race-group ratio; race ratios toward 1.0.",
                "summary_file": summary_path.name, "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
                "built_at": utc_now(), "cells": cells, "races": races}
    artifact_path = Path(artifact_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    load_elasticity.cache_clear()
    return {"artifact": str(artifact_path), "cells": len(cells)}


@lru_cache(maxsize=1)
def load_elasticity(artifact_path=ARTIFACT):
    path = Path(artifact_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cell_ratio(population_race, sex, education, artifact=None):
    """Responsiveness ratio for one cell, floored at zero (1.0 if no artifact)."""
    artifact = artifact if artifact is not None else load_elasticity()
    if not artifact:
        return 1.0
    item = artifact["cells"].get(f"{race5(population_race)}|{sex}|{education}")
    return max(0.0, item["ratio"]) if item else 1.0
