"""Prototype: data-driven demographic swing elasticities from CES cumulative 2006-2024.

Usage:
    python -I ces_elasticity_analysis.py <ces_csv> <research_dir> <out_dir>

<research_dir> must contain wiki/house_<year>.json (Wikipedia infobox wikitext) and
turnout_src/Turnout_1980_2022_v1.2.csv (UF Election Lab VEP turnout).

Outputs (CSV) in <out_dir>:
  cell_year_shares.csv       House two-party D share by year x group (several group levels)
  national_verification.csv  CES national House D share vs actual House popular vote
  elasticity.csv             Per-group elasticity vs national, several specifications, EB-pooled toward 1
  midterm_shifts.csv         Per-group shift for 2008->10, 2012->14, 2016->18, 2020->22 and ratio to national
  midterm_ratio_summary.csv  Pooled (all 4 midterms) and R-president (2018) ratios per group
  turnout.csv                Validated turnout (vv_turnout_gvm==1) by year x group, citizens 25+
  turnout_dropoff.csv        Midterm/presidential turnout ratios by group, relative to national
  recall_defection.csv       Midterm House D share by recalled prior presidential vote, by group
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

T0 = time.time()
YEARS = list(range(2006, 2025, 2))
POST_WEIGHT_YEARS = {2012, 2016, 2018, 2020, 2022}  # per cumulative guide, weight_post availability
MIDTERMS = [(2008, 2010, "D"), (2012, 2014, "D"), (2016, 2018, "R"), (2020, 2022, "D")]
PRES_YEARS = {2008, 2012, 2016, 2020, 2024}

# race_h codes: 1 White 2 Black 3 Hispanic 4 Asian 5 Native American 6 Mixed 7 Other 8 Middle Eastern
RACE5 = {1: "white", 2: "black", 3: "hispanic", 4: "asian", 5: "other", 6: "other", 7: "other", 8: "other"}
EDUC2 = {1: "high_school_or_less", 2: "high_school_or_less", 3: "some_college_or_more",
         4: "some_college_or_more", 5: "some_college_or_more", 6: "some_college_or_more"}
SEX = {1: "male", 2: "female"}

LEVELS = {
    "national": [],
    "race5": ["race5"],
    "educ2": ["educ2"],
    "sex": ["sex"],
    "educ_sex": ["educ2", "sex"],
    "race_educ": ["race5", "educ2"],
    "white_nonwhite_educ_sex": ["wnw", "educ2", "sex"],
    "cell20": ["race5", "sex", "educ2"],
}


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def load(path: Path) -> pd.DataFrame:
    f = pd.read_csv(path, skipinitialspace=True, na_values=["."])
    f = f[f["year"].isin(YEARS)].copy()
    f["race5"] = f["race_h"].map(RACE5)
    f["educ2"] = f["educ"].map(EDUC2)
    f["sex"] = f["gender"].map(SEX)
    f["wnw"] = np.where(f["race5"].eq("white"), "white", "nonwhite")
    f["national"] = "all"
    # primary weight for post-wave items: weight_post where the year has it, else weight
    f["w_post"] = np.where(f["year"].isin(POST_WEIGHT_YEARS), f["weight_post"], f["weight"])
    f["w_pre"] = f["weight"]
    f["dem_house"] = np.where(f["voted_rep_party"].eq(1), 1.0, np.where(f["voted_rep_party"].eq(2), 0.0, np.nan))
    f["dem_house_intent"] = np.where(f["intent_rep_party"].eq(1), 1.0,
                                     np.where(f["intent_rep_party"].eq(2), 0.0, np.nan))
    f["vv_voted"] = np.where(f["vv_turnout_gvm"].isin([1, 2, 3]), f["vv_turnout_gvm"].eq(1).astype(float), np.nan)
    f["pres_recall"] = f["voted_pres_party"].map({1: "D", 2: "R"})
    return f


def wmean_table(df: pd.DataFrame, value: str, weight: str, keys: list[str]) -> pd.DataFrame:
    d = df.dropna(subset=[value, weight] + keys)
    d = d[d[weight] > 0]
    d = d.assign(_wy=d[weight] * d[value], _w2=d[weight] ** 2)
    g = d.groupby(["year"] + keys, observed=True)
    out = g.agg(n=(value, "size"), wsum=(weight, "sum"), wy=("_wy", "sum"), w2=("_w2", "sum")).reset_index()
    out["share"] = out["wy"] / out["wsum"]
    out["n_eff"] = out["wsum"] ** 2 / out["w2"]
    out["se"] = np.sqrt(out["share"] * (1 - out["share"]) / out["n_eff"])
    return out.drop(columns=["wy", "w2"])


def group_id(row, keys):
    return "all" if not keys else "|".join(str(row[k]) for k in keys)


def shares_all_levels(df, value, weight, label):
    frames = []
    for level, keys in LEVELS.items():
        k = keys if keys else ["national"]
        t = wmean_table(df, value, weight, k)
        t["level"] = level
        t["group"] = t.apply(lambda r: group_id(r, keys), axis=1)
        for col in ("race5", "sex", "educ2"):
            if col not in t:
                t[col] = "all"
        t["spec"] = label
        frames.append(t[["spec", "level", "group", "year", "race5", "sex", "educ2", "n", "n_eff", "wsum", "share", "se"]])
    out = pd.concat(frames, ignore_index=True)
    # voter-composition share: group weight / national weight that year (within level)
    nat = out[out.level == "national"].set_index("year")["wsum"]
    out["composition"] = out["wsum"] / out["year"].map(nat)
    out["logit"] = logit(out["share"])
    out["logit_se"] = out["se"] / (out["share"] * (1 - out["share"]))
    return out


def actual_house_vote(research_dir: Path) -> pd.DataFrame:
    rows = []
    for y in YEARS:
        w = json.loads((research_dir / "wiki" / f"house_{y}.json").read_text(encoding="utf-8"))["parse"]["wikitext"]["*"]
        vals = {}
        for k in ("party1", "party2", "popular_vote1", "popular_vote2"):
            m = re.search(r"\|\s*" + k + r"\s*=\s*([^\n|]*)", w)
            vals[k] = m.group(1) if m else ""
        votes = {}
        for i in ("1", "2"):
            party = "D" if "Democratic" in vals["party" + i] else "R" if "Republican" in vals["party" + i] else None
            num = re.search(r"[\d,]{6,}", vals["popular_vote" + i])
            votes[party] = int(num.group(0).replace(",", ""))
        rows.append({"year": y, "actual_d_votes": votes["D"], "actual_r_votes": votes["R"],
                     "actual_d2p": votes["D"] / (votes["D"] + votes["R"])})
    return pd.DataFrame(rows)


def actual_vep_turnout(research_dir: Path) -> pd.Series:
    t = pd.read_csv(research_dir / "turnout_src" / "Turnout_1980_2022_v1.2.csv", dtype=str)
    t = t[t["STATE"].eq("United States")].drop_duplicates("YEAR")
    s = t.set_index(t["YEAR"].astype(int))["VEP_TURNOUT_RATE"].str.rstrip("%").astype(float) / 100
    return s


# ---------------------------------------------------------------- elasticity
def ols(x, y):
    """y = a + b x ; returns b, se_b, a, resid sd, df."""
    X = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    df = len(y) - 2
    s2 = resid @ resid / df if df > 0 else np.nan
    cov = s2 * np.linalg.inv(X.T @ X)
    return beta[1], float(np.sqrt(cov[1, 1])), beta[0], float(np.sqrt(s2)), df


def ols_trend(x, t, y):
    """y = a + g t + b x ; returns b, se_b."""
    X = np.column_stack([np.ones_like(x), t, x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    df = len(y) - 3
    s2 = resid @ resid / df
    cov = s2 * np.linalg.inv(X.T @ X)
    return beta[2], float(np.sqrt(cov[2, 2])), beta[1], df


def eb_pool(b, se, target=1.0):
    """Normal-normal empirical Bayes shrinkage toward a known `target` (scalar or array).

    tau^2 uses the DerSimonian-Laird moment estimator for a KNOWN centre:
      Q = sum w (b - target)^2, w = 1/se^2 ; E[Q] = k + tau^2 * sum w
      tau^2 = max(0, (Q - k) / sum w)
    Returns posterior mean, posterior sd, tau^2, Q, k.  When tau^2 == 0 the data are
    consistent with every group equal to the target (full pooling).
    """
    b = np.asarray(b, float)
    se = np.asarray(se, float)
    target = np.broadcast_to(np.asarray(target, float), b.shape)
    ok = np.isfinite(b) & np.isfinite(se) & (se > 0)
    if ok.sum() < 2:
        return np.where(ok, b, np.nan), np.where(ok, se, np.nan), np.nan, np.nan, int(ok.sum())
    w = 1 / se[ok] ** 2
    Q = float(np.sum(w * (b[ok] - target[ok]) ** 2))
    k = int(ok.sum())
    tau2 = max(0.0, (Q - k) / float(np.sum(w)))
    if tau2 == 0:
        return target.copy(), np.zeros_like(b), tau2, Q, k
    prec = 1 / se ** 2 + 1 / tau2
    post = (b / se ** 2 + target / tau2) / prec
    return post, np.sqrt(1 / prec), tau2, Q, k


def q_pvalue(Q, k):
    from scipy.stats import chi2
    return float(chi2.sf(Q, k)) if np.isfinite(Q) else np.nan


def elasticities(shares: pd.DataFrame, actual: pd.DataFrame) -> pd.DataFrame:
    s = shares[shares.spec == "primary"].copy()
    nat = s[s.level == "national"].set_index("year")
    nat_p = nat["share"]
    act_p = actual.set_index("year")["actual_d2p"]
    rows = []
    for (level, group), g in s[s.level != "national"].groupby(["level", "group"]):
        g = g.set_index("year").reindex(YEARS)
        if g["share"].isna().any() or (g["n"] < 30).any():
            continue
        p = g["share"].to_numpy()
        P = nat_p.reindex(YEARS).to_numpy()
        A = act_p.reindex(YEARS).to_numpy()
        t = (np.array(YEARS) - 2016) / 2.0
        r = {"level": level, "group": group, "race5": g["race5"].iloc[0], "sex": g["sex"].iloc[0],
             "educ2": g["educ2"].iloc[0], "min_n": int(g["n"].min()), "mean_n": float(g["n"].mean()),
             "share_2024": float(p[-1]), "composition_2024": float(g["composition"].iloc[-1])}
        # (1) linear first differences with intercept (cell-specific drift): dp_c = g + b dP
        r["lin_fd_b"], r["lin_fd_se"], r["lin_fd_drift_pp"], r["lin_fd_resid_pp"], r["lin_fd_df"] = ols(np.diff(P), np.diff(p))
        r["lin_fd_drift_pp"] *= 100
        r["lin_fd_resid_pp"] *= 100
        # (2) logit first differences with drift
        r["logit_fd_b"], r["logit_fd_se"], _, r["logit_fd_resid"], _ = ols(np.diff(logit(P)), np.diff(logit(p)))
        # (3) logit levels with cell trend
        r["logit_lvl_trend_b"], r["logit_lvl_trend_se"], _, _ = ols_trend(logit(P), t, logit(p))
        # (4) logit levels, no trend (naive)
        r["logit_lvl_b"], r["logit_lvl_se"], *_ = ols(logit(P), logit(p))
        # (5) linear first differences using ACTUAL national House two-party vote as regressor
        r["lin_fd_actual_b"], r["lin_fd_actual_se"], *_ = ols(np.diff(A), np.diff(p))
        # (6) linear FD, 2012-2024 only (post-realignment-onset window, 6 transitions)
        m = np.array(YEARS) >= 2012
        r["lin_fd_2012on_b"], r["lin_fd_2012on_se"], *_ = ols(np.diff(P[m]), np.diff(p[m]))
        # (7) linear FD, 2008-2024 (drops 2006, whose nonwhite cells look anomalous)
        m = np.array(YEARS) >= 2008
        r["lin_fd_2008on_b"], r["lin_fd_2008on_se"], *_ = ols(np.diff(P[m]), np.diff(p[m]))
        rows.append(r)
    e = pd.DataFrame(rows)
    for spec in ("lin_fd", "logit_fd", "logit_lvl_trend", "lin_fd_actual", "lin_fd_2012on", "lin_fd_2008on"):
        for level in e.level.unique():
            m = e.level.eq(level)
            post, post_se, tau2, Q, k = eb_pool(e.loc[m, spec + "_b"], e.loc[m, spec + "_se"])
            e.loc[m, spec + "_pooled"] = post
            e.loc[m, spec + "_pooled_se"] = post_se
            e.loc[m, spec + "_tau"] = np.sqrt(tau2) if np.isfinite(tau2) else np.nan
            e.loc[m, spec + "_Q_p_vs_uniform"] = q_pvalue(Q, k)
    return e


# ---------------------------------------------------------------- midterm shifts
def midterm_shifts(shares: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = shares[shares.spec == "primary"].set_index(["level", "group", "year"])
    nat = shares[(shares.spec == "primary") & (shares.level == "national")].set_index("year")
    rows = []
    for (level, group), _ in shares[(shares.spec == "primary")].groupby(["level", "group"]):
        for y0, y1, pres in MIDTERMS:
            try:
                a, b = s.loc[(level, group, y0)], s.loc[(level, group, y1)]
            except KeyError:
                continue
            S = nat.loc[y1, "share"] - nat.loc[y0, "share"]
            S_var = nat.loc[y1, "se"] ** 2 + nat.loc[y0, "se"] ** 2
            d = b["share"] - a["share"]
            d_var = a["se"] ** 2 + b["se"] ** 2
            pi = 0.5 * (a["composition"] + b["composition"])
            ratio = d / S
            # delta method incl. cov(cell, national) ~= composition * var(cell)
            r_var = (d_var + ratio ** 2 * S_var - 2 * ratio * pi * d_var) / S ** 2
            Ld = logit(b["share"]) - logit(a["share"])
            LS = logit(nat.loc[y1, "share"]) - logit(nat.loc[y0, "share"])
            rows.append({
                "level": level, "group": group, "race5": a["race5"], "sex": a["sex"], "educ2": a["educ2"],
                "transition": f"{y0}->{y1}", "president_party": pres,
                "n_pres": int(a["n"]), "n_mid": int(b["n"]),
                "share_pres": a["share"], "share_mid": b["share"],
                "shift_pp": 100 * d, "shift_se_pp": 100 * np.sqrt(d_var),
                "national_shift_pp": 100 * S, "ratio_to_national": ratio, "ratio_se": float(np.sqrt(max(r_var, 0))),
                "logit_shift": Ld, "logit_ratio_to_national": Ld / LS,
                # shift toward the out-party (positive = toward the party not holding the White House)
                "out_party_shift_pp": 100 * d * (1 if pres == "R" else -1),
            })
    m = pd.DataFrame(rows)
    summ = []
    for (level, group), g in m.groupby(["level", "group"]):
        if len(g) < 4:
            continue
        S = g["national_shift_pp"].to_numpy()
        d = g["shift_pp"].to_numpy()
        v = g["shift_se_pp"].to_numpy() ** 2
        # pooled slope through origin: d_k = b * S_k + e_k ; WLS with sampling variance
        w = 1 / v
        b = float(np.sum(w * S * d) / np.sum(w * S * S))
        b_se_sampling = float(np.sqrt(1 / np.sum(w * S * S)))
        resid = d - b * S
        # also empirical (residual-based, df=3) SE capturing between-election heterogeneity
        b_ols = float(np.sum(S * d) / np.sum(S * S))
        res_ols = d - b_ols * S
        b_se_emp = float(np.sqrt(np.sum(res_ols ** 2) / 3 / np.sum(S * S)))
        r18 = g[g.transition == "2016->2018"].iloc[0]
        summ.append({
            "level": level, "group": group, "race5": g["race5"].iloc[0], "sex": g["sex"].iloc[0], "educ2": g["educ2"].iloc[0],
            "pooled_ratio_wls": b, "pooled_ratio_se_sampling": b_se_sampling,
            "pooled_ratio_ols": b_ols, "pooled_ratio_se_empirical": b_se_emp,
            "ratio_2018_R_pres": r18["ratio_to_national"], "ratio_2018_se": r18["ratio_se"],
            "shift_2018_pp": r18["shift_pp"], "shift_2018_se_pp": r18["shift_se_pp"],
            "logit_ratio_2018": r18["logit_ratio_to_national"],
            "mean_logit_ratio_4mid": float(g["logit_ratio_to_national"].mean()),
            "ratio_2010": float(g.loc[g.transition == "2008->2010", "ratio_to_national"].iloc[0]),
            "ratio_2014": float(g.loc[g.transition == "2012->2014", "ratio_to_national"].iloc[0]),
            "ratio_2022": float(g.loc[g.transition == "2020->2022", "ratio_to_national"].iloc[0]),
            "min_n": int(min(g["n_pres"].min(), g["n_mid"].min())),
        })
    sm = pd.DataFrame(summ)
    for level in sm.level.unique():
        msk = sm.level.eq(level) & sm.level.ne("national")
        if not msk.any():
            continue
        post, post_se, tau2, Q, k = eb_pool(sm.loc[msk, "ratio_2018_R_pres"], sm.loc[msk, "ratio_2018_se"])
        sm.loc[msk, "ratio_2018_pooled"] = post
        sm.loc[msk, "ratio_2018_pooled_se"] = post_se
        sm.loc[msk, "ratio_2018_tau"] = np.sqrt(tau2) if np.isfinite(tau2) else np.nan
        sm.loc[msk, "ratio_2018_Q_p_vs_uniform"] = q_pvalue(Q, k)
        post, post_se, tau2, Q, k = eb_pool(sm.loc[msk, "pooled_ratio_ols"], sm.loc[msk, "pooled_ratio_se_empirical"])
        sm.loc[msk, "pooled_ratio_eb"] = post
        sm.loc[msk, "pooled_ratio_eb_se"] = post_se
        sm.loc[msk, "pooled_ratio_eb_tau"] = np.sqrt(tau2) if np.isfinite(tau2) else np.nan
        sm.loc[msk, "pooled_ratio_Q_p_vs_uniform"] = q_pvalue(Q, k)
    # Hierarchical recommendation for an R-president midterm (2026 analog = 2016->2018):
    # race5 2018 ratios shrink toward 1; each cell20 / race_educ 2018 ratio shrinks toward its
    # race5 posterior.  Other levels shrink toward 1 (already in ratio_2018_pooled).
    race = sm[sm.level == "race5"].set_index("group")
    sm["recommended_ratio_R_midterm"] = sm["ratio_2018_pooled"]
    sm["recommended_ratio_R_midterm_se"] = sm["ratio_2018_pooled_se"]
    sm["recommended_prior"] = np.where(sm.level.eq("national"), "identity", "1.0")
    for level in ("cell20", "race_educ"):
        msk = sm.level.eq(level)
        prior = sm.loc[msk, "race5"].map(race["ratio_2018_pooled"]).to_numpy()
        post, post_se, tau2, Q, k = eb_pool(sm.loc[msk, "ratio_2018_R_pres"], sm.loc[msk, "ratio_2018_se"], prior)
        sm.loc[msk, "recommended_ratio_R_midterm"] = post
        # posterior sd ignores uncertainty in the race5 prior; add it in quadrature (conservative)
        prior_se = sm.loc[msk, "race5"].map(race["ratio_2018_pooled_se"]).to_numpy()
        se = sm.loc[msk, "ratio_2018_se"].to_numpy()
        # weight on the prior in the posterior mean (1 = full pooling when tau2 == 0)
        shrink = (1 / tau2) / (1 / se ** 2 + 1 / tau2) if tau2 and tau2 > 0 else np.ones_like(se)
        sm.loc[msk, "recommended_ratio_R_midterm_se"] = np.sqrt(post_se ** 2 + (shrink * prior_se) ** 2)
        sm.loc[msk, "recommended_prior"] = "race5_2018_pooled"
        sm.loc[msk, "recommended_tau"] = np.sqrt(tau2)
    return m, sm


# ---------------------------------------------------------------- turnout
def turnout(df: pd.DataFrame, vep: pd.Series):
    d = df[(df.year <= 2022) & df.citizen.eq(1)]
    t = shares_all_levels(d, "vv_voted", "w_pre", "turnout_citizen25")
    t = t.rename(columns={"share": "turnout", "se": "turnout_se", "composition": "pop_share"})
    t = t.drop(columns=["logit", "logit_se"])
    nat = t[t.level == "national"].set_index("year")["turnout"]
    t["turnout_rel_national"] = t["turnout"] / t["year"].map(nat)
    t["actual_vep_turnout_national"] = t["year"].map(vep)
    rows = []
    idx = t.set_index(["level", "group", "year"])
    for (level, group), _ in t.groupby(["level", "group"]):
        for y0, y1, pres in MIDTERMS:
            a, b = idx.loc[(level, group, y0)], idx.loc[(level, group, y1)]
            ratio = b["turnout"] / a["turnout"]
            ratio_se = ratio * np.sqrt((a["turnout_se"] / a["turnout"]) ** 2 + (b["turnout_se"] / b["turnout"]) ** 2)
            nr = nat[y1] / nat[y0]
            rows.append({"level": level, "group": group, "race5": a["race5"], "sex": a["sex"], "educ2": a["educ2"],
                         "transition": f"{y0}->{y1}", "president_party": pres,
                         "n_pres": int(a["n"]), "n_mid": int(b["n"]),
                         "turnout_pres": a["turnout"], "turnout_mid": b["turnout"],
                         "dropoff_ratio": ratio, "dropoff_ratio_se": ratio_se,
                         "dropoff_pp": 100 * (b["turnout"] - a["turnout"]),
                         "dropoff_ratio_rel_national": ratio / nr,
                         "national_ces_ratio": nr, "national_actual_vep_ratio": vep[y1] / vep[y0],
                         # CES validated turnout overstates midterm turnout; rescale so the national
                         # ratio matches the actual VEP ratio and keep only the relative pattern.
                         "dropoff_ratio_vep_rescaled": (ratio / nr) * (vep[y1] / vep[y0])})
    return t, pd.DataFrame(rows)


# ---------------------------------------------------------------- recall defection
def recall_defection(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df.year.isin([2010, 2014, 2018, 2022]) & df.age.ge(25)].dropna(subset=["dem_house", "pres_recall"])
    frames = []
    for level in ("national", "educ_sex", "race5", "cell20"):
        keys = LEVELS[level] if LEVELS[level] else ["national"]
        t = wmean_table(d, "dem_house", "w_post", keys + ["pres_recall"])
        t["level"] = level
        t["group"] = t.apply(lambda r: group_id(r, LEVELS[level]), axis=1)
        frames.append(t[["level", "group", "year", "pres_recall", "n", "n_eff", "share", "se"]])
    out = pd.concat(frames, ignore_index=True)
    out = out.rename(columns={"share": "house_dem_share"})
    # defection rate = share voting for the other party's House candidate
    out["defection_rate"] = np.where(out.pres_recall.eq("D"), 1 - out.house_dem_share, out.house_dem_share)
    return out


# ---------------------------------------------------------------- extra verification / contrasts
EXIT_MAP = {"White": ("race5", "white"), "[[White Americans|White]]": ("race5", "white"),
            "Black": ("race5", "black"), "[[African Americans|Black]]": ("race5", "black"),
            "Latino": ("race5", "hispanic"), "[[Hispanic and Latino Americans|Hispanic]] (of any race)": ("race5", "hispanic"),
            "Asian": ("race5", "asian"), "[[Asian Americans|Asian]]": ("race5", "asian"),
            "Men": ("sex", "male"), "Women": ("sex", "female")}


def exit_polls(research_dir: Path) -> pd.DataFrame:
    """Parse the national House exit-poll table (Edison via Wikipedia) for race and sex rows."""
    rows = []
    for y in (2018, 2022):
        t = json.loads((research_dir / "wiki" / f"house_full_{y}.json").read_text(encoding="utf-8"))["parse"]["wikitext"]["*"]
        i = t.find("== Exit poll ==")
        seg = t[i:t.find("|}", i)]
        header = seg[:600]
        dem_first = header.find("DEM") < header.find("GOP")
        seen = set()
        for r in seg.split("|-"):
            cells = [c.strip() for c in re.split(r"\n\||\|\|", r) if c.strip()]
            cells = [re.sub(r'style="[^"]*"\s*\|?', "", c).strip() for c in cells]
            if len(cells) < 4 or cells[0] not in EXIT_MAP:
                continue
            key = EXIT_MAP[cells[0]]
            if key in seen:
                continue
            seen.add(key)
            a, b = float(cells[1]), float(cells[2])
            d, rr = (a, b) if dem_first else (b, a)
            rows.append({"year": y, "level": key[0], "group": key[1], "exit_dem_pct": d, "exit_rep_pct": rr,
                         "exit_d2p": d / (d + rr), "exit_pct_of_vote": float(cells[3])})
    return pd.DataFrame(rows)


def contrasts(shares: pd.DataFrame) -> pd.DataFrame:
    """Pairwise differences in swing responsiveness with SEs, computed directly on differences."""
    s = shares[shares.spec == "primary"]
    nat = s[s.level == "national"].set_index("year")
    P = nat["share"].reindex(YEARS).to_numpy()
    out = []
    for level in ("educ_sex", "white_nonwhite_educ_sex", "race5", "educ2", "sex"):
        g = s[s.level == level].pivot(index="year", columns="group", values="share").reindex(YEARS)
        se = s[s.level == level].pivot(index="year", columns="group", values="se").reindex(YEARS)
        groups = list(g.columns)
        for i, a in enumerate(groups):
            for b in groups[i + 1:]:
                diff = (g[a] - g[b]).to_numpy()
                if np.isnan(diff).any():
                    continue
                bd, bd_se, *_ = ols(np.diff(P), np.diff(diff))
                d18 = (g.loc[2018, a] - g.loc[2016, a]) - (g.loc[2018, b] - g.loc[2016, b])
                d18_se = np.sqrt(se.loc[2018, a] ** 2 + se.loc[2016, a] ** 2 + se.loc[2018, b] ** 2 + se.loc[2016, b] ** 2)
                S18 = P[YEARS.index(2018)] - P[YEARS.index(2016)]
                Sk, Dk = [], []
                for y0, y1, _ in MIDTERMS:
                    Sk.append(nat.loc[y1, "share"] - nat.loc[y0, "share"])
                    Dk.append((g.loc[y1, a] - g.loc[y0, a]) - (g.loc[y1, b] - g.loc[y0, b]))
                Sk, Dk = np.array(Sk), np.array(Dk)
                bm = float(np.sum(Sk * Dk) / np.sum(Sk * Sk))
                bm_se = float(np.sqrt(np.sum((Dk - bm * Sk) ** 2) / 3 / np.sum(Sk * Sk)))
                out.append({"level": level, "group_a": a, "group_b": b,
                            "fd_elasticity_diff": bd, "fd_elasticity_diff_se": bd_se,
                            "ratio_2018_diff": d18 / S18, "ratio_2018_diff_se": d18_se / abs(S18),
                            "shift_2018_diff_pp": 100 * d18, "shift_2018_diff_se_pp": 100 * d18_se,
                            "pooled_4mid_ratio_diff": bm, "pooled_4mid_ratio_diff_se": bm_se})
    return pd.DataFrame(out)


def main(csv_path: str, research_dir: str, out_dir: str):
    research = Path(research_dir)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = load(Path(csv_path))
    print(f"loaded {len(df):,} rows in {time.time() - T0:.1f}s")
    a25 = df[df.age.ge(25)]
    primary = shares_all_levels(a25, "dem_house", "w_post", "primary")
    alt_pre = shares_all_levels(a25, "dem_house", "w_pre", "weight_pre_all_years")
    validated = shares_all_levels(a25[a25.vv_voted.eq(1)], "dem_house", "w_post", "validated_voters_only")
    shares = pd.concat([primary, alt_pre, validated], ignore_index=True)
    shares.to_csv(out / "cell_year_shares.csv", index=False, float_format="%.6g")

    actual = actual_house_vote(research)
    nat = lambda frame: frame[frame.level == "national"].set_index("year")["share"]
    allages = nat(shares_all_levels(df, "dem_house", "w_post", "x"))
    intent = nat(shares_all_levels(df, "dem_house_intent", "w_pre", "x"))
    ver = actual.set_index("year")
    ver["ces_d2p_age25_primary"] = nat(primary)
    ver["ces_d2p_all_ages_primary"] = allages
    ver["ces_d2p_age25_weight_pre"] = nat(alt_pre)
    ver["ces_d2p_age25_validated_voters"] = nat(validated)
    ver["ces_intent_d2p_all_ages_pre_wave"] = intent
    ver["n_house_voters_age25"] = primary[primary.level == "national"].set_index("year")["n"]
    ver["weight_used"] = ["weight_post" if y in POST_WEIGHT_YEARS else "weight" for y in ver.index]
    ver["bias_pp_all_ages"] = 100 * (ver["ces_d2p_all_ages_primary"] - ver["actual_d2p"])
    ver["actual_margin_pp_D_minus_R_twoparty"] = 100 * (2 * ver["actual_d2p"] - 1)
    ver["ces_margin_pp_all_ages"] = 100 * (2 * ver["ces_d2p_all_ages_primary"] - 1)
    ver.reset_index().to_csv(out / "national_verification.csv", index=False, float_format="%.6g")

    e = elasticities(shares, actual)
    e.to_csv(out / "elasticity.csv", index=False, float_format="%.5g")
    m, sm = midterm_shifts(shares)
    m.to_csv(out / "midterm_shifts.csv", index=False, float_format="%.5g")
    sm.to_csv(out / "midterm_ratio_summary.csv", index=False, float_format="%.5g")
    vep = actual_vep_turnout(research)
    t, drop = turnout(a25, vep)
    t.to_csv(out / "turnout.csv", index=False, float_format="%.5g")
    drop.to_csv(out / "turnout_dropoff.csv", index=False, float_format="%.5g")
    rd = recall_defection(df)
    rd.to_csv(out / "recall_defection.csv", index=False, float_format="%.5g")
    # national shift check (CES all ages vs actual)
    shift_rows = []
    for y0, y1, pres in MIDTERMS + [(2006, 2008, "R"), (2010, 2012, "D"), (2014, 2016, "D"), (2018, 2020, "R"), (2022, 2024, "D")]:
        shift_rows.append({"transition": f"{y0}->{y1}", "president_party_before_election": pres,
                           "is_midterm": y1 not in PRES_YEARS,
                           "actual_shift_pp": 100 * (ver.loc[y1, "actual_d2p"] - ver.loc[y0, "actual_d2p"]),
                           "ces_all_ages_shift_pp": 100 * (ver.loc[y1, "ces_d2p_all_ages_primary"] - ver.loc[y0, "ces_d2p_all_ages_primary"]),
                           "ces_age25_shift_pp": 100 * (ver.loc[y1, "ces_d2p_age25_primary"] - ver.loc[y0, "ces_d2p_age25_primary"])})
    shift_check = pd.DataFrame(shift_rows)
    shift_check.to_csv(out / "national_shift_check.csv", index=False, float_format="%.4g")
    ex = exit_polls(research)
    allages_groups = shares_all_levels(df, "dem_house", "w_post", "all_ages")
    ex = ex.merge(allages_groups[["level", "group", "year", "share", "se", "n"]].rename(
        columns={"share": "ces_d2p_all_ages", "se": "ces_se", "n": "ces_n"}), on=["level", "group", "year"], how="left")
    ex["ces_minus_exit_pp"] = 100 * (ex["ces_d2p_all_ages"] - ex["exit_d2p"])
    ex.to_csv(out / "exit_poll_check.csv", index=False, float_format="%.4g")
    con = contrasts(shares)
    con.to_csv(out / "contrasts.csv", index=False, float_format="%.4g")
    print(f"done in {time.time() - T0:.1f}s")

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 300)
    print("\n== national verification ==")
    print(ver[["actual_d2p", "ces_d2p_all_ages_primary", "ces_d2p_age25_primary", "ces_d2p_age25_weight_pre",
               "ces_d2p_age25_validated_voters", "ces_intent_d2p_all_ages_pre_wave", "bias_pp_all_ages",
               "n_house_voters_age25", "weight_used"]].round(4))
    print(shift_check.round(2).to_string())
    print("\n== exit poll check ==")
    print(ex.round(3).to_string())
    print("\n== elasticity ==")
    cols = ["level", "group", "min_n", "lin_fd_b", "lin_fd_se", "lin_fd_pooled", "lin_fd_pooled_se", "lin_fd_Q_p_vs_uniform",
            "lin_fd_drift_pp", "logit_fd_b", "logit_fd_se", "logit_lvl_trend_b", "lin_fd_actual_b",
            "lin_fd_2008on_b", "lin_fd_2008on_se", "lin_fd_2012on_b", "lin_fd_2012on_se"]
    print(e[cols].round(3).to_string())
    print("\n== midterm ratio summary ==")
    cols = ["level", "group", "min_n", "shift_2018_pp", "shift_2018_se_pp", "ratio_2018_R_pres", "ratio_2018_se",
            "ratio_2018_pooled", "ratio_2018_Q_p_vs_uniform", "recommended_ratio_R_midterm", "recommended_ratio_R_midterm_se",
            "ratio_2010", "ratio_2014", "ratio_2022", "pooled_ratio_ols", "pooled_ratio_se_empirical",
            "pooled_ratio_eb", "pooled_ratio_Q_p_vs_uniform", "logit_ratio_2018", "mean_logit_ratio_4mid"]
    print(sm[sm.level != "national"][cols].round(3).to_string())
    print("\n== contrasts ==")
    print(con.round(3).to_string())
    print("\n== national midterm shifts ==")
    print(m[m.level == "national"][["transition", "share_pres", "share_mid", "shift_pp", "shift_se_pp"]].round(4))
    print("\n== turnout national vs VEP ==")
    print(t[t.level == "national"][["year", "n", "turnout", "turnout_se", "actual_vep_turnout_national"]].round(4))
    print("\n== turnout relative to national ==")
    tt = t[t.year.isin([2016, 2018, 2020, 2022]) & t.level.isin(["educ_sex", "race5", "white_nonwhite_educ_sex"])]
    print(tt.pivot(index=["level", "group"], columns="year", values="turnout_rel_national").round(3))
    print("\n== turnout dropoff ==")
    print(drop[drop.level.isin(["national", "educ_sex", "race5", "white_nonwhite_educ_sex"])][
        ["level", "group", "transition", "turnout_pres", "turnout_mid", "dropoff_ratio", "dropoff_ratio_se",
         "dropoff_ratio_rel_national", "dropoff_ratio_vep_rescaled", "national_actual_vep_ratio"]].round(3).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:4])
