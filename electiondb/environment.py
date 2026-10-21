"""National environment (midterm history + generic-ballot polls) and turnout weights.

The 2026 national House two-party target is

    target = PRIOR_WEIGHT x (2024 House D share + mean out-party midterm gain)
           + POLL_WEIGHT  x (generic-ballot two-party D share)

Every race is shifted by one common national swing chosen so the 435 House
districts, weighted by expected midterm voters, add up to that target. The
midterm history and turnout rates come from the CES research outputs and are
frozen into data/model/midterm_inputs.json by ``build-midterm-inputs``.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .db import ROOT, utc_now

ARTIFACT = ROOT / "data" / "model" / "midterm_inputs.json"
RESEARCH = ROOT / "data" / "cache" / "research" / "ces_elasticity" / "outputs"
PRIOR_WEIGHT = 0.5
POLL_WEIGHT = 0.5
# White House party at each midterm; the out-party is the other one.
PRESIDENT = {2006: "R", 2010: "D", 2014: "D", 2018: "R", 2022: "D", 2026: "R"}
MIDTERM_TURNOUT_YEARS = (2018, 2022)
PRESIDENTIAL_TURNOUT_YEAR = 2020  # CES v10 has no validated 2024 turnout yet
RACE5 = {"white_non_hispanic": "white", "black_non_hispanic": "black", "hispanic": "hispanic",
         "asian_non_hispanic": "asian"}
WIKI_PAGE = "2026 United States House of Representatives elections"
WIKI_API = "https://en.wikipedia.org/w/api.php"
POLL_SOURCE_ID = "wikipedia-generic-ballot-2026"


def build_midterm_inputs(research_dir=RESEARCH, artifact_path=ARTIFACT):
    """Freeze national House results and CES validated turnout by cell."""
    research_dir = Path(research_dir)
    house = {}
    with open(research_dir / "national_verification.csv", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            house[int(row["year"])] = float(row["actual_d2p"])
    gains = []
    for year, party in sorted(PRESIDENT.items()):
        if year in house and year - 2 in house:
            change = 100 * (house[year] - house[year - 2])
            # Positive = the out-party gained.
            gains.append({"year": year, "president_party": party,
                          "out_party_gain_pp": round(change if party == "R" else -change, 4)})
    turnout = {}
    with open(research_dir / "turnout.csv", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["spec"] == "turnout_citizen25" and row["level"] == "cell20":
                turnout.setdefault(row["group"], {})[int(row["year"])] = float(row["turnout_rel_national"])
    cells = {}
    for group, years in turnout.items():
        midterm = [years[y] for y in MIDTERM_TURNOUT_YEARS if y in years]
        if PRESIDENTIAL_TURNOUT_YEAR not in years or not midterm:
            raise ValueError(f"Missing validated turnout for {group}")
        cells[group] = {"presidential": years[PRESIDENTIAL_TURNOUT_YEAR], "midterm": sum(midterm) / len(midterm)}
    if len(cells) != 20:
        raise ValueError(f"Expected 20 race x sex x education turnout cells, found {len(cells)}")
    if 2024 not in house:
        raise ValueError("national_verification.csv lacks the 2024 House result")
    artifact = {"source": "CES cumulative 2006-2024 v10 validated turnout (citizens 25+); House popular vote from Wikipedia election infoboxes",
                "built_at": utc_now(), "house_two_party_d": {str(k): v for k, v in sorted(house.items())},
                "midterms": gains, "mean_out_party_gain_pp": sum(g["out_party_gain_pp"] for g in gains) / len(gains),
                "turnout_method": f"Turnout relative to the CES national rate. Presidential weights use {PRESIDENTIAL_TURNOUT_YEAR}; midterm weights average {', '.join(map(str, MIDTERM_TURNOUT_YEARS))}.",
                "turnout_rel": cells,
                "inputs_sha256": {name: hashlib.sha256((research_dir / name).read_bytes()).hexdigest()
                                  for name in ("national_verification.csv", "turnout.csv")}}
    artifact_path = Path(artifact_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    load_midterm_inputs.cache_clear()
    return {"artifact": str(artifact_path), "midterms": gains,
            "mean_out_party_gain_pp": artifact["mean_out_party_gain_pp"], "turnout_cells": len(cells)}


@lru_cache(maxsize=1)
def load_midterm_inputs(artifact_path=ARTIFACT):
    path = Path(artifact_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def turnout_weight(population_race, sex, education, kind, inputs=None):
    """Relative turnout ('presidential' or 'midterm'); 1.0 without the artifact."""
    inputs = inputs if inputs is not None else load_midterm_inputs()
    if not inputs:
        return 1.0
    item = inputs["turnout_rel"].get(f"{RACE5.get(population_race, 'other')}|{sex}|{education}")
    return item[kind] if item else 1.0


def _parse_percent(cell):
    match = re.search(r"(\d+(?:\.\d+)?)%", cell)
    return float(match.group(1)) if match else None


def _parse_date(text):
    from datetime import datetime
    return datetime.strptime(re.sub(r"\s+", " ", text.strip()), "%B %d, %Y").date().isoformat()


def parse_generic_ballot(wikitext):
    """Rows of the 'Generic congressional ballot aggregate polls' table.

    Columns: aggregator, dates administered, dates updated, Republicans,
    Democrats, other, margin. The table's own 'Average' row is skipped; the
    model averages aggregators itself.
    """
    rows = []
    for block in wikitext.split("\n|-")[1:]:
        cells = [c.strip() for c in re.split(r"\n\|", "\n" + block.strip()) if c.strip()]
        if len(cells) < 6 or cells[0].startswith(("colspan", "}")):
            continue
        name = re.search(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", cells[0])
        url = re.search(r"url=([^|}\s]+)", cells[0])
        rep, dem = _parse_percent(cells[3]), _parse_percent(cells[4])
        if not name or rep is None or dem is None:
            continue
        rows.append({"aggregator": name.group(1).strip(), "dates_administered": re.sub(r"\s+", " ", cells[1]),
                     "updated": _parse_date(cells[2]), "rep": rep, "dem": dem, "url": url.group(1) if url else ""})
    return rows


def _save_polls(conn, rows, source_id, source_name, source_url, notes):
    with conn:
        conn.execute("""INSERT INTO sources VALUES (?,?,?,?,?,?) ON CONFLICT(source_id)
            DO UPDATE SET name=excluded.name,url=excluded.url,retrieved_at=excluded.retrieved_at,notes=excluded.notes""",
            (source_id, source_name, source_url, utc_now(), "polls", notes))
        conn.executemany("""INSERT INTO poll_aggregates VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(aggregator,updated)
            DO UPDATE SET dates_administered=excluded.dates_administered,dem=excluded.dem,rep=excluded.rep,
            url=excluded.url,fetched_at=excluded.fetched_at,source_id=excluded.source_id""",
            [(r["aggregator"], r["updated"], r["dates_administered"], r["dem"], r["rep"], r["url"], utc_now(), source_id) for r in rows])


def fetch_polls(conn, cache_dir=ROOT / "data" / "cache"):
    """Generic-ballot aggregator averages listed on Wikipedia's 2026 House page."""
    headers = {"User-Agent": "ElectionResearch/0.4 (aggregate research)"}
    sections = json.load(urlopen(Request(f"{WIKI_API}?action=parse&format=json&prop=sections&page={quote(WIKI_PAGE)}",
                                         headers=headers), timeout=40))["parse"]["sections"]
    index = next((s["index"] for s in sections if "Generic" in s["line"] and "ballot" in s["line"]), None)
    if index is None:
        raise ValueError("The generic-ballot section was not found on the Wikipedia page")
    url = f"{WIKI_API}?action=parse&format=json&prop=wikitext&section={index}&page={quote(WIKI_PAGE)}"
    raw = urlopen(Request(url, headers=headers), timeout=40).read()
    folder = Path(cache_dir) / "polls"
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    (folder / f"generic_ballot_{digest[:16]}.json").write_bytes(raw)
    rows = parse_generic_ballot(json.loads(raw)["parse"]["wikitext"]["*"])
    if not rows:
        raise ValueError("No generic-ballot aggregator rows could be parsed")
    _save_polls(conn, rows, POLL_SOURCE_ID, "Wikipedia: 2026 House generic-ballot aggregates",
                f"https://en.wikipedia.org/wiki/{quote(WIKI_PAGE.replace(' ', '_'))}#Generic_congressional_ballot_aggregate_polls",
                "Aggregator averages (Decision Desk HQ, RealClearPolitics, Silver Bulletin...) as listed on Wikipedia. Raw wikitext cached with checksum.")
    return {"aggregators": len(rows), "rows": rows, "sha256": digest}


