"""Import actual CES survey microdata into aggregate historical cells only."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin

import requests
import pandas as pd
from bs4 import BeautifulSoup

SDA_DATASET = "ces-cumulative-2024-v10"
SDA_URL = f"https://sda.berkeley.edu/sdaweb/analysis/?dataset={SDA_DATASET}"
SDA_POST_URL = "https://sda.berkeley.edu/sdaweb/analysis/index.jsf"
SDA_VARIABLES = (
    "year", "weight", "weight_post", "tookpost", "state", "state_post", "dist_up", "dist_up_post",
    "age", "gender", "educ", "race_h", "voted_rep_party", "voted_sen_party",
    "voted_gov_party",
)


def download_ces_subset(cache_dir: Path) -> Path:
    """Download a minimal 2022/2024 CES subset through Berkeley SDA's public form.

    This uses the same read-only export operation as SDA's Download Custom Subset
    interface. Dynamic form IDs are discovered from the returned form.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "ces_2022_2024_sda_v10.csv"
    if path.exists() and path.stat().st_size > 1000:
        with path.open(encoding="utf-8") as existing:
            header_matches = set(SDA_VARIABLES).issubset(existing.readline().strip().split(","))
        metadata_path = path.with_suffix(".metadata.json")
        if header_matches and metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as error:
                raise ValueError("CES cache metadata is unreadable; restore the original cache or download again") from error
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != metadata.get("sha256"):
                raise ValueError("CES cache checksum mismatch; restore the original cache or remove the cached CSV and metadata to download again")
            if metadata.get("dataset") != SDA_DATASET or metadata.get("selection") != "year(2022,2024)":
                raise ValueError("CES cache metadata does not match the verified dataset selection")
            return path
    session = requests.Session()
    response = session.get(SDA_URL, timeout=45)
    response.raise_for_status()
    document = BeautifulSoup(response.text, "html.parser")
    state = document.find("input", {"name": "javax.faces.ViewState"})["value"]

    def post(source, form, values=None, render=None, event=None):
        nonlocal state
        payload = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": source,
            "javax.faces.partial.execute": form,
            form: form,
            "javax.faces.ViewState": state,
        }
        if event:
            payload["javax.faces.behavior.event"] = event
            payload["javax.faces.partial.event"] = "change" if event == "valueChange" else event
        else:
            payload[source] = source
        if render:
            payload["javax.faces.partial.render"] = render
        payload.update(values or {})
        result = session.post(SDA_POST_URL, data=payload, timeout=60)
        result.raise_for_status()
        xml = ET.fromstring(result.text)
        fragments = []
        for update in xml.iter("update"):
            if "ViewState" in update.attrib.get("id", ""):
                state = update.text
            else:
                fragments.append(update.text or "")
        return BeautifulSoup("".join(fragments), "html.parser")

    document = post("top-form:cb-subset", "top-form", render="top-form mainPanel vsf:varselectpanel")
    form = document.find("form", {"id": "subsetWizardForm"})
    if form is None:
        raise ValueError("Berkeley SDA did not return its subset export form")
    radio = form.find("input", {"type": "radio", "value": "CSV"})["name"]
    fields = {
        radio: "CSV",
        "subsetWizardForm:subsetTabview:filters": "year(2022,2024)",
        "subsetWizardForm:subsetTabview:varTextarea": " ".join(SDA_VARIABLES),
        "subsetWizardForm:subsetTabview_activeIndex": "3",
    }
    # Register the normal input changes, then open the form's final review tab.
    for source in (radio, "subsetWizardForm:subsetTabview:filters", "subsetWizardForm:subsetTabview:varTextarea"):
        post(source, "subsetWizardForm", fields, event="valueChange")
    review = post(
        "subsetWizardForm:subsetTabview", "subsetWizardForm",
        {**fields, "subsetWizardForm:subsetTabview_newTab": "subsetWizardForm:subsetTabview:createfiles"},
        render="subsetWizardForm:subsetTabview:createfilespanel", event="tabChange",
    )
    button = next((b for b in review.find_all("button") if b.get_text(strip=True) == "Create Files"), None)
    if button is None or button.has_attr("disabled"):
        raise ValueError("Berkeley SDA could not validate the requested CES subset")
    files = post(button["id"], "subsetWizardForm", fields, render="subsetWizardForm:subsetTabview:fileLinksPanel")
    links = [(a.get_text(" ", strip=True), a.get("href", "")) for a in files.find_all("a")]
    download = next((href for label, href in links if "data" in label.lower() or href.lower().endswith(".csv")), None)
    if download:
        response = session.get(urljoin(SDA_POST_URL, download), timeout=60)
    else:
        button = next((b for b in files.find_all("button") if b.get_text(strip=True) == "Data file"), None)
        if button is None:
            raise ValueError("Berkeley SDA returned no subset data download control")
        response = session.post(SDA_POST_URL, data={
            "subsetWizardForm": "subsetWizardForm", "javax.faces.ViewState": state,
            button["id"]: button["id"], **fields,
        }, timeout=60)
    response.raise_for_status()
    # Never cache a server error page as if it were survey observations.
    if response.content.lstrip().startswith(b"<"):
        raise ValueError("Berkeley SDA returned HTML instead of the requested CSV")
    path.write_bytes(response.content)
    from .db import utc_now
    path.with_suffix(".metadata.json").write_text(json.dumps({
        "dataset": SDA_DATASET, "url": SDA_URL, "retrieved_at": utc_now(),
        "selection": "year(2022,2024)", "variables": SDA_VARIABLES,
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }, indent=2), encoding="utf-8")
    return path


