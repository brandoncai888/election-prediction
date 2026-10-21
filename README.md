# 2026 Election Observatory

An experimental forecast of the 2026 US midterms: every House seat, Senate contest and governor race. It combines who lives in each place, how those groups voted in 2024, and what the news has been saying recently. The forecasts appear on a local interactive map.

## Quick start

Requires Python 3.10+.

```powershell
python -m pip install -r requirements.txt
python main.py
```

Open **http://127.0.0.1:8000**. The database is already populated. To pull fresh news and rebuild every forecast, run `python main.py refresh`.

## How it works

1. **Who lives there.** Census data splits each state or district into 32 groups by race/ethnicity, sex and education.
2. **How those groups voted.** A model fitted to a large 2024 voter survey estimates each group's Democratic vote share. The estimates are then adjusted so each race as a whole matches its actual 2024 presidential result.
3. **What the news says.** Recent articles from major outlets are scored for whether coverage is good or bad for each party, which issues come up, and how specific racial groups are covered.
4. **Shift and combine.** Each group's 2024 baseline shifts with the news. Groups that historically swing more in midterms move more, and thin coverage moves things less. The groups are weighted by population into a projected margin, which is turned into a win probability.

## What you get

- **506 race forecasts:** 435 House seats, 35 Senate contests (including the FL and OH specials) and 36 governor races.
- For each race: the projected margin, D/R win probability, a rating (Safe / Likely / Lean / Tilt / Toss-up), the 2024 baseline, and how much the news moved it.
- House and Senate maps. Senate seat totals include the 65 seats not up this year.
- A breakdown for every race showing each demographic group's baseline, shift and population weight.
- A CSV export: `data/forecast_table_2026.csv`.

## Caveats

This is a research prototype. How strongly news moves votes is set by assumption, not learned from past elections. News tone reflects media coverage, not voter opinion. Turnout, candidates, incumbency and campaign spending are not modeled.

## More detail

- [electiondb/README.md](electiondb/README.md): the model step by step, code layout, API and tests.
- [data/README.md](data/README.md): data sources, refresh pipeline, database tables, and what is measured vs. assumed.