def import_polls(conn, path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream), 2):
            try:
                item = {"aggregator": row["aggregator"].strip(), "updated": date.fromisoformat(row["updated"]).isoformat(),
                        "dates_administered": row.get("dates_administered", "").strip(), "dem": float(row["dem"]),
                        "rep": float(row["rep"]), "url": row.get("url", "").strip()}
                if not item["aggregator"] or not (0 < item["dem"] < 100 and 0 < item["rep"] < 100):
                    raise ValueError("aggregator is required and dem/rep must be percentages")
                rows.append(item)
            except (ValueError, KeyError) as exc:
                raise ValueError(f"{path}, row {index}: {exc}") from exc
    uri = Path(path).resolve().as_uri()
    _save_polls(conn, rows, "polls:" + hashlib.sha256(uri.encode()).hexdigest()[:16],
                f"Imported generic-ballot file {Path(path).name}", uri, "User-imported generic-ballot averages.")
    return {"imported": len(rows)}


def poll_average(conn, as_of):
    """Latest row per aggregator updated on or before the cutoff, averaged."""
    latest = conn.execute("""SELECT p.* FROM poll_aggregates p JOIN (SELECT aggregator, MAX(updated) AS updated
        FROM poll_aggregates WHERE updated<=? GROUP BY aggregator) m USING(aggregator, updated) ORDER BY aggregator""",
        (as_of[:10],)).fetchall()
    if not latest:
        return None
    shares = [r["dem"] / (r["dem"] + r["rep"]) for r in latest]
    return {"two_party_d": sum(shares) / len(shares), "aggregators": [
        {"aggregator": r["aggregator"], "updated": r["updated"], "dem": r["dem"], "rep": r["rep"],
         "two_party_d": s} for r, s in zip(latest, shares)]}


