# Experimental 2026 election model

The application produces a Democratic and Republican two-party vote share, point-estimate winner, and seat classification for each loaded race. It also shows the fitted 2024 baseline, assumed news-driven change, and population weight for every demographic combination. It does not estimate a calibrated probability that a candidate wins.

## Demographic definitions

Education has two categories:

- **High school or less:** less than high school plus high school/GED; CES education codes 1–2.
- **Some college or more:** some college, associate degree, bachelor's degree, or graduate degree; CES codes 3–6.

Counts and survey observations are combined before vote shares are estimated. Suppressed historical cell percentages are never averaged to manufacture a larger cell.

The population universe is **residents age 25 and over, regardless of citizenship**. Census male/female sex categories describe the available published data, not gender identity. Eight modeled race/ethnicity groups × two sexes × two education levels produce 32 cells per geography.

### Census/CES ethnicity bridge

ACS tables C15002A–G report mutually exclusive races including Hispanic people. The CES `race_h` variable instead reports Hispanic-exclusive categories. Directly matching the two would count Hispanic voters under incompatible definitions.

The model uses C15002H for observed White non-Hispanic counts and C15002I for observed Hispanic counts. Within each sex × education cell:

1. White Hispanic = White of any ethnicity (A) − White non-Hispanic (H).
2. Nonwhite Hispanic = Hispanic (I) − White Hispanic.
3. Allocate nonwhite Hispanic population proportionally among the six nonwhite race categories B–G, then subtract those allocations to estimate non-Hispanic populations.
4. Combine the resulting seven non-Hispanic race groups with Hispanic of any race.

This construction conserves the sum of the original A–G population counts. White non-Hispanic and Hispanic counts are published observations; the six remaining ethnicity intersections are **modeled allocations**, and each row records `population_estimated`. They are not published Census intersections. Minor incompatible estimates are constrained to nonnegative, population-conserving totals and reported in `ethnicity_bridge_adjustments`.

CES v10 does not identify Native Hawaiian/Pacific Islander separately in `race_h`; its population cells use the fitted CES Other-race preference. CES Middle Eastern observations are pooled into White to bridge to the legacy ACS race scheme. These approximations are recorded limitations, not a claim that those populations have identical political preferences.

## Fitted 2024 baseline

