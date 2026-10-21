"""Import published ACS race x sex x education counts, never voter counts.

C15002A--G are mutually exclusive race groups, regardless of Hispanic origin.
The universe is residents age 25 and over, including noncitizens.  Sex is the
Census survey's male/female classification; it is not gender identity.  Counts
describe ACS publication geography, which may differ from 2026 boundaries.
"""

from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RACE_TABLES = {
    "white": "C15002A",
    "black": "C15002B",
    "american_indian_alaska_native": "C15002C",
    "asian": "C15002D",
    "native_hawaiian_pacific_islander": "C15002E",
    "other_race": "C15002F",
    "multiracial": "C15002G",
    "white_non_hispanic": "C15002H",
    "hispanic": "C15002I",
}
EDUCATION_LABELS = {
    "Less than high school diploma": "less_than_high_school",
    "High school graduate (includes equivalency)": "high_school",
    "Some college or associate's degree": "some_college",
    "Bachelor's degree or higher": "bachelors_plus",
}
UNIVERSE = "Resident population age 25 and over; all citizenship statuses"
SOURCE_NOTES = (
    "ACS 5-year joint race x sex x educational-attainment estimates, age 25+. "
    "Race categories include people of any Hispanic ethnicity; race is not "
    "ethnicity. Sex is the Census male/female variable, not gender identity. "
    "Published cells and 90% margins of error are imported directly, not "
    "constructed by multiplying marginal shares. These are population counts, "
    "not eligible-voter, registered-voter, turnout, or vote-choice estimates. "
    "ACS publication district boundaries may differ from the 2026 ballot."
)


