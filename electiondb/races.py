"""Source-verified, seat-level roster for the 2026 midterm election cycle.

This roster covers voting US House seats, US Senate contests, and state governors.
It deliberately does not attempt to infer a candidate roster from current officeholders.
See ``data/races_sources.md`` for coverage, sources, and geography limitations.
"""

from __future__ import annotations

import sqlite3

CYCLE = 2026
ELECTION_DATE = "2026-11-03"
VERIFIED_ON = "2026-10-21"

APPORTIONMENT_SOURCE = (
    "https://www.census.gov/data/tables/2020/dec/2020-apportionment-data.html"
)
SENATE_SOURCE = "https://www.senate.gov/senators/Class_II.htm"
GOVERNOR_SOURCE = "https://www.nga.org/governors/elections/"
FL_SPECIAL_SOURCE = (
    "https://dos.fl.gov/elections/candidates-committees/offices-up-for-election"
)
OH_SPECIAL_SOURCE = (
    "https://www.ohiosos.gov/globalassets/elections/directives/2025/"
    "directive-2025-54---writ-of-special-election-for-united-states-senate-election.pdf"
)

# Name, postal abbreviation, two-character Census state FIPS, 2020-apportioned seats.
_STATE_DATA = (
    ("Alabama", "AL", "01", 7),
    ("Alaska", "AK", "02", 1),
    ("Arizona", "AZ", "04", 9),
    ("Arkansas", "AR", "05", 4),
    ("California", "CA", "06", 52),
    ("Colorado", "CO", "08", 8),
    ("Connecticut", "CT", "09", 5),
    ("Delaware", "DE", "10", 1),
    ("Florida", "FL", "12", 28),
    ("Georgia", "GA", "13", 14),
    ("Hawaii", "HI", "15", 2),
    ("Idaho", "ID", "16", 2),
    ("Illinois", "IL", "17", 17),
    ("Indiana", "IN", "18", 9),
    ("Iowa", "IA", "19", 4),
    ("Kansas", "KS", "20", 4),
    ("Kentucky", "KY", "21", 6),
    ("Louisiana", "LA", "22", 6),
    ("Maine", "ME", "23", 2),
    ("Maryland", "MD", "24", 8),
    ("Massachusetts", "MA", "25", 9),
    ("Michigan", "MI", "26", 13),
    ("Minnesota", "MN", "27", 8),
    ("Mississippi", "MS", "28", 4),
    ("Missouri", "MO", "29", 8),
    ("Montana", "MT", "30", 2),
    ("Nebraska", "NE", "31", 3),
    ("Nevada", "NV", "32", 4),
    ("New Hampshire", "NH", "33", 2),
    ("New Jersey", "NJ", "34", 12),
    ("New Mexico", "NM", "35", 3),
    ("New York", "NY", "36", 26),
    ("North Carolina", "NC", "37", 14),
    ("North Dakota", "ND", "38", 1),
    ("Ohio", "OH", "39", 15),
    ("Oklahoma", "OK", "40", 5),
    ("Oregon", "OR", "41", 6),
    ("Pennsylvania", "PA", "42", 17),
    ("Rhode Island", "RI", "44", 2),
    ("South Carolina", "SC", "45", 7),
    ("South Dakota", "SD", "46", 1),
    ("Tennessee", "TN", "47", 9),
    ("Texas", "TX", "48", 38),
    ("Utah", "UT", "49", 4),
    ("Vermont", "VT", "50", 1),
    ("Virginia", "VA", "51", 11),
    ("Washington", "WA", "53", 10),
    ("West Virginia", "WV", "54", 2),
    ("Wisconsin", "WI", "55", 8),
    ("Wyoming", "WY", "56", 1),
)

STATES = {
    postal: {"name": name, "fips": fips, "house_seats": seats}
    for name, postal, fips, seats in _STATE_DATA
}