The exact source is the cached [CES cumulative v10 subset](https://doi.org/10.7910/DVN/II2DB6), downloaded through the [Berkeley SDA mirror](https://sda.berkeley.edu/sdaweb/analysis/?dataset=ces-cumulative-2024-v10). The file includes 60,000 respondents for 2024. Training uses respondents age 25+, known sex/race/education, finite positive survey weights, and a self-reported Democratic or Republican House, Senate, or governor vote.

The cached v10 2024 records have no post-election weights. The model therefore uses the available normal survey `weight`, with a corresponding attrition/validation limitation. It does not invent post-election weights or treat self-reports as validated votes.

For each usable office vote, the fitted probability is:

`p(D) = logistic(intercept + race + sex + education + race×sex×education cell effect + state effect + House district effect + office effect)`.

The weighted binomial log likelihood is fit with L2 penalties. National race, sex, and education terms share information across cells. Additional cell, state, and district effects shrink toward those broader terms. This produces a prediction for sparse cells without presenting small direct survey samples as reliable local observations. L2 penalty values are fixed modeling choices, saved alongside all coefficients in `data/model/forecast2026.json`; they have not been tuned against 2026 outcomes.

House district effects are learned from respondents reporting House votes on the historical 119th-Congress district identifiers. Senate and governor predictions share state and national demographic preferences and add a fitted national 2024 office offset. A state without that office's 2024 election therefore still receives a prediction. The model does not transfer an individual 2024 Senate/governor candidate's personal vote to a different 2026 candidate.

## News content and tone adjustment

News scoring returns signed Democratic-versus-Republican favorability and issue signals for economy, immigration, abortion, healthcare, democracy, crime, foreign policy, education, and climate. Publisher weighting, publication-date cutoff, recency, geographic relevance, target attribution, and per-article diagnostics are handled by `electiondb/news.py`.

The current feed snapshot alone cannot identify a causal or predictive relationship between news and later election outcomes. There are no historical, matched news snapshots and demographic vote-change labels with which to train that relationship. **The 2024 baseline is fitted; the following news response coefficients and demographic issue sensitivities are explicit priors.** The application exposes them and identifies the resulting race forecasts as experimental.

For group `g`, define evidence strength `q = n/(n+10)`, where `n` is the number of attributed articles in the applicable news window. Let `F` be signed party favorability, `T[j]` signed favorability for issue `j`, and `s[j]` its salience:

`swing[g] in percentage points = clip(q × (2F + 3 × sum(T[j] × normalized_salience[j] × sensitivity[g,j])), -6, +6)`.

The projected share is `clip(baseline[g] + swing[g]/100, 0, 1)`. Sparse news is attenuated; absent attributed news preserves the 2024 baseline exactly. Neutral evidence can correctly produce zero change. Positive and negative signals can also cancel.

Assumed issue sensitivities are bounded between 0.6 and 1.5. The specific priors are stored in the artifact and returned by the model metadata. Examples include higher economy sensitivity for high-school-or-less cells and higher abortion sensitivity for female cells. These are assumptions for exploring heterogeneous news response; they are not estimated demographic attitudes or measured group exposure. Each demographic row shows every issue's signal, prior sensitivity, and contribution in percentage points.

## Population aggregation and seat display

For each race, the model predicts all 32 demographic cells using its district or state geography. The race share is exactly:

`D share = sum(population[g] × projected_D_share[g]) / sum(population[g])`.

An identical calculation yields the population-standardized 2024 baseline. Republican share is `1 − D share`; displayed margin is `100 × (D share − R share)`. Winner indicates which party has the larger point estimate. Classifications use absolute two-party margin: tossup below 2 points, lean from 2 to below 5, likely from 5 to below 10, safe at 10 or above. The classification is not an estimate of uncertainty or win probability.

Equal relative turnout is assumed across cells. The `turnout: 1.0` row value is the common multiplier, not a measured 100% turnout estimate. The modeled electorate omits voters age 18–24 and includes noncitizens. Consequently these totals are **population-standardized proxies**, not validated likely-voter forecasts. A future version should use citizenship/age-complete electorate counts and independently estimated turnout by group.

House projections use ACS 119th-Congress population boundaries and historical CES district identifiers. The dashboard identifies them as unreconciled proxies for 2026 seats. Statewide Senate/geographic boundaries do not have the same congressional redistricting mismatch. Candidates, incumbency, spending, independents, and third-party vote share are not modeled. Senate chamber totals must distinguish the 35 modeled contests from the 65 seats not being elected.

## Validation and reproducibility

The model deterministically assigns **whole respondents** to an 80% training set or 20% holdout before stacking their office votes. Votes for different offices from one person never appear on opposite sides of the split. It reports weighted Brier score, weighted log loss, predicted and observed Democratic shares, calibration bins, an intercept-only Brier comparison, and vote-record counts overall and by office. It then refits all available 2024 observations for the production baseline.

This measures prediction of held-out responses from the **same 2024 survey**. It does not establish 2026 race accuracy, historical election accuracy, the precision of local small samples, or the correctness of news-to-vote priors. There is no calibrated uncertainty interval or win probability. A future evaluation must freeze historical data cutoffs, fit on earlier elections, predict entirely held-out election years, reconcile geography, and compare race results and demographic swings against independent evidence.

The model artifact saves source SHA-256, training metadata, feature names, coefficients, penalties, convergence diagnostics, validation results, and news priors. Loading checks the cached CSV checksum before reuse. Predictions retain their as-of date, model version, population coverage, news signals, and group contributions. Raw respondent records are not served by the dashboard.

## Version 2: anchoring, win probability, and Senate holdovers

**Anchoring to actual 2024 results.** The survey-only baseline missed actual 2024 results by an RMSE of about 15 margin points per House district (8.6 Senate states, 12 governor states). Each race now adds one common logit offset `δ` to every cohort so that `Σ w_g · logistic(η_g + δ) = actual 2024 presidential two-party D share`. Cohort differences from the survey model are preserved; the race total matches reality. House seats use The Downballot's 2024 presidential results on 2026 district lines (AL, CA, FL, LA, NC, OH, TN, TX, UT were redrawn; 135 seats). Statewide races use MEDSL state results. Run `python main.py fetch-anchors`.

**News → margin.** The 2026 cohort share is `anchored_baseline_g + swing_g/100`, using the news `swing_g` formula above. The race share is the population-weighted sum. `news_margin_shift_pp = projected margin − 2024 baseline margin`.

**Win probability.** `P(D) = Φ(margin / σ)`, `σ = sqrt(σ_national² + σ_race²)`. σ_race = 7 pts (House, Senate) and 9 pts (governor). The 7 is the robust spread of Senate-minus-presidential margins in MEDSL 2010–2024 data (8.0 all states; 6.1 in states within 20 points). The governor value is an assumption. σ_national = 5 pts is the assumed error in the news-implied national environment. Tiers: Toss-up <55% for the favorite, Tilt 55–65, Lean 65–85, Likely 85–95, Safe ≥95. `D win % without news` repeats the calculation on the 2024 baseline margin.

**Chamber control.** 20,000 simulations draw one shared national error plus independent race errors. Senate totals add the 65 seats not up in 2026 (34 Democratic caucus incl. King and Sanders, 31 Republican; `electiondb/senate.py`), so they sum to 100. The Democrats need 51; at 50–50 the Vice President (R) breaks ties. `python main.py forecast-table` writes `data/forecast_table_2026.csv`.

## Version 2.1: racial-group news response

Each cohort's 2026 change (percentage points of D two-party share) is:

`national_pp × k × ratio[cell]  +  6 × clip(group_signal − national_signal, −1, 1) × n_g/(n_g+8)`, capped at ±12.

- `national_pp = 5 × national_signal × n/(n+10)`: the news-implied national swing. 5 pts is about the mean absolute midterm House swing 2010–2022.
- `ratio[cell]`: how much that race × sex × education cell moved relative to the nation in the 2016→2018 midterm (CES cumulative, empirical-Bayes shrinkage; `data/model/demographic_elasticity.json`, built by `tools/ces_elasticity_analysis.py` + `python main.py build-elasticity`). Example: white college women 1.36, white non-college men 0.09, Black non-college men 1.56.
- `k` normalizes the ratios with national population weights, so the nation moves by `national_pp` while districts with high-response groups move more.
- `group_signal`: publisher-balanced party direction of coverage about that racial group (explicit mentions, full weight) or its policy issues (immigration enforcement/ICE/deportations → Hispanic; voting rights/policing/DEI → Black; anti-Asian/H-1B → Asian; tribal → Native American; 0.25 per term, max 0.5). Direction comes from party-outcome cues in sentences about the group, including voter-movement phrases ("swing away from", "sour on", "gains slip", "shift toward"). Policy coverage with no party cue uses VADER tone under an incumbent-party rule (negative coverage counts against the governing Republicans) at half weight. A bare group mention with no party cue is neutral. 6 pts ≈ two-thirds of the largest group-specific midterm deviation in CES (Hispanic voters, 2020→2022).
- Group coverage comes from Google News searches (`python main.py fetch-group-news`; also part of `refresh`), balanced by original outlet.
