# Model and code

This package builds the database, fits the model, scores news and serves the dashboard. Everything runs through `main.py`; use `python main.py COMMAND --help` for options.

## Code layout

| Module | Role |
|---|---|
| `db.py` | SQLite schema and connection |
| `races.py`, `senate.py` | 2026 race roster, Senate holdovers and chamber control |
| `census.py` | ACS population downloads |
| `surveys.py` | CES survey downloads and aggregation |
| `predict.py` | Baseline model fitting, cohort shifts, race forecasts, chamber simulation |
| `anchors.py` | Actual 2024 presidential results used to anchor baselines |
| `elasticity.py` | How much each demographic group swings relative to the nation |
| `news.py` | RSS collection, article scoring and news signals |
| `forecasts.py` | Saving forecasts and reading them back for the dashboard and exports |
| `analysis.py` | Historical comparisons and the separate manual `scenario` tool |
| `importers.py` | CSV imports (see `templates/`) |
| `server.py` | Local HTTP server and JSON API for `web/` |

## Demographic baseline

1. **Population cohorts** (`population_cells`). Each geography's ACS population age 25+ is split into 32 cohorts: eight race/ethnicity groups (seven non-Hispanic races plus Hispanic) × two sexes × two education levels (*high school or less*, *some college or more*).
2. **2024 vote model** (`train_model`). A weighted logistic regression is fitted to 74,421 CES 2024 office votes from 40,587 respondents. Its features run from broad to specific: intercept, sex, education, race, the full race × sex × education cell, state, House district, and a Senate/governor offset. L2 penalties grow with specificity (8 for sex and education, 15 for race, 60 for the cell, 25 for state, 30 for district, 25 for office), so small cohorts borrow strength from broader groups. The fitted model is cached in `data/model/forecast2026.json`.
3. **Anchoring** (`predict_race`). For each race, one logit offset is added to every cohort so the population-weighted baseline equals the actual 2024 presidential two-party result. The survey sets the differences *between* cohorts, and the anchor sets the overall *level*.
4. **Swing ratios** (`cell_ratio`). Each race × sex × education cell's 2016→2018 House swing divided by the national swing, from the CES cumulative file. The ratios are shrunk toward the race-group ratio and normalized so the national average is 1. Example: white college-educated women moved 1.36× the nation in 2018, and white non-college men about 0.09×. The values are stored in `data/model/demographic_elasticity.json`.

## News signals

1. **Collect.** `refresh`/`fetch-news` pull BBC, NPR Politics and Guardian US Politics RSS. `fetch-group-news` runs Google News searches on coverage of racial groups. `import-news` loads your own scored CSV.
2. **Score each article** (`analyze_content`):
   - **Party tone:** hand-written outcome phrases (e.g. "wins" +1.6, "loses" −1.8, "losing support" −2.0) are credited to the nearest party name within 10 words. A preceding negation flips the sign at 0.74 strength. Each party's total is squashed to (−1, 1). Unattributed outcomes stay empty, not zero.
   - **VADER compound:** general tone of the text, stored for diagnostics.
   - **Topics:** keyword matches for 9 issues; intensity = min(1, terms / 3).
   - **Group features:** relevance to and direction for Hispanic, Black, Asian, white and Native American voters. The direction comes from party tone in sentences about the group. When a policy issue such as raids or voting rights is covered with no party named, VADER tone counts against the governing party at half weight (the "incumbent referendum" rule).
3. **Aggregate** (`news_signals`). Articles from the 30 days before the cutoff are weighted by recency (14-day half-life) and geographic relevance (0.25 if the article names only other states). Articles are averaged within each publisher, then across publishers equally. The outputs are the national D-minus-R `difference`, per-topic signals and salience, and per-group signals.

## Combining them

For each cohort, `demographic_swing` adds three terms, in points of Democratic two-party share:

```text
national = 5 × difference × strength × cell_ratio × national_k        strength = n / (n + 10)
group    = 6 × (group_signal − difference) × m / (m + 8)
topics   = Σ 3 × topic_signal × salience_share × sensitivity × strength
swing    = clip(national + group + topics, −12, +12)
```

- `n` is the number of articles with a party target, and `m` is the number with a direction for that cohort's group.
- `sensitivity` combines the race, sex and education priors in `TOPIC_SENSITIVITY`, clipped to 0.6–1.5. For example, abortion is 1.3× for women and immigration is 1.3× for Hispanic voters.
- The race result is the population-weighted sum of `baseline + swing`.
- Win probability is `Φ(margin / σ)`, with `σ = √(5² + race_sd²)` and `race_sd` = 7 (House, Senate) or 9 (governor).
- `simulate_chambers` draws 20,000 simulations with a shared national error to get seat totals and control probabilities. Senate totals add the 65 holdovers, and the Vice President breaks a 50–50 tie.

The 5, 6 and 3 point scales, the evidence half-points and the topic sensitivities are set by hand, not fitted to past elections. Each forecast records this as `news_coefficient_status`.

The `scenario` command and `/api/scenario` are a separate manual tool: they apply one uniform shift around the 2024 baselines and are not part of `predict`.

## API

Read-only endpoints served by `python main.py serve`:

- `/api/summary`, `/api/races`, `/api/groups`
- `/api/predictions?office=house` (also `senate`, `governor`)
- `/api/race?id=2026-house-PA-07`
- `/api/scenario?id=2026-house-PA-07&coefficient_pp=2&assumed_shift_pp=0`
- `/api/export/forecasts`, `/api/export/history?id=2026-house-PA-07`

If port 8000 is busy, stop the other server or pass `--port 8001`.

## Tests

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py electiondb
node --check web/app.js
```

The tests cover:

- News scoring, negation, rescoring and publisher balancing.
- Census ethnicity conservation and education collapsing.
- Logistic estimation, demographic shifts and exact aggregation.
- Win probabilities, persistence and import validation.

They run against temporary or in-memory databases.
