"""Learn how group-targeted news moves a group's vote, from past midterms.

For each midterm 2010-2022 the same Google News group searches used live are
re-run for the equivalent pre-election window (30 days ending 13 days before
Election Day, matching the 2026 cutoff) and scored with the same rules, with
that year's White House party for the referendum rule. The outcome is each
racial group's CES House swing beyond what its usual midterm responsiveness
predicts:

    deviation[g, y] = shift[g, y] - ratio[g] x national_shift[y]

Both sides are centered on the voter-weighted mean across groups, because each
year's national swing is already explained by the midterm/poll baseline. The coefficient is
a weighted least-squares slope through the origin (weights 1 / CES SE^2) with a
normal prior N(0, PRIOR_SD^2) on the slope, so a noisy fit is shrunk toward no
effect rather than toward an assumed value.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from .db import ROOT, utc_now
from .elasticity import load_elasticity
from .news import GROUP_QUERIES, _url_key, aggregate_groups, group_features, normalize_date, plain_text

ARTIFACT = ROOT / "data" / "model" / "news_effect.json"
RESEARCH = ROOT / "data" / "cache" / "research" / "ces_elasticity" / "outputs"
ELECTIONS = {2010: "2010-11-02", 2014: "2014-11-04", 2018: "2018-11-06", 2022: "2022-11-08"}
INCUMBENT = {2010: "democratic", 2014: "democratic", 2018: "republican", 2022: "democratic"}
LEAD_DAYS = 13  # 2026-10-21 cutoff is 13 days before the 2026-11-03 election
WINDOW_DAYS = 30
GROUPS = ("hispanic", "black", "asian", "white")
EVIDENCE_HALF = 8.0  # articles at which a group signal counts half
PRIOR_SD = 6.0  # pp per unit of evidence-weighted signal; the former hand-set scale
CAP_PP = 12.0


def evidence(item):
    """Group signal x n / (n + 8), or None without a scored article."""
    if not item or item.get("signal") is None:
        return None
    n = item.get("articles", 0) or 0
    return float(item["signal"]) * n / (n + EVIDENCE_HALF)


def _window(year):
    cutoff = date.fromisoformat(ELECTIONS[year]) - timedelta(days=LEAD_DAYS)
    return cutoff - timedelta(days=WINDOW_DAYS - 1), cutoff


def _fetch(url, path, pause):
    if path.exists():
        return path.read_bytes()
    raw = urlopen(Request(url, headers={"User-Agent": "ElectionResearch/0.4 (aggregate research)"}), timeout=40).read(5_000_001)
    path.write_bytes(raw)
    path.with_suffix(".metadata.json").write_text(json.dumps(
        {"url": url, "retrieved_at": utc_now(), "sha256": hashlib.sha256(raw).hexdigest()}, indent=2), encoding="utf-8")
    time.sleep(pause)
    return raw


def historical_articles(year, cache_dir=ROOT / "data" / "cache", pause=2.0):
    """Google News group-search items for one midterm's pre-election window."""
    start, cutoff = _window(year)
    folder = Path(cache_dir) / "news_backtest" / str(year)
    folder.mkdir(parents=True, exist_ok=True)
    articles, seen, errors = [], set(), []
    for query in GROUP_QUERIES:
        q = query.replace("2026", str(year)) + f" after:{start.isoformat()} before:{(cutoff + timedelta(days=1)).isoformat()}"
        url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
        try:
            raw = _fetch(url, folder / f"{hashlib.sha256(q.encode()).hexdigest()[:16]}.xml", pause)
            items = ET.fromstring(raw).findall(".//item")
        except (OSError, ET.ParseError) as exc:
            errors.append({"query": q, "error": str(exc)})
            continue
        for item in items:
            title, link = plain_text(item.findtext("title") or ""), item.findtext("link") or ""
            publisher = plain_text(item.findtext("source") or "") or "Google News"
            title = re.sub(r"\s+-\s+" + re.escape(publisher) + r"\s*$", "", title)
            try:
                published = date.fromisoformat(normalize_date(item.findtext("pubDate") or "")[:10])
            except (ValueError, TypeError, OverflowError):
                continue
            key = _url_key(link)
            if not title or key in seen or not start <= published <= cutoff:
                continue
            seen.add(key)
            articles.append({"title": title, "source_name": publisher, "published": published.isoformat(),
                             "weight": 2 ** (-(cutoff - published).days / 14),
                             "groups": group_features(title, INCUMBENT[year])})
    return articles, errors


