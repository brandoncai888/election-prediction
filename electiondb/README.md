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
| `environment.py` | Midterm history, generic-ballot polls, the national target and turnout weights |
| `news.py` | RSS collection, article scoring and news signals |
| `newseffect.py` | Fits the group news coefficient on 2010–2022 midterms |
| `forecasts.py` | Saving forecasts and reading them back for the dashboard and exports |
| `analysis.py` | Historical comparisons and the separate manual `scenario` tool |
| `importers.py` | CSV imports (see `templates/`) |
| `server.py` | Local HTTP server and JSON API for `web/` |

## Demographic baseline

1. **Population cohorts** (`population_cells`). Each geography's ACS population age 25+ is split into 32 cohorts: eight race/ethnicity groups (seven non-Hispanic races plus Hispanic) × two sexes × two education levels (*high school or less*, *some college or more*).
2. **2024 vote model** (`train_model`). A weighted logistic regression is fitted to 74,421 CES 2024 office votes from 40,587 respondents. Its features run from broad to specific: intercept, sex, education, race, the full race × sex × education cell, state, House district, and a Senate/governor offset. L2 penalties grow with specificity (8 for sex and education, 15 for race, 60 for the cell, 25 for state, 30 for district, 25 for office), so small cohorts borrow strength from broader groups. The fitted model is cached in `data/model/forecast2026.json`.
3. **Turnout weights** (`turnout_weight`). Each cohort counts by population × CES validated turnout for its race, sex and education cell, relative to the national rate. The 2024 electorate uses 2020 turnout, since CES v10 has no validated 2024 vote yet. The 2026 electorate uses the 2018/2022 average. Values are frozen in `data/model/midterm_inputs.json` by `build-midterm-inputs`.
4. **Anchoring** (`race_inputs`). For each race, one logit offset is added to every cohort so the 2024-turnout-weighted baseline equals the actual 2024 presidential two-party result. The survey sets the differences *between* cohorts, and the anchor sets the overall *level*.
5. **Swing ratios** (`cell_ratio`). Each race × sex × education cell's 2016→2018 House swing divided by the national swing, from the CES cumulative file. The ratios are shrunk toward the race-group ratio and normalized so the national average is 1. Example: white college-educated women moved 1.36× the nation in 2018, and white non-college men about 0.09×. The values are stored in `data/model/demographic_elasticity.json`.

## National environment

`national_environment` sets the 2026 national House two-party target:

```text
prior  = 2024 House D share (48.7%) + mean out-party midterm gain (5.1 pts: 2010 9.0, 2014 3.5, 2018 4.9, 2022 3.0)
polls  = mean two-party D share of the latest generic-ballot aggregator averages on or before the cutoff
target = 0.5 × prior + 0.5 × polls          (prior alone if no polls are loaded)
```

`fetch-polls` reads the aggregator table (Decision Desk HQ, RealClearPolitics, Silver Bulletin…) from Wikipedia's 2026 House page, and `refresh` runs it too. `import-polls` loads your own CSV instead (`templates/polls.csv`).

`solve_national_swing` then finds one national swing `x` such that all 435 House districts, weighted by the 2024 electorate, add up exactly to the target. The same `x` applies to Senate and governor races. Midterm turnout and group news are then added on top, so the final national result is target + turnout effect + news effect (currently D+8.5 − 0.5 + 0.4 = D+8.3).

## Group news

1. **Collect and score.** RSS feeds and Google News group searches are scored as before (`analyze_content`, `group_features`). A group's direction comes from party tone in sentences about the group. If a policy issue (raids, voting rights…) is covered with no party named, VADER tone counts against the White House party at half weight. `aggregate_groups` averages articles within each publisher, weighted by recency (14-day half-life), geography and group relevance, then averages publishers equally.
2. **Learned coefficient** (`build-news-effect`, [newseffect.py](newseffect.py)). The same searches are re-run on Google News for the matching pre-election window of each midterm from 2010 to 2022, and scored with that year's White House party. Each racial group's CES House swing beyond `ratio × national swing` is regressed on its evidence-weighted signal, with both sides centered across groups. The fit is weighted least squares with a N(0, 6²) prior on the slope. Current fit: 10 usable points, raw slope 7.5 ± 9.8, posterior **2.0 ± 5.1** pts per unit. Its leave-one-out error (1.74 pts) is barely below assuming no effect (1.75), so the effect is weak.
3. **Applied** (`group_news_pp`). `coefficient × signal × m/(m+8)`, capped at ±12, added on top of the baseline. It is not centered, so net favorable coverage of a group also moves the national result. The coefficient itself was fitted on relative (centered) swings, since four midterms cannot separate a national news effect from the midterm swing.

The overall D-minus-R news `difference` and the topic signals are still computed and shown, but they no longer move the forecast. An earlier GDELT study found national news tone had no predictive power for the national swing.

## Combining them

For each cohort:

```text
projected  = clip(anchored 2024 baseline + (cell_ratio × national_k × x + group_news) / 100, 0, 1)
race share = Σ midterm-voter weight × projected
```

Each race reports `margin − baseline_margin = turnout_shift + environment_shift + news_margin_shift`:

- `turnout_shift_pp`: the 2024 electorate reweighted to the midterm electorate.
- `environment_shift_pp`: the national swing.
- `news_margin_shift_pp`: group news.

Win probability is `Φ(margin / σ)`, with `σ = √(5² + race_sd²)` and `race_sd` = 7 (House, Senate) or 9 (governor). `win_probability_d_without_news` removes the group news term. `simulate_chambers` draws 20,000 simulations with a shared national error to get seat totals and control probabilities. Senate totals add the 65 holdovers, and the Vice President breaks a 50–50 tie.

The `scenario` command and `/api/scenario` are a separate manual tool: they apply one uniform shift around the 2024 baselines and are not part of `predict`.

## API

Read-only endpoints served by `python main.py serve`:

- `/api/summary`, `/api/races`, `/api/groups`
- `/api/predictions?office=house` (also `senate`, `governor`)
- `/api/race?id=2026-house-PA-07`
- `/api/scenario?id=2026-house-PA-07&coefficient_pp=2&assumed_shift_pp=0`
- `/api/export/forecasts`, `/api/export/history?id=2026-house-PA-07`

If port 8000 is busy, stop the other server or pass `--port 8001`.

## Static site (GitHub Pages)

`python main.py export-site` (`site.py`) writes the dashboard to `docs/` as plain files: the `web/` assets plus one JSON file per API response. The exported `index.html` has a `<meta name="election-static">` tag, which makes `app.js` read `data/*.json` instead of `/api/`.

To keep the export near 60 MB, each race file is compacted:

- The shared news list is stored once in `data/news.json`.
- Cohort rows that repeat across keys are rebuilt in the browser (`expandDetail` in `web/app.js`).
- The unused article ID list is dropped.
- Floats are rounded to six decimal places.

The manual scenario slider falls back to its in-browser estimate, since there is no server. Re-run `export-site` after `refresh` or `predict` so the site matches the database.

## Tests

```powershell
python -m unittest discover -s tests -v
python -m compileall -q main.py electiondb
node --check web/app.js
```

The tests cover:

- News scoring, negation, rescoring and publisher balancing.
- Census ethnicity conservation and education collapsing.
- Logistic estimation, national-target solving, turnout weighting, the margin breakdown and exact aggregation.
- Poll parsing and averaging, and the news-effect fit.
- Win probabilities, persistence and import validation.

They run against temporary or in-memory databases.