RACE_CODES = {
    1: "white", 2: "black", 3: "hispanic", 4: "asian",
    5: "american_indian_alaska_native", 6: "multiracial", 7: "other_race",
    8: "white",
}
EDUCATION_CODES = {
    1: "high_school_or_less", 2: "high_school_or_less", 3: "some_college_or_more",
    4: "some_college_or_more", 5: "some_college_or_more", 6: "some_college_or_more",
}
RACE_SCHEME = "ces_hispanic_exclusive"
MIN_SAMPLE_SIZE = 30


def aggregate_ces(path: Path, year: int, min_sample_size: int = MIN_SAMPLE_SIZE) -> tuple[list[dict], dict]:
    """Produce weighted 25+ D/(D+R) cells, suppressing small raw/effective samples.

    District cells describe the district map elected in the historical year. They
    must not be interpreted as observations on a later redistricted electorate.
    Kish effective n describes weight concentration only, not full survey design.
    """
    from .races import STATES
    if year not in (2022, 2024):
        raise ValueError("The verified CES subset supports election years 2022 and 2024")
    if min_sample_size < 30:
        raise ValueError("Survey cell suppression cannot be set below 30 respondents")
    frame = pd.read_csv(path, skipinitialspace=True, na_values=["."], usecols=SDA_VARIABLES)
    frame = frame.loc[frame["year"].eq(year)].copy()
    if len(frame) != 60000:
        raise ValueError(f"Expected 60,000 CES {year} respondents; received {len(frame):,}")
    # v10 predates the 2024 validation release and has no 2024 post-wave weights.
    # The cumulative guide allows normal weights as a second-best alternative.
    weight_name = "weight_post" if frame["weight_post"].notna().any() else "weight"
    frame["w"] = frame[weight_name]
    frame["race_group"] = frame["race_h"].map(RACE_CODES)
    frame["sex"] = frame["gender"].map({1: "male", 2: "female"})
    frame["education"] = frame["educ"].map(EDUCATION_CODES)
    frame["state_fips"] = frame["state_post"].fillna(frame["state"])
    frame["district"] = frame["dist_up_post"].fillna(frame["dist_up"])
    valid_fips = {int(info["fips"]): info for info in STATES.values()}
    usable = frame.loc[
        frame["age"].ge(25) & frame["w"].gt(0) & frame["w"].lt(float("inf"))
        & frame["state_fips"].isin(valid_fips)
    ].dropna(subset=["race_group", "sex", "education"]).copy()
    usable["state_geo"] = usable["state_fips"].map(lambda f: f"state:{int(f):02d}")

    def district_geo(row):
        fips = int(row["state_fips"])
        district = row["district"]
        if pd.isna(district):
            return None
        info = valid_fips[fips]
        if info["house_seats"] == 1:
            district = 0
        elif district != int(district) or not 1 <= district <= info["house_seats"]:
            return None
        return f"cd:{fips:02d}{int(district):02d}"

    usable["district_geo"] = usable.apply(district_geo, axis=1)
    usable["national_geo"] = "us"
    rows = []
    office_sizes = {}
    source_id = f"ces-cumulative-v10-{year}"
    kind = "survey_weighted" if weight_name == "weight_post" else "survey_weighted_preweight"
    for office, variable in (("house", "voted_rep_party"), ("senate", "voted_sen_party"), ("governor", "voted_gov_party")):
        voters = usable.loc[usable[variable].isin([1, 2])].copy()
        voters["dem_w"] = voters["w"] * voters[variable].eq(1)
        voters["w_squared"] = voters["w"] ** 2
        office_sizes[office] = len(voters)
        # National Senate/governor averages cover only states holding those
        # offices that year, as reflected by a nonmissing reported party choice.
        for geography in ("national_geo", "state_geo", "district_geo"):
            if geography == "district_geo" and office != "house":
                continue
            groups = voters.groupby([geography, "race_group", "sex", "education"], dropna=True)
            for (geo, race, sex, education), group in groups:
                sample_size = len(group)
                total = float(group["w"].sum())
                effective_n = total * total / float(group["w_squared"].sum())
                suppressed = sample_size < min_sample_size or effective_n < min_sample_size
                rows.append({
                    "geography_id": geo, "election_year": year, "office": office,
                    "race_group": race, "sex": sex, "education": education,
                    "dem_share": None if suppressed else float(group["dem_w"].sum() / total),
                    "sample_size": sample_size, "effective_n": effective_n,
                    "weighted_total": total,
                    "estimate_kind": "survey_suppressed" if suppressed else kind,
                    "race_scheme": RACE_SCHEME,
                    "universe": "survey_voters_age25_plus",
                    "boundary_vintage": f"{118 if year == 2022 else 119}th_congress" if geo.startswith("cd:") else "state",
                    "source_id": source_id,
                })
    return rows, {
        "year": year, "respondents": len(frame), "weight": weight_name,
        "usable_demographic_respondents": len(usable), "two_party_voters_by_office": office_sizes,
        "cells": len(rows), "published_cells": sum(r["dem_share"] is not None for r in rows),
        "suppressed_cells": sum(r["dem_share"] is None for r in rows),
        "race_scheme": RACE_SCHEME, "minimum_raw_and_effective_n": min_sample_size,
    }


