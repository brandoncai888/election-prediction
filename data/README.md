# Data

| Path | Contents |
|---|---|
| `elections.sqlite` | The populated database the dashboard reads |
| `forecast_table_2026.csv` | One row per race: win probabilities, margins, 2024 baseline, news shift |
| `races_2026.csv` | 2026 race roster |
| `model/forecast2026.json` | Fitted 2024 baseline model and its validation |
| `model/demographic_elasticity.json` | Midterm swing ratios for each demographic group |
| `model/midterm_inputs.json` | National House results, out-party midterm gains, CES validated turnout by group |
| `model/news_effect.json` | Fitted group news coefficient, with every data point used |
| `cache/` | Raw downloads, each with URL, timestamp and SHA256 sidecar |
| `*_methodology.md`, `*_sources.md` | Detailed methodology and source notes (linked below) |

## Rebuild from scratch

```powershell
python main.py fetch-census --year 2024
python main.py fetch-survey --year 2022
python main.py fetch-survey --year 2024
python main.py train-model --force
python main.py fetch-anchors
python main.py build-midterm-inputs
python main.py build-news-effect
python main.py refresh --as-of 2026-10-21
python main.py serve --as-of 2026-10-21
```

- `refresh` fetches the default RSS feeds, Google News group searches and generic-ballot polls, rescores articles and regenerates every forecast.
- `build-midterm-inputs` and `build-news-effect` read the CES research outputs in `cache/research/ces_elasticity/` (see `tools/ces_elasticity_analysis.py`). `build-news-effect` also downloads historical Google News searches into `cache/news_backtest/`.
- `rescore-news` rescores saved articles without downloading.
- `predict` rebuilds forecasts from the saved inputs.
- `forecast-table` rewrites the CSV.

The default information cutoff is **October 21, 2026**. For a later snapshot, pass the same date to `refresh` and `serve`. Articles published after the cutoff (UTC) are excluded.

Census downloads use official public table files when `CENSUS_API_KEY` is not set. The CES subset is checksum-verified before reuse.

## Database tables

- **Inputs and provenance:** `races`, `demographics`, `historical_cells`, `sources`. Raw four-level Census education cells are kept for audit; the model uses two levels.
- **Polls:** `poll_aggregates` holds each generic-ballot aggregator's average by update date.
- **News:** `news_items` holds article identity and timestamps. `news_analysis` holds content, VADER compound, party tone, topics and explanations.
- **Forecasts:** `race_forecasts` holds one result per race. `demographic_forecasts` holds each cohort's population, weight, baseline, projected share and shift. `forecast_metadata` holds the model and validation. `model_runs` holds saved manual scenarios.

## Importing your own data

Header-only templates are in `templates/`. Load them with `import-history`, `import-turnout`, `import-results`, `import-news` or `import-polls`.

- Shares use 0–1.
- Geography IDs look like `us`, `state:37` and `cd:3707`. At-large districts use `00`.
- A file is validated in full before anything is committed.

## What is measured and what is assumed

**Population and turnout.** ACS education tables cover residents age 25+, including noncitizens, so ages 18–24 are missing. Census sex is a binary source variable, not gender identity. Each group is weighted by CES validated turnout among citizens for its race, sex and education. Noncitizens are not removed, which overstates groups with many noncitizens.

**Race and ethnicity.** C15002A–G give any-ethnicity race counts, H gives White non-Hispanic and I gives Hispanic of any race. Nonwhite Hispanic people are allocated proportionally within each sex/education cell; this step is estimated. The 32 cohorts exactly conserve the A–G total. MENA respondents are folded into White, and NHPI shares the CES Other-race coefficient.

**Baseline.** Cohort shares are fitted to self-reported 2024 survey votes, not official returns by group. The CES v10 release lacks 2024 post-election weights, so pre-election weights are used. Each race is then anchored to its actual 2024 presidential result.

**National environment.** The typical out-party gain averages only four midterms (3.0 to 9.0 points). The polling half uses aggregator averages as listed on Wikipedia, not individual polls.

**News.** Coverage comes from three outlets plus targeted Google News searches, a convenience sample. VADER is a general sentiment tool; the party-attribution rules help but can't resolve every ambiguous report. The group news coefficient is fitted to ten group-midterm observations from archived Google News headlines, so it is imprecise. Archived search results for 2010 and 2014 are sparse.

**Boundaries and candidates.** House demographics use 119th-Congress district boundaries as proxies where lines changed for 2026. Baselines are anchored to 2026 lines where available. Candidates, incumbency, spending and third parties are not modeled.

**Validation.** An 80/20 holdout of whole 2024 survey respondents gives a weighted Brier score of about 0.2206, versus 0.2494 for an intercept-only model. This checks the 2024 baseline only. The win probabilities use historical race-level error widths but have not been backtested on past elections.

## Detailed docs and sources

- [Model methodology](model_methodology.md), [news methodology](news_methodology.md), [map sources](map_sources.md)
- [Races](races_sources.md), [Census](census_sources.md), [survey](survey_sources.md)
- [Census ACS five-year](https://www.census.gov/data/developers/data-sets/acs-5year.html) and [C15002 variables](https://api.census.gov/data/2024/acs/acs5/groups/C15002A.html)
- [CES cumulative data (Kuriwaki)](https://doi.org/10.7910/DVN/II2DB6) and the [Berkeley SDA mirror](https://sda.berkeley.edu/sdaweb/analysis/?dataset=ces-cumulative-2024-v10)
- [VADER](https://github.com/cjhutto/vaderSentiment)
- [Census boundary files](https://www.census.gov/geographies/mapping-files/time-series/geo/carto-boundary-file.html)
- [BBC](https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml), [NPR Politics](https://feeds.npr.org/1014/rss.xml) and [Guardian US Politics](https://www.theguardian.com/us-news/us-politics/rss) RSS feeds
