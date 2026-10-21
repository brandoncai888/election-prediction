"""Population-standardized 2026 predictions with fitted 2024 survey baselines.

The baseline is estimated; the news response is an explicit sensitivity prior.
Those are deliberately kept separate in every returned prediction.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import brentq, minimize
from scipy.special import expit, logit
from scipy.stats import norm

from .db import ROOT, utc_now
from .races import STATES

MODEL_VERSION = "2026-population-news-v1"  # fitted survey artifact
FORECAST_VERSION = "2026-anchored-news-v2"  # race forecasts built on the artifact
# Margin uncertainty in two-party percentage points (D minus R).
# race_sd: robust SD of Senate margin minus same-state presidential margin,
# MEDSL 1976-2024 data, 2010-2024 contests (8.0 all states; 6.1 within 20 pts).
# House uses the Senate value until district House results are imported;
# governor races decouple more from presidential partisanship (assumption).
# national_sd: shared error in the news-predicted national environment. The
# spread of midterm national swings 2010-2022 around their mean is ~4-5 pts.
UNCERTAINTY = {"national_sd_margin_pp": 5.0,
               "race_sd_margin_pp": {"house": 7.0, "senate": 7.0, "governor": 9.0},
               "simulations": 20000, "seed": 2026,
               "basis": "Race SD: robust spread of Senate-minus-presidential margins, MEDSL 2010-2024 (House shares the Senate value; governor widened by assumption). National SD: assumed error of the news-implied national environment."}
# Win-probability tiers modeled on 270toWin's Safe/Likely/Lean/Tilt/Toss-up.
RATING_TIERS = ((0.95, "safe"), (0.85, "likely"), (0.65, "lean"), (0.55, "tilt"))
DEFAULT_ARTIFACT = ROOT / "data" / "model" / "forecast2026.json"
EDUCATION = ("high_school_or_less", "some_college_or_more")
SEXES = ("male", "female")
RAW_RACES = ("white", "black", "american_indian_alaska_native", "asian",
             "native_hawaiian_pacific_islander", "other_race", "multiracial")
POPULATION_RACES = tuple(r + "_non_hispanic" for r in RAW_RACES) + ("hispanic",)
SURVEY_RACES = ("white", "black", "hispanic", "asian",
                "american_indian_alaska_native", "multiracial", "other_race")
RACE_CODES = {1: "white", 2: "black", 3: "hispanic", 4: "asian",
              5: "american_indian_alaska_native", 6: "multiracial", 7: "other_race", 8: "white"}
EDUCATION_BRIDGE = {
    "less_than_high_school": EDUCATION[0], "high_school": EDUCATION[0],
    "some_college": EDUCATION[1], "bachelors_plus": EDUCATION[1],
    **{e: e for e in EDUCATION},
}
TOPIC_SENSITIVITY = {
    "economy": {"high_school_or_less": 1.25, "some_college_or_more": 1.0},
    "immigration": {"hispanic": 1.3, "asian": 1.15},
    "abortion": {"female": 1.3, "male": 0.85},
    "healthcare": {"female": 1.15, "high_school_or_less": 1.15},
    "democracy": {"some_college_or_more": 1.2},
    "crime": {"high_school_or_less": 1.15},
    "foreign_policy": {}, "education": {"some_college_or_more": 1.15},
    "climate": {"some_college_or_more": 1.2},
}
LIMITATIONS = [
    "Experimental population-standardized two-party forecast, not a calibrated election probability.",
    "ACS counts are residents age 25+, including noncitizens; equal turnout is assumed. Voters age 18-24 are absent.",
    "2024 CES v10 uses pre-election survey weights because post-wave weights and validated votes are unavailable in this cached release.",
    "News-to-vote coefficients and demographic issue sensitivities are explicit assumptions, not effects learned from historical elections.",
    "Nonwhite Hispanic populations are allocated proportionally within sex and education; that race/ethnicity bridge is estimated, not an observed intersection.",
    "House geography uses ACS 119th-Congress district proxies; 2026 redistricting has not been reconciled.",
    "Candidate identity, incumbency, campaign spending, differential turnout, third parties, and election-specific uncertainty are not modeled.",
    "Senate and governor baselines share fitted state preferences with House and add 2024 national office effects; they do not transfer a past candidate's personal vote.",
]


def _features():
    names = ["intercept", "sex:female", "education:some_college_or_more"]
    penalties = [0.0, 8.0, 8.0]
    for race in SURVEY_RACES[1:]:
        names.append("race:" + race)
        penalties.append(15.0)
    for race in SURVEY_RACES:
        for sex in SEXES:
            for education in EDUCATION:
                names.append(f"cell:{race}:{sex}:{education}")
                penalties.append(60.0)
    for info in STATES.values():
        names.append("state:" + info["fips"])
        penalties.append(25.0)
    for info in STATES.values():
        for number in range(1, info["house_seats"] + 1):
            district = "00" if info["house_seats"] == 1 else f"{number:02d}"
            names.append(f"district:cd:{info['fips']}{district}")
            penalties.append(30.0)
    for office in ("senate", "governor"):
        names.append("office:" + office)
        penalties.append(25.0)
    return names, np.asarray(penalties, dtype=float)


def _survey_race(population_race):
    race = population_race.removesuffix("_non_hispanic")
    return "other_race" if race == "native_hawaiian_pacific_islander" else race


def _row_features(race, sex, education, state, geography_id, office):
    race = _survey_race(race)
    result = ["intercept", "race:" + race, f"cell:{race}:{sex}:{education}", "state:" + state]
    if sex == "female":
        result.append("sex:female")
    if education == EDUCATION[1]:
        result.append("education:some_college_or_more")
    if office == "house" and geography_id and geography_id.startswith("cd:"):
        result.append("district:" + geography_id)
    if office != "house":
        result.append("office:" + office)
    return result


def _matrix(rows, names):
    lookup = {name: i for i, name in enumerate(names)}
    ri, ci = [], []
    for i, row in enumerate(rows):
        for name in _row_features(row["race_group"], row["sex"], row["education"],
                                  row["state_fips"], row.get("geography_id"), row["office"]):
            if name in lookup:
                ri.append(i)
                ci.append(lookup[name])
    return sparse.csr_matrix((np.ones(len(ri)), (ri, ci)), shape=(len(rows), len(names)))


def _fit_logistic(matrix, target, weights, penalties):
    """Weighted binomial likelihood plus Gaussian partial-pooling penalties."""
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.mean()
    target = np.asarray(target, dtype=float)

    def objective(coef):
        eta = matrix @ coef
        loss = np.sum(weights * (np.logaddexp(0, eta) - target * eta))
        gradient = matrix.T @ (weights * (expit(eta) - target))
        return float(loss + .5 * np.dot(penalties, coef ** 2)), np.asarray(gradient) + penalties * coef

    initial = np.zeros(matrix.shape[1])
    p = float(np.dot(weights, target) / weights.sum())
    initial[0] = math.log(max(p, 1e-6) / max(1 - p, 1e-6))
    result = minimize(objective, initial, jac=True, method="L-BFGS-B",
                      options={"maxiter": 400, "ftol": 1e-10, "gtol": 1e-5})
    if not result.success:
        raise RuntimeError("2024 baseline optimization failed: " + str(result.message))
    return result.x, {"converged": True, "iterations": int(result.nit), "objective": float(result.fun)}


def _metrics(target, prediction, weights):
    target, prediction, weights = map(lambda v: np.asarray(v, dtype=float), (target, prediction, weights))
    weights = weights / weights.sum()
    p = np.clip(prediction, 1e-12, 1 - 1e-12)
    bins = []
    for lower in np.arange(0, 1, .1):
        mask = (prediction >= lower) & (prediction < lower + .1 + (1e-10 if lower > .89 else 0))
        if mask.any():
            w = weights[mask]
            bins.append({"lower": round(float(lower), 1), "upper": round(float(lower + .1), 1),
                         "respondent_votes": int(mask.sum()),
                         "predicted_dem_share": float(np.average(prediction[mask], weights=w)),
                         "observed_dem_share": float(np.average(target[mask], weights=w))})
    return {"n_vote_records": len(target), "weighted_brier": float(np.dot(weights, (prediction - target) ** 2)),
            "weighted_log_loss": float(-np.dot(weights, target * np.log(p) + (1 - target) * np.log(1 - p))),
            "predicted_dem_share": float(np.dot(weights, prediction)),
            "observed_dem_share": float(np.dot(weights, target)),
            "share_error_pp": float(100 * np.dot(weights, prediction - target)), "calibration_bins": bins}


def _training_records(path):
    columns = ["year", "weight", "weight_post", "tookpost", "state", "state_post", "dist_up", "dist_up_post",
               "age", "gender", "educ", "race_h", "voted_rep_party", "voted_sen_party", "voted_gov_party"]
    frame = pd.read_csv(path, usecols=columns, skipinitialspace=True, na_values=["."])
    frame = frame.loc[frame.year.eq(2024)].copy()
    weight_name = "weight_post" if frame.weight_post.notna().any() else "weight"
    frame["w"] = frame[weight_name]
    frame["state_fips"] = frame.state_post.fillna(frame.state)
    frame["district"] = frame.dist_up_post.fillna(frame.dist_up)
    frame["race_group"] = frame.race_h.map(RACE_CODES)
    frame["sex"] = frame.gender.map({1: "male", 2: "female"})
    frame["education"] = frame.educ.map({1: EDUCATION[0], 2: EDUCATION[0], 3: EDUCATION[1],
                                        4: EDUCATION[1], 5: EDUCATION[1], 6: EDUCATION[1]})
    states = {int(v["fips"]): v for v in STATES.values()}
    frame = frame.loc[frame.age.ge(25) & frame.w.gt(0) & np.isfinite(frame.w)
                      & frame.state_fips.isin(states)].dropna(subset=["race_group", "sex", "education"])
    records = []
    for index, row in frame.iterrows():
        state = states[int(row.state_fips)]
        district = row.district
        geo = None
        if state["house_seats"] == 1:
            geo = f"cd:{state['fips']}00"
        elif pd.notna(district) and int(district) == district and 1 <= district <= state["house_seats"]:
            geo = f"cd:{state['fips']}{int(district):02d}"
        # Hold out whole respondents, never separate office votes from the same person.
        holdout = int(hashlib.sha256(f"ces-2024:{index}:271828".encode()).hexdigest()[:8], 16) % 5 == 0
        for office, variable in (("house", "voted_rep_party"), ("senate", "voted_sen_party"), ("governor", "voted_gov_party")):
            if row[variable] not in (1, 2):
                continue
            records.append({"respondent": int(index), "race_group": row.race_group, "sex": row.sex,
                            "education": row.education, "state_fips": state["fips"], "geography_id": geo,
                            "office": office, "target": int(row[variable] == 1), "weight": float(row.w),
                            "holdout": holdout})
    if len(records) < 100:
        raise ValueError("Insufficient usable 2024 CES observations to fit a baseline")
    return records, {"weight": weight_name, "respondents_with_demographics": len(frame),
                     "respondents_with_votes": len({r["respondent"] for r in records})}


def train_model(conn=None, cache_dir=ROOT / "data" / "cache", artifact_path=DEFAULT_ARTIFACT):
    """Fit once on cached 2024 microdata, validate, then refit all observations.

    ``conn`` is accepted for CLI consistency; model fitting never publishes raw
    respondent data or changes the database.
    """
    path = Path(cache_dir) / "ces_2022_2024_sda_v10.csv"
    if not path.exists():
        raise ValueError("Import the verified CES 2022/2024 subset before training the model")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    metadata_path = path.with_suffix(".metadata.json")
    if metadata_path.exists():
        source_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if source_metadata.get("sha256") != digest:
            raise ValueError("CES checksum does not match its provenance metadata")
    records, metadata = _training_records(path)
    names, penalties = _features()
    matrix = _matrix(records, names)
    target = np.asarray([r["target"] for r in records])
    weights = np.asarray([r["weight"] for r in records])
    holdout = np.asarray([r["holdout"] for r in records])
    train_coef, train_fit = _fit_logistic(matrix[~holdout], target[~holdout], weights[~holdout], penalties)
    held_predictions = expit(matrix[holdout] @ train_coef)
    validation = _metrics(target[holdout], held_predictions, weights[holdout])
    null_share = float(np.average(target[~holdout], weights=weights[~holdout]))
    validation["intercept_only_weighted_brier"] = _metrics(
        target[holdout], np.full(holdout.sum(), null_share), weights[holdout])["weighted_brier"]
    validation.update({"method": "Deterministic 80/20 respondent holdout; all office votes from a respondent stay together.",
                       "scope": "Same-election 2024 survey response validation only; not 2026 race accuracy or historical news validation.",
                       "train_respondents": len({r["respondent"] for r in records if not r["holdout"]}),
                       "holdout_respondents": len({r["respondent"] for r in records if r["holdout"]}),
                       "offices": {}})
    held_records = [r for r in records if r["holdout"]]
    for office in ("house", "senate", "governor"):
        mask = np.asarray([r["office"] == office for r in held_records])
        if mask.any():
            validation["offices"][office] = _metrics(target[holdout][mask], held_predictions[mask], weights[holdout][mask])
            train_office = np.asarray([r["office"] == office for r in records]) & ~holdout
            office_null = float(np.average(target[train_office], weights=weights[train_office]))
            validation["offices"][office]["intercept_only_weighted_brier"] = _metrics(
                target[holdout][mask], np.full(mask.sum(), office_null), weights[holdout][mask])["weighted_brier"]
    coefficient, fit = _fit_logistic(matrix, target, weights, penalties)
    artifact = {"model_version": MODEL_VERSION, "status": "experimental_forecast", "trained_at": utc_now(), "baseline_year": 2024,
                "training_source": "CES cumulative v10, 2024 self-reported two-party votes, age25+",
                "source_url": "https://doi.org/10.7910/DVN/II2DB6", "source_sha256": digest,
                "features": names, "coefficients": coefficient.tolist(), "l2_penalties": penalties.tolist(),
                "fit": fit, "validation_fit": train_fit, "validation": validation,
                "training": {**metadata, "vote_records": len(records), "feature_count": len(names)},
                "news_response": {"learned": False, "party_max_pp": 2.0, "topic_max_pp": 3.0,
                                  "absolute_swing_cap_pp": 6.0, "topic_sensitivity_priors": TOPIC_SENSITIVITY},
                "limitations": LIMITATIONS, "artifact_path": str(Path(artifact_path).resolve())}
    artifact_path = Path(artifact_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(artifact_path)
    return artifact


def load_model(conn=None, cache_dir=ROOT / "data" / "cache", artifact_path=DEFAULT_ARTIFACT):
    artifact_path = Path(artifact_path)
    if artifact_path.exists():
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        source = Path(cache_dir) / "ces_2022_2024_sda_v10.csv"
        if (artifact.get("model_version") == MODEL_VERSION and source.exists()
                and artifact.get("source_sha256") == hashlib.sha256(source.read_bytes()).hexdigest()):
            return artifact
    return train_model(conn, cache_dir=cache_dir, artifact_path=artifact_path)


def population_cells(conn, geography_id):
    """Build disjoint 32-cell population weights while preserving Census totals.

    A-G remain the raw any-ethnicity race observations. H and I provide observed
    non-Hispanic White and Hispanic counts. Remaining intersections are modeled.
    """
    year = conn.execute("SELECT MAX(acs_year) FROM demographics WHERE geography_id=?", (geography_id,)).fetchone()[0]
    if year is None:
        return [], {"status": "missing_population", "message": "No ACS population counts for this geography"}
    rows = conn.execute("SELECT * FROM demographics WHERE geography_id=? AND acs_year=?", (geography_id, year)).fetchall()
    aggregated = {}
    vintages = set()
    for raw in rows:
        row = dict(raw)
        edu = EDUCATION_BRIDGE.get(row["education"])
        if edu and row["population"] is not None:
            key = (row["race_group"], row["sex"], edu)
            aggregated[key] = aggregated.get(key, 0) + row["population"]
            vintages.add(row["boundary_vintage"])
    expected = {(r, s, e) for r in (*RAW_RACES, "white_non_hispanic", "hispanic") for s in SEXES for e in EDUCATION}
    missing = sorted(expected - set(aggregated))
    # Never silently substitute Hispanic-exclusive CES estimates for any-ethnicity Census groups.
    if missing:
        return [], {"status": "missing_population", "message": "A complete A-I Census ethnicity bridge is required",
                    "missing_cells": [list(k) for k in missing], "acs_year": year}
    result, adjustments = [], []
    for sex in SEXES:
        for education in EDUCATION:
            counts = {r: float(aggregated[r, sex, education]) for r in RAW_RACES}
            white = counts["white"]
            white_nonh = min(white, max(0.0, float(aggregated["white_non_hispanic", sex, education])))
            white_hisp = white - white_nonh
            nonwhite = sum(counts[r] for r in RAW_RACES if r != "white")
            observed_hispanic = float(aggregated["hispanic", sex, education])
            hispanic_nonwhite = min(nonwhite, max(0.0, observed_hispanic - white_hisp))
            effective_hispanic = white_hisp + hispanic_nonwhite
            if abs(effective_hispanic - observed_hispanic) > .01:
                adjustments.append({"sex": sex, "education": education,
                                    "published_hispanic": observed_hispanic, "consistent_hispanic": effective_hispanic})
            nonwhite_nonh_fraction = 1 - hispanic_nonwhite / nonwhite if nonwhite else 0.0
            for race in RAW_RACES:
                population = white_nonh if race == "white" else counts[race] * nonwhite_nonh_fraction
                result.append({"race_group": race + "_non_hispanic", "sex": sex, "education": education,
                               "population": population, "population_estimated": race != "white"})
            result.append({"race_group": "hispanic", "sex": sex, "education": education,
                           "population": effective_hispanic,
                           "population_estimated": abs(effective_hispanic - observed_hispanic) > .01})
    total = sum(g["population"] for g in result)
    if total <= 0:
        return [], {"status": "missing_population", "message": "Population total must be positive"}
    return result, {"status": "complete", "acs_year": year, "population_25_plus": total,
                    "cells": len(result), "boundary_vintages": sorted(vintages),
                    "universe": "resident_population_age25_plus_all_citizenships", "turnout_assumption": "equal_across_cells",
                    "race_scheme": "modeled_hispanic_exclusive", "ethnicity_bridge_adjustments": adjustments,
                    "ethnicity_bridge": "Observed C15002H White non-Hispanic and C15002I Hispanic; nonwhite Hispanics allocated proportionally among B-G within sex and education. Total A-G population conserved."}


# News -> vote-share scales (percentage points of two-party share).
# national: a fully one-sided national news environment with strong evidence
#   moves the nation 5 pts, about the mean absolute midterm House swing
#   2010-2022 (-9.0, -3.5, +4.9, -3.0).
# group: coverage of one racial group that is fully more favorable to a party
#   than the overall environment moves that group 6 pts beyond the national
#   swing, about two-thirds of the largest group-specific midterm deviation in
#   CES (Hispanic voters, 2020->2022: ~9 pts beyond the nation).
NEWS_RESPONSE = {"national_scale_pp": 5.0, "group_scale_pp": 6.0, "national_evidence_half": 10.0,
                 "group_evidence_half": 8.0, "cap_pp": 12.0,
                 "group_signal": "group coverage direction minus overall party direction (deviation from the national environment)",
                 "national_distribution": "CES 2016->2018 cell response ratios (data/model/demographic_elasticity.json), normalized so the national population-weighted shift equals the national swing"}
CELL_NEWS_GROUP = {"hispanic": "hispanic", "black_non_hispanic": "black", "asian_non_hispanic": "asian",
                   "white_non_hispanic": "white", "american_indian_alaska_native_non_hispanic": "native"}


def national_ratio_scale(conn):
    """k such that sum(national population share x k x cell ratio) = 1."""
    from .elasticity import cell_ratio
    cells, coverage = population_cells(conn, "us")
    if not cells:
        return 1.0
    total = sum(c["population"] for c in cells)
    weighted = sum(c["population"] / total * cell_ratio(c["race_group"], c["sex"], c["education"]) for c in cells)
    return 1.0 / weighted if weighted > 0 else 1.0


def demographic_swing(race_group, sex, education, signals, national_k=1.0):
    """National swing x measured group responsiveness + racial-group news, in pp."""
    from .elasticity import cell_ratio
    race = _survey_race(race_group)
    difference = signals.get("difference")
    attributed = signals.get("attributed_article_count", 0) or 0
    strength = attributed / (attributed + NEWS_RESPONSE["national_evidence_half"])
    drivers = []
    if difference is not None:
        national_pp = NEWS_RESPONSE["national_scale_pp"] * float(np.clip(difference, -1, 1)) * strength
        ratio = cell_ratio(race_group, sex, education)
        drivers.append({"issue": "national_environment", "signal": difference, "national_swing_pp": national_pp,
                        "response_ratio": ratio, "normalizer": national_k,
                        "contribution_pp": national_pp * ratio * national_k})
    group = CELL_NEWS_GROUP.get(race_group)
    item = (signals.get("groups") or {}).get(group) if group else None
    if item and item.get("signal") is not None:
        n = item.get("articles", 0) or 0
        group_strength = n / (n + NEWS_RESPONSE["group_evidence_half"])
        deviation = float(np.clip(item["signal"] - (difference or 0.0), -1, 1))
        drivers.append({"issue": f"group_news:{group}", "signal": item["signal"], "deviation_from_national": deviation,
                        "articles": n, "evidence_strength": group_strength,
                        "contribution_pp": NEWS_RESPONSE["group_scale_pp"] * deviation * group_strength})
    topics = signals.get("topics") or {}
    salience = signals.get("topic_salience") or {}
    usable = [(k, float(v), max(0.0, float(salience.get(k, 0)))) for k, v in topics.items()
              if k in TOPIC_SENSITIVITY and v is not None and math.isfinite(float(v))]
    total_salience = sum(weight for _, _, weight in usable)
    for issue, signal, weight in usable:
        if not total_salience or not weight:
            continue
        priors = TOPIC_SENSITIVITY[issue]
        sensitivity = float(np.clip(priors.get(race, 1.0) * priors.get(sex, 1.0) * priors.get(education, 1.0), .6, 1.5))
        contribution = 3.0 * float(np.clip(signal, -1, 1)) * weight / total_salience * sensitivity * strength
        drivers.append({"issue": issue, "signal": signal, "salience": weight,
                        "sensitivity_prior": sensitivity, "contribution_pp": contribution})
    cap = NEWS_RESPONSE["cap_pp"]
    swing = float(np.clip(sum(d["contribution_pp"] for d in drivers), -cap, cap))
    return swing, drivers, strength


def _rating(margin, probability=None):
    """Tier from Democratic win probability; falls back to margin if absent."""
    if probability is None:
        absolute = abs(margin)
        side = "D" if margin > 0 else "R" if margin < 0 else "Tie"
        if absolute < 2:
            return "tossup"
        return ("lean_" if absolute < 5 else "likely_" if absolute < 10 else "safe_") + side.lower()
    favorite = max(probability, 1 - probability)
    side = "d" if probability > .5 else "r"
    for threshold, tier in RATING_TIERS:
        if favorite >= threshold:
            return f"{tier}_{side}"
    return "tossup"


def _sigma(office):
    return math.hypot(UNCERTAINTY["national_sd_margin_pp"], UNCERTAINTY["race_sd_margin_pp"].get(office, 7.0))


def win_probability(margin_pp, office):
    """P(Democrat wins) = Phi(margin / sqrt(national_sd^2 + race_sd^2))."""
    return float(norm.cdf(margin_pp / _sigma(office)))


def _anchor_offset(eta, weights, target):
    """Logit shift that makes the weighted cohort average equal the actual result."""
    return brentq(lambda delta: float(np.dot(weights, expit(eta + delta))) - target, -15, 15)


def predict_race(conn, race, *, as_of="2026-10-21", model=None, signals=None,
                 cache_dir=ROOT / "data" / "cache", artifact_path=DEFAULT_ARTIFACT, national_k=None):
    race = dict(race)
    if date.fromisoformat(as_of) >= date.fromisoformat(race["election_date"]):
        raise ValueError("Prediction cutoff must precede the election date")
    if date.fromisoformat(as_of).year <= 2024:
        raise ValueError("Prediction cutoff must follow the 2024 training election")
    model = model if model is not None else load_model(conn, cache_dir, artifact_path)
    if signals is None:
        from .news import news_signals
        signals = news_signals(conn, race, as_of=as_of)
    # Article diagnostics are served by the news API. Repeating all article text
    # and analysis inside every seat forecast would inflate a run by tens of MB.
    feature_summary = {k: v for k, v in signals.items() if k != "articles"}
    if "articles" in signals:
        feature_summary["article_ids"] = [r["news_id"] for r in signals["articles"] if r.get("news_id")]
    cells, coverage = population_cells(conn, race["geography_id"])
    national_k = national_ratio_scale(conn) if national_k is None else national_k
    result = {**race, "as_of": as_of, "baseline_year": 2024, "model_version": MODEL_VERSION,
              "status": "experimental_forecast", "dem_share": None, "rep_share": None,
              "winner": None, "margin_pp": None, "rating": "unavailable", "groups": [],
              "news_feature": feature_summary, "population_coverage": coverage,
              "news_article_count": signals.get("article_count", 0),
              "validation": model["validation"], "limitations": LIMITATIONS,
              "news_coefficient_status": "assumed_prior_not_historically_fitted",
              "prediction_kind": "population_standardized_two_party_share",
              "boundary_status": "119th_congress_proxy_unreconciled" if race["office"] == "house" else "statewide"}
    if not cells:
        result.update(status="missing_population", message=coverage["message"])
        return result
    rows = [{**g, "state_fips": race["state_fips"], "geography_id": race["geography_id"], "office": race["office"]} for g in cells]
    eta = _matrix(rows, model["features"]) @ np.asarray(model["coefficients"])
    total = coverage["population_25_plus"]
    weights = np.asarray([c["population"] / total for c in cells])
    survey_baseline = expit(eta)
    from .anchors import anchor_share
    target, anchor = anchor_share(conn, race)
    if target is not None:
        # Keep the survey's differences between cohorts, but shift every cohort
        # by one common logit offset so the district/state matches 2024 reality.
        anchor["logit_offset"] = _anchor_offset(eta, weights, target)
        anchor["actual_dem_share"] = target
        anchor["survey_only_dem_share"] = float(np.dot(weights, survey_baseline))
        baseline = expit(eta + anchor["logit_offset"])
    else:
        baseline = survey_baseline
    result["anchor"] = anchor
    groups, weighted_drivers = [], {}
    for cell, base, survey in zip(cells, baseline, survey_baseline):
        swing, drivers, strength = demographic_swing(cell["race_group"], cell["sex"], cell["education"], signals, national_k)
        projected = float(np.clip(base + swing / 100, 0.0, 1.0))
        weight = cell["population"] / total
        for driver in drivers:
            key = "group_news" if driver["issue"].startswith("group_news:") else driver["issue"]
            weighted_drivers[key] = weighted_drivers.get(key, 0) + weight * driver["contribution_pp"]
        groups.append({**cell, "baseline_dem_share": float(base), "survey_baseline_dem_share": float(survey),
                       "projected_dem_share": projected,
                       "projected_rep_share": 1 - projected, "swing_pp": 100 * (projected - base),
                       "weight": weight, "turnout": 1.0, "model_status": "regularized_estimate_plus_assumed_news_response",
                       "baseline_geography": race["geography_id"], "race_scheme": "modeled_hispanic_exclusive",
                       "news_evidence_strength": strength, "drivers": drivers})
    base_share = sum(g["weight"] * g["baseline_dem_share"] for g in groups)
    share = sum(g["weight"] * g["projected_dem_share"] for g in groups)
    margin = 100 * (2 * share - 1)
    baseline_margin = 100 * (2 * base_share - 1)
    probability = win_probability(margin, race["office"])
    result.update(groups=groups, baseline_dem_share=base_share, dem_share=share, rep_share=1 - share,
                  swing_pp=100 * (share - base_share), margin_pp=margin, baseline_margin_pp=baseline_margin,
                  news_margin_shift_pp=margin - baseline_margin,
                  win_probability_d=probability, win_probability_r=1 - probability,
                  win_probability_d_without_news=win_probability(baseline_margin, race["office"]),
                  sigma_margin_pp=_sigma(race["office"]), model_version=FORECAST_VERSION,
                  winner="D" if share > .5 else "R" if share < .5 else "Tie", rating=_rating(margin, probability),
                  drivers=[{"issue": issue, "contribution_pp": value} for issue, value in sorted(weighted_drivers.items(), key=lambda item: -abs(item[1]))],
                  news_status="active" if (signals.get("attributed_article_count", 0) or 0) > 0 else "no_attributed_news_baseline_only",
                  message="Cohort baselines anchored to the actual 2024 presidential result, shifted by news-derived demographic changes, then population-weighted. Win probability = Phi(margin / sigma) with sigma from historical race deviations and national-environment uncertainty.")
    return result


def simulate_chambers(races, simulations=None, seed=None):
    """Monte Carlo seat totals with one shared national error per simulation.

    Senate totals add the 65 holdover seats so they sum to 100; control at 50
    seats goes to the Vice President's party.
    """
    from .senate import holdover_counts, control
    simulations = simulations or UNCERTAINTY["simulations"]
    rng = np.random.default_rng(UNCERTAINTY["seed"] if seed is None else seed)
    national = rng.normal(0, UNCERTAINTY["national_sd_margin_pp"], simulations)
    summary = {}
    for office in ("house", "senate", "governor"):
        rows = [r for r in races if r["office"] == office and r.get("margin_pp") is not None]
        if not rows:
            continue
        margins = np.asarray([r["margin_pp"] for r in rows])
        race_sd = UNCERTAINTY["race_sd_margin_pp"][office]
        draws = margins[None, :] + national[:, None] + rng.normal(0, race_sd, (simulations, len(rows)))
        dem = (draws > 0).sum(axis=1)
        item = {"contested": len(rows), "simulations": simulations,
                "expected_dem_wins": float(dem.mean()), "median_dem_wins": float(np.median(dem)),
                "dem_wins_p10": float(np.percentile(dem, 10)), "dem_wins_p90": float(np.percentile(dem, 90))}
        if office == "senate":
            hold = holdover_counts()
            totals = dem + hold["D"]
            item.update(holdovers=hold, expected_dem_seats=float(totals.mean()),
                        expected_rep_seats=float(100 - totals.mean()),
                        p_dem_control=float(np.mean([control(int(t)) == "D" for t in totals])),
                        seats_for_control={"D": 51, "R": 50}, tiebreak="Vice President (R) breaks a 50-50 tie")
            item["p_rep_control"] = 1 - item["p_dem_control"]
        elif office == "house":
            item.update(p_dem_control=float(np.mean(dem >= 218)), seats_for_control={"D": 218, "R": 218})
            item["p_rep_control"] = 1 - item["p_dem_control"]
        histogram = np.bincount(dem if office != "senate" else totals)
        item["distribution"] = {str(i): float(c / simulations) for i, c in enumerate(histogram) if c}
        summary[office] = item
    return summary


def predict_all(conn, *, as_of="2026-10-21", cache_dir=ROOT / "data" / "cache", artifact_path=DEFAULT_ARTIFACT,
                model=None):
    model = model if model is not None else load_model(conn, cache_dir, artifact_path)
    national_k = national_ratio_scale(conn)
    races = [predict_race(conn, race, as_of=as_of, model=model, national_k=national_k) for race in conn.execute("SELECT * FROM races WHERE cycle=2026 ORDER BY office,state,district,race_id").fetchall()]
    counts = {}
    for race in races:
        # One shared validation object is retained at run level. Single-race
        # calls still return validation without needing a separate metadata read.
        race.pop("validation", None)
        office = counts.setdefault(race["office"], {"D": 0, "R": 0, "Tie": 0, "unavailable": 0})
        office[race["winner"] or "unavailable"] += 1
    anchored = [r for r in races if r.get("anchor", {}).get("actual_dem_share") is not None]
    anchor_error = {office: float(np.sqrt(np.mean([(200 * (r["anchor"]["survey_only_dem_share"] - r["anchor"]["actual_dem_share"])) ** 2
                                                    for r in anchored if r["office"] == office])))
                    for office in ("house", "senate", "governor") if any(r["office"] == office for r in anchored)}
    return {"as_of": as_of, "status": "experimental_forecast", "races": races, "counts": counts, "validation": model["validation"],
            "model": {**{k: model[k] for k in ("model_version", "trained_at", "baseline_year", "training", "news_response", "artifact_path")},
                      "forecast_version": FORECAST_VERSION, "status": "experimental_forecast",
                      "news_response": {**NEWS_RESPONSE, "national_ratio_normalizer": national_k},
                      "uncertainty": UNCERTAINTY, "chambers": simulate_chambers(races),
                      "anchoring": {"races_anchored": len(anchored), "races_total": len(races),
                                    "survey_only_rmse_margin_pp_vs_actual_2024": anchor_error,
                                    "method": "Per race, one logit offset added to every cohort so the population-weighted 2024 baseline equals the actual 2024 presidential two-party result (2026 district lines where redrawn)."}},
            "limitations": LIMITATIONS}