def national_environment(conn, as_of, inputs=None):
    """Target 2026 national House two-party D share and its components."""
    inputs = inputs if inputs is not None else load_midterm_inputs()
    if not inputs:
        raise ValueError("Run python main.py build-midterm-inputs before predicting")
    year = date.fromisoformat(as_of[:10]).year
    out_party = "D" if PRESIDENT.get(year) == "R" else "R"
    house_2024 = inputs["house_two_party_d"]["2024"]
    gain = inputs["mean_out_party_gain_pp"] / 100
    prior = house_2024 + (gain if out_party == "D" else -gain)
    polls = poll_average(conn, as_of)
    if polls:
        target = PRIOR_WEIGHT * prior + POLL_WEIGHT * polls["two_party_d"]
        status = "prior_and_polls"
    else:
        target, status = prior, "prior_only_no_polls"
    return {"target_two_party_d": target, "target_margin_pp": 100 * (2 * target - 1), "status": status,
            "house_2024_two_party_d": house_2024, "out_party": out_party,
            "mean_out_party_gain_pp": inputs["mean_out_party_gain_pp"], "midterms": inputs["midterms"],
            "prior_two_party_d": prior, "poll_two_party_d": polls["two_party_d"] if polls else None,
            "polls": polls["aggregators"] if polls else [], "weights": {"prior": PRIOR_WEIGHT, "polls": POLL_WEIGHT if polls else 0.0}}
