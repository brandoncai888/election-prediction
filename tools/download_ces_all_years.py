"""Prototype: download CES cumulative 2006-2024 (even years) subset from Berkeley SDA.

Adapted (copied, not imported) from electiondb/surveys.py::download_ces_subset with
parameterized filter / variable list / output path.

Usage:
    python -I download_ces_all_years.py <cache_dir>
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SDA_DATASET = "ces-cumulative-2024-v10"
SDA_URL = f"https://sda.berkeley.edu/sdaweb/analysis/?dataset={SDA_DATASET}"
SDA_POST_URL = "https://sda.berkeley.edu/sdaweb/analysis/index.jsf"

# Exact names verified against
# https://sda.berkeley.edu/sdaweb/docs/ces-cumulative-2024-v10/DOC/hcbkx01.htm
VARIABLES = (
    "year", "weight", "weight_cumulative", "weight_post", "tookpost",
    "state", "dist_up", "gender", "educ", "race_h", "age", "citizen",
    "voted_rep_party", "intent_rep_party", "voted_pres_party", "vv_turnout_gvm",
)
EVEN_YEARS = tuple(range(2006, 2025, 2))
FILTER = "year(" + ",".join(str(y) for y in EVEN_YEARS) + ")"


def download_subset(cache_dir: Path, variables=VARIABLES, selection=FILTER,
                    filename="ces_cumulative_even_2006_2024_sda_v10.csv") -> Path:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / filename
    meta_path = path.with_suffix(".metadata.json")
    if path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if hashlib.sha256(path.read_bytes()).hexdigest() == meta.get("sha256") \
                and meta.get("selection") == selection and list(meta.get("variables", [])) == list(variables):
            print("cache hit", path)
            return path
    session = requests.Session()
    session.headers["User-Agent"] = "election-forecast-research/0.1 (python-requests)"
    t0 = time.time()
    response = session.get(SDA_URL, timeout=45)
    response.raise_for_status()
    document = BeautifulSoup(response.text, "html.parser")
    state = document.find("input", {"name": "javax.faces.ViewState"})["value"]

    def post(source, form, values=None, render=None, event=None, timeout=120):
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
        result = session.post(SDA_POST_URL, data=payload, timeout=timeout)
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
        "subsetWizardForm:subsetTabview:filters": selection,
        "subsetWizardForm:subsetTabview:varTextarea": " ".join(variables),
        "subsetWizardForm:subsetTabview_activeIndex": "3",
    }
    for source in (radio, "subsetWizardForm:subsetTabview:filters", "subsetWizardForm:subsetTabview:varTextarea"):
        post(source, "subsetWizardForm", fields, event="valueChange")
    review = post(
        "subsetWizardForm:subsetTabview", "subsetWizardForm",
        {**fields, "subsetWizardForm:subsetTabview_newTab": "subsetWizardForm:subsetTabview:createfiles"},
        render="subsetWizardForm:subsetTabview:createfilespanel", event="tabChange",
    )
    button = next((b for b in review.find_all("button") if b.get_text(strip=True) == "Create Files"), None)
    if button is None or button.has_attr("disabled"):
        (cache_dir / "sda_review_debug.html").write_text(str(review), encoding="utf-8")
        raise ValueError("Berkeley SDA could not validate the requested CES subset (see sda_review_debug.html)")
    t1 = time.time()
    files = post(button["id"], "subsetWizardForm", fields,
                 render="subsetWizardForm:subsetTabview:fileLinksPanel", timeout=600)
    print(f"create files took {time.time() - t1:.1f}s")
    links = [(a.get_text(" ", strip=True), a.get("href", "")) for a in files.find_all("a")]
    print("links:", links)
    download = next((href for label, href in links if "data" in label.lower() or href.lower().endswith(".csv")), None)
    t2 = time.time()
    if download:
        response = session.get(urljoin(SDA_POST_URL, download), timeout=600)
    else:
        button = next((b for b in files.find_all("button") if b.get_text(strip=True) == "Data file"), None)
        if button is None:
            (cache_dir / "sda_files_debug.html").write_text(str(files), encoding="utf-8")
            raise ValueError("Berkeley SDA returned no subset data download control")
        response = session.post(SDA_POST_URL, data={
            "subsetWizardForm": "subsetWizardForm", "javax.faces.ViewState": state,
            button["id"]: button["id"], **fields,
        }, timeout=600)
    response.raise_for_status()
    print(f"data download took {time.time() - t2:.1f}s, {len(response.content):,} bytes")
    if response.content.lstrip().startswith(b"<"):
        (cache_dir / "sda_data_debug.html").write_bytes(response.content[:200000])
        raise ValueError("Berkeley SDA returned HTML instead of the requested CSV")
    path.write_bytes(response.content)
    meta_path.write_text(json.dumps({
        "dataset": SDA_DATASET, "url": SDA_URL,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "selection": selection, "variables": list(variables),
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
        "elapsed_seconds": round(time.time() - t0, 1),
    }, indent=2), encoding="utf-8")
    print(f"total {time.time() - t0:.1f}s -> {path}")
    return path


if __name__ == "__main__":
    out = download_subset(Path(sys.argv[1]))
    print(out, out.stat().st_size)