class CensusDataError(RuntimeError):
    """A source response could not safely be imported."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cached_bytes(url: str, cache_path: Path, *, api_key: str = "") -> tuple[bytes, dict]:
    """Keep exact response bytes with a URL, checksum, and original fetch date.

    The optional key is never written to the cache, provenance, or an error.
    A second run reuses its cached snapshot for reproducible analysis.
    """
    meta_path = cache_path.with_suffix(".meta.json")
    if cache_path.exists() and meta_path.exists():
        try:
            raw = cache_path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["url"] == url and meta["sha256"] == hashlib.sha256(raw).hexdigest():
                return raw, meta
        except (ValueError, KeyError, OSError):
            pass
    request_url = url
    if api_key:
        request_url += ("&" if "?" in url else "?") + urlencode({"key": api_key})
    request = Request(request_url, headers={"User-Agent": "ElectionResearchDatabase/1.0"})
    try:
        with urlopen(request, timeout=45) as response:
            if "missing_key" in response.geturl() or "invalid_key" in response.geturl():
                raise CensusDataError("Census API requires a valid CENSUS_API_KEY; public summary-file import is available without a key")
            raw = response.read()
    except HTTPError as exc:
        raise CensusDataError(f"Census API returned HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError) as exc:
        raise CensusDataError(f"Census API unavailable ({type(exc).__name__})") from None
    if raw.lstrip().startswith((b"<html", b"<!DOCTYPE", b"<!doctype")):
        raise CensusDataError("Census source returned an HTML page instead of data")
    meta = {"url": url, "retrieved_at": _utc_now(), "sha256": hashlib.sha256(raw).hexdigest()}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temporary names also permit independent importer processes.
    with tempfile.NamedTemporaryFile(dir=cache_path.parent, suffix=".tmp", delete=False) as stream:
        stream.write(raw)
        tmp = Path(stream.name)
    tmp.replace(cache_path)
    with tempfile.NamedTemporaryFile(dir=cache_path.parent, suffix=".tmp", delete=False) as stream:
        stream.write((json.dumps(meta, indent=2) + "\n").encode("utf-8"))
        tmp = Path(stream.name)
    tmp.replace(meta_path)
    return raw, meta


def _cached_json(url: str, cache_path: Path, *, api_key: str = "") -> tuple[object, dict]:
    raw, meta = _cached_bytes(url, cache_path, api_key=api_key)
    try:
        return json.loads(raw), meta
    except (ValueError, UnicodeError):
        raise CensusDataError("Census API response was not JSON") from None


def _bulk_payloads(table: str, cells: dict, year: int, cache_root: Path) -> tuple[dict, dict]:
    """Read official public table files when an API key is not configured.

    Each file includes all Census geographies.  Select only the exact overall
    population geography identifiers (not race-iteration geography variants).
    Retain the unmodified source file so every imported cell is reproducible.
    """
    filename = f"acsdt5y{year}-{table.lower()}.dat"
    url = f"https://www2.census.gov/programs-surveys/acs/summary_file/{year}/table-based-SF/data/5YRData/{filename}"
    raw, provenance = _cached_bytes(url, cache_root / "summary_files" / filename)
    variables = sorted({*cells.values(), *(value[:-1] + "M" for value in cells.values())})
    header = ["NAME", *variables, "state", "congressional district"]
    payloads = {scope: [header] for scope in ("national", "states", "districts")}
    reader = csv.reader(io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig"), delimiter="|")
    try:
        source_header = next(reader)
    except StopIteration:
        raise CensusDataError("Empty Census summary file") from None
    # The file spells C15002A_E003; the API spells C15002A_003E.
    converted = [name if not name.startswith(table + "_") else table + "_" + name[-3:] + name[len(table) + 1] for name in source_header]
    required = {"GEO_ID", *variables}
    if not required.issubset(converted):
        raise CensusDataError("Summary file is missing requested estimate or MOE columns")
    geo_index = converted.index("GEO_ID")
    indexes = [converted.index(name) for name in variables]
    for record in reader:
        if len(record) != len(source_header):
            raise CensusDataError("Summary file contains an invalid row")
        geoid = record[geo_index]
        selected_scope = None
        state = district = ""
        if geoid == "0100000US":
            selected_scope = "national"
        elif geoid.startswith("0400000US") and len(geoid) == 11:
            selected_scope, state = "states", geoid[-2:]
        # The middle two digits encode Congress: 5001900 is the 119th.
        # Keep the final characteristic-iteration digits 00, but do not assume
        # the congressional-vintage digits are zero as they are for states.
        elif re.fullmatch(r"500\d{2}00US\d{4}", geoid):
            selected_scope, state, district = "districts", geoid[-4:-2], geoid[-2:]
        if selected_scope:
            payloads[selected_scope].append([geoid, *(record[index] for index in indexes), state, district])
    return payloads, provenance


def _cell_variables(metadata: object, table: str) -> dict[tuple[str, str], str]:
    """Read the source labels rather than assume table numbering is stable."""
    if not isinstance(metadata, dict) or not isinstance(metadata.get("variables"), dict):
        raise CensusDataError(f"Invalid metadata for {table}")
    cells = {}
    for name, details in metadata["variables"].items():
        if not name.startswith(table + "_") or not name.endswith("E"):
            continue
        label = details.get("label", "")
        parts = label.split("!!")
        if len(parts) != 4 or parts[0] != "Estimate":
            continue
        sex = parts[2].rstrip(":").lower()
        education = EDUCATION_LABELS.get(parts[3].rstrip(":"))
        if sex in ("male", "female") and education:
            if (sex, education) in cells:
                raise CensusDataError(f"Ambiguous demographic cell in {table}")
            cells[sex, education] = name
    if len(cells) != 8:
        raise CensusDataError(f"Expected 8 sex x education cells in {table}, found {len(cells)}")
    for estimate in cells.values():
        if estimate[:-1] + "M" not in metadata["variables"]:
            raise CensusDataError(f"Missing margin-of-error variable for {estimate}")
    return cells


def _integer(value: object) -> int | None:
    """Census negative sentinel values are missing, never negative people."""
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _geography_id(row: dict[str, object], scope: str) -> str | None:
    if scope == "national":
        return "us"
    state = str(row.get("state", "")).zfill(2)
    # The default project covers the 50 states and DC, not Puerto Rico.
    if state not in {
        "01", "02", "04", "05", "06", "08", "09", "10", "11", "12", "13",
        "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25",
        "26", "27", "28", "29", "30", "31", "32", "33", "34", "35", "36",
        "37", "38", "39", "40", "41", "42", "44", "45", "46", "47", "48",
        "49", "50", "51", "53", "54", "55", "56",
    }:
        return None
    if scope == "states":
        return "state:" + state
    district = str(row.get("congressional district", "")).zfill(2)
    if not district.isdigit() or not (0 <= int(district) <= 53 or state == "11" and district == "98"):
        return None
    return "cd:" + state + district


def _boundary_vintage(metadata: object, year: int) -> str:
    if isinstance(metadata, dict):
        for item in metadata.get("fips", []):
            if item.get("name") == "congressional district" and item.get("referenceDate"):
                return str(item["referenceDate"])
    return f"ACS {year} publication geography; verify district boundaries"


def fetch_census(
    conn: sqlite3.Connection,
    year: int = 2024,
    scope: str = "all",
    cache_dir: Path = Path("data/cache"),
) -> dict:
    """Fetch/cache Census estimates, then upsert source-linked demographic rows.

    ``scope`` is all, states, districts, or national.  Import errors are returned
    explicitly; missing estimates are omitted, and missing MOEs remain NULL.
    Each table/geography batch is atomic.  Failed batches preserve prior data.
    This function neither initializes schema nor silently changes the ACS year.
    """
    if scope not in {"all", "states", "districts", "national"}:
        raise ValueError("scope must be all, states, districts, or national")
    if not isinstance(year, int) or not 2009 <= year <= 2100:
        raise ValueError("year must be an ACS 5-year publication year")
    scopes = ["national", "states", "districts"] if scope == "all" else [scope]
    base = f"https://api.census.gov/data/{year}/acs/acs5"
    cache_root = Path(cache_dir) / "census" / str(year)
    api_key = os.environ.get("CENSUS_API_KEY", "").strip()
    result = {
        "year": year, "scope": scope, "rows": 0, "geographies": 0,
        "sources": 0, "missing_cells": 0, "errors": [],
        "boundary_vintage": f"ACS {year} publication geography; verify district boundaries",
        "transport": "api" if api_key else "public_summary_file",
    }
    geographies = set()
    try:
        geography_metadata, _ = _cached_json(base + "/geography.json", cache_root / "geography.json", api_key=api_key)
        result["boundary_vintage"] = _boundary_vintage(geography_metadata, year)
    except (CensusDataError, OSError, UnicodeError) as exc:
        result["errors"].append({"stage": "geography_metadata", "error": str(exc)})
    queries = {
        "national": {"for": "us:*"},
        "states": {"for": "state:*"},
        "districts": {"for": "congressional district:*", "in": "state:*"},
    }
    for race, table in RACE_TABLES.items():
        try:
            metadata, _ = _cached_json(base + f"/groups/{table}.json", cache_root / "groups" / f"{table}.json", api_key=api_key)
            cells = _cell_variables(metadata, table)
        except (CensusDataError, OSError, UnicodeError) as exc:
            result["errors"].append({"stage": "table_metadata", "table": table, "error": str(exc)})
            continue
        bulk_payloads = None
        bulk_provenance = None
        if not api_key:
            try:
                bulk_payloads, bulk_provenance = _bulk_payloads(table, cells, year, cache_root)
            except (CensusDataError, OSError, UnicodeError, csv.Error) as exc:
                result["errors"].append({"stage": "summary_file", "table": table, "error": str(exc)})
                continue
        for selected_scope in scopes:
            try:
                if bulk_payloads is not None:
                    payload = bulk_payloads[selected_scope]
                    provenance = bulk_provenance
                    url = provenance["url"]
                else:
                    url = base + "?" + urlencode({"get": f"NAME,group({table})", **queries[selected_scope]})
                    payload, provenance = _cached_json(url, cache_root / selected_scope / f"{table}.json", api_key=api_key)
                if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[0], list):
                    raise CensusDataError("Census API returned no tabular data")
                header = payload[0]
                required = {"NAME", *cells.values(), *(value[:-1] + "M" for value in cells.values())}
                if not required.issubset(header):
                    raise CensusDataError("Source response is missing requested estimate or MOE columns")
                source_id = f"acs5:{year}:{table}:{selected_scope}"
                batch = []
                batch_geographies = set()
                missing = 0
                for record in payload[1:]:
                    if not isinstance(record, list) or len(record) != len(header):
                        raise CensusDataError("Source response contains an invalid row")
                    row = dict(zip(header, record))
                    geography_id = _geography_id(row, selected_scope)
                    if not geography_id:
                        continue
                    for (sex, education), estimate_name in cells.items():
                        population = _integer(row[estimate_name])
                        if population is None:
                            missing += 1
                            continue
                        moe = _integer(row[estimate_name[:-1] + "M"])
                        batch.append((
                            geography_id, year, race, sex, education, population,
                            moe, UNIVERSE, result["boundary_vintage"], source_id,
                        ))
                        batch_geographies.add(geography_id)
                if not batch:
                    raise CensusDataError("No valid population estimates in source batch")
                with conn:
                    notes = SOURCE_NOTES
                    if table in {'C15002H','C15002I'}:
                        cohort = 'White alone, NOT Hispanic or Latino' if table=='C15002H' else 'Hispanic or Latino, any race'
                        notes = (f'ACS 5-year sex x educational-attainment counts for {cohort}, residents age 25+. '
                                 'Published estimates and 90% margins of error. This overlaps the A–G race tables and must not be summed with them. '
                                 'Used to build mutually exclusive ethnicity groups for model weighting. Includes noncitizens; not eligible-voter counts.')
                    conn.execute(
                        """INSERT INTO sources(source_id,name,url,retrieved_at,kind,notes)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                        name=excluded.name,url=excluded.url,retrieved_at=excluded.retrieved_at,
                        kind=excluded.kind,notes=excluded.notes""",
                        (source_id, f"{year} ACS 5-year {table} ({selected_scope})", url,
                         provenance["retrieved_at"], "census", notes),
                    )
                    conn.executemany(
                        """INSERT INTO demographics(geography_id,acs_year,race_group,sex,
                        education,population,moe,universe,boundary_vintage,source_id)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(geography_id,acs_year,race_group,sex,education) DO UPDATE SET
                        population=excluded.population,moe=excluded.moe,universe=excluded.universe,
                        boundary_vintage=excluded.boundary_vintage,source_id=excluded.source_id""",
                        batch,
                    )
                result["rows"] += len(batch)
                result["sources"] += 1
                result["missing_cells"] += missing
                geographies.update(batch_geographies)
            except (CensusDataError, sqlite3.Error, OSError) as exc:
                result["errors"].append({"stage": "data", "table": table, "scope": selected_scope, "error": str(exc)})
    result["geographies"] = len(geographies)
    return result
