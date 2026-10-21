"""Actual 2024 presidential results used to anchor each race's demographic baseline.

The survey model describes how cohorts differ from each other; these official
results pin each district or state to how it actually voted in 2024.

Sources:
- The Downballot, 2024 presidential results by congressional district for the
  maps used in 2024 (119th Congress) and in 2026 (states that redistricted).
- MIT Election Data + Science Lab, U.S. President 1976-2024 (state level).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen

from .db import ROOT, utc_now
from .races import STATES

SHEET = "https://docs.google.com/spreadsheets/d/{id}/export?format=csv&gid=1491069057"
SOURCES = {
    "downballot-pres-cd-2024maps": {
        "name": "The Downballot: 2024 presidential results by congressional district (2024 maps)",
        "url": SHEET.format(id="1ng1i_Dm_RMDnEvauH44pgE6JCUsapcuu8F2pCfeLWFo"),
        "vintage": "119th_congress",
    },
    "downballot-pres-cd-2026maps": {
        "name": "The Downballot: 2024 presidential results by congressional district (2026 maps)",
        "url": SHEET.format(id="1eZfaFI-c-PFOoKx1-zZA2MP0_dxRq_LVK0re3BOQqy0"),
        "vintage": "2026_map",
    },
    "medsl-president-1976-2024": {
        "name": "MIT Election Data + Science Lab: U.S. President 1976-2024",
        "url": "https://dataverse.harvard.edu/api/access/datafile/13887042",
        "vintage": "state",
    },
}
FIPS = {postal: info["fips"] for postal, info in STATES.items()}


def _download(source_id, cache_dir):
    folder = Path(cache_dir) / "anchors"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{source_id}.csv"
    meta = path.with_suffix(".metadata.json")
    if not path.exists():
        request = Request(SOURCES[source_id]["url"], headers={"User-Agent": "ElectionResearch/0.3"})
        with urlopen(request, timeout=60) as response:
            raw = response.read(20_000_001)
        if len(raw) > 20_000_000 or raw.lstrip().startswith(b"<"):
            raise ValueError(f"Unexpected response for {source_id}")
        path.write_bytes(raw)
        meta.write_text(json.dumps({"url": SOURCES[source_id]["url"], "retrieved_at": utc_now(),
                                    "sha256": hashlib.sha256(raw).hexdigest()}, indent=2), encoding="utf-8")
    return path.read_text(encoding="utf-8-sig")


def parse_district_sheet(text):
    """Return {'cd:SSDD': (harris_votes, trump_votes)} from a Downballot vote-total sheet."""
    rows = list(csv.reader(io.StringIO(text)))
    header = next(r for r in rows if "Harris" in r)
    harris = header.index("Harris")
    result = {}
    for row in rows:
        if not row or "-" not in row[0] or row[0].split("-")[0] not in FIPS:
            continue
        state, district = row[0].split("-")
        district = "00" if district == "AL" else district.zfill(2)
        result[f"cd:{FIPS[state]}{district}"] = (int(row[harris].replace(",", "")), int(row[harris + 1].replace(",", "")))
    if len(result) != 435:
        raise ValueError(f"Expected 435 districts, parsed {len(result)}")
    return result


def parse_state_president(text, year=2024):
    totals = {}
    for row in csv.DictReader(io.StringIO(text)):
        if int(row["year"]) == year and row["state_po"] in FIPS and row["party_simplified"] in ("DEMOCRAT", "REPUBLICAN"):
            item = totals.setdefault(f"state:{FIPS[row['state_po']]}", [0, 0])
            item[row["party_simplified"] == "REPUBLICAN"] += int(row["candidatevotes"])
    if len(totals) != 50:
        raise ValueError(f"Expected 50 states, parsed {len(totals)}")
    return {geo: tuple(v) for geo, v in totals.items()}


def fetch_anchors(conn, cache_dir=ROOT / "data" / "cache"):
    counts = {}
    parsed = {
        "downballot-pres-cd-2024maps": parse_district_sheet(_download("downballot-pres-cd-2024maps", cache_dir)),
        "downballot-pres-cd-2026maps": parse_district_sheet(_download("downballot-pres-cd-2026maps", cache_dir)),
        "medsl-president-1976-2024": parse_state_president(_download("medsl-president-1976-2024", cache_dir)),
    }
    with conn:
        for source_id, rows in parsed.items():
            info = SOURCES[source_id]
            conn.execute("""INSERT INTO sources VALUES (?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                name=excluded.name,url=excluded.url,retrieved_at=excluded.retrieved_at,notes=excluded.notes""",
                (source_id, info["name"], info["url"], utc_now(), "election_results",
                 "2024 presidential two-party votes used to anchor demographic baselines to actual results."))
            conn.execute("DELETE FROM election_results WHERE source_id=?", (source_id,))
            conn.executemany("INSERT INTO election_results VALUES (?,?,?,?,?,?,?,?)",
                             [(geo, 2024, "president", d, r, 0, info["vintage"], source_id) for geo, (d, r) in rows.items()])
            counts[source_id] = len(rows)
    return {"anchors": counts}


def anchor_share(conn, race):
    """2024 presidential two-party D share for the race's 2026 electorate.

    House seats prefer the 2026-map district result (identical to the 2024 map
    where lines did not change); statewide races use the state result.
    """
    geo = race["geography_id"]
    preferred = (("downballot-pres-cd-2026maps", "2026_map"), ("downballot-pres-cd-2024maps", "119th_congress")) \
        if race["office"] == "house" else (("medsl-president-1976-2024", "state"),)
    for source_id, vintage in preferred:
        row = conn.execute("""SELECT dem_votes,rep_votes FROM election_results WHERE geography_id=?
            AND election_year=2024 AND office='president' AND source_id=?""", (geo, source_id)).fetchone()
        if row and row[0] + row[1] > 0:
            redrawn = False
            if vintage == "2026_map":
                old = conn.execute("""SELECT dem_votes,rep_votes FROM election_results WHERE geography_id=?
                    AND election_year=2024 AND office='president' AND source_id='downballot-pres-cd-2024maps'""", (geo,)).fetchone()
                redrawn = bool(old) and abs(old[0] / (old[0] + old[1]) - row[0] / (row[0] + row[1])) > 0.0005
            return row[0] / (row[0] + row[1]), {"source_id": source_id, "boundary": vintage, "office": "president",
                                                "year": 2024, "district_redrawn_for_2026": redrawn}
    return None, {"status": "missing_anchor"}