SENATE_CLASS_II = (
    "AL", "AK", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS", "KY",
    "LA", "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH", "NJ", "NM",
    "NC", "OK", "OR", "RI", "SC", "SD", "TN", "TX", "VA", "WV", "WY",
)

GOVERNOR_STATES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "FL", "GA", "HI", "ID",
    "IL", "IA", "KS", "ME", "MD", "MA", "MI", "MN", "NE", "NV", "NH",
    "NM", "NY", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
    "VT", "WI", "WY",
)

HOUSE_BOUNDARY_NOTE = (
    "Seat identifier uses 2020 apportionment. ACS congressional-district data may "
    "describe 119th-Congress boundaries rather than the map used in 2026. Verify "
    "the election map and use a geographic crosswalk before forecasting."
)

RACE_COLUMNS = (
    "race_id", "cycle", "office", "state", "state_fips", "district", "seat_class",
    "election_type", "election_date", "label", "geography_id", "boundary_note",
    "source_url",
)


def race_rows() -> list[dict]:
    """Return 506 independently addressable 2026 contests.

    ``district`` is a two-character string; Census uses ``00`` for at-large seats.
    ``geography_id`` describes the required ACS lookup identifier, not proof of a
    match between historical ACS boundaries and 2026 election boundaries.
    """
    rows = []
    for state, info in STATES.items():
        for number in range(1, info["house_seats"] + 1):
            district = "00" if info["house_seats"] == 1 else f"{number:02d}"
            district_label = "at-large" if district == "00" else f"District {number}"
            note = HOUSE_BOUNDARY_NOTE
            if state == "LA":
                note += (
                    " Louisiana's November 3 open primary can decide the seat; "
                    "a December 12, 2026 runoff is required if no candidate wins "
                    "more than 50 percent."
                )
            rows.append({
                "race_id": f"2026-house-{state}-{district}",
                "cycle": CYCLE,
                "office": "house",
                "state": state,
                "state_fips": info["fips"],
                "district": district,
                "seat_class": None,
                "election_type": "regular",
                "election_date": ELECTION_DATE,
                "label": f"{info['name']} {district_label} - US House",
                "geography_id": f"cd:{info['fips']}{district}",
                "boundary_note": note,
                "source_url": APPORTIONMENT_SOURCE,
            })

    for state in SENATE_CLASS_II:
        rows.append(_statewide_race(state, "senate", "regular", 2, SENATE_SOURCE))
    for state, source in (("FL", FL_SPECIAL_SOURCE), ("OH", OH_SPECIAL_SOURCE)):
        rows.append(_statewide_race(state, "senate", "special", 3, source))
    for state in GOVERNOR_STATES:
        rows.append(_statewide_race(state, "governor", "regular", None, GOVERNOR_SOURCE))
    return rows


def _statewide_race(state, office, election_type, seat_class, source_url):
    info = STATES[state]
    office_label = "US Senate" if office == "senate" else "Governor"
    suffix = " (special, unexpired term through January 3, 2029)" if election_type == "special" else ""
    return {
        "race_id": f"2026-{office}-{state}-{election_type}",
        "cycle": CYCLE,
        "office": office,
        "state": state,
        "state_fips": info["fips"],
        "district": None,
        "seat_class": seat_class,
        "election_type": election_type,
        "election_date": ELECTION_DATE,
        "label": f"{info['name']} - {office_label}{suffix}",
        "geography_id": f"state:{info['fips']}",
        "boundary_note": "Statewide geography; congressional redistricting does not change this electorate.",
        "source_url": source_url,
    }


def seed_races(conn: sqlite3.Connection) -> int:
    """Insert the verified roster without overwriting user-maintained race rows.

    The caller owns the transaction and creates the ``races`` table.
    """
    before = conn.total_changes
    columns = ", ".join(RACE_COLUMNS)
    placeholders = ", ".join(f":{column}" for column in RACE_COLUMNS)
    conn.executemany(
        f"INSERT OR IGNORE INTO races ({columns}) VALUES ({placeholders})",
        race_rows(),
    )
    return conn.total_changes - before
