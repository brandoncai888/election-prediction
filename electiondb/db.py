"""SQLite storage. Unknown values stay NULL; observations retain their source."""
from pathlib import Path
import sqlite3
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'elections.sqlite'
RACE_GROUPS = {
    'white': 'White (any ethnicity)', 'black': 'Black (any ethnicity)',
    'american_indian_alaska_native': 'American Indian / Alaska Native',
    'asian': 'Asian', 'native_hawaiian_pacific_islander': 'Native Hawaiian / Pacific Islander',
    'other_race': 'Other race', 'multiracial': 'Two or more races',
}
SEXES = {'male': 'Male', 'female': 'Female'}
EDUCATION = {'less_than_high_school': 'Less than high school', 'high_school': 'High school / GED',
             'some_college': 'Some college / associate degree', 'bachelors_plus': "Bachelor's degree or higher"}
COARSE_EDUCATION = {'high_school_or_less':'High school or less',
                    'some_college_or_more':'Some college or more'}
EDUCATION_BRIDGE = {'less_than_high_school':'high_school_or_less','high_school':'high_school_or_less',
                    'some_college':'some_college_or_more','bachelors_plus':'some_college_or_more'}
MODEL_RACE_GROUPS = {
 'white_non_hispanic':'White, non-Hispanic','black_non_hispanic':'Black, non-Hispanic',
 'american_indian_alaska_native_non_hispanic':'American Indian / Alaska Native, non-Hispanic',
 'asian_non_hispanic':'Asian, non-Hispanic',
 'native_hawaiian_pacific_islander_non_hispanic':'Native Hawaiian / Pacific Islander, non-Hispanic',
 'other_race_non_hispanic':'Other race, non-Hispanic','multiracial_non_hispanic':'Multiracial, non-Hispanic',
 'hispanic':'Hispanic, any race',
}

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
 source_id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
 retrieved_at TEXT NOT NULL, kind TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS races (
 race_id TEXT PRIMARY KEY, cycle INTEGER NOT NULL, office TEXT NOT NULL,
 state TEXT NOT NULL, state_fips TEXT NOT NULL, district TEXT, seat_class INTEGER,
 election_type TEXT NOT NULL, election_date TEXT NOT NULL, label TEXT NOT NULL,
 geography_id TEXT NOT NULL, boundary_note TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demographics (
 geography_id TEXT NOT NULL, acs_year INTEGER NOT NULL, race_group TEXT NOT NULL,
 sex TEXT NOT NULL, education TEXT NOT NULL, population INTEGER CHECK(population>=0),
 moe REAL, universe TEXT NOT NULL, boundary_vintage TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(source_id),
 PRIMARY KEY(geography_id,acs_year,race_group,sex,education)
);
CREATE TABLE IF NOT EXISTS historical_cells (
 geography_id TEXT NOT NULL, election_year INTEGER NOT NULL, office TEXT NOT NULL,
 race_group TEXT NOT NULL, sex TEXT NOT NULL, education TEXT NOT NULL,
 dem_share REAL CHECK(dem_share BETWEEN 0 AND 1), sample_size INTEGER NOT NULL CHECK(sample_size>=0),
 effective_n REAL, weighted_total REAL, estimate_kind TEXT NOT NULL,
 race_scheme TEXT NOT NULL DEFAULT 'census_race_any_ethnicity',
 universe TEXT NOT NULL DEFAULT 'survey_voters_age25_plus', boundary_vintage TEXT NOT NULL DEFAULT 'unspecified',
 source_id TEXT NOT NULL REFERENCES sources(source_id),
 PRIMARY KEY(geography_id,election_year,office,race_group,sex,education,source_id)
);
CREATE TABLE IF NOT EXISTS election_results (
 geography_id TEXT NOT NULL, election_year INTEGER NOT NULL, office TEXT NOT NULL,
 dem_votes INTEGER NOT NULL CHECK(dem_votes>=0), rep_votes INTEGER NOT NULL CHECK(rep_votes>=0),
 other_votes INTEGER NOT NULL DEFAULT 0 CHECK(other_votes>=0),
 boundary_vintage TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(source_id),
 PRIMARY KEY(geography_id,election_year,office,source_id)
);
CREATE TABLE IF NOT EXISTS news_items (
 news_id TEXT PRIMARY KEY, race_id TEXT REFERENCES races(race_id),
 published_at TEXT NOT NULL, fetched_at TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
 source_name TEXT NOT NULL, party TEXT NOT NULL CHECK(party IN ('democratic','republican','unassigned')),
 sentiment REAL CHECK(sentiment BETWEEN -1 AND 1), method TEXT NOT NULL,
 UNIQUE(url,party)
);
CREATE TABLE IF NOT EXISTS turnout_assumptions (
 geography_id TEXT NOT NULL, cycle INTEGER NOT NULL, race_group TEXT NOT NULL,
 sex TEXT NOT NULL, education TEXT NOT NULL, eligible_population REAL NOT NULL CHECK(eligible_population>=0),
 turnout REAL NOT NULL CHECK(turnout BETWEEN 0 AND 1), universe TEXT NOT NULL,
 boundary_vintage TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(source_id),
 PRIMARY KEY(geography_id,cycle,race_group,sex,education)
);
CREATE TABLE IF NOT EXISTS model_runs (
 run_id INTEGER PRIMARY KEY AUTOINCREMENT, race_id TEXT NOT NULL REFERENCES races(race_id),
 created_at TEXT NOT NULL, as_of TEXT NOT NULL, baseline_year INTEGER NOT NULL,
 coefficient_pp REAL NOT NULL, assumed_shift_pp REAL NOT NULL, status TEXT NOT NULL,
 parameters TEXT NOT NULL, result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS race_office_state ON races(office,state);
CREATE INDEX IF NOT EXISTS historical_geo ON historical_cells(geography_id,office,election_year);
CREATE INDEX IF NOT EXISTS news_date ON news_items(published_at);
CREATE TABLE IF NOT EXISTS race_forecasts (
 race_id TEXT NOT NULL REFERENCES races(race_id), as_of TEXT NOT NULL,
 model_version TEXT NOT NULL, updated_at TEXT NOT NULL,
 dem_share REAL CHECK(dem_share BETWEEN 0 AND 1), rep_share REAL CHECK(rep_share BETWEEN 0 AND 1),
 winner TEXT, margin_pp REAL, rating TEXT, status TEXT NOT NULL, result TEXT NOT NULL,
 PRIMARY KEY(race_id,as_of)
);
CREATE TABLE IF NOT EXISTS forecast_metadata (
 as_of TEXT PRIMARY KEY, updated_at TEXT NOT NULL, model TEXT NOT NULL, validation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demographic_forecasts (
 race_id TEXT NOT NULL REFERENCES races(race_id),as_of TEXT NOT NULL,
 race_group TEXT NOT NULL,sex TEXT NOT NULL,education TEXT NOT NULL,
 population REAL NOT NULL CHECK(population>=0), weight REAL NOT NULL CHECK(weight BETWEEN 0 AND 1),
 baseline_dem_share REAL NOT NULL CHECK(baseline_dem_share BETWEEN 0 AND 1),
 projected_dem_share REAL NOT NULL CHECK(projected_dem_share BETWEEN 0 AND 1),
 swing_pp REAL NOT NULL,model_version TEXT NOT NULL,details TEXT NOT NULL,
 PRIMARY KEY(race_id,as_of,race_group,sex,education)
);
CREATE VIEW IF NOT EXISTS demographic_coverage AS
 SELECT geography_id,acs_year,COUNT(*) AS cells,SUM(population) AS population_25_plus,
 SUM(CASE WHEN population IS NULL THEN 1 ELSE 0 END) AS missing_cells
 FROM demographics GROUP BY geography_id,acs_year;
'''

LIMITATIONS = [
 'Scope: 435 voting House seats, regularly scheduled Senate elections and verified Senate specials, and state governors. Candidates, delegates, territories, and state/local offices are not yet loaded.',
 'ACS education intersections describe residents age 25+, including noncitizens. They are not counts of eligible or likely voters, and exclude voters age 18–24.',
 'Census sex is not a measure of gender identity. Race categories include people of Hispanic ethnicity; Hispanic origin is a separate concept.',
 'House projections use 119th-Congress population and geographic baselines as proxies where 2026 boundaries changed. The House map is a seat cartogram and does not present older boundaries as current ones.',
 'Ballots do not record race, sex, or education. Historical demographic vote shares are weighted survey estimates, not official demographic returns. Small samples are suppressed.',
 'The 2024 demographic baseline is fitted to CES responses. News response coefficients are explicit priors; no historically calibrated causal news effect or election win probability is claimed.',
 'Forecasts combine mutually exclusive Hispanic/non-Hispanic cohorts. Nonwhite Hispanic allocation is estimated within each sex and education cell; uncertainty in this bridge is not measured.',
 'Predicted race shares are population-standardized preferences for adults age 25+ under equal-turnout assumptions, an experimental proxy for the full electorate.',
 'Two-party vote share excludes other candidates. State-level history cannot be substituted for congressional-district observations without a labeled model.',
]

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def initialize(conn):
    conn.executescript(SCHEMA)
    from .races import seed_races
    count = seed_races(conn)
    references = [
        ('census-acs-docs', 'Census ACS five-year data documentation', 'https://www.census.gov/data/developers/data-sets/acs-5year.html', 'documentation', 'C15002A–G: sex by educational attainment for each racial group, population age 25+.'),
        ('ces-docs', 'Cooperative Election Study', 'https://cces.gov.harvard.edu/', 'documentation', 'Historical voting estimates require documented survey weights, matching race definitions, and sufficient sample sizes.'),
        ('anes-docs', 'American National Election Studies', 'https://electionstudies.org/data-center/', 'documentation', 'Historical vote microdata and codebooks. No respondent records are served by the dashboard.'),
    ]
    conn.executemany('INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?)',
                     [(sid,name,url,utc_now(),kind,notes) for sid,name,url,kind,notes in references])
    conn.commit()
    return count