def fetch_survey(conn, year: int = 2024, cache_dir: Path = Path("data/cache")) -> dict:
    """Download/cache verified CES data and replace this source's aggregate cells."""
    from .db import utc_now
    path = download_ces_subset(cache_dir)
    rows, report = aggregate_ces(path, year)
    source_id = f"ces-cumulative-v10-{year}"
    notes = (
        "Shiro Kuriwaki, Cumulative CES Common Content, DOI10.7910/DVN/II2DB6 v10; "
        "Berkeley SDA mirror. Self-reported Democratic/(Democratic+Republican) vote, "
        "age25+, race_h any-part Hispanic-exclusive categories, standardized sex. Two education levels: high school or less, some college or more. Middle Eastern respondents join White to align legacy ACS race definitions. "
        f"Weight={report['weight']}. Raw or Kish effective n<30 suppressed. "
        "National Senate/governor averages cover states holding that office. "
        "District estimates are descriptive and are not district-representative samples. "
        "Kish effective n does not account for full survey design."
    )
    if year == 2024:
        notes += " 2024 v10 predates vote validation and lacks post-wave weights; normal pre-wave weight used per guide, with residual attrition risk."
    metadata_path = path.with_suffix(".metadata.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    retrieved_at = metadata.get("retrieved_at", utc_now())
    with conn:
        conn.execute(
            "INSERT INTO sources(source_id,name,url,retrieved_at,kind,notes) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,url=excluded.url,"
            "retrieved_at=excluded.retrieved_at,kind=excluded.kind,notes=excluded.notes",
            (source_id, f"CES {year} weighted historical demographic vote estimates (cumulative v10)",
             "https://doi.org/10.7910/DVN/II2DB6", retrieved_at, "survey", notes),
        )
        conn.execute("DELETE FROM historical_cells WHERE source_id=?", (source_id,))
        if rows:
            columns = list(rows[0])
            conn.executemany(
                "INSERT INTO historical_cells(" + ",".join(columns) + ") VALUES(" + ",".join(":" + c for c in columns) + ")", rows,
            )
    return {**report, "source_id": source_id, "cache_file": str(path), "notes": notes}