def _outcomes(research_dir, ratios):
    shares = {}
    with open(Path(research_dir) / "midterm_shifts.csv", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["level"] == "race5" and row["group"] in GROUPS:
                year = int(row["transition"][-4:])
                if year in ELECTIONS:
                    shift, national = float(row["shift_pp"]), float(row["national_shift_pp"])
                    shares[year, row["group"]] = {"shift_pp": shift, "se_pp": float(row["shift_se_pp"]),
                        "national_shift_pp": national,
                        "deviation_pp": shift - ratios[row["group"]] * national}
    weights = {}
    with open(Path(research_dir) / "turnout.csv", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            if row["spec"] == "turnout_citizen25" and row["level"] == "race5" and row["group"] in GROUPS and int(row["year"]) in ELECTIONS:
                # Voter share = population share x relative turnout.
                weights[int(row["year"]), row["group"]] = float(row["pop_share"]) * float(row["turnout_rel_national"])
    return shares, weights


def fit(points):
    """Centered WLS slope through the origin with a N(0, PRIOR_SD^2) prior."""
    used = [p for p in points if p["x"] is not None]
    sxx = sum(p["x"] ** 2 / p["se_pp"] ** 2 for p in used)
    sxy = sum(p["x"] * p["y"] / p["se_pp"] ** 2 for p in used)
    if not used or sxx == 0:
        return {"n": len(used), "beta_wls": None, "se_wls": None, "beta": 0.0, "se": PRIOR_SD}
    beta_wls, se_wls = sxy / sxx, math.sqrt(1 / sxx)
    residual = [(p["y"] - beta_wls * p["x"]) / p["se_pp"] for p in used]
    # Inflate the SE when the scatter exceeds the CES sampling error.
    scale = max(1.0, sum(r * r for r in residual) / max(1, len(used) - 1))
    se_wls *= math.sqrt(scale)
    precision = 1 / se_wls ** 2 + 1 / PRIOR_SD ** 2
    beta = (beta_wls / se_wls ** 2) / precision
    loo = []
    for i, p in enumerate(used):
        rest = used[:i] + used[i + 1:]
        b = sum(q["x"] * q["y"] / q["se_pp"] ** 2 for q in rest) / (sum(q["x"] ** 2 / q["se_pp"] ** 2 for q in rest) or math.inf)
        loo.append((p["y"] - b * p["x"]) ** 2)
    return {"n": len(used), "beta_wls": beta_wls, "se_wls": se_wls, "dispersion": scale,
            "beta": beta, "se": math.sqrt(1 / precision),
            "loo_rmse_pp": math.sqrt(sum(loo) / len(loo)),
            "zero_effect_rmse_pp": math.sqrt(sum(p["y"] ** 2 for p in used) / len(used))}


def build_news_effect(cache_dir=ROOT / "data" / "cache", research_dir=RESEARCH, artifact_path=ARTIFACT, pause=2.0):
    elasticity = load_elasticity()
    if not elasticity:
        raise ValueError("Run python main.py build-elasticity first")
    ratios = {g: elasticity["races"][g]["ratio"] for g in GROUPS}
    outcomes, voter_share = _outcomes(research_dir, ratios)
    points, years, errors = [], {}, []
    for year in sorted(ELECTIONS):
        articles, failed = historical_articles(year, cache_dir, pause)
        errors += failed
        signals = aggregate_groups(articles)
        raw = {g: evidence(signals.get(g)) for g in GROUPS}
        present = [g for g in GROUPS if raw[g] is not None and (year, g) in outcomes]
        total = sum(voter_share[year, g] for g in present)
        mean_x = sum(voter_share[year, g] * raw[g] for g in present) / total if total else 0.0
        mean_y = sum(voter_share[year, g] * outcomes[year, g]["deviation_pp"] for g in present) / total if total else 0.0
        years[year] = {"window": [d.isoformat() for d in _window(year)], "articles": len(articles),
                       "incumbent": INCUMBENT[year], "groups": {g: {k: signals[g][k] for k in ("signal", "articles", "publishers")} for g in GROUPS}}
        for g in GROUPS:
            if (year, g) not in outcomes:
                continue
            points.append({"year": year, "group": g, **outcomes[year, g], "signal": signals[g]["signal"],
                           "articles": signals[g]["articles"], "evidence": raw[g],
                           "x": raw[g] - mean_x if g in present else None,
                           "y": outcomes[year, g]["deviation_pp"] - mean_y})
    result = fit(points)
    artifact = {"built_at": utc_now(), "method": __doc__.strip().split("\n\n")[1].replace("\n", " "),
                "coefficient_pp_per_unit": result["beta"], "coefficient_se": result["se"], "fit": result,
                "prior_sd": PRIOR_SD, "evidence_half_articles": EVIDENCE_HALF, "cap_pp": CAP_PP,
                "ratios": ratios, "years": years, "points": points, "errors": errors,
                "queries": list(GROUP_QUERIES)}
    artifact_path = Path(artifact_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    load_news_effect.cache_clear()
    return {"artifact": str(artifact_path), **result, "articles_by_year": {y: v["articles"] for y, v in years.items()},
            "errors": errors}


@lru_cache(maxsize=1)
def load_news_effect(artifact_path=ARTIFACT):
    path = Path(artifact_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
