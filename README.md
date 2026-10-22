# 2026 Election

An experimental forecast of the 2026 US midterms: every House seat, Senate contest and governor race. It combines who lives in each place, how those groups voted in 2024, and what the news has been saying recently. The forecasts appear on a local interactive map.

## Visualization
https://brandoncai888.github.io/election-prediction/

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
3. **The national baseline.** The 2026 national House vote baseline is half the typical out-party midterm gain since 2010 and half the generic-ballot polling average. Every group moves toward it, with groups that historically swing more in midterms moving more.
4. **Who turns out.** Groups are weighted by how often they actually voted in recent midterms (validated survey turnout), so each place's midterm electorate differs from its 2024 one.
5. **Group-specific news.** On top of that baseline, recent coverage of specific racial groups and their issues shifts those groups, and with them the places where they live. How much news moves a group is learned from the 2010–2022 midterms.
6. **Combine.** Each place's groups add up to a projected margin, which is turned into a win probability.

## What you get

- **506 race forecasts:** 435 House seats, 35 Senate contests (including the FL and OH specials) and 36 governor races.
- For each race: the projected margin, D/R win probability, a rating (Safe / Likely / Lean / Tilt / Toss-up), the 2024 baseline, and how much the national environment, turnout and group news each moved it.
- House and Senate maps. Senate seat totals include the 65 seats not up this year.
- A breakdown for every race showing each demographic group's baseline, shift and population weight.
- A CSV export: `data/forecast_table_2026.csv`.

## Submission picks

`bjc_congress_prediction.csv` holds one pick per House and Senate race, chosen to maximize expected points under the scoring rule: a correct pick earns log½(p) points, where p is Kalshi's price for that candidate; a wrong pick earns 0. The rules live in `electiondb/kalshi.py`:

1. If the model gives a candidate more than 80% (`AUTO_PICK`), pick that candidate.
2. Otherwise, expected points = model probability × log½(Kalshi price). Pick the model's underdog only when its expected points are at least 2× the favorite's (`UNDERDOG_RATIO`).
3. Keep the favorite when Kalshi has no market, or when an independent or third candidate trades at 5% or more, since the model only splits the vote between the two parties.

`model_probability` in the CSV is the model's probability for the picked party, so underdog picks show values below 0.5. Candidate names come from Wikipedia's general-election listings and are checked against Kalshi's. The dashboard opens with the rules and every pick.

```powershell
python main.py fetch-kalshi       # current Kalshi prices, then rewrite the picks
python main.py fetch-candidates   # Wikipedia candidate roster, then rewrite the names
```

`python main.py refresh` also fetches Kalshi prices before regenerating the forecasts.

## Caveats

This is a research prototype. The midterm history covers only four elections, and the news effect is fitted to just ten group-midterm data points, so it is small and imprecise. News tone reflects media coverage, not voter opinion. Candidates, incumbency and campaign spending are not modeled.

## More detail

- [electiondb/README.md](electiondb/README.md): the model step by step, code layout, API and tests.
- [data/README.md](data/README.md): data sources, refresh pipeline, database tables, and what is measured vs. assumed.
