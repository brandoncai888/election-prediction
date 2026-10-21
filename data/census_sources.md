# Census demographic sources

The importer uses **2024 ACS five-year estimates (2020–2024)**. The Census
Bureau's [2024 release schedule](https://www.census.gov/programs-surveys/acs/news/data-releases/2024/release-schedule.html)
reports publication on January 29, 2026. The
[API catalog](https://api.census.gov/data/2024/acs/acs5.html) and actual network
responses were checked on October 21, 2026.

## Exact intersections

The published joint tables are **C15002A–G**, titled *Sex by Educational
Attainment for the Population 25 Years and Over*, with these race universes:

| Table | Database race group | Census universe |
| --- | --- | --- |
| C15002A | white | White alone |
| C15002B | black | Black or African American alone |
| C15002C | american_indian_alaska_native | American Indian and Alaska Native alone |
| C15002D | asian | Asian alone |
| C15002E | native_hawaiian_pacific_islander | Native Hawaiian and Other Pacific Islander alone |
| C15002F | other_race | Some other race alone |
| C15002G | multiracial | Two or more races |

These are disjoint race groups **regardless of Hispanic ethnicity**. Hispanic
residents occur within these race groups. Do not combine them with a separate
Hispanic category or compare them directly with non-Hispanic White survey
categories. The Census also publishes C15002H (non-Hispanic White alone) and
C15002I (Hispanic or Latino); they are not imported because they overlap A–G.

[Verified table labels](https://api.census.gov/data/2024/acs/acs5/groups/C15002A.html)
provide male estimates 003–006 and female estimates 008–011, respectively:

1. Less than high school diploma.
2. High school graduate, including equivalency.
3. Some college or an associate's degree.
4. Bachelor's degree or higher.

All eight cells are published directly; no marginal shares are multiplied.
Each cell imports its published 90% margin of error directly. There is no need
to approximate a margin of error by combining component estimates. Negative
sentinels and other missing estimates are omitted; unavailable margins of error
remain SQL `NULL`.

## Downloading without an API key

In this environment, Census data queries redirect to the official
[`missing_key.html`](https://api.census.gov/data/missing_key.html) page without a
key. Metadata remains accessible. When `CENSUS_API_KEY` is absent the importer
automatically reads the official public
[table-based summary files](https://www.census.gov/programs-surveys/acs/data/summary-file.html).
Each table file contains estimates and margins of error for all published
geographies; only exact national, state, and congressional district records are
loaded.

Files are in the
[2024 five-year download directory](https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/):

```text
https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-c15002a.dat
```

The remaining files replace `c15002a` with `c15002b` through `c15002g`. The
verified A file is 29,733,303 bytes. The importer downloads only these seven
tables, totaling 174,460,778 bytes, not the 12 GB complete archive. Raw pipe-separated files, API metadata,
retrieval timestamps, public URLs, and SHA-256 checksums are cached under
`data/cache/census/2024/`. Subsequent runs reuse this snapshot. To refresh,
move the relevant cached file and its `.meta.json` sidecar out of that folder.
Optional API keys are never included in cached URLs, source records, or errors.

## Population and geography limits

These are estimates of **residents age 25 and older**, including noncitizens.
They exclude people aged 18–24 and are not counts of eligible voters,
registered voters, actual turnout, or votes. Census sex is male/female and must
not be presented as a measure of gender identity. A demographic association
does not establish that demographics cause a result.

The import covers the 50 states, DC, their published congressional districts,
and the US national total; Puerto Rico is excluded. Alaska and other at-large
districts retain district code `00`. The Census
[geography metadata](https://api.census.gov/data/2024/acs/acs5/geography.html)
reports a January 1, 2024 reference date. Store and display this vintage:
redistricting can make ACS publication districts differ from 2026 election
districts. Race forecasts need a verified boundary crosswalk before treating
these counts as the 2026 district population.

The downloaded district identifiers start with `5001900US`, identifying the
**119th Congress**. They must not be mistaken for `5000000US` or stripped of
their congressional vintage before selecting rows. Actual cached tables were
checked for all seven race groups: each contains the national total, all 51
state/DC records, and all 436 congressional district/DC records after excluding
Puerto Rico. Summed age-25+ populations agree exactly between the national,
state/DC, and district/DC levels for every race group.
